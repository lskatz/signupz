"""Sign-up state and the rules for applying commands to it.

Everything here is pure: no network, no clock (``now`` is passed in), no files.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .commands import ADMIN_COMMANDS, Command
from .model import SignupList

OPEN, CLOSED = "open", "closed"


@dataclass
class Claim:
    item: str
    user: str
    note: str
    comment_id: int
    at: str  # ISO 8601, UTC


@dataclass
class State:
    """What the bot has recorded for one list. Saved as _data/signups/<id>.json."""

    list_id: str
    issue: int | None = None
    last_comment_id: int = 0
    manual_status: str | None = None  # set by /close or /reopen; overrides the date
    closed_announced: bool = False
    claims: list[Claim] = field(default_factory=list)
    # Derived fields, recomputed every run, for the Jekyll site to render.
    view: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> State:
        d = json.loads(text)
        d["claims"] = [Claim(**c) for c in d.get("claims", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"

    def claims_for(self, item: str) -> list[Claim]:
        return [c for c in self.claims if c.item == item]


def status(lst: SignupList, state: State, now: datetime) -> str:
    if state.manual_status in (OPEN, CLOSED):
        return state.manual_status
    if lst.closes_at is not None and now >= lst.closes_at:
        return CLOSED
    return OPEN


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str
    reply: str = ""  # extra text to post (for /list and /help)


def apply(
    lst: SignupList,
    state: State,
    cmd: Command,
    *,
    author: str,
    is_admin: bool,
    now: datetime,
    comment_id: int,
    help_text: str = "",
) -> Result:
    if cmd.name in ADMIN_COMMANDS and not is_admin:
        return Result(False, f"`/{cmd.name}` is only for people with write access to the repository")

    if cmd.name == "help":
        return Result(True, "help", help_text)
    if cmd.name == "list":
        return Result(True, "list", status_table(lst, state))
    if cmd.name == "close":
        state.manual_status = CLOSED
        return Result(True, "sign-ups closed")
    if cmd.name == "reopen":
        state.manual_status = OPEN
        state.closed_announced = False
        return Result(True, "sign-ups reopened")

    item = lst.item(cmd.item)
    if item is None:
        names = ", ".join(f"`{i.name}`" for i in lst.items)
        return Result(False, f"there is no item `{cmd.item}`; choose one of {names}")

    if cmd.name == "claim":
        if status(lst, state, now) == CLOSED:
            return Result(False, "sign-ups for this list are closed")
        taken = len(state.claims_for(item.name))
        if item.max is not None and taken >= item.max:
            return Result(False, f"**{item.label}** is full ({taken}/{item.max})")
        state.claims.append(
            Claim(
                item=item.name,
                user=author,
                note=cmd.note,
                comment_id=comment_id,
                at=now.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"),
            )
        )
        return Result(True, f"claimed **{item.label}**")

    if cmd.name == "unclaim":
        if status(lst, state, now) == CLOSED and not is_admin:
            return Result(False, "sign-ups for this list are closed; ask the organizer to change it")
        mine = [c for c in state.claims if c.item == item.name and c.user.lower() == author.lower()]
        if not mine:
            return Result(False, f"you have not claimed **{item.label}**")
        state.claims.remove(mine[-1])  # most recent first
        return Result(True, f"unclaimed **{item.label}**")

    if cmd.name == "remove":
        before = len(state.claims)
        state.claims = [c for c in state.claims if not (c.item == item.name and c.user.lower() == cmd.user.lower())]
        removed = before - len(state.claims)
        if not removed:
            return Result(False, f"{cmd.user} has not claimed **{item.label}**")
        return Result(True, f"removed {removed} claim(s) of **{item.label}** by {cmd.user}")

    return Result(False, f"unhandled command `/{cmd.name}`")  # pragma: no cover


# ---------------------------------------------------------------- rendering


def _safe(text: str) -> str:
    """Make user text safe inside a Markdown table cell, and stop it pinging people."""
    return (
        text.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace("\n", " ").replace("@", "@\u200b")
    )


def _who(c: Claim) -> str:
    link = f"[{c.user}](https://github.com/{c.user})"  # a link, not an @mention: no notifications
    return f"{link} ({_safe(c.note)})" if c.note else link


def closes_display(lst: SignupList) -> str:
    if lst.closes_at is None:
        return ""
    tz = ZoneInfo(lst.timezone)
    if lst.closes_is_date:
        last_day = (lst.closes_at.astimezone(tz) - timedelta(days=1)).date()
        return f"end of {last_day:%a %b} {last_day.day}, {last_day.year} ({lst.timezone})"
    dt = lst.closes_at.astimezone(tz)
    hour = dt.strftime("%I").lstrip("0")
    return f"{dt:%a %b} {dt.day}, {dt.year} {hour}:{dt:%M %p %Z}"


def status_table(lst: SignupList, state: State) -> str:
    rows = ["| Item | Command name | Taken | Who |", "|---|---|---|---|"]
    for it in lst.items:
        claims = state.claims_for(it.name)
        taken = f"{len(claims)} / {it.max}" if it.max is not None else f"{len(claims)}"
        who = ", ".join(_who(c) for c in claims) or "—"
        label = _safe(it.label) + (f"<br><sub>{_safe(it.description)}</sub>" if it.description else "")
        rows.append(f"| {label} | `{it.name}` | {taken} | {who} |")
    return "\n".join(rows)


def issue_body(lst: SignupList, state: State, now: datetime, site_url: str = "") -> str:
    st = status(lst, state, now)
    parts = [
        "<!-- Generated by the sign-up bot from "
        f"{lst.source or 'the list file'}. Edits here are overwritten; change the YAML instead. -->",
    ]
    if lst.description:
        parts += [lst.description, ""]
    badge = "🟢 **Open**" if st == OPEN else "🔒 **Closed**"
    closes = closes_display(lst)
    parts.append(f"{badge}" + (f" · closes {closes}" if closes and st == OPEN else ""))
    parts += [
        "",
        "### How to sign up",
        "Comment on this issue with a command on its own line:",
        "",
        "- `/claim <item> [note]`, for example `/claim dessert brownies`",
        "- `/unclaim <item>` to give a slot back",
        "- `/list` for the current status, `/help` for all commands",
        "",
        "The bot reacts 👍 when it has recorded your sign-up, and replies if something went wrong.",
        "",
        "### Items",
        status_table(lst, state),
        "",
    ]
    footer = f"<sub>list id `{lst.id}`"
    if site_url:
        footer = f"[View on the website]({site_url}#list-{lst.id})\n\n" + footer
    parts.append(footer + "</sub>")
    return "\n".join(parts) + "\n"


def build_view(lst: SignupList, state: State, now: datetime, issue_url: str) -> dict:
    return {
        "status": status(lst, state, now),
        "closes_display": closes_display(lst),
        "issue_url": issue_url,
        "counts": {it.name: len(state.claims_for(it.name)) for it in lst.items},
    }
