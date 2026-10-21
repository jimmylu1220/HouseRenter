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
# FACEBOOK_GROUPS：逗號分隔的社團 id 或 vanity slug；留空即停用 FB 爬蟲。
# FACEBOOK_STATE_PATH：已登入的 Playwright storage_state JSON（用 scripts/fb_login.py 產生）。
# FACEBOOK_INTERVAL_MINUTES：FB 獨立排程的間隔，刻意比 591 慢以降低封號風險。
# FACEBOOK_ADMIN_CHAT_ID：登入失效時通知的 Telegram chat id（選填）。
FACEBOOK_GROUPS: list[str] = [
    g.strip() for g in os.environ.get("FACEBOOK_GROUPS", "").split(",") if g.strip()
]
FACEBOOK_STATE_PATH: str = os.environ.get("FACEBOOK_STATE_PATH", "secrets/fb_state.json")
FACEBOOK_MAX_POSTS_PER_GROUP: int = int(os.environ.get("FACEBOOK_MAX_POSTS_PER_GROUP", "30"))
FACEBOOK_INTERVAL_MINUTES: int = int(os.environ.get("FACEBOOK_INTERVAL_MINUTES", "15"))
FACEBOOK_ADMIN_CHAT_ID: int | None = (
    int(os.environ["FACEBOOK_ADMIN_CHAT_ID"]) if os.environ.get("FACEBOOK_ADMIN_CHAT_ID") else None
)
FACEBOOK_ENABLED: bool = bool(FACEBOOK_GROUPS)

# ── Discord webhook（選填）─────────────────────────────────────────────────────
# DISCORD_WEBHOOK_URL：頻道 webhook 網址；留空即停用。
# DISCORD_MIRROR_CHAT_ID：Discord 頻道同步這位 Telegram 使用者收到的新房源
#   （套用他的篩選條件與黑名單，且他需在監控中）。
DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_MIRROR_CHAT_ID: int | None = (
    int(os.environ["DISCORD_MIRROR_CHAT_ID"]) if os.environ.get("DISCORD_MIRROR_CHAT_ID") else None
)
