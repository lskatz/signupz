# Sign-up commands

You sign up by commenting on a list's GitHub issue. Each list has an issue titled
`<title> [list: <id>]`. The bot only reads comments on its own issues.

## Rules

- Put each command **on its own line**. One comment can have several commands; they run top to bottom.
- Commands and item names ignore case: `/Claim Dessert` works.
- Lines that don't start with `/` are ignored, so you can chat in the same comment.
  Quoted lines (`> /claim …`) and lines inside ``` code fences are ignored too.
- **Item names** are the words in the issue's *Command name* column, e.g. `dessert`, `paper-plates`.
- The bot reacts to your comment: 👍 all commands worked, 👎 none worked, 😕 some worked.
  If anything failed, it replies saying why.
- **Editing or deleting a comment does nothing.** Use `/unclaim` to undo a claim.
- First come, first served: comments are processed in the order they were posted.

## Everyone

| Command | What it does |
|---|---|
| `/claim <item> [note]` | Take one slot of `<item>`. Everything after the item name is your note, up to 200 characters. Example: `/claim dessert pecan pie`. You can claim several different items, or the same item more than once (one slot per `/claim`). Rejected if the item is full or the list is closed. |
| `/unclaim <item>` | Give back your most recent slot of `<item>`. Not allowed after the list closes. |
| `/list` | The bot replies with who has signed up for what. |
| `/help` | The bot replies with this command list. |

## People with write access to the repository

| Command | What it does |
|---|---|
| `/remove @user <item>` | Remove all of `user`'s claims of `<item>`, even after the list closes. |
| `/close` | Stop sign-ups now, before any closing date. |
| `/reopen` | Allow sign-ups again. Overrides the `closes` date until the next `/close`. |

To add, rename or resize items, edit the list's YAML file and push. Don't change the `id`.

## When a list closes

A list closes when its `closes` date passes (the bot checks every hour and on every
comment) or when someone with write access comments `/close`. The bot then posts a
final summary once and closes the issue. Comments on a closed issue still reach the bot,
so `/reopen` works there.

## Format reference (for tools that generate commands)

```
line     := "/" command [ " " args ]
claim    := "/claim" SP item [ SP note ]
unclaim  := "/unclaim" SP item
remove   := "/remove" SP [ "@" ] user SP item
other    := "/list" | "/help" | "/close" | "/reopen"
item     := [a-z0-9][a-z0-9_-]{0,39}          (case-insensitive)
note     := any text, up to 200 characters, to end of line
```

A web form that writes this text into a comment, or into a new issue later, works
without any change to the bot.
