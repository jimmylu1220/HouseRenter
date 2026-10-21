"""Log in to Facebook once by hand and save the session for the scraper.

Usage (on a machine with a display, e.g. your laptop):
    python scripts/fb_login.py [output_path]

A browser window opens. Log in (including 2FA), then come back to the terminal
and press Enter. The storage_state is written to output_path (default:
FACEBOOK_STATE_PATH or secrets/fb_state.json). Copy that file to the server's
./secrets/ directory.

Use a dedicated FB account that has joined the rental groups — automated
browsing can get an account restricted.
"""
import asyncio
import os
import sys

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def main(out_path: str) -> None:
    from playwright.async_api import async_playwright

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(user_agent=_UA, locale="zh-TW")
        page = await context.new_page()
        await page.goto("https://www.facebook.com/login")
        await asyncio.to_thread(input, "登入完成並看到動態牆後，回到這裡按 Enter… ")
        await context.storage_state(path=out_path)
        await browser.close()
    print(f"已儲存登入狀態到 {out_path}")


if __name__ == "__main__":
    default = os.environ.get("FACEBOOK_STATE_PATH", "secrets/fb_state.json")
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else default))
