from pathlib import Path

import pytest

from signup.commands import ALL_COMMANDS, Command, CommandError, parse

REPO = Path(__file__).resolve().parent.parent


def test_claim_with_note_and_case():
    [c] = parse("/CLAIM Dessert  Grandma's pecan pie")
    assert c == Command("claim", item="dessert", note="Grandma's pecan pie", line="/CLAIM Dessert  Grandma's pecan pie")


def test_multiple_lines_and_chatter():
    body = "Happy to help!\n/claim main\n> /claim side (quoted, ignored)\n  /claim drinks lemonade\nthanks"
    assert [(c.name, c.item, c.note) for c in parse(body)] == [("claim", "main", ""), ("claim", "drinks", "lemonade")]


def test_code_fences_ignored():
    assert parse("example:\n```\n/claim dessert\n```\n") == []


@pytest.mark.parametrize(
    "line, msg",
    [("/claim", "needs an item"), ("/unclaim", "needs an item"), ("/remove dessert", "usage"), ("/bogus", "unknown")],
)
def test_errors(line, msg):
    [e] = parse(line)
    assert isinstance(e, CommandError) and msg in e.message


def test_remove_strips_at():
    [c] = parse("/remove @Some-User dessert")
    assert (c.user, c.item) == ("Some-User", "dessert")


def test_urls_and_paths_are_not_commands():
    assert parse("see https://x.org/claim and a/claim b") == []


def test_every_command_is_documented():
    doc = (REPO / "COMMANDS.md").read_text()
    for name in ALL_COMMANDS:
        assert f"`/{name}" in doc, f"/{name} missing from COMMANDS.md"
