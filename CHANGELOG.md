# Changes

## Unreleased

## 0.1.0-preview.8 — source preview

- First paste starts with your writing, then asks which account it belongs to.
  A second step confirms the account boundaries before saving and checking.
  Interrupted saves preserve the submitted text and distinguish a confirmed save
  from an uncertain response; refreshing never repeats an uncertain request.
- Keep appears beside the saved post and opens its saved History result. Today
  shows kept posts even when nothing needs attention. Local rule summaries no
  longer count as extra concerns when their individual findings are displayed.
- Paste text becomes the primary action when account checks are unavailable.
  Samples are also reachable from Settings after you have saved real posts.
- Exact YouTube community comment and reply links retain their source identity.
- Windows sample-workspace tests allow slow local requests to finish and close
  their HTTP connections before temporary-data cleanup.

This remains an early source preview. Desktop candidates are unsigned; physical
first-user acceptance and browser-store distribution are separate release gates.
The app still makes no outgoing connections, holds no credentials and has no
model inside. Database schema 6 and backup format 3 are unchanged.

## 0.1.0-preview.7 — source preview

- A self-contained desktop candidate opens Resonance without a separate Python
  installation, with Open and Quit controls and a plain occupied-address error.
  Builds are native to Mac or Windows. Signing, store distribution and physical
  first-user acceptance remain pending; this source preview is not a finished desktop release.
- The separate Resonance Reader candidate supports explicit own-account
  discovery and bounded Check now reads for X posts and YouTube comments.
  A discovered identity is saved only after confirmation and account boundaries.
  Instagram, Substack and other saved platforms retain manual and assistant routes.
- Check now keeps Reading and Saving separate, records dated coverage receipts,
  exposes exact saved text and revisions, and reconciles a lost save response
  before retrying. Cancellation or disconnection discards unsaved results.
  The reader never changes anything on a platform and has no store listing yet.
- Paste my first post continues through account setup to the resulting review.
- Database schema 6 and portable backup format 3 preserve reader receipts and
  exact-text history. Old backups still import. Restore validates the meaning
  and links of records, not just their hashes. An upgrade retains its original
  pre-upgrade snapshot even when several migration steps run.

- A public comment or reply you leave addressed to RES (`RES: …`, `Hey RES, …`,
  `@RES …`) can be brought in by your assistant as a local review request, with
  an evaluation and an optional proposed reply or edit. Nothing watches your
  accounts and nothing is triggered instantly; assistant checks and imports are
  required. A request is a note: it grants no access, posts nothing, and leaves
  every edit, deletion and reply guard unchanged. Adds database schema 5 and an
  optional `request_inbox` key in backups, retained in format 3.

## 0.1.0-preview.6

This preview brings together the tested account-review improvements and the
RES text-branding workflow:

- Focused assistant reviews can read one account's saved coverage, exact post
  links, past choices and pending-work summaries in one read-only command.
  The Facebook assistant workflow starts from exact sources and batches its
  first findings. History limits and coverage gaps remain explicit.
- Assistant-led checks require looking for sensitive text in readable media.
  Cards show bounded privacy observations and concrete crop or opaque-redaction
  suggestions. Missing checks stay unknown, and exposed-image previews are
  refused. Res does not analyze pixels or replace media itself.
- After an attempted change is verified as still needed, the retired request
  stays in history and the user can make a fresh choice. Active work still
  blocks duplicate requests; no old attempt or permission is revived.
- Older requests without identity history no longer block the final read-only
  check of a complete modern request. Absent history grants no authority;
  lifecycle, source, identity and edit-availability safeguards remain required.
- Threads joins saved account setup and assistant-supplied coverage reports.
  Copied profile links save without tracking parameters. Saving an account
  connects nothing; there is no Threads reader or assistant edit/delete support.
- The app wordmark and header spacing are smaller; icon artwork is unchanged.
- The new AUTHORING.md workflow uses the RES text signature and actual repository
  URL for standalone creations, a compact signature for short replies, and no
  added branding when requested. Graphic banners require an explicit request.
  This is assistant guidance, not automatic formatting or permission to publish.

The experimental browser extensions, unfinished Facebook reader and automatic
Check now workflow are not included. Account checks still need an external
assistant. The app makes no outgoing connections and holds no credentials or
AI model. Schema 4 and portable backup format 2 are unchanged.

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
