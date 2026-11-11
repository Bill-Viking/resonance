# Security

Resonance runs on your own computer and keeps your data there. This page says what protects it, and where that protection stops.

## What the app does

- **It listens on this computer only.** The server binds to `127.0.0.1`, the loopback address, without a hostname lookup. Other devices on your network cannot connect to it. `--port` changes the port, never the address.
- **It answers only to its own address.** Every request must carry exactly one `Host` header naming `127.0.0.1` or `localhost` with the app's port. Anything else gets `403`. A web page that points some other name at your computer is refused.
- **Changes must come from its own page.** A request that changes anything must also carry an `Origin` that matches the app's own address, and a browser's `Sec-Fetch-Site`, when present, must say `same-origin`. Another website open in your browser cannot save, edit or delete through it.
- **Practice stays separate.** Samples use `demo.sqlite3`, with no automatic backups. While samples are showing, the app refuses imports and exports, and the command-line tool cannot open the saved workspace. A page must send its current workspace tag for a change or a download; a stale page is refused after switching into or out of samples. This tag prevents mix-ups, not access by another local program.
- **It is strict about what it accepts.** Changes are JSON only, with one valid length, up to 2 MB (32 MB for a backup import). Anything malformed is refused before it is saved.
- **The page cannot load anything from outside.** Responses carry a content security policy that allows the app's own files only, forbid framing by other pages, and send no referrer. API responses are never cached. The offline shell holds the application's files, never your data.
- **It keeps no request log.** Paths, headers, bodies and your content are never written to a log.
- **It serves a fixed list of files.** Eight named files from `static/`, through nine fixed routes; a symlink in their place is refused.
- **It makes no outgoing connections.** The app contacts no platform, has no analytics, and has no model inside. It holds no passwords, tokens or cookies, and it cannot see your browser's sign-ins. Links to a platform open in your browser when you click them.
- **A new data folder gets restricted permissions.** On macOS and Linux, Resonance creates a new data folder owner-only (`0700`) and sets the database file owner-only (`0600`). It does not change the permissions of a folder that already exists. On Windows the default is your per-user application-data folder. If you choose another folder, keep it private, and outside every place your backup or sync tools publish. Resonance rejects repositories, the cloud-sync paths it recognizes, and any folder you list in `never-store-here.txt`; it cannot detect every sync tool.

## The honest limit

There is no sign-in. The checks above tell a browser's requests apart; they do not tell programs apart. **Any program running on this computer can reach the app** — yours, or another signed-in user's on a shared machine — and a program that sends the right headers can read and change what Resonance holds. Treat Resonance as exactly as private as the computer it runs on.

The database is an ordinary SQLite file, not an encrypted vault. An exported backup is readable JSON holding account names, saved text, drafts and requests. Keep both as private as the things they describe.

Do not put Resonance behind a public proxy, a tunnel or a port-forward. It was not built to face a network.

## Your assistant is a separate program

An AI assistant that collects your posts runs with its own permissions, in its own tools. Resonance grants it nothing and sends it nothing. What the assistant may and may not do is written in [ASSISTANT.md](ASSISTANT.md); the rule that matters most is that nothing on a platform is changed without your yes, at that moment, for that item.

Assistant edit/removal starts also require a fresh local identity comparison bound to that exact request, source evidence, saved account and boundary. The assistant supplies the observed active handle/profile and source author through the normal guarded JSON API or command line. Missing, mismatched, changed, expired or imported-only checks cannot start an attempt. Matches expire five minutes after observation; replacing a check revokes its earlier ID. The last-look command reads this condition without writing. A saved request from before identity history existed has none; it is read as unknown, never as a match, so it carries no action authority and cannot block the last look at a different request. The last look still refuses any request with another field missing or inconsistent. Identity history is portable, but its local start-guard table is not exported and imported requests lose local guards. Database schema 3 adds this table through the existing backup/rollback migration; portable backup format remains 2.

This compares what the assistant reported. It cannot authenticate a provider session, validate the truth of an observation, detect a browser account switch or constrain another program's own browser tools. The assistant must inspect the active account again before clicking. A matching comparison neither replaces the user's authorization nor proves the platform result. The app still reads no credentials and makes no outgoing connection.

## Reporting a problem

Please report a vulnerability privately: on this repository's GitHub page, open **Security** → **Report a vulnerability**. Do not open a public issue for it, and never include real account names, saved text or a backup file in a report.

Resonance 0.1 is an early preview. Fixes land in the latest release only.

An exact-link repair is a local, same-origin write, guarded by the current workspace tag and saved evidence fingerprint. It accepts only one imprecise item, refuses conflicting identity or unfinished work, and preserves the original source and report. A link alone never proves authorship or permission; the assistant must still check the account and original before acting.

