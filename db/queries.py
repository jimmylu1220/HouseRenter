import json
from datetime import datetime, timezone
from typing import Optional
import asyncpg

from db.models import Listing, WishlistItem, UserPreference, DiscordChannel
from db.database import get_pool


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Listings ────────────────────────────────────────────────────────────────

async def upsert_listing(listing: Listing) -> tuple[int, bool]:
    """Insert or update a listing. Returns (listing_id, is_new)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM listings WHERE source=$1 AND external_id=$2",
            listing.source, listing.external_id,
        )
        if existing:
            await conn.execute(
                "UPDATE listings SET last_seen_at=$1, is_available=TRUE WHERE id=$2",
                _now(), existing["id"],
            )
            return existing["id"], False

        row = await conn.fetchrow(
            """
            INSERT INTO listings
                (source, external_id, url, title, area, district, address,
                 rent, layout, size_ping, floor, floor_total, deposit,
                 utilities, trash, kitchen, parking, pets, elevator,
                 gender, extra_info, thumbnail_url, description, images,
                 first_seen_at, last_seen_at, is_available)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,TRUE)
            RETURNING id
            """,
            listing.source, listing.external_id, listing.url, listing.title,
            listing.area, listing.district, listing.address,
            listing.rent, listing.layout, listing.size_ping,
            listing.floor, listing.floor_total, listing.deposit,
            listing.utilities, listing.trash, listing.kitchen,
            listing.parking, listing.pets, listing.elevator,
            listing.gender,
            json.dumps(listing.extra_info) if listing.extra_info else None,
            listing.thumbnail_url,
            listing.description,
            json.dumps(listing.images) if listing.images else None,
            _now(), _now(),
        )
        return row["id"], True


async def update_listing_images(listing_id: int, images: list) -> None:
    """Replace the listing's `images` JSONB column.

    Used by bot/sender.py to cache Telegram file_ids alongside the original
    URLs after the first successful send_media_group/send_photo, so that
    subsequent deliveries can reuse the file_id and skip a download.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE listings SET images=$1::jsonb WHERE id=$2",
            json.dumps(images), listing_id,
        )


async def mark_listing_unavailable(source: str, external_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE listings SET is_available=FALSE WHERE source=$1 AND external_id=$2",
            source, external_id,
        )


async def get_listing_by_id(listing_id: int) -> Optional[Listing]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM listings WHERE id=$1", listing_id)
    return _row_to_listing(row) if row else None


def _row_to_listing(row) -> Listing:
    extra = row["extra_info"]
    if isinstance(extra, str):
        extra = json.loads(extra)
    images = row["images"]
    if isinstance(images, str):
        images = json.loads(images)
    return Listing(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        url=row["url"],
        title=row["title"],
        area=row["area"],
        district=row["district"],
        address=row["address"],
        rent=row["rent"],
        layout=row["layout"],
        size_ping=row["size_ping"],
        floor=row["floor"],
        floor_total=row["floor_total"],
        deposit=row["deposit"],
        utilities=row["utilities"],
        trash=row["trash"],
        kitchen=row["kitchen"],
        parking=row["parking"],
        pets=row["pets"],
        elevator=row["elevator"],
        gender=row["gender"],
        extra_info=extra,
        thumbnail_url=row["thumbnail_url"],
        description=row["description"],
        images=images,
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        is_available=row["is_available"],
    )


# ─── Wishlist ─────────────────────────────────────────────────────────────────

async def add_to_wishlist(chat_id: int, listing_id: int) -> bool:
    """Returns True if added, False if already in wishlist."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO wishlist (chat_id, listing_id, added_at) VALUES ($1,$2,$3)",
                chat_id, listing_id, _now(),
            )
            return True
        except asyncpg.UniqueViolationError:
            return False


async def remove_from_wishlist(chat_id: int, listing_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM wishlist WHERE chat_id=$1 AND listing_id=$2",
            chat_id, listing_id,
        )


async def get_wishlist(chat_id: int) -> list[tuple[WishlistItem, Listing]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.chat_id, w.listing_id, w.added_at, w.notified_rented,
                   l.*
            FROM wishlist w
            JOIN listings l ON l.id = w.listing_id
            WHERE w.chat_id = $1
            ORDER BY w.added_at DESC
            """,
            chat_id,
        )
    result = []
    for row in rows:
        item = WishlistItem(
            id=row["id"],
            chat_id=row["chat_id"],
            listing_id=row["listing_id"],
            added_at=row["added_at"],
            notified_rented=row["notified_rented"],
        )
        listing = _row_to_listing(row)
        result.append((item, listing))
    return result


