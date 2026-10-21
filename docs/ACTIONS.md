# Posts, decisions and verified outcomes

An assistant follows [ASSISTANT.md](../ASSISTANT.md), which is the whole contract. This page is the reference behind it; where the two differ, ASSISTANT.md is what an assistant does.

One source item is the unit of review. Posts and Today use the same grouped items, counts and status. Each item retains every saved observation, rule and report finding, with its source and provenance. Grouping never deletes the original records.

Each card shows the saved text in full in a scrolling box, with any quoted words from a finding marked. A mark opens that finding. Findings without quotes mark nothing. The saved text is still labelled as possibly partial.

The card offers **Keep**, **Modify**, **Delete** and **Snooze**. Modify and Delete need a precise saved source, exactly as the earlier pair did; without one the buttons stay disabled and the card says why. Both open a choice of the same underlying flows, which are unchanged.

- **Keep** closes the local review for the current evidence and withdraws eligible queued changes. It makes no provider change.
- **Modify** offers editing it yourself, asking the assistant for an edit, or retracting it. **Retract** asks who posts the withdrawal notice and then opens the matching edit flow with suggested wording already in the proposed-text box; a further choice offers another notice, and wording the user typed is never replaced.
- **Delete** offers deleting it yourself or asking the assistant to delete it. The page never contacts the platform; an assistant deletion is queued for the next check and still needs the user's confirmation at the time it happens.
- **Fix now**, the self-service route, opens the exact original and helps the user prepare wording. The saved text may be only an excerpt. Editing availability depends on the source; the app does not assert a provider capability. Copy happens only after a click. If copying or opening fails, the source link and selectable text remain available.
- **I changed it / I deleted it** saves a separate, note-required user attestation. It closes local attention and leaves **Done by you · verification pending**. It is not an assistant receipt. Eligible queued changes are withdrawn atomically; a running or uncertain attempt remains visible for reconciliation.
- **Ask assistant**, the queued route, saves the specific edit/delete for separate execution under current authorization and tool requirements. A source with active work, an unresolved attestation or an imprecise source opens its current state instead of a second request. **Next scheduled check** is an expected check time, not a promise that the action will finish then.
- **Snooze** postpones local attention for 24 hours. It does not stop an already-started action. Waiting work and verification-only work remain visible.

Keep and verification apply only to the evidence they cover. Changed wording, account identity, boundaries or a new concern require another assessment. Repeated identical findings do not reopen a decision just because another report recorded them.

## Identity

A shared backend helper governs display grouping and mutations. Account identity is always part of the key. Only known tracking parameters are stripped. Meaningful query parameters and fragments, including distinct comment identifiers, remain intact. Profiles, broad history links, aggregate samples, missing links and unrecognized item routes remain separate evidence and cannot authorize a mutation. A precise saved link still needs live authorship and source verification before provider action.

**Add the exact link** repairs one item with a missing or imprecise link. It preserves the original observation and report, records the supplied link separately, and reopens the card under its corrected source. The app checks the supported platform and item type, and refuses a conflicting author when the URL names one; it cannot establish authorship from a URL without an author. It refuses aggregate samples, a stale card, an existing exact source, a collision with another card, and pending or uncertain action or attestation work. No prior action permission moves to the corrected source.

`GET /api/state` includes authoritative `source_items`, original `reviews`, `policy_flags`, `action_requests` and separate `attestations`. Each source item contains its supporting IDs, findings, shared status and evidence fingerprint. This fingerprint binds a decision or outcome to the actual evidence, not just a display card. Report-only social findings retain report provenance; incoming mailbox findings are not authored-post reviews.

## Local APIs

