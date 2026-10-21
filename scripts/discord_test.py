"""Send a sample listing to DISCORD_WEBHOOK_URL to check the webhook works.

Usage:
    python scripts/discord_test.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unused")
os.environ.setdefault("DATABASE_URL", "unused")

from db.models import Listing  # noqa: E402
from notifiers.discord_webhook import get_discord_notifier  # noqa: E402


async def main() -> None:
    notifier = get_discord_notifier()
    if notifier is None:
        sys.exit("DISCORD_WEBHOOK_URL 未設定（請寫在 .env）")
    await notifier.send_listing(Listing(
        source="591",
        external_id="test",
        url="https://rent.591.com.tw/",
        title="【測試】大安區 近捷運 溫馨2房",
        area="",
        district="大安區",
        rent=28000,
        layout="2房",
        size_ping=22,
        floor="5",
        floor_total=12,
    ))
    print("已送出測試訊息，請到 Discord 頻道確認。")


if __name__ == "__main__":
    asyncio.run(main())
