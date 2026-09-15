"""Explicit development smoke test: one private folder, one existing favorite.

Only run when live testing is authorized. Does not unlike/unfavorite any video.
The test-created folder is removed in finally; other folders are never deleted.
"""

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from douyin_collections_mcp.browser import DETAIL, MAINTAIN_FOLDER, BrowserClient
from douyin_collections_mcp.config import Settings
from douyin_collections_mcp.service import CollectionService
from douyin_collections_mcp.storage import Store


async def main():
    b = BrowserClient(Settings.from_env())
    out = Path("output/live-smoke.json")
    out.parent.mkdir(exist_ok=True)
    state = {"test": "private_folder_single_existing_favorite", "steps": []}
    folder_id = None
    account = None
    name = "MCP验证" + datetime.now(UTC).strftime("%H%M%S")
    state["folder_name"] = name

    def record(step, **kwargs):
        row = {"step": step, **kwargs}
        state["steps"].append(row)
        out.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        out.chmod(0o600)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    try:
        account = await b.get_account()

        async def write_response(response):
            if "/aweme/v1/web/collects/maintain/" in response.url:
                try:
                    data = await response.json()
                    record(
                        "platform_response",
                        code=data.get("status_code"),
                        message=str(data.get("status_msg", ""))[:200],
                    )
                except (ValueError, TypeError):
                    pass

        b.context.on("response", write_response)
        existing = await b.all_folders()
        assert not any(f["name"] == name for f in existing)
        videos = await b.list_videos("favorites", limit=3)
        v = videos["items"][1]
        before_detail = (await b._request(DETAIL, params={"aweme_id": v["id"]}))["aweme_detail"]
        assert before_detail["collect_stat"] == 1
        state["video_id"] = v["id"]
        record("read_baseline", folders=len(existing), video_favorited=True)
        folder = await b.ensure_folder(name, expected_account_id=account["id"])
        folder_id = folder["id"]
        state["folder_id"] = folder_id
        record(
            "create_private_folder", verified=folder["verified"], visibility=folder["visibility"]
        )
        with tempfile.TemporaryDirectory(prefix="douyin-mcp-live-test-") as directory:
            store = Store(Path(directory))
            try:
                store.upsert_videos(account["id"], "favorites", [v])
                service = CollectionService(b, store)
                plan = await service.prepare_plan([{"video_id": v["id"], "folder_name": name}])
                record("plan_preview", executed=plan["executed"])
                result = await service.apply_plan(plan["plan_id"], max_operations=1)
                record(
                    "apply", status=result["status"], operation=result["assignments"][0]["detail"]
                )
                assert result["status"] == "completed"
                repeat = await service.apply_plan(plan["plan_id"], max_operations=1)
                assert repeat["status"] == "completed"
                record("resume_completed_plan", status=repeat["status"])
                after_detail = (await b._request(DETAIL, params={"aweme_id": v["id"]}))[
                    "aweme_detail"
                ]
                assert after_detail["collect_stat"] == before_detail["collect_stat"]
                assert after_detail["user_digged"] == before_detail["user_digged"]
                record("verify_interactions_unchanged", verified=True)
            finally:
                store.close()
    finally:
        if account:
            # Even if creation succeeded but its response was lost, find only this
            # unique test name; never delete an existing or renamed folder.
            matches = [f for f in await b.all_folders() if f["name"] == name]
            if len(matches) == 1 and (folder_id is None or matches[0]["id"] == folder_id):
                await b._write(
                    MAINTAIN_FOLDER,
                    {
                        "action": "2",
                        "collects_id": matches[0]["id"],
                        "version_code": "230000",
                        "version_name": "23.0.0",
                    },
                    account["id"],
                )
                remaining = await b.all_folders()
                clean = not any(f["id"] == matches[0]["id"] for f in remaining)
                record("cleanup_test_folder", verified=clean)
                assert clean
        await b.close()


asyncio.run(main())
