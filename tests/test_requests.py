"""New-list requests: parsing the form and the /approve flow."""

import json
from datetime import UTC, datetime

import pytest
import yaml

from signup.model import ConfigError, parse_list
from signup.requests import build_draft, make_list_id, parse_form, parse_items
from signup.runner import run

FORM = """### Description

Saturday, Dec 20 at 6 pm.

### Items

- Main dish (2)
- Dessert (3): store-bought is fine
- Drinks
- Plates, cups and napkins `plates` (1)

### Sign-ups close

2026-12-15

### Time zone

America/New_York
"""
# Opened 2026-11-23 21:24 New York time.
CREATED = "2026-11-24T02:24:10Z"
LIST_ID = "holiday-potluck-2026-11-23T21-24"
NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)


# ------------------------------------------------------------------ parsing


def test_parse_form_handles_no_response():
    form = parse_form("### Description\n\n_No response_\n\n### Items\n\n- A\n")
    assert form == {"Description": "", "Items": "- A"}


def test_parse_items_conventions():
    items = parse_items(
        "- Main dish (2)\n* Dessert (3): store-bought is fine\n1. Drinks\n"
        "- [ ] **Plates, cups and napkins** `plates` (1)\n\n<!-- a comment -->\n"
    )
    assert items == [
        {"name": "main-dish", "label": "Main dish", "max": 2},
        {"name": "dessert", "label": "Dessert", "max": 3, "description": "store-bought is fine"},
        {"name": "drinks", "label": "Drinks"},
        {"name": "plates", "label": "Plates, cups and napkins", "max": 1},
    ]


@pytest.mark.parametrize(
    "text, msg",
    [
        ("Dessert", "isn't a list item"),
        ("- Dessert (0)", "maximum of 1 or more"),
        ("- Dessert (lots)", "maximum of 1 or more"),
        ("- Dessert\n- dessert (2)", "lines 1 and 2 would both be called `dessert`"),
        ("- !!!", "backticks"),
        ("", "at least one item"),
    ],
)
def test_parse_items_errors(text, msg):
    with pytest.raises(ConfigError, match=msg.replace("(", r"\(")):
        parse_items(text)


def test_list_id_format():
    when = datetime(2026, 11, 23, 21, 24)
    assert make_list_id("Holiday Potluck!", when) == "holiday-potluck-2026-11-23T21-24"
    assert make_list_id("Holiday Potluck", when, {LIST_ID}) == LIST_ID + "-2"
    long = make_list_id("word " * 30, when)
    assert len(long) <= 64 and long.endswith("-2026-11-23T21-24")


def test_draft_is_a_valid_list():
    draft = build_draft("Holiday Potluck", FORM, datetime.fromisoformat(CREATED), set())
    assert not draft.problems
    assert draft.data["id"] == LIST_ID
    assert parse_list(draft.data).item("plates").max == 1


def test_draft_reports_bad_fields():
    bad = FORM.replace("2026-12-15", "next tuesday").replace("America/New_York", "Mars/Base")
    draft = build_draft("Potluck [v2]", bad, datetime.fromisoformat(CREATED), set())
    assert draft.problems and draft.lst is None


# ------------------------------------------------------------------ flow


@pytest.fixture
def site(repo):
    (repo / "_config.yml").write_text("title: Signupz\nsignupz:\n  approvers: [Helper]\n")
    return repo


def request(fake_gh, who="parent", body=FORM, title="Holiday Potluck"):
    return fake_gh.user_issue(who, title, body, created_at=CREATED)


def files(repo):
    return sorted(p.name for p in (repo / "_data" / "lists").glob("*.yml"))


def test_request_needs_approval(site, fake_gh):
    n = request(fake_gh)
    run(site, fake_gh, NOW)
    [check] = fake_gh.bot_comments(n)
    assert "`main-dish`" in check and "`/approve`" in check and LIST_ID in check
    assert files(site) == []
    run(site, fake_gh, NOW)  # no duplicate check comment
    assert len(fake_gh.bot_comments(n)) == 1
    assert {"new-list-request", "signup-list"} <= fake_gh.labels_created


def test_non_approver_cannot_approve(site, fake_gh):
    n = request(fake_gh)
    run(site, fake_gh, NOW)
    fake_gh.user_comment(n, "parent", "/approve")
    run(site, fake_gh, NOW)
    run(site, fake_gh, NOW)
    assert files(site) == []
    assert sum("only approvers" in b for b in fake_gh.bot_comments(n)) == 1


