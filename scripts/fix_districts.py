"""
One-time fix: update district from numeric sectionid to Chinese name
for listings that were scraped before the _SECTION_MAP was added.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.database import init_db, close_db, get_pool

# Complete 591 sectionid → district name mapping for Taipei + New Taipei
SECTION_MAP = {
    # Taipei (regionid=1)
    1: "中正區", 2: "大同區", 3: "中山區", 4: "松山區",
    5: "大安區", 6: "萬華區", 7: "信義區", 8: "士林區",
    9: "北投區", 10: "內湖區", 11: "南港區", 12: "文山區",
    # New Taipei (regionid=3)
    20: "瑞芳區", 26: "平溪區", 27: "雙溪區", 28: "板橋區",
    29: "新莊區", 30: "貢寮區", 31: "三重區", 32: "中和區",
    33: "永和區", 34: "土城區", 35: "新店區", 36: "樹林區",
    37: "汐止區", 38: "鶯歌區", 39: "三峽區", 40: "淡水區",
    41: "蘆洲區", 42: "烏來區", 43: "五股區", 44: "泰山區",
    45: "林口區", 46: "石門區", 47: "深坑區", 48: "石碇區",
    49: "坪林區", 50: "萬里區", 51: "金山區",
}


async def main():
    await init_db()
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Show unmapped numeric districts still in DB
        rows = await conn.fetch(
            "SELECT DISTINCT district FROM listings WHERE district ~ '^[0-9]+$' ORDER BY district"
        )
        if rows:
            unknown = [r["district"] for r in rows if int(r["district"]) not in SECTION_MAP]
            if unknown:
                print(f"警告：以下 sectionid 不在對應表中，將保留原值：{unknown}")

        total_updated = 0
        for section_id, name in SECTION_MAP.items():
            result = await conn.execute(
                "UPDATE listings SET district = $1 WHERE district = $2",
                name, str(section_id),
            )
            n = int(result.split()[-1])
            if n:
                print(f"  {section_id} → {name}：{n} 筆")
                total_updated += n

        if total_updated == 0:
            print("沒有需要修復的資料（可能已是最新格式）。")
        else:
            print(f"\n共更新 {total_updated} 筆。")

    await close_db()


asyncio.run(main())
