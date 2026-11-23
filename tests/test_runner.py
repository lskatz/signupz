import json
from datetime import UTC, datetime

from signup.cli import default_site_url, main
from signup.runner import run


def new_list(repo, name="potluck"):
    assert main(["--root", str(repo), "new", name]) == 0
    text = (repo / "_data" / "lists" / f"{name}.yml").read_text()
    list_id = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("id: "))
    return list_id


def state(repo, list_id):
    return json.loads((repo / "_data" / "signups" / f"{list_id}.json").read_text())


def test_push_creates_issue_once(repo, fake_gh, now):
    lid = new_list(repo)
    r = run(repo, fake_gh, now)
    assert r.created_issues == [(lid, 1)] and not r.errors
    assert fake_gh.issues[1]["title"] == f"Holiday Potluck [list: {lid}]"
    # Second run: nothing new, nothing changes, no second issue.
    r2 = run(repo, fake_gh, now)
    assert r2.created_issues == [] and not r2.changed and len(fake_gh.issues) == 1


def test_relinks_existing_issue_if_state_lost(repo, fake_gh, now):
    lid = new_list(repo)
    run(repo, fake_gh, now)
    (repo / "_data" / "signups" / f"{lid}.json").unlink()
    r = run(repo, fake_gh, now)
    assert r.created_issues == [] and len(fake_gh.issues) == 1
    assert state(repo, lid)["issue"] == 1


def test_comments_flow(repo, fake_gh, now):
    lid = new_list(repo)
    run(repo, fake_gh, now)
    ok = fake_gh.user_comment(1, "alice", "Count me in!\n/claim dessert brownies\n/claim drinks")
    bad = fake_gh.user_comment(1, "bob", "/claim salad")
    chat = fake_gh.user_comment(1, "carol", "What time is it?")
    r = run(repo, fake_gh, now)
    assert r.changed
    s = state(repo, lid)
    assert [(c["user"], c["item"], c["note"]) for c in s["claims"]] == [
        ("alice", "dessert", "brownies"),
        ("alice", "drinks", ""),
    ]
    assert s["last_comment_id"] == chat
    assert (ok, "+1") in fake_gh.reactions and (bad, "-1") in fake_gh.reactions
    assert not any(cid == chat for cid, _ in fake_gh.reactions)
    replies = fake_gh.bot_comments(1)
    assert len(replies) == 1 and "@bob" in replies[0] and "no item `salad`" in replies[0]
    assert "brownies" in fake_gh.issues[1]["body"]
    assert s["view"]["counts"]["dessert"] == 1 and s["view"]["status"] == "open"


def test_catch_up_is_idempotent(repo, fake_gh, now):
    """A skipped run loses nothing, and a re-run never double-applies."""
    lid = new_list(repo)
    run(repo, fake_gh, now)
    for user in ("a1", "a2", "a3", "a4"):
        fake_gh.user_comment(1, user, "/claim dessert")  # max is 3
    run(repo, fake_gh, now)
    run(repo, fake_gh, now)
    s = state(repo, lid)
    assert [c["user"] for c in s["claims"]] == ["a1", "a2", "a3"]  # first come, first served
    assert sum("full" in b for b in fake_gh.bot_comments(1)) == 1


def test_bot_comments_are_ignored(repo, fake_gh, now):
    lid = new_list(repo)
    run(repo, fake_gh, now)
    fake_gh.user_comment(1, "github-actions[bot]", "/claim dessert", bot=True)
    run(repo, fake_gh, now)
    assert state(repo, lid)["claims"] == []


def test_close_by_date_announces_once_and_closes_issue(repo, fake_gh, now):
    lid = new_list(repo)  # template closes end of 2026-12-15, New York
    run(repo, fake_gh, now)
    later = datetime(2026, 12, 16, 6, 0, tzinfo=UTC)
    run(repo, fake_gh, later)
    run(repo, fake_gh, later)
    assert fake_gh.issues[1]["state"] == "closed"
    assert sum("now closed" in b for b in fake_gh.bot_comments(1)) == 1
    assert state(repo, lid)["view"]["status"] == "closed"


def test_admin_close_and_reopen(repo, fake_gh, now):
    lid = new_list(repo)
    run(repo, fake_gh, now)
    fake_gh.user_comment(1, "mallory", "/close")
    run(repo, fake_gh, now)
    assert fake_gh.issues[1]["state"] == "open"
    fake_gh.user_comment(1, "owner", "/close")
    run(repo, fake_gh, now)
    assert fake_gh.issues[1]["state"] == "closed" and state(repo, lid)["manual_status"] == "closed"
    fake_gh.user_comment(1, "owner", "/reopen")
    fake_gh.user_comment(1, "dave", "/claim side")
    run(repo, fake_gh, now)
    assert fake_gh.issues[1]["state"] == "open"
    assert [c["user"] for c in state(repo, lid)["claims"]] == ["dave"]


def test_invalid_list_reported_but_others_run(repo, fake_gh, now):
    new_list(repo)
    (repo / "_data" / "lists" / "broken.yml").write_text("id: REPLACE-ME\ntitle: X\nitems: [{name: a}]\n")
    r = run(repo, fake_gh, now)
    assert len(fake_gh.issues) == 1
    assert len(r.errors) == 1 and "placeholder" in r.errors[0]


def test_yaml_edit_updates_issue_title(repo, fake_gh, now):
    new_list(repo)
    run(repo, fake_gh, now)
    p = repo / "_data" / "lists" / "potluck.yml"
    p.write_text(p.read_text().replace("title: Holiday Potluck", "title: New Year Potluck"))
    run(repo, fake_gh, now)
    assert fake_gh.issues[1]["title"].startswith("New Year Potluck [list: ")


def test_default_site_url():
    assert default_site_url("lskatz/signups") == "https://lskatz.github.io/signups/"
    assert default_site_url("LSkatz/lskatz.github.io") == "https://lskatz.github.io/"


def test_deadline_uses_comment_time_not_run_time(repo, fake_gh, now):
    lid = new_list(repo)  # closes at 05:00 UTC on 2026-12-16
    run(repo, fake_gh, now)
    on_time = fake_gh.user_comment(1, "early", "/claim side")
    late = fake_gh.user_comment(1, "late", "/claim side")
    for c in fake_gh.comments[1]:
        if c["id"] == on_time:
            c["created_at"] = "2026-12-16T04:59:00Z"
        if c["id"] == late:
            c["created_at"] = "2026-12-16T05:00:30Z"
    run(repo, fake_gh, datetime(2026, 12, 16, 5, 7, tzinfo=UTC))  # hourly run, after the deadline
    assert [c["user"] for c in state(repo, lid)["claims"]] == ["early"]
    assert (late, "-1") in fake_gh.reactions
