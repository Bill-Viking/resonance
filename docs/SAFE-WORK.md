# Work Safe audit

This is an assistant-led review of the user's own public writing for content
that may be unsafe to leave visible under a named or not-yet-classified identity.
It uses the normal Resonance check and report. The app's Work Safe card prepares
a request for the assistant; it adds no data field or authority to change a platform.

## Start and scope

Run one only when the user asks directly, for example: “Run a Work Safe audit on
[platform/account] for [scope]” or “Run a Safe Work audit …”. Treat the named
platform/account and scope as the current limit. Read the current account settings and follow the whole
[assistant contract](../ASSISTANT.md), including `--profile`, `--review-context`,
`--work`, identity checks, and action guards where relevant. Do not widen the
requested scope or infer permission from an old report, saved request, Keep, or
the audit name.

Review the full requested scope across accessible public surfaces; do not replace
it with a sample or a narrower pass. If the work takes time, give dated progress
checkpoints that state what is covered and what remains. Respect platform access
limits and mark inaccessible areas plainly. Review only the user's own public
posts, comments and replies. Do not inspect inboxes, DMs, private chats, or another person's account. For each item, inspect
the complete text and available thread context, plus every attachment that can
be read in the authorized view. Treat quoted text in context before attributing
it to the user. If media or context cannot be inspected, say so in coverage and
leave that portion unchecked. Never transcribe or decode exposed private text,
identifiers, QR codes or barcodes into a finding; describe their type and
location instead.

Treat the main post, each of the user's own comments and replies, and each
attachment as separate review units. A harmless parent does not clear a reply;
a clarification reply does not clear the original. Keep each authored item's
exact source and ownership distinct. Record attachment inspection separately in
coverage; do not count one attachment or a repeated view as another authored
item. A thread is not fully reviewed when some own replies or media remain unseen.

Plan coverage by surface before traversal: own posts, own top-level comments on
own and other people's posts, own nested replies on own and other people's posts,
and media. Use the user's own activity log or equivalent to locate their comments
elsewhere; a post-feed count is not a comment census. A search box that searches
activity categories instead of text supplies no evidence that an item is absent.
Choose an inclusive comment view and expand reply controls when available; record
filters, loading failures and any remaining collapsed replies. Navigation snippets
do not count as an inspected item or justify a disposition.

For Facebook, run [the comment discovery procedure](FACEBOOK-DISCOVERY.md).
Capture whole own-activity batches with full View hrefs and build the persistent
inventory before selecting findings. Do not require the user to name a person,
topic or missed reply. Preserve every candidate, including those sharing a parent,
and use `check_coverage.py` with `--discovery` for completion and handoffs.

If the user ends historical traversal, stop collecting older items and move
promptly to the findings already collected, within the user's remaining scope
and action authority. State the reading boundary and unchecked remainder. Do not
keep scrolling to finish the old scope or treat ending traversal as resolving
the findings. If the user instead pauses or stops all work, preserve the pending
state and stop; ending collection is not new permission to act.

## What to review

Apply the account's saved boundaries. Named and unclassified accounts stay SFW,
regardless of daring. A deliberately relaxed alias (`sfw_only: false`) is not
subject to that SFW boundary, but the rest of the normal contract still applies.
Review for sexual, graphic or crude material; threats, abuse or slurs; visible
private or work-confidential text; and the user's saved brand-specific
boundaries.

Context matters. Ordinary opinions, fiction, costumes or appearance alone are
not violations. Fiction and satire are context, not automatic exemptions from
reviewing the user's own words against the saved audience and boundaries.
Distinguish a quotation, critical analysis, a character's dialogue and the user's
own endorsement-like wording. Consider both the available context and how the
specific wording could read without that context, especially on a named, careful
account. Explain the concrete ambiguity; do not invent an audience reaction or
infer the user's political identity, beliefs or character. An ordinary political
opinion or an interest in a fictional villain is not itself a finding. There is
no political, franchise or character blacklist.
When the meaning or boundary is ambiguous, report it for review rather than
claiming a violation. Respect an explicit prior Keep for unchanged concerns;
new SFW risk on a named or unclassified account still gets `attention`, as the
assistant contract requires. An unreadable attachment is unchecked, not clear.
No findings means only that this review found no concerns in the inspected
scope; it does not certify the account or its other content as safe.

