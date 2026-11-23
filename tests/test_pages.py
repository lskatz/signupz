from signup.cli import main
from signup.pages import MARK, stub_text
from signup.runner import run


def test_new_writes_list_and_page(repo):
    assert main(["--root", str(repo), "new", "potluck"]) == 0
    text = (repo / "_data" / "lists" / "potluck.yml").read_text()
    list_id = next(line[4:] for line in text.splitlines() if line.startswith("id: "))
    assert (repo / "lists" / f"{list_id}.html").read_text() == stub_text(list_id)


def test_run_adds_missing_pages_and_removes_orphans(repo, fake_gh, now):
    main(["--root", str(repo), "new", "potluck"])
    for p in (repo / "lists").glob("*.html"):
        p.unlink()  # e.g. a list created by copying the template by hand
    (repo / "lists" / "gone.html").write_text(stub_text("gone"))
    (repo / "lists" / "about.html").write_text("---\nlayout: default\n---\nhand-written\n")
    r = run(repo, fake_gh, now)
    names = sorted(p.name for p in (repo / "lists").glob("*.html"))
    assert "gone.html" not in names and "about.html" in names and len(names) == 2
    assert r.changed and any(c.endswith("gone.html") for c in r.changed_pages)
    assert MARK in next(p for p in (repo / "lists").glob("*.html") if p.name != "about.html").read_text()
    assert run(repo, fake_gh, now).changed_pages == []


def test_issue_links_to_list_page(repo, fake_gh, now):
    main(["--root", str(repo), "new", "potluck"])
    run(repo, fake_gh, now, "https://lskatz.github.io/signupz/")
    body = fake_gh.issues[1]["body"]
    list_id = fake_gh.issues[1]["title"].split("[list: ")[1].rstrip("]")
    assert f"(https://lskatz.github.io/signupz/lists/{list_id}/)" in body
