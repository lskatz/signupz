from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from signup.model import ConfigError, id_from_title, load_all, parse_list

REPO = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")


def base(**over):
    d = {"id": "abcd-1234", "title": "Potluck", "items": [{"name": "dessert", "max": 2}]}
    d.update(over)
    return d


def test_template_is_valid_once_id_is_filled():
    data = yaml.safe_load((REPO / "templates" / "list.yml").read_text())
    with pytest.raises(ConfigError, match="placeholder"):
        parse_list(data)
    data["id"] = "023f6c1e-0000-4000-8000-000000000000"
    lst = parse_list(data)
    assert [i.name for i in lst.items] == ["main", "side", "dessert", "drinks", "plates"]
    assert lst.item("drinks").max is None


def test_bare_date_closes_at_end_of_day():
    lst = parse_list(base(closes=datetime(2026, 12, 15).date()))
    assert lst.closes_at == datetime(2026, 12, 16, 0, 0, tzinfo=NY)
    assert lst.closes_is_date


def test_string_datetime_uses_list_timezone():
    lst = parse_list(base(closes="2026-12-15T18:00", timezone="America/Chicago"))
    assert lst.closes_at == datetime(2026, 12, 15, 18, 0, tzinfo=ZoneInfo("America/Chicago"))


@pytest.mark.parametrize(
    "over, msg",
    [
        ({"id": None}, "'id' is required"),
        ({"id": "a b"}, "'id' is required"),
        ({"title": ""}, "'title' is required"),
        ({"title": "x [y]"}, r"\[ or \]"),
        ({"items": []}, "non-empty"),
        ({"items": [{"name": "Dessert"}]}, "lowercase"),
        ({"items": [{"name": "dessert"}, {"name": "dessert"}]}, "more than once"),
        ({"items": [{"name": "dessert", "max": 0}]}, "'max'"),
        ({"items": [{"name": "dessert", "max": True}]}, "'max'"),
        ({"items": [{"name": "dessert", "slots": 2}]}, "unknown item field"),
        ({"close": "2026-12-15"}, "unknown field"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"closes": "next tuesday"}, "closes"),
    ],
)
def test_invalid(over, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_list(base(**over))


def test_load_all_reports_duplicate_ids(tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.yml").write_text(yaml.safe_dump(base()))
    (tmp_path / "c.yml").write_text("id: [unclosed")
    lists, errors = load_all(tmp_path)
    assert len(lists) == 1
    assert any("already used" in e for e in errors)
    assert any("not valid YAML" in e for e in errors)


def test_id_from_title():
    assert id_from_title("Potluck [list: 023f-abcd]") == "023f-abcd"
    assert id_from_title("Potluck [list:023f-abcd ]") == "023f-abcd"
    assert id_from_title("Potluck") is None
