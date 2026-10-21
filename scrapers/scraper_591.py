import asyncio
import logging
import random
import re
import time
from typing import AsyncGenerator, Optional

import aiohttp
from yarl import URL

from db.models import Listing

# 591 sectionid → Chinese district name
# Taipei (regionid=1)
_TAIPEI_SECTION_MAP: dict[int, str] = {
    1: "中正區", 2: "大同區", 3: "中山區", 4: "松山區",
    5: "大安區", 6: "萬華區", 7: "信義區", 8: "士林區",
    9: "北投區", 10: "內湖區", 11: "南港區", 12: "文山區",
}
# New Taipei (regionid=3)
_NEW_TAIPEI_SECTION_MAP: dict[int, str] = {
    20: "瑞芳區", 26: "平溪區", 27: "雙溪區", 28: "板橋區",
    29: "新莊區", 30: "貢寮區", 31: "三重區", 32: "中和區",
    33: "永和區", 34: "土城區", 35: "新店區", 36: "樹林區",
    37: "汐止區", 38: "鶯歌區", 39: "三峽區", 40: "淡水區",
    41: "蘆洲區", 42: "烏來區", 43: "五股區", 44: "泰山區",
    45: "林口區", 46: "石門區", 47: "深坑區", 48: "石碇區",
    49: "坪林區", 50: "萬里區", 51: "金山區",
}
_SECTION_MAP: dict[int, str] = {**_TAIPEI_SECTION_MAP, **_NEW_TAIPEI_SECTION_MAP}


def _parse_layout(layout_str: str) -> str:
    """Normalize 591 layoutStr to one of: 套房, 1房, 2房, 3房+"""
    if not layout_str:
        return ""
    if "套" in layout_str:
        return "套房"
    m = re.match(r"(\d+)房", layout_str)
    if m:
        n = int(m.group(1))
        return "3房+" if n >= 3 else f"{n}房"
    return layout_str


def _parse_tags(record: dict):
    """Parse rentTagList (and similar fields) for pets, parking, elevator."""
    pets = None
    parking = None
    elevator = None

    # Current API: tags is a list of strings ("可養寵物", "有電梯", "有車位");
    # older versions used [{"name": ...}].
    tags = record.get("rentTagList") or record.get("tags") or record.get("tag") or []
    tag_names = {
        (t.get("name", "") if isinstance(t, dict) else str(t)).strip() for t in tags
    }

    if any("寵物" in name for name in tag_names):
        pets = "可"
    if any("停車" in name or "車位" in name for name in tag_names):
        parking = "有"
    if any("電梯" in name for name in tag_names):
        elevator = True

    # fallback to top-level fields some API versions expose
    if pets is None and record.get("pet") not in (None, 0, "0", "不可", ""):
        pets = str(record["pet"])
    if parking is None and record.get("parkingStr"):
        parking = str(record["parkingStr"])
    if parking is None and record.get("price_has_carport") in (1, "1", True):
        parking = "有"
    if elevator is None and record.get("elevator") in (1, "1", True):
        elevator = True

    return pets, parking, elevator
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

# --- 2026 新版 API 常數 ---
LIST_URL   = "https://rent.591.com.tw/list"
LIST_API   = "https://bff-house.591.com.tw/v3/web/rent/list"
PAGE_SIZE  = 30

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

