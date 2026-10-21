# Changes

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
