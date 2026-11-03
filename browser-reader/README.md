# Resonance Reader (browser extension, preview 0.1.0)

Source-only note for builders; this file is not part of the shipped package.
This is the transport and package increment only. It is not the finished
first-user flow: store publication, app UI integration, install discovery and
fresh-user proof are separate and not done. Nothing here has run against a real
provider, a real browser or real data.

The extension reads a person's own recent X posts and YouTube comments after
they click **Check now** in the local Resonance app, and hands the complete,
validated result to the page. It does not save anything; the local app saves
after the whole check has been read.

The reader logic (exact text, identity checks, canonical-source rechecks, owned
tab rules, permission/timeout/cancel cleanup) is the R1-accepted code from
`experiments/live-reader-spike`, with Facebook, fixture mode and the dev port
removed. The development experiment is unchanged.

## What ships

`manifest.json` `bridge.js` `shared.js` `reader.js` `worker.js` `options.html`
`options.js` `popup.html` `popup.js`. Build with
`python3 tools/build_reader.py --out <new folder>` (see below). No icon ships: the
only existing artwork (`static/icon.svg`) is the app's, Chrome needs PNGs, and the
approved logo is a private asset. Store art is parent work.

Manifest: name `Resonance Reader`, version `0.1.0`, Manifest V3, Chrome 106+,
`permissions: ["scripting"]`, `host_permissions: ["http://127.0.0.1:4317/*"]`,
optional `https://x.com/*`, `https://www.youtube.com/*`,
`https://myactivity.google.com/*`. One content script
(`shared.js`, `bridge.js`) on `http://127.0.0.1:4317/`, top frame only.
Not allowed in incognito.

## The app page

Chrome match patterns ignore ports, so the bridge enforces the real address:
`location.origin === "http://127.0.0.1:4317"`, the root path with no query, and
top frame. View fragments such as `#overview` and `#accounts` are allowed. The
worker re-checks sender id, tab, frame 0, exact url, origin and document
lifecycle for every connection. Anything else gets no bridge at all.

### Buttons that give authority

An actual trusted (`event.isTrusted`) click on one of these `button` elements,
captured in the capture phase of `document`. Keyboard-generated trusted button
clicks count; synthetic `click()` does not.

| id | gesture it grants |
| --- | --- |
| `res-check-now`, `res-check-account` | one `arm` |
| `res-reader-options` | one `options` |
| `res-check-cancel` | none needed (see `cancel`) |

A gesture lasts for the click's own task (a 0 ms timer clears it) and is used
once, accepted or refused. The page never supplies a URL, code or selector.

### Events

All events are `CustomEvent`s on `document`, prefix `resonance:reader:`, with a
plain-data `detail`. Every detail has exactly the listed keys; extra or missing
keys are refused. IDs are 32 lowercase hex characters.

Page to extension (listen on `document`; dispatch on `document`):

| event | detail | rule |
| --- | --- | --- |
| `arm` | `{batchId, requests:[{requestId, provider, handle, channelId}]}` | Dispatch inside the same trusted Check click's synchronous task. 1 to 20 requests; `provider` is `x` or `youtube`; `handle` has no `@` or URL; `channelId` is `""` or, for YouTube only, a `UC...` channel ID. Unique request IDs, unique `provider+handle` (case-insensitive), unique non-empty channel IDs, none equal to the batch ID. The whole plan is validated before anything is accepted. Pins a copy for 5 seconds. No read starts. |
| `start` | `{batchId}` | The app sends it after its server-issued check ID arrives (within 5 s of `armed`). Only the unexpired, matching, never-started armed plan starts, once. |
| `cancel` | `{batchId}` | No gesture needed (it only removes authority). Stops an armed or running batch. |
| `probe` | `{}` | Read-only fresh connection check; opens no provider page. |
| `options` | `{}` | After a trusted `res-reader-options` click: opens the extension's own options page. |

Extension to page:

