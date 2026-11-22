---
name: res-work-safe
description: Run a user-requested Work Safe or Safe Work audit of the user's own public posts, comments, replies, and readable attachments under the Resonance contract. Not for employment screening, employer risk scoring, or inbox review.
---

# Work Safe

Use this skill only for a direct request to run a Work Safe audit. “Safe Work”
is an accepted synonym. This is a content review for the user's stated scope,
not employment selection, employer risk scoring, or an inbox/DM review.

## Locate the active Resonance contract

Use the Resonance folder the user selected or named for this task. If none was
named, use the current project/workspace only when it clearly contains
`ASSISTANT.md` and `docs/SAFE-WORK.md`; otherwise locate an existing Resonance
folder using available local workspace search and identify it by those files.
If several installs remain plausible, ask which one to use. Resolve and read
that folder's `ASSISTANT.md` and `docs/SAFE-WORK.md` in full. Follow their current
commands, report schema, data location, and safety rules. These files are the
source of truth; do not assume a path based on this skill's installation, copy
their full contract here, invent CLI flags, or imply the app has a Work Safe
classifier or automatic audit engine. A setup button may prepare an assistant
request; it does not run this audit.

## Run the requested audit

Use the requested platform/account and scope as the limit. When a critical
scope detail is missing, clarify it before provider access. Read current saved
account boundaries, brand context, prior decisions, and queued/held/verification
work using the contract's read-only commands. Follow its identity and action
guards. Review the full requested scope across accessible public surfaces; do
not substitute a sample or narrower pass. If the work takes time, give dated
checkpoints stating coverage and remaining work.

Before traversal, record separate coverage for own posts, own top-level comments
and own nested replies, distinguishing those on the user's posts from those on
other people's posts. A profile feed does not cover comments elsewhere. Follow
the procedure's private coverage ledger, including an unresolved placeholder for
each user-reported miss whose exact source is not yet known. Keep those targets
through handoffs; a parent deletion never resolves an item on another parent.

For Facebook comments/replies, follow
[Facebook discovery](../../docs/FACEBOOK-DISCOVERY.md): enumerate the own Comments
activity log without person/topic filters, capture full View hrefs using the
packaged read-only DOM collector, and build/resume the inventory with
`scripts/discover_facebook.py`. Capture every activity row before selecting
findings. Multiple rows on one parent remain separate. User clues and known
problem links are optional additions, never discovery prerequisites.

Inspect only the user's own public posts, comments and replies, with complete
text, available thread context and every readable attachment. Never inspect
inboxes, DMs, private chats, or another person's account. Contextualize quotes
before attributing them. Review sexual, graphic or crude content, threats,
abuse or slurs, visible private or work-confidential text (including QR codes
and barcodes), and the user's saved brand boundaries. Do not copy or decode
exposed private values; describe type and location. Ordinary opinions, fiction,
costumes or appearance alone are not violations. Ambiguous cases go to review;
unreadable content remains unchecked. No flags are not certification.

Assess the main post, each own comment or reply, and attachment coverage
separately. Fiction or satire is context, not an automatic exemption for the
user's own wording. Distinguish quotations and criticism from endorsement-like
wording; explain a concrete ambiguity against the saved audience without
inferring beliefs, political identity or character. Do not invent concerns from
ordinary opinions or a fictional character alone. An earlier assistant
clarification or RES banner is not a user Keep or proof a concern was resolved.

Named and unclassified accounts remain SFW regardless of daring. A deliberately
relaxed alias (`sfw_only: false`) is relaxed only for that SFW boundary. Honor
prior Keeps for unchanged concerns; new SFW risk on named/unclassified accounts
still requires attention. Mark inaccessible surfaces truthfully and do not
claim uninspected coverage.

When the user ends historical traversal, turn to the collected findings within
their remaining instructions and action authority, and state the unchecked
remainder. A stop of all work instead stops all work. Preserve a current explicit
removal request even when the item was previously Kept or called harmless; never
silently turn it into Keep, an edit or a reply. Missing confirmation remains a
hold, not a reason to change the user's choice.

Save a dated report using the existing private report workflow and unchanged
schema. Include every saved account exactly once, identify the Work Safe audit
and scope in the summary/coverage, and count only inspected items and removals
actually verified on the platform. Do not place private evidence in this skill,
the repo, another service, or the conversation beyond the concise report the
user requested.

Keep reviewed, flagged, queued, waiting for required confirmation, attempted and
verified removed distinct, using the procedure's meanings and existing schema.
A chat request is not a saved queue entry, a started attempt is not a verified
removal, and an uncertain attempt is verification-only rather than retryable.

Before a completion claim or handoff, reconcile every named target and requested
surface and run this skill's `scripts/check_coverage.py` on the private ledger,
using the version 2 schema in [the procedure](../../docs/SAFE-WORK.md#coverage-ledger).
Keep one ledger per verified account and review run; match its `account_url`,
`scope_id` and scope text to the discovery inventory. Upgrade older ledger
metadata explicitly while preserving its targets and completed outcomes.
For Facebook comment/reply scope, include `--discovery PRIVATE_INVENTORY.json`
so a missing discovered candidate or incomplete traversal cannot pass the check.
Do not call the audit complete while the checker reports gaps or pending actions.
A stopped historical pass stays partial. Say when only the collected action list
is resolved, and carry its unchecked surfaces forward. The checker validates
recorded evidence only; it cannot discover omitted items, verify a provider result
or authorize an action. Do not substitute its output for the normal saved report.

## Separate action authority

The audit name alone authorizes no platform action. Never make permanent
deletions automatically. Follow the contract's exact-target, authorization,
fresh identity, attempt, tool confirmation and verification requirements.
Normally, obtain the user's yes for each item at action time. An explicit current
delegation may cover recoverable removal or the fallback edit below within a
defined scope only where the tool permits preauthorization. Recoverable means
the user can undo removal through a restore path verified for that exact platform
surface; treat an unknown or unverified restore path as permanent. Permanent deletion
always needs the user's current yes to that exact item under the contract,
as well as any tool-required confirmation. No delegation waives a mandatory
action-time confirmation. Do not change a delete request into an edit or reply without
explicit authorization.

For the user's own conversation comment only: if deletion is unavailable,
editing is available, and the user explicitly authorized this fallback for the
item or through a current scoped delegation that covers it and the tool accepts,
use the existing Modify flow in Resonance to create a distinct edit request with
the exact replacement; never reuse the delete request. Recheck active identity
and exact-source edit controls immediately before its separate attempt, and
follow all required confirmations. Replace only that comment's text with exactly:

```text
Comment withdrawn.
Edited by RES.
```

Add no banner, full signature, repository URL, reply, or separate public note.
Do not claim this removed text embedded in an image or erased edit history.
If deletion succeeds, leave no public banner, attribution, replacement, reply
or announcement. Report only what the platform actually shows. If the needed
controls or mandatory confirmation are missing, stop that action and report the
hold.
