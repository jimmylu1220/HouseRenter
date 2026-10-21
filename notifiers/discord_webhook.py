"""Push new listings to a Discord channel via an incoming webhook.

One-way only: the channel mirrors what DISCORD_MIRROR_CHAT_ID receives on
Telegram (same preferences, same blacklist). No buttons — wishlist/blacklist
actions stay on Telegram / the web UI.
"""
import asyncio
import logging
from datetime import timezone

import aiohttp

from db.models import Listing

logger = logging.getLogger(__name__)

_SOURCE_STYLE = {
    "591": ("🏠 591", 0xF5A623),
    "facebook": ("📘 FB社團", 0x1877F2),
}
_DEFAULT_STYLE = ("🏡 其他", 0x95A5A6)

_TITLE_MAX = 256
_DESC_PREVIEW_CHARS = 500
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_MAX_RETRIES = 3
# Discord allows ~5 webhook calls / 2s; stay well under it.
_MIN_INTERVAL = 0.5


def build_embed(listing: Listing) -> dict:
    label, color = _SOURCE_STYLE.get(listing.source, _DEFAULT_STYLE)

    fields = []
    if listing.rent:
        fields.append({"name": "💰 租金", "value": f"{listing.rent:,} 元/月", "inline": True})
    if listing.district:
        fields.append({"name": "📍 區域", "value": listing.district, "inline": True})
    if listing.layout:
        fields.append({"name": "🛏 格局", "value": listing.layout, "inline": True})
    if listing.size_ping:
        fields.append({"name": "📐 坪數", "value": f"{listing.size_ping:g} 坪", "inline": True})
    if listing.floor:
        floor = f"{listing.floor}/{listing.floor_total} 樓" if listing.floor_total else f"{listing.floor} 樓"
        fields.append({"name": "🏢 樓層", "value": floor, "inline": True})
    if listing.address:
        fields.append({"name": "🗺 地址", "value": listing.address[:1024], "inline": False})

    title = listing.title or (listing.description or "(無標題)")[:30]
    embed: dict = {
        "title": title[:_TITLE_MAX],
        "url": listing.url,
        "color": color,
        "fields": fields,
        "footer": {"text": label},
    }

    if listing.description:
        body = listing.description.strip()
        if len(body) > _DESC_PREVIEW_CHARS:
            body = body[:_DESC_PREVIEW_CHARS].rstrip() + "…"
        embed["description"] = body

    image = listing.thumbnail_url
    if not image and listing.images:
        first = listing.images[0]
        image = first if isinstance(first, str) else first.get("url")
    if image:
        embed["image"] = {"url": image}

    if listing.first_seen_at:
        ts = listing.first_seen_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        embed["timestamp"] = ts.isoformat()

    return embed


class DiscordWebhookNotifier:
    def __init__(self, webhook_url: str):
        self._url = webhook_url
        self._lock = asyncio.Lock()

    async def send_listing(self, listing: Listing) -> None:
        await self._post({"embeds": [build_embed(listing)]})

    async def send_text(self, text: str) -> None:
        await self._post({"content": text[:2000]})

    async def _post(self, payload: dict) -> None:
        # Serialise calls so bursts of new listings respect the rate limit.
        async with self._lock:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                for _ in range(_MAX_RETRIES):
                    async with session.post(self._url, json=payload) as resp:
                        if resp.status == 429:
                            data = await resp.json(content_type=None)
                            wait = float(data.get("retry_after", 1))
                            logger.info("Discord webhook rate-limited, retrying in %.1fs", wait)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status >= 400:
                            body = await resp.text()
                            raise RuntimeError(f"Discord webhook HTTP {resp.status}: {body[:200]}")
                        break
            await asyncio.sleep(_MIN_INTERVAL)


_notifier: DiscordWebhookNotifier | None = None


def get_discord_notifier() -> DiscordWebhookNotifier | None:
    """None when DISCORD_WEBHOOK_URL is not configured."""
    global _notifier
    from config import DISCORD_WEBHOOK_URL
    if not DISCORD_WEBHOOK_URL:
        return None
    if _notifier is None:
        _notifier = DiscordWebhookNotifier(DISCORD_WEBHOOK_URL)
    return _notifier
