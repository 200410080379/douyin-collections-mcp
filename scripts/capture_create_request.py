"""Inspect the site's request format. Abort the create request before transmission."""

import asyncio
import json
from urllib.parse import parse_qsl, urlsplit

from douyin_collections_mcp.browser import ORIGIN, BrowserClient
from douyin_collections_mcp.config import Settings


async def main():
    b = BrowserClient(Settings.from_env())
    captured = asyncio.Event()

    async def intercept(route):
        r = route.request
        params = dict(parse_qsl(urlsplit(r.url).query))
        allowed = {
            "device_platform",
            "aid",
            "channel",
            "version_code",
            "version_name",
            "collects_id",
            "action",
            "collects_name",
            "secret",
            "item_type",
            "pc_client_type",
            "app_id",
        }
        print(
            json.dumps(
                {
                    "method": r.method,
                    "query_keys": list(params),
                    "business_query": {k: v for k, v in params.items() if k in allowed},
                    "body": r.post_data,
                    "header_names": list(r.headers),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        await route.abort()
        captured.set()

    try:
        await b.start()
        await b.context.route("**/aweme/v1/web/collects/maintain/**", intercept)
        await b.page.goto(
            f"{ORIGIN}/user/self?showTab=favorite_collection", wait_until="domcontentloaded"
        )
        await b.page.wait_for_timeout(4000)
        await b.page.get_by_role("tab", name="收藏夹", exact=True).click()
        await b.page.locator("#user_detail_element").get_by_text("新建收藏夹", exact=True).click()
        dialog = b.page.get_by_role("dialog")
        await dialog.get_by_role("textbox").fill("接口格式验证")
        await dialog.get_by_role("switch").uncheck()
        await dialog.get_by_role("button", name="确认", exact=True).click()
        await asyncio.wait_for(captured.wait(), timeout=15)
    finally:
        await b.close()


asyncio.run(main())
