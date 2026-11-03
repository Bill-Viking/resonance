"use strict";

// Service worker for Resonance Reader. It has no fetch, storage, cookies,
// credentials, eval or main-world script. Provider pages load only through
// ordinary tab navigation to destinations this worker builds from validated
// parts; they are read by the fixed isolated-world reader.
// Two tab strategies. X reads in background tabs. YouTube reads in ONE separate
// normal browser window this worker creates per account; every later source
// opens as a new active tab in that same window (the channel page's own-channel
// controls do not render in a hidden tab). In both strategies nothing is ever
// closed, navigated or selected after creation.
// The worker runs one account (one "job") at a time and knows nothing about
// batches: the root bridge owns the batch, sends one START per account and
// stops the whole batch when this worker's port goes away.
importScripts("shared.js");
const L = globalThis.ResonanceReader;
const ORIGIN = L.LOCAL;
const ROOT_URL = `${ORIGIN}/`;
const PORT_NAME = "resonance-reader:root";
const MAX_RECENT_IDS = 256;
const POLL_MS = 600;
const STEP_POLLS = 40;
const rootPorts = new Map();
const jobsByRootTab = new Map();
const ownedTabs = new Map(); // tabId -> { job, rec }; only tabs this worker created
const recentIds = new Set();
const setupRuns = new Map(); // root port -> { again }; one serialized setup check per port

class Stop extends Error {
  constructor(status, reason, diagnostic = null) {
    super(reason);
    this.status = status;
    this.reason = reason;
    this.diagnostic = diagnostic; // Only the validated channel diagnostic; never free text.
  }
}

function senderAt(port, url) {
  const sender = port.sender;
  if (!sender || sender.id !== chrome.runtime.id ||
      !sender.tab || !Number.isInteger(sender.tab.id) || sender.tab.id < 0 ||
      sender.frameId !== 0 || typeof sender.url !== "string" || sender.url.split("#", 1)[0] !== url) return false;
  if (sender.origin !== undefined && sender.origin !== ORIGIN) return false;
  if (sender.documentLifecycle !== undefined && sender.documentLifecycle !== "active") return false;
  return true;
}
function rememberId(id) {
  recentIds.add(id);
  if (recentIds.size > MAX_RECENT_IDS) recentIds.delete(recentIds.values().next().value);
}
function post(port, message) {
  try { port.postMessage(message); return true; } catch (_) { return false; }
}
const live = job => { if (job.done) throw new Stop(null, "ended"); };
function sleep(job, ms) {
  return new Promise(resolve => setTimeout(resolve, ms)).then(() => live(job));
}
function readerEntry(cmd) { return globalThis.ResonanceReaderPage.run(cmd); }

function progress(job, stage) {
  if (!job.done && job.kind !== "discover") post(job.rootPort, { type: "PROGRESS", requestId: job.requestId,
    provider: job.provider, mode: job.mode, stage });
}

function settle(job, status, reason, out, diagnostic) {
  if (job.done) return;
  job.done = true;
  clearTimeout(job.timer);
  if (jobsByRootTab.get(job.rootTabId) === job) jobsByRootTab.delete(job.rootTabId);
  // Provider tabs are never closed or navigated here: an uncertain tab stays
  // open for inspection. They simply stop being used.
  for (const rec of job.tabs) if (ownedTabs.get(rec.id) && ownedTabs.get(rec.id).job === job) ownedTabs.delete(rec.id);
  if (status === null) return; // The originating document went away, or the discovery was revoked.
  if (job.kind === "discover") {
    // A discovery has its own message type: never a post-reading RESULT.
    const found = { type: "DISCOVERY_RESULT", discoveryId: job.discoveryId, provider: job.provider, status };
    if (status === "ok") Object.assign(found, out); else found.reason = reason;
    post(job.rootPort, found);
    return;
  }
  const message = { type: "RESULT", requestId: job.requestId, provider: job.provider,
    mode: job.mode, status, tabs: job.tabs.map(rec => rec.id) };
  if (status === "ok") Object.assign(message, out);
  else message.reason = reason;
  // Only a held "ownership not confirmed" may carry the already validated diagnostic.
  if (status === "held" && reason === L.DIAG_REASON && diagnostic) message.diagnostic = diagnostic;
  post(job.rootPort, message);
}

