"""Collect raw posts from one group feed.

Instead of scraping the rendered DOM (class names change constantly), we read
the same JSON the FB web app consumes:
  * the first batch of posts is server-rendered into
    <script type="application/json"> tags on page load;
  * every further batch arrives via /api/graphql/ while scrolling.
Both are fed to parser.extract_posts_from_bodies().
"""
import asyncio
import logging
import random

from scrapers.facebook.parser import RawPost, extract_posts_from_bodies
from scrapers.facebook.session import FacebookSession

logger = logging.getLogger(__name__)

_MAX_SCROLLS = 12
# Stop scrolling once this many already-known posts have shown up and the
# last few scrolls brought nothing new (chronological feed → we've caught up).
_KNOWN_STOP_THRESHOLD = 3
_STALE_SCROLL_LIMIT = 2


def group_feed_url(group: str) -> str:
    return f"https://www.facebook.com/groups/{group}/?sorting_setting=CHRONOLOGICAL"


async def _human_pause(lo: float = 1.5, hi: float = 3.5) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def collect_group_posts(
    session: FacebookSession,
    group: str,
    max_posts: int,
    known_ids: set[str] | None = None,
) -> list[RawPost]:
    """Return up to `max_posts` posts from the group, newest first."""
    known_ids = known_ids or set()
    context = await session.context()
    page = await context.new_page()

    bodies: list[str] = []
    pending: list[asyncio.Task] = []

    async def _capture(response) -> None:
        try:
            if "/api/graphql" in response.url and response.ok:
                bodies.append(await response.text())
        except Exception:
            pass

    page.on("response", lambda r: pending.append(asyncio.create_task(_capture(r))))

    try:
        await page.goto(group_feed_url(group), wait_until="domcontentloaded", timeout=45_000)
        await _human_pause(2.5, 4.0)
        await session.assert_logged_in(page)

        bodies.extend(await page.eval_on_selector_all(
            'script[type="application/json"]',
            "els => els.map(e => e.textContent).filter(t => t && t.includes('post_id'))",
        ))

        posts: dict[str, RawPost] = {}
        stale_scrolls = 0
        for scroll in range(_MAX_SCROLLS + 1):
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                pending.clear()

            before = len(posts)
            for p in extract_posts_from_bodies(bodies, group):
                prev = posts.get(p.post_id)
                if prev is None or len(p.text) > len(prev.text):
                    posts[p.post_id] = p
            bodies.clear()

            known_seen = sum(1 for pid in posts if pid in known_ids)
            stale_scrolls = stale_scrolls + 1 if len(posts) == before else 0
            if len(posts) >= max_posts:
                break
            if known_seen >= _KNOWN_STOP_THRESHOLD:
                break
            if stale_scrolls > _STALE_SCROLL_LIMIT:
                break
            if scroll == _MAX_SCROLLS:
                break

            await page.mouse.wheel(0, random.randint(2200, 3200))
            await _human_pause()

        result = sorted(
            posts.values(),
            key=lambda p: p.created_at.timestamp() if p.created_at else 0,
            reverse=True,
        )[:max_posts]
        logger.info("FB group %s: collected %d posts (%d known)", group, len(result),
                    sum(1 for p in result if p.post_id in known_ids))
        return result
    finally:
        for t in pending:
            t.cancel()
        await page.close()
