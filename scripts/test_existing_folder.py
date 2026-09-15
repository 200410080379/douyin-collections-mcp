"""Authorized live check against the user-created private '工具测试' folder.

Adds one existing favorite, verifies it, then removes only that test membership.
Does not delete the user-created folder or cancel any likes/favorites.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from douyin_collections_mcp.browser import DETAIL, MOVE_VIDEO, BrowserClient
from douyin_collections_mcp.config import Settings
from douyin_collections_mcp.errors import DouyinError
from douyin_collections_mcp.service import CollectionService
from douyin_collections_mcp.storage import Store


async def main():
    browser = BrowserClient(Settings.from_env())
    state = {"test": "user_created_private_folder", "steps": []}
    out = Path("output/existing-folder-smoke.json")
    out.parent.mkdir(exist_ok=True)
    account = folder = selected = before = None

    def record(step, **kwargs):
        row = {"step": step, **kwargs}
        state["steps"].append(row)
        out.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        out.chmod(0o600)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    try:
        account = await browser.get_account()
        matches = [f for f in await browser.all_folders() if f["name"] == "工具测试"]
        if len(matches) != 1:
            record("folder_missing_or_ambiguous", count=len(matches))
            return
        folder = matches[0]
        record("folder_found", visibility=folder["visibility"], count=folder["count"])
        if folder["visibility"] != "private":
            return
        items = (await browser.list_videos("favorites", limit=5))["items"]
        for item in items:
            if not await browser.folder_contains(folder["id"], item["id"]):
                # Avoid picking the user's first favorite if another item is available.
                selected = item
                if items.index(item) > 0:
                    break
        if selected is None:
            raise RuntimeError("No candidate absent from the test folder")
        before = (await browser._request(DETAIL, params={"aweme_id": selected["id"]}))[
            "aweme_detail"
        ]
        if before.get("collect_stat") != 1:
            raise RuntimeError("Candidate no longer favorited")
        state["folder_id"] = folder["id"]
        state["video_id"] = selected["id"]
        record("baseline", test_membership_present=False, favorited=True)

        async def observe(response):
            if MOVE_VIDEO in response.url:
                try:
                    data = await response.json()
                    record(
                        "move_response",
                        code=data.get("status_code"),
                        message=str(data.get("status_msg", ""))[:100],
                    )
                except (ValueError, TypeError):
                    pass

        browser.context.on("response", observe)

        with tempfile.TemporaryDirectory(prefix="douyin-membership-test-") as directory:
            store = Store(Path(directory))
            try:
                store.upsert_videos(account["id"], "favorites", [selected])
                service = CollectionService(browser, store)
                plan = await service.prepare_plan(
                    [{"video_id": selected["id"], "folder_name": folder["name"]}]
                )
                record("preview", executed=plan["executed"])
                result = await service.apply_plan(plan["plan_id"], max_operations=1)
                operation = result["assignments"][0]
                record(
                    "apply",
                    status=operation["status"],
                    verified=operation["detail"].get("verified"),
                    error=operation["detail"].get("error"),
                )
                if operation["status"] != "completed":
                    return
                assert await browser.folder_contains(folder["id"], selected["id"])
                record("membership_readback", verified=True)
                repeat = await service.apply_plan(plan["plan_id"], max_operations=1)
                record("completed_plan_repeat", status=repeat["status"])
            finally:
                store.close()
    finally:
        try:
            if account and folder and selected and before:
                if await browser.folder_contains(folder["id"], selected["id"]):
                    await browser._write(
                        MOVE_VIDEO,
                        {
                            "item_ids": [selected["id"]],
                            "item_type": "2",
                            "from_collects_id": folder["id"],
                            "collects_name": folder["name"],
                        },
                        account["id"],
                    )
                    removed = not await browser.folder_contains(folder["id"], selected["id"])
                    record("cleanup_membership", verified=removed)
                    assert removed
                else:
                    record("cleanup_membership", needed=False)
                after = (await browser._request(DETAIL, params={"aweme_id": selected["id"]}))[
                    "aweme_detail"
                ]
                unchanged = after.get("collect_stat") == before.get("collect_stat") and after.get(
                    "user_digged"
                ) == before.get("user_digged")
                record("interactions_unchanged", verified=unchanged)
                assert unchanged
        except DouyinError as exc:
            record("cleanup_needs_attention", error=exc.as_dict())
            raise
        finally:
            await browser.close()


asyncio.run(main())
