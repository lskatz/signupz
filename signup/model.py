"""Sign-up list definitions: loading and validating the YAML files in _data/lists/."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{3,63}$")
ITEM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PLACEHOLDER_IDS = {"replace-me", "changeme", "todo"}
DEFAULT_TZ = "America/New_York"

TOP_KEYS = {"id", "title", "description", "closes", "timezone", "items"}
ITEM_KEYS = {"name", "label", "max", "description"}


class ConfigError(ValueError):
    """A list file is invalid. The message says which file and why."""


@dataclass(frozen=True)
class Item:
    name: str
    label: str
    max: int | None = None
    description: str = ""


@dataclass(frozen=True)
class SignupList:
    id: str
    title: str
    items: tuple[Item, ...]
    description: str = ""
    closes_at: datetime | None = None  # timezone-aware
    closes_is_date: bool = False  # True when the YAML gave a bare date
    timezone: str = DEFAULT_TZ
    source: str = ""

    def item(self, name: str) -> Item | None:
        name = name.lower()
        return next((i for i in self.items if i.name == name), None)

    @property
    def issue_title(self) -> str:
        return f"{self.title} [list: {self.id}]"


TITLE_ID_RE = re.compile(r"\[list:\s*([A-Za-z0-9-]+)\s*\]")


def id_from_title(title: str) -> str | None:
    m = TITLE_ID_RE.search(title or "")
    return m.group(1) if m else None


def _parse_closes(raw: object, tz: ZoneInfo, where: str) -> tuple[datetime | None, bool]:
    if raw is None:
        return None, False
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, date):
        # A bare date closes at the end of that day: midnight starting the next.
        return datetime.combine(raw + timedelta(days=1), time(0), tzinfo=tz), True
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError:
            raise ConfigError(f"{where}: 'closes' must look like 2026-12-15 or 2026-12-15T18:00, got {raw!r}") from None
        if len(raw.strip()) == 10:  # "YYYY-MM-DD" given as a quoted string
            return _parse_closes(dt.date(), tz, where)
    else:
        raise ConfigError(f"{where}: 'closes' must be a date or date-time, got {raw!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt, False


def _parse_item(raw: object, where: str) -> Item:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: each item must be a mapping with at least 'name'")
    unknown = set(raw) - ITEM_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown item field(s) {sorted(unknown)}; allowed: {sorted(ITEM_KEYS)}")
    name = raw.get("name")
    if not isinstance(name, str) or not ITEM_RE.match(name):
        raise ConfigError(
            f"{where}: item name {name!r} must be lowercase letters, digits, '-' or '_' "
            "(max 40 chars), e.g. 'dessert' or 'paper-plates'"
        )
    mx = raw.get("max")
    if mx is not None and (isinstance(mx, bool) or not isinstance(mx, int) or mx < 1):
        raise ConfigError(f"{where}: item {name!r} 'max' must be a whole number of 1 or more, or left out")
    label = raw.get("label") or name
    return Item(name=name, label=str(label), max=mx, description=str(raw.get("description") or ""))


def parse_list(data: object, source: str = "") -> SignupList:
    where = source or "<list>"
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: file must be a YAML mapping (key: value lines)")
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown field(s) {sorted(unknown)}; allowed: {sorted(TOP_KEYS)}")

    list_id = data.get("id")
    if not isinstance(list_id, str) or not ID_RE.match(list_id):
        raise ConfigError(
            f"{where}: 'id' is required: 4-64 letters, digits or '-' (generate one with uuidgen), got {list_id!r}"
        )
    if list_id.lower() in PLACEHOLDER_IDS:
        raise ConfigError(f"{where}: replace the placeholder id {list_id!r} with a real one (uuidgen)")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ConfigError(f"{where}: 'title' is required")
    if "[" in title or "]" in title:
        raise ConfigError(f"{where}: 'title' may not contain [ or ] (they mark the list id in the issue title)")

    tz_name = data.get("timezone") or DEFAULT_TZ
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"{where}: unknown timezone {tz_name!r}, e.g. America/New_York") from None

    closes_at, is_date = _parse_closes(data.get("closes"), tz, where)

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ConfigError(f"{where}: 'items' must be a non-empty list")
    items = tuple(_parse_item(r, f"{where} item #{n}") for n, r in enumerate(raw_items, 1))
    seen: set[str] = set()
    for it in items:
        if it.name in seen:
            raise ConfigError(f"{where}: item name {it.name!r} appears more than once; names must be unique")
        seen.add(it.name)

    return SignupList(
        id=list_id,
        title=title.strip(),
        items=items,
        description=str(data.get("description") or "").strip(),
        closes_at=closes_at,
        closes_is_date=is_date,
        timezone=str(tz_name),
        source=source,
    )


def load_list(path: Path, root: Path | None = None) -> SignupList:
    rel = str(path.relative_to(root)) if root else str(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{rel}: not valid YAML: {e}") from None
    return parse_list(data, rel)


def load_all(lists_dir: Path, root: Path | None = None) -> tuple[list[SignupList], list[str]]:
    """Load every list file. Returns the valid lists and a message per invalid file.

    A file whose id duplicates an earlier file's id is reported and skipped.
    """
    lists: list[SignupList] = []
    errors: list[str] = []
    by_id: dict[str, str] = {}
    files = sorted(p for p in lists_dir.glob("*.y*ml") if p.suffix in {".yml", ".yaml"})
    for path in files:
        try:
            lst = load_list(path, root)
        except ConfigError as e:
            errors.append(str(e))
            continue
        if lst.id in by_id:
            errors.append(f"{lst.source}: id {lst.id!r} is already used by {by_id[lst.id]}; ids must be unique")
            continue
        by_id[lst.id] = lst.source
        lists.append(lst)
    return lists, errors
