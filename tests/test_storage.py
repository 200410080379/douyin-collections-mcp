import stat

import pytest

from douyin_collections_mcp.storage import Store


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