| event | detail |
| --- | --- |
| `ready` | `{protocol:1}` the worker connection is up (also the answer to `probe`) |
| `setup` | `{version, protocol:1, x, youtube}`; `version` string or `null`, `x`/`youtube` `true`, `false` or `null` (unavailable). Browser permission state only; never sign-in. Re-sent when permissions change. |
| `disconnected` | `{reason, batchId}`; `batchId` is the aborted batch or `null`. Reasons: `transport_disconnected`, `transport_unavailable`, `extension_context_invalidated`. The last one means the extension was reloaded or removed under this page: tell the person to reload the page. |
| `armed` | `{batchId}` |
| `progress` | `{batchId, requestId, provider, stage}`; stage is `permission`, `identity`, `listing`, `source`, `recheck` or `final_identity` |
| `result` | `{batchId, result}`; `result` is one complete validated original worker `RESULT`, see below |
| `done` | `{batchId}` once, after every request has a terminal result delivered |
| `cancelled` | `{batchId}` once; never followed by `done` |
| `rejected` | `{batchId \| null, reason}`; echoes only a well-formed batch ID, never free input. Reasons: `invalid_input`, `gesture_required`, `wrong_page`, `busy`, `replayed_batch`, `unknown_batch`, `expired`, `transport_unavailable`, `extension_context_invalidated` |

The listeners attach when the page does, so a page that loads after the content
script missed the first `ready`/`setup`: dispatch `probe` on load to get them
again. Only the first `ready` after `probe` on a dropped connection means a new
connection; a `disconnected` while idle has `batchId: null`.

### Result shape

`result` is exactly the worker's message, validated by the bridge against the
request: `{type:"RESULT", requestId, provider, mode:"live", status, tabs:[int...]}`
plus, by status:

* `ok`: `items` (1 to 3), `coverage`, `identity:{handle, channel_id}`. Each item
  `{provider, source_id, url, author, channel_id, text, date_iso, date_label,
  date_unknown, links_shortened}` with the exact text. `coverage` is
  `{limit, accepted, examined, skipped:[{reason,count}], more_available, partial, notes}`.
* `held`, `error` or `cancelled`: `reason` (a lowercase token) and nothing else
  (a YouTube `held` `ownership_not_confirmed` may carry a closed non-content
  `diagnostic`). **No items, no identity.**

Held means the reader stopped on purpose (signed out, a different account is
active, permission missing, a recheck disagreed...). The bridge never forwards a
malformed worker result: it becomes `{status:"error", reason:"result_invalid",
tabs:[]}`. Results the bridge writes itself (watchdog, batch timeout, invalid
result) have `tabs: []`.

### Sequence

1. Trusted click, then in that same task: `arm` -> `armed {batchId}`. If a
   refusal (`rejected`) comes back, nothing started.
2. App calls its backend for a check ID (any time within 5 s), then `start`.
3. Accounts are read one at a time, in plan order. Per account: `progress`...,
   then one `result`. A held or failed account does not stop the others.
4. After the last `result`: `done`.

Never saved by the extension. The app saves only after `done`, and discards the
batch on `cancelled`, on `disconnected` with a `batchId`, on navigation, or when
it never gets `done`.

### Limits and timers

* Per-request watchdog 130 s. If the worker goes silent the account ends with
  `reader_unresponsive`, the worker connection is dropped and the remaining
  accounts end with the same reason; then `done`. (Worker's own per-account
  limit is 120 s with reason `timeout`.)
* Whole batch 480 s from `start`. On expiry the current account and every
  remaining one end with `error` / `batch_timeout`, the worker is told to cancel,
  then `done`. Nothing arriving later is accepted.
* Armed plan 5 s.
* Disconnect or worker restart mid-batch: `disconnected {batchId}`, whole batch
  aborted, no `done`. Nothing reconnects or retries; only a fresh trusted click
  with new IDs reconnects. Old results are ignored.
* The app tab leaving its exact root (a query or fragment counts) stops the batch:
  `cancelled`, no further account starts.
* The extension runs one account at a time across all app tabs; a second
  concurrent start gets `error` / `busy`. A request ID is never reused.

