"""Dedicated browser session and same-origin API transport.

Only Douyin account/collection endpoints are exposed. No arbitrary browser tool,
remote evaluation, OS input simulation, credential export, or downloads.
"""

import asyncio
import json
import time
from collections import deque
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .config import Settings
from .errors import DouyinError
from .normalize import check_response, folders_page, videos_page

ORIGIN = "https://www.douyin.com"
SELF = "/aweme/v1/web/user/profile/self/"
FAVORITES = "/aweme/v1/web/aweme/listcollection/"
LIKES = "/aweme/v1/web/aweme/favorite/"
FOLDERS = "/aweme/v1/web/collects/list/"
FOLDER_VIDEOS = "/aweme/v1/web/collects/video/list/"
MAINTAIN_FOLDER = "/aweme/v1/web/collects/maintain/"
MOVE_VIDEO = "/aweme/v1/web/collects/video/move/"
DETAIL = "/aweme/v1/web/aweme/detail/"
COLLECT = "/aweme/v1/web/aweme/collect/"
READ_PATHS = {SELF, FAVORITES, LIKES, FOLDERS, FOLDER_VIDEOS}
READ_PATHS.add(DETAIL)
WRITE_PATHS = {MAINTAIN_FOLDER, MOVE_VIDEO, COLLECT}


def numeric_id(value: str) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 30:
        raise ValueError("ID 必须是 1–30 位数字字符串。")
    return value


class BrowserClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.context = None
        self.page = None
        self.playwright = None
        self._templates = {}
        self._responses = {}
        self._tasks = set()
        self._last_request = 0.0
        self._events = deque(maxlen=100)
        self.account = None

    async def start(self):
        if self.context and self.page and not self.page.is_closed():
            return
        if self.context or self.playwright:
            await self.close()
        self.settings.prepare()
        self.settings.profile_dir.mkdir(mode=0o700, exist_ok=True)
        self.settings.profile_dir.chmod(0o700)
        self.playwright = await async_playwright().start()
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.settings.profile_dir),
                channel=None if self.settings.channel == "chromium" else self.settings.channel,
                headless=self.settings.headless,
                viewport={"width": 1280, "height": 900},
                locale="zh-CN",
                accept_downloads=False,
                timeout=self.settings.timeout_ms,
            )
            self.page = (
                self.context.pages[0] if self.context.pages else await self.context.new_page()
            )
            self.page.set_default_timeout(self.settings.timeout_ms)
            self.context.on("response", self._on_response)
            await self.page.goto(ORIGIN, wait_until="domcontentloaded")
        except PlaywrightError:
            await self.close()
            raise DouyinError(
                "browser_unavailable",
                "独立浏览器无法启动。请检查 Chrome 是否已安装，以及是否有另一服务占用该浏览器目录。",
            ) from None

    def _on_response(self, response):
        parsed = urlsplit(response.url)
        if parsed.scheme != "https" or parsed.netloc != "www.douyin.com":
            return
        if not parsed.path.startswith("/aweme/v1/web/"):
            return
        # Diagnostics contains paths/methods only, never cookies or signed URLs.
        self._events.append(
            {"path": parsed.path, "method": response.request.method, "status": response.status}
        )
        if parsed.path not in READ_PATHS:
            return
        task = asyncio.create_task(self._record_response(response, parsed))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record_response(self, response, parsed):
        try:
            data = await response.json()
            if not isinstance(data, dict) or data.get("status_code") != 0:
                return
            request = response.request
            body = request.post_data or ""
            self._templates[parsed.path] = {
                "method": request.method,
                "params": {
                    k: v
                    for k, v in parse_qsl(parsed.query)
                    if k.lower() not in {"a_bogus", "x-bogus", "signature"}
                },
                "body": dict(parse_qsl(body)),
            }
            self._responses[parsed.path] = {"data": data, "at": time.monotonic()}
        except (PlaywrightError, ValueError, json.JSONDecodeError):
            return

    async def close(self):
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.context:
            try:
                await self.context.close()
            except PlaywrightError:
                pass
        if self.playwright:
            await self.playwright.stop()
        self.context = self.page = self.playwright = None

    async def login_start(self):
        await self.start()
        await self.page.goto(f"{ORIGIN}/user/self", wait_until="domcontentloaded")
        await self.page.bring_to_front()
        # Login is completed by the person. No OTP, password, or session values enter MCP.
        return {
            "browser_open": True,
            "message": "请在独立 Chrome 窗口中登录抖音；完成后调用 douyin_login_status。",
        }

    async def session_status(self):
        if not self.context:
            return {"browser_open": False, "session_present": False, "logged_in": False}
        cookies = await self.context.cookies(ORIGIN)
        present = any(c["name"] in {"sessionid", "sessionid_ss"} and c["value"] for c in cookies)
        if not present:
            self.account = None
            return {"browser_open": True, "session_present": False, "logged_in": False}
        try:
            account = await self.get_account()
            return {
                "browser_open": True,
                "session_present": True,
                "logged_in": True,
                "account": account,
            }
        except DouyinError as exc:
            return {
                "browser_open": True,
                "session_present": True,
                "logged_in": False,
                "error": exc.as_dict(),
            }

    async def get_account(self):
        await self.start()
        data = await self._request(SELF)
        user = data.get("user") or data.get("user_info")
        if not isinstance(user, dict) or not user.get("uid"):
            raise DouyinError(
                "not_logged_in", "无法确认当前账号，请先调用 douyin_login_start 并登录。"
            )
        account = {
            "id": str(user["uid"]),
            "sec_uid": str(user.get("sec_uid") or ""),
            "nickname": str(user.get("nickname") or ""),
        }
        self.account = account
        return account

    async def _request(self, path, *, params=None, body=None, _write=False):
        if path not in (WRITE_PATHS if _write else READ_PATHS):
            raise DouyinError("unsupported_endpoint", "此接口不在读取白名单内。")
        await self.start()
        if urlsplit(self.page.url).netloc != "www.douyin.com":
            raise DouyinError("unexpected_origin", "浏览器已离开抖音页面，请重新打开登录窗口。")
        template = self._templates.get(path, {})
        # The site's runtime signs fetch and inserts fresh session parameters.
        # Reusing observed msToken/verification parameters invalidates later requests.
        query = {"aid": "6383", "device_platform": "webapp", **(params or {})}
        if _write:
            query = {
                "channel": "channel_pc_web",
                "pc_client_type": "1",
                "version_code": "170400",
                "version_name": "17.4.0",
                **query,
            }
        method = (
            "POST" if _write else template.get("method", "POST" if path == FAVORITES else "GET")
        )
        request_body = dict(body or {}) if method == "POST" else None
        delay = 0.7 - (time.monotonic() - self._last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_request = time.monotonic()
        try:
            if _write:
                await self.page.wait_for_function(
                    "window.axiosInstance && typeof window.axiosInstance.request === 'function'",
                    timeout=10000,
                )
            result = await self.page.evaluate(
                """async ({path, query, method, body, useSiteClient}) => {
                if (useSiteClient) {
                    // Use the site's own request interceptors for current CSRF,
                    // ticket-guard and device headers. Never export those headers.
                    const response = await window.axiosInstance.request({
                        url: path, method, params: query,
                        paramsSerializer: params => {
                            const search = new URLSearchParams();
                            for (const [k,v] of Object.entries(params)) {
                                if (v === undefined || v === null) continue;
                                if (Array.isArray(v)) for (const item of v) search.append(k, String(item));
                                else search.append(k, String(v));
                            }
                            return search.toString();
                        },
                        data: body && Object.keys(body).length ? new URLSearchParams(body).toString() : '',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                        withCredentials: true, timeout: 18000,
                        responseType: 'text', transformResponse: [raw => raw],
                        validateStatus: () => true,
                    });
                    return {status: response.status, text: response.data};
                }
                const url = new URL(path, location.origin);
                for (const [k,v] of Object.entries(query)) {
                    url.searchParams.delete(k);
                    if (Array.isArray(v)) for (const item of v) url.searchParams.append(k, String(item));
                    else url.searchParams.set(k, String(v));
                }
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 18000);
                try {
                    const options = {method, credentials: 'include', signal: controller.signal};
                    if (body !== null) {
                        options.headers = {'Content-Type': 'application/x-www-form-urlencoded'};
                        options.body = new URLSearchParams(body).toString();
                    }
                    const response = await fetch(url.toString(), options);
                    // Preserve 64-bit IDs. JSON.parse in JavaScript rounds numeric IDs.
                    return {status: response.status, text: await response.text()};
                } finally { clearTimeout(timer); }
            }""",
                {
                    "path": path,
                    "query": query,
                    "method": method,
                    "body": request_body,
                    "useSiteClient": _write,
                },
            )
        except PlaywrightError:
            raise DouyinError(
                "request_failed",
                "抖音请求未完成，请检查独立浏览器是否需要登录或验证。",
                retryable=True,
            ) from None
        if result["status"] in (401, 403, 429):
            raise DouyinError(
                "login_or_verification_required", "抖音要求登录、验证或暂缓请求，请在浏览器中处理。"
            )
        try:
            data = json.loads(result.get("text", ""))
        except (ValueError, TypeError):
            data = None
        if result["status"] != 200 or not isinstance(data, dict):
            raise DouyinError(
                "unexpected_response",
                "接口没有返回可用 JSON。可能需要在浏览器中完成验证或更新接口适配。",
            )
        return check_response(data)

    async def list_videos(self, source, *, cursor="0", limit=20, folder_id=None):
        if source == "favorites":
            data = await self._request(FAVORITES, body={"cursor": cursor, "count": str(limit)})
        elif source == "likes":
            account = await self.get_account()
            if not account["sec_uid"]:
                raise DouyinError("unexpected_response", "账号响应缺少 sec_uid。")
            data = await self._request(
                LIKES,
                params={
                    "sec_user_id": account["sec_uid"],
                    "max_cursor": cursor,
                    "count": str(limit),
                },
            )
        elif source == "folder":
            data = await self._request(
                FOLDER_VIDEOS,
                params={
                    "collects_id": numeric_id(folder_id),
                    "cursor": cursor,
                    "count": str(limit),
                },
            )
        else:
            raise ValueError("source 必须是 favorites、likes 或 folder。")
        return videos_page(data)

    async def list_folders(self, *, cursor="0", limit=20):
        return folders_page(
            await self._request(FOLDERS, params={"cursor": cursor, "count": str(limit)})
        )

    async def all_folders(self):
        folders, seen, cursor = [], set(), "0"
        for _ in range(30):
            if cursor in seen:
                raise DouyinError("pagination_stalled", "收藏夹游标重复，无法确认全部收藏夹。")
            seen.add(cursor)
            result = await self.list_folders(cursor=cursor, limit=20)
            folders.extend(result["items"])
            if not result["has_more"]:
                return folders
            cursor = result["next_cursor"]
        raise DouyinError("page_limit", "收藏夹数量超过本次读取上限，未继续写入。")

    async def folder_contains(self, folder_id, video_id):
        cursor, seen = "0", set()
        for _ in range(100):
            if cursor in seen:
                raise DouyinError(
                    "pagination_stalled", "收藏夹内容游标重复，无法确认视频是否存在。"
                )
            seen.add(cursor)
            result = await self.list_videos("folder", folder_id=folder_id, cursor=cursor, limit=50)
            if any(v["id"] == video_id for v in result["items"]):
                return True
            if not result["has_more"]:
                return False
            cursor = result["next_cursor"]
        raise DouyinError("page_limit", "收藏夹内容超过本次检查上限，未继续写入。")

    async def _write(self, path, params, expected_account_id, body=None):
        await self.assert_account(expected_account_id)
        try:
            return await self._request(path, params=params, body=body, _write=True)
        except DouyinError as exc:
            if exc.code in {"request_failed", "unexpected_response"}:
                raise DouyinError(
                    "write_uncertain", "写入请求的结果未确认，必须回读后才能决定是否重试。"
                ) from None
            raise

    async def assert_account(self, expected_account_id):
        current = await self.get_account()
        if current["id"] != expected_account_id:
            raise DouyinError("account_mismatch", "账号已切换，停止本次操作。")

    async def _create_folder_ui(self, name, expected_account_id):
        """Submit the official private-folder form, including its runtime request hooks."""
        try:
            await self.page.goto(
                f"{ORIGIN}/user/self?showTab=favorite_collection", wait_until="domcontentloaded"
            )
            tab = self.page.get_by_role("tab", name="收藏夹", exact=True)
            trigger = self.page.locator("#user_detail_element").get_by_text(
                "新建收藏夹", exact=True
            )
            # The page hydrates its selected tab after navigation. Re-select only
            # while opening this read-only view; never retry a submit blindly.
            for attempt in range(3):
                await tab.click()
                try:
                    await trigger.wait_for(state="visible", timeout=3000)
                    break
                except PlaywrightError:
                    if attempt == 2:
                        raise
            await trigger.click()
            dialog = self.page.get_by_role("dialog").filter(
                has=self.page.get_by_role("heading", name="新建收藏夹", exact=True)
            )
            await dialog.get_by_role("textbox").fill(name)
            switch = dialog.get_by_role("switch")
            await switch.uncheck()
            if await switch.is_checked():
                raise DouyinError(
                    "privacy_unverified", "无法确认新建收藏夹的公开开关已关闭，未提交。"
                )
            await self.assert_account(expected_account_id)
            async with self.page.expect_response(
                lambda r: (
                    urlsplit(r.url).netloc == "www.douyin.com"
                    and urlsplit(r.url).path == MAINTAIN_FOLDER
                    and r.request.method == "POST"
                ),
                timeout=self.settings.timeout_ms,
            ) as pending:
                await dialog.get_by_role("button", name="确认", exact=True).click()
            response = await pending.value
            check_response(await response.json())
        except PlaywrightError:
            raise DouyinError(
                "write_uncertain", "收藏夹表单操作未完成确认。请回读检查，避免重复提交。"
            ) from None

    async def ensure_folder(self, name, *, expected_account_id):
        name = name.strip()
        if not name or len(name.encode("utf-16-le")) // 2 > 15 or any(ord(c) < 32 for c in name):
            raise ValueError("收藏夹名称须为1–15个字符（表情按UTF-16长度计算）且不含控制字符。")
        matches = [f for f in await self.all_folders() if f["name"] == name]
        if len(matches) > 1:
            raise DouyinError("ambiguous_folder", "存在多个同名收藏夹，请在抖音中区分名称后重试。")
        if matches:
            if matches[0].get("visibility") == "unknown":
                raise DouyinError(
                    "folder_visibility_unknown", "同名收藏夹的可见范围未确认，暂不复用。"
                )
            await self.assert_account(expected_account_id)
            return {**matches[0], "created": False, "verified": True}
        # Verified against Douyin's current create-folder UI module: public ? 0 : 1.
        await self._create_folder_ui(name, expected_account_id)
        for _ in range(3):
            matches = [f for f in await self.all_folders() if f["name"] == name]
            if len(matches) == 1:
                if matches[0].get("visibility") != "private":
                    raise DouyinError("write_unverified", "收藏夹已创建，但未能确认仅自己可见。")
                await self.assert_account(expected_account_id)
                return {**matches[0], "created": True, "verified": True}
            if len(matches) > 1:
                break
            await asyncio.sleep(1)
        raise DouyinError("write_unverified", "已发送创建请求，但未能唯一回读确认收藏夹。")

    async def add_to_named_folder(
        self, video_id, folder_name, *, expected_account_id, allow_favorite=False
    ):
        video_id = numeric_id(video_id)
        detail = await self._request(DETAIL, params={"aweme_id": video_id})
        item = detail.get("aweme_detail") or {}
        if str(item.get("aweme_id")) != video_id:
            raise DouyinError("video_unavailable", "无法确认该视频仍可访问。")
        collected = item.get("collect_stat")
        if collected not in (0, 1):
            raise DouyinError("unexpected_response", "无法确认视频收藏状态，未继续操作。")
        if collected == 0:
            if not allow_favorite:
                raise DouyinError("favorite_consent_required", "视频当前未收藏，方案未授权先收藏。")
            aweme_type = item.get("aweme_type")
            if type(aweme_type) is not int:
                raise DouyinError("unexpected_response", "视频类型缺失，未发送收藏请求。")
            await self._write(
                COLLECT,
                {},
                expected_account_id,
                body={
                    "aweme_id": video_id,
                    "action": "1",
                    "aweme_type": str(aweme_type),
                },
            )
            after = (await self._request(DETAIL, params={"aweme_id": video_id})).get(
                "aweme_detail"
            ) or {}
            if after.get("collect_stat") != 1:
                raise DouyinError("write_unverified", "已发送收藏请求，但未能回读确认。")
        folder = await self.ensure_folder(folder_name, expected_account_id=expected_account_id)
        if await self.folder_contains(folder["id"], video_id):
            await self.assert_account(expected_account_id)
            return {"verified": True, "already_present": True, "folder": folder}
        await self._write(
            MOVE_VIDEO,
            {
                "item_ids": [video_id],
                "item_type": "2",
                "to_collects_id": folder["id"],
                "update_collects_sort": "true",
            },
            expected_account_id,
        )
        for _ in range(3):
            if await self.folder_contains(folder["id"], video_id):
                await self.assert_account(expected_account_id)
                return {"verified": True, "already_present": False, "folder": folder}
            await asyncio.sleep(1)
        raise DouyinError("write_unverified", "已发送入夹请求，但尚未回读到目标视频。")

    def diagnostics(self):
        return {
            "browser_open": self.context is not None,
            "observed_paths": list(self._events),
            "observed_read_templates": sorted(self._templates),
        }