async def get_all_wishlist_available() -> list[tuple[WishlistItem, Listing]]:
    """All wishlist items where the listing is still marked available."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.chat_id, w.listing_id, w.added_at, w.notified_rented,
                   l.*
            FROM wishlist w
            JOIN listings l ON l.id = w.listing_id
            WHERE l.is_available = TRUE
            ORDER BY w.chat_id
            """,
        )
    result = []
    for row in rows:
        item = WishlistItem(
            id=row["id"],
            chat_id=row["chat_id"],
            listing_id=row["listing_id"],
            added_at=row["added_at"],
            notified_rented=row["notified_rented"],
        )
        listing = _row_to_listing(row)
        result.append((item, listing))
    return result


async def mark_wishlist_notified(wishlist_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE wishlist SET notified_rented=TRUE WHERE id=$1",
            wishlist_id,
        )


# ─── Preferences ─────────────────────────────────────────────────────────────

async def save_preference(pref: UserPreference) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_preferences
                (chat_id, min_rent, max_rent, regions, districts, min_size,
                 layout_filter, pets_required, parking_required, elevator_required,
                 keywords_include, keywords_exclude, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (chat_id) DO UPDATE SET
                min_rent          = EXCLUDED.min_rent,
                max_rent          = EXCLUDED.max_rent,
                regions           = EXCLUDED.regions,
                districts         = EXCLUDED.districts,
                min_size          = EXCLUDED.min_size,
                layout_filter     = EXCLUDED.layout_filter,
                pets_required     = EXCLUDED.pets_required,
                parking_required  = EXCLUDED.parking_required,
                elevator_required = EXCLUDED.elevator_required,
                keywords_include  = EXCLUDED.keywords_include,
                keywords_exclude  = EXCLUDED.keywords_exclude,
                updated_at        = EXCLUDED.updated_at
            """,
            pref.chat_id, pref.min_rent, pref.max_rent,
            json.dumps(pref.regions) if pref.regions else None,
            json.dumps(pref.districts) if pref.districts else None,
            pref.min_size,
            json.dumps(pref.layout_filter) if pref.layout_filter else None,
            pref.pets_required, pref.parking_required, pref.elevator_required,
            json.dumps(pref.keywords_include) if pref.keywords_include else None,
            json.dumps(pref.keywords_exclude) if pref.keywords_exclude else None,
            _now(),
        )


async def get_preference(chat_id: int) -> Optional[UserPreference]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM user_preferences WHERE chat_id=$1", chat_id
        )
    if not row:
        return None
    return _pref_from_row(row, row["chat_id"])


def _loads(v):
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else v


def _pref_from_row(row, chat_id: int) -> UserPreference:
    return UserPreference(
        chat_id=chat_id,
        min_rent=row["min_rent"],
        max_rent=row["max_rent"],
        regions=_loads(row.get("regions")),
        districts=_loads(row["districts"]),
        min_size=row["min_size"],
        layout_filter=_loads(row["layout_filter"]),
        pets_required=row["pets_required"],
        parking_required=row["parking_required"],
        elevator_required=row["elevator_required"],
        keywords_include=_loads(row["keywords_include"]),
        keywords_exclude=_loads(row["keywords_exclude"]),
        updated_at=row["updated_at"],
    )


# ─── Active Monitors ──────────────────────────────────────────────────────────

async def set_monitor_active(chat_id: int) -> None:
    """Insert or reset the monitor record. Always resets first_run_done=FALSE."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO active_monitors (chat_id, started_at, first_run_done)
            VALUES ($1, $2, FALSE)
            ON CONFLICT (chat_id) DO UPDATE
                SET started_at = EXCLUDED.started_at,
                    first_run_done = FALSE
            """,
            chat_id, _now(),
        )


async def set_first_run_done(chat_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE active_monitors SET first_run_done=TRUE WHERE chat_id=$1",
            chat_id,
        )


async def is_first_run_done(chat_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT first_run_done FROM active_monitors WHERE chat_id=$1", chat_id
        )
    return bool(row["first_run_done"]) if row else False


async def set_monitor_inactive(chat_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM active_monitors WHERE chat_id=$1", chat_id
        )


async def is_monitor_active(chat_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM active_monitors WHERE chat_id=$1", chat_id
        )
    return row is not None


async def get_all_active_monitors() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT chat_id FROM active_monitors")
    return [r["chat_id"] for r in rows]


async def mark_stale_listings_unavailable(source: str, seen_ids: set[str]) -> list[int]:
    """Mark listings of this source as unavailable if their external_id is NOT in seen_ids.
    Returns the list of listing IDs that were just marked unavailable."""
    if not seen_ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE listings
               SET is_available = FALSE
             WHERE source = $1
               AND is_available = TRUE
               AND external_id <> ALL($2::text[])
            RETURNING id
            """,
            source, list(seen_ids),
        )
    return [r["id"] for r in rows]


