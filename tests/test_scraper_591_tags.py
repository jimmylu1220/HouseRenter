from scrapers.scraper_591 import _parse_tags


def test_string_tags():
    record = {"tags": ["近捷運", "可養寵物", "有電梯", "有車位"]}
    assert _parse_tags(record) == ("可", "有", True)


def test_legacy_dict_tags():
    record = {"rentTagList": [{"name": "可養寵物"}, {"name": "有停車位"}]}
    assert _parse_tags(record) == ("可", "有", None)


def test_no_tags():
    assert _parse_tags({"tags": ["近捷運", "新上架"]}) == (None, None, None)


def test_carport_flag():
    assert _parse_tags({"tags": [], "price_has_carport": 1}) == (None, "有", None)
