import asyncio
import logging
import random
from collections import defaultdict
from typing import AsyncGenerator

from config import FACEBOOK_GROUPS, FACEBOOK_MAX_POSTS_PER_GROUP, FACEBOOK_STATE_PATH
from db.models import Listing
from scrapers.base_scraper import BaseScraper
from scrapers.facebook.feed import collect_group_posts
from scrapers.facebook.parser import SOURCE_NAME, RawPost, parse_post
from scrapers.facebook.session import FacebookAuthError, FacebookSession

logger = logging.getLogger(__name__)

# Remember this many post ids per group between cycles.
_SEEN_CAP = 500


class ScraperFacebook(BaseScraper):
    SOURCE_NAME = SOURCE_NAME
    # We only ever see the newest posts of a group, so "not seen this cycle"
    # does not mean "taken down".
    STALE_CHECK_ENABLED = False
    # Groups are scraped one after another; the scheduler's "N known listings
    # in a row → stop" shortcut would skip later groups, so it is disabled.
    EARLY_EXIT_AFTER_KNOWN = None

    def __init__(
        self,
        groups: list[str] | None = None,
        state_path: str = FACEBOOK_STATE_PATH,
        max_posts: int = FACEBOOK_MAX_POSTS_PER_GROUP,
    ):
        self.groups = groups if groups is not None else FACEBOOK_GROUPS
        self.max_posts = max_posts
        self.session = FacebookSession(state_path)
        self._seen: dict[str, list[str]] = defaultdict(list)

    async def fetch_listings(self, region_ids: list[int]) -> AsyncGenerator[Listing, None]:
        # region_ids is unused: groups are not region-scoped.
        for i, group in enumerate(self.groups):
            if i:
                await asyncio.sleep(random.uniform(5, 12))
            try:
                posts = await collect_group_posts(
                    self.session, group, self.max_posts, known_ids=set(self._seen[group])
                )
            except FacebookAuthError:
                raise
            except Exception as exc:
                logger.warning("FB group %s scrape failed: %s", group, exc, exc_info=True)
                continue

            for post in posts:
                if post.post_id in self._seen[group]:
                    continue
                self._remember(group, post.post_id)
                listing = parse_post(post)
                if listing is not None:
                    yield listing

        await self.session.persist_state()

    async def preview(self, group: str, limit: int = 10) -> list[tuple[RawPost, Listing | None]]:
        """Fetch without touching the seen-set; used by /check_fb."""
        posts = await collect_group_posts(self.session, group, limit)
        return [(p, parse_post(p)) for p in posts]

    async def check_still_available(self, listing: Listing) -> bool:
        return True

    async def close(self) -> None:
        await self.session.close()

    def _remember(self, group: str, post_id: str) -> None:
        seen = self._seen[group]
        seen.append(post_id)
        if len(seen) > _SEEN_CAP:
            del seen[: len(seen) - _SEEN_CAP]