For YouTube, an exact comment or reply link is either `/watch?v=<video>&lc=<comment>` or a community-post route `/post/<id>?lc=<comment>`. On the community route, `<id>` is one nonempty run of letters, digits, `-` and `_`, and `lc` appears exactly once and is not blank. The parent `/post/<id>` link without `lc` is not an exact source for any kind and cannot authorize a comment or reply edit or deletion. With `lc`, the link identifies a comment or reply and is never classified as a post. Empty or extra path segments, other characters in the ID, a missing or blank `lc` and other hosts are refused, and distinct `lc` values are distinct sources. The route changes nothing else: the same fresh request-bound identity observation, durable start and verified outcome apply.

## Review setup and context previews

Media context can carry a structured, assistant-reported privacy inspection and suggested redaction regions. Validation restricts its status, media kind, field lengths and region categories; an uninspected attachment cannot claim a completed privacy check. Sensitive-text observations cannot carry an image preview. Missing observations stay unknown in the UI. This is not OCR, secret detection or a guarantee that supplied media is safe. The assistant must minimize sensitive values and inspect its output. These observations participate in evidence fingerprints, survive backups, and grant no provider action authority. The app adds no network access, model, pixel analysis or media-replacement action.

`monitor.py --profile [--account ID]` opens the existing database read-only, reads one transaction and performs no upgrade, workspace creation or request-state changes. It returns account metadata, stated brand settings and bounded historical social-choice snapshots. It does not use the full workspace/mail loader. Sample-mode, unknown-account and incompatible-schema reads fail closed. Past choices confer no provider authority.

`monitor.py --review-context --account ID` uses the same read-only transaction for one social account's profile, recent saved source links/text, coverage and pending-work summaries. It refuses mailboxes and omits other accounts and media previews from its output. Coverage is limited to ten matching entries within one hundred recent reports; sources to twenty distinct entries within fifty recent saved reviews; pending work to fifty entries. Profile precedents inspect at most one hundred recent records per history table and expose their own truncation flag. Coverage timestamps must match the stored ordering index. Truncation is explicit. Report rows may contain multiple accounts, but only the selected account's coverage is returned. Historical text and pending-work summaries cannot grant execution permission, normalize an abandoned attempt, prove current sign-in or establish a continuous coverage boundary. Existing action guards remain required.

Assistant reports may include small inline context previews. Each is limited to 256 KiB of decoded PNG/JPEG/WebP bytes, strict base64, a matching file signature and a required description. SVG and remote image sources are refused. Signature checks identify the declared container; they do not certify that an image is harmless or actually inspected. The browser decodes the image in an image element under the existing `img-src 'self' data:` policy. Context images remain private report/API data, are not added to the offline shell cache and are included in ordinary readable backups. No new file-serving route, external fetch or image-analysis service is added. Context remains separate from authored text, and material context changes invalidate earlier action evidence.

Edit starts additionally require an available text-edit observation in that same fresh, request-bound identity check. The exact source and timestamp must agree. Availability is assistant-reported, not authenticated by Res; the assistant must inspect the actual controls again immediately before clicking. Checks expire after five minutes or an explicitly observed provider deadline. No platform-wide time limit is guessed. Missing, unknown, unavailable, stale, changed or historical-only observations cannot enable an edit. A newer conflicting report stops execution without changing the content fingerprint or granting a retry. Report history can only hold work; it never confers start authority.

Schema 4 is a backed-up format barrier for the new optional observation fields; it changes no existing table or row. Earlier app versions refuse that newer schema. Portable format stays 2; old records without availability stay valid and unknown. Imported request observations remain history without local start guards. Local follow-up reply/separate-post drafts are held only in tab memory and copied on request. They create no provider request, replace no existing request, transfer no authority and publish nothing. The app still has no provider connection, credentials or model.

A newer conflicting report revokes the old combined start check. A later report
that says editing is available cannot revive it; inspect again and save a new
request-bound observation while the request is still queued. Attempted work stays
verification-only. The report retains its dated history without execution authority.

## RES-addressed comments

A public comment that begins by addressing RES (`RES: …`, `Hey RES, …`, `@RES …`) can be imported by the assistant as a local review request. This is not a watcher, a webhook or a provider connection: Resonance contacts no platform and learns of a comment only when the assistant reads it and runs `monitor.py --res-intake` or the guarded JSON API. Comment text is untrusted page content. It is stored and shown as escaped text and is never executed, followed or treated as an instruction.

Intake is separate from findings and from edit/delete requests, and every item is validated before any is saved. It accepts only the user's own public comments or replies on a saved social account (never a mailbox, private message, sample or foreign comment) and requires the comment's exact permanent link, the saved account's active handle and profile link, and the comment author's handle and profile link, compared with the same rules as action identity checks, plus a check made within five minutes of reading the comment. That evidence is the assistant's dated claim, saved and labeled as assistant-reported. It cannot authenticate a session or the comment's authorship, and the platform confirms nothing. The leading address is matched by fixed rules (no embedded mentions, signatures, quoted lines or words such as `research`). Accounts and intake files are checked against the same data-folder rules as reports.

