"""One bot run: create issues for new lists, catch up on comments, handle closing.

Each run is idempotent and catches up from ``last_comment_id``, so it does not
matter which event triggered it, or if GitHub skipped a queued run: the next
run processes whatever is still unprocessed, in comment order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import commands as cmds
from .engine import CLOSED, OPEN, State, apply, build_view, issue_body, status, status_table
from .gh import GitHubAPI
from .gh import is_bot as _is_bot
from .intake import process_requests
from .model import SignupList, load_all
from .pages import sync_pages

HELP_TEXT = """\
**Sign-up commands.** Put each command on its own line in a comment on this issue.

| Command | What it does |
|---|---|
| `/claim <item> [note]` | Take one slot of an item, e.g. `/claim dessert brownies`. Repeat to take more. |
| `/unclaim <item>` | Give back your most recent slot of that item. |
| `/list` | Show who has signed up for what. |
| `/help` | Show this message. |
| `/remove @user <item>` | *Write access only.* Remove that person's claims of the item. |
| `/close` | *Write access only.* Stop sign-ups now. |
| `/reopen` | *Write access only.* Allow sign-ups again, even past the closing date. |

Item names are the words in the **Command name** column of the issue. Full docs: COMMANDS.md in the repository.
"""


@dataclass
class Report:
    changed_lists: list[str] = field(default_factory=list)
    changed_pages: list[str] = field(default_factory=list)
    created_lists: list[str] = field(default_factory=list)  # from approved requests
    created_issues: list[tuple[str, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changed_lists or self.changed_pages or self.created_lists)


def _posted_at(comment: dict, fallback: datetime) -> datetime:
    raw = comment.get("created_at")
    if not raw:
        return fallback
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# Hidden markers in the bot's own comments. They make every comment the bot posts
# idempotent: if a run posts a reply but its state never gets committed (the job is
# cancelled, the push fails, ...), the next run replays the same comments and must
# not post the same reply again.
REPLY_MARK = "<!-- signupz:reply-to={} -->"
CLOSED_MARK = "<!-- signupz:closed -->"
_REPLY_RE = re.compile(r"<!-- signupz:reply-to=(\d+) -->")


def _posted_marks(comments: list[dict]) -> set[tuple[str, int]]:
    """("reply-to", <comment id>) for each reply posted, ("closed", <own id>) for each close notice."""
    marks: set[tuple[str, int]] = set()
    for c in comments:
        if _is_bot(c):
            body = c.get("body") or ""
            marks.update(("reply-to", int(n)) for n in _REPLY_RE.findall(body))
            if CLOSED_MARK in body:
                marks.add(("closed", c["id"]))
    return marks


def process_comments(
    lst: SignupList,
    state: State,
    gh: GitHubAPI,
    comments: list[dict],
    marks: set[tuple[str, int]],
    now: datetime,
    report: Report,
) -> None:
    assert state.issue is not None
    new = sorted((c for c in comments if c["id"] > state.last_comment_id), key=lambda c: c["id"])
    for comment in new:
        state.last_comment_id = comment["id"]
        if _is_bot(comment):
            continue
        parsed = cmds.parse(comment.get("body", ""))
        if not parsed:
            continue
        author = comment["user"]["login"]
        # Judge deadlines by when the comment was posted, not when the bot got to it.
        posted = _posted_at(comment, now)
        lines: list[str] = []
        replies: list[str] = []
        oks = fails = 0
        for p in parsed:
            if isinstance(p, cmds.CommandError):
                fails += 1
                lines.append(f"- ❌ `{p.line}`: {p.message}")
                continue
            is_admin = p.name in cmds.ADMIN_COMMANDS and gh.can_write(author)
            res = apply(
                lst,
                state,
                p,
                author=author,
                is_admin=is_admin,
                now=posted,
                comment_id=comment["id"],
                help_text=HELP_TEXT,
            )
            if res.ok:
                oks += 1
                lines.append(f"- ✅ `{p.line}`: {res.message}")
            else:
                fails += 1
                lines.append(f"- ❌ `{p.line}`: {res.message}")
            if res.reply:
                replies.append(res.reply)
        report.log.append(f"{lst.id} #{comment['id']} by {author}: {oks} ok, {fails} failed")

        gh.add_reaction(comment["id"], "+1" if not fails else ("-1" if not oks else "confused"))
        if (fails or replies) and ("reply-to", comment["id"]) not in marks:
            body = f"@{author}\n\n" + "\n".join(lines)
            if replies:
                body += "\n\n" + "\n\n".join(replies)
            gh.create_comment(state.issue, body + "\n\n" + REPLY_MARK.format(comment["id"]))
            marks.add(("reply-to", comment["id"]))


def sync_list(lst: SignupList, state: State, gh: GitHubAPI, now: datetime, site_url: str, report: Report) -> None:
    if state.issue is None:
        found = gh.find_issue(lst.id)
        if found is not None:
            state.issue = found
            report.log.append(f"{lst.id}: re-linked to existing issue #{found}")
        else:
            state.issue = gh.create_issue(lst.issue_title, issue_body(lst, state, now, site_url))
            report.created_issues.append((lst.id, state.issue))
            report.log.append(f"{lst.id}: created issue #{state.issue}")

    comments = gh.list_comments(state.issue)
    marks = _posted_marks(comments)
    before = status(lst, state, now)
    process_comments(lst, state, gh, comments, marks, now, report)
    after = status(lst, state, now)

    # Announce closing once per closing, whether it came from the date or /close.
    if after == CLOSED and not state.closed_announced:
        # A close notice posted after the latest /reopen means this closing was already announced.
        announced = any(kind == "closed" and cid > state.reopen_comment_id for kind, cid in marks)
        if not announced:
            why = "by the organizer" if state.manual_status == CLOSED else "because the closing date has passed"
            gh.create_comment(
                state.issue,
                f"🔒 Sign-ups are now closed {why}.\n\n{status_table(lst, state)}\n\n" + CLOSED_MARK,
            )
        state.closed_announced = True
    elif after == OPEN and before == CLOSED:
        state.closed_announced = False

    issue = gh.get_issue(state.issue)
    want_body = issue_body(lst, state, now, site_url)
    want_state = "closed" if after == CLOSED else "open"
    fields: dict = {}
    if issue.get("title") != lst.issue_title:
        fields["title"] = lst.issue_title
    if (issue.get("body") or "").strip() != want_body.strip():
        fields["body"] = want_body
    if issue.get("state") != want_state:
        fields["state"] = want_state
    if fields:
        gh.update_issue(state.issue, **fields)

    state.view = build_view(lst, state, now, gh.issue_url(state.issue))


def run(root: Path, gh: GitHubAPI, now: datetime, site_url: str = "") -> Report:
    report = Report()
    lists, errors = load_all(root / "_data" / "lists", root)
    taken = {lst.id for lst in lists}
    report.created_lists = process_requests(root, gh, now, taken, site_url, report.log, report.errors)
    if report.created_lists:
        lists, errors = load_all(root / "_data" / "lists", root)
    report.errors.extend(errors)
    state_dir = root / "_data" / "signups"
    state_dir.mkdir(parents=True, exist_ok=True)

    for lst in lists:
        path = state_dir / f"{lst.id}.json"
        state = State.from_json(path.read_text(encoding="utf-8")) if path.exists() else State(list_id=lst.id)
        original = state.to_json() if path.exists() else ""
        try:
            sync_list(lst, state, gh, now, site_url, report)
        except Exception as e:  # keep going for other lists; save what we have
            report.errors.append(f"{lst.source}: {type(e).__name__}: {e}")
        new = state.to_json()
        if new != original:
            path.write_text(new, encoding="utf-8")
            report.changed_lists.append(lst.id)
    report.changed_pages = sync_pages(root, lists)
    return report