// ---- owned tabs ---------------------------------------------------------
// X: the final, validated destination is the tab's creation address
// (background, never selected). YouTube: the first destination is the creation
// address of a separate visible window; later destinations are new active tabs
// in that same window. There is no later redirect of an uncertain tab: nothing
// is ever navigated, closed or otherwise changed after creation.
function checkUrlState(now, rec) {
  if (typeof now.pendingUrl === "string" && now.pendingUrl !== "" && !L.urlsMatch(now.pendingUrl, rec.expect, rec.loose)) {
    throw new Stop("error", "tab_navigated");
  }
  if (now.url && now.url !== "about:blank" && !L.urlsMatch(now.url, rec.expect, rec.loose) && !now.pendingUrl) {
    throw new Stop("error", "tab_navigated");
  }
}
function checkTabState(now, rec) {
  if (now.active !== false || now.highlighted !== false) throw new Stop("error", "tab_selected");
  checkUrlState(now, rec);
}
// Visible strategy: the tab must be the one current reader tab, selected, in
// exactly the owned window, at the expected address.
function checkVisibleState(job, now, rec) {
  if (job.current !== rec || !now || now.windowId !== job.readerWindowId) throw new Stop("error", "tab_moved");
  if (now.active !== true || now.highlighted !== true) throw new Stop("error", "tab_selected");
  checkUrlState(now, rec);
}
function validTabId(tab) { return !!tab && Number.isInteger(tab.id) && tab.id >= 0; }
function register(job, tab, url, loose) {
  const rec = { id: tab.id, expect: url, loose: !!loose, docId: null, loaded: false };
  job.tabs.push(rec);
  ownedTabs.set(tab.id, { job, rec });
  return rec;
}
async function openBackground(job, url, loose) {
  const tab = await chrome.tabs.create({ url, active: false });
  // A job that ended while the tab was being created never owns it: no live
  // ownership record, no injection. The tab is left open, untouched.
  live(job);
  if (!validTabId(tab)) throw new Stop("error", "tab_create_failed");
  const rec = register(job, tab, url, loose);
  // Anything that happened before registration (the user selecting or
  // navigating the new tab) is caught by this fresh state check before any read.
  checkTabState(tab, rec);
  return rec;
}
// Creates the one visible reader tab. Activation events for every window are
// kept while the create is in flight (the new tab's own may arrive before or
// after create returns). Once the actual tab is known, an activation in the
// owned window of any other tab is a stop, even if the expected tab became
// active again afterwards. A job that ended meanwhile registers and injects
// nothing; the new window or tab stays open, unlisted.
async function openVisibleTab(job, url, loose) {
  const first = job.readerWindowId === null;
  const failure = first ? "window_create_failed" : "tab_create_failed";
  job.creating = { events: [] };
  let made;
  try {
    made = first ? await chrome.windows.create({ url, type: "normal", focused: true })
      : await chrome.tabs.create({ url, active: true, windowId: job.readerWindowId });
  } catch (_) {
    job.creating = null;
    live(job);
    throw new Stop("error", failure);
  }
  const events = job.creating ? job.creating.events : [];
  job.creating = null;
  live(job);
  let tab = made;
  let windowId = job.readerWindowId;
  if (first) {
    // Only a singular returned window holding exactly one tab of its own is eligible.
    const tabs = made && Array.isArray(made.tabs) ? made.tabs : null;
    if (!made || !Number.isInteger(made.id) || made.id < 0 || made.id === job.rootWindowId ||
        !tabs || tabs.length !== 1) throw new Stop("error", failure);
    tab = tabs[0];
    windowId = made.id;
  }
  if (!validTabId(tab) || tab.windowId !== windowId || tab.id === job.rootTabId || ownedTabs.has(tab.id)) {
    throw new Stop("error", failure);
  }
  const rec = register(job, tab, url, loose);
  job.readerWindowId = windowId;
  job.current = rec;
  for (const event of events) {
    const mine = ownedTabs.get(event.tabId);
    if (event.tabId !== tab.id && ((mine && mine.job === job) || event.windowId === windowId)) {
      throw new Stop("error", "tab_selected");
    }
  }
  checkVisibleState(job, tab, rec);
  return rec;
}
async function openOwned(job, url, loose) {
  live(job);
  // A discovery job may open only its own fixed home page and the exact own
  // profile or channel derived from it; every other job keeps the read routes.
  const allowed = job.kind === "discover" ? L.validDiscoveryDestination(url, job.provider)
    : L.validDestination(url, job.mode, job.provider);
  if (!allowed) throw new Stop("error", "destination_refused");
  const rec = job.visible ? await openVisibleTab(job, url, loose) : await openBackground(job, url, loose);
  for (let i = 0; i < STEP_POLLS; i += 1) {
    const now = await chrome.tabs.get(rec.id);
    live(job);
    if (!now || now.id !== rec.id) throw new Stop("error", "tab_unverifiable");
    if (job.visible) checkVisibleState(job, now, rec); else checkTabState(now, rec);
    if (now.status === "complete" && L.urlsMatch(now.url, url, rec.loose)) { rec.loaded = true; return rec; }
    await sleep(job, POLL_MS);
  }
  throw new Stop("error", "page_load_timeout");
}

async function freshTabCheck(job, rec) {
  const now = await chrome.tabs.get(rec.id);
  live(job);
  // Never trust an unrelated returned tab object.
  if (!now || now.id !== rec.id) throw new Stop("error", "tab_unverifiable");
  if (job.visible) {
    if (job.current !== rec || now.windowId !== job.readerWindowId) throw new Stop("error", "tab_moved");
    if (now.active !== true || now.highlighted !== true) throw new Stop("error", "tab_selected");
    // A navigation that has begun but not yet changed the current address.
    if (typeof now.pendingUrl === "string" && now.pendingUrl !== "" &&
        !L.urlsMatch(now.pendingUrl, rec.expect, rec.loose)) throw new Stop("error", "tab_navigated");
  } else if (now.active !== false || now.highlighted !== false) throw new Stop("error", "tab_selected");
  if (typeof now.url !== "string") throw new Stop("error", "tab_unverifiable");
  if (!L.urlsMatch(now.url, rec.expect, rec.loose)) throw new Stop("error", "tab_navigated");
}
function topFrameResult(out, needsResultObject) {
  if (!Array.isArray(out) || out.length !== 1 || !out[0] || out[0].frameId !== 0 ||
      typeof out[0].documentId !== "string" || out[0].documentId === "") return null;
  if (needsResultObject && (out[0].result === null || typeof out[0].result !== "object" ||
      Array.isArray(out[0].result) || typeof out[0].result.state !== "string")) return null;
  return out[0];
}

