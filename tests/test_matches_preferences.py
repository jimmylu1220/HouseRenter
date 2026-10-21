from db.models import Listing, UserPreference
from scheduler.job_manager import _matches_preferences


def _listing(**kw):
    base = dict(source="591", external_id="1", url="https://x/1", title="大安區 2房",
                area="", district="大安區", rent=20000, layout="2房")
    base.update(kw)
    return Listing(**base)


def _prefs(**kw):
    return UserPreference(chat_id=1, **kw)


def test_no_prefs_matches_everything():
    assert _matches_preferences(_listing(), None)
    assert _matches_preferences(_listing(), _prefs())


def test_all_conditions_must_match():
    prefs = _prefs(districts=["大安區"], layout_filter=["2房"], pets_required=True)
    assert _matches_preferences(_listing(pets="可養寵物"), prefs)
    # Right district and layout but no pets → rejected (was accepted under OR).
    assert not _matches_preferences(_listing(pets=None), prefs)
    assert not _matches_preferences(_listing(pets="可養寵物", district="信義區"), prefs)
    assert not _matches_preferences(_listing(pets="可養寵物", layout="3房"), prefs)


def test_amenities():
    prefs = _prefs(parking_required=True, elevator_required=True)
    assert _matches_preferences(_listing(parking="有", elevator=True), prefs)
    assert not _matches_preferences(_listing(parking="有", elevator=None), prefs)
    assert not _matches_preferences(_listing(parking=None, elevator=True), prefs)


def test_keywords():
    prefs = _prefs(keywords_include=["捷運", "陽台"], keywords_exclude=["頂加"])
    assert _matches_preferences(_listing(title="近捷運 有陽台"), prefs)
    assert not _matches_preferences(_listing(title="近捷運"), prefs)
    assert not _matches_preferences(_listing(title="近捷運 有陽台 頂加"), prefs)


def test_rent_and_size():
    prefs = _prefs(min_rent=15000, max_rent=25000, min_size=10)
    assert _matches_preferences(_listing(size_ping=12), prefs)
    assert not _matches_preferences(_listing(rent=30000, size_ping=12), prefs)
    assert not _matches_preferences(_listing(size_ping=8), prefs)


def test_unknown_district_or_layout_not_rejected():
    # Free-form sources (FB) often lack these fields; don't drop them.
    prefs = _prefs(districts=["大安區"], layout_filter=["2房"])
    assert _matches_preferences(_listing(district="", layout=None), prefs)