An earlier assistant-authored explanation, signature or banner is context only,
not a user Keep or evidence that an issue is fixed. Reassess a fresh concern on
its merits. A current explicit request to remove an exact item remains a removal
request even if the assistant thinks the item is harmless or previously clarified.
Do not silently mark it Keep, substitute a reply, or require the user to prove a
policy violation. Follow the existing request workflow and report any missing
confirmation or other hold. A previous user Keep does not cancel a later explicit
removal request; only the user's changed choice changes that requested disposition.

## Report and actions

Save through the existing private report workflow and unchanged report schema
in [ASSISTANT.md](../ASSISTANT.md). Use a run ID beginning
`safe-work-<timestamp>`. State “Work Safe audit” (or “Safe Work audit”) in the run summary and account
coverage. Include every saved account exactly once; mark accounts outside the
requested scope as `blocked` with an honest explanation that they were not
reviewed. Report real timestamps, complete coverage limits and only items
actually inspected. Counts of deleted items include only removals verified on
the platform during this run.

Keep these meanings distinct in the private report's existing summary and
coverage fields and in the user's update. These are reporting terms, not new
schema fields or permission states:

| Term | Evidence required |
| --- | --- |
| Reviewed | This item's text or named attachment was actually inspected; say which. No action is implied. |
| Flagged | A specific inspected concern was raised. It is not a removal request or a verified result. |
| Queued | The exact requested change exists in the current Resonance work record. A suggestion or chat request alone is not a saved queue entry. |
| Waiting for confirmation | Required exact-item or tool confirmation is missing. Name the hold; no provider attempt has started. Other blockers remain named separately. |
| Attempted | The durable attempt was started; a provider click may have occurred, but the result is not yet verified. Interrupted or uncertain work stays verification-only, with no repeat attempt. |
| Verified removed | The provider result was checked for the exact item and recorded with its receipt. Only this contributes to `deleted_count`. |

Use `pending_deletion_count` for requested removals still waiting, and describe
attempted but unresolved actions separately. Never add invented state values to
the report or report all flagged items as deleted. Review coverage and action
outcomes answer different questions, including when an earlier assistant said a
thread had been handled.

### Coverage ledger

Keep one private JSON sidecar per account and review run in the application data folder, separate from the
unchanged monitoring report. Start it with the user's scope and every reported
miss, even when a source link is unknown. Reconcile it at checkpoints, before a
handoff and before saying the audit is complete. Each exact own item has its own
row. Keep only coverage, source and outcome metadata there; collected post text
and media belong in the report. Never resolve another row from a parent's outcome. For a descendant on the
same parent, record the actual provider evidence for that descendant; a failed
link, missing snippet or inaccessible thread is not a verified removal.

Use the installed skill's `scripts/check_coverage.py`, or from the active checkout:

```sh
python3 skills/res-work-safe/scripts/check_coverage.py PRIVATE_LEDGER.json
```

The helper reads that ledger and, when supplied, its discovery inventory. It opens no database, changes no file, contacts
no provider and grants no action authority. It checks consistency of recorded
evidence, not the truth or exhaustiveness of an assistant's observations. The
assistant must compare the ledger with the user's request, prior unresolved
targets, actual navigation and current work records; an omitted item cannot be
discovered by the checker. A passing check is scoped completion, never an account
safety certificate. The normal private report remains required.

Facebook comment/reply checks also supply `--discovery PRIVATE_INVENTORY.json`
from [the discovery procedure](FACEBOOK-DISCOVERY.md). That adds traversal and
candidate-to-target reconciliation. The helper refuses completion without the
inventory when the ledger names a Facebook account and includes any comment or
reply surface. Facebook posts-only and other-provider ledgers can use the
single-file invocation. Discovery metadata still cannot prove what the provider
never exposed.

The schema has exactly these top-level fields:

| Field | Content |
| --- | --- |
| `version` | Integer `2`. |
| `account_url` | The verified stable HTTPS profile URL for this one account, without credentials and with an ASCII hostname. Facebook uses a supported stable profile, never `/me/` or a short alias. |
| `scope_id` | A nonblank run identifier, at most 100 characters, shared with this account’s discovery inventory. |
| `scope` | Nonblank user-requested scope and boundary. |
| `surfaces` | All six rows named below, each with `status` and nonblank `evidence`. |
| `targets` | One row per exact intended item, with the fields below. |
| `unresolved` | Nonblank descriptions of user-reported misses or scope questions not yet reconciled; empty only when none remain. |

