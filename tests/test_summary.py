"""Synthetic fixtures for native summaries; no account data or generated summaries."""

from copy import deepcopy

import pytest

from douyin_collections_mcp.errors import DouyinError
from douyin_collections_mcp.summary import native_summary

VIDEO_ID = "1234567890123456789"
OTHER_VIDEO_ID = "1234567890123456790"


def response(**fields):
    return {
        "status_code": 0,
        "aweme_detail": {
            "aweme_id": VIDEO_ID,
            "desc": "合成测试标题，不是摘要",
            "author": {"nickname": "合成作者"},
            "chapter_list": None,
            **fields,
        },
    }


def chapter(timestamp=19000, title="合成章节", summary="合成章节正文", **extra):
    return {"desc": title, "detail": summary, "timestamp": timestamp, **extra}


def recommendation(abstract="平台合成测试摘要", chapters=None, **fields):
    return {
        "chapter_abstract": abstract,
        "chapter_recommend_type": 1,
        "recommend_chapter_list": [] if chapters is None else chapters,
        **fields,
    }


def normalize_recommendation(**fields):
    return native_summary(response(recommend_chapter_info=recommendation(**fields)), VIDEO_ID)


def test_native_summary_preserves_original_text_and_metadata_without_rewriting():
    original = " 第一段原文。\n\n第二段原文，保留空格。 "
    result = normalize_recommendation(abstract=original, chapters=[chapter()])
    assert result["video_id"] == VIDEO_ID
    assert result["title"] == "合成测试标题，不是摘要"
    assert result["author"] == "合成作者"
    assert result["url"] == f"https://www.douyin.com/video/{VIDEO_ID}"
    assert result["source"] == "douyin_native"
    assert result["source_field"] == "recommend_chapter_info"
    assert result["status"] == "available"
    assert result["reason"] is None
    assert result["ai_generated"] is True
    assert result["summary"] == original
    assert result["chapters"][0]["title"] == "合成章节"
    assert result["chapters"][0]["summary"] == "合成章节正文"


def test_empty_abstract_with_valid_ai_chapters_is_available():
    result = normalize_recommendation(abstract="", chapters=[chapter()])
    assert result["status"] == "available"
    assert result["summary"] is None
    assert result["ai_generated"] is True
    assert len(result["chapters"]) == 1


@pytest.mark.parametrize("abstract", [None, "", " \n "])
def test_empty_ai_payload_is_unavailable_without_fabricated_text(abstract):
    result = normalize_recommendation(abstract=abstract)
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_native_ai_summary"
    assert result["summary"] is None
    assert result["chapters"] == []
    assert result["ai_generated"] is False


def test_description_captions_subtitles_aigc_and_unknown_schema_never_become_summary():
    data = response(
        caption="字幕正文",
        video_text="逐字稿正文",
        is_aigc_media=1,
        douyin_p_c_video_extra={"ai_summary": "未知schema不得使用"},
    )
    result = native_summary(data, VIDEO_ID)
    assert result["source_field"] is None
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_native_ai_summary"
    assert result["summary"] is None
    assert result["chapters"] == []
    assert result["ai_generated"] is False


def test_handwritten_chapters_are_not_labeled_or_returned_as_ai_summary():
    data = response(
        chapter_list=[chapter(title="作者手写章节")],
        chapter_abstract="作者手写摘要",
        is_aigc_media=1,
        recommend_chapter_info=recommendation(),
    )
    result = native_summary(data, VIDEO_ID)
    assert result["source_field"] == "chapter_list"
    assert result["reason"] == "unconfirmed_ai_chapters"
    assert result["status"] == "unavailable"
    assert result["summary"] is None
    assert result["chapters"] == []
    assert result["ai_generated"] is False


def test_applied_ai_chapters_take_precedence_over_recommended_ones():
    result = native_summary(
        response(
            chapter_list=[chapter(title="已应用AI章节")],
            chapter_abstract="已应用AI摘要",
            recommend_chapter_apply_status=1,
            recommend_chapter_info=recommendation(abstract="未采用的推荐摘要"),
        ),
        VIDEO_ID,
    )
    assert result["source_field"] == "chapter_list"
    assert result["summary"] == "已应用AI摘要"
    assert result["chapters"][0]["title"] == "已应用AI章节"
    assert result["ai_generated"] is True


