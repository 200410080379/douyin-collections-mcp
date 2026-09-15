"""Live stdio MCP acceptance check. Restores only this test's added memberships."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from douyin_collections_mcp.browser import (
    COLLECT,
    DETAIL,
    MAINTAIN_FOLDER,
    MOVE_VIDEO,
    BrowserClient,
)
from douyin_collections_mcp.config import Settings


async def main():
    state = {"steps": [], "videos": []}
    out = Path("output/mcp-live-smoke.json")
    out.parent.mkdir(exist_ok=True)
    account_id = folder = None

    def record(step, **kwargs):
        row = {"step": step, **kwargs}
        state["steps"].append(row)
        out.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        out.chmod(0o600)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    try:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "douyin_collections_mcp", "serve"],
            cwd=tempfile.gettempdir(),
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                if result.isError:
                    raise RuntimeError(next(c.text for c in result.content if c.type == "text"))
                value = result.structuredContent or json.loads(
                    next(c.text for c in result.content if c.type == "text")
                )
                return value["data"]

            try:
                login = await call("douyin_login_status")
                assert login["logged_in"]
                account_id = login["account"]["id"]
                record("mcp_login", verified=True)
                existing = (await call("douyin_list_folders"))["items"]
                assert not any(f["name"] == "工具验收" for f in existing)
                folder = await call("douyin_create_folder", {"name": "工具验收"})
                state["folder_id"] = folder["id"]
                assert folder["created"] and folder["visibility"] == "private"
                record("mcp_create", verified=folder["verified"], visibility=folder["visibility"])
                favorites = (await call("douyin_list_videos", {"source": "favorites", "limit": 3}))[
                    "items"
                ]
                chosen = favorites[1]
                state["videos"].append(
                    {
                        "id": chosen["id"],
                        "was_favorited": True,
                        "was_liked": chosen["extra"].get("is_liked"),
                        "aweme_type": chosen["extra"].get("aweme_type"),
                    }
                )
                plan = await call(
                    "douyin_prepare_classification",
                    {"assignments": [{"video_id": chosen["id"], "folder_name": folder["name"]}]},
                )
                record("mcp_preview", executed=plan["executed"])
                applied = await call(
                    "douyin_apply_plan", {"plan_id": plan["plan_id"], "max_operations": 1}
                )
                record("mcp_apply", status=applied["status"])
                assert applied["status"] == "completed"
                members = (
                    await call(
                        "douyin_list_videos", {"source": "folder", "folder_id": folder["id"]}
                    )
                )["items"]
                assert any(v["id"] == chosen["id"] for v in members)
                record("mcp_readback", verified=True)
                repeated = await call(
                    "douyin_apply_plan", {"plan_id": plan["plan_id"], "max_operations": 1}
                )
                assert repeated["status"] == "completed"
                record("mcp_resume", verified=True)

                likes = (await call("douyin_list_videos", {"source": "likes", "limit": 20}))[
                    "items"
                ]
                candidate = next(
                    (
                        v
                        for v in likes
                        if v["extra"].get("is_liked") is True
                        and v["extra"].get("is_favorited") is False
                    ),
                    None,
                )
                if candidate:
                    state["videos"].append(
                        {
                            "id": candidate["id"],
                            "was_favorited": False,
                            "was_liked": True,
                            "aweme_type": candidate["extra"]["aweme_type"],
                        }
                    )
                    liked_plan = await call(
                        "douyin_prepare_classification",
                        {
                            "assignments": [
                                {"video_id": candidate["id"], "folder_name": folder["name"]}
                            ],
                            "allow_favorite_liked": True,
                        },
                    )
                    record(
                        "mcp_liked_preview",
                        will_favorite=liked_plan["assignments"][0]["detail"]["will_favorite"],
                    )
                    liked_result = await call(
                        "douyin_apply_plan",
                        {"plan_id": liked_plan["plan_id"], "max_operations": 1},
                    )
                    record(
                        "mcp_liked_apply",
                        status=liked_result["status"],
                        error=liked_result["assignments"][0]["detail"].get("error"),
                    )
                    assert liked_result["status"] == "completed"
                else:
                    record("mcp_liked_apply", skipped="no liked-only candidate in first page")
            finally:
                await call("douyin_close_browser")
    finally:
        if account_id and folder:
            browser = BrowserClient(Settings.from_env())
            try:
                for video in state["videos"]:
                    if await browser.folder_contains(folder["id"], video["id"]):
                        await browser._write(
                            MOVE_VIDEO,
                            {
                                "item_ids": [video["id"]],
                                "item_type": "2",
                                "from_collects_id": folder["id"],
                                "collects_name": folder["name"],
                            },
                            account_id,
                        )
                        assert not await browser.folder_contains(folder["id"], video["id"])
                    detail = (await browser._request(DETAIL, params={"aweme_id": video["id"]}))[
                        "aweme_detail"
                    ]
                    if not video["was_favorited"] and detail.get("collect_stat") == 1:
                        await browser._write(
                            COLLECT,
                            {},
                            account_id,
                            body={
                                "aweme_id": video["id"],
                                "action": "0",
                                "aweme_type": str(video["aweme_type"]),
                            },
                        )
                        detail = (await browser._request(DETAIL, params={"aweme_id": video["id"]}))[
                            "aweme_detail"
                        ]
                    assert (detail.get("collect_stat") == 1) == video["was_favorited"]
                    assert (detail.get("user_digged") != 0) == video["was_liked"]
                remaining = [f for f in await browser.all_folders() if f["id"] == folder["id"]]
                assert (
                    len(remaining) == 1
                    and remaining[0]["name"] == "工具验收"
                    and remaining[0]["count"] == 0
                )
                await browser._write(
                    MAINTAIN_FOLDER,
                    {
                        "action": "2",
                        "collects_id": folder["id"],
                        "version_code": "230000",
                        "version_name": "23.0.0",
                    },
                    account_id,
                )
                assert not any(f["id"] == folder["id"] for f in await browser.all_folders())
                record("cleanup", test_folder_removed=True, original_interactions_restored=True)
            finally:
                await browser.close()


asyncio.run(main())
