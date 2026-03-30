import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import db.queries as queries
from db.models import UserPreference
from bot.keyboards import (
    skip_keyboard,
    yes_no_skip_keyboard,
    layout_keyboard,
    district_keyboard,
)
from config import ALL_DISTRICTS, TAIPEI_DISTRICTS, NEW_TAIPEI_DISTRICTS

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
(
    RENT_MIN,
    RENT_MAX,
    DISTRICTS,
    LAYOUT,
    PETS,
    PARKING,
    ELEVATOR,
    KEYWORDS_INC,
    KEYWORDS_EXC,
) = range(9)

_PREF_KEY = "pref_draft"  # key in context.user_data


def _draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if _PREF_KEY not in context.user_data:
        context.user_data[_PREF_KEY] = {}
    return context.user_data[_PREF_KEY]


# ── Entry ──────────────────────────────────────────────────────────────────────

async def start_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[_PREF_KEY] = {}
    await update.message.reply_text(
        "⚙️ <b>設定篩選條件</b>\n\n"
        "我會根據這些條件過濾租屋資訊，只推播符合的物件。\n"
        "每個步驟都可以點「略過」跳過。\n\n"
        "輸入 /cancel 可隨時取消。",
        parse_mode="HTML",
    )
    await update.message.reply_text(
        "💰 <b>最低租金</b>（元/月）？\n例如：8000",
        parse_mode="HTML",
        reply_markup=skip_keyboard("rent_min"),
    )
    return RENT_MIN


# ── RENT_MIN ──────────────────────────────────────────────────────────────────

async def receive_rent_min_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", "")
    if text.isdigit():
        _draft(context)["min_rent"] = int(text)
    return await _ask_rent_max(update, context)


async def receive_rent_min_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _ask_rent_max(update.callback_query, context)


