from __future__ import annotations

import itertools
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


class FakeGitHub:
    """In-memory stand-in for signup.gh.GitHub."""

    def __init__(self, writers: set[str] | None = None):
        self.issues: dict[int, dict] = {}
        self.comments: dict[int, list[dict]] = {}
        self.reactions: list[tuple[int, str]] = []
        self.writers = {w.lower() for w in (writers or {"owner"})}
        self._num = itertools.count(1)
        self._cid = itertools.count(1000)

    # helpers for tests
    def user_comment(self, issue: int, login: str, body: str, bot: bool = False) -> int:
        cid = next(self._cid)
        self.comments.setdefault(issue, []).append(
            {"id": cid, "body": body, "user": {"login": login, "type": "Bot" if bot else "User"}}
        )
        return cid

    def bot_comments(self, issue: int) -> list[str]:
        return [c["body"] for c in self.comments.get(issue, []) if c["user"]["type"] == "Bot"]

    # API
    def find_issue(self, list_id):
        from signup.model import id_from_title

        return next((n for n, i in self.issues.items() if id_from_title(i["title"]) == list_id), None)

    def create_issue(self, title, body):
        n = next(self._num)
        self.issues[n] = {"number": n, "title": title, "body": body, "state": "open"}
        return n

    def get_issue(self, number):
        return dict(self.issues[number])

    def update_issue(self, number, **fields):
        self.issues[number].update(fields)

    def list_comments(self, number):
        return list(self.comments.get(number, []))

    def create_comment(self, number, body):
        self.user_comment(number, "github-actions[bot]", body, bot=True)

    def add_reaction(self, comment_id, content):
        self.reactions.append((comment_id, content))

    def can_write(self, user):
        return user.lower() in self.writers

    def issue_url(self, number):
        return f"https://github.com/o/r/issues/{number}"


@pytest.fixture
def fake_gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo root with the real template available."""
    (tmp_path / "_data" / "lists").mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    shutil.copy(REPO / "templates" / "list.yml", tmp_path / "templates" / "list.yml")
    return tmp_path


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 12, 1, 15, 0, tzinfo=UTC)