### Other result reasons from the worker

`permission_missing` (held), `permission_lost`, `tab_selected`, `tab_navigated`,
`tab_closed`, `tab_moved`, `user_input`, `root_navigated`, `window_create_failed`,
`page_load_timeout`, `timeout`, `busy`, `replayed_request`, `reader_failed`,
and the identity/recheck holds (`signed_out`, `identity_mismatch`,
`identity_changed`, `channel_id_mismatch`, `ownership_not_confirmed`,
`source_recheck_mismatch`, `no_items_accepted`, ...).

## Own-account discovery (protocol 1 addition, not yet proven natively)

A second job beside the check. It finds which X and YouTube accounts this browser
is currently signed in to, so the app can offer "Use this account". It reads the
signed-in identity and the account menu only: no posts, comments, history, cookies,
storage, credentials or other tabs; it writes nothing to a provider. It adds no file
to the package and no permission; access stays the explicit Allow buttons in the
options page (it never asks). The page-side state machine is
`static/account-discovery.js` (`ResonanceAccountDiscovery`, no DOM, no network, not
shipped in the extension). Parent integration of the confirmation UI is separate.

Page to extension (on `document`, prefix `resonance:reader:`, every shape exact):

| event | detail | rule |
| --- | --- | --- |
| `discover` | `{discoveryId, providers}` | Same synchronous task as a trusted click on `button#res-find-accounts` (own gesture kind; a check click gives none). `discoveryId` is 32 lowercase hex, `providers` is 1 or 2 unique values of `x`, `youtube`. No handle, ID, URL or selector is accepted. Refused while a check is armed or running or a discovery is running; an ID is never accepted twice (shared with batch IDs). |
| `cancel-discovery` | `{discoveryId}` | No gesture (it only removes authority). Stops the running discovery; anything else is silent. |

Extension to page:

| event | detail |
| --- | --- |
| `discovery-accepted` | `{discoveryId}` |
| `discovery-result` | `{discoveryId, provider, status:"ok", identity:{handle, channel_id, profile_url}, observed_at}` or `{discoveryId, provider, status:"held"\|"error", reason}`; one per provider, in the order asked |
| `discovery-done` | `{discoveryId}` once, after every provider's result |
| `discovery-cancelled` | `{discoveryId}` once; never followed by a result or `discovery-done` |
| `discovery-rejected` | `{discoveryId \| null, reason}`: `invalid_input`, `gesture_required`, `wrong_page`, `busy`, `replayed_discovery`, `transport_unavailable`, `extension_context_invalidated` |
| `discovery-aborted` | `{discoveryId, reason}`: the worker connection dropped (also sent as `disconnected` with `batchId:null`); no result or done follows |

`discovery-rejected` and `discovery-aborted` are additions to the brief's event list
(same pattern as `rejected` / `disconnected` for checks). The `ok` identity: X
`{handle, channel_id:null, profile_url:"https://x.com/<handle>"}`; YouTube `{handle,
channel_id:"UC...", profile_url:"https://www.youtube.com/channel/UC..."}`, the
address always derived from the identity, never from the page. `observed_at` is an
ISO time stamped by the worker. Reasons are lowercase tokens: held
`permission_needed`, `signed_out`, `identity_unreadable`, `identity_not_ready`,
`identity_mismatch`, `identity_changed`, `current_handle_unavailable`,
`channel_id_conflict`, `ownership_not_confirmed`, `menu_close_failed`; error
`busy`, `timeout`, `permission_lost`, `tab_selected`, `user_input`, `root_navigated`,
`window_create_failed`, `page_load_timeout`, `reader_unresponsive`, `result_invalid`,
`reader_failed`, and the other tab reasons of a check.

Messages between bridge and worker are separate types with their own validators
(`DISCOVER`, `CANCEL_DISCOVERY`, `DISCOVERY_RESULT`), never the check `START` /
`RESULT`; a discovery result is not accepted as a post-reading success and the
reverse. The same worker rules apply: sender id/tab/frame/url/origin/lifecycle on
every connection, only the current port speaks, one job at a time across the
extension (`busy`), no replay (`replayed_request` at the worker).

