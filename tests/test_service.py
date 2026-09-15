"""Workflow tests against temporary SQLite and a contract-level browser fake.

The fake models the browser's read-before-write contract. These tests verify
service recovery and intent persistence; they do not claim live UI idempotency.
"""

import asyncio

import pytest

from douyin_collections_mcp.errors import DouyinError
from douyin_collections_mcp.service import CollectionService
from douyin_collections_mcp.storage import Store

ACCOUNT_A = "7311111111111111111"
ACCOUNT_B = "7322222222222222222"
VIDEO_A = "1234567890123456789"
VIDEO_B = "1234567890123456790"


def cached_video(video_id=VIDEO_A, title="学习视频"):
    return {
        "id": video_id,
        "title": title,
        "author": "作者",
        "url": f"https://www.douyin.com/video/{video_id}",
        "kind": "video",
        "extra": {},
    }


def page(items, next_cursor=None):
    return {"items": items, "has_more": next_cursor is not None, "next_cursor": next_cursor}


class RemoteState:
    def __init__(self):
        self.memberships = set()
        self.writes = []


class FakeBrowser:
    def __init__(self, remote=None):
        self.account_id = ACCOUNT_A
        self.pages = {}
        self.reads = []
        self.calls = []
        self.remote = remote or RemoteState()
        self.after_write = None
        self.switch_after_write = False
        self.folders = []

    async def get_account(self):
        return {"id": self.account_id, "sec_uid": "self-sec-uid", "nickname": "本人"}

    async def list_videos(self, source, *, cursor, limit, folder_id=None):
        self.reads.append((source, cursor, limit, folder_id))
        return self.pages[(source, cursor)]

    async def list_folders(self, *, cursor, limit):
        return page(self.folders)

    async def add_to_named_folder(
        self,
        video_id,
        folder_name,
        *,
        expected_account_id,
        allow_favorite,
    ):
        assert expected_account_id == self.account_id
        self.calls.append((video_id, folder_name, expected_account_id, allow_favorite))
        key = (expected_account_id, video_id, folder_name)
        already_present = key in self.remote.memberships
        if not already_present:
            self.remote.memberships.add(key)
            self.remote.writes.append(key)
            if self.switch_after_write:
                self.account_id = ACCOUNT_B
            failure, self.after_write = self.after_write, None
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "network":
                raise DouyinError("request_failed", "响应中断", retryable=True)
            if failure == "unverified":
                return {"verified": False}
        return {
            "verified": True,
            "already_present": already_present,
            "folder": {"id": "7412345678912345678", "name": folder_name, "count": 1},
        }


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "data")
    yield instance
    instance.close()


async def test_sync_detects_repeated_cursor_and_preserves_observed_cache(store):
    browser = FakeBrowser()
    browser.pages = {
        ("favorites", "0"): page([cached_video()], "10"),
        ("favorites", "10"): page([cached_video(VIDEO_B)], "10"),
    }
    service = CollectionService(browser, store)
    with pytest.raises(DouyinError) as error:
        await service.sync(max_pages=5)
    assert error.value.code == "pagination_stalled"
    assert [request[1] for request in browser.reads] == ["0", "10"]
    assert {row["id"] for row in store.list_videos(ACCOUNT_A)} == {VIDEO_A, VIDEO_B}


async def test_bounded_sync_reports_continuation_then_terminal_page(store):
    browser = FakeBrowser()
    browser.pages = {
        ("likes", "0"): page([cached_video()], "1750000000000001"),
        ("likes", "1750000000000001"): page([cached_video(VIDEO_B)]),
    }
    service = CollectionService(browser, store)
    first = await service.sync("likes", max_pages=1)
    assert first["complete"] is False
    assert first["next_cursor"] == "1750000000000001"
    last = await service.sync("likes", cursor=first["next_cursor"], max_pages=1)
    assert last["complete"] is True
    assert last["next_cursor"] is None
    assert all(row["sources"] == ["likes"] for row in store.list_videos(ACCOUNT_A))