// Every script call is bound to the exact document. The first call into a tab
// injects the files into the top frame and captures that document's ID (Chrome
// 106+); the reader function and every later call target only that document ID
// (never combined with frameIds). A missing, changed or non-top-frame document
// identity refuses before the reader can interact with anything.
async function callReader(job, rec, cmd) {
  live(job);
  if (ownedTabs.get(rec.id) === undefined || ownedTabs.get(rec.id).job !== job) throw new Stop("error", "tab_released");
  await freshTabCheck(job, rec);
  if (rec.docId === null) {
    const injected = await chrome.scripting.executeScript({ target: { tabId: rec.id, frameIds: [0] },
      files: ["shared.js", "reader.js"] });
    live(job);
    const first = topFrameResult(injected, false);
    if (!first) throw new Stop("error", "document_unverifiable");
    rec.docId = first.documentId;
    await freshTabCheck(job, rec); // The user may have acted between the two injections.
  }
  let out;
  try {
    out = await chrome.scripting.executeScript({ target: { tabId: rec.id, documentIds: [rec.docId] },
      func: readerEntry, args: [cmd] });
  } catch (_) {
    live(job);
    throw new Stop("error", "document_unavailable");
  }
  live(job);
  const answer = topFrameResult(out, true);
  if (!answer) throw new Stop("error", "reader_result_invalid");
  if (answer.documentId !== rec.docId) throw new Stop("error", "document_changed");
  // The reader saw trusted user input in this document after it was bound. End
  // the job at once (not after an unwinding cleanup) so no further command is
  // sent to a page the user is now using.
  if (answer.result.state === "user_input") {
    settle(job, "error", "user_input");
    throw new Stop(null, "ended");
  }
  return answer.result;
}

// Polls one fixed command until it is ready (and, if asked, identical twice
// in a row). Terminal states are returned to the caller; running out of polls
// returns {state:"timeout"}. This is waiting for a page, not a retry of a job.
async function poll(job, rec, cmd, stable) {
  let last = null;
  let same = 0;
  let pending = null;
  for (let i = 0; i < STEP_POLLS; i += 1) {
    const result = await callReader(job, rec, cmd);
    if (result.state === "pending") { last = null; same = 0; pending = result; }
    else if (result.state === "ready") {
      const key = JSON.stringify(result);
      same = key === last ? same + 1 : 1;
      last = key;
      if (same >= stable) return result;
    } else return result;
    await sleep(job, POLL_MS);
  }
  // The last pending answer is kept only so a caller may read its bounded diagnostic.
  return { state: "timeout", pending };
}
const base = (op, job, url) => ({ op, mode: job.mode, url });

function heldFor(state, fallback) {
  const map = { signed_out: "signed_out", identity_mismatch: "identity_mismatch",
    identity_unreadable: "identity_unreadable", channel_id_conflict: "channel_id_conflict",
    menu_ambiguous: "identity_unreadable", menu_unreadable: "identity_unreadable" };
  return new Stop("held", Object.hasOwn(map, state) ? map[state] : fallback);
}

// ---- shared item bookkeeping -------------------------------------------
function newLedger() {
  return { items: [], skipped: {}, examined: 0, bytes: 0, more: false, notes: new Set() };
}
function skipOne(ledger, reason) { ledger.skipped[reason] = (ledger.skipped[reason] || 0) + 1; }
function addItem(ledger, item) {
  const size = L.utf8Length(item.text);
  if (ledger.items.length >= L.LIMITS.items) { ledger.more = true; return false; }
  if (size > L.LIMITS.itemBytes) { skipOne(ledger, "oversize"); return false; }
  if (ledger.bytes + size > L.LIMITS.totalBytes) { skipOne(ledger, "total_limit"); return false; }
  ledger.bytes += size;
  ledger.items.push(item);
  return true;
}
function outcome(job, ledger, identity) {
  // No supported zero-item provider evidence exists, so nothing accepted is
  // held, never ok.
  if (ledger.items.length === 0) throw new Stop("held", "no_items_accepted");
  const skipped = Object.keys(ledger.skipped).sort().map(reason => ({ reason, count: ledger.skipped[reason] }));
  if (ledger.items.some(item => item.links_shortened)) ledger.notes.add("link_display_shortened");
  if (ledger.items.some(item => item.date_unknown)) ledger.notes.add("dates_relative_or_unknown");
  if (job.provider === "youtube") ledger.notes.add("activity_text_not_used");
  const partial = ledger.items.length < L.LIMITS.items || skipped.length > 0 || ledger.more;
  return { items: ledger.items,
    coverage: { limit: L.LIMITS.items, accepted: ledger.items.length, examined: ledger.examined,
      skipped, more_available: ledger.more, partial, notes: Array.from(ledger.notes).sort() },
    identity };
}

