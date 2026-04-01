import logging
import warnings

from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram import BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config import BOT_TOKEN
from db.database import init_db, close_db
from bot.handlers.command_handlers import (
    handle_start_monitoring,
    handle_stop_monitoring,
    handle_wishlist,
    handle_status,
    handle_list_latest,
    handle_blacklist,
    handle_reset,
    handle_find,
    handle_list_all,
    handle_cleanup_db,
    handle_web_token,
)
from bot.handlers.conversation_handlers import preferences_conv_handler
from bot.handlers.callback_handlers import handle_callback
from scheduler.job_manager import get_scheduler, start_global_job, start_weekly_cleanup, start_job_for_chat

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update, context) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning("Network error (will retry): %s", context.error)
    else:
        logger.error("Unhandled error: %s", context.error, exc_info=context.error)


async def post_init(application) -> None:
    await init_db()
    logger.info("Database initialised.")

    import db.queries as queries

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("Scheduler started.")

    await application.bot.set_my_commands([
        BotCommand("status",      "📊 查看目前監控狀態"),
        BotCommand("preferences", "⚙️ 設定篩選條件"),
        BotCommand("find",        "🔍 搜尋符合條件的房源"),
        BotCommand("list_latest", "📋 最新房源（套用篩選）"),
        BotCommand("list_all",    "📋 全部已爬到的房源"),
        BotCommand("wishlist",    "❤️ 查看願望清單"),
        BotCommand("blacklist",   "🚫 查看黑名單"),
        BotCommand("web_token",   "🔑 取得網頁登入 Token"),
        BotCommand("cleanup_db",  "🗑 清除不符合條件的房源"),
        BotCommand("reset",       "⚠️ 重置所有個人設定"),
    ])
    logger.info("Bot commands registered.")

    active_chats = await queries.get_all_active_monitors()
    if active_chats:
        start_global_job(application)
        logger.info("Restored global monitor for %d chat(s): %s", len(active_chats), active_chats)
    start_weekly_cleanup(application)


async def post_shutdown(application) -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await close_db()
    logger.info("Shutdown complete.")


def main() -> None:
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30)
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── Handler registration order matters ─────────────────────────────────────
    # ConversationHandler must be registered BEFORE the plain-text "開始"/"停止"
    # handlers so the conversation flow can intercept text messages in mid-flow.
    app.add_handler(preferences_conv_handler)

    app.add_handler(MessageHandler(filters.Regex("^開始$"), handle_start_monitoring))
    app.add_handler(MessageHandler(filters.Regex("^停止$"), handle_stop_monitoring))

    app.add_handler(CommandHandler("wishlist",    handle_wishlist))
    app.add_handler(CommandHandler("status",      handle_status))
    app.add_handler(CommandHandler("list_latest", handle_list_latest))
    app.add_handler(CommandHandler("blacklist",   handle_blacklist))
    app.add_handler(CommandHandler("reset",       handle_reset))
    app.add_handler(CommandHandler("find",        handle_find))
    app.add_handler(CommandHandler("list_all",    handle_list_all))
    app.add_handler(CommandHandler("cleanup_db",  handle_cleanup_db))
    app.add_handler(CommandHandler("web_token",   handle_web_token))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    logger.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