async def test_folder_read_is_cached_with_folder_membership(store):
    browser = FakeBrowser()
    browser.pages = {("folder", "0"): page([cached_video()])}
    service = CollectionService(browser, store)
    result = await service.list_videos("folder", folder_id="7412345678912345678")
    assert result["source"] == "folder:7412345678912345678"
    assert store.list_videos(ACCOUNT_A)[0]["sources"] == [result["source"]]


async def test_search_and_plan_access_are_isolated_by_current_account(store):
    store.upsert_videos(ACCOUNT_A, "favorites", [cached_video(title="甲的标题")])
    store.upsert_videos(ACCOUNT_B, "likes", [cached_video(title="乙的标题")])
    browser = FakeBrowser()
    service = CollectionService(browser, store)
    plan = await service.prepare_plan([{"video_id": VIDEO_A, "folder_name": "学习"}])
    browser.account_id = ACCOUNT_B
    assert [row["title"] for row in (await service.search())["items"]] == ["乙的标题"]
    for operation in (service.plan_status, service.apply_plan):
        with pytest.raises(DouyinError) as error:
            await operation(plan["plan_id"])
        assert error.value.code == "account_mismatch"
    assert browser.calls == []


async def test_like_only_video_needs_explicit_favorite_intent_and_preview_does_not_write(store):
    store.upsert_videos(ACCOUNT_A, "likes", [cached_video()])
    browser = FakeBrowser()
    service = CollectionService(browser, store)
    assignments = [{"video_id": VIDEO_A, "folder_name": "学习"}]
    with pytest.raises(DouyinError) as error:
        await service.prepare_plan(assignments)
    assert error.value.code == "favorite_consent_required"
    plan = await service.prepare_plan(assignments, allow_favorite_liked=True)
    assert plan["executed"] is False
    assert plan["assignments"][0]["detail"]["will_favorite"] is True
    assert browser.calls == []
    await service.apply_plan(plan["plan_id"])
    assert browser.calls[0][-1] is True


@pytest.mark.parametrize("existing_source", ["favorites", "folder:7412345678912345678"])
async def test_existing_favorite_never_grants_extra_favorite_permission(store, existing_source):
    store.upsert_videos(ACCOUNT_A, "likes", [cached_video()])
    store.upsert_videos(ACCOUNT_A, existing_source, [cached_video()])
    browser = FakeBrowser()
    service = CollectionService(browser, store)
    plan = await service.prepare_plan([{"video_id": VIDEO_A, "folder_name": "学习"}])
    assert plan["assignments"][0]["detail"]["will_favorite"] is False
    await service.apply_plan(plan["plan_id"])
    assert browser.calls[0][-1] is False


async def test_latest_unfavorited_observation_requires_and_persists_explicit_permission(store):
    # Membership cache deliberately retains historical favorites. The most
    # recent video observation says the person has since removed the favorite.
    store.upsert_videos(ACCOUNT_A, "favorites", [cached_video()])
    latest = cached_video()
    latest["extra"] = {"is_favorited": False, "is_liked": True}
    store.upsert_videos(ACCOUNT_A, "likes", [latest])
    assert store.list_videos(ACCOUNT_A)[0]["sources"] == ["favorites", "likes"]

    browser = FakeBrowser()
    service = CollectionService(browser, store)
    assignments = [{"video_id": VIDEO_A, "folder_name": "学习"}]
    with pytest.raises(DouyinError) as error:
        await service.prepare_plan(assignments, allow_favorite_liked=False)
    assert error.value.code == "favorite_consent_required"
    assert browser.calls == []

    plan = await service.prepare_plan(assignments, allow_favorite_liked=True)
    persisted = store.get_plan(plan["plan_id"])["assignments"][0]["detail"]
    assert persisted["will_favorite"] is True
    assert persisted["allow_favorite"] is True
    assert browser.calls == []

    # A new service instance consumes durable intent rather than the preview call's arguments.
    resumed = await CollectionService(browser, store).apply_plan(plan["plan_id"])
    assert resumed["status"] == "completed"
    assert browser.calls == [(VIDEO_A, "学习", ACCOUNT_A, True)]