// ---- X --------------------------------------------------------------------
async function readX(job) {
  const site = L.SITES[job.mode].x;
  const handle = job.handle;
  const profileUrl = L.xProfileUrl(site, handle);
  progress(job, "identity");
  const profile = await openOwned(job, profileUrl, true);
  const first = await poll(job, profile, { ...base("x_profile", job, profileUrl), handle }, 2);
  if (first.state !== "ready") throw heldFor(first.state, "profile_not_ready");
  const ledger = newLedger();
  ledger.examined = Number.isInteger(first.examined) && first.examined >= 0 && first.examined <= 64 ? first.examined : 0;
  ledger.more = first.more_available === true;
  for (const row of Array.isArray(first.skipped) ? first.skipped.slice(0, 16) : []) {
    if (row && L.TOKEN.test(row.reason) && Number.isInteger(row.count) && row.count > 0) ledger.skipped[row.reason] = row.count;
  }
  const candidates = [];
  for (const raw of Array.isArray(first.items) ? first.items.slice(0, L.LIMITS.items) : []) {
    const link = raw && L.validateXStatusUrl(raw.permalink, site);
    if (!link || L.lower(link.handle) !== L.lower(handle) || raw.id !== link.id ||
        typeof raw.text !== "string" || raw.text.length === 0 ||
        L.utf8Length(raw.text) > L.LIMITS.itemBytes || typeof raw.links_shortened !== "boolean" ||
        (raw.date_iso !== null && !L.validIso(raw.date_iso)) ||
        (raw.date_label !== null && (typeof raw.date_label !== "string" || raw.date_label.length > 80))) {
      skipOne(ledger, "invalid_reader_item");
      continue;
    }
    if (candidates.some(c => c.link.id === link.id)) throw new Stop("error", "conflicting_duplicates");
    candidates.push({ link, raw });
  }
  if (candidates.length === 0) throw new Stop("held", "no_posts_accepted");
  // Source recheck in a fresh owned tab per accepted item, from the validated
  // canonical permalink; author, status ID and the whole text must agree.
  for (const { link, raw } of candidates) {
    progress(job, "recheck");
    const tab = await openOwned(job, link.url, true);
    const seen = await poll(job, tab, { ...base("x_status", job, link.url), handle, id: link.id }, 2);
    if (seen.state === "item_unreadable" || seen.state === "timeout" || seen.state === "conflicting_duplicates") {
      throw new Stop("held", "source_recheck_failed");
    }
    if (seen.state !== "ready") throw heldFor(seen.state, "source_recheck_failed");
    const check = seen.item && L.validateXStatusUrl(seen.item.permalink, site);
    if (!check || check.id !== link.id || L.lower(check.handle) !== L.lower(handle) ||
        seen.item.id !== link.id || L.lower(seen.item.author) !== L.lower(handle) ||
        seen.item.text !== raw.text) throw new Stop("held", "source_recheck_mismatch");
    addItem(ledger, { provider: "x", source_id: link.id, url: link.url, author: link.handle,
      channel_id: null, text: raw.text, date_iso: raw.date_iso, date_label: raw.date_label,
      date_unknown: raw.date_iso === null, links_shortened: raw.links_shortened });
  }
  // Final proof of the active account: a NEW owned profile tab loaded from the
  // fixed destination, never a re-read of a possibly stale earlier document.
  progress(job, "final_identity");
  const finalTab = await openOwned(job, profileUrl, true);
  const last = await poll(job, finalTab, { ...base("x_identity", job, profileUrl), handle }, 2);
  if (last.state !== "ready") throw last.state === "identity_mismatch" || last.state === "signed_out"
    ? new Stop("held", "identity_changed") : heldFor(last.state, "identity_changed");
  const named = [first.identity, last.identity].map(found => found && typeof found.handle === "string" ? L.lower(found.handle) : null);
  if (named.some(name => name !== L.lower(handle))) throw new Stop("held", "identity_changed");
  return outcome(job, ledger, { handle, channel_id: null });
}

// ---- YouTube -----------------------------------------------------------
async function verifyYouTubeIdentity(job, channelTab, channelUrl) {
  const handle = job.handle;
  const channel = await poll(job, channelTab, { ...base("yt_channel", job, channelUrl), handle }, 2);
  // Running out of time is "ownership not confirmed", never a claim about whose
  // channel it is. Its non-content diagnostic is accepted only in the exact
  // allowlisted shape; anything else is dropped and the hold stands without it.
  if (channel.state === "timeout") {
    throw new Stop("held", L.DIAG_REASON, L.cleanChannelDiag(channel.pending && channel.pending.diag));
  }
  if (channel.state !== "ready") throw heldFor(channel.state, L.DIAG_REASON);
  if (!L.validChannelId(channel.channel_id)) throw new Stop("held", L.DIAG_REASON);
  if (job.channelId !== null && channel.channel_id !== job.channelId) throw new Stop("held", "channel_id_mismatch");
  let menu;
  try {
    const opened = await callReader(job, channelTab, { ...base("yt_menu_open", job, channelUrl), handle });
    if (opened.state !== "ready" && opened.state !== "pending") throw heldFor(opened.state, "identity_unreadable");
    menu = await poll(job, channelTab, { ...base("yt_menu_read", job, channelUrl), handle }, 1);
  } finally {
    // Always try to put the menu back; only the avatar button is clicked.
    if (!job.done) {
      try {
        const closed = await poll(job, channelTab, { ...base("yt_menu_close", job, channelUrl), handle }, 1);
        if (closed.state !== "ready" || closed.closed !== true) menu = { state: "menu_close_failed" };
      } catch (_) { menu = { state: "menu_close_failed" }; }
    }
  }
  if (menu.state === "menu_close_failed") throw new Stop("held", "menu_close_failed");
  if (menu.state !== "ready") throw heldFor(menu.state, "identity_unreadable");
  const menuHandleOk = menu.handle !== null && L.validYtHandle(menu.handle) && L.lower(menu.handle) === L.lower(handle);
  const menuIdOk = menu.channel_id !== null && menu.channel_id === channel.channel_id;
  if (!menuHandleOk && !menuIdOk) throw new Stop("held", "identity_mismatch");
  if (menu.handle !== null && !menuHandleOk) throw new Stop("held", "identity_mismatch");
  if (menu.channel_id !== null && !menuIdOk) throw new Stop("held", "identity_mismatch");
  return { handle, channel_id: channel.channel_id };
}