@pytest.mark.parametrize("approver", ["owner", "helper"])  # repo write access / _config.yml (any case)
def test_approve_turns_request_into_the_signup_issue(site, fake_gh, approver):
    n = request(fake_gh)
    run(site, fake_gh, NOW)
    fake_gh.user_comment(n, approver, "Looks great!\n/approve")
    r = run(site, fake_gh, NOW, "https://lskatz.github.io/signupz/")
    assert r.created_lists == [LIST_ID] and r.changed and not r.errors
    assert files(site) == [f"{LIST_ID}.yml"]
    assert (site / "lists" / f"{LIST_ID}.html").exists()
    issue = fake_gh.issues[n]
    assert issue["title"] == f"Holiday Potluck [list: {LIST_ID}]"
    assert "/claim <item> [note]" in issue["body"] and "Original request" in issue["body"]
    assert "Approved by" in fake_gh.bot_comments(n)[-1]
    assert len(fake_gh.issues) == 1  # no second issue

    # Sign-ups work on the same issue; the /approve comment is not a sign-up command.
    fake_gh.user_comment(n, "parent", "/claim dessert pie")
    run(site, fake_gh, NOW)
    state = json.loads((site / "_data" / "signups" / f"{LIST_ID}.json").read_text())
    assert [(c["user"], c["item"]) for c in state["claims"]] == [("parent", "dessert")]
    assert not any("unknown command" in b for b in fake_gh.bot_comments(n))
    # The request label goes once the list is committed (here: present at the next run).
    assert fake_gh.issues[n]["labels"] == ["signup-list"]


def test_approvers_own_request_is_approved_immediately(site, fake_gh):
    n = request(fake_gh, who="helper")
    r = run(site, fake_gh, NOW)
    assert r.created_lists == [LIST_ID]
    assert "creating it now" in fake_gh.bot_comments(n)[0]


def test_edit_rechecks_and_old_approval_does_not_count(site, fake_gh):
    n = request(fake_gh, body=FORM.replace("- Drinks\n", "- Drinks\n- drinks (2)\n"))
    run(site, fake_gh, NOW)
    assert "would both be called `drinks`" in fake_gh.bot_comments(n)[0]
    fake_gh.user_comment(n, "owner", "/approve")
    run(site, fake_gh, NOW)
    assert files(site) == [] and "still has problems" in fake_gh.bot_comments(n)[-1]
    fake_gh.issues[n]["body"] = FORM  # the requester fixes it
    run(site, fake_gh, NOW)
    assert files(site) == [] and "Here's the list" in fake_gh.bot_comments(n)[-1]
    fake_gh.user_comment(n, "owner", "/approve")
    run(site, fake_gh, NOW)
    assert files(site) == [f"{LIST_ID}.yml"]


def test_reject_closes_request(site, fake_gh):
    n = request(fake_gh)
    run(site, fake_gh, NOW)
    fake_gh.user_comment(n, "owner", "/reject we already have one")
    run(site, fake_gh, NOW)
    assert fake_gh.issues[n]["state"] == "closed" and fake_gh.issues[n]["state_reason"] == "not_planned"
    assert "we already have one" in fake_gh.bot_comments(n)[-1] and files(site) == []


def test_lost_commit_after_approval_is_rebuilt(site, fake_gh):
    n = request(fake_gh)
    run(site, fake_gh, NOW)
    fake_gh.user_comment(n, "owner", "/approve")
    run(site, fake_gh, NOW)
    for sub in ("_data/lists", "_data/signups", "lists"):  # that run's commit never landed
        for p in (site / sub).glob(f"{LIST_ID}*"):
            p.unlink()
    r = run(site, fake_gh, NOW)
    assert r.created_lists == [LIST_ID] and files(site) == [f"{LIST_ID}.yml"]
    assert sum("Approved by" in b for b in fake_gh.bot_comments(n)) == 1
    written = yaml.safe_load((site / "_data" / "lists" / f"{LIST_ID}.yml").read_text())
    assert written["items"][0] == {"name": "main-dish", "label": "Main dish", "max": 2}


def test_two_requests_same_title_same_minute_get_distinct_ids(site, fake_gh):
    a, b = request(fake_gh, who="helper"), request(fake_gh, who="helper")
    r = run(site, fake_gh, NOW)
    assert r.created_lists == [LIST_ID, LIST_ID + "-2"]
    assert fake_gh.issues[b]["title"].endswith(f"[list: {LIST_ID}-2]")
    assert a != b
