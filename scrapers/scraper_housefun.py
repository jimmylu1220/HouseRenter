import asyncio
import logging
import random
from typing import AsyncGenerator, Optional

import aiohttp
from bs4 import BeautifulSoup

from db.models import Listing
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://rent.housefun.com.tw"
REGION_URLS = {
    1: f"{BASE_URL}/region/台北市/",
    3: f"{BASE_URL}/region/新北市/",
}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


class ScraperHouseFun(BaseScraper):
    SOURCE_NAME = "housefun"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "User-Agent": random.choice(_USER_AGENTS),
                "Accept-Language": "zh-TW,zh;q=0.9",
            })

    async def _get_html(self, url: str) -> Optional[str]:
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
        except Exception as exc:
            logger.warning("HouseFun fetch error %s: %s", url, exc)
            return None

    async def fetch_listings(self, region_ids: list[int]) -> AsyncGenerator[Listing, None]:
        await self._ensure_session()

        for region_id in region_ids:
            base = REGION_URLS.get(region_id)
            if not base:
                continue

            page = 1
            while True:
                url = f"{base}?page={page}" if page > 1 else base
                html = await self._get_html(url)
                if not html:
                    break

                soup = BeautifulSoup(html, "lxml")
                cards = soup.select(".house-list-item, .list-item, article.item")
                if not cards:
                    # Try alternate selectors
                    cards = soup.select("[class*='house-item'], [class*='rent-item']")
                if not cards:
                    logger.debug("HouseFun: no cards found on page %s of %s", page, base)
                    break

                for card in cards:
                    listing = self._parse_card(card, region_id)
                    if listing:
                        yield listing

                # Check if there's a next page
                next_btn = soup.select_one("a[rel='next'], .pagination .next:not(.disabled)")
                if not next_btn:
                    break

                page += 1
                await asyncio.sleep(random.uniform(1.5, 3.0))

    def _parse_card(self, card, region_id: int) -> Optional[Listing]:
        try:
            # Link and ID
            link_tag = card.select_one("a[href*='/home/'], a[href*='/rent/']")
            if not link_tag:
                link_tag = card.find("a", href=True)
            if not link_tag:
                return None

            href = link_tag.get("href", "")
            if not href.startswith("http"):
                href = BASE_URL + href

            # Extract external ID from URL
            parts = [p for p in href.split("/") if p.isdigit()]
            ext_id = parts[-1] if parts else href.split("?")[0].rstrip("/").split("/")[-1]
            if not ext_id:
                return None

            title_tag = card.select_one(".title, .house-title, h3, h2")
            title = title_tag.get_text(strip=True) if title_tag else ""

            price_tag = card.select_one(".price, .rent-price, [class*='price']")
            rent = 0
            if price_tag:
                price_text = price_tag.get_text(strip=True)
                digits = "".join(c for c in price_text if c.isdigit())
                rent = int(digits) if digits else 0

            area_name = "台北市" if region_id == 1 else "新北市"
            district_tag = card.select_one(".district, .area, [class*='location']")
            district = district_tag.get_text(strip=True) if district_tag else ""

            size_tag = card.select_one("[class*='size'], [class*='area'], [class*='ping']")
            size_ping = None
            if size_tag:
                txt = size_tag.get_text(strip=True)
                digits = "".join(c for c in txt if c.isdigit() or c == ".")
                try:
                    size_ping = float(digits)
                except ValueError:
                    pass

            layout_tag = card.select_one("[class*='layout'], [class*='room']")
            layout = layout_tag.get_text(strip=True) if layout_tag else None

            img_tag = card.select_one("img")
            thumbnail = img_tag.get("src") or img_tag.get("data-src") if img_tag else None

            return Listing(
                source=self.SOURCE_NAME,
                external_id=ext_id,
                url=href,
                title=title,
                area=area_name,
                district=district,
                rent=rent,
                layout=layout,
                size_ping=size_ping,
                thumbnail_url=thumbnail,
            )
        except Exception as exc:
            logger.warning("HouseFun parse error: %s", exc)
            return None

    async def check_still_available(self, listing: Listing) -> bool:
        await self._ensure_session()
        html = await self._get_html(listing.url)
        if html is None:
            return False
        if "已出租" in html or "已下架" in html or "rent-out" in html:
            return False
        return True

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
