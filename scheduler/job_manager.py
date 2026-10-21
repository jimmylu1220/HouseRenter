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
    """Start the shared scraper jobs that serve all active users.

    591 runs every SCRAPE_INTERVAL_MINUTES. Facebook (if configured) runs as a
    separate, slower job so a slow or broken FB scrape never delays 591.
    """
    from config import FACEBOOK_ENABLED, FACEBOOK_INTERVAL_MINUTES

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

    if FACEBOOK_ENABLED:
        scheduler.add_job(
            func=_facebook_scrape_and_notify,
            trigger=IntervalTrigger(minutes=FACEBOOK_INTERVAL_MINUTES),
            args=[application],
            id="facebook_monitor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            # Give the first 591 run (and any first-run scans) a head start.
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        logger.info("Started Facebook scrape job (every %s min)", FACEBOOK_INTERVAL_MINUTES)


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

async def _load_user_data(active_chat_ids: list[int], queries) -> dict[int, dict]:
    """Load each user's prefs + blacklist + first-run state."""
    user_data: dict[int, dict] = {}
    for chat_id in active_chat_ids:
        user_data[chat_id] = {
            "prefs": await queries.get_preference(chat_id),
            "blacklisted": await queries.get_blacklisted_set(chat_id),
            "is_first": not await queries.is_first_run_done(chat_id),
            "first_run_ids": [],
        }
    return user_data


async def _run_source(scraper, region_ids, user_data, application, queries) -> tuple[set[str], bool]:
    """Scrape one source, upsert matching listings and push new ones.

    Returns (external ids seen, whether the source was scraped to the end).
    """
    from bot.sender import send_listing_message
    from bot.keyboards import listing_keyboard

    seen: set[str] = set()
    early_exit_after = getattr(scraper, "EARLY_EXIT_AFTER_KNOWN", 5)
    consecutive_old_count = 0

    async for listing in scraper.fetch_listings(region_ids):
        # 先篩選：找出所有符合條件的使用者
        matching_users = [
            (chat_id, ud)
            for chat_id, ud in user_data.items()
            if (listing.source, listing.external_id) not in ud["blacklisted"]
            and _matches_preferences(listing, ud["prefs"])
        ]

        # 沒有任何使用者符合 → 不寫入 DB，直接略過
        if not matching_users:
            continue

        # 至少一位使用者符合 → upsert 到 DB，並記錄 seen（用於下架偵測）
        listing_id, is_new = await queries.upsert_listing(listing)
        listing.id = listing_id
        seen.add(listing.external_id)

        if is_new:
            consecutive_old_count = 0
        else:
            consecutive_old_count += 1
            if early_exit_after and consecutive_old_count >= early_exit_after:
                logger.info("%s 已追上進度，提早結束爬取。", scraper.SOURCE_NAME)
                return seen, False

        # 分發給符合條件的使用者
        for chat_id, ud in matching_users:
            if ud["is_first"]:
                ud["first_run_ids"].append(listing_id)
            elif is_new:
                try:
                    await send_listing_message(
                        application.bot, chat_id, listing, listing_keyboard(listing_id)
                    )
                except Exception as exc:
                    logger.warning("即時推播失敗 listing %s → chat %s: %s", listing_id, chat_id, exc)

    return seen, True


async def _global_scrape_and_notify(application: "Application") -> None:
    import db.queries as queries
    from scrapers.scraper_591 import Scraper591
    from bot.sender import send_page
    from config import REGION_IDS

    active_chat_ids = await queries.get_all_active_monitors()
    if not active_chat_ids:
        logger.info("全域爬蟲：無活躍監控使用者，跳過本輪。")
        return

    logger.info("全域爬蟲開始，服務 %d 位使用者", len(active_chat_ids))

    user_data = await _load_user_data(active_chat_ids, queries)
    for chat_id, ud in user_data.items():
        if ud["is_first"]:
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
            seen, completed = await _run_source(scraper, region_ids, user_data, application, queries)
            seen_ids[scraper.SOURCE_NAME] |= seen
            if completed:
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
    # Sources may opt out via STALE_CHECK_ENABLED=False (e.g. Facebook, where
    # we only see the most recent posts and "not seen" doesn't mean "removed").
    stale_disabled_sources = {
        s.SOURCE_NAME for s in scrapers if not getattr(s, "STALE_CHECK_ENABLED", True)
    }
    stale_ids: list[int] = []
    for source, ids in seen_ids.items():
        if source in stale_disabled_sources:
            continue
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


# ── Facebook job ───────────────────────────────────────────────────────────────

_fb_scraper = None
_fb_auth_alerted = False


def get_facebook_scraper():
    """Shared instance so the logged-in browser is reused across cycles."""
    global _fb_scraper
    if _fb_scraper is None:
        from scrapers.facebook import ScraperFacebook
        _fb_scraper = ScraperFacebook()
    return _fb_scraper


async def close_scrapers() -> None:
    global _fb_scraper
    if _fb_scraper is not None:
        await _fb_scraper.close()
        _fb_scraper = None


async def _facebook_scrape_and_notify(application: "Application") -> None:
    global _fb_auth_alerted
    import db.queries as queries
    from scrapers.facebook import FacebookAuthError
    from config import FACEBOOK_ADMIN_CHAT_ID

    active_chat_ids = await queries.get_all_active_monitors()
    if not active_chat_ids:
        return

    # Users still in their first 591 scan are skipped; they get FB listings
    # from the next cycle on.
    user_data = {
        cid: ud for cid, ud in (await _load_user_data(active_chat_ids, queries)).items()
        if not ud["is_first"]
    }
    if not user_data:
        return

    scraper = get_facebook_scraper()
    try:
        await _run_source(scraper, [], user_data, application, queries)
        _fb_auth_alerted = False
    except FacebookAuthError as exc:
        logger.error("FB 登入失效: %s", exc)
        await scraper.close()
        if FACEBOOK_ADMIN_CHAT_ID and not _fb_auth_alerted:
            _fb_auth_alerted = True
            try:
                await application.bot.send_message(
                    chat_id=FACEBOOK_ADMIN_CHAT_ID,
                    text=(
                        "⚠️ <b>FB 爬蟲登入失效</b>\n\n"
                        f"{exc}\n\n"
                        "請在本機執行 <code>python scripts/fb_login.py</code> 重新登入，"
                        "並更新伺服器上的 state 檔。"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
    except Exception as exc:
        logger.error("FB 爬蟲發生錯誤: %s", exc, exc_info=True)
        await scraper.close()


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

    combined = (
        f"{listing.title or ''} {listing.description or ''} {str(listing.extra_info or '')}"
    ).lower()
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
