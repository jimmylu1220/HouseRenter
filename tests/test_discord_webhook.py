from datetime import datetime

from db.models import Listing
from notifiers.discord_webhook import build_embed


def _listing(**kw):
    base = dict(source="591", external_id="1", url="https://x/1", title="大安區 2房",
                area="", district="大安區", rent=28000)
    base.update(kw)
    return Listing(**base)


def test_embed_basic_fields():
    e = build_embed(_listing(layout="2房", size_ping=22.0, floor="5", floor_total=12,
                             thumbnail_url="https://img/1.jpg"))
    assert e["title"] == "大安區 2房"
    assert e["url"] == "https://x/1"
    assert e["footer"]["text"] == "🏠 591"
    fields = {f["name"]: f["value"] for f in e["fields"]}
    assert fields["💰 租金"] == "28,000 元/月"
    assert fields["📐 坪數"] == "22 坪"
    assert fields["🏢 樓層"] == "5/12 樓"
    assert e["image"]["url"] == "https://img/1.jpg"
    assert "description" not in e


def test_embed_facebook_post_with_body_and_cached_images():
    e = build_embed(_listing(
        source="facebook", rent=0, district="", title="",
        description="很長的貼文" * 200,
        images=[{"url": "https://scontent/a.jpg", "file_id": "abc"}],
        first_seen_at=datetime(2026, 10, 21, 8, 0),
    ))
    assert e["footer"]["text"] == "📘 FB社團"
    assert e["fields"] == []
    assert e["title"].startswith("很長的貼文")
    assert len(e["description"]) <= 501 and e["description"].endswith("…")
    assert e["image"]["url"] == "https://scontent/a.jpg"
    assert e["timestamp"] == "2026-10-21T08:00:00+00:00"


def test_embed_title_truncated():
    assert len(build_embed(_listing(title="長" * 400))["title"]) == 256
