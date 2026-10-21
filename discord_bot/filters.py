"""Parse /filters slash-command input and render a channel's filters as text.

No discord.py imports here so the parsing rules are easy to unit-test.
"""
import re

from bot.keyboards import LAYOUT_OPTIONS
from config import ALL_DISTRICTS
from db.models import UserPreference

_SPLIT_RE = re.compile(r"[,，、;\s]+")
# Typing one of these into a text option clears that filter.
CLEAR_TOKENS = {"-", "無", "清除", "不限", "none"}


def split_list(text: str) -> list[str]:
    seen: list[str] = []
    for token in _SPLIT_RE.split(text.strip()):
        if token and token not in seen:
            seen.append(token)
    return seen


def is_clear(text: str) -> bool:
    return text.strip().lower() in CLEAR_TOKENS


def parse_districts(text: str) -> list[str]:
    """'大安, 信義區' → ['大安區', '信義區']. Raises ValueError on unknown names."""
    result, unknown = [], []
    for token in split_list(text):
        name = token if token.endswith("區") else token + "區"
        if name in ALL_DISTRICTS:
            if name not in result:
                result.append(name)
        else:
            unknown.append(token)
    if unknown:
        raise ValueError(f"不認識的行政區：{'、'.join(unknown)}")
    return result


def _normalize_layout(token: str) -> str | None:
    if token in LAYOUT_OPTIONS:
        return token
    if "套" in token:
        return "套房"
    m = re.match(r"^(\d+)房?\+?$", token)
    if m:
        rooms = int(m.group(1))
        if rooms >= 3:
            return "3房+"
        if rooms >= 1:
            return f"{rooms}房"
    return None


def parse_layouts(text: str) -> list[str]:
    """'套房, 2, 4房' → ['套房', '2房', '3房+']. Raises ValueError on unknown names."""
    result, unknown = [], []
    for token in split_list(text):
        layout = _normalize_layout(token)
        if layout is None:
            unknown.append(token)
        elif layout not in result:
            result.append(layout)
    if unknown:
        raise ValueError(
            f"不認識的格局：{'、'.join(unknown)}（可用：{'、'.join(LAYOUT_OPTIONS)}）"
        )
    return result


def apply_changes(
    prefs: UserPreference,
    *,
    min_rent: int | None = None,
    max_rent: int | None = None,
    min_size: float | None = None,
    districts: str | None = None,
    layouts: str | None = None,
    pets: bool | None = None,
    parking: bool | None = None,
    elevator: bool | None = None,
    include: str | None = None,
    exclude: str | None = None,
) -> UserPreference:
    """Apply only the options the user filled in; leave the rest untouched.

    Numbers: 0 clears. Text: a CLEAR_TOKENS value clears. Yes/no: False clears.
    Raises ValueError with a user-facing message on bad input.
    """
    if min_rent is not None:
        prefs.min_rent = min_rent or None
    if max_rent is not None:
        prefs.max_rent = max_rent or None
    if min_size is not None:
        prefs.min_size = min_size or None
    if districts is not None:
        prefs.districts = None if is_clear(districts) else parse_districts(districts) or None
    if layouts is not None:
        prefs.layout_filter = None if is_clear(layouts) else parse_layouts(layouts) or None
    if pets is not None:
        prefs.pets_required = pets or None
    if parking is not None:
        prefs.parking_required = parking or None
    if elevator is not None:
        prefs.elevator_required = elevator or None
    if include is not None:
        prefs.keywords_include = None if is_clear(include) else split_list(include) or None
    if exclude is not None:
        prefs.keywords_exclude = None if is_clear(exclude) else split_list(exclude) or None

    if prefs.min_rent and prefs.max_rent and prefs.min_rent > prefs.max_rent:
        raise ValueError(f"最低租金 {prefs.min_rent:,} 不能高於最高租金 {prefs.max_rent:,}")
    return prefs


def format_prefs(prefs: UserPreference | None) -> str:
    if prefs is None:
        prefs = UserPreference(chat_id=0)

    if prefs.min_rent and prefs.max_rent:
        rent = f"{prefs.min_rent:,} ~ {prefs.max_rent:,} 元"
    elif prefs.min_rent:
        rent = f"{prefs.min_rent:,} 元以上"
    elif prefs.max_rent:
        rent = f"{prefs.max_rent:,} 元以下"
    else:
        rent = "不限"

    required = [
        name for name, on in (
            ("可養寵物", prefs.pets_required),
            ("有車位", prefs.parking_required),
            ("有電梯", prefs.elevator_required),
        ) if on
    ]

    lines = [
        f"💰 租金：{rent}",
        f"📐 坪數：{f'{prefs.min_size:g} 坪以上' if prefs.min_size else '不限'}",
        f"📍 行政區：{'、'.join(prefs.districts) if prefs.districts else '不限'}",
        f"🛏 格局：{'、'.join(prefs.layout_filter) if prefs.layout_filter else '不限'}",
        f"✅ 必須：{'、'.join(required) if required else '無'}",
        f"🔎 包含關鍵字：{'、'.join(prefs.keywords_include) if prefs.keywords_include else '無'}",
        f"🚫 排除關鍵字：{'、'.join(prefs.keywords_exclude) if prefs.keywords_exclude else '無'}",
    ]
    return "\n".join(lines)


def complete_list(current: str, options: list[str], limit: int = 25) -> list[str]:
    """Autocomplete the last item of a comma-separated list.

    '大安區,信' → ['大安區,信義區']
    """
    parts = re.split(r"[,，、]", current)
    head = [p.strip() for p in parts[:-1] if p.strip()]
    last = parts[-1].strip()
    prefix = ",".join(head) + ("," if head else "")
    out = []
    for opt in options:
        if last in opt and opt not in head:
            value = prefix + opt
            if len(value) <= 100:   # Discord choice length limit
                out.append(value)
        if len(out) >= limit:
            break
    return out
