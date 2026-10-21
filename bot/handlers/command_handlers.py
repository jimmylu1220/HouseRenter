import asyncio
import logging

import aiohttp
from telegram import Update
from telegram.ext import ContextTypes

import db.queries as queries
from bot.formatter import format_listing, format_wishlist_empty
from bot.keyboards import wishlist_item_keyboard, wishlist_header_keyboard, reset_confirm_keyboard
from bot.sender import send_listing_message, send_page

logger = logging.getLogger(__name__)


async def handle_start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if await queries.is_monitor_active(chat_id):
        await update.message.reply_text(
            "監控已在執行中 🔄\n\n"
            "輸入「停止」可暫停，或 /preferences 調整篩選條件。"
        )
        return

    await queries.set_monitor_active(chat_id)

    from scheduler.job_manager import start_global_job
    start_global_job(context.application)

    from config import SCRAPE_INTERVAL_MINUTES
    await update.message.reply_text(
        "✅ 開始監控雙北租屋！\n\n"
        f"每 {SCRAPE_INTERVAL_MINUTES} 分鐘自動抓取 591 租屋網 最新資訊。\n"
        "只會推播符合你設定條件的物件。\n\n"
        "📝 輸入「停止」暫停監控\n"
        "⚙️ /preferences — 設定篩選條件\n"
        "❤️ /wishlist — 查看願望清單\n"
        "🚫 /blacklist — 黑名單管理\n"
        "📊 /status — 查看目前狀態"
    )

    # 展示資料庫中已有的符合條件房源，並在背景清除不符合的
    await _show_existing_and_cleanup(update, context, chat_id)


async def _show_existing_and_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """展示 DB 中符合目前偏好的房源（分頁），並在背景刪除不符合的。"""
    try:
        from scheduler.job_manager import _matches_preferences
        prefs = await queries.get_preference(chat_id)
        all_listings = await queries.get_recent_listings(limit=500)

        if not all_listings:
            return

        matched = [l for l in all_listings if _matches_preferences(l, prefs)]
        non_matched_ids = [l.id for l in all_listings if l not in matched]

        if matched:
            matched_ids = [l.id for l in matched]
            await queries.save_pagination_session(chat_id, matched_ids)
            await update.message.reply_text(
                f"📋 資料庫中現有 <b>{len(matched)}</b> 筆符合條件的房源：",
                parse_mode="HTML",
            )
            await send_page(context.bot, chat_id, matched_ids, offset=0, queries=queries)

        # 背景清除不符合條件且不在願望清單的物件
        if non_matched_ids:
            asyncio.create_task(_delete_non_matching(non_matched_ids))

    except Exception as exc:
        logger.warning("展示現有房源時發生錯誤: %s", exc)


async def _delete_non_matching(ids: list[int]) -> None:
    try:
        deleted = await queries.delete_listings_not_in_wishlist(ids)
        if deleted:
            logger.info("背景清除不符合條件的房源 %d 筆", deleted)
    except Exception as exc:
        logger.warning("背景清除失敗: %s", exc)


async def handle_stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not await queries.is_monitor_active(chat_id):
        await update.message.reply_text("監控目前沒有在執行。輸入「開始」啟動。")
        return

    await queries.set_monitor_inactive(chat_id)

    from scheduler.job_manager import stop_job_for_chat
    stop_job_for_chat(chat_id)

    await update.message.reply_text(
        "⏹ 已停止監控。\n\n你的願望清單與篩選條件已保留，輸入「開始」可重新啟動。"
    )


