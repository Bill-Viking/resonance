'use strict';

// Own-account discovery controller. It speaks only the page side of the
// resonance:reader:discover event protocol through injected functions and holds
// what the reader found in memory for a few minutes. It has no DOM and no network
// access, saves nothing and never turns a result into an account: the app asks
// the person ("Use this account") and does the saving. It starts only when the
// caller says an explicit trusted click on button#res-find-accounts happened, and
// start() must run in that click's own synchronous task (the bridge refuses it
// otherwise). Results are checked again here (exact shape, derived profile
// address, timestamp) because the page code is a separate trust boundary.
(function (scope, factory) {
  const api = factory();
  if (typeof module === 'object' && module && module.exports) module.exports = api;
  else scope.ResonanceAccountDiscovery = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const PREFIX = 'resonance:reader:';
  const TTL_MS = 5 * 60 * 1000;
  // The extension and the page share one computer; this only absorbs small clock steps.
  const SKEW_MS = 60 * 1000;
  const PROVIDERS = Object.freeze(['x', 'youtube']);
  const ID32 = /^[a-f0-9]{32}$/;
  const TOKEN = /^[a-z_]{1,48}$/;
  const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,3})?Z$/;
  const X_HANDLE = /^[A-Za-z0-9_]{1,15}$/;
  const X_RESERVED = new Set(['home','explore','notifications','messages','search','settings','i','compose','login','logout','signup','tos','privacy','intent','share','hashtag','account','jobs','help','about']);
  const YT_HANDLE = /^[A-Za-z0-9._-]{3,30}$/;
  const YT_CHANNEL = /^UC[A-Za-z0-9_-]{22}$/;
  const ACTIVE = new Set(['starting', 'running']);

  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exactKeys = (value, keys) => isObject(value) && Object.keys(value).sort().join(',') === keys.slice().sort().join(',');
  const clone = value => JSON.parse(JSON.stringify(value));

  function validProviders(list) {
    return Array.isArray(list) && list.length >= 1 && list.length <= 2 &&
      list.every(p => PROVIDERS.includes(p)) && new Set(list).size === list.length;
  }

  // The identity a result may carry. The profile address must be exactly the one
  // derived from the identity (X: the handle; YouTube: the stable channel ID).
  function validIdentity(provider, identity) {
    if (!exactKeys(identity, ['handle', 'channel_id', 'profile_url'])) return false;
    if (provider === 'x') {
      return typeof identity.handle === 'string' && X_HANDLE.test(identity.handle) &&
        !X_RESERVED.has(identity.handle.toLowerCase()) && identity.channel_id === null &&
        identity.profile_url === 'https://x.com/' + identity.handle;
    }
    if (provider !== 'youtube') return false;
    return typeof identity.handle === 'string' && YT_HANDLE.test(identity.handle) &&
      typeof identity.channel_id === 'string' && YT_CHANNEL.test(identity.channel_id) &&
      identity.profile_url === 'https://www.youtube.com/channel/' + identity.channel_id;
  }

  // Exact result shape. `bounds` (optional {min, max} in ms) limits observed_at.
  function validateResult(detail, bounds) {
    if (!isObject(detail) || typeof detail.discoveryId !== 'string' || !ID32.test(detail.discoveryId) ||
        !PROVIDERS.includes(detail.provider)) return false;
    if (detail.status === 'ok') {
      if (!exactKeys(detail, ['discoveryId', 'provider', 'status', 'identity', 'observed_at']) ||
          !validIdentity(detail.provider, detail.identity) ||
          typeof detail.observed_at !== 'string' || !ISO.test(detail.observed_at)) return false;
      const at = Date.parse(detail.observed_at);
      if (!Number.isFinite(at)) return false;
      return !bounds || (at >= bounds.min && at <= bounds.max);
    }
    if (detail.status !== 'held' && detail.status !== 'error') return false;
    return exactKeys(detail, ['discoveryId', 'provider', 'status', 'reason']) &&
      typeof detail.reason === 'string' && TOKEN.test(detail.reason);
  }

  // options: { emit(type, detail), on(type, handler) -> off, change(handler) -> off,
  //            clock() -> ms, randomId?() -> 32 hex }
  // emit sends a page event, on listens to an extension event (handler gets the
  // event's detail), change reports a workspace change, clock is the time source.
  function create(options) {
    const o = options || {};
    for (const name of ['emit', 'on', 'change', 'clock']) {
      if (typeof o[name] !== 'function') throw new TypeError('ResonanceAccountDiscovery needs a ' + name + ' function');
    }
    const randomId = typeof o.randomId === 'function' ? o.randomId : defaultRandomId;
    const listeners = new Set();
    const offs = [];
    // session: the one discovery this controller knows about. null when idle or
    // finished with; late events for anything else are ignored.
    let session = null; // { id, providers, status: 'starting'|'running'|'done', startedAt, results: Map }
    let ended = { status: 'idle', reason: null, discoveryId: null, providers: [] };
    let ignored = 0;

    const now = () => {
      const t = o.clock();
      if (typeof t !== 'number' || !Number.isFinite(t)) throw new Error('clock_unavailable');
      return t;
    };
    function notify() {
      const snapshot = view();
      for (const fn of Array.from(listeners)) { try { fn(snapshot); } catch (_) { /* a listener must not break the controller */ } }
    }
    // Drops results whose five minutes are over. Lazy: no timers.
    function purge() {
      if (!session) return;
      let t;
      try { t = now(); } catch (_) { return; }
      for (const [provider, entry] of Array.from(session.results)) {
        if (entry.expires_at <= t) session.results.delete(provider);
      }
    }
    function view() {
      purge();
      const live = session;
      const results = live ? Array.from(live.results.values()).map(clone) : [];
      return Object.freeze({
        status: live ? live.status : ended.status,
        reason: live ? null : ended.reason,
        discoveryId: live ? live.id : ended.discoveryId,
        providers: live ? live.providers.slice() : ended.providers.slice(),
        results,
        ignored,
      });
    }
    function finish(status, reason) {
      const was = session;
      session = null;
      ended = { status, reason: reason || null, discoveryId: was ? was.id : null, providers: was ? was.providers.slice() : [] };
      notify();
    }

    // ---- extension events -------------------------------------------------
    const mine = detail => session !== null && isObject(detail) && detail.discoveryId === session.id;
    function onAccepted(detail) {
      if (!exactKeys(detail, ['discoveryId']) || !mine(detail) || session.status !== 'starting') { ignored += 1; return; }
      session.status = 'running';
      notify();
    }
    function onResult(detail) {
      if (!mine(detail) || session.status !== 'running') { ignored += 1; return; }
      let t;
      try { t = now(); } catch (_) { ignored += 1; return; }
      const bounds = { min: session.startedAt - SKEW_MS, max: t + SKEW_MS };
      if (!validateResult(detail, bounds) || !session.providers.includes(detail.provider) ||
          session.results.has(detail.provider)) { ignored += 1; return; }
      const entry = clone(detail);
      delete entry.discoveryId;
      entry.expires_at = t + TTL_MS;
      session.results.set(detail.provider, entry);
      notify();
    }
    function onDone(detail) {
      if (!exactKeys(detail, ['discoveryId']) || !mine(detail) || session.status !== 'running') { ignored += 1; return; }
      // Done is only valid after every selected provider reported.
      if (session.results.size !== session.providers.length) { finish('aborted', 'incomplete_results'); return; }
      session.status = 'done';
      notify();
    }
    function onCancelled(detail) {
      if (!exactKeys(detail, ['discoveryId']) || !mine(detail) || !ACTIVE.has(session.status)) { ignored += 1; return; }
      finish('cancelled', null);
    }
    function onAborted(detail) {
      if (!exactKeys(detail, ['discoveryId', 'reason']) || !mine(detail) || !ACTIVE.has(session.status) ||
          typeof detail.reason !== 'string' || !TOKEN.test(detail.reason)) { ignored += 1; return; }
      finish('aborted', detail.reason);
    }
    function onRejected(detail) {
      if (!exactKeys(detail, ['discoveryId', 'reason']) || typeof detail.reason !== 'string' || !TOKEN.test(detail.reason) ||
          !session || session.status !== 'starting' ||
          !(detail.discoveryId === null || detail.discoveryId === session.id)) { ignored += 1; return; }
      finish('rejected', detail.reason);
    }
    // The reader connection dropped (or the extension was reloaded): an active
    // discovery is abandoned and its late replies will be ignored.
    function onDisconnected(detail) {
      if (!exactKeys(detail, ['reason', 'batchId']) || !session || !ACTIVE.has(session.status)) { ignored += 1; return; }
      finish('aborted', typeof detail.reason === 'string' && TOKEN.test(detail.reason) ? detail.reason : 'transport_disconnected');
    }
    const handlers = { 'discovery-accepted': onAccepted, 'discovery-result': onResult, 'discovery-done': onDone,
      'discovery-cancelled': onCancelled, 'discovery-aborted': onAborted, 'discovery-rejected': onRejected,
      disconnected: onDisconnected };
    for (const name of Object.keys(handlers)) {
      const off = o.on(PREFIX + name, handlers[name]);
      if (typeof off === 'function') offs.push(off);
    }

    // ---- caller operations ------------------------------------------------
    // Must be called synchronously from the trusted click on button#res-find-accounts.
    // Returns { ok: true, discoveryId } or { ok: false, reason }.
    function start(providers) {
      if (session && ACTIVE.has(session.status)) return { ok: false, reason: 'busy' };
      if (!validProviders(providers)) return { ok: false, reason: 'invalid_input' };
      let startedAt;
      try { startedAt = now(); } catch (_) { return { ok: false, reason: 'clock_unavailable' }; }
      const id = randomId();
      if (typeof id !== 'string' || !ID32.test(id)) return { ok: false, reason: 'id_unavailable' };
      // The session exists before the event goes out: the bridge answers inside
      // the same synchronous call.
      session = { id, providers: providers.slice(), status: 'starting', startedAt, results: new Map() };
      ended = { status: 'idle', reason: null, discoveryId: null, providers: [] };
      try {
        o.emit(PREFIX + 'discover', { discoveryId: id, providers: providers.slice() });
      } catch (_) {
        if (session && session.id === id) finish('aborted', 'emit_failed');
        return { ok: false, reason: 'emit_failed' };
      }
      if (!session || session.id !== id) return { ok: false, reason: ended.reason || 'not_started' };
      notify();
      return { ok: true, discoveryId: id };
    }
    // Revokes active work and forgets what it had found. Nothing to do when idle
    // or already finished (use clear() to forget finished results).
    function cancel() {
      if (!session || !ACTIVE.has(session.status)) return false;
      const id = session.id;
      finish('cancelled', null); // Late replies are ignored from here on.
      try { o.emit(PREFIX + 'cancel-discovery', { discoveryId: id }); } catch (_) { /* revoked locally either way */ }
      return true;
    }
    // Cancels anything active, then forgets every result. Also what a workspace change does.
    function clear() {
      const had = session !== null;
      cancel();
      session = null;
      ended = { status: 'idle', reason: null, discoveryId: null, providers: [] };
      if (had) notify();
    }
    // ok results the person may still confirm (unexpired copies).
    function candidates() {
      purge();
      return session ? Array.from(session.results.values()).filter(r => r.status === 'ok').map(clone) : [];
    }
    // Forget one provider's result (after "Use this account", or declining it).
    function discard(provider) {
      if (!session || !session.results.delete(provider)) return false;
      notify();
      return true;
    }
    // The earliest moment a held result expires, so the app can refresh then; null if none.
    function nextExpiry() {
      purge();
      if (!session || session.results.size === 0) return null;
      return Math.min(...Array.from(session.results.values()).map(r => r.expires_at));
    }
    // An installed reader that predates discovery never answers a discover event.
    // The app calls this after its own wait; a discovery still not accepted by then ends.
    function expireStalled(maxMs) {
      if (!session || session.status !== 'starting' || !Number.isFinite(maxMs) || maxMs < 0) return false;
      let t;
      try { t = now(); } catch (_) { return false; }
      if (t - session.startedAt < maxMs) return false;
      const id = session.id;
      finish('aborted', 'no_response');
      try { o.emit(PREFIX + 'cancel-discovery', { discoveryId: id }); } catch (_) { /* revoked locally either way */ }
      return true;
    }
    function subscribe(fn) {
      if (typeof fn !== 'function') throw new TypeError('subscribe needs a function');
      listeners.add(fn);
      return () => listeners.delete(fn);
    }
    function destroy() {
      clear();
      for (const off of offs.splice(0)) { try { off(); } catch (_) { /* already gone */ } }
      listeners.clear();
      if (offWorkspace) { try { offWorkspace(); } catch (_) { /* already gone */ } }
    }

    const offWorkspace = o.change(() => clear());
    return Object.freeze({ start, cancel, clear, candidates, discard, nextExpiry, expireStalled, subscribe, destroy,
      state: view, results: () => view().results });
  }

  function defaultRandomId() {
    const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
    if (!c || typeof c.getRandomValues !== 'function') return null;
    const bytes = c.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }

  return Object.freeze({ create, validateResult, PREFIX, TTL_MS, PROVIDERS });
});
