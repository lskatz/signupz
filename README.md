# GitHub sign-up lists

Sign-up lists (potlucks, volunteer slots, bake sales) on a GitHub Pages site.
People sign up with their GitHub account by commenting slash commands like
`/claim dessert brownies` on the list's issue. No server, no database.

```
you push _data/lists/potluck.yml
        │
        ▼
GitHub Action ──creates──▶ Issue "Holiday Potluck [list: 023f…]"
        ▲                         │
        │   people comment /claim dessert
        └──────── issue_comment ◀─┘
        │
        ├─ reacts 👍 / replies with errors, updates the issue's table
        ├─ commits _data/signups/<id>.json
        └─ rebuilds and deploys the Jekyll site
```

## Set up (once)

1. Create a repository and push these files to `main`.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. If `main` has branch protection, let `github-actions[bot]` push to it.
   The bot commits sign-up state there.
4. Organization repos only: if the org caps workflow permissions at read-only,
   allow read and write under **Settings → Actions → General**.

## Make a list

```bash
pip install -e ".[dev]"
python -m signup new potluck        # copies templates/list.yml with a fresh id,
                                    # and adds the list's page, lists/<id>.html
$EDITOR _data/lists/potluck.yml     # the template has inline instructions
python -m signup validate
git add _data/lists/potluck.yml lists/ && git commit -m "Add potluck"
git pull --rebase && git push       # the bot commits too; see below
```

Within a minute or two the bot opens the issue and the site shows the list.
The home page links to every list; each list lives at `/lists/<id>/`.

**Always `git pull --rebase` before you push.** The bot commits sign-ups to the
same branch, so your push is rejected whenever it has committed since your last
pull. `git config pull.rebase true` makes plain `git pull` do the right thing.
Share either link. To change items, the title, or the closing date, edit the YAML
and push. **Never change `id`.** A new id is a new list.

Commands are in [COMMANDS.md](COMMANDS.md).

## Layout

| Path | What it is | Who writes it |
|---|---|---|
| `_data/lists/*.yml` | List definitions | You |
| `_data/signups/<id>.json` | Who claimed what, plus a precomputed view for the site | The bot only |
| `lists/<id>.html` | One stub page per list (renders with `_layouts/signup.html`) | `new`, kept in sync by the bot |
| `templates/list.yml` | Template with instructions | You |
| `signup/` | The bot (Python, only depends on PyYAML) | |
| `_includes/`, `_layouts/`, `index.html`, `assets/` | The Jekyll site | |
| `.github/workflows/signup.yml` | Bot and deploy | |
| `.github/workflows/ci.yml` | ruff, pytest, validate | |

## How it stays correct

- **Serialized.** All bot runs share one concurrency group, so they never overlap.
- **Catch-up, not per-event.** Each run processes every comment newer than
  `last_comment_id`, oldest first. GitHub may drop a queued run when several
  pile up; the next run picks up those comments. Re-running is always safe.
- **Fair deadlines.** A claim counts if its comment was *posted* before
  `closes`, even if the bot processes it later.
- **Replies are idempotent.** Each bot reply carries a hidden marker naming the
  comment it answers, so a replayed comment never gets a second reply.
- **One writer.** Only the bot writes `_data/signups/`. Comments are requests;
  the JSON is the record. If a state file is lost, the bot re-links the
  existing issue by the `[list: <id>]` in its title, and doesn't open a duplicate.
- **Comment text is never executed.** It's parsed as data. User notes are
  escaped on the site and in the issue, and `@mentions` in notes are defused.

## Limits

- Everyone who signs up needs a GitHub account, and sign-ups are public.
- Updates take about 1–2 minutes (Action plus Pages deploy).
- Closing by date happens on the hourly run. Late claims are rejected regardless.
- Editing or deleting a comment does nothing; use `/unclaim`.
- The bot manages each list issue's body and open/closed state. To stop sign-ups,
  comment `/close` instead of closing the issue by hand, which the bot would undo.

## Later: a sign-up form on the site

[COMMANDS.md](COMMANDS.md) ends with a grammar for tools that generate commands.
A form can build the `/claim …` text today. The site's **Sign up** button already
copies it and opens the issue. Posting it for the user needs either a prefilled
*new issue* (the bot would also need to read commands from new issues) or a small
OAuth token-exchange service.
