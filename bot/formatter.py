from db.models import Listing


def format_listing(listing: Listing) -> str:
    source_badge = "🏠 591" if listing.source == "591" else f"🏡 {listing.source}"
    lines = [f"{source_badge}  <b>{_esc(listing.title)}</b>", ""]

    # Location
    area_str = " ".join(filter(None, [listing.area, listing.district]))
    if area_str:
        lines.append(f"📍 <b>地區：</b>{_esc(area_str)}")
    if listing.address:
        lines.append(f"　　{_esc(listing.address)}")

    # Rent
    if listing.rent:
        lines.append(f"💰 <b>租金：</b>{listing.rent:,} 元/月")

    # Core details
    detail_parts = []
    if listing.layout:
        detail_parts.append(f"格局：{listing.layout}")
    if listing.size_ping:
        detail_parts.append(f"坪數：{listing.size_ping:.1f}坪")
    if listing.floor:
        if listing.floor_total:
            detail_parts.append(f"樓層：{listing.floor}/{listing.floor_total}樓")
        else:
            detail_parts.append(f"樓層：{listing.floor}樓")
    if detail_parts:
        lines.append("🏢 " + "　".join(detail_parts))

    # Compact features (only non-trivial info)
    features = []
    if listing.elevator is True:
        features.append("電梯✓")
    if listing.trash is True:
        features.append("垃圾代收✓")
    if listing.parking:
        features.append(f"停車:{listing.parking}")
    if listing.pets:
        features.append(f"寵物:{listing.pets}")
    if listing.gender and listing.gender != "不限":
        features.append(f"性別:{listing.gender}")
    if features:
        lines.append("✨ " + "　".join(features))

    # Upload timestamp
    if listing.first_seen_at:
        date_str = listing.first_seen_at.strftime("%Y-%m-%d")
        lines.append(f"🕐 {date_str}")

    lines.append("")
    lines.append(f'🔗 <a href="{listing.url}">查看詳情</a>')
    return "\n".join(lines)


def format_listing_detail(listing: Listing) -> str:
    """Extended format showing all available fields."""
    base = format_listing(listing)
    extra_lines = []
    if listing.deposit:
        extra_lines.append(f"📋 <b>押金：</b>{_esc(listing.deposit)}")
    if listing.utilities:
        extra_lines.append(f"⚡ <b>水電：</b>{_esc(listing.utilities)}")
    if listing.kitchen:
        extra_lines.append(f"🍳 廚房：{_esc(listing.kitchen)}")
    if listing.extra_info:
        for k, v in listing.extra_info.items():
            if v and k not in ("id", "cover", "is_hide", "status"):
                extra_lines.append(f"  {k}: {v}")
    if extra_lines:
        base += "\n\n<b>詳細資訊：</b>\n" + "\n".join(extra_lines)
    return base


def format_wishlist_empty() -> str:
    return "你的願望清單是空的。\n找到喜歡的房子時，點選 ❤️ 加入願望清單 即可儲存。"


def _esc(text: str) -> str:
    """Escape HTML special chars for Telegram HTML parse mode."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
