# Scheduled check reports

An assistant follows [ASSISTANT.md](../ASSISTANT.md), which is the whole contract. This page is the reference behind it; where the two differ, ASSISTANT.md is what an assistant does.

Resonance displays reports produced by an authorized assistant using supported browser or connector tools. The scheduler belongs to the assistant's host application. Resonance itself does not sign in to providers, read cookies, collect activity, send messages or delete social content.

The Overview shows the latest saved check. Checks shows recent history, the recorded schedule, findings and coverage for each account. Reports refresh while the page is visible. A report describes the content actually inspected; it is not an all-time account audit or a guarantee of SFW content. Public-only views and incomplete pagination must be marked partial. Inaccessible accounts must appear as blocked rather than silently disappearing.

Daily schedules can record `daily_time` (24-hour HH:MM) and `timezone` (for example America/Los_Angeles), with `ends_at: null` for an ongoing routine. Older metadata without these optional fields remains supported. The assistant scheduler must be updated separately using its automation tool; changing dashboard metadata alone does not reschedule a run.

Account-assisted checks need an awake, unlocked computer, the user's assistant available and valid browser sign-ins. Check every saved platform, including Reddit, with an explicit coverage result. Do not equate a saved account/profile or old excerpt with a current connection. The app's refresh button refreshes saved reports, not provider content. User-selected Keep/Edit/Delete work follows [the action-request contract](ACTIONS.md) and is handled during the scheduled check, with its own verification and confirmation gates.

## Save a report

From this project folder, run `python3 monitor.py --report /absolute/private/report.json`. Use `--config /absolute/private/config.json` to record schedule metadata and `--status` to verify the latest saved report. The command writes the same private SQLite database used by the GUI, even while the web server is stopped. It does not start a scheduler. `--data-dir` selects an isolated private test database.

Input files and storage must be outside repositories and common cloud-sync folders. Never include credentials. Ordinary report/config documents are limited to 2 MB.

Each new report must include every currently saved account exactly once. Use an unchanged run ID when retrying the same report: an identical replay is harmless; a different payload with that ID is rejected. Capture real check timestamps between the run's start and completion. Counts must represent actual inspected items, verified removals, or known pending deletions. A queued click is not a verified removal.

The API exposes the same guarded operations as `POST /api/monitor-runs` and `PATCH /api/monitoring`. Existing loopback Host, same-origin, content-type and bounded-body protections apply. `GET /api/state` includes:

```json
{
  "monitoring": {
    "config": {
      "enabled": true,
      "cadence_minutes": 360,
      "ends_at": "2026-10-03T18:00:00Z",
      "scope": "All saved accounts; own recent public activity",
      "action_scope": "Only actions authorized by the user and permitted by current tool requirements",
      "automation_id": "example-routine",
      "updated_at": "2026-10-01T18:00:00Z"
    },
    "runs": [{
      "id": "example-check-1",
      "started_at": "2026-10-01T18:00:00Z",
      "completed_at": "2026-10-01T18:02:00Z",
      "summary": "One profile sampled; full comment history unavailable.",
      "accounts": [{
        "account_id": "existing-account-id",
        "status": "partial",
        "checked_at": "2026-10-01T18:01:00Z",
        "summary": "Recent visible posts reviewed.",
        "coverage": "Two public posts; private comment history unavailable.",
        "items_reviewed": 2,
        "deleted_count": 0,
        "pending_deletion_count": 0,
        "findings": [{
          "severity": "suggestion",
          "title": "Check the missing activity surface",
          "detail": "The available view did not expose all own comments.",
          "source_url": "https://example.com/profile",
          "kind": "account",
          "text": "Example saved post text, including the exact words that caused this finding.",
          "quotes": ["the exact words that caused this finding"]
        }]
      }]
    }]
  }
}
```

The example is documentation only; real reports require saved account IDs and observed source URLs. Status is `checked`, `partial` or `blocked`. Checked means the stated scope was completed, not that the account has no concerns. Findings use `attention`, `suggestion` or `strength`, and kind `post`, `comment`, `reply` or `account`. `checked_at` may be null only for blocked accounts.

A finding may also carry `text` and `quotes`. Both are optional; a report that omits them stays valid and unchanged, and an omitted key is never stored as an empty default.

- `text` is the complete original text of the post or comment as you actually saw it, up to 20000 characters. Save the complete text you actually saw. Do not summarise it, do not stitch two views together, and leave it out when the view did not show you the whole item. This text becomes the saved excerpt for that finding in the interface, and it is still labelled as possibly partial.
- `quotes` is a list of at most 20 distinct, non-empty strings of at most 500 characters. Each one must be an exact substring of `text`: the precise words that caused the finding, copied verbatim. Never paraphrase into quotes, never normalise spacing or punctuation, and never quote words you did not read in `text`. The interface marks each occurrence in the saved text; a finding with no quotes simply marks nothing.
- Quotes without a `text` to copy from are rejected, as are quotes that do not appear in `text`, repeated quotes and empty quotes. One invalid quote rejects the whole report, so nothing partial is stored. Schedule configuration is reported metadata, not proof the host scheduler is running. Keep it in sync when pausing or changing the actual routine.