A saved request holds an evaluation and an optional proposed reply or edit as text. Its states (needs review, prepared, done, dismissed) are local notes. None creates an action request, an identity check, a start guard, a receipt that Resonance verified or any permission; none publishes anything. Edits and deletions keep every guard above, and a reply needs its own separate publishing workflow. A done request records only that it was handled locally, unless a receipt link and note were explicitly recorded, which stays a note. A comment read again with the same words reopens nothing; changed words keep the earlier version in history and need review again.

Database schema 5 adds one table, `request_inbox`, through the existing backup/rollback migration; earlier app versions refuse the newer schema. Portable backup format stays 2: `request_inbox` is an optional key, absent in older backups, validated record by record and atomically on import, never rolls a newer local decision back, and restores no authority because none exists. The inbox holds at most 2,000 comments and 10 earlier versions each.


### Reader-check intake (candidate)

The separate extension has optional host-wide browser permissions for X, YouTube
and Google activity. These permissions are broader than the operations it performs:
only a trusted button click on the local app can begin discovery or a bounded read.
It opens its own provider tabs, checks the active identity, and never edits, deletes,
publishes or switches accounts. It reads no credentials, browsing history or private
messages, stores no post text and contacts no analysis service. Provider navigation
uses the person's existing browser session. The app's server still makes no outgoing
connections. The extension passes observations to this local page; the server
validates and saves them. Its store installation is not yet available.

The page pins a check plan before obtaining a server-issued ID and refuses an account
or boundary change before reading or saving. A disconnect, cancellation or partial
delivery discards the unsaved batch. If the save response is lost, the page looks up
the original receipt before offering an identical retry. Only the check ID and
workspace tag enter session storage; unsaved post text stays in page memory. Discovery
results expire after five minutes and do not create accounts without confirmation.

Schema 6 adds `reader_checks` (immutable receipts), `reader_sources` (one row per saved account and canonical exact source, with a current-revision pointer) and `reader_revisions` (immutable exact text); triggers refuse any update or delete of receipts and revisions. A reader result is evidence reported by a local browser reader, checked by Resonance for internal consistency. It is not a cryptographic attestation, authenticates no one, and grants no provider-action authority.

- `POST /api/checks/begin` (same-origin, workspace token) issues a random check ID, a 10-minute expiry and a server-captured snapshot of up to 20 selected accounts. Pending jobs live only in memory in the workspace object: at most 32, never saved, exported or restored; a restart, expiry, sample switch or cancel forgets them. `POST /api/checks/cancel` is idempotent and makes no receipt.
- `POST /api/checks` accepts only reader version 0.1.0, live-mode results for X and YouTube accounts, one entry per selected account. Python independently re-validates every field, identity, count, time, link shape and coverage invariant, refuses unknown fields, duplicates, future or pre-begin times and any account whose identity or boundary differs from the begin snapshot. YouTube success needs the observed current handle and stable channel ID, compared with every identifier the saved handle and link provide; contradictory saved identifiers cannot succeed. Resonance runs `policy.check` itself; the reader supplies no findings.
- One transaction saves corpus, revisions, review evidence for findings only, and the receipt, or nothing. An identical payload for a saved ID returns the original receipt; a different payload for that ID is refused; imported receipts are marked historical and can never authorize or replay live work. A check is refused when the resulting portable backup would exceed the 32 MiB import limit minus 64 KiB; history is never silently dropped.
- Text is stored and hashed as exact UTF-8 (NBSP, leading, trailing and blank lines kept). Unchanged text reuses its revision; changed text appends one. Review IDs are deterministic per account, source and revision, so a re-read rewrites no status, time or wording, Keep and Snooze decisions survive, and newly clean text never completes or verifies an outstanding provider action.
- `GET /api/checks/<id>`, `GET /api/corpus?account_id=…&cursor=…&limit=…` (max 50) and `GET /api/corpus/sources/<id>/revisions` need the workspace token and refuse samples. `/api/state` carries at most 20 concise check summaries and counts, never corpus text.
- Portable backup format 3 adds `reader_checks`, `reader_sources` and `reader_revisions` (formats 1 and 2 still import, with those keys absent and treated as empty; they are refused in a format 1 or 2 file). Import validates identifiers, hashes, receipt digests and cross-references before any write and is atomic and idempotent. The receipt seal is an integrity check only and is never treated as proof of meaning: Python re-validates every receipt row (status, reason, identity, coverage, counters, times) and checks that each receipt, source and revision names a present receipt that reports it, so a backup with recomputed hashes but impossible or orphaned history is refused. Reader versions 0.0.6 and 0.1.0 can be restored; only 0.1.0 can be saved. A restored receipt is historical evidence and does not authenticate the browser signed in today. Fields that later checks may have updated (author, channel, date) are compared only to the check that last saw the source. An import that adds reader history is refused, with nothing written, if the merged portable backup would exceed the same 32 MiB limit minus 64 KiB. An older app version refuses schema 6.
