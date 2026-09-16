import sqlite3
import stat
from datetime import UTC, datetime

import pytest

from douyin_collections_mcp.storage import Store


def native_summary(video_id="1", *, status="available"):
    return {"video_id": video_id, "source": "douyin_native", "status": status}


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "private")
    yield result
    result.close()


def test_cache_isolation_deduplication_and_membership(store):
    store.upsert_videos("a", "favorites", [{"id": "1", "title": "old"}] * 2)
    store.upsert_videos("a", "likes", [{"id": "1", "title": "new"}])
    store.upsert_videos("b", "favorites", [{"id": "1", "title": "private"}])
    rows = store.list_videos("a")
    assert len(rows) == 1
    assert rows[0]["title"] == "new"
    assert rows[0]["sources"] == ["favorites", "likes"]
    assert store.list_videos("b")[0]["title"] == "private"
    assert store.list_videos("c") == []
    assert store.list_videos("a", source="folder:1") == []


def test_search_is_parameterized_and_treats_wildcards_literally(store):
    titles = ["100% recipe", "my_recipe", "path\\name", "' OR 1=1 --", "normal"]
    store.upsert_videos(
        "a", "favorites", [{"id": str(i), "title": t} for i, t in enumerate(titles)]
    )
    for query, expected in [
        ("%", titles[0]),
        ("_", titles[1]),
        ("\\", titles[2]),
        (titles[3], titles[3]),
    ]:
        assert [v["title"] for v in store.list_videos("a", query=query)] == [expected]
    assert store.list_videos("' OR 1=1 --") == []
    assert len(store.list_videos("a")) == 5


def test_pagination_has_deterministic_tie_breaker(store):
    store.upsert_videos("a", "likes", [{"id": str(i)} for i in reversed(range(5))])
    assert [v["id"] for v in store.list_videos("a", limit=2)] == ["0", "1"]
    assert [v["id"] for v in store.list_videos("a", limit=2, offset=2)] == ["2", "3"]
    assert store.list_videos("a", offset=100) == []


def test_get_videos_by_ids_is_scoped_ordered_and_deduplicated(store):
    store.upsert_videos("a", "likes", [{"id": "1"}, {"id": "2"}])
    store.upsert_videos("a", "folder:9", [{"id": "2"}])
    store.upsert_videos("b", "favorites", [{"id": "3", "title": "private"}])
    result = store.get_videos_by_ids("a", ["2", "3", "1", "2", "missing"])
    assert [row["id"] for row in result] == ["2", "1"]
    assert result[0]["sources"] == ["folder:9", "likes"]
    assert store.get_videos_by_ids("a", []) == []
    assert store.get_videos_by_ids("a", ["' OR 1=1 --"]) == []
    assert store.get_videos_by_ids("b", ["3"])[0]["title"] == "private"


@pytest.mark.parametrize("ids", ["1", [""], [None], ["1"] * 201])
def test_get_videos_by_ids_rejects_invalid_input(store, ids):
    with pytest.raises(ValueError):
        store.get_videos_by_ids("a", ids)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"offset": -1},
        {"offset": 1.5},
        {"offset": 2**63},
        {"query": []},
        {"source": "folder:"},
    ],
)
def test_invalid_listing_arguments(store, kwargs):
    with pytest.raises(ValueError):
        store.list_videos("a", **kwargs)


def test_folders_are_scoped_and_upserted(store):
    store.upsert_folders("a", [{"id": "1", "name": "学习", "count": 1}])
    store.upsert_folders("b", [{"id": "1", "name": "旅行", "count": 20}])
    store.upsert_folders("a", [{"id": "1", "name": "学习", "count": 2}])
    assert store.list_folders("a") == [{"id": "1", "name": "学习", "count": 2}]
    assert store.list_folders("b") == [{"id": "1", "name": "旅行", "count": 20}]


def test_batch_validation_does_not_partially_write(store):
    with pytest.raises(ValueError):
        store.upsert_videos("a", "favorites", [{"id": "1"}, {"id": ""}])
    assert store.list_videos("a") == []
    with pytest.raises(ValueError):
        store.upsert_folders(
            "a", [{"id": "1", "name": "valid"}, {"id": "2", "name": "bad", "count": -1}]
        )
    assert store.list_folders("a") == []


def test_plan_validates_account_ownership_and_duplicates(store):
    store.upsert_videos("a", "favorites", [{"id": "1"}])
    assignment = {"video_id": "1", "folder_name": "学习"}
    with pytest.raises(ValueError, match="not cached"):
        store.create_plan("b", [assignment])
    with pytest.raises(ValueError, match="duplicate"):
        store.create_plan("a", [assignment, {**assignment, "folder_name": " 学习 "}])
    plan = store.create_plan("a", [assignment, {**assignment, "folder_name": "阅读"}])
    assert plan["status"] == "pending"
    assert len(plan["assignments"]) == 2
    assert plan["account_id"] == "a"
    assert plan["mode"] == "add"


