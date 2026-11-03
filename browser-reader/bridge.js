"use strict";

// Isolated content script for the exact local app root only (see README.md for
// the full page protocol). Only an actual trusted click on one of the app's four
// known buttons gives the page one chance to arm a check or open the options
// page. The page supplies expected handles and optionally a stable channel ID,
// never a URL, selector or script. One check ("batch") reads up to twenty
// planned accounts one after another through the worker, which reads a single
// account per request. A second, separate job ("discovery") can find which X and
// YouTube accounts the browser is currently signed in to; it needs its own
// trusted click, takes no account input from the page and never overlaps a check.
(() => {
  const S = globalThis.ResonanceReader;
  const ORIGIN = S.LOCAL;
  const ROOT_URL = `${ORIGIN}/`;
  // Chrome match patterns ignore ports, so the exact origin, root address and
  // top frame are enforced here and again by the worker for every connection.
  const appRoot = () => location.origin === ORIGIN &&
    typeof location.href === "string" && location.href.split("#", 1)[0] === ROOT_URL;
  if (window.top !== window || !appRoot()) return;

  const PROTOCOL = 1;
  const PORT_NAME = "resonance-reader:root";
  const PREFIX = "resonance:reader:";
  const PAGE_EVENTS = ["arm", "start", "cancel", "probe", "options", "discover", "cancel-discovery"];
  const STAGES = new Set(["permission", "identity", "listing", "source", "recheck", "final_identity"]);
  const BUTTONS = Object.freeze({
    "res-check-now": "check",
    "res-check-account": "check",
    "res-reader-options": "options",
    "res-find-accounts": "discover",
    "res-check-cancel": "cancel",
  });
  const MAX_REQUESTS = 20;
  // An armed plan is only a pin on a validated list: the app has this long to
  // obtain its server-issued check ID before it must send start.
  const ARM_MS = 5000;
  // The worker ends a job by itself after two minutes. This is the bridge-side
  // backstop for a reader that never answers at all, armed per request.
  const REQUEST_WATCHDOG_MS = 130000;
  // The whole check, from start until its last terminal result.
  const BATCH_MS = 480000;
  const MAX_USED_IDS = 1024;
  const MAX_ENDED = 64;

  let port = null;
  let gesture = null;
  let batch = null; // { id, requests, state: "armed" | "running", index, current, armTimer, batchTimer }
  let discovery = null; // { id, providers, index, current: { provider, watchdog } }; never alongside a batch
  const usedIds = new Set(); // every batch and request ID ever accepted by arm (bounded)
  const ended = new Map(); // batch ID -> how it ended (bounded), only to say why a start was refused
  let connectFailure = "transport_unavailable";

  const isId = value => typeof value === "string" && S.REQUEST_ID.test(value);
  const emit = (name, detail) => document.dispatchEvent(new CustomEvent(PREFIX + name, { detail }));
  const rootOk = () => window.top === window && appRoot();
  const refuse = (batchId, reason) => emit("rejected", { batchId: isId(batchId) ? batchId : null, reason });
  function remember(id) {
    usedIds.add(id);
    if (usedIds.size > MAX_USED_IDS) usedIds.delete(usedIds.values().next().value);
  }
  function markEnded(id, how) {
    ended.delete(id);
    ended.set(id, how);
    if (ended.size > MAX_ENDED) ended.delete(ended.keys().next().value);
  }
  // A terminal result the bridge itself writes (the worker never produced it).
  const errorResult = (request, reason) => ({ type: "RESULT", requestId: request.requestId,
    provider: request.provider, mode: "live", status: "error", tabs: [], reason });
  const clone = value => JSON.parse(JSON.stringify(value));

  // True when the extension was reloaded or removed under this page: only a page
  // reload can reattach then. Anything else is retried by a fresh click.
  function contextGone(error) {
    try {
      return !chrome.runtime || !chrome.runtime.id || /context invalidated/i.test(String(error && error.message));
    } catch (_) {
      return true;
    }
  }

  // ---- batch lifecycle ----------------------------------------------------
  // The one way a batch stops being armed or running: every timer ends with it,
  // so every terminal path cleans up the same way.
  function release(b, how) {
    if (b.armTimer !== undefined) { clearTimeout(b.armTimer); b.armTimer = undefined; }
    if (b.batchTimer !== undefined) { clearTimeout(b.batchTimer); b.batchTimer = undefined; }
    if (b.current && b.current.watchdog !== undefined) { clearTimeout(b.current.watchdog); b.current.watchdog = undefined; }
    if (batch === b) batch = null;
    markEnded(b.id, how);
  }
  function post(message) {
    try { if (port) { port.postMessage(message); return true; } } catch (_) { /* best effort */ }
    return false;
  }
  function finish(b) {
    release(b, "done");
    emit("done", { batchId: b.id });
  }
  // Ends the whole batch because the worker connection is gone. Nothing is
  // retried or restarted; results already delivered stay delivered.
  function lost(reason, old) {
    if (old !== undefined && port !== old) return;
    const b = batch;
    const d = discovery;
    const gone = port;
    port = null;
    try { if (gone) gone.disconnect(); } catch (_) { /* best effort */ }
    if (b) release(b, "aborted");
    if (d) releaseDiscovery(d);
    emit("disconnected", { reason, batchId: b ? b.id : null });
    // Whatever the discovery had already delivered stays with the page, which
    // decides what to keep; nothing more is reported for it and nothing retries.
    if (d) emit("discovery-aborted", { discoveryId: d.id, reason });
  }
  function sendStart(request) {
    try {
      port.postMessage({ type: "START", requestId: request.requestId, provider: request.provider, mode: "live",
        handle: request.handle, channelId: request.channelId === null ? "" : request.channelId });
    } catch (error) {
      lost(contextGone(error) ? "extension_context_invalidated" : "transport_disconnected");
    }
  }
  function next(b) {
    if (batch !== b) return;
    if (b.index + 1 >= b.requests.length) { finish(b); return; }
    if (!rootOk()) { stop(b); return; } // The app left its exact root: nothing more is read.
    b.index += 1;
    const request = b.requests[b.index];
    const current = { requestId: request.requestId, provider: request.provider, mode: "live",
      handle: request.handle, channelId: request.channelId, watchdog: undefined };
    b.current = current;
    current.watchdog = setTimeout(() => expireRequest(b, current), REQUEST_WATCHDOG_MS);
    sendStart(request);
  }
  // Cancel, or a page that left its root: remove all authority, tell the worker,
  // discard what is queued and say so once. Never a done event.
  function stop(b) {
    const current = b.current;
    release(b, "cancelled");
    if (current) post({ type: "CANCEL", requestId: current.requestId });
    emit("cancelled", { batchId: b.id });
  }
  // Every request not yet answered ends with the same explicit error, then the
  // batch is over: nothing late is accepted afterwards.
  function failRest(b, reason) {
    const current = b.current;
    b.current = null;
    if (current && current.watchdog !== undefined) clearTimeout(current.watchdog);
    const pending = [];
    if (current) pending.push(current);
    for (let i = b.index + 1; i < b.requests.length; i += 1) pending.push(b.requests[i]);
    b.index = b.requests.length; // Nothing is queued any more.
    for (const request of pending) {
      if (batch !== b) return;
      emit("result", { batchId: b.id, result: errorResult(request, reason) });
    }
  }
  function expireBatch(b) {
    if (batch !== b || b.state !== "running") return;
    const current = b.current;
    if (current) post({ type: "CANCEL", requestId: current.requestId });
    failRest(b, "batch_timeout");
    if (batch === b) finish(b);
  }
  // The watchdog fired for this exact request: the worker went quiet. State is
  // revoked first, so nothing from the old request or its port can act later.
  // There is no reconnect or restart: the account and the rest of the batch end
  // with explicit errors, and only a fresh trusted click can begin another check.
  function expireRequest(b, request) {
    if (batch !== b || b.current !== request) return;
    const old = port;
    port = null;
    try { if (old) old.postMessage({ type: "CANCEL", requestId: request.requestId }); } catch (_) { /* best effort */ }
    try { if (old) old.disconnect(); } catch (_) { /* best effort */ }
    failRest(b, "reader_unresponsive");
    if (batch === b) finish(b);
  }

  // ---- discovery lifecycle ------------------------------------------------
  // One discovery at a time, never alongside a batch. Providers run one after the
  // other, each asked of the worker once and never retried. Every way a discovery
  // stops goes through releaseDiscovery, so late worker replies find nothing to
  // match and are ignored.
  const refuseDiscovery = (discoveryId, reason) =>
    emit("discovery-rejected", { discoveryId: isId(discoveryId) ? discoveryId : null, reason });
  function releaseDiscovery(d) {
    if (d.current && d.current.watchdog !== undefined) { clearTimeout(d.current.watchdog); d.current.watchdog = undefined; }
    if (discovery === d) discovery = null;
  }
  function finishDiscovery(d) {
    releaseDiscovery(d);
    emit("discovery-done", { discoveryId: d.id });
  }
  // Cancel, or a page that left its root: remove all authority, tell the worker,
  // say so once. Never a result or done event afterwards.
  function stopDiscovery(d) {
    const current = d.current;
    releaseDiscovery(d);
    if (current) post({ type: "CANCEL_DISCOVERY", discoveryId: d.id });
    emit("discovery-cancelled", { discoveryId: d.id });
  }
  function nextDiscovery(d) {
    if (discovery !== d) return;
    if (d.index + 1 >= d.providers.length) { finishDiscovery(d); return; }
    if (!rootOk()) { stopDiscovery(d); return; } // The app left its exact root: nothing more is opened.
    d.index += 1;
    const current = { provider: d.providers[d.index], watchdog: undefined };
    d.current = current;
    current.watchdog = setTimeout(() => expireDiscovery(d, current), REQUEST_WATCHDOG_MS);
    try {
      port.postMessage({ type: "DISCOVER", discoveryId: d.id, provider: current.provider });
    } catch (error) {
      lost(contextGone(error) ? "extension_context_invalidated" : "transport_disconnected");
    }
  }
  // The worker went quiet for this provider. Like a silent check, the provider
  // and every one after it end with the same explicit error, the connection is
  // dropped (nothing late can arrive on it) and then the discovery is done.
  function expireDiscovery(d, current) {
    if (discovery !== d || d.current !== current) return;
    const old = port;
    port = null;
    try { if (old) old.postMessage({ type: "CANCEL_DISCOVERY", discoveryId: d.id }); } catch (_) { /* best effort */ }
    try { if (old) old.disconnect(); } catch (_) { /* best effort */ }
    const pending = d.providers.slice(d.index);
    d.current = null;
    d.index = d.providers.length;
    for (const provider of pending) {
      if (discovery !== d) return;
      emit("discovery-result", { discoveryId: d.id, provider, status: "error", reason: "reader_unresponsive" });
    }
    if (discovery === d) finishDiscovery(d);
  }
  function onDiscoveryResult(message) {
    const d = discovery;
    const current = d ? d.current : null;
    if (!current || !message || message.discoveryId !== d.id || message.provider !== current.provider) return; // Old or unsolicited.
    clearTimeout(current.watchdog);
    current.watchdog = undefined;
    d.current = null;
    const request = { discoveryId: d.id, provider: current.provider };
    let detail = { discoveryId: d.id, provider: current.provider, status: "error", reason: "result_invalid" };
    if (S.validateDiscoveryResult(message, request)) {
      detail = message.status === "ok"
        ? { discoveryId: d.id, provider: message.provider, status: "ok", identity: clone(message.identity),
          observed_at: message.observed_at }
        : { discoveryId: d.id, provider: message.provider, status: message.status, reason: message.reason };
    }
    emit("discovery-result", detail);
    nextDiscovery(d);
  }

  // ---- worker connection --------------------------------------------------
  function connect() {
    if (port) return port;
    try {
      const fresh = chrome.runtime.connect({ name: PORT_NAME });
      port = fresh;
      fresh.onMessage.addListener(message => {
        if (port !== fresh) return;
        if (S.exactShape(message, ["type"]) && message.type === "READY") {
          emit("ready", { protocol: PROTOCOL });
          return;
        }
        // Setup feedback from the current port only. It is independent of any batch:
        // it never touches the active request, its watchdog or the result checks,
        // and a malformed or extended one is simply dropped.
        if (message && message.type === "SETUP") {
          if (S.validSetup(message)) {
            emit("setup", { version: message.version, protocol: PROTOCOL, x: message.x, youtube: message.youtube });
          }
          return;
        }
        if (message && message.type === "DISCOVERY_RESULT") { onDiscoveryResult(message); return; }
        const b = batch;
        const current = b && b.state === "running" ? b.current : null;
        if (!current || !message || message.requestId !== current.requestId ||
            message.provider !== current.provider || message.mode !== current.mode) return; // Old or unsolicited.
        if (S.exactShape(message, ["type", "requestId", "provider", "mode", "stage"]) &&
            message.type === "PROGRESS" && STAGES.has(message.stage)) {
          emit("progress", { batchId: b.id, requestId: current.requestId, provider: current.provider, stage: message.stage });
          return;
        }
        if (message.type !== "RESULT") return; // Unknown or malformed progress is dropped.
        clearTimeout(current.watchdog);
        b.current = null;
        const result = S.validateResult(message, current) ? clone(message) : errorResult(current, "result_invalid");
        emit("result", { batchId: b.id, result });
        next(b);
      });
      fresh.onDisconnect.addListener(() => {
        void chrome.runtime.lastError;
        lost("transport_disconnected", fresh);
      });
      return fresh;
    } catch (error) {
      connectFailure = contextGone(error) ? "extension_context_invalidated" : "transport_unavailable";
      return null;
    }
  }

  // ---- trusted gestures ---------------------------------------------------
  document.addEventListener("click", event => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    const button = event.target.closest("button");
    if (!button || !Object.hasOwn(BUTTONS, button.id)) return;
    const fresh = { kind: BUTTONS[button.id], used: false };
    gesture = fresh;
    setTimeout(() => { if (gesture === fresh) gesture = null; }, 0);
  }, true);

  // ---- page events --------------------------------------------------------
  // The whole plan is validated before anything is accepted. Identities and IDs
  // must be unique; requestIds, batchId and handles are checked by the same
  // validators the worker applies again to every request.
  function validatePlan(detail) {
    if (!S.exactShape(detail, ["batchId", "requests"]) || !isId(detail.batchId) ||
        !Array.isArray(detail.requests) || detail.requests.length < 1 || detail.requests.length > MAX_REQUESTS) return null;
    const ids = new Set([detail.batchId]);
    const identities = new Set();
    const channels = new Set();
    const requests = [];
    for (const raw of detail.requests) {
      if (!S.exactShape(raw, ["requestId", "provider", "handle", "channelId"])) return null;
      const request = S.validateStart({ type: "START", requestId: raw.requestId, provider: raw.provider,
        mode: "live", handle: raw.handle, channelId: raw.channelId });
      if (!request) return null;
      const identity = `${request.provider}:${S.lower(request.handle)}`;
      if (ids.has(request.requestId) || identities.has(identity)) return null;
      if (request.channelId !== null && channels.has(request.channelId)) return null;
      ids.add(request.requestId);
      identities.add(identity);
      if (request.channelId !== null) channels.add(request.channelId);
      requests.push(Object.freeze({ requestId: request.requestId, provider: request.provider,
        handle: request.handle, channelId: request.channelId }));
    }
    return { batchId: detail.batchId, requests: Object.freeze(requests), ids: Array.from(ids) };
  }

  function onArm(detail) {
    const claimed = detail && typeof detail === "object" ? detail.batchId : null;
    const g = gesture;
    if (!g || g.used || g.kind !== "check") { refuse(claimed, "gesture_required"); return; }
    g.used = true; // One chance per trusted click, accepted or not.
    if (!rootOk()) { refuse(claimed, "wrong_page"); return; }
    const plan = validatePlan(detail);
    if (!plan) { refuse(claimed, "invalid_input"); return; }
    if (batch || discovery) { refuse(plan.batchId, "busy"); return; }
    if (plan.ids.some(id => usedIds.has(id))) { refuse(plan.batchId, "replayed_batch"); return; }
    // A fresh deliberate click reconnects an idle or disconnected worker. This is
    // the only place a connection is re-made for a check; nothing reconnects on its own.
    if (!connect()) { refuse(plan.batchId, connectFailure); return; }
    for (const id of plan.ids) remember(id);
    const b = { id: plan.batchId, requests: plan.requests, state: "armed", index: -1, current: null,
      armTimer: undefined, batchTimer: undefined };
    batch = b;
    b.armTimer = setTimeout(() => {
      if (batch !== b || b.state !== "armed") return;
      release(b, "expired");
    }, ARM_MS);
    emit("armed", { batchId: b.id });
  }

  function onStart(detail) {
    if (!S.exactShape(detail, ["batchId"]) || !isId(detail.batchId)) { refuse(null, "invalid_input"); return; }
    const id = detail.batchId;
    const b = batch;
    if (!b || b.id !== id) {
      const how = ended.get(id);
      refuse(id, how === undefined ? "unknown_batch" : how === "expired" ? "expired" : "replayed_batch");
      return;
    }
    if (b.state !== "armed") { refuse(id, "replayed_batch"); return; }
    if (!rootOk() || !port) { release(b, "aborted"); refuse(id, "wrong_page"); return; }
    clearTimeout(b.armTimer);
    b.armTimer = undefined;
    b.state = "running";
    b.batchTimer = setTimeout(() => expireBatch(b), BATCH_MS);
    next(b);
  }

  function onCancel(detail) {
    if (!S.exactShape(detail, ["batchId"]) || !isId(detail.batchId)) { refuse(null, "invalid_input"); return; }
    // Cancel needs no gesture: it can only remove authority. Anything that is
    // not the current armed or running batch is already over, and says nothing.
    if (batch && batch.id === detail.batchId) stop(batch);
  }

  // A fresh, read-only connection check for install detection. It opens nothing
  // and reads no provider page: the worker answers with its ready and setup events.
  function onProbe(detail) {
    if (!S.exactShape(detail, [])) { refuse(null, "invalid_input"); return; }
    if (!rootOk()) { refuse(null, "wrong_page"); return; }
    const fresh = port === null;
    const connection = connect();
    if (!connection) { emit("disconnected", { reason: connectFailure, batchId: null }); return; }
    if (fresh) return; // A new connection is answered by the worker on its own.
    if (!post({ type: "PROBE" })) lost("transport_disconnected");
  }

  function onOptions(detail) {
    if (!S.exactShape(detail, [])) { refuse(null, "invalid_input"); return; }
    const g = gesture;
    if (!g || g.used || g.kind !== "options") { refuse(null, "gesture_required"); return; }
    g.used = true;
    if (!rootOk()) { refuse(null, "wrong_page"); return; }
    const connection = connect();
    if (!connection) { emit("disconnected", { reason: connectFailure, batchId: null }); return; }
    if (!post({ type: "OPEN_OPTIONS" })) lost("transport_disconnected");
  }

  // Needs the same kind of trusted click as a check, on its own button. The page
  // names only which providers to look at: never a handle, ID, URL or selector.
  function onDiscover(detail) {
    const claimed = detail && typeof detail === "object" ? detail.discoveryId : null;
    const g = gesture;
    if (!g || g.used || g.kind !== "discover") { refuseDiscovery(claimed, "gesture_required"); return; }
    g.used = true; // One chance per trusted click, accepted or not.
    if (!rootOk()) { refuseDiscovery(claimed, "wrong_page"); return; }
    const plan = S.validateDiscoverInput(detail);
    if (!plan) { refuseDiscovery(claimed, "invalid_input"); return; }
    if (batch || discovery) { refuseDiscovery(plan.discoveryId, "busy"); return; }
    if (usedIds.has(plan.discoveryId)) { refuseDiscovery(plan.discoveryId, "replayed_discovery"); return; }
    if (!connect()) { refuseDiscovery(plan.discoveryId, connectFailure); return; }
    remember(plan.discoveryId);
    const d = { id: plan.discoveryId, providers: Object.freeze(plan.providers), index: -1, current: null };
    discovery = d;
    emit("discovery-accepted", { discoveryId: d.id });
    nextDiscovery(d);
  }

  // Cancel needs no gesture: it can only remove authority. Anything that is not
  // the current discovery is already over, and says nothing.
  function onCancelDiscovery(detail) {
    if (!S.exactShape(detail, ["discoveryId"]) || !isId(detail.discoveryId)) { refuseDiscovery(null, "invalid_input"); return; }
    if (discovery && discovery.id === detail.discoveryId) stopDiscovery(discovery);
  }

  const handlers = { arm: onArm, start: onStart, cancel: onCancel, probe: onProbe, options: onOptions,
    discover: onDiscover, "cancel-discovery": onCancelDiscovery };
  for (const name of PAGE_EVENTS) {
    document.addEventListener(PREFIX + name, event => {
      if (event.target !== document) return;
      handlers[name](event.detail);
    });
  }

  connect();
})();
