"""Pure parsing for Facebook rental-group posts.

Two stages, both side-effect free so they can be unit-tested without a browser:

  1. extract_posts(payload)  — walk FB's GraphQL / inline-script JSON and pull
                               out RawPost objects (id, text, images, time).
  2. parse_post(raw)         — turn a RawPost into a Listing with best-effort
                               rent / district / layout / size / floor fields,
                               or None if it is not a rental offer.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from config import ALL_DISTRICTS
from db.models import Listing

SOURCE_NAME = "facebook"

_DESC_MAX_CHARS = 5000
_TITLE_MAX_CHARS = 60
_MAX_IMAGES = 10
_MIN_IMAGE_EDGE = 200


@dataclass
class RawPost:
    post_id: str
    group: str
    text: str = ""
    images: list[str] = field(default_factory=list)
    url: Optional[str] = None
    created_at: Optional[datetime] = None
    author: Optional[str] = None


# ── Stage 1: JSON → RawPost ───────────────────────────────────────────────────

def iter_json_documents(body: str) -> Iterator[Any]:
    """FB responses may be prefixed with `for (;;);` and contain several JSON
    documents separated by newlines. Yield each one that parses."""
    body = body.strip()
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    try:
        yield json.loads(body)
        return
    except ValueError:
        pass
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _walk(obj: Any) -> Iterator[dict]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def _find_message_text(story: dict) -> str:
    for d in _walk(story):
        msg = d.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("text"), str) and msg["text"].strip():
            return msg["text"]
    return ""


def _find_creation_time(story: dict) -> Optional[datetime]:
    for d in _walk(story):
        ts = d.get("creation_time")
        if isinstance(ts, int) and ts > 1_000_000_000:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _find_author(story: dict) -> Optional[str]:
    for d in _walk(story):
        actors = d.get("actors")
        if isinstance(actors, list) and actors and isinstance(actors[0], dict):
            name = actors[0].get("name")
            if isinstance(name, str):
                return name
    return None


def _find_images(story: dict) -> list[str]:
    """Collect photo URLs from the story's attachments only (so avatars and
    profile pictures elsewhere in the payload are ignored)."""
    images: list[str] = []
    for d in _walk(story):
        att = d.get("attachments")
        if not isinstance(att, list):
            continue
        for node in _walk(att):
            for key in ("photo_image", "image", "viewer_image"):
                img = node.get(key)
                if not isinstance(img, dict):
                    continue
                uri = img.get("uri")
                if not isinstance(uri, str) or "scontent" not in uri:
                    continue
                w, h = img.get("width"), img.get("height")
                if isinstance(w, int) and isinstance(h, int) and min(w, h) < _MIN_IMAGE_EDGE:
                    continue
                if uri not in images:
                    images.append(uri)
        if images:
            break
    return images[:_MAX_IMAGES]


def _find_url(story: dict, post_id: str) -> Optional[str]:
    for d in _walk(story):
        for key in ("url", "permalink_url", "wwwURL"):
            u = d.get(key)
            if isinstance(u, str) and post_id in u and "facebook.com" in u:
                return u
    return None


def extract_posts(payload: Any, group: str) -> list[RawPost]:
    """Find every story-like node (a dict with a numeric `post_id` and a
    message) anywhere in the payload. Duplicates are merged, keeping the
    richest text and the union of images."""
    found: dict[str, RawPost] = {}
    for d in _walk(payload):
        pid = d.get("post_id")
        if not isinstance(pid, str) or not pid.isdigit():
            continue
        text = _find_message_text(d)
        if not text:
            continue
        post = RawPost(
            post_id=pid,
            group=group,
            text=text,
            images=_find_images(d),
            url=_find_url(d, pid),
            created_at=_find_creation_time(d),
            author=_find_author(d),
        )
        prev = found.get(pid)
        if prev is None:
            found[pid] = post
            continue
        if len(post.text) > len(prev.text):
            prev.text = post.text
        prev.images.extend(u for u in post.images if u not in prev.images)
        prev.url = prev.url or post.url
        prev.created_at = prev.created_at or post.created_at
        prev.author = prev.author or post.author
    return list(found.values())


def extract_posts_from_bodies(bodies: Iterable[str], group: str) -> list[RawPost]:
    merged: dict[str, RawPost] = {}
    for body in bodies:
        for doc in iter_json_documents(body):
            for post in extract_posts(doc, group):
                prev = merged.get(post.post_id)
                if prev is None or len(post.text) > len(prev.text):
                    if prev is not None and not post.images:
                        post.images = prev.images
                    merged[post.post_id] = post
    return list(merged.values())


# ── Stage 2: RawPost → Listing ────────────────────────────────────────────────

# Posts from people *looking* for a place, not offering one.
_WANTED_RE = re.compile(r"徵求|求租|徵租|徵室友|找房|想租|誠徵|尋租")
_OFFER_RE = re.compile(r"出租|租金|月租|招租|分租|轉租|房租")

_NUM = r"(\d{1,3}(?:[,，]\d{3})+|\d{4,6})"
_RENT_STRONG_RE = [
    re.compile(r"(?:租金|月租|房租|租)\s*[:：]?\s*(?:NT\$?|＄|\$)?\s*" + _NUM + r"(?!\d)"),
    re.compile(r"(?<!\d)" + _NUM + r"\s*(?:元|塊)?\s*[/／]\s*月"),
    re.compile(r"(?<!\d)" + _NUM + r"\s*(?:元|塊)\s*(?:每月|一個月)"),
]
_RENT_WAN_RE = re.compile(r"(?:租金|月租|房租|租)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*萬\s*(\d)?(?!\d)")
_RENT_WEAK_RE = re.compile(r"(?<![\d押金])(?:NT\$?|＄|\$)?\s*" + _NUM + r"\s*(?:元|塊)(?!\d)")

_RENT_MIN, _RENT_MAX = 2_000, 200_000

_PING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*坪")
_ROOMS_RE = re.compile(r"([1-9一二三四五])\s*房")
_SUITE_RE = re.compile(r"套房")
_SHARED_RE = re.compile(r"雅房")
_FLOOR_PAIR_RE = re.compile(r"(\d{1,2})\s*[樓F]?\s*[/／]\s*(\d{1,2})\s*[樓F]")
_FLOOR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[樓F](?![a-zA-Z])")

# Amenities. Negations are checked first so "不可養寵物" / "無電梯" don't count.
_PET_WORD = r"(?:寵物?|貓|狗|毛孩|犬)"
_PETS_NO_RE = re.compile(
    rf"(?:不可|不能|不得|禁止|禁養|謝絕|婉拒|拒絕|不接受|無法)[^\n，,。；;]{{0,4}}{_PET_WORD}"
    rf"|寵物[^\n，,。；;]{{0,3}}(?:不可|不行|禁止|謝絕|勿|NO|No|no)"
)
_PETS_YES_RE = re.compile(
    rf"(?:可|能|歡迎|接受|允許)[^\n，,。；;]{{0,4}}{_PET_WORD}"
    rf"|寵物[^\n，,。；;]{{0,3}}(?:可|友善|OK|ok|歡迎)"
)
_ELEVATOR_NO_RE = re.compile(r"(?:無|沒有?|非|不含)電梯")
_ELEVATOR_YES_RE = re.compile(r"電梯")
# 機車位 is a motorcycle spot, not the parking people filter for.
_PARKING_NO_RE = re.compile(r"(?:無|沒有?|不含|不附)(?:汽車)?(?:停)?車位")
_PARKING_YES_RE = re.compile(r"(?<!機)(?:停車位|車位|車庫)")

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}

# Bare names ("大安" for "大安區"). Excluded when followed by 路/街 so that
# e.g. "信義路" in another district is not mistaken for 信義區.
_DISTRICT_BARE_RE = {
    d: re.compile(re.escape(d[:-1]) + r"(?![路街])") for d in ALL_DISTRICTS
}


def _to_int(raw: str) -> int:
    return int(raw.replace(",", "").replace("，", ""))


def extract_rent(text: str) -> int:
    """Rent in NTD/month, or 0 if nothing plausible was found."""
    for rx in _RENT_STRONG_RE:
        for m in rx.finditer(text):
            n = _to_int(m.group(1))
            if _RENT_MIN <= n <= _RENT_MAX:
                return n
    for m in _RENT_WAN_RE.finditer(text):
        n = int(float(m.group(1)) * 10_000 + (int(m.group(2)) * 1_000 if m.group(2) else 0))
        if _RENT_MIN <= n <= _RENT_MAX:
            return n
    for m in _RENT_WEAK_RE.finditer(text):
        n = _to_int(m.group(1))
        if _RENT_MIN <= n <= _RENT_MAX:
            return n
    return 0


def extract_district(text: str) -> Optional[str]:
    for d in ALL_DISTRICTS:
        if d in text:
            return d
    for d, rx in _DISTRICT_BARE_RE.items():
        if rx.search(text):
            return d
    return None


def extract_size_ping(text: str) -> Optional[float]:
    for m in _PING_RE.finditer(text):
        v = float(m.group(1))
        if 1 <= v <= 200:
            return v
    return None


def extract_layout(text: str) -> Optional[str]:
    """Normalised to the values the preference filter uses: 套房/1房/2房/3房+.
    雅房 is kept as-is (no matching filter option)."""
    m = _ROOMS_RE.search(text)
    if m:
        raw = m.group(1)
        n = _CN_DIGITS.get(raw) or int(raw)
        return "3房+" if n >= 3 else f"{n}房"
    if _SUITE_RE.search(text):
        return "套房"
    if _SHARED_RE.search(text):
        return "雅房"
    return None


def extract_floor(text: str) -> tuple[Optional[str], Optional[int]]:
    m = _FLOOR_PAIR_RE.search(text)
    if m:
        floor, total = int(m.group(1)), int(m.group(2))
        if 0 < floor <= total:
            return str(floor), total
    m = _FLOOR_RE.search(text)
    if m:
        return m.group(1), None
    return None, None


def _mentions(text: str, yes: re.Pattern, no: re.Pattern) -> bool:
    return not no.search(text) and bool(yes.search(text))


def extract_amenities(text: str) -> tuple[Optional[str], Optional[str], Optional[bool]]:
    """(pets, parking, elevator) in the same shape the 591 scraper uses."""
    return (
        "可" if _mentions(text, _PETS_YES_RE, _PETS_NO_RE) else None,
        "有" if _mentions(text, _PARKING_YES_RE, _PARKING_NO_RE) else None,
        True if _mentions(text, _ELEVATOR_YES_RE, _ELEVATOR_NO_RE) else None,
    )


def is_wanted_post(text: str) -> bool:
    head = text[:80]
    return bool(_WANTED_RE.search(head)) or "求租" in text


def _title_from(text: str) -> str:
    for line in text.splitlines():
        line = line.strip(" \t#*【】[]")
        if len(line) >= 4:
            return line[:_TITLE_MAX_CHARS]
    return "(FB貼文)"


def post_url(raw: RawPost) -> str:
    return raw.url or f"https://www.facebook.com/groups/{raw.group}/posts/{raw.post_id}/"


def parse_post(raw: RawPost) -> Optional[Listing]:
    text = (raw.text or "").strip()
    if len(text) < 20 or is_wanted_post(text):
        return None

    rent = extract_rent(text)
    if not rent and not _OFFER_RE.search(text):
        return None

    floor, floor_total = extract_floor(text)
    pets, parking, elevator = extract_amenities(text)
    return Listing(
        source=SOURCE_NAME,
        external_id=raw.post_id,
        url=post_url(raw),
        title=_title_from(text),
        area="",
        district=extract_district(text) or "",
        rent=rent,
        layout=extract_layout(text),
        size_ping=extract_size_ping(text),
        floor=floor,
        floor_total=floor_total,
        pets=pets,
        parking=parking,
        elevator=elevator,
        description=text[:_DESC_MAX_CHARS],
        images=list(raw.images),
        thumbnail_url=raw.images[0] if raw.images else None,
        extra_info={
            "fb_group": raw.group,
            "fb_post_id": raw.post_id,
            "author": raw.author,
            "posted_at": raw.created_at.isoformat() if raw.created_at else None,
        },
    )
