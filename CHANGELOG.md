# Changes

## 0.1.0-preview.5

Per-post edit availability shows dated assistant observations, concrete reasons
and explicitly observed deadlines. Unknown stays unknown. Assistant edit starts
and the read-only last look require fresh available controls as well as the
account match; no platform time rule is guessed. Reports and imports retain
history without granting execution authority.

When editing is unavailable, Keep and separate follow-up reply/post drafts are
offered. Drafting/copying preserves the original and existing request; publishing
is a separate reviewed workflow. No reply/post executor or automatic substitution
is added. Schema 4 protects the optional record format without changing old rows.
The Res logo and existing style remain unchanged.

Account checks still require an external assistant; the automatic browser reader
is unfinished. Assistants must supply edit availability with their fresh account
observation and require `edit_current` in the final read-only check before editing.

## 0.1.0-preview.4

Assistant edit/removal attempts require a fresh local comparison of the intended
account, observed active identity and exact source author. The preview shows both
identities, the selected boundary and check date. Missing, mismatched, expired,
changed or imported-only checks hold the action. Checks expire after five minutes;
the assistant still rechecks its browser immediately before acting. No browser
sign-in access, credentials, provider calls or automatic account switching added.

Database upgrades preserve existing records and add a local identity guard table.
Portable backups retain observation history, but never permission to start an
action; imported requests need a fresh check. Assistants must use the updated
`--check-identity` and `--start-request --identity-check-id` contract.

The Res logo is unchanged. Reading accounts and carrying out approved changes
still require an external assistant; the automatic browser reader is unfinished.

## 0.1.0-preview.3

Welcome has tighter panel padding and spacing between its heading and three
choices, including at narrow widths. Text sizes, wording, keyboard focus,
entry paths and the existing style remain the same. The Res logo is unchanged.
The app shell refreshes its cached stylesheet.

This is a presentation update. Account checks still require an external
assistant; the automatic browser reader remains unfinished.

## 0.1.0-preview.2

Finding cards have clearer borders and raised backgrounds in every palette.
Finding text is easier to read, and wording, source and context links stay
underlined with stronger keyboard-focus and hover emphasis. Selected findings
retain their existing highlight. The app shell refreshes its cached stylesheet.

The Res logo is unchanged. This is a presentation update: review behavior,
context evidence and the source preview's external-assistant requirement remain
the same.

## 0.1.0-preview.1

First source preview. Runs locally with synthetic practice posts, text checks,
saved account boundaries and assistant-supplied reports. Keep, edit and deletion
requests retain their exact source, evidence and verified outcome. The app makes
no outgoing connections and holds no credentials or AI model.

This preview adds:

- A read-only `monitor.py --profile` command, optionally scoped with `--account`,
  for saved settings and historical verified social decisions. It does not load
  mailbox content or authorize a platform change.
- Context cards for parent text, conversations and attachments. A saved local
  image, inspection state and observation date stay separate from authored text.
  Changed context reopens stale decisions; a new check date alone does not.
- Exact X reply links recognized as individual sources, with existing author
  and execution checks retained.
- Local server startup without an unnecessary reverse DNS lookup that could
  delay launch when the resolver is unavailable.

Account collection and approved platform changes still require an external
assistant with its own browser tools. The browser reader, automatic “Check now”
control, archive importer and accepted-learning storage are not included.

The automated suites and a synthetic browser walkthrough were run on macOS.
Windows and Linux checks are included in CI; a successful run is separate from
a human first-run walkthrough on those systems.
