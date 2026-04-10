from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Listing:
    source: str
    external_id: str
    url: str
    title: str
    area: str
    district: str
    rent: int
    id: Optional[int] = None
    address: Optional[str] = None
    layout: Optional[str] = None
    size_ping: Optional[float] = None
    floor: Optional[str] = None
    floor_total: Optional[int] = None
    deposit: Optional[str] = None
    utilities: Optional[str] = None
    trash: Optional[bool] = None
    kitchen: Optional[str] = None
    parking: Optional[str] = None
    pets: Optional[str] = None
    elevator: Optional[bool] = None
    gender: Optional[str] = None
    extra_info: Optional[dict] = None
    thumbnail_url: Optional[str] = None
    description: Optional[str] = None      # full post body (mainly Facebook)
    images: Optional[list] = None          # list[str] of URLs, or list[{url, file_id}] after Telegram upload
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    is_available: bool = True


@dataclass
class WishlistItem:
    chat_id: int
    listing_id: int
    id: Optional[int] = None
    added_at: Optional[datetime] = None
    notified_rented: bool = False


@dataclass
class UserPreference:
    chat_id: int
    min_rent: Optional[int] = None
    max_rent: Optional[int] = None
    regions: Optional[list] = None
    districts: Optional[list] = None
    min_size: Optional[float] = None
    layout_filter: Optional[list] = None
    pets_required: Optional[bool] = None
    parking_required: Optional[bool] = None
    elevator_required: Optional[bool] = None
    keywords_include: Optional[list] = None
    keywords_exclude: Optional[list] = None
    updated_at: Optional[datetime] = None
