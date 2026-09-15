"""Normalize observed Douyin responses without exposing session or tracking data."""

from .errors import DouyinError


def check_response(data: dict) -> dict:
    if not isinstance(data, dict):
        raise DouyinError("unexpected_response", "抖音返回格式变化，未执行后续操作。")
    code = data.get("status_code")
    if code is None:
        raise DouyinError("unexpected_response", "响应缺少 status_code，无法确认是否成功。")
    if code != 0:
        if code in (8, 10000, 10001, 10002):
            raise DouyinError(
                "login_or_verification_required", "请在独立浏览器中检查登录或验证提示。"
            )
        raise DouyinError(
            "platform_error", f"抖音返回错误码 {int(code) if str(code).isdigit() else 'unknown'}。"
        )
    return data


def video(raw: dict) -> dict:
    video_id = str(raw.get("aweme_id") or "")
    if not video_id.isdigit():
        raise DouyinError("unexpected_response", "视频缺少有效的 aweme_id。")
    author = raw.get("author") or {}
    kind = "image" if raw.get("images") else "video"
    return {
        "id": video_id,
        "title": str(raw.get("desc") or (raw.get("article_info") or {}).get("article_title") or ""),
        "author": str(author.get("nickname") or ""),
        "url": f"https://www.douyin.com/{'note' if kind == 'image' else 'video'}/{video_id}",
        "kind": kind,
        "extra": {
            "created_at": raw.get("create_time"),
            "duration_ms": (raw.get("video") or {}).get("duration"),
            "aweme_type": raw.get("aweme_type"),
            "is_favorited": raw.get("collect_stat") == 1 if "collect_stat" in raw else None,
            "is_liked": raw.get("user_digged") != 0 if "user_digged" in raw else None,
        },
    }


def videos_page(data: dict) -> dict:
    check_response(data)
    items = data.get("aweme_list")
    if items is None:
        if data.get("has_more") in (False, 0):
            items = []
        else:
            raise DouyinError("unexpected_response", "视频列表字段缺失，不能视为已同步完毕。")
    if not isinstance(items, list):
        raise DouyinError("unexpected_response", "视频列表格式变化。")
    cursor = data.get("max_cursor", data.get("cursor"))
    more = bool(data.get("has_more"))
    if more and cursor is None:
        raise DouyinError("unexpected_response", "分页游标缺失，无法继续读取。")
    return {
        "items": [video(v) for v in items],
        "next_cursor": str(cursor) if more else None,
        "has_more": more,
    }


def folders_page(data: dict) -> dict:
    check_response(data)
    items = data.get("collects_list")
    if items is None:
        items = data.get("collects")
    if not isinstance(items, list):
        raise DouyinError("unexpected_response", "收藏夹列表字段缺失。")
    folders = []
    for item in items:
        fid = str(item.get("collects_id_str") or item.get("collects_id") or "")
        name = item.get("collects_name")
        if not fid.isdigit() or not isinstance(name, str):
            raise DouyinError("unexpected_response", "收藏夹标识或名称缺失。")
        # Read status differs from write secret: status 0 = private; secret 1 = private.
        status = item.get("status")
        folders.append(
            {
                "id": fid,
                "name": name,
                "count": item.get("total_number", 0),
                "visibility": "private" if status == 0 else "public" if status == 1 else "unknown",
            }
        )
    cursor = data.get("cursor", data.get("max_cursor"))
    more = bool(data.get("has_more"))
    if more and cursor is None:
        raise DouyinError("unexpected_response", "收藏夹分页游标缺失。")
    return {"items": folders, "next_cursor": str(cursor) if more else None, "has_more": more}
