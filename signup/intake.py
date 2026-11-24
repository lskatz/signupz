"""New-list requests: issues opened with the "New sign-up list" form.

Anyone can open one. The bot checks it and replies with a preview or with what
to fix; editing the issue re-runs the check. An approver comments /approve (or
/reject <reason>). Approvers are people with write access to the repository and
anyone listed under `signupz: approvers:` in _config.yml. A request opened by an
approver is approved as soon as it checks out.

On approval the bot writes _data/lists/<id>.yml and the list's page, and the
request issue becomes the list's sign-up issue. The approval comment carries the
list file, so if that run's commit is lost the next run rebuilds it from there.
The request label is removed only once the list file is committed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .engine import State, closes_display
from .gh import LABEL as LIST_LABEL
from .gh import GitHubAPI, is_bot
from .model import TITLE_ID_RE, ConfigError, parse_list
from .pages import write_stub
from .requests import REQUEST_LABEL, Draft, build_draft, content_hash, list_yaml

CHECK_MARK = "<!-- signupz:check={} -->"
APPROVED_MARK = "<!-- signupz:approved -->"
REQ_REPLY_MARK = "<!-- signupz:request-reply-to={} -->"
_CHECK_RE = re.compile(r"<!-- signupz:check=([0-9a-f]+) -->")
_REQ_REPLY_RE = re.compile(r"<!-- signupz:request-reply-to=(\d+) -->")
_YAML_BLOCK_RE = re.compile(r"~~~~yaml\n(.*?)\n~~~~", re.S)
_COMMAND_RE = re.compile(r"^/(approve|reject)\b[ \t]*(.*)$", re.I | re.M)


def load_approvers(root: Path) -> set[str]:
    path = root / "_config.yml"
    if not path.exists():
        return set()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names = ((cfg.get("signupz") or {}).get("approvers")) or []
    return {str(n).lstrip("@").lower() for n in names if n}


@dataclass
class Intake:
    root: Path
    gh: GitHubAPI
    approvers: set[str]
    taken_ids: set[str]
    site_url: str
    log: list[str]

    def is_approver(self, user: str) -> bool:
        return user.lower() in self.approvers or self.gh.can_write(user)


def _parse_created(issue: dict, fallback: datetime) -> datetime:
    raw = issue.get("created_at")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else fallback


def _list_title(issue: dict) -> str:
    """The issue title without a "[list: id]" suffix the bot may already have added."""
    return TITLE_ID_RE.sub("", issue.get("title") or "").strip()


def _preview(draft: Draft) -> str:
    lst = draft.lst
    assert lst is not None
    rows = ["| Item | Command name | Max |", "|---|---|---|"]
    for it in lst.items:
        hint = f"<br><sub>{it.description}</sub>" if it.description else ""
        rows.append(f"| {it.label}{hint} | `{it.name}` | {it.max if it.max is not None else 'no limit'} |")
    closes = closes_display(lst)
    meta = [f"list id `{lst.id}`", f"closes {closes}" if closes else "closes when an approver comments `/close`"]
    return f"**{lst.title}** · " + " · ".join(meta) + "\n\n" + "\n".join(rows)


def committed_list_for_issue(root: Path, number: int) -> str | None:
    """The id of a list whose (committed) state points at this issue, if any."""
    for path in (root / "_data" / "signups").glob("*.json"):
        try:
            state = State.from_json(path.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            continue
        if state.issue == number and (root / "_data" / "lists" / f"{state.list_id}.yml").exists():
            return state.list_id
    return None


def write_list(ctx: Intake, issue: dict, data: dict, last_comment_id: int, request_text: str) -> str:
    list_id = data["id"]
    (ctx.root / "_data" / "lists").mkdir(parents=True, exist_ok=True)
    (ctx.root / "_data" / "lists" / f"{list_id}.yml").write_text(list_yaml(data, issue["number"]), encoding="utf-8")
    write_stub(ctx.root, list_id)
    state = State(list_id=list_id, issue=issue["number"], last_comment_id=last_comment_id, request=request_text)
    state_path = ctx.root / "_data" / "signups" / f"{list_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json(), encoding="utf-8")
    ctx.taken_ids.add(list_id)
    return list_id


def handle_request(ctx: Intake, issue: dict, now: datetime) -> str | None:
    """Process one open request issue. Returns the new list's id if one was written."""
    gh, number = ctx.gh, issue["number"]

    # Already turned into a list and committed: just retire the request label.
    if committed_list_for_issue(ctx.root, number):
        gh.update_issue(number, labels=[LIST_LABEL])
        ctx.log.append(f"request #{number}: list committed, request label removed")
        return None

    comments = sorted(gh.list_comments(number), key=lambda c: c["id"])
    bot_comments = [c for c in comments if is_bot(c)]
    last_id = comments[-1]["id"] if comments else 0

    # Approved before, but that run's commit was lost: rebuild from the approval comment.
    for c in reversed(bot_comments):
        if APPROVED_MARK in (c.get("body") or ""):
            m = _YAML_BLOCK_RE.search(c["body"])
            if not m:
                break
            data = yaml.safe_load(m.group(1))
            parse_list(data, f"approval comment on #{number}")  # still valid?
            list_id = write_list(ctx, issue, data, last_id, "")
            ctx.log.append(f"request #{number}: rebuilt list {list_id} from its approval comment")
            return list_id

    title, body = _list_title(issue), issue.get("body") or ""
    author = issue["user"]["login"]
    created = _parse_created(issue, now)
    draft = build_draft(title, body, created, ctx.taken_ids)
    digest = content_hash(title, body)

    # The latest check comment for exactly this content; post one if there is none.
    check_id = max((c["id"] for c in bot_comments if digest in _CHECK_RE.findall(c.get("body") or "")), default=0)
    replied = {int(n) for c in bot_comments for n in _REQ_REPLY_RE.findall(c.get("body") or "")}
    if not check_id:
        if draft.problems:
            text = (
                f"@{author} thanks! I couldn't turn this into a list yet:\n\n"
                + "\n".join(f"- {p}" for p in draft.problems)
                + "\n\nEdit this issue to fix it and I'll check again."
            )
        else:
            text = f"@{author} thanks! Here's the list this request will create:\n\n{_preview(draft)}\n\n"
            if ctx.is_approver(author):
                text += "You're an approver, so I'm creating it now."
            else:
                text += (
                    "An approver needs to comment `/approve` (or `/reject <reason>`). "
                    "Edit this issue to change anything; I'll check it again."
                )
        check_id = gh.create_comment(number, text + "\n\n" + CHECK_MARK.format(digest))
        ctx.log.append(f"request #{number}: checked ({'problems' if draft.problems else 'ok'})")
        last_id = max(last_id, check_id)

    # Decide: auto-approve the approver's own request, or look for /approve and /reject
    # posted after the latest check (older ones were about earlier content).
    approved_by = author if not draft.problems and ctx.is_approver(author) else None
    if approved_by is None:
        for c in comments:
            if c["id"] <= check_id or is_bot(c):
                continue
            m = _COMMAND_RE.search(c.get("body") or "")
            if not m:
                continue
            who, verb, reason = c["user"]["login"], m.group(1).lower(), m.group(2).strip()
            if not ctx.is_approver(who):
                if c["id"] not in replied:
                    gh.create_comment(
                        number,
                        f"@{who} only approvers can `/{verb}` a request.\n\n" + REQ_REPLY_MARK.format(c["id"]),
                    )
                continue
            if verb == "reject":
                why = f": {reason}" if reason else "."
                if c["id"] not in replied:
                    gh.create_comment(number, f"Closed by @{who}{why}\n\n" + REQ_REPLY_MARK.format(c["id"]))
                gh.update_issue(number, state="closed", state_reason="not_planned")
                ctx.log.append(f"request #{number}: rejected by {who}")
                return None
            if draft.problems:
                if c["id"] not in replied:
                    gh.create_comment(
                        number,
                        f"@{who} this request still has problems (see above), so I can't approve it yet."
                        + "\n\n"
                        + REQ_REPLY_MARK.format(c["id"]),
                    )
                continue
            approved_by = who
            break

    if approved_by is None:
        return None

    assert draft.lst is not None
    list_id = write_list(ctx, issue, draft.data, last_id, body)
    page = f"{ctx.site_url}lists/{list_id}/" if ctx.site_url else ""
    text = (
        f"✅ Approved by @{approved_by}. Sign-ups are open on this issue: comment `/claim <item> [note]`, "
        "for example `/claim " + draft.lst.items[0].name + "`."
    )
    if page:
        text += f"\n\nWebsite: {page}"
    text += (
        "\n\n<details><summary>List file</summary>\n\n~~~~yaml\n"
        + list_yaml(draft.data, number)
        + "~~~~\n</details>\n\n"
        + APPROVED_MARK
    )
    gh.create_comment(number, text)
    # Keep the request label until the list file is committed (see module docstring).
    gh.update_issue(number, labels=[REQUEST_LABEL, LIST_LABEL])
    ctx.log.append(f"request #{number}: approved by {approved_by} as {list_id}")
    return list_id


def process_requests(
    root: Path, gh: GitHubAPI, now: datetime, taken_ids: set[str], site_url: str, log: list[str], errors: list[str]
) -> list[str]:
    gh.ensure_label(REQUEST_LABEL, "fbca04", "New sign-up list, waiting for /approve")
    gh.ensure_label(LIST_LABEL, "0e8a16", "Managed by the sign-up bot")
    ctx = Intake(root, gh, load_approvers(root), taken_ids, site_url, log)
    created: list[str] = []
    for issue in sorted(gh.list_issues(REQUEST_LABEL), key=lambda i: i["number"]):
        try:
            list_id = handle_request(ctx, issue, now)
        except (ConfigError, yaml.YAMLError) as e:
            errors.append(f"request #{issue['number']}: {e}")
            continue
        if list_id:
            created.append(list_id)
    return created