@pytest.mark.parametrize("apply_status", [None, 0, 1, True, "1"])
def test_empty_top_level_array_still_blocks_recommendation_fallback(apply_status):
    result = native_summary(
        response(
            chapter_list=[],
            recommend_chapter_apply_status=apply_status,
            recommend_chapter_info=recommendation(),
        ),
        VIDEO_ID,
    )
    assert result["source_field"] == "chapter_list"
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_native_ai_summary"
    assert result["summary"] is None


def test_empty_top_level_array_with_applied_ai_abstract_is_available():
    result = native_summary(
        response(
            chapter_list=[],
            chapter_abstract="已应用的摘要原文",
            recommend_chapter_apply_status=1,
        ),
        VIDEO_ID,
    )
    assert result["status"] == "available"
    assert result["summary"] == "已应用的摘要原文"
    assert result["chapters"] == []


@pytest.mark.parametrize("flag", [True, False, "1", "true", 0, None])
def test_recommended_ai_flag_requires_numeric_one(flag):
    result = normalize_recommendation(chapter_recommend_type=flag)
    assert result["status"] == "unavailable"
    assert result["summary"] is None
    assert result["ai_generated"] is False


@pytest.mark.parametrize("flag", [True, "1"])
def test_applied_ai_flag_does_not_accept_boolean_or_string(flag):
    result = native_summary(
        response(
            chapter_list=[chapter()],
            chapter_abstract="无有效AI标记",
            recommend_chapter_apply_status=flag,
        ),
        VIDEO_ID,
    )
    assert result["reason"] == "unconfirmed_ai_chapters"
    assert result["ai_generated"] is False


@pytest.mark.parametrize("flag", [1, 1.0])
def test_numeric_one_matches_javascript_strict_equality(flag):
    assert normalize_recommendation(chapter_recommend_type=flag)["status"] == "available"
    result = native_summary(
        response(
            chapter_list=[chapter()],
            recommend_chapter_apply_status=flag,
        ),
        VIDEO_ID,
    )
    assert result["status"] == "available"


@pytest.mark.parametrize(
    "timestamp, seconds, clock",
    [
        (0, 0.0, "00:00"),
        (19000, 19.0, "00:19"),
        (19500, 19.5, "00:19"),
        (3600000, 3600.0, "01:00:00"),
        (3723000, 3723.0, "01:02:03"),
    ],
)
def test_millisecond_time_units_are_exact(timestamp, seconds, clock):
    result = normalize_recommendation(chapters=[chapter(timestamp=timestamp)])
    normalized = result["chapters"][0]
    assert normalized["start_time_ms"] == timestamp
    assert normalized["start_time_seconds"] == seconds
    assert isinstance(normalized["start_time_seconds"], float)
    assert normalized["start_time"] == clock


def test_chapter_order_is_preserved_with_non_decreasing_timestamps():
    result = normalize_recommendation(
        chapters=[
            chapter(0, title="先出现"),
            chapter(0, title="同时出现"),
            chapter(19000, title="后出现"),
        ]
    )
    assert [row["title"] for row in result["chapters"]] == ["先出现", "同时出现", "后出现"]


@pytest.mark.parametrize("timestamp", [-1, 19000.0, "19000", None, True])
def test_invalid_chapter_time_rejects_entire_payload_without_partial_output(timestamp):
    with pytest.raises(DouyinError) as error:
        normalize_recommendation(chapters=[chapter(0), chapter(timestamp)])
    assert error.value.code == "unexpected_response"


def test_decreasing_timestamps_are_not_silently_sorted():
    with pytest.raises(DouyinError) as error:
        normalize_recommendation(chapters=[chapter(19000), chapter(0)])
    assert error.value.code == "unexpected_response"


@pytest.mark.parametrize(
    "info",
    [
        [],
        "unexpected",
        1,
        recommendation(abstract=[]),
        recommendation(chapters={"desc": "not-a-list"}),
        recommendation(chapters=[chapter(0), "bad-entry"]),
        recommendation(chapters=[chapter(0), chapter(title=1)]),
        recommendation(chapters=[chapter(0), chapter(summary={})]),
        recommendation(chapters=[chapter(title="", summary="")]),
    ],
)
def test_changed_recommendation_schema_is_reported(info):
    with pytest.raises(DouyinError) as error:
        native_summary(response(recommend_chapter_info=info), VIDEO_ID)
    assert error.value.code == "unexpected_response"


