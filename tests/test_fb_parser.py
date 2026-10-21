import json

import pytest

from scrapers.facebook.parser import (
    RawPost,
    extract_district,
    extract_floor,
    extract_layout,
    extract_posts,
    extract_posts_from_bodies,
    extract_rent,
    extract_size_ping,
    is_wanted_post,
    parse_post,
)

OFFER_POST = """【大安區】近捷運科技大樓站 溫馨2房1廳 出租
租金：28,000元/月（含管理費）
押金兩個月
坪數：22坪
樓層：5/12樓 有電梯
聯絡電話 0912345678
"""

SUITE_POST = """信義區獨立套房出租～
月租15000 水電另計
8坪 3F 可養寵物
"""

WANTED_POST = """徵求 中山區 套房
預算 12000/月 以內，希望近捷運
"""


# ── field extraction ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("租金：28,000元/月", 28000),
    ("月租15000 水電另計", 15000),
    ("12000/月", 12000),
    ("每月 9,500 元", 9500),
    ("租金1.8萬", 18000),
    ("月租 2萬2", 22000),
    ("押金20000元 租金 16000", 16000),
    ("聯絡 0912345678", 0),
    ("建於 1998 年", 0),
    ("", 0),
])
def test_extract_rent(text, expected):
    assert extract_rent(text) == expected


def test_rent_ignores_phone_number_before_real_rent():
    assert extract_rent("電話0912345678 租金 18,000") == 18000


@pytest.mark.parametrize("text,expected", [
    ("大安區近捷運", "大安區"),
    ("位於板橋 新埔站", "板橋區"),
    ("信義路三段 近大安森林公園", "大安區"),   # 信義路 must not map to 信義區
    ("台中市西屯", None),
])
def test_extract_district(text, expected):
    assert extract_district(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("溫馨2房1廳", "2房"),
    ("三房兩廳", "3房+"),
    ("4房2衛", "3房+"),
    ("獨立套房", "套房"),
    ("雅房出租", "雅房"),
    ("整層住家", None),
])
def test_extract_layout(text, expected):
    assert extract_layout(text) == expected


def test_extract_size_and_floor():
    assert extract_size_ping("坪數：22坪") == 22
    assert extract_size_ping("8.5 坪") == 8.5
    assert extract_size_ping("無") is None
    assert extract_floor("樓層：5/12樓") == ("5", 12)
    assert extract_floor("3F 可養寵物") == ("3", None)
    assert extract_floor("沒有樓層") == (None, None)


def test_is_wanted_post():
    assert is_wanted_post(WANTED_POST)
    assert not is_wanted_post(OFFER_POST)


# ── parse_post ────────────────────────────────────────────────────────────────

def test_parse_offer_post():
    raw = RawPost(post_id="111", group="taipei.rent", text=OFFER_POST, images=["https://scontent/a.jpg"])
    listing = parse_post(raw)
    assert listing is not None
    assert listing.source == "facebook"
    assert listing.external_id == "111"
    assert listing.url == "https://www.facebook.com/groups/taipei.rent/posts/111/"
    assert listing.rent == 28000
    assert listing.district == "大安區"
    assert listing.layout == "2房"
    assert listing.size_ping == 22
    assert (listing.floor, listing.floor_total) == ("5", 12)
    assert listing.thumbnail_url == "https://scontent/a.jpg"
    assert listing.title.startswith("大安區")
    assert listing.description.startswith("【大安區】")


def test_parse_suite_post():
    listing = parse_post(RawPost(post_id="222", group="g", text=SUITE_POST))
    assert listing is not None
    assert (listing.rent, listing.district, listing.layout) == (15000, "信義區", "套房")


def test_parse_rejects_wanted_and_noise():
    assert parse_post(RawPost(post_id="1", group="g", text=WANTED_POST)) is None
    assert parse_post(RawPost(post_id="2", group="g", text="短")) is None
    assert parse_post(RawPost(post_id="3", group="g", text="大家好，今天天氣很好，分享一下我家的貓咪照片")) is None


# ── GraphQL extraction ────────────────────────────────────────────────────────

def _story(post_id, text, images=(), ts=1760000000):
    return {
        "__typename": "Story",
        "post_id": post_id,
        "url": f"https://www.facebook.com/groups/g/posts/{post_id}/",
        "actors": [{"name": "房東A", "profile_picture": {"uri": "https://scontent/avatar.jpg"}}],
        "comet_sections": {
            "content": {"story": {"message": {"text": text}}},
            "context_layout": {"story": {"creation_time": ts}},
        },
        "attachments": [
            {"media": {"photo_image": {"uri": u, "width": 960, "height": 720}}} for u in images
        ] + [{"media": {"image": {"uri": "https://scontent/tiny.jpg", "width": 50, "height": 50}}}],
    }


def test_extract_posts_from_graphql_payload():
    payload = {"data": {"node": {"group_feed": {"edges": [
        {"node": _story("100", OFFER_POST, ["https://scontent/1.jpg", "https://scontent/2.jpg"])},
        {"node": _story("200", SUITE_POST, ts=1760000100)},
    ]}}}}
    posts = {p.post_id: p for p in extract_posts(payload, "g")}
    assert set(posts) == {"100", "200"}
    p = posts["100"]
    assert p.text == OFFER_POST
    assert p.images == ["https://scontent/1.jpg", "https://scontent/2.jpg"]  # avatar & tiny skipped
    assert p.author == "房東A"
    assert p.created_at.timestamp() == 1760000000
    assert p.url.endswith("/posts/100/")


def test_extract_posts_from_multiline_bodies():
    line1 = json.dumps({"data": {"node": _story("100", OFFER_POST)}})
    line2 = json.dumps({"label": "x", "data": {"node": _story("300", SUITE_POST)}})
    body = "for (;;);" + line1 + "\n" + line2 + "\nnot json"
    posts = extract_posts_from_bodies([body], "g")
    assert sorted(p.post_id for p in posts) == ["100", "300"]


def test_extract_posts_ignores_nodes_without_message():
    payload = {"post_id": "999", "feedback": {"reaction_count": 3}}
    assert extract_posts(payload, "g") == []


@pytest.mark.parametrize("text, expected", [
    ("8坪 3F 可養寵物", ("可", None, None)),
    ("寵物友善，歡迎毛孩", ("可", None, None)),
    ("可養貓 有電梯 含車位", ("可", "有", True)),
    ("不可養寵物 無電梯", (None, None, None)),
    ("謝絕寵物，公寓沒電梯", (None, None, None)),
    ("寵物不可", (None, None, None)),
    ("附機車位", (None, None, None)),
    ("無車位 電梯大樓", (None, None, True)),
    ("平面車位另租", (None, "有", None)),
])
def test_extract_amenities(text, expected):
    from scrapers.facebook.parser import extract_amenities
    assert extract_amenities(text) == expected


def test_parse_post_sets_amenities():
    listing = parse_post(RawPost(post_id="9", group="g", text=SUITE_POST))
    assert listing.pets == "可"
