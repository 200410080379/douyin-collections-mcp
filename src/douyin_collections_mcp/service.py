"""Account-scoped sync, classification previews, and resumable execution."""

import asyncio
from datetime import UTC, datetime, timedelta

from .browser import numeric_id
from .errors import DouyinError


class CollectionService:
    def __init__(self, browser, store):
        self.browser = browser
        self.store = store
        self.lock = asyncio.Lock()

    async def _check_account(self, expected):
        current = await self.browser.get_account()
        if current["id"] != expected:
            raise DouyinError("account_mismatch", "读取期间账号发生切换，结果未写入缓存。")

    async def list_videos(self, source="favorites", cursor="0", limit=20, folder_id=None):
        account = await self.browser.get_account()
        result = await self.browser.list_videos(
            source, cursor=cursor, limit=limit, folder_id=folder_id
        )
        await self._check_account(account["id"])
        key = f"folder:{folder_id}" if source == "folder" else source
        self.store.upsert_videos(account["id"], key, result["items"])
        return {"account": account, "source": key, **result}

    async def list_folders(self, cursor="0", limit=20):
        account = await self.browser.get_account()
        result = await self.browser.list_folders(cursor=cursor, limit=limit)
        await self._check_account(account["id"])
        self.store.upsert_folders(account["id"], result["items"])
        return {"account": account, **result}

    async def sync(self, source="favorites", cursor="0", max_pages=3, folder_id=None):
        account = await self.browser.get_account()
        key = f"folder:{folder_id}" if source == "folder" else source
        seen = set()
        total = 0
        result = None
        for _ in range(max_pages):
            if cursor in seen:
                raise DouyinError(
                    "pagination_stalled", "抖音重复返回相同游标，已停止，不能视为同步完整。"
                )
            seen.add(cursor)
            result = await self.browser.list_videos(
                source, cursor=cursor, limit=20, folder_id=folder_id
            )
            await self._check_account(account["id"])
            self.store.upsert_videos(account["id"], key, result["items"])
            total += len(result["items"])
            if not result["has_more"]:
                break
            cursor = result["next_cursor"]
        return {
            "account": account,
            "source": key,
            "processed": total,
            "complete": not result["has_more"],
            "next_cursor": result["next_cursor"],
            "note": "缓存为已观察到的视频；不会因本次未出现而自动删除历史缓存。",
        }

    async def search(self, query="", source=None, limit=50, offset=0, include_summaries=True):
        account = await self.browser.get_account()
        items = self.store.list_videos(account["id"], source, query, limit, offset)
        if include_summaries:
            summaries = self.store.get_summaries_by_ids(account["id"], [v["id"] for v in items])
            for item in items:
                record = summaries.get(item["id"])
                if record:
                    item["native_summary"] = self._summary_result(record, cached=True)
        return {
            "account": account,
            "scope": "local_cache",
            "items": items,
        }

    @staticmethod
    def _summary_fresh(record):
        try:
            observed = datetime.fromisoformat(record["fetched_at"])
            if observed.tzinfo is None:
                return False
            age = datetime.now(UTC) - observed
            ttl = (
                timedelta(hours=24)
                if record["data"]["status"] == "available"
                else timedelta(minutes=15)
            )
            return timedelta(0) <= age < ttl
        except (ValueError, KeyError, TypeError):
            return False

    @classmethod
    def _summary_result(cls, record, *, cached):
        return {
            **record["data"],
            "fetched_at": record["fetched_at"],
            "cached": cached,
            "cache_expired": not cls._summary_fresh(record),
        }

    async def _read_summary(self, account_id, video_id, refresh):
        record = self.store.get_summary(account_id, video_id)
        if not refresh and record and self._summary_fresh(record):
            return self._summary_result(record, cached=True)
        result = await self.browser.get_video_summary(video_id)
        await self._check_account(account_id)
        self.store.upsert_summary(account_id, video_id, result)
        return self._summary_result(self.store.get_summary(account_id, video_id), cached=False)

    async def get_video_summary(self, video_id, refresh=False):
        video_id = numeric_id(video_id)
        account = await self.browser.get_account()
        result = await self._read_summary(account["id"], video_id, refresh)
        return {"account": account, **result}

    async def get_video_summaries(self, video_ids, refresh=False):
        if not isinstance(video_ids, list) or not 1 <= len(video_ids) <= 10:
            raise ValueError("video_ids 须为1–10个视频ID。")
        # Preserve first occurrence ordering; do not read or count duplicates twice.
        ids = list(dict.fromkeys(numeric_id(value) for value in video_ids))
        account = await self.browser.get_account()
        results = []
        for video_id in ids:
            try:
                result = await self._read_summary(account["id"], video_id, refresh)
                results.append(result)
            except DouyinError as exc:
                if exc.code in {
                    "account_mismatch",
                    "not_logged_in",
                    "login_or_verification_required",
                    "browser_unavailable",
                    "unexpected_origin",
                }:
                    raise  # Stop immediately when the account/session cannot be trusted.
                results.append({"video_id": video_id, "status": "error", "error": exc.as_dict()})
        await self._check_account(account["id"])
        return {
            "account": account,
            "items": results,
            "requested_count": len(video_ids),
            "unique_count": len(ids),
            "available_count": sum(r["status"] == "available" for r in results),
            "unavailable_count": sum(r["status"] == "unavailable" for r in results),
            "error_count": sum(r["status"] == "error" for r in results),
            "complete": all(r["status"] != "error" for r in results),
        }

    async def prepare_plan(self, assignments, allow_favorite_liked=False):
        account = await self.browser.get_account()
        cached = {
            v["id"]: v
            for v in self.store.get_videos_by_ids(
                account["id"], [a["video_id"] for a in assignments]
            )
        }
        additions = []
        for entry in assignments:
            row = cached.get(entry["video_id"])
            if row:
                sources = row.get("sources", [])
                favorite = "favorites" in sources or any(s.startswith("folder:") for s in sources)
                observed = row.get("extra", {}).get("is_favorited")
                if isinstance(observed, bool):
                    favorite = observed
                if not favorite:
                    additions.append(entry["video_id"])
        if additions and not allow_favorite_liked:
            raise DouyinError(
                "favorite_consent_required",
                "方案包含仅点赞的视频。将其放入收藏夹需要先收藏，请明确设置 allow_favorite_liked=true 后重新生成方案。",
            )
        plan = self.store.create_plan(account["id"], assignments)
        # Persist the intent in every operation for resumability across MCP restarts.
        for index, entry in enumerate(plan["assignments"]):
            row = cached.get(entry["video_id"], {})
            detail = {
                "title": row.get("title", ""),
                "will_favorite": entry["video_id"] in additions,
                "allow_favorite": allow_favorite_liked,
            }
            self.store.set_operation(plan["plan_id"], index, "pending", detail)
        plan = self.store.get_plan(plan["plan_id"])
        return {
            **plan,
            "effect": "将视频加入目标收藏夹；保留已有收藏夹归属与点赞。新收藏夹默认仅自己可见。",
            "executed": False,
        }

    async def plan_status(self, plan_id):
        account = await self.browser.get_account()
        plan = self.store.get_plan(plan_id)
        if plan["account_id"] != account["id"]:
            raise DouyinError("account_mismatch", "当前登录账号与方案账号不同。")
        return plan

    async def apply_plan(self, plan_id, max_operations=5):
        account = await self.browser.get_account()
        plan = self.store.get_plan(plan_id)
        if plan["account_id"] != account["id"]:
            raise DouyinError("account_mismatch", "当前登录账号与方案账号不同，未执行。")
        count = 0
        for index, entry in enumerate(plan["assignments"]):
            if entry["status"] == "completed":
                continue
            if count >= max_operations:
                break
            # A previous process may have stopped after the remote write. The browser
            # operation always checks the target membership before doing another write.
            intent = entry.get("detail") or {}
            self.store.set_operation(plan_id, index, "running", intent)
            try:
                current = await self.browser.get_account()
                if current["id"] != account["id"]:
                    raise DouyinError("account_mismatch", "账号已切换，停止执行。")
                result = await self.browser.add_to_named_folder(
                    entry["video_id"],
                    entry["folder_name"],
                    expected_account_id=account["id"],
                    allow_favorite=bool(intent.get("allow_favorite", intent.get("will_favorite"))),
                )
                if not result.get("verified"):
                    raise DouyinError("write_unverified", "操作结果未能回读验证，请检查后再继续。")
                await self._check_account(account["id"])
                self.store.set_operation(plan_id, index, "completed", {**intent, **result})
                if result.get("folder"):
                    self.store.upsert_folders(account["id"], [result["folder"]])
            except DouyinError as exc:
                status = (
                    "uncertain"
                    if exc.code
                    in {"write_unverified", "request_failed", "ui_changed", "write_uncertain"}
                    else "failed"
                )
                self.store.set_operation(plan_id, index, status, {**intent, "error": exc.as_dict()})
                break  # Never keep writing after an account, validation, or UI error.
            except Exception:  # noqa: BLE001 - persist unknown write outcome without leaking exception data
                self.store.set_operation(
                    plan_id,
                    index,
                    "uncertain",
                    {
                        **intent,
                        "error": {
                            "code": "unexpected_error",
                            "message": "执行中断，结果未确认。再次执行会先检查远端状态。",
                        },
                    },
                )
                break
            count += 1
        return self.store.get_plan(plan_id)