async def handle_wishlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    items = await queries.get_wishlist(chat_id)

    if not items:
        await update.message.reply_text(format_wishlist_empty())
        return

    unavailable_count = sum(1 for _, listing in items if not listing.is_available)
    header_kb = wishlist_header_keyboard(unavailable_count)

    header_text = f"❤️ 你的願望清單（共 {len(items)} 筆）："
    if unavailable_count > 0:
        header_text += f"\n⚠️ 其中 {unavailable_count} 筆已下架"

    await update.message.reply_text(header_text, reply_markup=header_kb)

    for _, listing in items:
        status = "" if listing.is_available else " ⚠️ <b>已出租/下架</b>"
        text = format_listing(listing) + status
        await send_listing_message(
            context.bot, chat_id, listing, wishlist_item_keyboard(listing.id), text=text
        )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    active = await queries.is_monitor_active(chat_id)
    count = await queries.get_listing_count()
    wishlist_items = await queries.get_wishlist(chat_id)
    blacklist_items = await queries.get_blacklist(chat_id)

    status_text = "🟢 執行中" if active else "🔴 已停止"
    await update.message.reply_text(
        f"📊 <b>監控狀態</b>\n\n"
        f"Chat ID：<code>{chat_id}</code>\n"
        f"狀態：{status_text}\n"
        f"資料庫物件數：{count:,} 筆\n"
        f"願望清單：{len(wishlist_items)} 筆\n"
        f"黑名單：{len(blacklist_items)} 筆\n\n"
        f"<b>指令</b>\n"
        f"  開始 / 停止 — 控制監控\n"
        f"  /preferences — 設定篩選條件\n"
        f"  /find — 依篩選條件搜尋現有房源\n"
        f"  /list_latest — 列出最新房源（套用篩選）\n"
        f"  /list_all — 列出所有已爬到的房源\n"
        f"  /cleanup_db — 清除不符合篩選條件的房源\n"
        f"  /wishlist — 查看願望清單\n"
        f"  /blacklist — 查看黑名單\n"
        f"  /status — 目前狀態\n"
        f"  /reset — 重置所有個人設定",
        parse_mode="HTML",
    )


async def handle_list_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text("🔍 正在從資料庫撈取最新的房源資料...")

    try:
        listings = await queries.get_recent_listings(limit=500)
        if not listings:
            await update.message.reply_text("📭 目前資料庫中還沒有任何房源資料喔！請確認監控是否已啟動。")
            return

        prefs = await queries.get_preference(chat_id)
        from scheduler.job_manager import _matches_preferences
        matched_ids = [l.id for l in listings if _matches_preferences(l, prefs)]

        if not matched_ids:
            await update.message.reply_text("📭 目前沒有符合篩選條件的房源。")
            return

        await queries.save_pagination_session(chat_id, matched_ids)
        await send_page(context.bot, chat_id, matched_ids, offset=0, queries=queries)

    except Exception as exc:
        logger.error("讀取最新房源發生錯誤: %s", exc)
        await update.message.reply_text("❌ 讀取資料發生錯誤，請查看後台日誌。")


async def handle_list_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """列出資料庫中所有已爬到的房源（不套用篩選條件）。"""
    chat_id = update.effective_chat.id
    await update.message.reply_text("🔍 正在從資料庫撈取所有房源資料...")

    try:
        listings = await queries.get_recent_listings(limit=500)
        if not listings:
            await update.message.reply_text("📭 資料庫中還沒有任何房源，請先啟動監控。")
            return

        all_ids = [l.id for l in listings]
        logger.info("[/list_all] chat %s: DB 共 %d 筆", chat_id, len(all_ids))

        await update.message.reply_text(
            f"📋 資料庫中共有 <b>{len(all_ids)}</b> 筆房源（不套用篩選條件）：",
            parse_mode="HTML",
        )
        await queries.save_pagination_session(chat_id, all_ids)
        await send_page(context.bot, chat_id, all_ids, offset=0, queries=queries)

    except Exception as exc:
        logger.error("[/list_all] 發生錯誤: %s", exc)
        await update.message.reply_text("❌ 讀取資料發生錯誤，請查看後台日誌。")