async def test_apply_limit_and_resume_skip_completed_operations(store):
    store.upsert_videos(ACCOUNT_A, "favorites", [cached_video(), cached_video(VIDEO_B)])
    browser = FakeBrowser()
    service = CollectionService(browser, store)
    plan = await service.prepare_plan(
        [{"video_id": video_id, "folder_name": "学习"} for video_id in (VIDEO_A, VIDEO_B)]
    )
    partial = await service.apply_plan(plan["plan_id"], max_operations=1)
    assert [row["status"] for row in partial["assignments"]] == ["completed", "pending"]
    complete = await service.apply_plan(plan["plan_id"])
    assert complete["status"] == "completed"
    await service.apply_plan(plan["plan_id"])
    assert [call[0] for call in browser.calls] == [VIDEO_A, VIDEO_B]
    assert store.list_folders(ACCOUNT_A)[0]["name"] == "学习"


@pytest.mark.parametrize(
    "failure, first_status",
    [
        ("cancel", "running"),
        ("network", "uncertain"),
        ("unverified", "uncertain"),
    ],
)
async def test_restart_after_ambiguous_write_rechecks_without_duplicate_remote_write(
    tmp_path,
    failure,
    first_status,
):
    data_dir = tmp_path / "restart-data"
    first_store = Store(data_dir)
    remote = RemoteState()
    browser = FakeBrowser(remote)
    browser.after_write = failure
    try:
        first_store.upsert_videos(ACCOUNT_A, "likes", [cached_video()])
        first_service = CollectionService(browser, first_store)
        plan = await first_service.prepare_plan(
            [{"video_id": VIDEO_A, "folder_name": "学习"}],
            allow_favorite_liked=True,
        )
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await first_service.apply_plan(plan["plan_id"])
        else:
            await first_service.apply_plan(plan["plan_id"])
        assert first_store.get_plan(plan["plan_id"])["assignments"][0]["status"] == first_status
    finally:
        first_store.close()

    reopened = Store(data_dir)
    try:
        retry_browser = FakeBrowser(remote)
        resumed = await CollectionService(retry_browser, reopened).apply_plan(plan["plan_id"])
        assert resumed["status"] == "completed"
        assert resumed["assignments"][0]["detail"]["already_present"] is True
        assert retry_browser.calls[0][-1] is True  # Consent survived restart.
        assert remote.writes == [(ACCOUNT_A, VIDEO_A, "学习")]
    finally:
        reopened.close()


async def test_account_switch_between_operations_stops_remaining_writes(store):
    store.upsert_videos(ACCOUNT_A, "favorites", [cached_video(), cached_video(VIDEO_B)])
    browser = FakeBrowser()
    browser.switch_after_write = True
    service = CollectionService(browser, store)
    plan = await service.prepare_plan(
        [{"video_id": video_id, "folder_name": "学习"} for video_id in (VIDEO_A, VIDEO_B)]
    )
    result = await service.apply_plan(plan["plan_id"])
    assert [row["status"] for row in result["assignments"]] == ["failed", "pending"]
    assert result["assignments"][0]["detail"]["error"]["code"] == "account_mismatch"
    assert [call[0] for call in browser.calls] == [VIDEO_A]
    assert store.list_folders(ACCOUNT_A) == []
    assert store.list_folders(ACCOUNT_B) == []


async def test_uncertain_first_write_stops_later_operations(store):
    store.upsert_videos(ACCOUNT_A, "favorites", [cached_video(), cached_video(VIDEO_B)])
    browser = FakeBrowser()
    browser.after_write = "network"
    service = CollectionService(browser, store)
    plan = await service.prepare_plan(
        [{"video_id": video_id, "folder_name": "学习"} for video_id in (VIDEO_A, VIDEO_B)]
    )
    result = await service.apply_plan(plan["plan_id"])
    assert [row["status"] for row in result["assignments"]] == ["uncertain", "pending"]
    assert [call[0] for call in browser.calls] == [VIDEO_A]