- `POST /api/action-requests` accepts `{review_id, action: keep|edit|delete, proposed_text?}`. The server derives account, source and original text. Identical pending requests replay safely. Conflicting work must be cancelled or reconciled; it is never replaced silently. All sibling entry points show the same active request.
- `POST /api/attestations` accepts `{review_id, action: edit|delete, note, proposed_text?, result_url?}`. A note and precise source are required. The result identifies cancelled queued requests and any running/uncertain work. Optional result links are user statements, not verified receipts.
- `POST /api/source-items/{id}/link` accepts `{source_url, evidence_fingerprint}` and returns the corrected `source_item`, including its new ID. New requests and attestations use that link.
- `PATCH /api/source-items/{id}` accepts `{status: snoozed|open}`. Legacy review/flag routes use the same source relationship. A legacy resolve request cannot hide a pending mutation.
- `PATCH /api/action-requests/{id}` retains guarded cancellation/completion compatibility. Use report outcomes for the ordinary assistant workflow. A completed provider change requires an observed result URL and verification note. Keep never uses a provider receipt.
- `POST /api/action-requests/{id}/start` with `{}` durably records the start and a unique attempt ID. It is also exposed by `monitor.py --start-request ID`. Start refuses held, stale, blocked, cancelled or already-started work. This records an attempt; it neither contacts the provider nor waives current tool requirements.

Request presentation includes `execution_state`, `can_execute`, `can_cancel`, `confirmation_required`, `source_key`, `evidence_fingerprint`, `scheduled_for`, attempt facts and actual blocker details. Only `can_execute` exactly true together with `confirmation_required` exactly false identifies locally eligible mutation work. Those fields alone do not establish live provider authorization.

## Assistant execution and recovery

Read `monitor.py --work` from the project folder before the run. It distinguishes eligible changes from verification-only work.

1. Read current evidence, requested wording, earlier receipts and explicit Keeps. Check the exact account and source.
2. Verify current sign-in, authorship and full live text through supported controls. A saved excerpt is not necessarily a complete post. Ambiguous replacement wording, inaccessible content or unsupported edits block the action with a specific reason.
3. Re-read eligibility immediately before action. Obtain any current tool-required confirmation. Record a durable start before the provider change; do not act if it is refused. Re-read the request after the start to catch a cancellation or user attestation that arrived during preparation.
4. Perform only the separately authorized change. Verify the actual platform result; a click, missing generic page, hidden comment or failed request does not prove deletion.
5. Save an exact-target report outcome through `monitor.py --report`. Verify its report ID and the resulting source/request status in the GUI before claiming delivery.

**Never automatically retry running or uncertain work.** A process interruption after a possible provider change leaves verification-only work. Reconcile the live source before any further mutation. A failed verification can reopen local attention, but cannot grant new mutation authority. A fresh user choice is required to try another change.

Keep and user attestation withdraw not-yet-started work. They cannot promise that an action already in progress stopped. Preserve the attempt and any observations. If evidence changes or a target becomes stale before intake, retain the rejected report privately; investigate the current target and report the discrepancy without rewriting snapshots or reviving cancelled work.

## Reports and backups

[The monitoring contract](MONITORING.md) defines optional exact-target outcomes for requests and attestations: verified, blocked, reopened or unknown. All outcomes and the complete report validate before one transaction commits. Exact replay is harmless after the first intake changes the target's state. Unknown targets, contradictory outcomes, cross-account mismatches and stale/cancelled execution targets fail without partial intake.

Version-1 backups remain supported. Original observations, Keeps, completed/cancelled requests, attempt facts, attestations, communication and drafts remain durable. Restored pending requests require fresh local confirmation. Imported running/unknown records are verification-only. Importing a report stores history; it does not apply outcomes or execute actions. Backups never transfer execution authority. Optional paired `source_url_correction` and `source_url_corrected_at` review fields preserve exact-link corrections while `source_url` retains the original observation. Restoring an older snapshot does not erase a saved correction, and a conflicting correction under the same review ID is refused.

The server performs no provider calls. Opening the dashboard neither scans accounts nor wakes an assistant. Private requests, receipts, reports and exported backups stay outside source, Git and cloud-synced folders.

## Migration and retained evidence

Old pending requests without a source-evidence fingerprint are held for fresh dashboard review. Imported or legacy authority never becomes executable merely because an old record says pending. Historical completed choices remain visible; an old Keep cannot dismiss a newer concern. Current and future policy flags retain durable `flag_evidence` snapshots. Orphan decision IDs from earlier versions are preserved, but missing details that the old version never stored cannot be reconstructed. Import rejects conflicting original reviews and mismatched request/attestation/flag snapshots.