async def get_wishlist_items_for_listings(listing_ids: list[int]) -> list[tuple]:
    """Return (WishlistItem, Listing) pairs for the given listing IDs."""
    if not listing_ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.chat_id, w.listing_id, w.added_at, w.notified_rented,
                   l.*
            FROM wishlist w
            JOIN listings l ON l.id = w.listing_id
            WHERE w.listing_id = ANY($1::int[])
              AND w.notified_rented = FALSE
            """,
            listing_ids,
        )
    result = []
    for row in rows:
        item = WishlistItem(
            id=row["id"],
            chat_id=row["chat_id"],
            listing_id=row["listing_id"],
            added_at=row["added_at"],
            notified_rented=row["notified_rented"],
        )
        listing = _row_to_listing(row)
        result.append((item, listing))
    return result


async def get_listing_count() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM listings WHERE is_available=TRUE")
    return row["cnt"]

async def delete_old_listings(cutoff: "datetime") -> int:
    """Delete listings last seen before `cutoff` that are not in any wishlist.
    Returns the count of deleted rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM listings
            WHERE last_seen_at < $1
              AND id NOT IN (SELECT listing_id FROM wishlist)
            """,
            cutoff,
        )
    count = int(result.split()[-1])
    return count


async def get_all_listings() -> list[Listing]:
    """Return every listing in the DB (including unavailable)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM listings ORDER BY first_seen_at DESC")
    return [_row_to_listing(row) for row in rows]


async def get_recent_listings(limit: int = 10) -> list[Listing]:
    """從資料庫中取得最新的 N 筆有效房源"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM listings
            WHERE is_available = TRUE
            ORDER BY first_seen_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [_row_to_listing(row) for row in rows]


async def remove_unavailable_from_wishlist(chat_id: int) -> int:
    """Remove all wishlist entries for chat_id where the listing is unavailable.
    Returns the count of removed rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM wishlist
            USING listings
            WHERE wishlist.listing_id = listings.id
              AND wishlist.chat_id = $1
              AND listings.is_available = FALSE
            """,
            chat_id,
        )
    return int(result.split()[-1])


# ─── Blacklist ────────────────────────────────────────────────────────────────

async def add_to_blacklist(chat_id: int, source: str, external_id: str) -> bool:
    """Returns True if added, False if already blacklisted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO blacklist (chat_id, source, external_id, added_at) VALUES ($1,$2,$3,$4)",
                chat_id, source, external_id, _now(),
            )
            return True
        except asyncpg.UniqueViolationError:
            return False