@pytest.mark.parametrize(
    "assignments",
    [
        [],
        [{}],
        [{"video_id": "1", "folder_name": " "}],
        [{"video_id": "1", "folder_name": "x" * 16}],
        [{"video_id": "1", "folder_name": "📚" * 8}],
        [{"video_id": "1", "folder_name": "a\nb"}],
        [{"video_id": "1", "folder_name": "x"}] * 201,
    ],
)
def test_invalid_plans(store, assignments):
    store.upsert_videos("a", "likes", [{"id": "1"}])
    with pytest.raises(ValueError):
        store.create_plan("a", assignments)


@pytest.mark.parametrize("name", ["学" * 15, "📚" * 7 + "学"])
def test_folder_names_accept_platform_maximum(store, name):
    store.upsert_videos("a", "likes", [{"id": "1"}])
    plan = store.create_plan("a", [{"video_id": "1", "folder_name": name}])
    assert plan["assignments"][0]["folder_name"] == name


def test_plan_status_and_results_survive_reopen(tmp_path):
    store = Store(tmp_path / "data")
    store.upsert_videos("a", "folder:9", [{"id": "1"}, {"id": "2"}])
    plan = store.create_plan("a", [{"video_id": str(i), "folder_name": "学习"} for i in (1, 2)])
    plan_id = plan["plan_id"]
    store.set_operation(plan_id, 0, "running", {"started": True})
    assert store.get_plan(plan_id)["status"] == "running"
    store.set_operation(plan_id, 0, "completed", {"folder_id": "9", "verified": True})
    assert store.get_plan(plan_id)["status"] == "pending"
    store.set_operation(plan_id, 1, "uncertain", {"reason": "interrupted"})
    store.close()
    reopened = Store(tmp_path / "data")
    try:
        recovered = reopened.get_plan(plan_id)
        assert recovered["created_at"] == plan["created_at"]
        assert recovered["status"] == "uncertain"
        assert recovered["assignments"][0]["detail"] == {"folder_id": "9", "verified": True}
        reopened.set_operation(plan_id, 1, "failed", {"reason": "not added"})
        assert reopened.get_plan(plan_id)["status"] == "failed"
        reopened.set_operation(plan_id, 1, "completed", {"verified": True})
        assert reopened.get_plan(plan_id)["status"] == "completed"
    finally:
        reopened.close()


def test_plan_operation_validation(store):
    store.upsert_videos("a", "likes", [{"id": "1"}])
    assignment = [{"video_id": "1", "folder_name": "学习"}]
    with pytest.raises(ValueError):
        store.create_plan("a", assignment, mode="move")
    plan_id = store.create_plan("a", assignment)["plan_id"]
    for index, status, detail in [
        (-1, "pending", {}),
        (1, "pending", {}),
        (True, "pending", {}),
        (0, "invalid", {}),
        (0, "completed", []),
        (0, "completed", {"bad": float("nan")}),
    ]:
        with pytest.raises(ValueError):
            store.set_operation(plan_id, index, status, detail)
    with pytest.raises(ValueError):
        store.get_plan("missing")
    assert store.get_plan(plan_id)["status"] == "pending"


def test_private_permissions(store):
    assert stat.S_IMODE(store.data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600


def test_reopen_tightens_existing_permissions(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir(mode=0o755)
    db = directory / "collections.sqlite3"
    db.touch(mode=0o644)
    store = Store(directory)
    try:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(db.stat().st_mode) == 0o600
    finally:
        store.close()


def test_summaries_are_account_scoped_without_video_membership(store):
    available = native_summary()
    unavailable = native_summary(status="unavailable")
    store.upsert_summary("a", "1", available)
    store.upsert_summary("b", "1", unavailable)
    assert store.list_videos("a") == []
    assert store.get_summary("a", "1")["data"] == available
    assert store.get_summary("b", "1")["data"] == unavailable
    assert store.get_summary("c", "1") is None
    assert store.get_summary("a", "missing") is None
    fetched_at = datetime.fromisoformat(store.get_summary("a", "1")["fetched_at"])
    assert fetched_at.utcoffset() == datetime.now(UTC).utcoffset()


def test_summaries_survive_reopen_and_update(tmp_path, monkeypatch):
    first_time = "2026-09-16T01:00:00+00:00"
    second_time = "2026-09-16T02:00:00+00:00"
    monkeypatch.setattr("douyin_collections_mcp.storage._now", lambda: first_time)
    store = Store(tmp_path / "private")
    store.upsert_summary("a", "1", native_summary(status="unavailable"))
    before = store.get_summary("a", "1")
    store.close()
    reopened = Store(tmp_path / "private")
    try:
        assert reopened.get_summary("a", "1") == before
        monkeypatch.setattr("douyin_collections_mcp.storage._now", lambda: second_time)
        reopened.upsert_summary("a", "1", native_summary())
        assert reopened.get_summary("a", "1") == {
            "data": native_summary(),
            "fetched_at": second_time,
        }
    finally:
        reopened.close()


def test_batch_summaries_are_ordered_scoped_and_parameterized(store):
    for video_id in ("1", "2", "' OR 1=1 --"):
        store.upsert_summary("a", video_id, native_summary(video_id))
    store.upsert_summary("b", "3", native_summary("3"))
    result = store.get_summaries_by_ids("a", ["2", "3", "1", "2", "missing"])
    assert list(result) == ["2", "1"]
    assert result["2"] == store.get_summary("a", "2")
    assert store.get_summaries_by_ids("a", []) == {}
    assert list(store.get_summaries_by_ids("a", ["' OR 1=1 --"])) == ["' OR 1=1 --"]
    assert store.get_summaries_by_ids("' OR 1=1 --", ["1"]) == {}


@pytest.mark.parametrize("video_ids", ["1", [""], [None], ["1"] * 201])
def test_batch_summaries_reject_invalid_ids(store, video_ids):
    with pytest.raises(ValueError):
        store.get_summaries_by_ids("a", video_ids)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        native_summary("2"),
        {**native_summary(), "video_id": 1},
        {**native_summary(), "source": "model_generated"},
        {**native_summary(), "source": None},
        {**native_summary(), "status": "error"},
        {**native_summary(), "status": "pending"},
        {**native_summary(), "status": None},
        {**native_summary(), "status": []},
    ],
)
def test_invalid_summaries_never_replace_cached_data(store, payload):
    store.upsert_summary("a", "1", native_summary())
    before = store.get_summary("a", "1")
    with pytest.raises(ValueError):
        store.upsert_summary("a", "1", payload)
    assert store.get_summary("a", "1") == before