@pytest.mark.parametrize("top_level", [{}, "chapters", True])
def test_invalid_top_level_chapter_list_does_not_fallback(top_level):
    with pytest.raises(DouyinError) as error:
        native_summary(
            response(
                chapter_list=top_level,
                recommend_chapter_info=recommendation(),
            ),
            VIDEO_ID,
        )
    assert error.value.code == "unexpected_response"


def test_points_only_include_type_one_and_preserve_the_original_text():
    points = [
        {"type": 1, "desc": " 数字类型要点 ", "detail": "原文A\n原文B"},
        {"type": "1", "desc": "字符串类型要点", "detail": "另一段原文"},
        {"type": 1.0, "desc": "JS String(1.0)也是1", "detail": "保留"},
        {"type": True, "desc": "不展示布尔类型", "detail": "忽略"},
        {"type": 2, "desc": "不展示类型二", "detail": "忽略"},
    ]
    result = normalize_recommendation(chapters=[chapter(points=points)])
    assert result["chapters"][0]["points"] == [
        {"title": " 数字类型要点 ", "summary": "原文A\n原文B"},
        {"title": "字符串类型要点", "summary": "另一段原文"},
        {"title": "JS String(1.0)也是1", "summary": "保留"},
    ]
    assert normalize_recommendation(chapters=[chapter()])["chapters"][0]["points"] == []


@pytest.mark.parametrize("points", ["changed", [None], [{"type": 1, "desc": []}]])
def test_invalid_points_do_not_produce_partial_summary(points):
    with pytest.raises(DouyinError) as error:
        normalize_recommendation(chapters=[chapter(points=points)])
    assert error.value.code == "unexpected_response"


@pytest.mark.parametrize("detail", [None, {}])
def test_missing_or_empty_detail_is_video_unavailable(detail):
    with pytest.raises(DouyinError) as error:
        native_summary({"status_code": 0, "aweme_detail": detail}, VIDEO_ID)
    assert error.value.code == "video_unavailable"


def test_absent_detail_is_video_unavailable():
    with pytest.raises(DouyinError) as error:
        native_summary({"status_code": 0}, VIDEO_ID)
    assert error.value.code == "video_unavailable"


@pytest.mark.parametrize("detail", [[], "changed", True])
def test_wrong_detail_type_is_an_unexpected_response(detail):
    with pytest.raises(DouyinError) as error:
        native_summary({"status_code": 0, "aweme_detail": detail}, VIDEO_ID)
    assert error.value.code == "unexpected_response"


def test_mismatched_video_id_never_returns_another_videos_summary():
    with pytest.raises(DouyinError) as error:
        native_summary(
            response(aweme_id=OTHER_VIDEO_ID, recommend_chapter_info=recommendation()), VIDEO_ID
        )
    assert error.value.code == "video_unavailable"


def test_large_numeric_video_id_is_matched_without_float_conversion():
    result = native_summary(
        response(
            aweme_id=int(VIDEO_ID),
            recommend_chapter_info=recommendation(),
        ),
        VIDEO_ID,
    )
    assert result["video_id"] == VIDEO_ID


@pytest.mark.parametrize("code", [8, 10000, 10001, 10002])
def test_failed_login_is_not_misreported_as_no_native_summary(code):
    with pytest.raises(DouyinError) as error:
        native_summary({"status_code": code}, VIDEO_ID)
    assert error.value.code == "login_or_verification_required"


def test_missing_status_and_malformed_metadata_are_not_silently_accepted():
    with pytest.raises(DouyinError) as error:
        native_summary({"aweme_detail": {}}, VIDEO_ID)
    assert error.value.code == "unexpected_response"
    with pytest.raises(DouyinError) as error:
        native_summary(response(author=[]), VIDEO_ID)
    assert error.value.code == "unexpected_response"


def test_normalization_does_not_modify_platform_payload():
    payload = response(recommend_chapter_info=recommendation(chapters=[chapter()]))
    before = deepcopy(payload)
    native_summary(payload, VIDEO_ID)
    assert payload == before
