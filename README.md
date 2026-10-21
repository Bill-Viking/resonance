# Resonance

**See what you have said online and how it may land. Fix it in your own voice. Privately, on your own computer.**

`0.1.0-preview.2 · source preview`

Resonance is a small app that runs on your own computer. It gathers your own posts, comments and replies into one place, marks the exact words that may land badly with the audience of that account, and helps you keep, change or remove each one. It has no account to create, no cloud behind it and no tracking inside it.

This preview is for someone who can run one command, and who either pastes their own text or has an AI assistant that can use a browser. A double-click installer comes later.

**Account checks still need your external assistant.** This preview does not include a browser extension or a “Check now” button that reads your accounts automatically. Try the separate sample notebook before adding personal information. See [the release notes](CHANGELOG.md) for what is ready and what remains.

## Start it

You need [Python](https://www.python.org/downloads/) 3.9 or newer. Get this folder onto your computer (**Code → Download ZIP**, or `git clone`), open a terminal in it, and run:

| Your computer | Run |
| --- | --- |
| macOS or Linux | `./resonance` |
| Windows | `resonance.bat` |

On Windows, run `py -3 -m pip install -r requirements.txt` once first (`python -m pip …` if you have no `py`). It adds the time-zone data Windows does not include.

Your browser opens Resonance at `http://127.0.0.1:4317`. Leave the terminal window open while you use it; press Ctrl+C there to stop. Everything you saved is kept.

## First run

Resonance opens on a Welcome page with three doors.

1. **See it with sample posts.** Ten seconds, nothing to set up. The samples live in their own file, apart from anything of yours, under a banner that says so. **Clear samples** removes them.
2. **Check something I wrote.** Paste a post, a comment or a reply and see what Resonance marks, and why.
3. **Bring in my accounts.** One account at a time: pick the platform, paste your profile link or handle, say whether the account is under your real name or an alias and how daring it may be, then sign in to the platform in your own browser. Resonance never sees your password.

## How your posts get in

- **Paste them.** Always available, no assistant needed.
- **Your assistant brings them.** If you use an AI assistant that can drive a browser and run a command, give it one sentence — *"Read ASSISTANT.md in my Resonance folder and run my Resonance check."* — and it does the rest: it reads your own posts where you are signed in, saves what it found, and your tiles fill in. [ASSISTANT.md](ASSISTANT.md) is the whole contract it follows, written to be read by you too. The short version: your own writing only, never private messages, and nothing on a platform is changed without your yes, at that moment, for that item.
- **Your own data archive.** Importing a platform's "download your data" file is coming next.

Saved accounts can be on X, Reddit, YouTube, Instagram, Facebook, TikTok, LinkedIn, Discord and Substack, plus Gmail and Outlook mailboxes. Saving an account connects nothing. What Resonance can show depends on what you paste or what your assistant was able to read, and every check says plainly what it covered and what it could not reach.

### Proven with

An assistant is listed here only after it has run the whole road on this contract: read posts, saved a report, tiles appeared.

| Assistant | Computer | What was run | When |
| --- | --- | --- | --- |
| *No rows yet.* | | | |

## What you can do with a post

Each post gets one card showing the saved text with the flagged words marked. Assistant-supplied context can show the parent conversation or attachment, when it was inspected, and a small local image preview. Uninspected or missing context is labeled; the app does not fetch or analyze media. **Keep** it, **Modify** it, **Delete** it, or **Snooze** it for a day. Modify and Delete each offer two roads: do it yourself, with the original opened for you, or ask your assistant. **Retract** fills in a withdrawal notice you can reword.

Resonance is careful about what it claims. Clicking a button never changes a platform. "Deleted" appears only after the result was checked on the platform itself; until then the card says what is true — asked, done by you and not yet verified, or blocked, with the reason and the next move.

## Accounts and boundaries

Every account gets a daring level — careful, balanced or bold — and one question: is it under your real name, or an alias? Accounts under your real name always stay safe-for-work, whatever the daring level. Accounts you have not classified are treated the same way. An alias can relax that on purpose. An alias is not a promise of anonymity.

## Where your data lives

In one folder on your computer, outside this source folder:

| Your computer | Data folder |
| --- | --- |
| macOS | `~/Library/Application Support/Resonance` |
| Windows | `%LOCALAPPDATA%\Resonance` |
| Linux | `$XDG_DATA_HOME/Resonance`, or `~/.local/share/Resonance` |

Resonance prints the folder it is using when it starts, and **Settings → Your data** shows it with its size and the last automatic backup. Set `RESONANCE_DATA_DIR` to use a different folder. Choose a private local folder that none of your sync tools shares: Resonance rejects repositories and the cloud-sync paths it recognizes, but it cannot detect every folder another program syncs.

**Upgrades protect your data.** Before a new version changes the database it saves a copy under `backups/auto` in the data folder and keeps the last five. If the change fails, the copy is put back and Resonance tells you in one sentence.

**Keeping a copy is your choice.** Nothing leaves the folder automatically, and **Export backup** writes a file whenever you ask. Exports are readable JSON, including account names, saved text, context images, drafts and requests. This edition does not encrypt exported backups; keep them in a private location or encrypt them with your own trusted tool. A restored backup never carries permission to change anything; pending requests ask for your yes again.

## What it does not do

- **It does not understand everything.** The built-in checks are a limited set of English wording rules. They do not read images or video, check facts, or know the whole conversation. No flags means those rules matched nothing in the text that was saved — not that an account is safe.
- **It does not score you.** No grades, no reputation number, no judgment of what kind of person you are. A finding describes how words may read.
- **It does not read private messages.** Direct messages and private chats are out of scope, for you and for your assistant.
- **It does not act on its own.** The app contacts no platform, holds no password or token, and has no AI model inside. Collecting posts and making a change you approved are your assistant's work, in its own tools, under its own confirmations.
- **It does not sign you in.** A saved account is a note about where you post, not a connection.

## Security

Resonance listens on this computer only, answers only to its own address, and accepts changes only from its own page. It has no sign-in of its own, so it is as private as the computer it runs on. [SECURITY.md](SECURITY.md) says exactly what is checked, where the protection stops, and how to report a problem.

## Development

```
python3 -m unittest discover -s tests
node tests/test_ui_actions.cjs
node tests/test_ui_communication.cjs
node tests/test_ui_today.cjs
```

Run `python3 server.py --open` from this folder, with `RESONANCE_DATA_DIR` set to a temporary folder so your own data is never touched. The Node suites check views and handlers against a mock page; they do not replace a look in a real browser.

Product direction is in [PRODUCT.md](PRODUCT.md). The reference behind the assistant's contract is in [reports and coverage](docs/MONITORING.md) and [decisions and verified outcomes](docs/ACTIONS.md). What each platform's official access allows is in [INTEGRATIONS.md](INTEGRATIONS.md). AI agents working in this folder start at [AGENTS.md](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE).

Resonance is an independent project. It is not affiliated with, endorsed by or sponsored by any platform it names; their names and marks belong to their owners.
