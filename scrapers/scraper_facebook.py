"""Facebook rental group scraper.

Strategy
========
Facebook rental groups are members-only and JS-heavy. We use Playwright with
m.facebook.com (mobile site = lighter DOM) and a pre-exported logged-in
storage_state (FACEBOOK_COOKIES_PATH). For each configured group we scroll the
group feed, find post articles, extract:

  * post id          → external_id (group_postid)
  * full body text   → description (and we lift the first line as `title`)
  * scontent images  → images JSONB (delivered to Telegram by bot/sender.py
                       which downloads + uploads + caches file_ids)
  * regex extraction → rent / district / size_ping / layout / floor
                       (best-effort; many will be NULL)

Stale-detection is disabled for this source (STALE_CHECK_ENABLED=False)
because we cannot enumerate every live post in a group, only the most recent.
"""
import asyncio
import logging
import re
from typing import AsyncGenerator, Optional

from config import (
    ALL_DISTRICTS,
    FACEBOOK_COOKIES_PATH,
    FACEBOOK_GROUPS,
    FACEBOOK_MAX_POSTS_PER_GROUP,
)
from db.models import Listing
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


# ── Field extraction helpers ──────────────────────────────────────────────────

# Rent: 5–6 digits with optional comma, often preceded by 月租/租金 or followed by 元/月
_RENT_RE = re.compile(
    r"(?:租金|月租|每月|租)?\s*[NTnt$＄]*\s*([1-9]\d{0,2}[,，]?\d{3})\s*(?:元|塊|NTD?)?\s*(?:[/／]|每)?\s*月?",
)
_PING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*坪")
_LAYOUT_ROOM_RE = re.compile(r"(\d+)\s*房")
_LAYOUT_KIND_RE = re.compile(r"(獨立套房|套房|雅房|分租|整層|頂加|公寓)")
_FLOOR_RE = re.compile(r"(\d+)\s*[樓F]")
_POST_ID_RE = re.compile(r"/(?:posts|permalink)/(\d+)")


def _extract_rent(text: str) -> int:
    """Return rent in NTD, or 0 when nothing plausible was found."""
    for m in _RENT_RE.finditer(text):
        raw = m.group(1).replace(",", "").replace("，", "")
        if not raw.isdigit():
            continue
        n = int(raw)
        if 2_000 <= n <= 200_000:
            return n
    return 0


def _extract_district(text: str) -> Optional[str]:
    for d in ALL_DISTRICTS:
        if d in text:
            return d
    return None


def _extract_size_ping(text: str) -> Optional[float]:
    m = _PING_RE.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1))
        if 1 <= v <= 200:
            return v
    except ValueError:
        pass
    return None


def _extract_layout(text: str) -> Optional[str]:
    m = _LAYOUT_KIND_RE.search(text)
    if m:
        return m.group(1)
    m = _LAYOUT_ROOM_RE.search(text)
    if m:
        n = int(m.group(1))
        return "3房+" if n >= 3 else f"{n}房"
    return None


def _extract_floor(text: str) -> Optional[str]:
    m = _FLOOR_RE.search(text)
    return m.group(1) if m else None


# ── Scraper ──────────────────────────────────────────────────────────────────