async def handle_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """根據目前篩選條件，從 DB 列出所有符合的房源（分頁）。"""
    chat_id = update.effective_chat.id
    await update.message.reply_text("🔍 正在套用篩選條件搜尋資料庫...")

    try:
        prefs = await queries.get_preference(chat_id)
        from scheduler.job_manager import _matches_preferences

        # 顯示目前套用的篩選條件摘要
        if prefs:
            parts = []
            if prefs.min_rent or prefs.max_rent:
                rent_range = f"{prefs.min_rent:,}" if prefs.min_rent else "不限"
                rent_range += f" ~ {prefs.max_rent:,}" if prefs.max_rent else " ~ 不限"
                parts.append(f"💰 租金：{rent_range} 元")
            if prefs.districts:
                parts.append(f"📍 地區：{', '.join(prefs.districts)}")
            if prefs.layout_filter:
                parts.append(f"🏢 格局：{', '.join(prefs.layout_filter)}")
            if prefs.pets_required:
                parts.append("🐾 可養寵物")
            if prefs.parking_required:
                parts.append("🚗 有停車位")
            if prefs.elevator_required:
                parts.append("🛗 有電梯")
            if prefs.keywords_include:
                parts.append(f"🔍 包含：{', '.join(prefs.keywords_include)}")
            if prefs.keywords_exclude:
                parts.append(f"🚫 排除：{', '.join(prefs.keywords_exclude)}")

            if parts:
                await update.message.reply_text(
                    "⚙️ <b>目前篩選條件</b>\n" + "\n".join(parts),
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text("⚙️ 目前無篩選條件，顯示全部房源。")
        else:
            await update.message.reply_text("⚙️ 尚未設定篩選條件，顯示全部房源。")

        listings = await queries.get_recent_listings(limit=500)
        if not listings:
            await update.message.reply_text("📭 資料庫中還沒有任何房源，請先啟動監控。")
            return

        matched_ids = [l.id for l in listings if _matches_preferences(l, prefs)]
        logger.info("[/find] chat %s: DB 共 %d 筆，符合條件 %d 筆", chat_id, len(listings), len(matched_ids))

        if not matched_ids:
            await update.message.reply_text(
                "📭 資料庫中目前沒有符合條件的房源。\n"
                "監控持續運行中，有新物件會立即通知你！"
            )
            return

        await queries.save_pagination_session(chat_id, matched_ids)
        await send_page(context.bot, chat_id, matched_ids, offset=0, queries=queries)

    except Exception as exc:
        logger.error("[/find] 搜尋發生錯誤: %s", exc)
        await update.message.reply_text("❌ 搜尋時發生錯誤，請查看後台日誌。")


async def handle_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    items = await queries.get_blacklist(chat_id)

    if not items:
        await update.message.reply_text(
            "🚫 你的黑名單是空的。\n"
            "點選房源卡片上的「🚫 不再顯示」即可加入黑名單。"
        )
        return

    await update.message.reply_text(f"🚫 黑名單（共 {len(items)} 筆），點「🔓 解除封鎖」可移除：")
    from bot.keyboards import blacklist_item_keyboard
    for item in items:
        source = item["source"]
        external_id = item["external_id"]
        added_at = item["added_at"].strftime("%Y-%m-%d") if item["added_at"] else ""
        await update.message.reply_text(
            f"🚫 [{source}] {external_id}\n🕐 封鎖於 {added_at}",
            reply_markup=blacklist_item_keyboard(source, external_id),
        )


def _cleanup_matches(listing, prefs) -> bool:
    """Strict AND filter for cleanup: rent range AND district must both pass."""
    if prefs.min_rent and listing.rent and listing.rent < prefs.min_rent:
        return False
    if prefs.max_rent and listing.rent and listing.rent > prefs.max_rent:
        return False
    if prefs.districts and listing.district and listing.district not in prefs.districts:
        return False
    return True


async def handle_web_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a one-time web login token valid for 24 hours."""
    chat_id = update.effective_chat.id
    token = await queries.create_web_session(chat_id)
    await update.message.reply_text(
        f"🔑 <b>網頁登入 Token</b>\n\n"
        f"<code>{token}</code>\n\n"
        f"在網頁登入頁面選擇「Token 登入」，貼上以上 Token 即可。\n"
        f"⏱ 有效期限：<b>24 小時</b>（每次執行此指令會重新產生）",
        parse_mode="HTML",
    )


async def handle_cleanup_db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """預覽並清除 DB 中不符合篩選條件（租金 AND 行政區）的房源。"""
    chat_id = update.effective_chat.id
    prefs = await queries.get_preference(chat_id)

    if not prefs or (not prefs.min_rent and not prefs.max_rent and not prefs.districts):
        await update.message.reply_text(
            "⚙️ 尚未設定租金範圍或行政區條件，無法進行清理。\n"
            "請先用 /preferences 設定篩選條件。"
        )
        return

    await update.message.reply_text("🔍 正在掃描資料庫...")

    all_listings = await queries.get_all_listings()
    if not all_listings:
        await update.message.reply_text("📭 資料庫中目前沒有任何房源。")
        return

    to_delete = [l for l in all_listings if not _cleanup_matches(l, prefs)]

    if not to_delete:
        await update.message.reply_text(
            f"✅ 資料庫中全部 {len(all_listings):,} 筆房源都符合你的篩選條件，無需清理！"
        )
        return

    # 統計各條件不符原因
    rent_fail_count    = sum(1 for l in to_delete if (
        (prefs.min_rent and l.rent and l.rent < prefs.min_rent) or
        (prefs.max_rent and l.rent and l.rent > prefs.max_rent)
    ))
    district_fail_count = sum(1 for l in to_delete if (
        prefs.districts and l.district and l.district not in prefs.districts
    ))

    conditions = []
    if prefs.min_rent or prefs.max_rent:
        lo = f"{prefs.min_rent:,}" if prefs.min_rent else "不限"
        hi = f"{prefs.max_rent:,}" if prefs.max_rent else "不限"
        conditions.append(f"💰 租金：{lo} ~ {hi} 元")
    if prefs.districts:
        conditions.append(f"📍 行政區：{', '.join(prefs.districts)}")

    breakdown = []
    if rent_fail_count:
        breakdown.append(f"  • 租金不符：{rent_fail_count:,} 筆")
    if district_fail_count:
        breakdown.append(f"  • 行政區不符：{district_fail_count:,} 筆")

    from bot.keyboards import cleanup_confirm_keyboard
    await update.message.reply_text(
        f"🗑 <b>資料庫清理預覽</b>\n\n"
        f"<b>清理條件（全部符合才保留）</b>\n"
        + "\n".join(conditions) +
        f"\n\n<b>預計刪除：{len(to_delete):,} 筆</b>（共 {len(all_listings):,} 筆）\n"
        + "\n".join(breakdown) +
        f"\n\n⚠️ 已加入願望清單的房源不會被刪除。\n"
        f"確定要清理嗎？",
        parse_mode="HTML",
        reply_markup=cleanup_confirm_keyboard(),
    )


_591_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://rent.591.com.tw/list",
}
# 591 下架頁面的 title 關鍵字
_591_NOT_FOUND_TITLE = "頁面不存在"


async def _is_591_removed(session: aiohttp.ClientSession, url: str) -> bool:
    """Return True if the 591 listing has been taken down."""
    try:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status == 404:
                return True
            if resp.status in (301, 302):
                # redirect without following → destination is likely homepage/list
                return True
            # 200 but "page not found" content (591 sometimes returns 200 for removed)
            text = await resp.text(errors="ignore")
            return _591_NOT_FOUND_TITLE in text
    except Exception as exc:
        logger.debug("[check_591] request error for %s: %s", url, exc)
        return False  # connection failure → assume still up, don't false-positive


async def handle_check_591(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """對資料庫中所有可用的 591 房源發 GET 請求，確認是否已下架。"""
    await update.message.reply_text("🔍 正在檢查 591 房源是否下架，請稍候...")

    try:
        all_listings = await queries.get_all_listings()
        targets = [l for l in all_listings if l.source == "591" and l.is_available and l.url]

        if not targets:
            await update.message.reply_text("📭 資料庫中沒有可用的 591 房源需要檢查。")
            return

        await update.message.reply_text(f"共 {len(targets)} 筆 591 房源，開始逐一確認...")

        removed = []
        timeout = aiohttp.ClientTimeout(total=15, connect=8)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(
            headers=_591_HEADERS, timeout=timeout, connector=connector
        ) as session:
            for listing in targets:
                if await _is_591_removed(session, listing.url):
                    await queries.mark_listing_unavailable(listing.source, listing.external_id)
                    removed.append(listing)
                await asyncio.sleep(0.5)

        if removed:
            await update.message.reply_text(
                f"⚠️ <b>共 {len(removed)} 筆 591 房源已下架：</b>", parse_mode="HTML"
            )
            # Send in chunks to stay within Telegram's 4096-char limit.
            chunk: list[str] = []
            chunk_len = 0
            for l in removed:
                title = (l.title or l.external_id)[:40]
                line = f'  • <a href="{l.url}">{title}</a>'
                if chunk_len + len(line) + 1 > 3800:
                    await update.message.reply_text(
                        "\n".join(chunk), parse_mode="HTML", disable_web_page_preview=True
                    )
                    chunk, chunk_len = [], 0
                chunk.append(line)
                chunk_len += len(line) + 1
            if chunk:
                await update.message.reply_text(
                    "\n".join(chunk), parse_mode="HTML", disable_web_page_preview=True
                )
        else:
            await update.message.reply_text(
                f"✅ 全部 {len(targets)} 筆 591 房源仍在上架中，無下架物件。"
            )

    except Exception as exc:
        logger.error("[/check_591] 發生錯誤: %s", exc)
        await update.message.reply_text("❌ 檢查時發生錯誤，請查看後台日誌。")


async def handle_check_fb(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """抓一次各 FB 社團（不寫入資料庫），回報抓到的貼文數與解析結果。"""
    from config import FACEBOOK_ENABLED, FACEBOOK_GROUPS
    from scheduler.job_manager import get_facebook_scraper
    from scrapers.facebook import FacebookAuthError

    if not FACEBOOK_ENABLED:
        await update.message.reply_text("📘 FB 爬蟲未啟用（FACEBOOK_GROUPS 為空）。")
        return

    await update.message.reply_text(f"🔍 正在檢查 {len(FACEBOOK_GROUPS)} 個 FB 社團，請稍候...")
    scraper = get_facebook_scraper()
    for group in FACEBOOK_GROUPS:
        try:
            results = await scraper.preview(group, limit=10)
        except FacebookAuthError as exc:
            await scraper.close()
            await update.message.reply_text(
                f"❌ FB 登入失效：{exc}\n請執行 python scripts/fb_login.py 重新登入。"
            )
            return
        except Exception as exc:
            logger.error("[/check_fb] group %s 失敗: %s", group, exc, exc_info=True)
            await update.message.reply_text(f"❌ 社團 {group} 抓取失敗：{exc}")
            continue

        parsed = [(p, l) for p, l in results if l is not None]
        lines = [
            f"📘 <b>社團 {group}</b>",
            f"抓到 {len(results)} 篇貼文，{len(parsed)} 篇判定為出租物件：",
        ]
        for _post, l in parsed[:5]:
            parts = [
                f"{l.rent:,}元" if l.rent else "租金?",
                l.district or "區域?",
                l.layout or "格局?",
                f"{l.size_ping:g}坪" if l.size_ping else "坪數?",
            ]
            lines.append(f'• <a href="{l.url}">{_esc_html(l.title[:30])}</a>\n  ' + " / ".join(parts))
        await update.message.reply_text(
            "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True
        )


def _esc_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚠️ <b>確定要重置嗎？</b>\n\n"
        "這將清除你的：\n"
        "• 願望清單\n"
        "• 黑名單\n"
        "• 篩選條件\n"
        "• 監控狀態\n\n"
        "<b>房源資料庫不受影響。</b>",
        parse_mode="HTML",
        reply_markup=reset_confirm_keyboard(),
    )