const CHANGED_IDENTITY = new Set(["identity_mismatch", "channel_id_mismatch", "signed_out",
  L.DIAG_REASON, "channel_id_conflict"]);

async function readYouTube(job) {
  const sites = L.SITES[job.mode];
  const channelUrl = L.ytChannelUrl(sites.youtube, job.handle);
  progress(job, "identity");
  const channelTab = await openOwned(job, channelUrl, true);
  const identity = await verifyYouTubeIdentity(job, channelTab, channelUrl);
  progress(job, "listing");
  const activityUrl = L.activityUrl(sites.activity);
  const activityTab = await openOwned(job, activityUrl, false);
  const listing = await poll(job, activityTab, base("yt_activity", job, activityUrl), 2);
  if (listing.state === "timeout") throw new Stop("held", "activity_unavailable");
  if (listing.state !== "ready") throw new Stop("held", "activity_unavailable");
  const ledger = newLedger();
  ledger.examined = Number.isInteger(listing.listed) && listing.listed >= 0 && listing.listed <= 64 ? listing.listed : 0;
  ledger.more = listing.more_available === true;
  for (const row of Array.isArray(listing.skipped) ? listing.skipped.slice(0, 16) : []) {
    if (row && L.TOKEN.test(row.reason) && Number.isInteger(row.count) && row.count > 0) ledger.skipped[row.reason] = row.count;
  }
  const candidates = [];
  for (const raw of Array.isArray(listing.candidates) ? listing.candidates.slice(0, L.LIMITS.items) : []) {
    const parsed = raw && typeof raw.v === "string" && typeof raw.lc === "string" &&
      L.validateWatchUrl(L.watchUrl(sites.youtube, raw.v, raw.lc), sites.youtube);
    if (parsed && parsed.v === raw.v && parsed.lc === raw.lc && !candidates.some(c => c.lc === parsed.lc)) candidates.push(parsed);
    else skipOne(ledger, "invalid_reader_item");
  }
  if (candidates.length === 0) throw new Stop("held", "no_comments_seen");
  for (const candidate of candidates) {
    progress(job, "source");
    const read = await readComment(job, candidate, candidate.url, identity);
    if (read === "oversize") { skipOne(ledger, "oversize"); continue; }
    // Follow the comment's own canonical timestamp link in a fresh owned tab.
    progress(job, "recheck");
    const canonical = L.validateWatchUrl(read.href, sites.youtube);
    if (!canonical || canonical.v !== candidate.v || canonical.lc !== candidate.lc) throw new Stop("held", "source_recheck_mismatch");
    const again = await readComment(job, candidate, canonical.url, identity);
    if (again === "oversize" || again.text !== read.text || again.href !== read.href ||
        again.author_handle !== read.author_handle || again.author_channel_id !== read.author_channel_id) {
      throw new Stop("held", "source_recheck_mismatch");
    }
    addItem(ledger, { provider: "youtube", source_id: candidate.lc, url: canonical.url,
      author: identity.handle, channel_id: identity.channel_id, text: read.text,
      date_iso: null, date_label: read.date_label, date_unknown: true, links_shortened: false });
  }
  // Final proof of the active account: a NEW owned channel tab from the fixed
  // destination, with its own menu check. Intended, start and final identities
  // (handle and stable channel ID) must all agree.
  progress(job, "final_identity");
  const finalTab = await openOwned(job, channelUrl, true);
  let final;
  try {
    final = await verifyYouTubeIdentity(job, finalTab, channelUrl);
  } catch (error) {
    if (error instanceof Stop && error.status === "held" && CHANGED_IDENTITY.has(error.reason)) {
      throw new Stop("held", "identity_changed");
    }
    throw error;
  }
  if (L.lower(final.handle) !== L.lower(identity.handle) || L.lower(final.handle) !== L.lower(job.handle) ||
      final.channel_id !== identity.channel_id ||
      (job.channelId !== null && final.channel_id !== job.channelId)) throw new Stop("held", "identity_changed");
  return outcome(job, ledger, identity);
}

async function readComment(job, candidate, url, identity) {
  const tab = await openOwned(job, url, false);
  const result = await poll(job, tab, { ...base("yt_comment", job, url), v: candidate.v, lc: candidate.lc }, 2);
  if (result.state === "timeout" || result.state === "comment_unreadable") throw new Stop("held", "source_recheck_failed");
  if (result.state === "conflicting_duplicates") throw new Stop("held", "conflicting_duplicates");
  if (result.state !== "ready") throw heldFor(result.state, "source_recheck_failed");
  if (result.oversize === true) return "oversize";
  const c = result.comment;
  const href = c && L.validateWatchUrl(c.href, L.SITES[job.mode].youtube);
  if (!c || !href || c.v !== candidate.v || c.lc !== candidate.lc || href.v !== candidate.v ||
      href.lc !== candidate.lc || typeof c.text !== "string" || c.text.length === 0 ||
      L.utf8Length(c.text) > L.LIMITS.itemBytes ||
      (c.date_label !== null && (typeof c.date_label !== "string" || c.date_label.length > 80))) {
    throw new Stop("held", "source_recheck_failed");
  }
  // The author must be the verified active channel, bound by handle or by the
  // stable ID resolved on that channel's own profile.
  const byHandle = c.author_handle !== null && L.lower(c.author_handle) === L.lower(identity.handle);
  const byId = c.author_channel_id !== null && c.author_channel_id === identity.channel_id;
  if ((c.author_handle === null && c.author_channel_id === null) ||
      (c.author_handle !== null && !byHandle) || (c.author_channel_id !== null && !byId)) {
    throw new Stop("held", "comment_author_mismatch");
  }
  return c;
}

