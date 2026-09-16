"""Exercise native-summary MCP tools over official SDK stdio without a browser."""

import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VIDEO_A = "2000000000000000001"
VIDEO_B = "2000000000000000002"
VIDEO_C = "2000000000000000003"


def parameters(tmp_path):
    calls_file = tmp_path / "synthetic-browser-calls.txt"
    bootstrap = """
import json
import os
from pathlib import Path
from douyin_collections_mcp import server
from douyin_collections_mcp.errors import DouyinError

def record(value):
    with Path(os.environ['SYNTHETIC_CALLS_FILE']).open('a') as stream:
        stream.write(value + '\\n')

async def forbidden_start(self):
    record('START_FORBIDDEN')
    raise AssertionError('Tests must not launch a real browser')

async def account(self):
    record('account')
    return {'id': '1000000000000000001', 'sec_uid': 'synthetic', 'nickname': '合成账号'}

async def summary(self, video_id):
    record('summary:' + video_id)
    if video_id.endswith('3'):
        raise DouyinError('video_unavailable', '合成视频不可访问')
    available = not video_id.endswith('2')
    return {
        'video_id': video_id, 'title': '合成视频', 'author': '合成作者',
        'url': 'https://www.douyin.com/video/' + video_id,
        'source': 'douyin_native',
        'source_field': 'recommend_chapter_info' if available else None,
        'status': 'available' if available else 'unavailable',
        'reason': None if available else 'no_native_ai_summary',
        'summary': '合成的平台摘要' if available else None,
        'chapters': [{
            'title': '合成章节', 'summary': '合成章节要点',
            'start_time_ms': 19000, 'start_time_seconds': 19.0,
            'start_time': '00:19', 'points': [],
        }] if available else [],
        'ai_generated': available,
    }

server.BrowserClient.start = forbidden_start
server.BrowserClient.get_account = account
server.BrowserClient.get_video_summary = summary
server.mcp.run(transport='stdio')
"""
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", bootstrap],
        env={
            **os.environ,
            "DOUYIN_MCP_DATA_DIR": str(tmp_path / "mcp-data"),
            "SYNTHETIC_CALLS_FILE": str(calls_file),
        },
    )


def payload(response):
    assert response.isError is False
    result = response.structuredContent or json.loads(
        "\n".join(block.text for block in response.content if block.type == "text")
    )
    assert result["ok"] is True
    return result["data"]


def calls(tmp_path):
    path = tmp_path / "synthetic-browser-calls.txt"
    return path.read_text().splitlines() if path.exists() else []


async def test_stdio_summary_tools_are_readonly_and_return_single_and_batch_payloads(tmp_path):
    async with (
        stdio_client(parameters(tmp_path)) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as client,
    ):
        await client.initialize()
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in ("douyin_get_video_summary", "douyin_get_video_summaries"):
            assert tools[name].annotations.readOnlyHint is True
            assert tools[name].annotations.destructiveHint is False
            assert tools[name].annotations.idempotentHint is True
            assert tools[name].inputSchema["type"] == "object"
        one_schema = tools["douyin_get_video_summary"].inputSchema["properties"]
        assert one_schema["video_id"]["type"] == "string"
        assert one_schema["refresh"]["default"] is False
        batch_schema = tools["douyin_get_video_summaries"].inputSchema["properties"]["video_ids"]
        assert batch_schema["minItems"] == 1
        assert batch_schema["maxItems"] == 10
        assert batch_schema["items"]["type"] == "string"
        assert (
            tools["douyin_search_cache"].inputSchema["properties"]["include_summaries"]["default"]
            is True
        )

        status = payload(await client.call_tool("douyin_status", {}))
        assert status["browser_open"] is False
        assert calls(tmp_path) == []
        first = payload(await client.call_tool("douyin_get_video_summary", {"video_id": VIDEO_A}))
        assert first["video_id"] == VIDEO_A
        assert first["source"] == "douyin_native"
        assert first["status"] == "available"
        assert first["summary"] == "合成的平台摘要"
        assert first["chapters"][0]["start_time_ms"] == 19000
        assert first["chapters"][0]["start_time_seconds"] == 19.0
        assert first["cached"] is False
        assert first["cache_expired"] is False
        assert isinstance(first["fetched_at"], str)
        cached = payload(await client.call_tool("douyin_get_video_summary", {"video_id": VIDEO_A}))
        assert cached["cached"] is True
        refreshed = payload(
            await client.call_tool(
                "douyin_get_video_summary", {"video_id": VIDEO_A, "refresh": True}
            )
        )
        assert refreshed["cached"] is False
        assert calls(tmp_path).count("summary:" + VIDEO_A) == 2

        batch = payload(
            await client.call_tool(
                "douyin_get_video_summaries", {"video_ids": [VIDEO_B, VIDEO_A, VIDEO_B, VIDEO_C]}
            )
        )
        assert [item["video_id"] for item in batch["items"]] == [VIDEO_B, VIDEO_A, VIDEO_C]
        assert [item["status"] for item in batch["items"]] == ["unavailable", "available", "error"]
        assert batch["requested_count"] == 4
        assert batch["unique_count"] == 3
        assert batch["available_count"] == batch["unavailable_count"] == batch["error_count"] == 1
        assert batch["complete"] is False
        assert batch["items"][0]["summary"] is None
        assert batch["items"][0]["chapters"] == []
        assert batch["items"][2]["error"]["code"] == "video_unavailable"
        assert "START_FORBIDDEN" not in calls(tmp_path)
    assert not (tmp_path / "mcp-data" / "browser-profile").exists()


async def test_invalid_summary_arguments_do_not_call_account_or_browser(tmp_path):
    async with (
        stdio_client(parameters(tmp_path)) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as client,
    ):
        await client.initialize()
        cases = [
            ("douyin_get_video_summary", {"video_id": f"https://www.douyin.com/video/{VIDEO_A}"}),
            ("douyin_get_video_summary", {"video_id": "not-an-id"}),
            ("douyin_get_video_summary", {"video_id": "1" * 31}),
            ("douyin_get_video_summaries", {"video_ids": [VIDEO_A] * 11}),
            ("douyin_get_video_summaries", {"video_ids": []}),
            ("douyin_get_video_summaries", {"video_ids": [VIDEO_A, "https://example.invalid"]}),
        ]
        for name, arguments in cases:
            response = await client.call_tool(name, arguments)
            assert response.isError is True
            assert any(block.type == "text" and block.text for block in response.content)
        assert calls(tmp_path) == []
    assert not (tmp_path / "mcp-data" / "browser-profile").exists()
