from db.models import Listing

# Telegram caption hard limit is 1024 chars; leave headroom for HTML tags & other lines.
_DESC_PREVIEW_CHARS = 600


def _source_badge(source: str) -> str:
    if source == "591":
        return "🏠 591"
    if source == "facebook":
        return "📘 FB社團"
    return f"🏡 {source}"


def format_listing(listing: Listing) -> str:
    headline = listing.title or (listing.description[:30] + "…" if listing.description else "(無標題)")
    lines = [f"{_source_badge(listing.source)}  <b>{_esc(headline)}</b>", ""]

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

    lines.append(f'🔗 <a href="{listing.url}">查看詳情</a>')

    # Free-form post body (e.g. group posts). Truncated for Telegram caption limit.
    if listing.description:
        body = listing.description.strip()
        if len(body) > _DESC_PREVIEW_CHARS:
            body = body[:_DESC_PREVIEW_CHARS].rstrip() + "…"
        lines.append("")
        lines.append(f"📝 {_esc(body)}")

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
