import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL: str = os.environ["DATABASE_URL"]

SCRAPE_INTERVAL_MINUTES: int = 1
MAX_LISTINGS_PER_BATCH: int = 20       # cap for recurring runs
MAX_FIRST_RUN_BATCH: int = 50          # cap for the initial full listing

REGION_IDS: dict[str, int] = {
    "taipei": 1,
    "new_taipei": 3,
}

TAIPEI_DISTRICTS = [
    "中正區", "大同區", "中山區", "松山區", "大安區",
    "萬華區", "信義區", "士林區", "北投區", "內湖區",
    "南港區", "文山區",
]

NEW_TAIPEI_DISTRICTS = [
    "板橋區", "三重區", "中和區", "永和區", "新莊區",
    "新店區", "樹林區", "鶯歌區", "三峽區", "淡水區",
    "汐止區", "瑞芳區", "土城區", "蘆洲區", "五股區",
    "泰山區", "林口區", "深坑區", "石碇區", "坪林區",
]

ALL_DISTRICTS = TAIPEI_DISTRICTS + NEW_TAIPEI_DISTRICTS

# ── Facebook scraper ──────────────────────────────────────────────────────────
# Facebook rental groups to scrape. Provide as a comma-separated list of group
# IDs (numeric) or vanity slugs in the FACEBOOK_GROUPS env var, e.g.:
#     FACEBOOK_GROUPS=123456789012345,taipei.rent.house
#
# FACEBOOK_COOKIES_PATH points to a JSON file exported from a logged-in browser
# session (Playwright storage_state format). Without it, FB rental groups
# return mostly empty content because most are members-only.
#
# FACEBOOK_MAX_POSTS_PER_GROUP caps how many posts we pull per group per cycle
# to keep cycles bounded.
FACEBOOK_GROUPS: list[str] = [
    g.strip() for g in os.environ.get("FACEBOOK_GROUPS", "").split(",") if g.strip()
]
FACEBOOK_COOKIES_PATH: str | None = os.environ.get("FACEBOOK_COOKIES_PATH") or None
FACEBOOK_MAX_POSTS_PER_GROUP: int = int(os.environ.get("FACEBOOK_MAX_POSTS_PER_GROUP", "30"))
FACEBOOK_ENABLED: bool = bool(FACEBOOK_GROUPS)
