import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import SCRAPE_INTERVAL_MINUTES

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Track consecutive scrape failures per chat_id for alert notifications.
_scrape_failure_counts: dict[int, int] = defaultdict(int)
_FAILURE_ALERT_THRESHOLD = 3


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_global_job(application: "Application") -> None:
    """Start the single shared scraper job that serves all active users."""
    scheduler = get_scheduler()
    scheduler.add_job(
        func=_global_scrape_and_notify,
        trigger=IntervalTrigger(minutes=SCRAPE_INTERVAL_MINUTES),
        args=[application],
        id="global_monitor",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    logger.info("Started global scrape job (every %s min)", SCRAPE_INTERVAL_MINUTES)


def start_weekly_cleanup(application: "Application") -> None:
    """Start the weekly auto-cleanup job (runs every Sunday at 03:00 UTC)."""
    scheduler = get_scheduler()
    scheduler.add_job(
        func=_weekly_cleanup,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="UTC"),
        args=[application],
        id="weekly_cleanup",
        replace_existing=True,
    )
    logger.info("Scheduled weekly cleanup (Sun 03:00 UTC).")


# ── kept for backward compat (called from main.py restore loop) ───────────────
def start_job_for_chat(chat_id: int, application: "Application") -> None:
    """Ensure the global job is running (chat_id arg is ignored)."""
    start_global_job(application)


def stop_job_for_chat(chat_id: int) -> None:
    """Stop the global job only if no active monitors remain."""
    pass   # Global job lifecycle is managed by start/stop monitoring logic


# ── Global scrape job ──────────────────────────────────────────────────────────

async def _global_scrape_and_notify(application: "Application") -> None:
    import db.queries as queries
    from scrapers.scraper_591 import Scraper591
    from bot.sender import send_listing_message, send_page
    from config import REGION_IDS

    active_chat_ids = await queries.get_all_active_monitors()
    if not active_chat_ids:
        logger.info("全域爬蟲：無活躍監控使用者，跳過本輪。")
        return

    logger.info("全域爬蟲開始，服務 %d 位使用者", len(active_chat_ids))

    # Load each user's prefs + blacklist + first-run state
    user_data: dict[int, dict] = {}
    for chat_id in active_chat_ids:
        prefs      = await queries.get_preference(chat_id)
        blacklisted = await queries.get_blacklisted_set(chat_id)
        is_first   = not await queries.is_first_run_done(chat_id)
        user_data[chat_id] = {
            "prefs": prefs,
            "blacklisted": blacklisted,
            "is_first": is_first,
            "first_run_ids": [],
        }
        if is_first:
            await application.bot.send_message(
                chat_id=chat_id,
                text="🔍 首次掃描開始，這可能需要幾分鐘的時間建立初始資料庫...\n符合條件的最新房源會陸續出現！",
            )

    region_ids = list(REGION_IDS.values())
    seen_ids: dict[str, set[str]] = defaultdict(set)
    completed_sources: set[str] = set()

    scrapers = [Scraper591()]
    scrape_failed = False
    try:
        for scraper in scrapers:
            consecutive_old_count = 0
            early_exit = False

            async for listing in scraper.fetch_listings(region_ids):
                seen_ids[scraper.SOURCE_NAME].add(listing.external_id)

                # Upsert to DB once (globally) to determine if it's truly new
                listing_id, is_new = await queries.upsert_listing(listing)
                listing.id = listing_id

                if is_new:
                    consecutive_old_count = 0
                else:
                    consecutive_old_count += 1
                    if consecutive_old_count >= 5:
                        logger.info("%s 已追上進度，提早結束爬取。", scraper.SOURCE_NAME)
                        early_exit = True
                        break

                # Distribute to each user based on their prefs + blacklist
                for chat_id, ud in user_data.items():
                    if (listing.source, listing.external_id) in ud["blacklisted"]:
                        continue
                    if not _matches_preferences(listing, ud["prefs"]):
                        continue

                    if ud["is_first"]:
                        ud["first_run_ids"].append(listing_id)
                    elif is_new:
                        from bot.keyboards import listing_keyboard
                        try:
                            await send_listing_message(
                                application.bot, chat_id, listing, listing_keyboard(listing_id)
                            )
                        except Exception as exc:
                            logger.warning("即時推播失敗 listing %s → chat %s: %s", listing_id, chat_id, exc)

            if not early_exit:
                completed_sources.add(scraper.SOURCE_NAME)

        for scraper in scrapers:
            await scraper.close()

    except Exception as exc:
        logger.error("全域爬蟲發生錯誤: %s", exc, exc_info=True)
        scrape_failed = True

    # ── Failure alert ──────────────────────────────────────────────────────────
    if scrape_failed:
        for chat_id in active_chat_ids:
            _scrape_failure_counts[chat_id] += 1
            count = _scrape_failure_counts[chat_id]
            if count == _FAILURE_ALERT_THRESHOLD:
                try:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⚠️ <b>爬蟲告警</b>\n\n"
                            f"已連續 {count} 次爬取失敗，監控可能已中斷。\n"
                            f"請確認網路狀態或稍後重試。\n"
                            f"若問題持續，可輸入「停止」再「開始」重啟監控。"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return

    # Reset failure counts on success
    for chat_id in active_chat_ids:
        _scrape_failure_counts[chat_id] = 0

    # ── Stale detection ────────────────────────────────────────────────────────
    stale_ids: list[int] = []
    for source, ids in seen_ids.items():
        if source not in completed_sources:
            logger.info("%s 本輪提早結束，跳過下架判斷。", source)
            continue
        stale = await queries.mark_stale_listings_unavailable(source, ids)
        stale_ids.extend(stale)
    if stale_ids:
        await _notify_wishlist_removals(stale_ids, application, queries)

    # ── First-run finish & regular summary ────────────────────────────────────
    for chat_id, ud in user_data.items():
        if ud["is_first"]:
            ids = ud["first_run_ids"]
            if not ids:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text="📭 初始掃描完成，目前沒有找到符合條件的房源。\n我會持續監控，有新物件會立即通知你！",
                )
            else:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ 首次掃描完成！共找到 <b>{len(ids)}</b> 筆符合條件的房源，每次顯示 10 筆：",
                    parse_mode="HTML",
                )
                await queries.save_pagination_session(chat_id, ids)
                await send_page(application.bot, chat_id, ids, offset=0, queries=queries)
            await queries.set_first_run_done(chat_id)


# ── Weekly auto-cleanup ────────────────────────────────────────────────────────

async def _weekly_cleanup(application: "Application") -> None:
    """Delete listings unseen for 30+ days that are not in any wishlist."""
    import db.queries as queries

    logger.info("每週自動清理開始...")
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        deleted = await queries.delete_old_listings(cutoff)
        logger.info("每週清理完成，刪除 %d 筆過期房源。", deleted)
    except Exception as exc:
        logger.error("每週清理發生錯誤: %s", exc)


# ── Wishlist removal notifications ────────────────────────────────────────────

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
        return True

    # ── Hard AND conditions ────────────────────────────────────────────────────
    if prefs.min_rent and listing.rent and listing.rent < prefs.min_rent:
        return False
    if prefs.max_rent and listing.rent and listing.rent > prefs.max_rent:
        return False
    if prefs.min_size and listing.size_ping and listing.size_ping < prefs.min_size:
        return False

    combined = f"{listing.title or ''} {str(listing.extra_info or '')}".lower()
    if prefs.keywords_exclude:
        if any(kw.lower() in combined for kw in prefs.keywords_exclude):
            return False

    # ── Soft OR conditions ─────────────────────────────────────────────────────
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
        return False

    return True