// ---- own-account discovery ------------------------------------------------
// One provider per job. Nothing is parsed except the signed-in identity and the
// account menu's single "View your channel" link; no posts, comments, history or
// other tabs are read. The home page only SUGGESTS a handle: it is trusted only
// after the exact own profile or channel, opened as a new owned document, proves
// the same active account on its own, and (YouTube) a further fresh document agrees.
async function discoverX(job) {
  const site = L.SITES[job.mode].x;
  const homeUrl = L.xHomeUrl(site);
  const home = await openOwned(job, homeUrl, true);
  const seen = await poll(job, home, base("x_whoami", job, homeUrl), 2);
  if (seen.state !== "ready") throw heldFor(seen.state, "identity_not_ready");
  const handle = seen.identity && seen.identity.handle;
  if (!L.validXHandle(handle)) throw new Stop("held", "identity_unreadable");
  const profileUrl = L.xProfileUrl(site, handle);
  const profile = await openOwned(job, profileUrl, true);
  const again = await poll(job, profile, { ...base("x_identity", job, profileUrl), handle }, 2);
  if (again.state !== "ready") throw again.state === "identity_mismatch" || again.state === "signed_out"
    ? new Stop("held", "identity_changed") : heldFor(again.state, "identity_changed");
  if (!again.identity || typeof again.identity.handle !== "string" || L.lower(again.identity.handle) !== L.lower(handle)) {
    throw new Stop("held", "identity_changed");
  }
  return { identity: { handle, channel_id: null, profile_url: profileUrl }, observed_at: new Date().toISOString() };
}

async function discoverYouTube(job) {
  const sites = L.SITES[job.mode];
  const homeUrl = L.ytHomeUrl(sites.youtube);
  const home = await openOwned(job, homeUrl, false);
  let menu;
  let opened = false;
  try {
    const open = await poll(job, home, base("yt_home_menu_open", job, homeUrl), 1);
    if (open.state !== "ready") throw heldFor(open.state, "identity_not_ready");
    opened = true;
    menu = await poll(job, home, base("yt_home_menu_read", job, homeUrl), 2);
  } finally {
    // The menu was opened by this job's single avatar click: always put it back.
    if (opened && !job.done) {
      try {
        const closed = await poll(job, home, base("yt_home_menu_close", job, homeUrl), 1);
        if (closed.state !== "ready" || closed.closed !== true) menu = { state: "menu_close_failed" };
      } catch (_) { menu = { state: "menu_close_failed" }; }
    }
  }
  if (menu.state === "menu_close_failed") throw new Stop("held", "menu_close_failed");
  if (menu.state !== "ready") throw heldFor(menu.state, "identity_unreadable");
  // A menu that only carries the stable ID gives no current handle to verify:
  // a channel ID is never accepted as input and no handle is invented.
  if (menu.handle === null || menu.handle === undefined) throw new Stop("held", "current_handle_unavailable");
  if (!L.validYtHandle(menu.handle) || menu.channel_id !== null) throw new Stop("held", "identity_unreadable");
  job.handle = menu.handle;
  job.channelId = null;
  const channelUrl = L.ytChannelUrl(sites.youtube, job.handle);
  const channelTab = await openOwned(job, channelUrl, true);
  const identity = await verifyYouTubeIdentity(job, channelTab, channelUrl);
  // A fresh owned channel document must agree before anything is reported.
  const finalTab = await openOwned(job, channelUrl, true);
  let final;
  try {
    final = await verifyYouTubeIdentity(job, finalTab, channelUrl);
  } catch (error) {
    if (error instanceof Stop && error.status === "held" && CHANGED_IDENTITY.has(error.reason)) {
      throw new Stop("held", "identity_changed");
    }
    throw error;
  }
  if (L.lower(final.handle) !== L.lower(identity.handle) || L.lower(final.handle) !== L.lower(menu.handle) ||
      final.channel_id !== identity.channel_id) throw new Stop("held", "identity_changed");
  return { identity: { handle: identity.handle, channel_id: identity.channel_id,
      profile_url: L.ytChannelIdUrl(sites.youtube, identity.channel_id) },
    observed_at: new Date().toISOString() };
}

// ---- jobs ---------------------------------------------------------------
async function checkPermission(job, missing = "permission_missing") {
  let granted = false;
  try { granted = await chrome.permissions.contains({ origins: L.PERMISSION_ORIGINS[job.provider].slice() }); } catch (_) {}
  live(job);
  if (!granted) throw new Stop("held", missing);
}

async function run(job) {
  try {
    progress(job, "permission");
    await checkPermission(job);
    const out = job.provider === "x" ? await readX(job) : await readYouTube(job);
    live(job);
    settle(job, "ok", null, out);
  } catch (error) {
    if (error instanceof Stop) settle(job, error.status, error.reason, undefined, error.diagnostic);
    else settle(job, "error", "reader_failed");
  }
}

