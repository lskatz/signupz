from datetime import UTC, datetime, timedelta

import pytest

from signup.commands import parse
from signup.engine import CLOSED, OPEN, State, apply, closes_display, issue_body, status
from signup.model import parse_list

NOW = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)


@pytest.fixture
def lst():
    return parse_list(
        {
            "id": "abcd-1234",
            "title": "Potluck",
            "closes": datetime(2026, 12, 15).date(),
            "items": [{"name": "dessert", "label": "Dessert", "max": 2}, {"name": "drinks"}],
        }
    )


def do(lst, state, text, author="alice", admin=False, now=NOW):
    [cmd] = parse(text)
    return apply(lst, state, cmd, author=author, is_admin=admin, now=now, comment_id=1)


def test_claim_until_full_then_reject(lst):
    s = State("abcd-1234")
    assert do(lst, s, "/claim dessert pie").ok
    assert do(lst, s, "/claim dessert cake").ok  # same person, twice: allowed
    r = do(lst, s, "/claim dessert", author="bob")
    assert not r.ok and "full (2/2)" in r.message
    assert [c.note for c in s.claims] == ["pie", "cake"]


def test_unlimited_item(lst):
    s = State("abcd-1234")
    for n in range(50):
        assert do(lst, s, "/claim drinks", author=f"u{n}").ok


def test_unknown_item_lists_choices(lst):
    r = do(lst, State("x"), "/claim salad")
    assert not r.ok and "`dessert`" in r.message and "`drinks`" in r.message


def test_unclaim_removes_most_recent_own_claim(lst):
    s = State("x")
    do(lst, s, "/claim dessert pie")
    do(lst, s, "/claim dessert cake")
    assert do(lst, s, "/unclaim dessert").ok
    assert [c.note for c in s.claims] == ["pie"]
    assert not do(lst, s, "/unclaim dessert", author="bob").ok


def test_closing_by_date(lst):
    s = State("x")
    after = datetime(2026, 12, 16, 5, 1, tzinfo=UTC)  # 00:01 Dec 16 New York
    assert status(lst, s, after - timedelta(minutes=2)) == OPEN
    assert status(lst, s, after) == CLOSED
    r = do(lst, s, "/claim dessert", now=after)
    assert not r.ok and "closed" in r.message


def test_close_and_reopen_override_date(lst):
    s = State("x")
    assert not do(lst, s, "/close").ok  # not an admin
    assert do(lst, s, "/close", admin=True).ok
    assert status(lst, s, NOW) == CLOSED
    late = datetime(2027, 1, 1, tzinfo=UTC)
    assert do(lst, s, "/reopen", admin=True, now=late).ok
    assert status(lst, s, late) == OPEN
    assert do(lst, s, "/claim dessert", now=late).ok


def test_remove_needs_admin(lst):
    s = State("x")
    do(lst, s, "/claim dessert", author="bob")
    do(lst, s, "/claim dessert", author="Bob")
    assert not do(lst, s, "/remove @bob dessert").ok
    r = do(lst, s, "/remove @bob dessert", admin=True)
    assert r.ok and "2 claim" in r.message and s.claims == []


def test_issue_body_escapes_user_text(lst):
    s = State("x")
    do(lst, s, "/claim dessert pie | @everyone <b>")
    body = issue_body(lst, s, NOW)
    assert "\\|" in body and "@​everyone" in body and "&lt;b>" in body
    assert "[alice](https://github.com/alice)" in body
    assert "1 / 2" in body


def test_closes_display(lst):
    assert closes_display(lst) == "end of Tue Dec 15, 2026 (America/New_York)"
    timed = parse_list({"id": "abcd-1234", "title": "T", "closes": "2026-12-15T18:30", "items": [{"name": "a"}]})
    assert closes_display(timed) == "Tue Dec 15, 2026 6:30 PM EST"


def test_state_round_trip(lst):
    s = State("x", issue=3)
    do(lst, s, "/claim dessert pie")
    assert State.from_json(s.to_json()) == s
