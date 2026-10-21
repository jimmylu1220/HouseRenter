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

# Listings posted to a Discord channel after its baseline scan.
_DISCORD_FIRST_RUN_PREVIEW = 5


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


def ensure_global_job(application: "Application") -> None:
    """Start the scrape jobs unless they are already scheduled."""
    if get_scheduler().get_job("global_monitor") is None:
        start_global_job(application)


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


async def _load_channel_data(queries) -> dict[int, dict]:
    """Load each subscribed Discord channel's filters + first-run state.

    Empty when the Discord bot isn't configured or isn't running.
    """
    from discord_bot.client import get_discord_bot

    if get_discord_bot() is None:
        return {}
    return {
        ch.channel_id: {
            "prefs": ch.prefs,
            "is_first": not ch.first_run_done,
            "updated_at": ch.updated_at,
            "first_run_ids": [],
        }
        for ch in await queries.get_active_discord_channels()
    }


async def _run_source(
    scraper, region_ids, user_data, application, queries, channel_data=None
) -> tuple[set[str], bool]:
    """Scrape one source, upsert matching listings and push new ones.

    Returns (external ids seen, whether the source was scraped to the end).
    """
    from bot.sender import send_listing_message
    from bot.keyboards import listing_keyboard
    from config import DISCORD_MIRROR_CHAT_ID
    from discord_bot.client import get_discord_bot
    from notifiers.discord_webhook import get_discord_notifier

    discord = get_discord_notifier() if DISCORD_MIRROR_CHAT_ID else None
    discord_bot = get_discord_bot()
    channel_data = channel_data or {}
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

        matching_channels = [
            (channel_id, cd)
            for channel_id, cd in channel_data.items()
            if _matches_preferences(listing, cd["prefs"])
        ]

        # 沒有任何使用者或 Discord 頻道符合 → 不寫入 DB，直接略過
        if not matching_users and not matching_channels:
            continue

        # 至少一位使用者或頻道符合 → upsert 到 DB，並記錄 seen（用於下架偵測）
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
                if discord and chat_id == DISCORD_MIRROR_CHAT_ID:
                    try:
                        await discord.send_listing(listing)
                    except Exception as exc:
                        logger.warning("Discord 推播失敗 listing %s: %s", listing_id, exc)

        for channel_id, cd in matching_channels:
            if cd["is_first"]:
                cd["first_run_ids"].append(listing_id)
            elif is_new and discord_bot:
                try:
                    await discord_bot.send_listing(channel_id, listing)
                except Exception as exc:
                    logger.warning("Discord 頻道推播失敗 listing %s → channel %s: %s", listing_id, channel_id, exc)

    return seen, True


async def _finish_discord_first_runs(channel_data: dict[int, dict], queries) -> None:
    """Report each new/re-filtered channel's baseline and mark it done."""
    from discord_bot.client import get_discord_bot

    discord_bot = get_discord_bot()
    if discord_bot is None:
        return

    for channel_id, cd in channel_data.items():
        if not cd["is_first"]:
            continue
        ids = cd["first_run_ids"]
        try:
            if ids:
                preview = ids[:_DISCORD_FIRST_RUN_PREVIEW]
                await discord_bot.send_text(
                    channel_id,
                    f"✅ 掃描完成：目前有 **{len(ids)}** 筆符合條件的房源，以下是最新 {len(preview)} 筆。\n"
                    "之後有新房源會即時推播到這裡。",
                )
                for listing_id in preview:
                    listing = await queries.get_listing_by_id(listing_id)
                    if listing:
                        await discord_bot.send_listing(channel_id, listing)
            else:
                await discord_bot.send_text(
                    channel_id,
                    "📭 掃描完成：目前沒有符合條件的房源，有新物件會即時推播到這裡。",
                )
        except Exception as exc:
            logger.warning("Discord 首次掃描回報失敗 channel %s: %s", channel_id, exc)
        await queries.set_discord_first_run_done(channel_id, cd["updated_at"])


async def _global_scrape_and_notify(application: "Application") -> None:
    import db.queries as queries
    from scrapers.scraper_591 import Scraper591
    from bot.sender import send_page
    from config import REGION_IDS

    active_chat_ids = await queries.get_all_active_monitors()
    channel_data = await _load_channel_data(queries)
    if not active_chat_ids and not channel_data:
        logger.info("全域爬蟲：無活躍監控使用者或 Discord 頻道，跳過本輪。")
        return

    logger.info(
        "全域爬蟲開始，服務 %d 位使用者、%d 個 Discord 頻道",
        len(active_chat_ids), len(channel_data),
    )

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
            seen, completed = await _run_source(
                scraper, region_ids, user_data, application, queries, channel_data
            )
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

    await _finish_discord_first_runs(channel_data, queries)


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

    # Users and channels still in their first 591 scan are skipped; they get
    # FB listings from the next cycle on.
    user_data = {
        cid: ud for cid, ud in (await _load_user_data(active_chat_ids, queries)).items()
        if not ud["is_first"]
    }
    channel_data = {
        cid: cd for cid, cd in (await _load_channel_data(queries)).items()
        if not cd["is_first"]
    }
    if not user_data and not channel_data:
        return

    scraper = get_facebook_scraper()
    try:
        await _run_source(scraper, [], user_data, application, queries, channel_data)
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
    Every configured condition must pass (AND): rent range, min size,
    excluded keywords, districts, layout, pets, parking, elevator,
    included keywords.
    """
    if prefs is None:
        return True

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

    if prefs.districts and listing.district and listing.district not in prefs.districts:
        return False
    if prefs.layout_filter and listing.layout and listing.layout not in prefs.layout_filter:
        return False
    if prefs.pets_required and not listing.pets:
        return False
    if prefs.parking_required and not listing.parking:
        return False
    if prefs.elevator_required and listing.elevator is not True:
        return False
    if prefs.keywords_include:
        if any(kw.lower() not in combined for kw in prefs.keywords_include):
            return False

    return True