async def remove_from_blacklist(chat_id: int, source: str, external_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM blacklist WHERE chat_id=$1 AND source=$2 AND external_id=$3",
            chat_id, source, external_id,
        )


async def get_blacklist(chat_id: int) -> list[dict]:
    """Returns list of dicts with keys: source, external_id, added_at."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT source, external_id, added_at FROM blacklist WHERE chat_id=$1 ORDER BY added_at DESC",
            chat_id,
        )
    return [dict(r) for r in rows]


async def get_blacklisted_set(chat_id: int) -> set[tuple[str, str]]:
    """Returns a set of (source, external_id) tuples for fast lookup."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT source, external_id FROM blacklist WHERE chat_id=$1",
            chat_id,
        )
    return {(r["source"], r["external_id"]) for r in rows}


async def clear_user_data(chat_id: int) -> None:
    """Delete all personal data for a chat_id (wishlist, blacklist, prefs, monitor, pagination)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM wishlist WHERE chat_id=$1", chat_id)
        await conn.execute("DELETE FROM blacklist WHERE chat_id=$1", chat_id)
        await conn.execute("DELETE FROM user_preferences WHERE chat_id=$1", chat_id)
        await conn.execute("DELETE FROM active_monitors WHERE chat_id=$1", chat_id)
        await conn.execute("DELETE FROM pagination_sessions WHERE chat_id=$1", chat_id)


# ─── Pagination Sessions ──────────────────────────────────────────────────────

async def save_pagination_session(chat_id: int, listing_ids: list[int]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pagination_sessions (chat_id, listing_ids, total, updated_at)
            VALUES ($1, $2::jsonb, $3, $4)
            ON CONFLICT (chat_id) DO UPDATE SET
                listing_ids = EXCLUDED.listing_ids,
                total       = EXCLUDED.total,
                updated_at  = EXCLUDED.updated_at
            """,
            chat_id, json.dumps(listing_ids), len(listing_ids), _now(),
        )


async def get_pagination_session(chat_id: int) -> list[int] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT listing_ids FROM pagination_sessions WHERE chat_id=$1",
            chat_id,
        )
    if not row:
        return None
    ids = row["listing_ids"]
    if isinstance(ids, str):
        ids = json.loads(ids)
    return ids


async def clear_pagination_session(chat_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pagination_sessions WHERE chat_id=$1", chat_id
        )


async def delete_listings_not_in_wishlist(ids_to_delete: list[int]) -> int:
    """Delete listings by ID, skipping any that are currently in any wishlist.
    Returns the count of deleted rows."""
    if not ids_to_delete:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM listings
            WHERE id = ANY($1::int[])
              AND id NOT IN (SELECT listing_id FROM wishlist)
            """,
            ids_to_delete,
        )
    return int(result.split()[-1])


# ─── Web API helpers ──────────────────────────────────────────────────────────

async def get_listings_for_web(
    chat_id: int,
    offset: int = 0,
    limit: int = 20,
    available_only: bool = True,
    districts: list[str] | None = None,
    sort: str = "newest",          # newest | rent_asc | rent_desc
) -> tuple[list[dict], int]:
    """Return (rows, total_count) for the web frontend.
    Each row is a dict with all listing fields plus:
      is_wishlisted: bool
      is_blacklisted: bool
    """
    pool = await get_pool()

    # Fixed param order — always the same so asyncpg can always infer types:
    #   $1 = chat_id (bigint)
    #   $2 = districts (text[]) — empty array means "no filter"
    #   $3 = available_only (bool)
    #   $4 = limit (int)
    #   $5 = offset (int)
    district_arr = list(districts) if districts else []

    order = {
        "rent_asc":  "l.rent ASC NULLS LAST, l.first_seen_at DESC",
        "rent_desc": "l.rent DESC NULLS LAST, l.first_seen_at DESC",
    }.get(sort, "l.first_seen_at DESC")

    async with pool.acquire() as conn:
        # COUNT uses only $1=district_arr, $2=available_only (no chat_id needed)
        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM listings l
            WHERE ($2 = FALSE OR l.is_available = TRUE)
              AND (cardinality($1::text[]) = 0 OR l.district = ANY($1::text[]))
            """,
            district_arr, available_only,
        )
        rows = await conn.fetch(
            f"""
            SELECT
                l.*,
                (w.listing_id IS NOT NULL) AS is_wishlisted,
                (b.external_id IS NOT NULL) AS is_blacklisted
            FROM listings l
            LEFT JOIN wishlist w
                ON w.listing_id = l.id AND w.chat_id = $1
            LEFT JOIN blacklist b
                ON b.source = l.source AND b.external_id = l.external_id AND b.chat_id = $1
            WHERE ($3 = FALSE OR l.is_available = TRUE)
              AND (cardinality($2::text[]) = 0 OR l.district = ANY($2::text[]))
            ORDER BY {order}
            LIMIT $4 OFFSET $5
            """,
            chat_id, district_arr, available_only, limit, offset,
        )
    result = []
    for row in rows:
        d = dict(row)
        extra = d.get("extra_info")
        if isinstance(extra, str):
            d["extra_info"] = json.loads(extra)
        imgs = d.get("images")
        if isinstance(imgs, str):
            d["images"] = json.loads(imgs)
        for ts_field in ("first_seen_at", "last_seen_at"):
            if ts_field in d and d[ts_field] is not None:
                d[ts_field] = d[ts_field].isoformat()
        result.append(d)
    return result, int(total)