async def _ask_rent_max(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    send = src.message.reply_text if hasattr(src, "message") else src.edit_message_text
    try:
        await send(
            "💰 <b>最高租金</b>（元/月）？\n例如：20000",
            parse_mode="HTML",
            reply_markup=skip_keyboard("rent_max"),
        )
    except Exception:
        pass
    return RENT_MAX


# ── RENT_MAX ──────────────────────────────────────────────────────────────────

async def receive_rent_max_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", "")
    if text.isdigit():
        _draft(context)["max_rent"] = int(text)
    return await _ask_districts(update, context)


async def receive_rent_max_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _ask_districts(update.callback_query, context)


# ── DISTRICTS ────────────────────────────────────────────────────────────────

async def _ask_districts(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    if "selected_districts" not in context.user_data:
        context.user_data["selected_districts"] = set()
    selected = context.user_data["selected_districts"]
    kb = district_keyboard(ALL_DISTRICTS, selected)
    try:
        if hasattr(src, "message"):
            await src.message.reply_text(
                "📍 <b>選擇偏好行政區</b>（可多選，不選則不限）：",
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            await src.edit_message_text(
                "📍 <b>選擇偏好行政區</b>（可多選，不選則不限）：",
                parse_mode="HTML",
                reply_markup=kb,
            )
    except Exception:
        pass
    return DISTRICTS


async def toggle_district(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    district = q.data.replace("district_", "")
    selected: set = context.user_data.setdefault("selected_districts", set())
    if district in selected:
        selected.discard(district)
    else:
        selected.add(district)
    kb = district_keyboard(ALL_DISTRICTS, selected)
    await q.edit_message_reply_markup(reply_markup=kb)
    return DISTRICTS


async def confirm_districts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    selected = context.user_data.get("selected_districts", set())
    _draft(context)["districts"] = list(selected) if selected else None
    return await _ask_layout(update.callback_query, context)


# ── LAYOUT ────────────────────────────────────────────────────────────────────

async def _ask_layout(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    if "selected_layouts" not in context.user_data:
        context.user_data["selected_layouts"] = set()
    selected = context.user_data["selected_layouts"]
    try:
        await src.edit_message_text(
            "🏢 <b>偏好格局</b>（可多選，不選則不限）：",
            parse_mode="HTML",
            reply_markup=layout_keyboard(selected),
        )
    except Exception:
        pass
    return LAYOUT


async def toggle_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    opt = q.data.replace("layout_", "")
    selected: set = context.user_data.setdefault("selected_layouts", set())
    if opt in selected:
        selected.discard(opt)
    else:
        selected.add(opt)
    await q.edit_message_reply_markup(reply_markup=layout_keyboard(selected))
    return LAYOUT


async def confirm_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    selected = context.user_data.get("selected_layouts", set())
    _draft(context)["layout_filter"] = list(selected) if selected else None
    context.user_data.pop("selected_layouts", None)
    return await _ask_pets(update.callback_query, context)


async def skip_layout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data.pop("selected_layouts", None)
    return await _ask_pets(update.callback_query, context)


# ── PETS ─────────────────────────────────────────────────────────────────────

async def _ask_pets(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await src.edit_message_text(
            "🐾 <b>需要可養寵物？</b>",
            parse_mode="HTML",
            reply_markup=yes_no_skip_keyboard("pets"),
        )
    except Exception:
        pass
    return PETS


async def receive_pets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if "yn_pets_yes" in q.data:
        _draft(context)["pets_required"] = True
    elif "yn_pets_no" in q.data:
        _draft(context)["pets_required"] = False
    return await _ask_parking(q, context)


# ── PARKING ───────────────────────────────────────────────────────────────────

async def _ask_parking(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await src.edit_message_text(
            "🚗 <b>需要停車位？</b>",
            parse_mode="HTML",
            reply_markup=yes_no_skip_keyboard("parking"),
        )
    except Exception:
        pass
    return PARKING


async def receive_parking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if "yn_parking_yes" in q.data:
        _draft(context)["parking_required"] = True
    elif "yn_parking_no" in q.data:
        _draft(context)["parking_required"] = False
    return await _ask_elevator(q, context)


# ── ELEVATOR ──────────────────────────────────────────────────────────────────

async def _ask_elevator(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await src.edit_message_text(
            "🛗 <b>需要電梯？</b>",
            parse_mode="HTML",
            reply_markup=yes_no_skip_keyboard("elevator"),
        )
    except Exception:
        pass
    return ELEVATOR


async def receive_elevator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if "yn_elevator_yes" in q.data:
        _draft(context)["elevator_required"] = True
    elif "yn_elevator_no" in q.data:
        _draft(context)["elevator_required"] = False
    return await _ask_keywords_inc(q, context)


# ── KEYWORDS_INC ──────────────────────────────────────────────────────────────

async def _ask_keywords_inc(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await src.edit_message_text(
            "🔍 <b>必須包含的關鍵字</b>（用空格分隔，例如：近捷運 全新裝潢）\n"
            "若不設定請點略過。",
            parse_mode="HTML",
            reply_markup=skip_keyboard("keywords_inc"),
        )
    except Exception:
        pass
    return KEYWORDS_INC


async def receive_keywords_inc_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kws = [k.strip() for k in update.message.text.split() if k.strip()]
    if kws:
        _draft(context)["keywords_include"] = kws
    return await _ask_keywords_exc(update, context)


async def receive_keywords_inc_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _ask_keywords_exc(update.callback_query, context)


# ── KEYWORDS_EXC ──────────────────────────────────────────────────────────────

async def _ask_keywords_exc(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        if hasattr(src, "message"):
            await src.message.reply_text(
                "🚫 <b>排除的關鍵字</b>（用空格分隔，例如：頂加 隔套）\n"
                "若不設定請點略過。",
                parse_mode="HTML",
                reply_markup=skip_keyboard("keywords_exc"),
            )
        else:
            await src.edit_message_text(
                "🚫 <b>排除的關鍵字</b>（用空格分隔，例如：頂加 隔套）\n"
                "若不設定請點略過。",
                parse_mode="HTML",
                reply_markup=skip_keyboard("keywords_exc"),
            )
    except Exception:
        pass
    return KEYWORDS_EXC


async def receive_keywords_exc_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kws = [k.strip() for k in update.message.text.split() if k.strip()]
    if kws:
        _draft(context)["keywords_exclude"] = kws
    return await _finish(update, context)


async def receive_keywords_exc_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await _finish(update.callback_query, context)


# ── FINISH ────────────────────────────────────────────────────────────────────

async def _finish(src, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = src.effective_chat.id if hasattr(src, "effective_chat") else src.message.chat_id
    draft = _draft(context)

    pref = UserPreference(
        chat_id=chat_id,
        min_rent=draft.get("min_rent"),
        max_rent=draft.get("max_rent"),
        districts=draft.get("districts"),
        layout_filter=draft.get("layout_filter"),
        pets_required=draft.get("pets_required"),
        parking_required=draft.get("parking_required"),
        elevator_required=draft.get("elevator_required"),
        keywords_include=draft.get("keywords_include"),
        keywords_exclude=draft.get("keywords_exclude"),
    )
    await queries.save_preference(pref)

    # Summary
    lines = ["✅ <b>篩選條件已儲存！</b>\n"]
    if pref.min_rent:
        lines.append(f"最低租金：{pref.min_rent:,} 元")
    if pref.max_rent:
        lines.append(f"最高租金：{pref.max_rent:,} 元")
    if pref.districts:
        lines.append(f"地區：{', '.join(pref.districts)}")
    if pref.layout_filter:
        lines.append(f"格局：{', '.join(pref.layout_filter)}")
    if pref.pets_required:
        lines.append("需可養寵物：是")
    if pref.parking_required:
        lines.append("需停車位：是")
    if pref.elevator_required:
        lines.append("需電梯：是")
    if pref.keywords_include:
        lines.append(f"必含關鍵字：{', '.join(pref.keywords_include)}")
    if pref.keywords_exclude:
        lines.append(f"排除關鍵字：{', '.join(pref.keywords_exclude)}")

    lines.append("\n輸入「開始」啟動監控！")
    msg = "\n".join(lines)

    try:
        if hasattr(src, "message"):
            await src.message.reply_html(msg)
        else:
            await src.edit_message_text(msg, parse_mode="HTML")
    except Exception:
        pass

    context.user_data.pop(_PREF_KEY, None)
    context.user_data.pop("selected_districts", None)
    context.user_data.pop("selected_layouts", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_PREF_KEY, None)
    context.user_data.pop("selected_districts", None)
    context.user_data.pop("selected_layouts", None)
    await update.message.reply_text("已取消設定。")
    return ConversationHandler.END


# ── Build the ConversationHandler ─────────────────────────────────────────────

preferences_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("preferences", start_preferences)],
    states={
        RENT_MIN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_rent_min_text),
            CallbackQueryHandler(receive_rent_min_skip, pattern="^skip_rent_min$"),
        ],
        RENT_MAX: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_rent_max_text),
            CallbackQueryHandler(receive_rent_max_skip, pattern="^skip_rent_max$"),
        ],
        DISTRICTS: [
            CallbackQueryHandler(toggle_district, pattern="^district_"),
            CallbackQueryHandler(confirm_districts, pattern="^districts_confirm$"),
        ],
        LAYOUT: [
            CallbackQueryHandler(confirm_layout, pattern="^layout_confirm$"),
            CallbackQueryHandler(toggle_layout,  pattern="^layout_"),
            CallbackQueryHandler(skip_layout,    pattern="^skip_layout$"),
        ],
        PETS: [
            CallbackQueryHandler(receive_pets, pattern="^yn_pets_"),
            CallbackQueryHandler(receive_pets, pattern="^skip_pets$"),
        ],
        PARKING: [
            CallbackQueryHandler(receive_parking, pattern="^yn_parking_"),
            CallbackQueryHandler(receive_parking, pattern="^skip_parking$"),
        ],
        ELEVATOR: [
            CallbackQueryHandler(receive_elevator, pattern="^yn_elevator_"),
            CallbackQueryHandler(receive_elevator, pattern="^skip_elevator$"),
        ],
        KEYWORDS_INC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_keywords_inc_text),
            CallbackQueryHandler(receive_keywords_inc_skip, pattern="^skip_keywords_inc$"),
        ],
        KEYWORDS_EXC: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_keywords_exc_text),
            CallbackQueryHandler(receive_keywords_exc_skip, pattern="^skip_keywords_exc$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True,
    per_message=False,
)
