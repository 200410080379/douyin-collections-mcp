"""Normalize Douyin's explicitly marked native AI summary without generating text."""

from .errors import DouyinError
from .normalize import check_response


def _invalid(message: str) -> DouyinError:
    return DouyinError("unexpected_response", message)


def _text(value, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid(f"抖音摘要字段 {field} 格式变化。")
    return value


def _ai_flag(value) -> bool:
    # JSON integers and floats are both JS Numbers; booleans and strings are not.
    return type(value) in (int, float) and value == 1


def _clock(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _points(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid("抖音章节要点格式变化。")
    result = []
    for point in value:
        if not isinstance(point, dict):
            raise _invalid("抖音章节要点格式变化。")
        kind = point.get("type")
        # These are JSON equivalents of the site's String(point.type) === '1'.
        selected = kind == "1" or (type(kind) in (int, float) and kind == 1)
        if selected:
            result.append(
                {
                    "title": _text(point.get("desc"), "points.desc"),
                    "summary": _text(point.get("detail"), "points.detail"),
                }
            )
    return result


def _chapters(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid("抖音章节列表格式变化。")
    chapters = []
    previous = -1
    for item in value:
        if not isinstance(item, dict):
            raise _invalid("抖音章节内容格式变化。")
        timestamp = item.get("timestamp")
        if type(timestamp) is not int or timestamp < 0 or timestamp < previous:
            raise _invalid("抖音章节时间缺失、无效或顺序异常。")
        title = _text(item.get("desc"), "chapter.desc")
        summary = _text(item.get("detail"), "chapter.detail")
        points = _points(item.get("points"))
        if (
            not title.strip()
            and not summary.strip()
            and not any(point["title"].strip() or point["summary"].strip() for point in points)
        ):
            raise _invalid("抖音章节没有有效内容。")
        chapters.append(
            {
                "title": title,
                "summary": summary,
                "start_time_ms": timestamp,
                "start_time_seconds": timestamp / 1000,
                "start_time": _clock(timestamp),
                "points": points,
            }
        )
        previous = timestamp
    return chapters


def native_summary(data: dict, expected_video_id: str) -> dict:
    """Return only explicit platform AI summary text, with strict schema checks.

    Any non-null chapter_list (including []) takes precedence over recommended
    chapters, matching JavaScript truthiness. Captions and descriptions are
    never treated as summary material. source_field identifies the selected
    platform field even when it contains no usable native AI summary.
    """
    check_response(data)
    detail = data.get("aweme_detail")
    if detail is None or detail == {}:
        raise DouyinError("video_unavailable", "抖音未返回可访问的视频详情。")
    if not isinstance(detail, dict):
        raise _invalid("抖音视频详情格式变化。")
    raw_id = detail.get("aweme_id")
    if type(raw_id) not in (str, int) or not str(raw_id).isdigit():
        raise _invalid("抖音视频详情缺少有效标识。")
    if not isinstance(expected_video_id, str) or str(raw_id) != expected_video_id:
        raise DouyinError("video_unavailable", "抖音返回的视频与请求不一致。")
    author = detail.get("author")
    if author is None:
        author = {}
    if not isinstance(author, dict):
        raise _invalid("抖音视频作者格式变化。")
    result = {
        "video_id": expected_video_id,
        "title": _text(detail.get("desc"), "desc"),
        "author": _text(author.get("nickname"), "author.nickname"),
        "url": f"https://www.douyin.com/video/{expected_video_id}",
        "source": "douyin_native",
        "source_field": None,
        "status": "unavailable",
        "reason": "no_native_ai_summary",
        "summary": None,
        "chapters": [],
        "ai_generated": False,
    }

    top_chapters = detail.get("chapter_list")
    if top_chapters is not None:
        if not isinstance(top_chapters, list):
            raise _invalid("抖音章节列表格式变化。")
        result["source_field"] = "chapter_list"
        if not _ai_flag(detail.get("recommend_chapter_apply_status")):
            if top_chapters:
                result["reason"] = "unconfirmed_ai_chapters"
            return result
        abstract = _text(detail.get("chapter_abstract"), "chapter_abstract")
        chapters = _chapters(top_chapters)
    else:
        recommendation = detail.get("recommend_chapter_info")
        if recommendation is None:
            return result
        if not isinstance(recommendation, dict):
            raise _invalid("抖音原生摘要格式变化。")
        result["source_field"] = "recommend_chapter_info"
        if not _ai_flag(recommendation.get("chapter_recommend_type")):
            return result
        abstract = _text(recommendation.get("chapter_abstract"), "chapter_abstract")
        chapters = _chapters(recommendation.get("recommend_chapter_list"))

    if not abstract.strip() and not chapters:
        return result
    result.update(
        {
            "status": "available",
            "reason": None,
            "summary": abstract if abstract.strip() else None,
            "chapters": chapters,
            "ai_generated": True,
        }
    )
    return result