async def get_wishlist_listings_for_web(chat_id: int) -> list[dict]:
    """Return full listing data for all wishlisted items of a chat_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT l.*, w.added_at AS wishlisted_at
            FROM wishlist w
            JOIN listings l ON l.id = w.listing_id
            WHERE w.chat_id = $1
            ORDER BY w.added_at DESC
            """,
            chat_id,
        )
    result = []
    for row in rows:
        d = dict(row)
        extra = d.get("extra_info")
        if isinstance(extra, str):
            d["extra_info"] = json.loads(extra)
        imgs = d.get("images")
        if isinstance(imgs, str):
            d["images"] = json.loads(imgs)
        for ts in ("first_seen_at", "last_seen_at", "wishlisted_at"):
            if d.get(ts) is not None:
                d[ts] = d[ts].isoformat()
        result.append(d)
    return result


async def get_blacklist_listings_for_web(chat_id: int) -> list[dict]:
    """Return full listing data for all blacklisted items of a chat_id.
    Items that no longer exist in listings table are returned with minimal info."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT l.*, b.added_at AS blacklisted_at
            FROM blacklist b
            LEFT JOIN listings l ON l.source = b.source AND l.external_id = b.external_id
            WHERE b.chat_id = $1
            ORDER BY b.added_at DESC
            """,
            chat_id,
        )
    result = []
    for row in rows:
        d = dict(row)
        extra = d.get("extra_info")
        if isinstance(extra, str):
            d["extra_info"] = json.loads(extra)
        imgs = d.get("images")
        if isinstance(imgs, str):
            d["images"] = json.loads(imgs)
        for ts in ("first_seen_at", "last_seen_at", "blacklisted_at"):
            if d.get(ts) is not None:
                d[ts] = d[ts].isoformat()
        result.append(d)
    return result


# ─── Web Sessions (OTP token auth) ───────────────────────────────────────────

async def create_web_session(chat_id: int) -> str:
    """Generate a new 24-hour web session token for chat_id. Returns the token."""
    import secrets
    from datetime import timedelta
    pool = await get_pool()
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=24)
    async with pool.acquire() as conn:
        # Revoke any existing token for this chat_id first
        await conn.execute("DELETE FROM web_sessions WHERE chat_id = $1", chat_id)
        await conn.execute(
            "INSERT INTO web_sessions (token, chat_id, expires_at) VALUES ($1, $2, $3)",
            token, chat_id, expires,
        )
    return token


