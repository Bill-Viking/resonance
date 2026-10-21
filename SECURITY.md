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
- **It serves a fixed list of files.** Six named files from `static/`, through seven fixed routes; a symlink in their place is refused.
- **It makes no outgoing connections.** The app contacts no platform, has no analytics, and has no model inside. It holds no passwords, tokens or cookies, and it cannot see your browser's sign-ins. Links to a platform open in your browser when you click them.
- **A new data folder gets restricted permissions.** On macOS and Linux, Resonance creates a new data folder owner-only (`0700`) and sets the database file owner-only (`0600`). It does not change the permissions of a folder that already exists. On Windows the default is your per-user application-data folder. If you choose another folder, keep it private, and outside every place your backup or sync tools publish. Resonance rejects repositories, the cloud-sync paths it recognizes, and any folder you list in `never-store-here.txt`; it cannot detect every sync tool.

## The honest limit

There is no sign-in. The checks above tell a browser's requests apart; they do not tell programs apart. **Any program running on this computer can reach the app** — yours, or another signed-in user's on a shared machine — and a program that sends the right headers can read and change what Resonance holds. Treat Resonance as exactly as private as the computer it runs on.

The database is an ordinary SQLite file, not an encrypted vault. An exported backup is readable JSON holding account names, saved text, drafts and requests. Keep both as private as the things they describe.

Do not put Resonance behind a public proxy, a tunnel or a port-forward. It was not built to face a network.

## Your assistant is a separate program

An AI assistant that collects your posts runs with its own permissions, in its own tools. Resonance grants it nothing and sends it nothing. What the assistant may and may not do is written in [ASSISTANT.md](ASSISTANT.md); the rule that matters most is that nothing on a platform is changed without your yes, at that moment, for that item.

## Reporting a problem

Please report a vulnerability privately: on this repository's GitHub page, open **Security** → **Report a vulnerability**. Do not open a public issue for it, and never include real account names, saved text or a backup file in a report.

Resonance 0.1 is an early preview. Fixes land in the latest release only.

An exact-link repair is a local, same-origin write, guarded by the current workspace tag and saved evidence fingerprint. It accepts only one imprecise item, refuses conflicting identity or unfinished work, and preserves the original source and report. A link alone never proves authorship or permission; the assistant must still check the account and original before acting.

## Review setup and context previews

`monitor.py --profile [--account ID]` opens the existing database read-only, reads one transaction and performs no upgrade, workspace creation or request-state changes. It returns account metadata, stated brand settings and bounded historical social-choice snapshots. It does not use the full workspace/mail loader. Sample-mode, unknown-account and incompatible-schema reads fail closed. Past choices confer no provider authority.

Assistant reports may include small inline context previews. Each is limited to 256 KiB of decoded PNG/JPEG/WebP bytes, strict base64, a matching file signature and a required description. SVG and remote image sources are refused. Signature checks identify the declared container; they do not certify that an image is harmless or actually inspected. The browser decodes the image in an image element under the existing `img-src 'self' data:` policy. Context images remain private report/API data, are not added to the offline shell cache and are included in ordinary readable backups. No new file-serving route, external fetch or image-analysis service is added. Context remains separate from authored text, and material context changes invalidate earlier action evidence.
