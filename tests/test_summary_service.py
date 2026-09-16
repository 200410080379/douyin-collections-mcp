"""Native-summary workflows using synthetic metadata and temporary SQLite only."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from douyin_collections_mcp import service as service_module
from douyin_collections_mcp import storage as storage_module
from douyin_collections_mcp.errors import DouyinError
from douyin_collections_mcp.service import CollectionService
from douyin_collections_mcp.storage import Store

ACCOUNT_A = "1000000000000000001"
ACCOUNT_B = "1000000000000000002"
VIDEO_A = "2000000000000000001"
VIDEO_B = "2000000000000000002"
VIDEO_C = "2000000000000000003"


def summary(video_id=VIDEO_A, status="available", text="合成的平台摘要"):
    available = status == "available"
    return {
        "video_id": video_id,
        "title": "合成视频标题",
        "author": "合成作者",
        "url": f"https://www.douyin.com/video/{video_id}",
        "source": "douyin_native",
        "source_field": "recommend_chapter_info" if available else None,
        "status": status,
        "reason": None if available else "no_native_ai_summary",
        "summary": text if available else None,
        "chapters": [
            {
                "title": "合成章节",
                "summary": "合成的章节要点",
                "start_time_ms": 19000,
                "start_time_seconds": 19.0,
                "start_time": "00:19",
                "points": [],
            }
        ]
        if available
        else [],
        "ai_generated": available,
    }


class SummaryBrowser:
    def __init__(self):
        self.account_id = ACCOUNT_A
        self.account_reads = 0
        self.reads = []
        self.responses = {}
        self.switch_on_read = None

    async def get_account(self):
        self.account_reads += 1
        return {"id": self.account_id, "sec_uid": "synthetic-sec-uid", "nickname": "合成账号"}

    async def get_video_summary(self, video_id):
        self.reads.append(video_id)
        if self.switch_on_read == video_id:
            self.account_id = ACCOUNT_B
        result = self.responses.get(video_id, summary(video_id))
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        instant = datetime(2030, 1, 1, tzinfo=UTC)

        def advance(self, **kwargs):
            self.instant += timedelta(**kwargs)

    clock = Clock()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.instant.astimezone(tz) if tz else clock.instant.replace(tzinfo=None)

    monkeypatch.setattr(service_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(storage_module, "_now", lambda: clock.instant.isoformat())
    return clock


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "summary-service")
    yield instance
    instance.close()


async def test_available_summary_cache_hit_then_explicit_refresh(store, clock):
    browser = SummaryBrowser()
    service = CollectionService(browser, store)
    first = await service.get_video_summary(VIDEO_A)
    assert first["status"] == "available"
    assert first["cached"] is False
    assert first["cache_expired"] is False
    assert first["video_id"] == VIDEO_A
    browser.responses[VIDEO_A] = summary(text="更新后的合成摘要")
    clock.advance(minutes=1)
    cached = await service.get_video_summary(VIDEO_A)
    assert cached["cached"] is True
    assert cached["summary"] == first["summary"]
    assert cached["fetched_at"] == first["fetched_at"]
    assert browser.reads == [VIDEO_A]
    refreshed = await service.get_video_summary(VIDEO_A, refresh=True)
    assert refreshed["cached"] is False
    assert refreshed["summary"] == "更新后的合成摘要"
    assert refreshed["fetched_at"] != first["fetched_at"]
    assert browser.reads == [VIDEO_A, VIDEO_A]


@pytest.mark.parametrize("status, ttl_minutes", [("available", 1440), ("unavailable", 15)])
async def test_each_cache_status_expires_at_its_exact_ttl(store, clock, status, ttl_minutes):
    browser = SummaryBrowser()
    browser.responses[VIDEO_A] = summary(status=status)
    service = CollectionService(browser, store)
    first = await service.get_video_summary(VIDEO_A)
    clock.advance(minutes=ttl_minutes - 1, seconds=59)
    assert (await service.get_video_summary(VIDEO_A))["cached"] is True
    assert browser.reads == [VIDEO_A]
    clock.advance(seconds=1)
    browser.responses[VIDEO_A] = summary(text="过期后新读取的合成摘要")
    fresh = await service.get_video_summary(VIDEO_A)
    assert fresh["cached"] is False
    assert fresh["cache_expired"] is False
    assert fresh["status"] == "available"
    assert fresh["summary"] == "过期后新读取的合成摘要"
    assert fresh["fetched_at"] != first["fetched_at"]
    assert browser.reads == [VIDEO_A, VIDEO_A]


@pytest.mark.parametrize("initial_status", ["available", "unavailable"])
async def test_refresh_error_preserves_previous_cache_including_fetch_time(
    store,
    clock,
    initial_status,
):
    browser = SummaryBrowser()
    browser.responses[VIDEO_A] = summary(status=initial_status)
    service = CollectionService(browser, store)
    await service.get_video_summary(VIDEO_A)
    previous = store.get_summary(ACCOUNT_A, VIDEO_A)
    clock.advance(minutes=1)
    browser.responses[VIDEO_A] = DouyinError("request_failed", "合成网络中断", retryable=True)
    with pytest.raises(DouyinError, match="合成网络中断"):
        await service.get_video_summary(VIDEO_A, refresh=True)
    assert store.get_summary(ACCOUNT_A, VIDEO_A) == previous
    cached = await service.get_video_summary(VIDEO_A)
    assert cached["cached"] is True
    assert cached["status"] == initial_status
    assert browser.reads == [VIDEO_A, VIDEO_A]


async def test_account_switch_during_summary_read_caches_nothing(store, clock):
    browser = SummaryBrowser()
    browser.switch_on_read = VIDEO_A
    service = CollectionService(browser, store)
    with pytest.raises(DouyinError) as error:
        await service.get_video_summary(VIDEO_A)
    assert error.value.code == "account_mismatch"
    assert store.get_summary(ACCOUNT_A, VIDEO_A) is None
    assert store.get_summary(ACCOUNT_B, VIDEO_A) is None


async def test_summary_cache_is_scoped_to_current_account(store, clock):
    store.upsert_summary(ACCOUNT_A, VIDEO_A, summary(text="甲的合成缓存"))
    browser = SummaryBrowser()
    browser.account_id = ACCOUNT_B
    browser.responses[VIDEO_A] = summary(text="乙当前读取的合成摘要")
    result = await CollectionService(browser, store).get_video_summary(VIDEO_A)
    assert result["account"]["id"] == ACCOUNT_B
    assert result["cached"] is False
    assert result["summary"] == "乙当前读取的合成摘要"
    assert store.get_summary(ACCOUNT_A, VIDEO_A)["data"]["summary"] == "甲的合成缓存"


async def test_batch_preserves_first_occurrence_order_and_distinguishes_absence_from_error(
    store,
    clock,
):
    browser = SummaryBrowser()
    browser.responses[VIDEO_B] = summary(VIDEO_B, status="unavailable")
    browser.responses[VIDEO_C] = DouyinError("video_unavailable", "合成视频不可访问")
    result = await CollectionService(browser, store).get_video_summaries(
        [VIDEO_B, VIDEO_A, VIDEO_B, VIDEO_C, VIDEO_A]
    )
    assert browser.reads == [VIDEO_B, VIDEO_A, VIDEO_C]
    assert [row["video_id"] for row in result["items"]] == [VIDEO_B, VIDEO_A, VIDEO_C]
    assert [row["status"] for row in result["items"]] == ["unavailable", "available", "error"]
    assert result["requested_count"] == 5
    assert result["unique_count"] == 3
    assert result["available_count"] == 1
    assert result["unavailable_count"] == 1
    assert result["error_count"] == 1
    assert result["complete"] is False
    assert "error" not in result["items"][0]
    assert result["items"][2]["error"]["code"] == "video_unavailable"
    assert store.get_summary(ACCOUNT_A, VIDEO_B)["data"]["status"] == "unavailable"
    assert store.get_summary(ACCOUNT_A, VIDEO_C) is None


async def test_unavailable_batch_is_complete_and_uses_short_lived_cache(store, clock):
    browser = SummaryBrowser()
    browser.responses[VIDEO_A] = summary(status="unavailable")
    service = CollectionService(browser, store)
    first = await service.get_video_summaries([VIDEO_A])
    second = await service.get_video_summaries([VIDEO_A, VIDEO_A])
    assert first["complete"] is True
    assert second["items"][0]["cached"] is True
    assert second["unavailable_count"] == 1
    assert second["error_count"] == 0
    assert browser.reads == [VIDEO_A]


async def test_batch_refresh_error_does_not_replace_available_cache(store, clock):
    store.upsert_summary(ACCOUNT_A, VIDEO_A, summary(text="保留的合成摘要"))
    previous = store.get_summary(ACCOUNT_A, VIDEO_A)
    browser = SummaryBrowser()
    browser.responses[VIDEO_A] = DouyinError("platform_error", "合成平台错误")
    result = await CollectionService(browser, store).get_video_summaries(
        [VIDEO_A, VIDEO_B], refresh=True
    )
    assert [row["status"] for row in result["items"]] == ["error", "available"]
    assert browser.reads == [VIDEO_A, VIDEO_B]
    assert store.get_summary(ACCOUNT_A, VIDEO_A) == previous


@pytest.mark.parametrize(
    "code",
    [
        "not_logged_in",
        "login_or_verification_required",
        "account_mismatch",
        "browser_unavailable",
        "unexpected_origin",
    ],
)
async def test_batch_stops_immediately_on_session_or_account_errors(store, clock, code):
    browser = SummaryBrowser()
    browser.responses[VIDEO_B] = DouyinError(code, "合成会话错误")
    service = CollectionService(browser, store)
    with pytest.raises(DouyinError) as error:
        await service.get_video_summaries([VIDEO_A, VIDEO_B, VIDEO_C])
    assert error.value.code == code
    assert browser.reads == [VIDEO_A, VIDEO_B]
    assert store.get_summary(ACCOUNT_A, VIDEO_A) is not None
    assert store.get_summary(ACCOUNT_A, VIDEO_B) is None
    assert store.get_summary(ACCOUNT_A, VIDEO_C) is None


async def test_search_attaches_cached_summaries_by_default_without_remote_reads(store, clock):
    store.upsert_videos(
        ACCOUNT_A,
        "favorites",
        [
            {"id": VIDEO_A, "title": "有摘要的合成视频"},
            {"id": VIDEO_B, "title": "无缓存的合成视频"},
        ],
    )
    store.upsert_summary(ACCOUNT_A, VIDEO_A, summary())
    store.upsert_summary(ACCOUNT_B, VIDEO_B, summary(VIDEO_B, text="其他账号的合成缓存"))
    clock.advance(hours=25)
    browser = SummaryBrowser()
    service = CollectionService(browser, store)
    result = await service.search()
    items = {item["id"]: item for item in result["items"]}
    cached = items[VIDEO_A]["native_summary"]
    assert cached["summary"] == "合成的平台摘要"
    assert cached["cached"] is True
    assert cached["cache_expired"] is True
    assert "native_summary" not in items[VIDEO_B]
    disabled = await service.search(include_summaries=False)
    assert all("native_summary" not in item for item in disabled["items"])
    assert browser.reads == []


@pytest.mark.parametrize("ids", [[], [VIDEO_A] * 11, [VIDEO_A, "https://example.invalid/video"]])
async def test_invalid_batch_input_is_rejected_before_any_account_access(store, ids):
    browser = SummaryBrowser()
    with pytest.raises(ValueError):
        await CollectionService(browser, store).get_video_summaries(ids)
    assert browser.account_reads == 0
    assert browser.reads == []
