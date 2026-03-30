"""
DB 全表清空腳本（部署用）

用途：重新部署時清空所有資料，讓 bot 以全新狀態啟動。
警告：此操作不可逆，會刪除所有房源、願望清單、黑名單、偏好設定等資料。

使用方式：
    python scripts/reset_db.py
    python scripts/reset_db.py --yes   # 跳過確認提示
"""

import asyncio
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_URL
import asyncpg


async def reset_all_tables() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            TRUNCATE TABLE
                wishlist,
                blacklist,
                pagination_sessions,
                user_preferences,
                active_monitors,
                listings
            RESTART IDENTITY CASCADE;
        """)
        print("✅ 所有資料表已清空。")
    finally:
        await conn.close()


def main() -> None:
    skip_confirm = "--yes" in sys.argv or "-y" in sys.argv

    if not skip_confirm:
        print("⚠️  警告：此操作將永久清空以下資料表：")
        print("   listings / wishlist / blacklist / pagination_sessions")
        print("   user_preferences / active_monitors")
        print()
        answer = input("確定要繼續嗎？輸入 'yes' 確認：").strip().lower()
        if answer != "yes":
            print("已取消。")
            return

    asyncio.run(reset_all_tables())


if __name__ == "__main__":
    main()
