"""Parse slash commands out of an issue comment.

Grammar (one command per line, case-insensitive command and item names):

    /claim <item> [note...]
    /unclaim <item>
    /list
    /help
    /remove @<user> <item>     (write access only)
    /close                     (write access only)
    /reopen                    (write access only)

Lines inside ``` fences and lines not starting with "/" are ignored, so
people can chat, quote, or show examples freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_NOTE = 200

ADMIN_COMMANDS = frozenset({"remove", "close", "reopen"})
ALL_COMMANDS = frozenset({"claim", "unclaim", "list", "help"}) | ADMIN_COMMANDS

_LINE_RE = re.compile(r"^/([A-Za-z]+)\b\s*(.*)$")
_USER_RE = re.compile(r"^@?([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))$")


@dataclass(frozen=True)
class Command:
    name: str
    item: str = ""
    user: str = ""
    note: str = ""
    line: str = ""


@dataclass(frozen=True)
class CommandError:
    line: str
    message: str


def _parse_line(line: str) -> Command | CommandError | None:
    m = _LINE_RE.match(line)
    if not m:
        return None
    name, rest = m.group(1).lower(), m.group(2).strip()
    words = rest.split()
    if name not in ALL_COMMANDS:
        return CommandError(line, f"unknown command `/{name}`; try `/help`")
    if name == "claim":
        if not words:
            return CommandError(line, "`/claim` needs an item, e.g. `/claim dessert brownies`")
        note = rest[len(words[0]) :].strip()
        if len(note) > MAX_NOTE:
            return CommandError(line, f"note is too long (max {MAX_NOTE} characters)")
        return Command("claim", item=words[0].lower(), note=note, line=line)
    if name == "unclaim":
        if not words:
            return CommandError(line, "`/unclaim` needs an item, e.g. `/unclaim dessert`")
        return Command("unclaim", item=words[0].lower(), line=line)
    if name == "remove":
        if len(words) < 2 or not _USER_RE.match(words[0]):
            return CommandError(line, "usage: `/remove @user item`")
        user = _USER_RE.match(words[0]).group(1)  # type: ignore[union-attr]
        return Command("remove", user=user, item=words[1].lower(), line=line)
    return Command(name, line=line)


def parse(body: str) -> list[Command | CommandError]:
    out: list[Command | CommandError] = []
    in_fence = False
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        parsed = _parse_line(line)
        if parsed is not None:
            out.append(parsed)
    return out