Keep one ledger per account and review run. For a multi-account request, reconcile
every ledger before claiming the whole requested check is complete. Version 1
ledgers are refused: preserve their targets, outcomes and evidence, then supply
the verified account and run identifier explicitly in a version 2 ledger. Never
guess that binding or restart completed actions to upgrade the metadata.

The six surface keys are `posts`, `comments_on_own_posts`,
`comments_on_others_posts`, `replies_on_own_posts`, `replies_on_others_posts`,
and `media`. A surface's status is `complete`, `partial`, `blocked` or
`outside_scope`. Its evidence names the actual reading boundary and any gaps,
or the user's explicit exclusion. Inaccessible requested content is `blocked`,
never `outside_scope`. When historical traversal stops, record the reading
boundary and leave the original scope `partial`; do not silently redefine it
to mean the few items already collected. At least one surface must be in scope.
A Facebook comment/reply target cannot be listed while all four comment/reply
surfaces are outside scope. Correct the recorded scope from the user's request;
the helper never widens it automatically. Facebook short aliases must first be
resolved to a supported stable profile through the assistant's browser. The
helper follows no redirects. Source and receipt URLs also need ASCII hostnames;
use the verified address-bar URL rather than a hand-written look-alike hostname.

Every target requires these fields:

| Field | Content |
| --- | --- |
| `id` | Unique local identifier. |
| `source_url` | Exact own-item HTTP(S) source, or empty while unidentified. Never a parent in place of its reply. |
| `author_verified`, `text_inspected` | Booleans describing the actual own-author and complete-text observations. |
| `media` | `none`, `inspected`, `unchecked` or `unavailable`. |
| `requested_action` | `review`, `remove` or `edit`, preserving the user's current choice. |
| `state` | `unresolved`, `reviewed`, `pending`, `attempted`, `verified_removed`, `verified_edited` or `kept_by_user`. These are sidecar notes, not app request states. |
| `receipt_url`, `receipt_note` | Empty before verification; observed result link and exact-item verification evidence afterward. For `kept_by_user`, the note records the user's changed choice, never an assistant's preference. |

`pending` includes required confirmation, an unsaved chat request and other named
holds; explain which in the normal report. `attempted` stays verification-only.
Each verified state requires full review evidence and a result link and note.
Only the matching requested action can resolve a target; an edit does not satisfy
a removal request. No parent or previous corrective reply resolves another row.
The helper rejects duplicate IDs and exact duplicate links; the assistant must
also deduplicate equivalent provider links by item identity.

Exit `0` means the recorded scope and actions are complete; `1` means valid but
incomplete; `2` means invalid or unreadable. Output separates `review_complete`
from `actions_complete` and lists blockers. Missing requested surfaces, unresolved
targets, unchecked media and unverified attempts prevent a complete result.
If the helper is missing, record that verification gap and keep the pass partial
until it is available. Do not invent a monitor flag or bypass action guards.

Report the resolved collected actions and the remaining review gaps separately.
Earlier verified work remains verified and must not be repeated. A user-reported
miss reopens the coverage question even if an earlier close said “complete.”

### Action authority

The audit request itself never authorizes an edit, deletion or other platform
action. There is no automatic permanent deletion. Follow the exact-item,
authorization, identity, attempt, tool confirmation and verification requirements
in the assistant contract and the tool in use. Normally, get the user's yes for
each item at action time. An explicit current delegation may cover a defined
scope for recoverable removal or the fallback edit below only where the tool
permits preauthorization. Recoverable removal means the user can undo it through
a restore path verified for that exact platform surface. Treat an unknown or
unverified restore path as permanent. Permanent deletion always needs the user's current yes
to that exact item under the contract, as well as any tool-required confirmation.
A scoped delegation never replaces a mandatory action-time confirmation. If confirmation is required, wait for it;
continue only the independent review work that remains in scope.

