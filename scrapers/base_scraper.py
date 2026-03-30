from abc import ABC, abstractmethod
from typing import AsyncGenerator

from db.models import Listing


class BaseScraper(ABC):
    SOURCE_NAME: str = ""

    @abstractmethod
    async def fetch_listings(self, region_ids: list[int]) -> AsyncGenerator[Listing, None]:
        """Yield Listing objects for all given region IDs."""
        ...

    @abstractmethod
    async def check_still_available(self, listing: Listing) -> bool:
        """Return True if the listing is still active on the source site."""
        ...

    async def close(self) -> None:
        """Clean up any open sessions."""
        pass
