import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import SCRAPE_INTERVAL_MINUTES

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_job_for_chat(chat_id: int, application: "Application") -> None:
    scheduler = get_scheduler()
    job_id = f"monitor_{chat_id}"
    scheduler.add_job(
        func=_scrape_and_notify,
        trigger=IntervalTrigger(minutes=SCRAPE_INTERVAL_MINUTES),
        args=[chat_id, application],
        id=job_id,
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    logger.info("Started scrape job for chat %s (every %s min)", chat_id, SCRAPE_INTERVAL_MINUTES)


def stop_job_for_chat(chat_id: int) -> None:
    scheduler = get_scheduler()
    job_id = f"monitor_{chat_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("Stopped scrape job for chat %s", chat_id)


# ── Main scrape job ────────────────────────────────────────────────────────────

async def _scrape_and_notify(chat_id: int, application: "Application") -> None:
    import db.queries as queries
    from scrapers.scraper_591 import Scraper591
    from bot.keyboards import listing_keyboard
    from bot.sender import send_listing_message, send_page
    from config import REGION_IDS

    region_ids = list(REGION_IDS.values())
    prefs = await queries.get_preference(chat_id)
    is_first = not await queries.is_first_run_done(chat_id)
    blacklisted = await queries.get_blacklisted_set(chat_id)

    logger.info("開始執行監控排程 chat %s (首次執行=%s)", chat_id, is_first)

    if is_first:
        await application.bot.send_message(
            chat_id=chat_id,
            text="🔍 首次掃描開始，這可能需要幾分鐘的時間建立初始資料庫...\n符合條件的最新房源會陸續出現！",
        )

    seen_ids: dict[str, set[str]] = defaultdict(set)
    # Track which sources completed a full scrape (no early exit).
    # Only these are safe to use for stale detection.
    completed_sources: set[str] = set()
    new_listings: list[tuple[int, object]] = []
    first_run_matched_ids: list[int] = []

    scrapers = [Scraper591()]
    try:
        for scraper in scrapers:
            consecutive_old_count = 0
            early_exit = False

            async for listing in scraper.fetch_listings(region_ids):
                # Always track seen external_ids for stale detection,
                # regardless of prefs — so non-matching listings are NOT
                # incorrectly marked unavailable.
                seen_ids[scraper.SOURCE_NAME].add(listing.external_id)

                # Skip blacklisted listings entirely
                if (listing.source, listing.external_id) in blacklisted:
                    continue

                # Pre-filter: skip listings that don't match preferences (keeps DB clean).
                if not _matches_preferences(listing, prefs):
                    continue

                listing_id, is_new = await queries.upsert_listing(listing)
                listing.id = listing_id

                if is_new:
                    consecutive_old_count = 0
                    if is_first:
                        first_run_matched_ids.append(listing_id)
                    else:
                        new_listings.append((listing_id, listing))
                        from bot.keyboards import listing_keyboard
                        from bot.sender import send_listing_message
                        try:
                            await send_listing_message(
                                application.bot, chat_id, listing, listing_keyboard(listing_id)
                            )
                        except Exception as exc:
                            logger.warning("即時推播失敗 listing %s: %s", listing_id, exc)
                else:
                    if not is_first:
                        consecutive_old_count += 1
                        if consecutive_old_count >= 5:
                            logger.info("%s 已追上進度，提早結束爬取。", scraper.SOURCE_NAME)
                            early_exit = True
                            break

            # Only mark stale if the scraper ran to completion.
            # An early exit means we didn't see all pages, so we can't
            # reliably determine which listings are truly gone.
            if not early_exit:
                completed_sources.add(scraper.SOURCE_NAME)

        for scraper in scrapers:
            await scraper.close()

    except Exception as exc:
        logger.error("Scrape error for chat %s: %s", chat_id, exc)
        return

    # ── Step 2: 處理下架房源（日常監控，且爬蟲完整跑完時才做）────────────────────
    if not is_first:
        stale_ids: list[int] = []
        for source, ids in seen_ids.items():
            if source not in completed_sources:
                logger.info("%s 本輪提早結束，跳過下架判斷以避免誤標。", source)
                continue
            stale = await queries.mark_stale_listings_unavailable(source, ids)
            stale_ids.extend(stale)
        if stale_ids:
            await _notify_wishlist_removals(stale_ids, application, queries)

    # ── Step 3: 收尾與發送通知 ─────────────────────────────────────────────────
    if is_first:
        if not first_run_matched_ids:
            await application.bot.send_message(
                chat_id=chat_id,
                text="📭 初始掃描完成，目前沒有找到符合條件的房源。\n我會每分鐘持續監控，有新物件會立即通知你！",
            )
        else:
            total = len(first_run_matched_ids)
            await application.bot.send_message(
                chat_id=chat_id,
                text=f"✅ 首次掃描完成！共找到 <b>{total}</b> 筆符合條件的房源，每次顯示 10 筆：",
                parse_mode="HTML",
            )
            await queries.save_pagination_session(chat_id, first_run_matched_ids)
            await send_page(application.bot, chat_id, first_run_matched_ids, offset=0, queries=queries)
        await queries.set_first_run_done(chat_id)
    else:
        if new_listings:
            logger.info("本次共即時推播 %d 筆新房源給 chat %s", len(new_listings), chat_id)
        else:
            logger.info("本分鐘無符合條件的新房源。")



async def _notify_wishlist_removals(stale_ids: list[int], application, queries):
    """Notify users whose wishlisted listings just became unavailable."""
    pairs = await queries.get_wishlist_items_for_listings(stale_ids)
    for wish_item, listing in pairs:
        try:
            await application.bot.send_message(
                chat_id=wish_item.chat_id,
                text=(
                    f"⚠️ <b>願望清單通知</b>\n\n"
                    f"你收藏的房源已出租或下架：\n"
                    f"<b>{listing.title}</b>\n"
                    f"{listing.area} {listing.district} | {listing.rent:,} 元/月\n"
                    f'<a href="{listing.url}">查看原始頁面</a>'
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Failed to notify chat %s: %s", wish_item.chat_id, exc)
        await queries.mark_wishlist_notified(wish_item.id)
        await asyncio.sleep(0.3)


# ── Preference filter ─────────────────────────────────────────────────────────

def _matches_preferences(listing, prefs) -> bool:
    """
    Two-tier filter:
      AND (hard) — rent range, min size, excluded keywords: ALL must pass.
      OR  (soft) — districts, layout, pets, parking, elevator, included keywords:
                   if any are configured, AT LEAST ONE must match.
    """
    lid = getattr(listing, "id", None) or listing.external_id
    title_short = (listing.title or "")[:30]

    if prefs is None:
        logger.debug("[filter] %s | no prefs → PASS", lid)
        return True

    # ── Hard AND conditions ────────────────────────────────────────────────────
    if prefs.min_rent and listing.rent and listing.rent < prefs.min_rent:
        logger.debug("[filter] %s (%s) → FAIL rent %s < min %s", lid, title_short, listing.rent, prefs.min_rent)
        return False
    if prefs.max_rent and listing.rent and listing.rent > prefs.max_rent:
        logger.debug("[filter] %s (%s) → FAIL rent %s > max %s", lid, title_short, listing.rent, prefs.max_rent)
        return False
    if prefs.min_size and listing.size_ping and listing.size_ping < prefs.min_size:
        logger.debug("[filter] %s (%s) → FAIL size %.1f < %.1f", lid, title_short, listing.size_ping, prefs.min_size)
        return False

    combined = f"{listing.title or ''} {str(listing.extra_info or '')}".lower()
    if prefs.keywords_exclude:
        hit = [kw for kw in prefs.keywords_exclude if kw.lower() in combined]
        if hit:
            logger.debug("[filter] %s (%s) → FAIL excluded keywords %s", lid, title_short, hit)
            return False

    # ── Soft OR conditions ─────────────────────────────────────────────────────
    # Collect (label, passed) for every configured OR condition.
    # If none are configured → pass. Otherwise, at least one must be True.
    or_results: list[tuple[str, bool]] = []

    if prefs.districts and listing.district:
        or_results.append(("district", listing.district in prefs.districts))

    if prefs.layout_filter and listing.layout:
        or_results.append(("layout", listing.layout in prefs.layout_filter))

    if prefs.pets_required:
        or_results.append(("pets", bool(listing.pets)))

    if prefs.parking_required:
        or_results.append(("parking", bool(listing.parking)))

    if prefs.elevator_required:
        or_results.append(("elevator", listing.elevator is True))

    if prefs.keywords_include:
        missing = [kw for kw in prefs.keywords_include if kw.lower() not in combined]
        or_results.append(("keywords_include", not missing))

    if or_results and not any(passed for _, passed in or_results):
        failed = [name for name, passed in or_results if not passed]
        logger.debug("[filter] %s (%s) → FAIL OR conditions all failed: %s", lid, title_short, failed)
        return False

    logger.debug("[filter] %s (%s) → PASS", lid, title_short)
    return True
