"""Long-lived, logged-in Chromium context for Facebook.

The browser is reused across scrape cycles (launching Chromium every
15 minutes is slow and looks more bot-like). It is re-created transparently if
it crashes or disconnects.
"""
import logging
import os

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_LOGIN_URL_MARKERS = ("/login", "/checkpoint", "login.php", "/recover")


class FacebookAuthError(RuntimeError):
    """The stored login state is missing, expired, or hit a checkpoint."""


class FacebookSession:
    def __init__(self, state_path: str, headless: bool = True):
        self._state_path = state_path
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None

    async def context(self):
        if self._context is not None and self._browser is not None and self._browser.is_connected():
            return self._context
        await self.close()

        if not os.path.exists(self._state_path):
            raise FacebookAuthError(
                f"找不到 FB 登入狀態檔 {self._state_path}，請先執行 python scripts/fb_login.py"
            )

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            storage_state=self._state_path,
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
            timezone_id="Asia/Taipei",
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        # Skip heavy media we never look at; images are taken from JSON URIs.
        await self._context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("media", "font")
            else route.continue_(),
        )
        logger.info("FB session: browser started")
        return self._context

    @staticmethod
    async def assert_logged_in(page) -> None:
        url = page.url
        if any(m in url for m in _LOGIN_URL_MARKERS):
            raise FacebookAuthError(f"FB 登入已失效（被導向 {url}）")
        if await page.query_selector("form#login_form, input[name='email'][type='text'], input[name='pass']"):
            raise FacebookAuthError("FB 登入已失效（頁面出現登入表單）")

    async def persist_state(self) -> None:
        """Write refreshed cookies back so the session lives longer."""
        if self._context is None:
            return
        try:
            await self._context.storage_state(path=self._state_path)
        except Exception as exc:
            logger.debug("FB session: could not persist storage_state: %s", exc)

    async def close(self) -> None:
        for attr in ("_context", "_browser"):
            obj = getattr(self, attr)
            if obj is not None:
                try:
                    await obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