class Scraper591(BaseScraper):
    SOURCE_NAME = "591"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._deviceid: Optional[str] = None

    async def _init_session(self) -> None:
        """
        利用 Playwright 無頭瀏覽器獲取 591 的 deviceid 與初始 Cookies。
        只需攔截任意帶有 deviceid 的請求，即可轉交給 aiohttp 進行高效併發。
        """
        from playwright.async_api import async_playwright

        captured = {"deviceid": None, "cookies": []}

        async def _on_request(req):
            if captured["deviceid"] is None and "591.com.tw" in req.url:
                deviceid = req.headers.get("deviceid")
                if deviceid:
                    captured["deviceid"] = deviceid
                    logger.debug(f"成功捕獲 DeviceID: {deviceid}")

        logger.info("591: 啟動 Playwright 獲取通行證...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(user_agent=_UA)
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            page = await context.new_page()
            page.on("request", _on_request)

            try:
                # 載入首頁即可，通常 JS 初始化時就會生成 deviceid 並寫入 Cookie/Header
                await page.goto("https://rent.591.com.tw/", wait_until="domcontentloaded", timeout=45_000)
                # 等待一下讓背景 JS 發送數據收集請求 (通常帶有 deviceid)
                await asyncio.sleep(3) 
            except Exception as e:
                logger.warning(f"Playwright 載入逾時或發生錯誤，仍嘗試提取 Cookie: {e}")

            captured["cookies"] = await context.cookies()
            await browser.close()

        # 兜底機制：如果沒攔截到 Header，嘗試從 Cookie 中尋找 T591_Token 或 deviceid 相關欄位
        if not captured["deviceid"]:
            for c in captured["cookies"]:
                if c["name"] in ("T591_Token", "deviceid"):
                    captured["deviceid"] = c["value"]
                    break

        if not captured["deviceid"]:
            # 如果還是沒有，生成一個隨機的 21 碼字串作為偽裝 (591 前端 nanoid 格式)
            import string
            chars = string.ascii_letters + string.digits + "_-"
            captured["deviceid"] = "".join(random.choices(chars, k=21))
            logger.warning("未捕獲到真實 DeviceID，啟用隨機偽裝 ID。")

        self._deviceid = captured["deviceid"]

        jar = aiohttp.CookieJar(unsafe=True)
        for c in captured["cookies"]:
            domain = c["domain"].lstrip(".")
            jar.update_cookies(
                {c["name"]: c["value"]},
                response_url=URL(f"https://{domain}/"),
            )

        self._session = aiohttp.ClientSession(
            headers={
                "User-Agent": _UA,
                "Accept": "application/json, text/plain, */*",
                "deviceid": self._deviceid,
                "device": "pc",
                "Referer": LIST_URL,
            },
            cookie_jar=jar,
            connector=aiohttp.TCPConnector(limit=10, force_close=False),
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
        )
        logger.info("591: Session 初始化完成。")

    async def _api_get(self, params: dict, retry_limit: int = 1) -> dict:
        for attempt in range(retry_limit + 1):
            if not self._session:
                await self._init_session()

            try:
                async with self._session.get(LIST_API, params=params) as resp:
                    if resp.status in (401, 403, 419) and attempt < retry_limit:
                        logger.warning(f"591 API 拒絕訪問 (Status: {resp.status})，重新初始化 Session...")
                        await self.close()
                        self._session = None
                        continue
                    
                    resp.raise_for_status()
                    return await resp.json()
            except Exception as exc:
                if attempt >= retry_limit:
                    logger.error(f"API 請求失敗: {exc}")
                    raise
                await asyncio.sleep(2)

    async def fetch_listings(self, region_ids: list[int]) -> AsyncGenerator[Listing, None]:
        if not self._session:
            await self._init_session()

        for region_id in region_ids:
            first_row = 0
            total_rows: Optional[int] = None

            while True:
                # 依照最新版 API 所需的 Query 參數構建
                params = {
                    "timestamp": str(int(time.time() * 1000)),
                    "regionid": region_id,
                    "firstRow": first_row,
                    "order": "posttime",
                    "orderType": "desc",
                }
                
                try:
                    data = await self._api_get(params)
                except Exception:
                    break

                # 依據新版 JSON 結構提取資料
                records = data.get("data", {}).get("items", [])
                if not records:
                    logger.info(f"地區 {region_id} 已無更多資料或抓取完畢。")
                    break

                if total_rows is None:
                    total_rows = int(data.get("data", {}).get("total", 0))
                    logger.info(f"591 region={region_id} 總件數={total_rows}")

                for record in records:
                    listing = self._parse_record(record)
                    if listing:
                        yield listing

                first_row += PAGE_SIZE
                if total_rows and first_row >= total_rows:
                    break

                await asyncio.sleep(random.uniform(1.0, 2.5))

    def _parse_record(self, record: dict) -> Optional[Listing]:
        try:
            ext_id = str(record.get("id", ""))
            if not ext_id:
                return None

            # 處理價格字串 (例如 "23,000" -> 23000)
            rent_raw = str(record.get("price", "0")).replace(",", "").strip()
            rent = int(rent_raw) if rent_raw.isdigit() else 0

            # 提取坪數
            size_ping = float(record.get("area", 0)) if record.get("area") else None

            # sectionid → 中文行政區名
            section_id = record.get("sectionid")
            district = _SECTION_MAP.get(int(section_id), str(section_id)) if section_id else ""

            # 格局正規化
            layout = _parse_layout(record.get("layoutStr", ""))

            # 寵物 / 停車 / 電梯
            pets, parking, elevator = _parse_tags(record)

            return Listing(
                source=self.SOURCE_NAME,
                external_id=ext_id,
                url=record.get("url", f"https://rent.591.com.tw/{ext_id}"),
                title=record.get("title", ""),
                area=str(record.get("regionid", "")),
                district=district,
                address=record.get("address", ""),
                rent=rent,
                layout=layout,
                size_ping=size_ping,
                floor=record.get("floor_name", ""),
                floor_total=None,
                pets=pets,
                parking=parking,
                elevator=elevator,
                thumbnail_url=record.get("cover", ""),
                extra_info=record,
            )
        except Exception as exc:
            logger.warning(f"591 解析錯誤: {exc} | id={record.get('id')}")
            return None

    async def check_still_available(self, listing: Listing) -> bool:
        """
        利用 HTTP HEAD 請求快速檢查物件是否仍有效，避免下載龐大 HTML 觸發反爬機制。
        """
        if not self._session:
            return True
        try:
            async with self._session.head(listing.url, allow_redirects=False) as resp:
                # 591 下架物件通常會回傳 404，或者 302 重定向回首頁/列表頁
                if resp.status in (404, 302, 301):
                    return False
            return True
        except Exception:
            return True

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None