async def resolve_web_token(token: str) -> int | None:
    """Validate a token and return its chat_id, or None if invalid/expired."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT chat_id FROM web_sessions WHERE token = $1 AND expires_at > NOW()",
            token,
        )
    return row["chat_id"] if row else None


async def revoke_web_token(token: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE token = $1", token)


async def get_distinct_districts() -> list[str]:
    """Return all distinct non-empty district values in the listings table."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT district FROM listings
            WHERE district IS NOT NULL AND district <> ''
              AND district !~ '^[0-9]+$'
            ORDER BY district
            """
        )
    return [r["district"] for r in rows]

# ─── Discord channels ────────────────────────────────────────────────────────

def _discord_channel_from_row(row) -> DiscordChannel:
    return DiscordChannel(
        channel_id=row["channel_id"],
        prefs=_pref_from_row(row, row["channel_id"]),
        guild_id=row["guild_id"],
        active=row["active"],
        first_run_done=row["first_run_done"],
        updated_at=row["updated_at"],
    )


async def get_discord_channel(channel_id: int) -> Optional[DiscordChannel]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM discord_channels WHERE channel_id=$1", channel_id
        )
    return _discord_channel_from_row(row) if row else None


async def get_active_discord_channels() -> list[DiscordChannel]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM discord_channels WHERE active")
    return [_discord_channel_from_row(r) for r in rows]


async def subscribe_discord_channel(channel_id: int, guild_id: int | None) -> None:
    """Activate a channel; its next scan rebuilds the baseline (first_run_done=FALSE)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO discord_channels (channel_id, guild_id, active, first_run_done, updated_at)
            VALUES ($1, $2, TRUE, FALSE, $3)
            ON CONFLICT (channel_id) DO UPDATE SET
                guild_id       = EXCLUDED.guild_id,
                active         = TRUE,
                first_run_done = FALSE,
                updated_at     = EXCLUDED.updated_at
            """,
            channel_id, guild_id, _now(),
        )


async def unsubscribe_discord_channel(channel_id: int) -> bool:
    """Deactivate a channel but keep its filters. Returns False if it wasn't active."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            "UPDATE discord_channels SET active=FALSE WHERE channel_id=$1 AND active",
            channel_id,
        )
    return status != "UPDATE 0"


async def save_discord_preference(channel_id: int, guild_id: int | None, pref: UserPreference) -> None:
    """Save a channel's filters. Resets first_run_done so changed filters don't
    flood the channel with old listings that only now match."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO discord_channels
                (channel_id, guild_id, min_rent, max_rent, districts, min_size,
                 layout_filter, pets_required, parking_required, elevator_required,
                 keywords_include, keywords_exclude, first_run_done, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,FALSE,$13)
            ON CONFLICT (channel_id) DO UPDATE SET
                guild_id          = EXCLUDED.guild_id,
                min_rent          = EXCLUDED.min_rent,
                max_rent          = EXCLUDED.max_rent,
                districts         = EXCLUDED.districts,
                min_size          = EXCLUDED.min_size,
                layout_filter     = EXCLUDED.layout_filter,
                pets_required     = EXCLUDED.pets_required,
                parking_required  = EXCLUDED.parking_required,
                elevator_required = EXCLUDED.elevator_required,
                keywords_include  = EXCLUDED.keywords_include,
                keywords_exclude  = EXCLUDED.keywords_exclude,
                first_run_done    = FALSE,
                updated_at        = EXCLUDED.updated_at
            """,
            channel_id, guild_id, pref.min_rent, pref.max_rent,
            json.dumps(pref.districts) if pref.districts else None,
            pref.min_size,
            json.dumps(pref.layout_filter) if pref.layout_filter else None,
            pref.pets_required, pref.parking_required, pref.elevator_required,
            json.dumps(pref.keywords_include) if pref.keywords_include else None,
            json.dumps(pref.keywords_exclude) if pref.keywords_exclude else None,
            _now(),
        )


async def set_discord_first_run_done(channel_id: int, updated_at: datetime) -> None:
    """Only applies if the channel wasn't re-subscribed or re-filtered mid-scan."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE discord_channels SET first_run_done=TRUE WHERE channel_id=$1 AND updated_at=$2",
            channel_id, updated_at,
        )
