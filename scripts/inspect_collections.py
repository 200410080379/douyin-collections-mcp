"""Development-only, read-only inspection of the dedicated Douyin profile."""

import asyncio
import json
from pathlib import Path

from douyin_collections_mcp.browser import ORIGIN, BrowserClient
from douyin_collections_mcp.config import Settings


async def main():
    browser = BrowserClient(Settings.from_env())
    out = Path("output/playwright")
    out.mkdir(parents=True, exist_ok=True)
    try:
        await browser.start()
        await browser.page.goto(
            f"{ORIGIN}/user/self?showTab=favorite_collection", wait_until="domcontentloaded"
        )
        await browser.page.wait_for_timeout(4000)
        await browser.page.get_by_role("tab", name="收藏夹", exact=True).click()
        await browser.page.wait_for_timeout(2000)
        scripts = await browser.page.locator("script[src]").evaluate_all(
            "els => els.map(e => e.src)"
        )
        # Script URLs are public static assets; no cookies or request headers saved.
        Path("/tmp/douyin-script-urls.json").write_text(json.dumps(scripts, indent=2))
        resources = await browser.page.evaluate(
            "performance.getEntriesByType('resource').map(e => e.name).filter(u => /\\.js(?:\\?|$)/.test(u))"
        )
        Path("/tmp/douyin-resource-urls.json").write_text(json.dumps(resources, indent=2))
        await (
            browser.page.locator("#user_detail_element")
            .get_by_text("新建收藏夹", exact=True)
            .click()
        )
        await browser.page.wait_for_timeout(500)
        (out / "create-folder-aria.txt").write_text(
            await browser.page.locator("body").aria_snapshot()
        )
        await browser.page.screenshot(path=str(out / "create-folder.png"))
        print(json.dumps({"scripts": len(scripts), "artifacts": str(out.resolve())}))
    finally:
        await browser.close()


asyncio.run(main())