Flow. The bridge asks one provider at a time (`DISCOVER`), each limited to 130 s by
a bridge watchdog (the worker stops itself at 120 s, `timeout`), never retried.
A silent worker ends the current and every remaining provider with
`reader_unresponsive`, drops the connection and then `discovery-done`. Root
reload/navigation, disconnect, permission loss, a user selecting an owned tab,
trusted input in it and the timeout abandon the work; late replies find nothing to
match and are ignored. Provider tabs/windows are never closed, navigated or
reselected, and cancelling only revokes ownership. Missing permission gives a held
`permission_needed` for that provider and opens nothing.

* X: an owned background tab at the fixed `https://x.com/home` reads the active
  profile link plus account switcher with the vetted `readXIdentity` (new reader op
  `x_whoami`; the page supplies no expected handle). One valid handle is the only
  suggestion. A new owned tab at exactly that handle's profile address then
  rechecks the active handle with the vetted `x_identity` op. A match returns the
  handle with `channel_id:null`; signed out, ambiguous, reserved/foreign/loading or
  a different handle holds. Identities are never switched.
* YouTube: a separate visible owned window at the fixed `https://www.youtube.com/`
  opens the avatar menu (new ops `yt_home_menu_open|read|close`, same code as the
  vetted menu ops, plus a sign-in check) and reads the single visible "View your
  channel" link; the menu is closed in `finally`, and a menu that cannot be closed
  holds `menu_close_failed`. A valid current `@handle` is verified by the vetted
  `verifyYouTubeIdentity` in a new owned channel tab (own-channel controls give the
  stable UC ID and prove the active account; its own menu must agree), then a second
  fresh owned channel document must agree before success. A menu that gives only a
  `/channel/UC...` link holds `current_handle_unavailable`: a channel ID is not an
  input and manual profile addition is the fallback. Ambiguous, foreign, loading,
  signed-out or changed identities hold. This supports only identities the current
  semantic controls prove; no parser for unseen metadata was invented.

Destinations. A discovery job may open only `https://x.com/home`, the exact profile
of the handle it observed, `https://www.youtube.com/` and the exact `/@handle`
channel of the handle it observed (`validDiscoveryDestination`). Ordinary check
jobs use the unchanged `validDestination`: the home pages are not read routes.

Not done: native Chrome/Edge proof (real X and YouTube home pages, the account
menu control, a signed-out redirect to a login flow which this code reports as
`tab_navigated`, MV3 worker idle behaviour with a discovery in flight), a store
install, the app's confirmation UI and the extension version/protocol bump that
would let the app detect an installed reader without discovery (the manifest is
still 0.1.0, protocol 1; an older reader never answers `discover`, and the
controller's `expireStalled` is the app-side backstop).

## Options and popup

`options.html` has per-provider **Allow** and **Remove** buttons. Each calls
`permissions.request` / `permissions.remove` inside its own trusted click. It
explains the host-wide wording versus the reader's narrow code, own text only, up
to three items per account, media not inspected, the separate visible YouTube
window, no provider writes, and that the local app saves after reading. It links
back to `http://127.0.0.1:4317/`. The popup repeats that briefly and has two
buttons: open the app root, open options. There are no store links and no generic
extension-management URLs.

## Package

`tools/build_reader.py --out <folder that does not exist>` validates the manifest,
protocol and file allowlist, refuses symlinks, unexpected files and forbidden
content, then writes `resonance-reader-0.1.0.zip` (fixed timestamps and modes,
sorted entries, byte-identical on repeat) and `resonance-reader-0.1.0.sha256.json`
(zip and per-file SHA256). It does not sign, upload or publish.

Tests: `node tests/test_browser_reader.cjs` (uses the development harness and
invented fixtures under `experiments/live-reader-spike` for the fake browser only;
the shipped files import nothing outside this folder).
