# Find Facebook comments and replies

This is the discovery road for an external assistant doing an authorized
Facebook review. It starts from the signed-in user's own Comments activity log,
not a list of people, topics or links the user must supply. The assistant uses
its browser tools. Resonance itself makes no outgoing connection and holds no
credential. This does not add Facebook support to the separate browser extension.

## Enumerate before choosing findings

1. Read the saved account, current coverage and work through `--profile`,
   `--review-context` and `--work` as described in [ASSISTANT.md](../ASSISTANT.md).
   Establish the requested time/item boundary. A previously stopped historical
   pass stays stopped unless the user asks to resume it. Check the active account
   against the saved stable profile. A profile merely being visible is not proof
   of sign-in.
2. Open **that account's Activity log → Comments** using its visible controls.
   Include all people. Clear person/topic/text filters; a date filter may express
   the requested scope. The activity-log search can search categories rather than
   comment text, so an empty search result proves no comment is absent. Do not
   use notifications, the profile feed or known problem threads as the census.
3. Capture every currently loaded activity row, before judging its content. Use
   the read-only DOM function in
   `skills/res-work-safe/scripts/capture_facebook_activity.js` with the browser
   tool's supported page-evaluation API. Supply the current account, scope, batch
   ID, observation time, identity observation and actual boundary evidence. Its
   result contains navigation metadata, not comment text. Use the full `href`
   on each **View** link: an accessibility summary may omit its query string,
   including the exact `comment_id` and `reply_comment_id`.
4. Save that snapshot privately and build the navigation inventory:

   ```sh
   python3 skills/res-work-safe/scripts/discover_facebook.py PRIVATE_SNAPSHOT.json
   ```

   The command prints JSON. Save it inside the application's private data folder.
   For later batches, supply `--prior PRIVATE_INVENTORY.json`; save the new result
   separately, validate it, then replace the prior inventory. Do not redirect
   output onto a file the command is still reading. Nothing is saved to the app
   database by this command. Its exit `0` means valid input, not complete review.
5. Scroll or use the provider's next/load control normally, then capture the next
   batch. Record the actual reading boundary after each batch. Repeated rows are
   recognized within the same run, but distinct comments and replies sharing a
   parent remain separate candidates. Do not filter for likely concerns, familiar
   names, RES mentions or keywords before collecting. Grouping navigation by
   parent is useful; collapsing its candidate rows is not.
6. Continue to the requested boundary or an observed end. A loading indicator,
   failed request, repeated batch with no progress, inaccessible date range or
   unmatched row is a gap. Silence or a blank viewport is not an end marker.
   If the provider prevents progress, preserve the inventory and report partial
   coverage with the last successful boundary. Do not bypass access controls or
   silently replace the requested scope with the loaded batch.

The capture function recognizes the documented English Comments view. If language
or markup changes, treat unrecognized rows/controls as a discovery gap. Inspect
the current UI and repair the adapter with synthetic examples; do not drop the
unrecognized rows or claim an empty account. The current DOM adapter leaves
zero-row captures partial: there is no verified empty-list-message adapter.
The inventory helper prints a valid but incomplete empty inventory with
`no_candidates` and exit 0; this exit means valid input, never complete discovery.
The helper can accept an empty final batch after earlier captured rows when all
boundary evidence is valid. The current collector still marks every zero-row
capture partial, so its final capture must retain loaded rows; a blank viewport
cannot establish the end of history. A new scope or changed account needs
a new inventory. Resume only the same scope and account.

The capture function's input object has these fields:

| Field | Supplied observation |
| --- | --- |
| `account_url` | The saved stable Facebook profile URL, not `/me/`. |
| `scope_id`, `scope` | A run identifier and the unchanged requested boundary in plain words. |
| `id`, `observed_at` | A unique batch ID and its actual observation time, with timezone. |
| `account_verified` | Whether the active account was actually checked against the saved profile. |
| `boundary`, `boundary_note` | `more`, `end`, `scope_boundary`, `blocked`, `stalled` or `user_stopped`, with the actual evidence. |

The function reads category, filters, loading and rows from the DOM. Multiple
matching People or activity-search controls count as ambiguous, not unfiltered.
It removes unrelated URL query fields and rejects credential-bearing URLs before
returning a snapshot, while retaining supported exact comment/reply selectors.
Do not
replace those observations with favorable values. Treat an invalid category or
malformed snapshot as a capture failure and preserve the previous inventory.

## Resolve every candidate on its source

Open each candidate's complete navigation link. Select an inclusive comment view
and expand the relevant reply branches and truncated text. Verify the user's
actual author profile, full text, available context and readable attachments.
The parent post's author is not necessarily the comment's author. A snippet,
headline, timestamp link or existing correction is not the review itself.

If a navigation link lacks an exact comment/reply selector, it remains unresolved
until the source supplies one. Record that exact URL in `resolved_source_url` and
explain the match in `resolution_note`. Never invent selectors, use the parent's
URL as the reply's identity, or treat an inaccessible source as deleted. When two
activity rows genuinely represent one exact item, record that duplicate evidence
explicitly before mapping both to one reviewed target. Do not infer duplication
from matching parent links or similar wording.

An unknown row kind or a missing navigation URL remains an unresolved gap.
Re-capture it with a supported adapter after inspecting the current markup, or
carry the blocked candidate into the partial report. Do not delete the candidate,
change its kind by guesswork or claim completion to get past the check. A source
resolution note alone cannot clear an unknown kind or missing navigation evidence.

Also inspect other own comments/replies encountered in the relevant thread that
fall within the requested scope. Give each its own coverage row even if the
activity log did not expose it. Other people's writing is context only. Collection
and source review confer no permission to modify anything.

Each discovered item must reach the normal private report/coverage workflow.
Record discovered candidates separately from inspected items. Only exact source
review contributes to `items_reviewed`; navigation discovery never does.

## Reconcile discovery with review

For an assistant-led Facebook check including comments or replies, use the
[coverage ledger](SAFE-WORK.md#coverage-ledger) alongside the discovery inventory,
even when the review mode is not Work Safe. Use a version 2 ledger for each
account and run. Its `account_url`, `scope_id` and explicit scope text must match
the inventory; matching scope wording alone is insufficient. Before a completion claim or handoff, run:

```sh
python3 skills/res-work-safe/scripts/check_coverage.py PRIVATE_LEDGER.json --discovery PRIVATE_INVENTORY.json
```

The discovery argument is required for a Facebook ledger with any comment or
reply surface in scope. It checks that traversal reached its stated boundary
and every discovered candidate maps to an exact reviewed target. Missing rows,
unresolved parent-only links, identity/filter gaps and incomplete pagination keep
review completion false. Completing already-approved actions cannot fill those
gaps. Omitting the required inventory fails the check, including when no targets
have been recorded yet. The single-file checker remains available for other
providers and Facebook posts-only scope. Old version 1 ledgers must be upgraded
with verified account/run metadata while preserving their existing evidence and
outcomes; no legacy pass establishes Facebook discovery coverage.

Keep snapshots, inventories, source links and observations only beside the report
in the private application data folder. Retain only the metadata needed to resume
and explain coverage; exact authored text and media belong in the normal report.
Do not put account data in source, fixtures, synced notes or another service.

These helpers check the observations supplied to them. They cannot authenticate
Facebook, see unloaded/hidden activity, or prove an assistant captured every
available batch. Per-batch row references allow the local helper to check saved
counts and first/last occurrences against the inventory; they are not provider
attestation. The assistant must navigate the requested scope and state what
Facebook would not expose. The existing report and all action guards still apply.
