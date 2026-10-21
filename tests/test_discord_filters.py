import pytest

from db.models import UserPreference
from discord_bot.filters import (
    apply_changes,
    complete_list,
    format_prefs,
    parse_districts,
    parse_layouts,
    split_list,
)


def _prefs(**kw):
    return UserPreference(chat_id=1, **kw)


def test_split_list_accepts_mixed_separators_and_dedupes():
    assert split_list("近捷運, 陽台，電梯、陽台 ") == ["近捷運", "陽台", "電梯"]


def test_parse_districts_adds_suffix():
    assert parse_districts("大安, 信義區") == ["大安區", "信義區"]


def test_parse_districts_rejects_unknown():
    with pytest.raises(ValueError, match="火星"):
        parse_districts("大安區,火星")


def test_parse_layouts_normalizes():
    assert parse_layouts("套, 2, 4房") == ["套房", "2房", "3房+"]
    assert parse_layouts("3房+") == ["3房+"]


def test_parse_layouts_rejects_unknown():
    with pytest.raises(ValueError, match="樓中樓"):
        parse_layouts("樓中樓")


def test_apply_changes_only_touches_given_fields():
    prefs = _prefs(min_rent=10000, districts=["大安區"], pets_required=True)
    apply_changes(prefs, max_rent=25000)
    assert (prefs.min_rent, prefs.max_rent) == (10000, 25000)
    assert prefs.districts == ["大安區"]
    assert prefs.pets_required is True


def test_apply_changes_clears():
    prefs = _prefs(min_rent=10000, districts=["大安區"], pets_required=True, keywords_exclude=["頂加"])
    apply_changes(prefs, min_rent=0, districts="-", pets=False, exclude="無")
    assert prefs.min_rent is None
    assert prefs.districts is None
    assert prefs.pets_required is None
    assert prefs.keywords_exclude is None


def test_apply_changes_rejects_inverted_rent():
    with pytest.raises(ValueError):
        apply_changes(_prefs(max_rent=10000), min_rent=20000)


def test_format_prefs_defaults():
    text = format_prefs(None)
    assert "租金：不限" in text
    assert "行政區：不限" in text


def test_format_prefs_values():
    text = format_prefs(_prefs(min_rent=15000, max_rent=25000, elevator_required=True))
    assert "15,000 ~ 25,000 元" in text
    assert "必須：有電梯" in text


def test_complete_list_completes_last_item():
    assert complete_list("大安區,信", ["大安區", "信義區", "中正區"]) == ["大安區,信義區"]
    # Already-chosen items aren't offered again.
    assert complete_list("大安區,", ["大安區", "信義區"]) == ["大安區,信義區"]
