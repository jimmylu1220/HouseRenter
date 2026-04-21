import asyncio
import logging
from typing import Optional

import aiohttp
from telegram import Bot, InlineKeyboardMarkup, InputMediaPhoto

from db.models import Listing

logger = logging.getLogger(__name__)

PAGE_SIZE = 10

# Telegram media_group hard limit is 10; first item carries the caption.
_MAX_MEDIA_GROUP = 10
# Telegram caption hard limit
_CAPTION_MAX = 1024
# Per-image download timeout (FB CDN can be slow)
_IMG_DOWNLOAD_TIMEOUT = 15


def _normalize_images(images) -> list[dict]:
    """Coerce the listing.images JSONB into list[{url, file_id}].

    The scraper writes ["http://...", "http://..."]; once we've sent the
    media group once, we re-write it as [{"url": ..., "file_id": ...}, ...].
    Both shapes are accepted on read.
    """
    if not images:
        return []
    out: list[dict] = []
    for item in images:
        if isinstance(item, str):
            out.append({"url": item, "file_id": None})
        elif isinstance(item, dict):
            out.append({"url": item.get("url"), "file_id": item.get("file_id")})
    return out


async def _download_image(url: str) -> Optional[bytes]:
    """Fetch an image into memory. Returns None on failure."""
    if not url:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=_IMG_DOWNLOAD_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={
                # Some CDNs (Facebook) require a browser-ish UA
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception as exc:
        logger.debug("image download failed %s: %s", url, exc)
        return None


async def _build_media_item(img: dict, caption: str | None) -> Optional[InputMediaPhoto]:
    """Resolve one image entry into an InputMediaPhoto.

    Preference order:
      1. cached telegram file_id (free, instant)
      2. raw URL (Telegram fetches it; works for 591/HouseFun)
      3. downloaded bytes (fallback for protected CDNs like Facebook)
    """
    file_id = img.get("file_id")
    url = img.get("url")
    if file_id:
        return InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML")
    if not url:
        return None
    # Try direct URL first — fast path for public CDNs.
    # We can't actually probe Telegram from here without sending, so we
    # always download for the first send. After the first send we cache file_id.
    blob = await _download_image(url)
    if blob is None:
        return None
    return InputMediaPhoto(media=blob, caption=caption, parse_mode="HTML")


async def _send_multi_image(
    bot: Bot,
    chat_id: int,
    listing: Listing,
    text: str,
) -> bool:
    """Send a listing as a media group. Returns True on success.

    On the first successful send, captures file_ids from the returned
    Message objects and writes them back to listings.images so future
    sends are free.
    """
    images = _normalize_images(listing.images)[:_MAX_MEDIA_GROUP]
    if not images:
        return False

    # Caption only on the first photo, and only up to the limit.
    caption = text if len(text) <= _CAPTION_MAX else text[:_CAPTION_MAX - 1] + "…"

    media: list[InputMediaPhoto] = []
    for idx, img in enumerate(images):
        item = await _build_media_item(img, caption if idx == 0 else None)
        if item is not None:
            media.append(item)

    if not media:
        return False

    try:
        sent_messages = await bot.send_media_group(chat_id=chat_id, media=media)
    except Exception as exc:
        logger.warning("send_media_group failed for listing %s: %s", listing.id, exc)
        return False

    # Cache file_ids back to DB so subsequent sends are instant.
    try:
        from db.queries import update_listing_images
        cached: list[dict] = []
        # sent_messages aligns with media order; pair them up to len(media)
        for idx, msg in enumerate(sent_messages):
            url = images[idx]["url"] if idx < len(images) else None
            fid = None
            if msg.photo:
                fid = msg.photo[-1].file_id   # largest size
            cached.append({"url": url, "file_id": fid})
        # If the listing had more URLs than we successfully sent, append the rest unchanged
        if len(images) > len(cached):
            cached.extend(images[len(cached):])
        if listing.id is not None:
            await update_listing_images(listing.id, cached)
            listing.images = cached
    except Exception as exc:
        logger.debug("file_id cache write-back failed for listing %s: %s", listing.id, exc)

    return True


async def send_listing_message(
    bot: Bot,
    chat_id: int,
    listing: Listing,
    reply_markup: InlineKeyboardMarkup,
    text: str | None = None,
) -> None:
    """Send a listing card.

    Strategy:
      - If listing has 2+ images → send_media_group (caption on first photo).
      - Else if has thumbnail_url → single send_photo.
      - Else → text-only send_message.
    Falls back to text on any failure.
    """
    from bot.formatter import format_listing
    if text is None:
        text = format_listing(listing)

    images = _normalize_images(listing.images)

    # Multi-image path (skip for Facebook: send_media_group can't carry reply_markup,
    # so we'd need a split message. Use single-photo path to keep it as one message.)
    if len(images) >= 2 and listing.source != "facebook":
        ok = await _send_multi_image(bot, chat_id, listing, text)
        if ok:
            # send_media_group doesn't support reply_markup, so send the buttons separately.
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="↑ 操作 ↑",
                    reply_markup=reply_markup,
                    disable_notification=True,
                )
            except Exception as exc:
                logger.debug("button trailer failed for listing %s: %s", listing.id, exc)
            return
        # fall through to single-image / text fallback

    # Single image path (URL or first cached file_id)
    photo_ref: Optional[str] = listing.thumbnail_url
    if not photo_ref and images:
        photo_ref = images[0].get("file_id") or images[0].get("url")

    if photo_ref:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_ref,
                caption=text if len(text) <= _CAPTION_MAX else text[:_CAPTION_MAX - 1] + "…",
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
