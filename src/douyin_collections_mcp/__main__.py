"""stdio server plus local setup/diagnostics commands."""

import argparse
import asyncio
import json
import sys
import time

from .browser import BrowserClient
from .config import Settings
from .errors import DouyinError


async def login(timeout):
    browser = BrowserClient(Settings.from_env())
    try:
        print(json.dumps(await browser.login_start(), ensure_ascii=False), flush=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await browser.session_status()
            if status.get("logged_in"):
                print(json.dumps(status, ensure_ascii=False), flush=True)
                return 0
            await asyncio.sleep(3)
        print(
            json.dumps(
                {
                    "logged_in": False,
                    "message": "登录等待结束。可以再次运行 login，或通过 MCP 登录。",
                },
                ensure_ascii=False,
            )
        )
        return 2
    finally:
        await browser.close()


def main():
    parser = argparse.ArgumentParser(description="抖音收藏整理 MCP（默认通过 stdio 运行）")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="运行 stdio MCP 服务")
    p = sub.add_parser("login", help="打开独立浏览器供你登录，并保存本地会话")
    p.add_argument("--timeout", type=int, default=300)
    sub.add_parser("doctor", help="检查本地配置，不打开浏览器")
    sub.add_parser("config", help="输出通用 MCP 客户端配置")
    args = parser.parse_args()
    try:
        if args.command == "login":
            if args.timeout < 1 or args.timeout > 1800:
                parser.error("timeout must be 1–1800 seconds")
            raise SystemExit(asyncio.run(login(args.timeout)))
        if args.command == "doctor":
            settings = Settings.from_env()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "browser": settings.channel,
                        "data_dir": str(settings.data_dir),
                        "profile_exists": settings.profile_dir.exists(),
                        "live_account_verified": False,
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "config":
            print(
                json.dumps(
                    {
                        "mcpServers": {
                            "douyin-collections": {
                                "command": sys.executable,
                                "args": ["-m", "douyin_collections_mcp", "serve"],
                            }
                        }
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            from .server import mcp

            mcp.run(transport="stdio")
    except DouyinError as exc:
        print(
            json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