class ScraperFacebook(BaseScraper):
    SOURCE_NAME = "facebook"
    # Disable scheduler stale-detection for this source — see job_manager.
    STALE_CHECK_ENABLED = False

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None

    # ── Playwright lifecycle ──────────────────────────────────────────────────

    async def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Linux; Android 11; SM-G991B) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Mobile Safari/537.36"
            ),
            "viewport": {"width": 412, "height": 869},
            "locale": "zh-TW",
        }
        if FACEBOOK_COOKIES_PATH:
            ctx_kwargs["storage_state"] = FACEBOOK_COOKIES_PATH
        self._context = await self._browser.new_context(**ctx_kwargs)
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    async def close(self) -> None:
        for closer, attr in (
            (lambda: self._context.close() if self._context else None, "_context"),
            (lambda: self._browser.close() if self._browser else None, "_browser"),
            (lambda: self._playwright.stop() if self._playwright else None, "_playwright"),
        ):
            try:
                coro = closer()
                if coro is not None:
                    await coro
            except Exception:
                pass
            setattr(self, attr, None)

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_listings(self, region_ids: list[int]) -> AsyncGenerator[Listing, None]:
        # region_ids is unused — FB groups are not region-scoped. We iterate
        # configured groups instead.
        if not FACEBOOK_GROUPS:
            logger.info("FB scraper: FACEBOOK_GROUPS empty, skipping.")
            return

        await self._ensure_browser()

        for group in FACEBOOK_GROUPS:
            try:
                async for listing in self._fetch_group(group):
                    yield listing
            except Exception as exc:
                logger.warning("FB group %s scrape failed: %s", group, exc, exc_info=True)
                continue

    async def check_still_available(self, listing: Listing) -> bool:
        # FB posts rarely 404. Cheap default: assume still live. Combined with
        # STALE_CHECK_ENABLED=False, this means FB listings persist until
        # manually blacklisted or removed by weekly cleanup.
        return True

    # ── Per-group scrape ──────────────────────────────────────────────────────

    async def _fetch_group(self, group: str) -> AsyncGenerator[Listing, None]:
        url = f"https://m.facebook.com/groups/{group}/"
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(2)

            if "login" in page.url or "/checkpoint" in page.url:
                logger.warning(
                    "FB group %s: hit login wall — set FACEBOOK_COOKIES_PATH to a logged-in storage_state JSON",
                    group,
                )
                return

            collected = 0
            seen: set[str] = set()
            scroll = 0
            max_scroll = 10

            while collected < FACEBOOK_MAX_POSTS_PER_GROUP and scroll < max_scroll:
                articles = await page.query_selector_all("article, div[role='article']")
                for article in articles:
                    listing = await self._parse_article(article, group)
                    if listing is None:
                        continue
                    if listing.external_id in seen:
                        continue
                    seen.add(listing.external_id)
                    collected += 1
                    yield listing
                    if collected >= FACEBOOK_MAX_POSTS_PER_GROUP:
                        break

                if collected >= FACEBOOK_MAX_POSTS_PER_GROUP:
                    break
                await page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                await asyncio.sleep(2)
                scroll += 1

            logger.info("FB group %s: yielded %d posts", group, collected)
        finally:
            await page.close()

    async def _parse_article(self, article, group: str) -> Optional[Listing]:
        try:
            # ── post id + canonical URL ──────────────────────────────────
            post_id: Optional[str] = None
            post_url: Optional[str] = None
            for h in await article.query_selector_all("a[href*='/posts/'], a[href*='/permalink/']"):
                href = await h.get_attribute("href")
                if not href:
                    continue
                m = _POST_ID_RE.search(href)
                if m:
                    post_id = m.group(1)
                    clean = href.split("?")[0]
                    post_url = clean if clean.startswith("http") else f"https://m.facebook.com{clean}"
                    break
            if not post_id:
                return None

            # ── body text ────────────────────────────────────────────────
            body = (await article.inner_text() or "").strip()
            if len(body) < 20:
                return None

            # ── images ───────────────────────────────────────────────────
            images: list[str] = []
            for ih in await article.query_selector_all("img"):
                src = await ih.get_attribute("src") or await ih.get_attribute("data-src")
                if not src:
                    continue
                # FB image CDN; skip avatars / emojis
                if "scontent" not in src:
                    continue
                if src in images:
                    continue
                images.append(src)
                if len(images) >= 10:
                    break

            # ── structured field extraction ──────────────────────────────
            rent = _extract_rent(body)
            district = _extract_district(body) or ""
            size_ping = _extract_size_ping(body)
            layout = _extract_layout(body)
            floor = _extract_floor(body)

            first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            title = first_line[:60] or "(FB貼文)"

            return Listing(
                source=self.SOURCE_NAME,
                external_id=f"{group}_{post_id}",
                url=post_url or f"https://m.facebook.com/groups/{group}/posts/{post_id}",
                title=title,
                area="",
                district=district,
                rent=rent,
                layout=layout,
                size_ping=size_ping,
                floor=floor,
                description=body[:5000],   # cap to keep DB rows reasonable
                images=images,
                thumbnail_url=images[0] if images else None,
                extra_info={"fb_group": group, "fb_post_id": post_id},
            )
        except Exception as exc:
            logger.debug("FB article parse failed: %s", exc)
            return None