async function runDiscovery(job) {
  try {
    await checkPermission(job, "permission_needed"); // No provider page is opened without it.
    const out = job.provider === "x" ? await discoverX(job) : await discoverYouTube(job);
    live(job);
    settle(job, "ok", null, out);
  } catch (error) {
    if (error instanceof Stop) settle(job, error.status, error.reason);
    else settle(job, "error", "reader_failed");
  }
}

function startDiscovery(rootPort, message) {
  const request = L.validateDiscover(message);
  if (!request) return;
  const rootTabId = rootPort.sender.tab.id;
  const reply = (status, reason) => post(rootPort, { type: "DISCOVERY_RESULT", discoveryId: request.discoveryId,
    provider: request.provider, status, reason });
  const key = `${request.discoveryId}:${request.provider}`;
  if (recentIds.has(key)) { reply("error", "replayed_request"); return; }
  if (jobsByRootTab.size > 0) { reply("error", "busy"); return; }
  rememberId(key);
  const rootWindowId = Number.isInteger(rootPort.sender.tab.windowId) ? rootPort.sender.tab.windowId : null;
  const job = { kind: "discover", discoveryId: request.discoveryId, provider: request.provider, mode: "live",
    handle: null, channelId: null, rootPort, rootTabId, rootWindowId, tabs: [], done: false, timer: null,
    visible: request.provider === "youtube", readerWindowId: null, current: null, creating: null };
  job.timer = setTimeout(() => settle(job, "error", "timeout"), L.LIMITS.jobMs);
  jobsByRootTab.set(rootTabId, job);
  void runDiscovery(job);
}

// Revokes a discovery job: its provider tabs stay open but unowned, and nothing
// more is reported for it (the bridge says "cancelled" itself).
function cancelDiscovery(rootPort, message) {
  if (!L.exactShape(message, ["type", "discoveryId"]) || message.type !== "CANCEL_DISCOVERY" ||
      !L.REQUEST_ID.test(message.discoveryId || "")) return;
  const job = jobsByRootTab.get(rootPort.sender.tab.id);
  if (!job || job.kind !== "discover" || job.rootPort !== rootPort || job.discoveryId !== message.discoveryId) return;
  settle(job, null);
}

function start(rootPort, message) {
  const request = L.validateStart(message);
  if (!request) return;
  const rootTabId = rootPort.sender.tab.id;
  const reply = (status, reason) => post(rootPort, { type: "RESULT", requestId: request.requestId,
    provider: request.provider, mode: request.mode, status, reason, tabs: [] });
  if (recentIds.has(request.requestId)) { reply("error", "replayed_request"); return; }
  // One account at a time in the whole extension, even across two app tabs.
  if (jobsByRootTab.size > 0) { reply("error", "busy"); return; }
  rememberId(request.requestId);
  const rootWindowId = Number.isInteger(rootPort.sender.tab.windowId) ? rootPort.sender.tab.windowId : null;
  const job = { ...request, rootPort, rootTabId, rootWindowId, tabs: [], done: false, timer: null,
    visible: request.provider === "youtube", readerWindowId: null, current: null, creating: null };
  job.timer = setTimeout(() => settle(job, "error", "timeout"), L.LIMITS.jobMs);
  jobsByRootTab.set(rootTabId, job);
  void run(job);
}

function cancel(rootPort, message) {
  if (!L.exactShape(message, ["type", "requestId"]) || message.type !== "CANCEL" ||
      !L.REQUEST_ID.test(message.requestId || "")) return;
  const job = jobsByRootTab.get(rootPort.sender.tab.id);
  if (!job || job.kind === "discover" || job.rootPort !== rootPort || job.requestId !== message.requestId) return;
  settle(job, "cancelled", "user_cancelled");
}

// ---- setup diagnostics ----------------------------------------------------
// Read-only feedback for the app: the loaded manifest version and whether each
// provider's fixed origins are allowed. It starts no job, opens no tab, asks
// for nothing and stores nothing. A failed or missing check is null
// (unavailable), never false and never allowed. Allowed access does not show
// that anyone is signed in.
async function readAccess(origins) {
  try {
    const answer = await chrome.permissions.contains({ origins: origins.slice() });
    return answer === true || answer === false ? answer : null;
  } catch (_) {
    return null;
  }
}
async function readSetup() {
  let version = null;
  try {
    const found = chrome.runtime.getManifest().version;
    if (L.validSetupVersion(found)) version = found;
  } catch (_) { /* unavailable */ }
  const [x, youtube] = await Promise.all([readAccess(L.PERMISSION_ORIGINS.x), readAccess(L.PERMISSION_ORIGINS.youtube)]);
  return { type: "SETUP", version, x, youtube };
}
const isCurrentRoot = port => {
  try { return rootPorts.get(port.sender.tab.id) === port; } catch (_) { return false; }
};
// Checks for one port are serialized and coalesced: a request that arrives while
// one is running only marks it stale, and the stale answer is dropped, so an older
// check can never overwrite a newer one. Nothing is sent to a replaced or closed port.
function refreshSetup(port) {
  const running = setupRuns.get(port);
  if (running) { running.again = true; return; }
  const run = { again: false };
  setupRuns.set(port, run);
  void (async () => {
    try {
      do {
        run.again = false;
        const message = await readSetup();
        if (setupRuns.get(port) !== run || !isCurrentRoot(port)) return;
        if (!run.again) post(port, message);
      } while (run.again);
    } finally {
      if (setupRuns.get(port) === run) setupRuns.delete(port);
    }
  })();
}
function refreshAllSetup() {
  for (const port of Array.from(rootPorts.values())) refreshSetup(port);
}
const touchesProviders = change => {
  const origins = new Set((change && Array.isArray(change.origins) && change.origins) || []);
  return Object.values(L.PERMISSION_ORIGINS).some(list => list.some(origin => origins.has(origin)));
};

