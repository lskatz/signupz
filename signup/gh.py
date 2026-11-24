"""A thin GitHub REST client: just the calls the bot needs, standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from .model import id_from_title

LABEL = "signup-list"


def is_bot(comment_or_issue: dict) -> bool:
    user = comment_or_issue.get("user") or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHubAPI(Protocol):
    """The interface the runner depends on (tests use a fake)."""

    def find_issue(self, list_id: str) -> int | None: ...
    def create_issue(self, title: str, body: str) -> int: ...
    def get_issue(self, number: int) -> dict: ...
    def update_issue(self, number: int, **fields: Any) -> None: ...
    def list_comments(self, number: int) -> list[dict]: ...
    def create_comment(self, number: int, body: str) -> int: ...
    def add_reaction(self, comment_id: int, content: str) -> None: ...
    def can_write(self, user: str) -> bool: ...
    def issue_url(self, number: int) -> str: ...
    def list_issues(self, label: str) -> list[dict]: ...
    def ensure_label(self, name: str, color: str, description: str) -> None: ...


class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        self.token, self.repo, self.api = token, repo, api.rstrip("/")
        self._perm_cache: dict[str, bool] = {}
        self._labels_ready: set[str] = set()

    # -- plumbing
    def _req(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
        url = f"{self.api}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise GitHubError(e.code, e.read().decode(errors="replace")[:500]) from None
        return json.loads(raw) if raw else None

    def _paged(self, path: str, query: dict | None = None) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            batch = self._req("GET", path, query={**(query or {}), "per_page": 100, "page": page})
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def ensure_label(self, name: str, color: str, description: str) -> None:
        if name in self._labels_ready:
            return
        try:
            self._req("GET", f"/repos/{self.repo}/labels/{urllib.parse.quote(name)}")
        except GitHubError as e:
            if e.status != 404:
                raise
            self._req("POST", f"/repos/{self.repo}/labels", {"name": name, "color": color, "description": description})
        self._labels_ready.add(name)

    # -- API used by the runner
    def find_issue(self, list_id: str) -> int | None:
        for issue in self._paged(f"/repos/{self.repo}/issues", {"labels": LABEL, "state": "all"}):
            if "pull_request" not in issue and id_from_title(issue["title"]) == list_id:
                return issue["number"]
        return None

    def create_issue(self, title: str, body: str) -> int:
        self.ensure_label(LABEL, "0e8a16", "Managed by the sign-up bot")
        issue = self._req("POST", f"/repos/{self.repo}/issues", {"title": title, "body": body, "labels": [LABEL]})
        return issue["number"]

    def get_issue(self, number: int) -> dict:
        return self._req("GET", f"/repos/{self.repo}/issues/{number}")

    def update_issue(self, number: int, **fields: Any) -> None:
        self._req("PATCH", f"/repos/{self.repo}/issues/{number}", fields)

    def list_comments(self, number: int) -> list[dict]:
        return self._paged(f"/repos/{self.repo}/issues/{number}/comments")

    def create_comment(self, number: int, body: str) -> int:
        return self._req("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body})["id"]

    def add_reaction(self, comment_id: int, content: str) -> None:
        self._req("POST", f"/repos/{self.repo}/issues/comments/{comment_id}/reactions", {"content": content})

    def can_write(self, user: str) -> bool:
        key = user.lower()
        if key not in self._perm_cache:
            try:
                perm = self._req("GET", f"/repos/{self.repo}/collaborators/{user}/permission")
                self._perm_cache[key] = perm.get("permission") in {"admin", "maintain", "write"}
            except GitHubError as e:
                if e.status not in (403, 404):
                    raise
                self._perm_cache[key] = False
        return self._perm_cache[key]

    def list_issues(self, label: str) -> list[dict]:
        """Open issues (not pull requests) carrying the label."""
        issues = self._paged(f"/repos/{self.repo}/issues", {"labels": label, "state": "open"})
        return [i for i in issues if "pull_request" not in i]

    def issue_url(self, number: int) -> str:
        return f"https://github.com/{self.repo}/issues/{number}"
