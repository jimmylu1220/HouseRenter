import logging

from telegram import Update
from telegram.ext import ContextTypes

import db.queries as queries
from bot.formatter import format_listing_detail
from bot.sender import send_page

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    # NOTE: Each branch answers the query itself; do NOT call query.answer() here.
    data = query.data
    chat_id = update.effective_chat.id

    # ── Wishlist: inline remove (from listing card, keeps message) ───────────
    # Must be checked BEFORE wl_remove_ to avoid startswith collision
    if data.startswith("wl_remove_inline_"):
        listing_id = int(data.split("_")[-1])
        await queries.remove_from_wishlist(chat_id, listing_id)
        await query.answer("已從願望清單移除。", show_alert=True)
        from bot.keyboards import listing_keyboard
        try:
            await query.edit_message_reply_markup(reply_markup=listing_keyboard(listing_id))
        except Exception:
            pass

    # ── Wishlist: add ────────────────────────────────────────────────────────
    elif data.startswith("wl_add_"):
        listing_id = int(data.split("_")[-1])
        added = await queries.add_to_wishlist(chat_id, listing_id)
        if added:
            await query.answer("✅ 已加入願望清單！", show_alert=True)
            from bot.keyboards import listing_keyboard_wishlisted
            try:
                await query.edit_message_reply_markup(
                    reply_markup=listing_keyboard_wishlisted(listing_id)
                )
            except Exception:
                pass
        else:
            await query.answer("已在願望清單中。", show_alert=True)

    # ── Wishlist: remove from wishlist page (deletes message) ────────────────
    elif data.startswith("wl_remove_"):
        listing_id = int(data.split("_")[-1])
        await queries.remove_from_wishlist(chat_id, listing_id)
        await query.answer("🗑 已從願望清單移除。", show_alert=True)
        try:
            await query.delete_message()
        except Exception:
            pass

    # ── Wishlist: view original page ─────────────────────────────────────────
    elif data.startswith("wl_view_"):
        listing_id = int(data.split("_")[-1])
        listing = await queries.get_listing_by_id(listing_id)
        if listing:
            await query.answer()
            await context.bot.send_message(
                chat_id=chat_id,
                text=f'🔗 <a href="{listing.url}">點此查看原始頁面</a>',
                parse_mode="HTML",
            )
        else:
            await query.answer("找不到此房源資訊。", show_alert=True)

    # ── Wishlist: clear all unavailable ──────────────────────────────────────
    elif data == "wl_clear_unavailable":
        count = await queries.remove_unavailable_from_wishlist(chat_id)
        if count > 0:
            await query.answer(f"已清除 {count} 筆已下架物件。", show_alert=True)
        else:
            await query.answer("沒有已下架的物件。", show_alert=True)
        try:
            await query.delete_message()
        except Exception:
            pass

    # ── Blacklist: add ───────────────────────────────────────────────────────
    elif data.startswith("bl_add_"):
        listing_id = int(data.split("_")[-1])
        listing = await queries.get_listing_by_id(listing_id)
        if listing:
            await queries.add_to_blacklist(chat_id, listing.source, listing.external_id)
            await query.answer("🚫 已加入黑名單，此房源不會再出現。", show_alert=True)
            try:
                await query.delete_message()
            except Exception:
                pass
        else:
            await query.answer("找不到此房源。", show_alert=True)

    # ── Blacklist: remove ────────────────────────────────────────────────────
    elif data.startswith("bl_remove|"):
        # format: bl_remove|{source}|{external_id}
        parts = data.split("|", 2)
        if len(parts) == 3:
            _, source, external_id = parts
            await queries.remove_from_blacklist(chat_id, source, external_id)
            await query.answer("🔓 已解除封鎖。", show_alert=True)
            try:
                await query.delete_message()
            except Exception:
                pass
        else:
            await query.answer()

    # ── Detail view ──────────────────────────────────────────────────────────
    elif data.startswith("detail_"):
        listing_id = int(data.split("_")[-1])
        listing = await queries.get_listing_by_id(listing_id)
        if listing:
            await query.answer()
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_listing_detail(listing),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            await query.answer("找不到詳細資訊。", show_alert=True)

    # ── Pagination: load next page ───────────────────────────────────────────
    elif data.startswith("list_more_"):
        offset = int(data.split("_")[-1])
        listing_ids = await queries.get_pagination_session(chat_id)
        if listing_ids:
            await query.answer()
            try:
                await query.delete_message()
            except Exception:
                pass
            await send_page(context.bot, chat_id, listing_ids, offset, queries)
        else:
            await query.answer("找不到分頁資料，請重新執行 /list_latest。", show_alert=True)

    # ── Reset: confirm ───────────────────────────────────────────────────────
    elif data == "reset_confirm":
        await query.answer()
        await queries.clear_user_data(chat_id)
        from scheduler.job_manager import stop_job_for_chat
        stop_job_for_chat(chat_id)
        try:
            await query.edit_message_text("✅ 已重置完成。輸入「開始」可重新啟動監控。")
        except Exception:
            pass

    # ── Reset: cancel ────────────────────────────────────────────────────────
    elif data == "reset_cancel":
        await query.answer()
        try:
            await query.edit_message_text("已取消重置。")
        except Exception:
            pass

    else:
        await query.answer()
