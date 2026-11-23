"""Command line: python -m signup {run,validate,new}."""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .gh import GitHub
from .model import load_all
from .pages import PAGES_DIR, write_stub
from .runner import run

ROOT = Path(__file__).resolve().parent.parent


def default_site_url(repo: str) -> str:
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def _write_output(**kv: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def cmd_run(args: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        return 2
    site_url = os.environ.get("SITE_URL") or default_site_url(repo)
    report = run(args.root, GitHub(token, repo), datetime.now(UTC), site_url)
    for line in report.log:
        print(line)
    for err in report.errors:
        print(f"::error::{err}")
    _write_output(changed=str(report.changed).lower(), errors=str(len(report.errors)))
    if report.errors:
        _summary("### Sign-up bot problems\n\n" + "\n".join(f"- {e}" for e in report.errors))
    return 0  # problems are reported via the `errors` output so the commit step still runs


def cmd_validate(args: argparse.Namespace) -> int:
    lists, errors = load_all(args.root / "_data" / "lists", args.root)
    for lst in lists:
        print(f"ok   {lst.source}  id={lst.id}  items={len(lst.items)}")
    for err in errors:
        print(f"FAIL {err}")
    return 1 if errors else 0


def cmd_new(args: argparse.Namespace) -> int:
    slug = args.name.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        print("name must be lowercase letters, digits, - or _", file=sys.stderr)
        return 2
    dest = args.root / "_data" / "lists" / f"{slug}.yml"
    if dest.exists():
        print(f"{dest} already exists", file=sys.stderr)
        return 1
    template = (args.root / "templates" / "list.yml").read_text(encoding="utf-8")
    text, n = re.subn(r"(?m)^id: REPLACE-ME$", f"id: {uuid.uuid4()}", template)
    if n != 1:
        print("template has no 'id: REPLACE-ME' line", file=sys.stderr)
        return 1
    dest.write_text(text, encoding="utf-8")
    list_id = re.search(r"(?m)^id: (.+)$", text).group(1)  # type: ignore[union-attr]
    write_stub(args.root, list_id)
    print(f"created {dest.relative_to(args.root)} and {PAGES_DIR}/{list_id}.html")
    print("edit the .yml, then commit both files and push")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m signup", description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="one bot run (used by the GitHub Action)").set_defaults(fn=cmd_run)
    sub.add_parser("validate", help="check every list file").set_defaults(fn=cmd_validate)
    p_new = sub.add_parser("new", help="create _data/lists/<name>.yml from the template with a fresh id")
    p_new.add_argument("name")
    p_new.set_defaults(fn=cmd_new)
    args = ap.parse_args(argv)
    return args.fn(args)
