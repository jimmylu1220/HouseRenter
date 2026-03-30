import asyncio
import logging

from telegram import Bot, InlineKeyboardMarkup

from db.models import Listing

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


async def send_listing_message(
    bot: Bot,
    chat_id: int,
    listing: Listing,
    reply_markup: InlineKeyboardMarkup,
    text: str | None = None,
) -> None:
    """Send a listing card. Attempts send_photo if thumbnail_url is present,
    falls back to send_message on failure or if no thumbnail."""
    from bot.formatter import format_listing
    if text is None:
        text = format_listing(listing)

    if listing.thumbnail_url:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=listing.thumbnail_url,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return
        except Exception as exc:
            logger.warning(
                "send_photo failed for listing %s: %s — falling back to text",
                listing.id, exc,
            )

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def send_page(
    bot: Bot,
    chat_id: int,
    listing_ids: list[int],
    offset: int,
    queries,
) -> None:
    """Send a page of listings from a pre-computed ID list, with a pagination button at the end."""
    from bot.keyboards import listing_keyboard, pagination_keyboard

    batch_ids = listing_ids[offset:offset + PAGE_SIZE]
    for lid in batch_ids:
        listing = await queries.get_listing_by_id(lid)
        if listing:
            await send_listing_message(bot, chat_id, listing, listing_keyboard(lid))
            await asyncio.sleep(0.5)

    total = len(listing_ids)
    next_offset = offset + PAGE_SIZE
    if next_offset < total:
        await bot.send_message(
            chat_id=chat_id,
            text=f"📋 已顯示 {min(next_offset, total)}/{total} 筆符合條件的房源",
            reply_markup=pagination_keyboard(next_offset, total),
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ 已顯示全部 {total} 筆符合條件的房源。",
        )