def test_summary_input_output_do_not_share_mutable_state(store):
    payload = native_summary()
    store.upsert_summary("a", "1", payload)
    payload["status"] = "unavailable"
    loaded = store.get_summary("a", "1")
    assert loaded["data"]["status"] == "available"
    loaded["data"]["status"] = "unavailable"
    assert store.get_summary("a", "1")["data"]["status"] == "available"


def test_full_normalized_summary_round_trips_without_keyword_filtering(store):
    payload = {
        **native_summary(),
        "title": "浏览器 Cookie 技术",
        "author": "HTTP课堂",
        "url": "https://www.douyin.com/video/1",
        "source_field": "recommend_chapter_info",
        "reason": None,
        "summary": "解释 Cookie、签名和请求头的工作原理。",
        "ai_generated": True,
        "chapters": [
            {
                "title": "什么是Cookie",
                "summary": "Cookie 是浏览器保存的会话数据。",
                "start_time_ms": 1500,
                "start_time_seconds": 1.5,
                "start_time": "00:01",
                "points": [{"title": "HTTP请求", "summary": "请求头与会话。"}],
            }
        ],
    }
    store.upsert_summary("a", "1", payload)
    assert store.get_summary("a", "1")["data"] == payload


@pytest.mark.parametrize(
    "extra",
    [
        {"aweme_detail": {}},
        {"cookies": {"sessionid": "never-persist"}},
        {"headers": {"Cookie": "never-persist"}},
        {"error": {"code": "platform_error"}},
        {"cached": True},
        {"reason": "request_failed"},
        {"summary": {"cookies": "never-persist"}},
        {"chapters": [{"raw_response": {}}]},
        {"chapters": [{"title": {"cookie": "never-persist"}}]},
        {"chapters": [{"points": [{"cookie": "never-persist"}]}]},
        {"chapters": [{"points": [{"summary": {"cookie": "never-persist"}}]}]},
        {"chapters": [{"start_time_seconds": float("nan")}]},
    ],
)
def test_summary_cache_rejects_raw_and_non_normalized_data(store, extra):
    with pytest.raises(ValueError):
        store.upsert_summary("a", "1", {**native_summary(), **extra})
    assert store.get_summary("a", "1") is None


def test_summary_table_is_added_without_changing_legacy_data(tmp_path):
    directory = tmp_path / "legacy"
    legacy = Store(directory)
    legacy.upsert_videos("a", "favorites", [{"id": "1", "title": "existing video"}])
    legacy.upsert_folders("a", [{"id": "9", "name": "学习", "count": 1}])
    plan = legacy.create_plan("a", [{"video_id": "1", "folder_name": "学习"}])
    legacy.set_operation(plan["plan_id"], 0, "completed", {"verified": True})
    videos_before = legacy.list_videos("a")
    folders_before = legacy.list_folders("a")
    plan_before = legacy.get_plan(plan["plan_id"])
    legacy.close()
    # An older on-disk schema has all existing tables but no native-summary table.
    with sqlite3.connect(directory / "collections.sqlite3") as db:
        db.execute("DROP TABLE video_summaries")
    reopened = Store(directory)
    try:
        assert reopened.list_videos("a") == videos_before
        assert reopened.list_folders("a") == folders_before
        assert reopened.get_plan(plan["plan_id"]) == plan_before
        assert reopened.get_summary("a", "1") is None
        reopened.upsert_summary("a", "1", native_summary())
        assert reopened.get_summary("a", "1")["data"] == native_summary()
        assert reopened.list_videos("a") == videos_before
        assert stat.S_IMODE(reopened.data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(reopened.db_path.stat().st_mode) == 0o600
    finally:
        reopened.close()
