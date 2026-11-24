"""Turn a "New sign-up list" issue (from .github/ISSUE_TEMPLATE/new-list.yml) into a list.

Pure functions only: parsing the issue form, the Markdown item list, and making the id.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from .model import DEFAULT_TZ, ConfigError, SignupList, parse_list

REQUEST_LABEL = "new-list-request"

# Issue-form field labels, exactly as in the template. GitHub renders a submitted
# form as "### <label>" sections; an empty optional field becomes "_No response_".
F_DESCRIPTION = "Description"
F_ITEMS = "Items"
F_CLOSES = "Sign-ups close"
F_TIMEZONE = "Time zone"

_NO_RESPONSE = "_No response_"
_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$", re.M)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(?P<rest>.*)$")
_ITEM_RE = re.compile(
    r"^(?P<label>.+?)\s*"
    r"(?:`(?P<name>[^`]+)`)?\s*"
    r"(?:\(\s*(?P<max>[^)]*?)\s*\))?\s*"
    r"(?::\s*(?P<hint>.*))?$"
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def parse_form(body: str) -> dict[str, str]:
    """Split an issue-form body into {label: value}."""
    body = (body or "").replace("\r\n", "\n")
    sections: dict[str, str] = {}
    matches = list(_HEADING_RE.finditer(body))
    for n, m in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(body)
        value = body[m.end() : end].strip()
        sections[m.group(1)] = "" if value == _NO_RESPONSE else value
    return sections


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-")


def parse_items(markdown: str) -> list[dict]:
    """Parse the Markdown bullet list of items. Raises ConfigError naming the bad line.

    - Main dish (2)                      max 2
    - Dessert (3): store-bought is fine  max 3, with a hint
    - Drinks                             no limit
    - Plates, cups and napkins `plates` (1)   explicit command name
    """
    items: list[dict] = []
    seen: dict[str, int] = {}
    text = _COMMENT_RE.sub("", markdown or "")
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        bullet = _BULLET_RE.match(raw)
        if not bullet:
            raise ConfigError(f"Items, line {lineno}: `{raw.strip()}` isn't a list item; start it with `- `")
        m = _ITEM_RE.match(bullet.group("rest").strip())
        if not m or not m.group("label").strip():
            raise ConfigError(f"Items, line {lineno}: `{raw.strip()}` has no item name")
        label = m.group("label").strip().strip("*_").strip()
        name = (m.group("name") or "").strip().lower() or slugify(label)
        if not name:
            raise ConfigError(f"Items, line {lineno}: can't make a command name from `{label}`; add one in backticks")
        item: dict = {"name": name, "label": label}
        if m.group("max") is not None:
            mx = m.group("max")
            if not mx.isdigit() or int(mx) < 1:
                raise ConfigError(f"Items, line {lineno}: `({mx})` should be a maximum of 1 or more, like `(3)`")
            item["max"] = int(mx)
        if m.group("hint"):
            item["description"] = m.group("hint").strip()
        if name in seen:
            raise ConfigError(
                f"Items, lines {seen[name]} and {lineno} would both be called `{name}`; "
                "rename one, or give one its own name in backticks"
            )
        seen[name] = lineno
        items.append(item)
    if not items:
        raise ConfigError("Items: add at least one item, one per line, like `- Dessert (3)`")
    return items


def make_list_id(title: str, when: datetime, taken: set[str] = frozenset()) -> str:  # type: ignore[assignment]
    """Title slug plus ISO 8601 date-time with colons as dashes: holiday-potluck-2026-11-23T21-24."""
    stamp = when.strftime("%Y-%m-%dT%H-%M")
    base = f"{slugify(title, 64 - len(stamp) - 1 - 3) or 'list'}-{stamp}"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


@dataclass
class Draft:
    """The outcome of checking a request: either a list ready to write, or problems."""

    data: dict = field(default_factory=dict)
    lst: SignupList | None = None
    problems: list[str] = field(default_factory=list)


def build_draft(title: str, body: str, created_at: datetime, taken_ids: set[str]) -> Draft:
    form = parse_form(body)
    problems: list[str] = []
    missing = [f for f in (F_ITEMS,) if f not in form]
    if missing:
        problems.append(
            "This doesn't look like the **New sign-up list** form (no "
            + ", ".join(f"`{m}`" for m in missing)
            + " section). Please open the request with the form."
        )
        return Draft(problems=problems)

    tz_name = form.get(F_TIMEZONE) or DEFAULT_TZ
    try:
        when = created_at.astimezone(ZoneInfo(tz_name))
    except Exception:
        when = created_at.astimezone(ZoneInfo(DEFAULT_TZ))

    data: dict = {"id": make_list_id(title, when, taken_ids), "title": title.strip()}
    if form.get(F_DESCRIPTION):
        data["description"] = form[F_DESCRIPTION]
    if form.get(F_CLOSES):
        data["closes"] = form[F_CLOSES].strip()
    data["timezone"] = tz_name
    try:
        data["items"] = parse_items(form[F_ITEMS])
    except ConfigError as e:
        problems.append(str(e))
        return Draft(data=data, problems=problems)
    try:
        lst = parse_list(data, "this request")
    except ConfigError as e:
        problems.append(str(e).replace("this request: ", "").replace("'title'", "the issue title"))
        return Draft(data=data, problems=problems)
    return Draft(data=data, lst=lst)


def list_yaml(data: dict, issue: int) -> str:
    header = f"# Created from issue #{issue} by the signupz bot. Edit freely, but never change `id`.\n"
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()[:12]