## Assistant workflow

1. Read current account identifiers, identity/SFW/daring settings, previous reports and explicit keep decisions.
2. Verify the account and inspect its own recent posts/comments/replies. Exclude private messages unless separately authorized. Distinguish the profile being viewed from the account currently signed in.
3. Compare with the previous check. Label a first run as a baseline. Record any uncovered time interval, inaccessible activity view or uninspected media. Never describe old findings as newly posted content.
4. Execute only separately authorized provider actions through the supported tool. Respect current confirmation rules. Permanent deletion may require action-time approval; preserve pending counts and continue independent read-only checks while waiting. This application does not circumvent that gate.
5. Save the report through `monitor.py`, verify its run ID with `--status`, and present the same concise summary in the requested chat. If ingestion fails, state that the GUI has not updated and preserve the private report for a retry.
6. On a schedule's final run, deliver the final result, pause the real scheduler and set the stored configuration inactive. Do not extend it by assumption.

## Persistence and backups

All reports remain in SQLite; the interface receives the newest 30. Backups include the complete saved monitoring history and configuration. Import accepts up to 32 MB with full validation before writes; ordinary API mutations remain limited to 2 MB. Older backups without monitoring fields preserve existing reports. Importing schedule metadata does not create, start or restore a host automation. Browser offline storage continues to cache only the public application shell, never reports or private API responses.

## Exact-target outcomes and interrupted actions

The post-action workflow is defined in [ACTIONS.md](ACTIONS.md). A daily check uses the same report command; no extra scheduler or dispatch channel is created.

Before collecting or changing provider content, use `python3 monitor.py --work`. It distinguishes queued work eligible for preparation (`can_prepare`) from held and verification-only work. Inspect the active account and exact source author, then record the observation with `python3 monitor.py --check-identity ID --observation FILE`. Only a fresh local match can make `can_execute` true. Record an attempt with `python3 monitor.py --start-request ID --identity-check-id CHECK_ID` before a separately authorized provider change. A rejected start is a stop. The read-only `--request ID` last look must report both `evidence_current` and `identity_current` true and `user_completed` false. Recheck the browser identity too; the app cannot detect account switches. Already-started or uncertain work requires reconciliation, never another automatic attempt. User attestations are always verification-only. See ASSISTANT.md for the observation shape and five-minute expiry.

A report may add `requests`, an optional list of exact-target outcomes. Each entry uses this shape (synthetic values only):

```json
{
  "id": "exact-request-or-attestation-id",
  "target_type": "request",
  "account_id": "exact-saved-account-id",
  "source_key": "copy-from-current-work-state",
  "evidence_fingerprint": "copy-from-current-work-state",
  "outcome": "blocked",
  "reason": "The exact source requires sign-in.",
  "receipt_url": "",
  "receipt_note": "",
  "next_move": "open_source"
}
```

`target_type` is `request` or `attestation`. `outcome` is `verified`, `blocked`, `reopened` or `unknown`. `next_move` is `open_source`, `fix_now`, `confirm_request` or `none`; choose one the actual state supports.

- **Verified:** requires an observed result URL and a nonempty note explaining the actual platform verification. This may verify a user's already-completed local task. An inaccessible or generically missing page is insufficient proof of deletion.
- **Blocked:** retains the reason and a valid next move. No fabricated receipt. An action blocked before an attempt remains unperformed; an uncertain attempt must remain verification-only.
- **Reopened:** records the observed mismatch and returns local attention. It preserves the original evidence, attestation and prior history; it does not reauthorize a provider change.
- **Unknown:** records an interrupted or inconclusive attempt for reconciliation. It must not return to an executable queue automatically.

Use the exact target ID, account, source key and evidence fingerprint returned by current work state. Never derive these from a card label. Recheck cancellation and source changes. Retain a rejected outcome report privately, including any observed result, and reconcile it against the current target. Do not change evidence snapshots just to make intake succeed.

When evidence changed after a recorded attempt or user attestation, an explicit `reconcile: true` on a verified/reopened outcome can record the historical result. It requires the exact saved target identity/fingerprint, a reason, an observed receipt URL and verification note. It does not close newer source concerns, restore mutation authority, or revive cancelled work. Ordinary stale outcomes still fail. A blocked check after an attempted action remains verification-only until the outcome is reconciled.

The complete report and every outcome validate atomically. One invalid or contradictory outcome changes nothing. An identical report replay is checked before lifecycle transitions, so it stays harmless even after its first intake completed the target. Reusing a report ID for different content fails. Backup import stores report history without applying any of its outcomes.

After `--report`, run `--status` and check the saved ID. Check the exact target and source item in `/api/state` and the Posts/Today interface; changing report text alone does not complete delivery. Keep verified-removal counts distinct from queued actions and user attestations.

`monitoring.next_scheduled_check` is a derived ISO timestamp or null. Daily metadata uses its IANA timezone, daylight-saving rules and expiry. Disabled, expired or unavailable schedule metadata yields no countdown. Say **Next scheduled check**, never a promised completion time. No report means **No result recorded**, not proof the host scheduler missed a run.
