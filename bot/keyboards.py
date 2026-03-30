from telegram import InlineKeyboardButton, InlineKeyboardMarkup

LAYOUT_OPTIONS = ["套房", "1房", "2房", "3房+"]


def listing_keyboard(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❤️ 加入願望清單", callback_data=f"wl_add_{listing_id}"),
            InlineKeyboardButton("🔍 完整資訊",     callback_data=f"detail_{listing_id}"),
        ],
        [
            InlineKeyboardButton("🚫 不再顯示",      callback_data=f"bl_add_{listing_id}"),
        ],
    ])


def listing_keyboard_wishlisted(listing_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown after user adds listing to wishlist."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💔 移出願望清單", callback_data=f"wl_remove_inline_{listing_id}"),
            InlineKeyboardButton("🔍 完整資訊",     callback_data=f"detail_{listing_id}"),
        ],
        [
            InlineKeyboardButton("🚫 不再顯示",      callback_data=f"bl_add_{listing_id}"),
        ],
    ])


def wishlist_item_keyboard(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 移除", callback_data=f"wl_remove_{listing_id}"),
        InlineKeyboardButton("🔗 查看原頁", callback_data=f"wl_view_{listing_id}"),
    ]])


def wishlist_header_keyboard(unavailable_count: int) -> InlineKeyboardMarkup | None:
    """Returns a clear-unavailable button keyboard, or None if count is 0."""
    if unavailable_count <= 0:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🗑 清除已下架 ({unavailable_count})",
            callback_data="wl_clear_unavailable",
        )
    ]])


def blacklist_item_keyboard(source: str, external_id: str) -> InlineKeyboardMarkup:
    """Keyboard for each item in the blacklist view."""
    # Use | as separator; parse with split("|", 1)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔓 解除封鎖", callback_data=f"bl_remove|{source}|{external_id}"),
    ]])


def district_keyboard(available: list[str], selected: set[str]) -> InlineKeyboardMarkup:
    buttons = []
    for d in available:
        label = f"✅ {d}" if d in selected else d
        buttons.append(InlineKeyboardButton(label, callback_data=f"district_{d}"))
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("✔️ 確認選擇", callback_data="districts_confirm")])
    return InlineKeyboardMarkup(rows)


def layout_keyboard(selected: set[str] | None = None) -> InlineKeyboardMarkup:
    """Multi-select layout keyboard."""
    if selected is None:
        selected = set()
    buttons = []
    for opt in LAYOUT_OPTIONS:
        label = f"✅ {opt}" if opt in selected else opt
        buttons.append(InlineKeyboardButton(label, callback_data=f"layout_{opt}"))
    return InlineKeyboardMarkup([
        buttons,
        [InlineKeyboardButton("✔️ 確認選擇", callback_data="layout_confirm")],
        [InlineKeyboardButton("略過", callback_data="skip_layout")],
    ])


def skip_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("略過", callback_data=f"skip_{field}")
    ]])


def yes_no_skip_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("是", callback_data=f"yn_{field}_yes"),
        InlineKeyboardButton("否", callback_data=f"yn_{field}_no"),
        InlineKeyboardButton("略過", callback_data=f"skip_{field}"),
    ]])


def pagination_keyboard(next_offset: int, total: int) -> InlineKeyboardMarkup:
    end = min(next_offset + 10, total)
    label = f"▶ 顯示更多 (第 {next_offset + 1}-{end} 筆，共 {total} 筆)"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"list_more_{next_offset}"),
    ]])


def reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 確認重置", callback_data="reset_confirm"),
        InlineKeyboardButton("❌ 取消",     callback_data="reset_cancel"),
    ]])
