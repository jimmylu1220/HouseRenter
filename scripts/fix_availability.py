"""
One-time fix: reset all listings to is_available=TRUE so the scraper
can re-evaluate correctly on the next run.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.database import init_db, close_db, get_pool


async def main():
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM listings WHERE is_available = FALSE")
        print(f"目前標記為下架的房源：{count} 筆")

        if count == 0:
            print("沒有需要修復的資料。")
            await close_db()
            return

        if "--yes" not in sys.argv:
            ans = input(f"將全部 {count} 筆重設為 is_available=TRUE？(y/N) ")
            if ans.strip().lower() != "y":
                print("已取消。")
                await close_db()
                return

        result = await conn.execute("UPDATE listings SET is_available = TRUE")
        updated = int(result.split()[-1])
        print(f"已重設 {updated} 筆。下次爬蟲執行後會自動重新判斷真正下架的房源。")

    await close_db()


asyncio.run(main())
