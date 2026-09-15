"""MCP protocol tests using the official SDK and isolated subprocess data dirs.

The real stdio entrypoint is exercised without launching any browser. Domain
failure tests replace get_account before server startup, so no account is read.
"""

import json
import os
import sys
from datetime import timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def parameters(tmp_path, *, bootstrap=None):
    args = ["-m", "douyin_collections_mcp", "serve"]
    if bootstrap is not None:
        args = ["-c", bootstrap]
    return StdioServerParameters(
        command=sys.executable,
        args=args,
        env={**os.environ, "DOUYIN_MCP_DATA_DIR": str(tmp_path / "mcp-data")},
    )


def text_content(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


async def test_stdio_lists_tools_and_reports_status_without_browser(tmp_path):
    async with (
        stdio_client(parameters(tmp_path)) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as client,
    ):
        initialized = await client.initialize()
        assert initialized.serverInfo.name == "douyin-collections"
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert {
            "douyin_status",
            "douyin_list_videos",
            "douyin_list_folders",
            "douyin_sync",
            "douyin_prepare_classification",
            "douyin_get_plan",
            "douyin_apply_plan",
            "douyin_create_folder",
        } <= tools.keys()
        assert tools["douyin_list_videos"].annotations.readOnlyHint is True
        assert tools["douyin_apply_plan"].annotations.readOnlyHint is False
        assert tools["douyin_prepare_classification"].annotations.openWorldHint is False

        response = await client.call_tool("douyin_status", {})
        assert response.isError is False
        payload = response.structuredContent or json.loads(text_content(response))
        assert payload["ok"] is True
        assert payload["data"]["transport"] == "stdio"
        assert payload["data"]["browser_open"] is False
        assert payload["data"]["account_verified_this_process"] is False
    assert not (tmp_path / "mcp-data" / "browser-profile").exists()


async def test_invalid_arguments_are_mcp_tool_errors_without_launching_browser(tmp_path):
    async with (
        stdio_client(parameters(tmp_path)) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as client,
    ):
        await client.initialize()
        for arguments in ({"source": "other-account"}, {"limit": 0}, {"cursor": "bad"}):
            response = await client.call_tool("douyin_list_videos", arguments)
            assert response.isError is True
            assert text_content(response)
    assert not (tmp_path / "mcp-data" / "browser-profile").exists()


@pytest.mark.parametrize(
    "raised, expected_code",
    [
        ('DouyinError("not_logged_in", "请先登录")', "not_logged_in"),
        ('RuntimeError("credential-leak-test-marker")', "internal_error"),
    ],
)
async def test_domain_and_unexpected_errors_set_iserror_and_redact_raw_exceptions(
    tmp_path,
    raised,
    expected_code,
):
    bootstrap = f"""
from douyin_collections_mcp import server
from douyin_collections_mcp.errors import DouyinError
async def fail_without_browser(self):
    raise {raised}
server.BrowserClient.get_account = fail_without_browser
server.mcp.run(transport="stdio")
"""
    async with (
        stdio_client(parameters(tmp_path, bootstrap=bootstrap)) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as client,
    ):
        await client.initialize()
        response = await client.call_tool("douyin_list_videos", {})
        assert response.isError is True
        content = text_content(response)
        assert expected_code in content
        assert "credential-leak-test-marker" not in content
    assert not (tmp_path / "mcp-data" / "browser-profile").exists()