// Opens only this extension's own options page. The bridge sends this after a
// trusted click on the app's options button; the worker never opens anything else.
function openOptions() {
  try {
    const opened = chrome.runtime.openOptionsPage();
    if (opened && typeof opened.catch === "function") opened.catch(() => {});
  } catch (_) { /* nothing to open */ }
}

function connectRoot(port) {
  if (!senderAt(port, ROOT_URL)) { port.disconnect(); return; }
  const tabId = port.sender.tab.id;
  const prior = rootPorts.get(tabId);
  if (prior && prior !== port) {
    const previous = jobsByRootTab.get(tabId);
    if (previous) settle(previous, null);
    setupRuns.delete(prior);
    try { prior.disconnect(); } catch (_) {}
  }
  rootPorts.set(tabId, port);
  port.onMessage.addListener(message => {
    if (rootPorts.get(tabId) !== port) return;
    if (message && message.type === "START") start(port, message);
    else if (message && message.type === "CANCEL") cancel(port, message);
    else if (message && message.type === "DISCOVER") startDiscovery(port, message);
    else if (message && message.type === "CANCEL_DISCOVERY") cancelDiscovery(port, message);
    else if (L.exactShape(message, ["type"]) && message.type === "PROBE") {
      // A fresh read-only connection check: same answer as a new connection.
      post(port, { type: "READY" });
      refreshSetup(port);
    } else if (L.exactShape(message, ["type"]) && message.type === "OPEN_OPTIONS") openOptions();
  });
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    setupRuns.delete(port);
    if (rootPorts.get(tabId) === port) rootPorts.delete(tabId);
    const job = jobsByRootTab.get(tabId);
    if (job && job.rootPort === port) settle(job, null);
  });
  post(port, { type: "READY" });
  refreshSetup(port);
}

chrome.runtime.onConnect.addListener(port => {
  if (port.name === PORT_NAME) connectRoot(port);
  else port.disconnect();
});

chrome.tabs.onRemoved.addListener(tabId => {
  const rootJob = jobsByRootTab.get(tabId);
  if (rootJob) settle(rootJob, null);
  const owned = ownedTabs.get(tabId);
  if (owned) settle(owned.job, "error", "tab_closed");
});

chrome.tabs.onActivated.addListener(({ tabId, windowId }) => {
  for (const job of Array.from(jobsByRootTab.values())) {
    if (!job.visible) continue;
    // While a visible create is in flight, every activation is kept and judged
    // once the actual tab is known.
    if (job.creating) { job.creating.events.push({ tabId, windowId }); continue; }
    if (job.readerWindowId === null) continue;
    // A late activation of the correct current tab in the owned window is fine.
    if (job.current && tabId === job.current.id && windowId === job.readerWindowId) continue;
    const mine = ownedTabs.get(tabId);
    if (windowId === job.readerWindowId || (mine && mine.job === job)) settle(job, "error", "tab_selected");
  }
  const owned = ownedTabs.get(tabId);
  if (owned && !owned.job.visible) settle(owned.job, "error", "tab_selected"); // Selected: the user's now. Left open.
});

// A reader tab moved to or from another window is no longer the owned visible tab.
function tabMoved(tabId) {
  const owned = ownedTabs.get(tabId);
  if (owned && owned.job.visible) settle(owned.job, "error", "tab_moved");
}
chrome.tabs.onDetached.addListener(tabMoved);
chrome.tabs.onAttached.addListener(tabMoved);

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  const owned = ownedTabs.get(tabId);
  if (owned) {
    const { job, rec } = owned;
    if (changeInfo.url && rec.expect && changeInfo.url !== "about:blank" &&
        !L.urlsMatch(changeInfo.url, rec.expect, rec.loose)) settle(job, "error", "tab_navigated");
    else if (changeInfo.status === "loading" && rec.loaded) settle(job, "error", "tab_navigated");
  }
  // View fragments belong to the same app document. A query, other path or new
  // document ends the job; switching views within the app does not.
  const rootJob = jobsByRootTab.get(tabId);
  if (rootJob) {
    if (changeInfo.url !== undefined && (typeof changeInfo.url !== "string" || changeInfo.url.split("#", 1)[0] !== ROOT_URL)) settle(rootJob, "error", "root_navigated");
    else if (changeInfo.status === "loading") settle(rootJob, null);
  }
});

chrome.permissions.onRemoved.addListener(removed => {
  const origins = new Set((removed && removed.origins) || []);
  for (const job of Array.from(jobsByRootTab.values())) {
    if (L.PERMISSION_ORIGINS[job.provider].some(origin => origins.has(origin))) {
      settle(job, "error", "permission_lost");
    }
  }
});

// Setup feedback follows access changes. This is separate from the cancellation
// above and only refreshes what the page says.
chrome.permissions.onRemoved.addListener(change => { if (touchesProviders(change)) refreshAllSetup(); });
if (chrome.permissions.onAdded) {
  chrome.permissions.onAdded.addListener(change => { if (touchesProviders(change)) refreshAllSetup(); });
}