For a deletion request targeting the user's own conversation comment, do not
silently substitute an edit or reply. If the platform does not permit deletion
but does permit editing, use the edit fallback only when the user has authorized
it for that item or through an explicit current delegation whose scope covers
it and whose tool permits preauthorization. Use the existing Modify flow in
Resonance to create a distinct edit request with the exact replacement; never
reuse the delete request. It has its own attempt, fresh identity and edit-control
checks and any required confirmation. Replace only the user's own comment with
exactly these two lines, with no banner, signature, attribution or repository URL:

```text
Comment withdrawn.
Edited by RES.
```

Do not add a separate public note or comment unless the user explicitly chooses
that separate route and its own publishing workflow permits it. If deletion
succeeds, leave no public banner, attribution, replacement, reply or announcement.
Do not claim an edit removed text embedded in an image, or erased platform edit
history. Report the outcome the platform actually shows.

## Synthetic acceptance cases

| Case | Expected handling |
| --- | --- |
| Non-graphic fictional conflict clearly framed as fiction | Use context; fiction or a villain alone is not a violation. Graphic depictions still need an SFW review. |
| Quoted insult followed by clear criticism of the quote | Attribute the words using the full thread; do not treat the quote alone as the user's endorsement. |
| Readable nonpublic employee identifier on a work badge | Flag as `attention`; describe the badge and location without copying or decoding its details. A company logo alone does not establish exposure. |
| Monitor attachment unreadable | Mark attachment unchecked in coverage; do not infer that it is clear. |
| Explicit profanity on a named account | `attention` under the SFW boundary, even if daring is bold. |
| Same profanity on alias deliberately set `sfw_only: false` | Do not flag solely as an SFW violation; assess other saved boundaries normally. |
| Unchanged concern the user explicitly Kept | Preserve that choice; do not reopen the same concern absent new SFW risk or changed evidence. |
| Broad current permission to delete concerning items | Still obey action-time tool confirmations and exact-target guards; audit mode itself starts no deletion. |
| Comment deletion unavailable; edit available and fallback explicitly authorized | Start a distinct guarded edit, replace only that comment with the exact two-line text above, and report that edit; do not post a reply or separate note. |
| Deletion succeeds | Leave no public banner, attribution, replacement, reply or announcement. |
| A harmless fictional scene has an own reply, “I'd treat my real opponents just like that,” on a named, careful account | Inspect the reply and scene separately. Explain the specific ambiguity for the saved audience; fiction alone does not clear the reply. Do not infer a political identity or claim an actual threat without evidence. |
| A villain's line is quoted in a clearly critical analysis, or the user says “The villain is well written” | Retain the critical or artistic context. Do not invent endorsement, a concern or a blacklist merely from the character. |
| An ordinary political preference with no other concern against the saved boundaries | Do not flag it solely for its political position; do not infer or save a political identity. |
| The parent is unremarkable; an own reply is crude; one attachment is readable and another is not | Review each own text separately, raise the supported concern, inspect the readable attachment, and name the unseen remainder. Do not call the whole thread clear. |
| A previous assistant clarification and branded banner remain, but the user raises a new concern about the original | Reassess the original and relevant own replies. The assistant's explanation is not a user Keep or proof the concern is resolved. |
| The user says “Remove this exact post,” which an earlier assistant called harmless or the user previously Kept | Preserve the requested removal, use the normal request workflow, and name any hold. Do not substitute Keep or invent a violation to justify the request. |
| The user says “Stop going farther back; handle what you found” | End collection at that boundary and turn to the collected findings under current action authority. Do not continue the old traversal or infer deletion permission. |
| The user says “Stop everything; I'm stepping away” | Preserve pending work and stop. Do not reinterpret this as permission to dispose of findings. |
| A flagged post has a saved delete request but permanent-delete confirmation is missing | Report flagged, queued and waiting for confirmation; zero attempts and zero verified removals. Do not bypass the exact-item confirmation. |
| A removal attempt returns an error; another exact item has a verified removal receipt | Report the first as attempted and unresolved, for verification only; the second as verified removed. Count one verified removal, never two, and do not retry the first. |
| A user's parent post was removed, but their reply on another person's post remains | Keep the reply as an independent unresolved target. The parent receipt cannot clear it. |
| Hundreds of own posts were inspected but comments and nested replies elsewhere were not | Mark those surfaces partial or blocked within the requested scope; no audit-complete claim. |
| The user names a missed reply without a link | Keep an unresolved placeholder through search and handoff; a zero-result category search does not resolve it. |

