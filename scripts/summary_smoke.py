"""Read-only native summary acceptance check through the installed MCP entrypoint.

Usage: uv run python scripts/summary_smoke.py VIDEO_ID [VIDEO_ID ...]
Only counts and availability are printed, never the platform summary text.
"""

import argparse
import asyncio
import json
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(video_ids):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "douyin_collections_mcp", "serve"],
        cwd=tempfile.gettempdir(),
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as client,
    ):
        await client.initialize()
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in ("douyin_get_video_summary", "douyin_get_video_summaries"):
            assert name in tools and tools[name].annotations.readOnlyHint

        async def call(name, arguments):
            response = await client.call_tool(name, arguments)
            if response.isError:
                raise RuntimeError("MCP summary check failed; inspect login and adapter state.")
            payload = response.structuredContent or json.loads(
                next(block.text for block in response.content if block.type == "text")
            )
            assert payload["ok"]
            return payload["data"]

        try:
            status = await call("douyin_status", {})
            batch = await call(
                "douyin_get_video_summaries", {"video_ids": video_ids, "refresh": True}
            )
            print(
                json.dumps(
                    {
                        "version": status["version"],
                        "tool_count": len(tools),
                        "complete": batch["complete"],
                        "available_count": batch["available_count"],
                        "unavailable_count": batch["unavailable_count"],
                        "error_count": batch["error_count"],
                        "items": [
                            {
                                "status": item["status"],
                                "source": item.get("source"),
                                "summary_characters": len(item.get("summary") or ""),
                                "chapter_count": len(item.get("chapters", [])),
                            }
                            for item in batch["items"]
                        ],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            assert batch["complete"]
            cached = await call("douyin_get_video_summary", {"video_id": video_ids[0]})
            assert cached["cached"] and not cached["cache_expired"]
            print(
                json.dumps({"single_summary_cache_hit": True, "status": cached["status"]}),
                flush=True,
            )
        finally:
            await call("douyin_close_browser", {})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_ids", nargs="+")
    args = parser.parse_args()
    if not 1 <= len(args.video_ids) <= 10 or any(
        not v.isascii() or not v.isdigit() or len(v) > 30 for v in args.video_ids
    ):
        parser.error("Supply 1–10 numeric video IDs, each at most 30 digits.")
    asyncio.run(main(args.video_ids))
