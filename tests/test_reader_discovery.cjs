"use strict";

// Deterministic checks for own-account discovery in the production Resonance
// Reader (browser-reader/) and its page-side controller (static/account-discovery.js).
// The real worker, bridge, shared parsers and page reader run as shipped in Node
// VM contexts, wired together through linked fake ports; only the browser (tabs,
// windows, permissions, scripting, ports) and the DOM are simulated, using the
// self-contained synthetic fake-browser harness (tests only). The pages are
// invented semantic fixtures built here from the controls the current parsers
// read. This does NOT replace a native Chrome/Edge run, a store install, or any
// real provider read; no live provider, account or network is touched.

const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const ROOT = path.resolve(__dirname, "..");
const PKG = path.join(ROOT, "browser-reader");
const { FakeBrowser, signal } = require("./reader-fixtures/fake_browser.cjs");

const ORIGIN = "http://127.0.0.1:4317";
const ROOT_URL = `${ORIGIN}/`;
const PREFIX = "resonance:reader:";
const PAGE_EVENTS = new Set(["arm", "start", "cancel", "probe", "options", "discover", "cancel-discovery"]);
const read = name => fs.readFileSync(path.join(PKG, name), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
let counter = 0;
const newId = () => (++counter).toString(16).padStart(32, "0");
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate, label = "condition") {
  for (let i = 0; i < 6000; i += 1) {
    if (predicate()) return;
    await tick();
  }
  throw new Error(`timed out waiting for ${label}`);
}
async function settleAll(n = 60) { for (let i = 0; i < n; i += 1) await tick(); }

const sharedCtx = vm.createContext({ URL, console });
vm.runInContext(read("shared.js"), sharedCtx);
const S = sharedCtx.ResonanceReader;

// ---- the rig: production worker + production bridge, linked --------------
const GRANTS = ["https://x.com", "https://www.youtube.com", "https://myactivity.google.com"];

function makeWorker(options = {}) {
  const fb = new FakeBrowser({ manifestVersion: "0.1.0", grants: GRANTS, ...options });
  if (options.prepare) options.prepare(fb);
  // The harness injects the development files; this injects the production ones.
  const original = fb.chrome.scripting.executeScript;
  fb.chrome.scripting.executeScript = async function (injection) {
    if (!injection.files) return original.call(this, injection);
    fb.flushLate();
    const target = injection.target || {};
    const tab = fb.tabs.get(target.tabId);
    if (target.frameIds === undefined || target.frameIds.join() !== "0") throw new Error("frames");
    if (!tab || fb.userTabs.has(tab.id)) throw new Error("worker touched a user tab");
    if (!fb.grantedFor(tab.url)) throw new Error("Cannot access contents of the page");
    if (!tab.ctx) throw new Error("No document");
    assert.deepEqual(plain(injection.files), ["shared.js", "reader.js"]);
    fb.injections.push({ tabId: tab.id, kind: "files", target, windowId: tab.windowId });
    for (const file of injection.files) vm.runInContext(read(file), tab.ctx);
    return [{ frameId: 0, documentId: tab.docId, result: null }];
  };
  const timers = [];
  // Everything in this rig is microtask-driven, so a whole provider normally
  // finishes inside one test tick. Freezing the gate parks a job at its first
  // wait (after its first read) so a test can act in the middle of a job.
  const gate = { frozen: false };
  const ctx = vm.createContext({ chrome: fb.chrome, URL, console, crypto: webcrypto,
    setTimeout(cb, ms) {
      if (ms === 120000) { timers.push(cb); return timers.length; }
      if (!gate.frozen) Promise.resolve().then(cb);
      return 0;
    },
    clearTimeout() {}, importScripts(name) { vm.runInContext(read(name), ctx); } });
  vm.runInContext(read("worker.js"), ctx, { filename: "worker.js" });
  const workerSides = [];
  const w = { fb, ctx, timers, gate, workerSides, rootTab: fb.addUserTab(ROOT_URL), connects: 0,
    // Simulates the service worker being killed or restarted: every port drops.
    kill() { for (const side of workerSides.slice()) side.disconnect(); } };
  return w;
}

const workerSender = (tabId, over = {}) => ({ id: "test-extension", tab: { id: tabId, windowId: 1 }, frameId: 0,
  url: ROOT_URL, origin: ORIGIN, documentLifecycle: "active", ...over });

// Linked pair of ports. Delivery is asynchronous, as in a browser.
function link(w, page, name, senderOver) {
  const log = page.log;
  const bridgeSide = { name, posted: [], closed: false, onMessage: signal(), onDisconnect: signal() };
  const workerSide = { name, sender: workerSender(w.rootTab, senderOver), posted: [], closed: false,
    onMessage: signal(), onDisconnect: signal() };
  bridgeSide.postMessage = message => {
    if (bridgeSide.closed) throw new Error("Attempting to use a disconnected port object");
    if (page.failPost) throw page.failPost;
    const copy = plain(message);
    bridgeSide.posted.push(copy);
    log.push(`${copy.type.toLowerCase()}${copy.requestId ? ":" + copy.requestId : ""}`);
    Promise.resolve().then(() => { if (!workerSide.closed) workerSide.onMessage.emit(copy); });
  };
  workerSide.postMessage = message => {
    if (workerSide.closed) throw new Error("Attempting to use a disconnected port object");
    let copy = plain(message);
    if (page.tamper) copy = page.tamper(copy);
    if (copy === null) return;
    workerSide.posted.push(copy);
    Promise.resolve().then(() => { if (!bridgeSide.closed) bridgeSide.onMessage.emit(copy); });
  };
  const close = (self, other) => () => {
    if (self.closed) return;
    self.closed = true;
    other.closed = true;
    Promise.resolve().then(() => other.onDisconnect.emit());
  };
  bridgeSide.disconnect = close(bridgeSide, workerSide);
  workerSide.disconnect = close(workerSide, bridgeSide);
  w.workerSides.push(workerSide);
  return { bridgeSide, workerSide };
}

// The production bridge in a fake page. Nothing runs by itself: timers are
// recorded with their delay and only run when a test fires them.
function makePage(w, { url = ROOT_URL, top = true, origin = new URL(url).origin } = {}) {
  const listeners = new Map();
  const events = [];
  const log = [];
  class Element {
    constructor(id, tag = "button") { this.id = id; this.tag = tag; }
    closest(selector) { return selector === "button" && this.tag === "button" ? this : null; }
  }
  class CustomEvent { constructor(type, options) { this.type = type; this.detail = options.detail; this.target = null; } }
  const document = {
    addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); },
    dispatchEvent(event) {
      if (!event.target) event.target = document;
      if (event.type.startsWith(PREFIX) && !PAGE_EVENTS.has(event.type.slice(PREFIX.length))) {
        events.push({ name: event.type.slice(PREFIX.length), detail: plain(event.detail) });
      }
      for (const l of listeners.get(event.type) || []) l(event);
    },
  };
  const timers = [];
  const location = { origin, href: url };
  const window = {};
  window.top = top ? window : {};
  const page = { w, events, log, timers, location, document, tamper: null, failPost: null, failConnect: null, ports: [] };
  const chrome = { runtime: { id: "test-extension", connect({ name }) {
    if (page.failConnect) throw page.failConnect;
    w.connects += 1;
    const pair = link(w, page, name);
    page.ports.push(pair);
    w.fb.signals.onConnect.emit(pair.workerSide);
    return pair.bridgeSide;
  } } };
  const ctx = vm.createContext({ chrome, window, location, document, Element, CustomEvent, URL, console,
    setTimeout(fn, ms) { timers.push({ fn, ms, cleared: false, ran: false }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cleared = true; } });
  vm.runInContext(read("shared.js"), ctx);
  vm.runInContext(read("bridge.js"), ctx, { filename: "bridge.js" });
  Object.assign(page, {
    pending: ms => timers.filter(t => t.ms === ms && !t.cleared && !t.ran),
    fire(ms) { const [t] = this.pending(ms); assert.ok(t, `a ${ms} ms timer is pending`); t.ran = true; t.fn(); },
    expireGestures() { for (const t of this.pending(0)) { t.ran = true; t.fn(); } },
    click(id, trusted = true, tag = "button") { document.dispatchEvent({ type: "click", isTrusted: trusted, target: new Element(id, tag) }); },
    send(name, detail) { document.dispatchEvent(new CustomEvent(PREFIX + name, { detail })); },
    of(name) { return events.filter(e => e.name === name).map(e => e.detail); },
    names() { return events.map(e => e.name); },
    last(name) { const list = this.of(name); return list[list.length - 1]; },
    clear() { events.length = 0; log.length = 0; },
    workerPost(message) { const live = page.ports.filter(p => !p.workerSide.closed); live[live.length - 1].workerSide.postMessage(message); },
    port() { return page.ports[page.ports.length - 1]; },
  });
  return page;
}

async function boot(options = {}, pageOptions) {
  const w = makeWorker(options);
  const page = makePage(w, pageOptions);
  await settleAll(10);
  return { w, page };
}
const req = (provider, handle, channelId = "") => ({ requestId: newId(), provider, handle, channelId });
function arm(page, requests, { click = "res-check-now", batchId = newId() } = {}) {
  page.click(click);
  page.send("arm", { batchId, requests });
  return batchId;
}

// ---- invented semantic pages for discovery --------------------------------
// Each page carries only the controls the reader's current parsers look for (the
// X profile link and account switcher, the YouTube avatar menu and own-channel
// controls), plus a recognisable "timeline" marker that must never leave the page.
const UC_A = "UCdiscoverChannel000001A";
const UC_B = "UCdiscoverChannel000002B";
const SECRET = "SECRET-TIMELINE-TEXT-7731";
const doc = body => `<!doctype html><html lang="en"><meta charset="utf-8"><body>${body}</body></html>`;
const TWEET = `<main><article data-testid="tweet"><div data-testid="tweetText">${SECRET}</div></article></main>`;
const xNav = (handle, switcher = handle) => `<nav><a data-testid="AppTabBar_Profile_Link" href="/${handle}" aria-label="Profile">Profile</a><button data-testid="SideNav_AccountSwitcher_Button" type="button"><span>Sample Person</span><span>@${switcher}</span></button></nav>`;
const xPage = (handle, switcher) => doc(xNav(handle, switcher) + TWEET);
const X_SIGNED_OUT = doc(`<nav><a href="/login">Log in</a><button data-testid="loginButton" type="button">Log in</button></nav>${TWEET}`);
const X_LOADING = doc(`<nav></nav>${TWEET}`);
const X_TWO = doc(`<nav><a data-testid="AppTabBar_Profile_Link" href="/fx_disc_a">Profile</a><a data-testid="AppTabBar_Profile_Link" href="/fx_disc_b">Profile</a><button data-testid="SideNav_AccountSwitcher_Button" type="button"><span>@fx_disc_a</span></button></nav>${TWEET}`);
const X_FOREIGN_LINK = doc(`<nav><a data-testid="AppTabBar_Profile_Link" href="https://evil.example/fx_disc_a">Profile</a><button data-testid="SideNav_AccountSwitcher_Button" type="button"><span>@fx_disc_a</span></button></nav>${TWEET}`);
const FX_SCRIPT = '<script src="/fx.js"></script>';
const menuLink = href => `<a href="${href}">View your channel</a>`;
const ytHome = (links, { toggles = true, hidden = true } = {}) => doc(`<ytd-masthead><button id="avatar-btn" type="button">Account menu</button></ytd-masthead><ytd-popup-container><div id="account-menu"${hidden ? " hidden" : ""}>${links}<a href="/switch_account">Switch account</a></div></ytd-popup-container>${toggles ? FX_SCRIPT : ""}`);
const YT_HOME_SIGNED_OUT = doc(`<ytd-masthead><a href="https://accounts.google.com/ServiceLogin">Sign in</a></ytd-masthead>${FX_SCRIPT}`);
const YT_HOME_LOADING = doc(`<ytd-masthead></ytd-masthead>${FX_SCRIPT}`);
const ytChannel = ({ handle = "fx_disc_yt", id = UC_A, menu, studio = true, extraId = null }) => doc(`<ytd-masthead><button id="avatar-btn" type="button">Account menu</button></ytd-masthead><ytd-popup-container><div id="account-menu" hidden>${menu}</div></ytd-popup-container><ytd-page-header><h1>Sample Channel</h1><span>@${handle}</span>${studio ? `<a href="https://studio.youtube.com/channel/${id}/editing">Customize channel</a><a href="https://studio.youtube.com/channel/${extraId || id}/content">Manage videos</a>` : ""}</ytd-page-header>${FX_SCRIPT}`);
const YT_OK_HOME = ytHome(menuLink("/@fx_disc_yt"));
const YT_OK_CHANNEL = ytChannel({ menu: menuLink("/@fx_disc_yt") });

function setX(fb, handle, home, profiles) {
  fb.override("/fixture/x/home", [{ html: home }]);
  if (handle) fb.override(`/fixture/x/${handle}`, profiles.map(html => ({ html })));
}
function setYT(fb, home, channels, handle = "fx_disc_yt") {
  fb.override("/fixture/youtube/", [{ html: home }]);
  if (channels) fb.override(`/fixture/youtube/@${handle}`, channels.map(html => ({ html })));
}
function setBoth(fb) {
  setX(fb, "fx_disc_a", xPage("fx_disc_a"), [xPage("fx_disc_a")]);
  setYT(fb, YT_OK_HOME, [YT_OK_CHANNEL, YT_OK_CHANNEL]);
}

function discover(page, providers, { click = "res-find-accounts", trusted = true, discoveryId = newId() } = {}) {
  page.click(click, trusted);
  page.send("discover", { discoveryId, providers });
  return discoveryId;
}
const discoveryEnded = (page, id) => () => ["discovery-done", "discovery-cancelled", "discovery-aborted", "discovery-rejected"]
  .some(name => page.of(name).some(d => d.discoveryId === id));
async function discoverAndWait(page, providers, options) {
  const id = discover(page, providers, options);
  await until(discoveryEnded(page, id), "discovery end");
  await settleAll(5);
  return id;
}
const found = (page, id) => page.of("discovery-result").filter(r => r.discoveryId === id);
const dnames = page => page.names().filter(name => name.startsWith("discovery"));
const discRejected = page => page.of("discovery-rejected").map(r => r.reason);
const ops = w => w.fb.calls.map(c => c.cmd.op);
const strip = result => { const { observed_at, ...rest } = result; return rest; };

// ---- package surface: validators, destinations, source audit ------------------
test("discovery adds fixed destinations only for discovery; ordinary read routes are unchanged", () => {
  const x = S.SITES.live.x;
  const yt = S.SITES.live.youtube;
  assert.equal(S.xHomeUrl(x), "https://x.com/home");
  assert.equal(S.ytHomeUrl(yt), "https://www.youtube.com/");
  for (const [url, provider] of [["https://x.com/home", "x"], ["https://x.com/fx_disc_a", "x"],
    ["https://www.youtube.com/", "youtube"], ["https://www.youtube.com/@fx_disc_yt", "youtube"]]) {
    assert.equal(S.validDiscoveryDestination(url, provider), true, url);
  }
  for (const [url, provider] of [["https://x.com/home?x=1", "x"], ["https://x.com/home/", "x"], ["https://x.com/explore", "x"],
    ["https://x.com/fx_disc_a/status/1", "x"], ["https://x.com/", "x"], ["https://X.com/home", "x"], ["http://x.com/home", "x"],
    ["https://x.com/fx_disc_a?lang=en", "x"], ["https://x.com/fx_disc_a#a", "x"],
    ["https://www.youtube.com/watch?v=aaaaaaaaaaa&lc=Ugx0000000", "youtube"], [`https://www.youtube.com/channel/${UC_A}`, "youtube"],
    ["https://www.youtube.com/@ab", "youtube"], ["https://www.youtube.com/@fx_disc_yt/videos", "youtube"],
    ["https://www.youtube.com/@fx_disc_yt?x=1", "youtube"], ["https://www.youtube.com/?x=1", "youtube"],
    ["https://myactivity.google.com/page?page=youtube_comments", "youtube"], ["https://studio.youtube.com/", "youtube"],
    ["https://www.facebook.com/fx.user1", "facebook"], [`${ORIGIN}/`, "x"], ["https://x.com/home", "youtube"],
    ["https://www.youtube.com/", "x"], ["https://www.youtube.com/", "facebook"], [null, "x"]]) {
    assert.equal(S.validDiscoveryDestination(url, provider), false, `${url} ${provider}`);
  }
  // A read never gets the home pages.
  assert.equal(S.validDestination("https://x.com/home", "live", "x"), false);
  assert.equal(S.validDestination("https://www.youtube.com/", "live", "youtube"), false);
  assert.equal(S.validDestination("https://x.com/fx_disc_a", "live", "x"), true);
  assert.equal(S.validDestination("https://www.youtube.com/@fx_disc_yt", "live", "youtube"), true);
});

test("DISCOVER and its result have their own validators; neither accepts the other's messages", () => {
  const id = newId();
  assert.deepEqual(plain(S.validateDiscover({ type: "DISCOVER", discoveryId: id, provider: "x" })), { discoveryId: id, provider: "x" });
  for (const bad of [{ type: "DISCOVER", discoveryId: id, provider: "facebook" }, { type: "DISCOVER", discoveryId: id, provider: "x", mode: "live" },
    { type: "DISCOVER", discoveryId: id, provider: "x", handle: "fx_disc_a" }, { type: "START", discoveryId: id, provider: "x" },
    { type: "DISCOVER", discoveryId: "A".repeat(32), provider: "x" }, { type: "DISCOVER", discoveryId: id.slice(1), provider: "x" },
    { type: "DISCOVER", provider: "x" }, null, [], "DISCOVER"]) assert.equal(S.validateDiscover(bad), null);
  const start = { type: "START", requestId: id, provider: "x", mode: "live", handle: "fx_disc_a", channelId: "" };
  assert.equal(S.validateDiscover(start), null);
  assert.equal(S.validateStart({ type: "DISCOVER", discoveryId: id, provider: "x" }), null);
  const ok = { type: "DISCOVERY_RESULT", discoveryId: id, provider: "x", status: "ok",
    identity: { handle: "fx_disc_a", channel_id: null, profile_url: "https://x.com/fx_disc_a" }, observed_at: "2026-11-01T10:00:00.000Z" };
  assert.equal(S.validateDiscoveryResult(ok, { discoveryId: id, provider: "x" }), true);
  // Not a post-reading success: validateResult refuses it, and the reverse.
  assert.equal(S.validateResult(ok, { requestId: id, provider: "x", mode: "live", handle: "fx_disc_a", channelId: null }), false);
  const read = { type: "RESULT", requestId: id, provider: "x", mode: "live", status: "held", tabs: [], reason: "signed_out" };
  assert.equal(S.validateDiscoveryResult(read, { discoveryId: id, provider: "x" }), false);
  const yt = { type: "DISCOVERY_RESULT", discoveryId: id, provider: "youtube", status: "ok",
    identity: { handle: "fx_disc_yt", channel_id: UC_A, profile_url: `https://www.youtube.com/channel/${UC_A}` }, observed_at: "2026-11-01T10:00:00Z" };
  assert.equal(S.validateDiscoveryResult(yt, { discoveryId: id, provider: "youtube" }), true);
  const held = { type: "DISCOVERY_RESULT", discoveryId: id, provider: "x", status: "held", reason: "signed_out" };
  assert.equal(S.validateDiscoveryResult(held, { discoveryId: id, provider: "x" }), true);
  const bads = [
    { ...ok, extra: 1 }, { ...ok, tabs: [] }, { ...ok, status: "cancelled" }, { ...ok, provider: "youtube" },
    { ...ok, discoveryId: newId() }, { ...ok, observed_at: "yesterday" }, { ...ok, observed_at: "2026-13-45T10:00:00Z" },
    { ...ok, identity: { ...ok.identity, profile_url: "https://x.com/other" } },
    { ...ok, identity: { ...ok.identity, profile_url: "https://x.com/fx_disc_a/" } },
    { ...ok, identity: { ...ok.identity, channel_id: UC_A } }, { ...ok, identity: { ...ok.identity, handle: "home", profile_url: "https://x.com/home" } },
    { ...ok, identity: { ...ok.identity, extra: 1 } }, { ...ok, identity: { handle: "fx_disc_a", channel_id: null } },
    { ...held, reason: "Signed Out" }, { ...held, reason: "" }, { ...held, identity: ok.identity }, { ...held, diagnostic: {} },
    { ...held, status: "ok" }, { ...held, status: "partial" }, { ...held, tabs: [] }];
  for (const bad of bads) assert.equal(S.validateDiscoveryResult(bad, { discoveryId: id, provider: "x" }), false, JSON.stringify(bad));
  const ytBads = [{ ...yt, identity: { ...yt.identity, channel_id: null } },
    { ...yt, identity: { ...yt.identity, profile_url: "https://www.youtube.com/@fx_disc_yt" } },
    { ...yt, identity: { ...yt.identity, profile_url: `https://www.youtube.com/channel/${UC_B}` } },
    { ...yt, identity: { ...yt.identity, handle: "ab" } }, { ...yt, identity: { ...yt.identity, channel_id: "UCshort" } }];
  for (const bad of ytBads) assert.equal(S.validateDiscoveryResult(bad, { discoveryId: id, provider: "youtube" }), false, JSON.stringify(bad));
  assert.equal(S.validateDiscoverInput({ discoveryId: id, providers: ["x", "youtube"] }).providers.length, 2);
  for (const bad of [{ discoveryId: id, providers: [] }, { discoveryId: id, providers: ["x", "x"] }, { discoveryId: id, providers: ["x", "youtube", "x"] },
    { discoveryId: id, providers: ["facebook"] }, { discoveryId: id, providers: "x" }, { discoveryId: id, providers: ["x"], handle: "a" },
    { providers: ["x"] }, { discoveryId: id }, { discoveryId: 5, providers: ["x"] }, { discoveryId: id, providers: [{ provider: "x" }] }])
    assert.equal(S.validateDiscoverInput(bad), null, JSON.stringify(bad));
});

test("static audit: discovery adds no new file, storage, network or provider write, and no new tab or window call site", () => {
  const present = fs.readdirSync(PKG).filter(name => name !== ".DS_Store").sort();
  assert.deepEqual(present, ["README.md", "bridge.js", "icon-128.png", "icon-16.png", "icon-32.png", "manifest.json", "options.html", "options.js", "popup.html", "popup.js",
    "reader.js", "shared.js", "worker.js"]);
  const m = JSON.parse(read("manifest.json"));
  assert.deepEqual(m.permissions, ["scripting"]);
  assert.deepEqual(m.host_permissions, [`${ORIGIN}/*`]);
  assert.deepEqual(m.optional_host_permissions, ["https://x.com/*", "https://www.youtube.com/*", "https://myactivity.google.com/*"]);
  const worker = read("worker.js");
  const bridge = read("bridge.js");
  const reader = read("reader.js");
  assert.equal([...worker.matchAll(/tabs\.create\(/g)].length, 2);
  assert.equal([...worker.matchAll(/windows\.create\(/g)].length, 1);
  assert.equal([...reader.matchAll(/\.click\(\)/g)].length, 3);
  for (const [name, source] of [["worker.js", worker], ["bridge.js", bridge], ["reader.js", reader], ["shared.js", read("shared.js")]]) {
    for (const pattern of [/\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /chrome\.storage/, /chrome\.cookies/, /chrome\.history/,
      /chrome\.tabs\.(query|update|remove)/, /permissions\.(request|remove)/, /localStorage|sessionStorage|indexedDB/, /document\.cookie/,
      /\beval\s*\(/, /facebook/i]) assert.equal(pattern.test(source), false, `${name} ${pattern}`);
  }
  // Discovery reader operations are identity and menu reads only: no post parser is reachable from them.
  const section = reader.slice(reader.indexOf('case "x_whoami"'), reader.indexOf('case "yt_activity"'));
  assert.match(section, /readXIdentity/);
  assert.equal(/xProfile|xStatus|parseXArticle|ytComment|ytActivity/.test(section), false);
  const cmds = reader.slice(reader.indexOf("const COMMANDS"), reader.indexOf("};", reader.indexOf("const COMMANDS")));
  for (const op of ["x_whoami", "yt_home_menu_open", "yt_home_menu_read", "yt_home_menu_close"]) {
    assert.match(cmds, new RegExp(`${op}: \\["op", "mode", "url"\\]`));
  }
  // The discovery button is the only new authority and it is a different gesture from a check.
  assert.match(bridge, /"res-find-accounts": "discover"/);
  assert.equal(/"res-find-accounts": "check"/.test(bridge), false);
});

// ---- X --------------------------------------------------------------------
test("X: the home page names a handle and a new owned profile document confirms it", async () => {
  const { w, page } = await boot();
  setX(w.fb, "fx_disc_a", xPage("fx_disc_a"), [xPage("fx_disc_a")]);
  page.clear();
  const id = await discoverAndWait(page, ["x"]);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-result", "discovery-done"]);
  assert.deepEqual(plain(page.of("discovery-accepted")), [{ discoveryId: id }]);
  const [result] = found(page, id);
  assert.deepEqual(strip(result), { discoveryId: id, provider: "x", status: "ok",
    identity: { handle: "fx_disc_a", channel_id: null, profile_url: "https://x.com/fx_disc_a" } });
  assert.deepEqual(Object.keys(result).sort(), ["discoveryId", "identity", "observed_at", "provider", "status"]);
  assert.equal(S.validIso(result.observed_at), true);
  // A background home tab, then a new background profile tab; no window; nothing closed or navigated.
  assert.deepEqual(w.fb.tabCreates.map(t => [t.url, t.active]), [["https://x.com/home", false], ["https://x.com/fx_disc_a", false]]);
  assert.equal(w.fb.windowCreates.length, 0);
  assert.deepEqual([w.fb.removed.length, w.fb.updates.length, w.fb.windowCalls.length], [0, 0, 0]);
  assert.deepEqual([...new Set(ops(w))], ["x_whoami", "x_identity"]);
  for (const call of w.fb.calls) {
    if (call.cmd.op === "x_whoami") assert.deepEqual(Object.keys(call.cmd).sort(), ["mode", "op", "url"], "the page supplies no expected handle");
    else assert.equal(call.cmd.handle, "fx_disc_a");
  }
  // No timeline text ever left the page.
  assert.equal(JSON.stringify([page.events, w.fb.calls]).includes(SECRET), false);
  assert.equal(page.pending(130000).length, 0);
});

test("X: a profile that shows a different active handle holds; nothing is switched or guessed", async () => {
  const { w, page } = await boot();
  setX(w.fb, "fx_disc_a", xPage("fx_disc_a"), [xPage("fx_disc_b")]);
  page.clear();
  const id = await discoverAndWait(page, ["x"]);
  const [result] = found(page, id);
  assert.deepEqual(plain(result), { discoveryId: id, provider: "x", status: "held", reason: "identity_changed" });
  assert.equal(page.of("discovery-done").length, 1);
  assert.equal(w.fb.tabCreates.length, 2);
  // The signed-in switcher naming someone else on the home page is unreadable, not a guess.
  const second = await boot();
  setX(second.w.fb, null, xPage("fx_disc_a", "fx_disc_b"));
  second.page.clear();
  const id2 = await discoverAndWait(second.page, ["x"]);
  assert.deepEqual(found(second.page, id2).map(r => [r.status, r.reason]), [["held", "identity_unreadable"]]);
  assert.equal(second.w.fb.tabCreates.length, 1, "no profile was opened for an unreadable identity");
});

test("X: signed out, ambiguous, reserved, foreign or still loading holds before any profile is opened", async () => {
  const cases = [[X_SIGNED_OUT, "signed_out"], [X_TWO, "identity_unreadable"], [xPage("settings"), "identity_unreadable"],
    [X_FOREIGN_LINK, "identity_unreadable"], [X_LOADING, "identity_not_ready"]];
  for (const [home, reason] of cases) {
    const { w, page } = await boot();
    setX(w.fb, null, home);
    page.clear();
    const id = await discoverAndWait(page, ["x"]);
    assert.deepEqual(plain(found(page, id)), [{ discoveryId: id, provider: "x", status: "held", reason }], reason);
    assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-result", "discovery-done"]);
    assert.deepEqual(w.fb.tabCreates.map(t => t.url), ["https://x.com/home"], `${reason}: only the fixed home page opened`);
    assert.equal(ops(w).every(op => op === "x_whoami"), true);
  }
});

// ---- YouTube ----------------------------------------------------------------
test("YouTube: the menu names the current handle; a channel document proves the stable ID; a fresh one agrees", async () => {
  const { w, page } = await boot();
  setYT(w.fb, YT_OK_HOME, [YT_OK_CHANNEL, YT_OK_CHANNEL]);
  page.clear();
  const id = await discoverAndWait(page, ["youtube"]);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-result", "discovery-done"]);
  const [result] = found(page, id);
  assert.deepEqual(strip(result), { discoveryId: id, provider: "youtube", status: "ok",
    identity: { handle: "fx_disc_yt", channel_id: UC_A, profile_url: `https://www.youtube.com/channel/${UC_A}` } });
  assert.equal(S.validIso(result.observed_at), true);
  // One separate visible window at the fixed home page; two fresh channel tabs in it.
  assert.deepEqual(plain(w.fb.windowCreates), [{ url: "https://www.youtube.com/", type: "normal", focused: true }]);
  const windowId = Math.max(...w.fb.windows.keys());
  assert.deepEqual(w.fb.tabCreates.map(t => [t.url, t.active, t.windowId]),
    [["https://www.youtube.com/@fx_disc_yt", true, windowId], ["https://www.youtube.com/@fx_disc_yt", true, windowId]]);
  assert.deepEqual([w.fb.removed.length, w.fb.updates.length, w.fb.windowCalls.length], [0, 0, 0]);
  const used = new Set(ops(w));
  assert.deepEqual([...used].sort(), ["yt_channel", "yt_home_menu_close", "yt_home_menu_open", "yt_home_menu_read",
    "yt_menu_close", "yt_menu_open", "yt_menu_read"]);
  // Every account menu that was opened is closed again.
  for (const tab of w.fb.tabs.values()) {
    if (!tab.document) continue;
    const menu = tab.document.querySelector("#account-menu");
    if (menu) assert.equal(menu.hasAttribute("hidden"), true, `menu closed in tab ${tab.id}`);
  }
  assert.equal(page.pending(130000).length, 0);
});

test("YouTube: the channel page's own menu may use the stable ID; success still needs the handle from the home menu", async () => {
  const { w, page } = await boot();
  const byId = ytChannel({ menu: menuLink(`/channel/${UC_A}`) });
  setYT(w.fb, YT_OK_HOME, [byId, byId]);
  page.clear();
  const id = await discoverAndWait(page, ["youtube"]);
  assert.deepEqual(found(page, id).map(r => [r.status, r.identity && r.identity.channel_id]), [["ok", UC_A]]);
});

test("YouTube: a menu with only the stable ID holds as current_handle_unavailable and opens no channel", async () => {
  const { w, page } = await boot();
  setYT(w.fb, ytHome(menuLink(`/channel/${UC_A}`)), null);
  page.clear();
  const id = await discoverAndWait(page, ["youtube"]);
  assert.deepEqual(plain(found(page, id)), [{ discoveryId: id, provider: "youtube", status: "held", reason: "current_handle_unavailable" }]);
  assert.equal(w.fb.tabCreates.length, 0);
  assert.equal(w.fb.windowCreates.length, 1);
  assert.equal(w.fb.tabs.get(Math.max(...w.fb.created)).document.querySelector("#account-menu").hasAttribute("hidden"), true, "menu closed");
});

test("YouTube: signed out, loading, ambiguous or unreadable menus hold; the menu is put back", async () => {
  const cases = [
    [YT_HOME_SIGNED_OUT, "signed_out", "unopened"],
    [YT_HOME_LOADING, "identity_not_ready", "unopened"],
    [ytHome(menuLink("/@fx_disc_yt") + menuLink("/@fx_other_yt")), "identity_unreadable", "closed"],
    [ytHome(menuLink("https://evil.example/@fx_disc_yt")), "identity_unreadable", "closed"],
    [ytHome(menuLink("/@fx_disc_yt/videos")), "identity_unreadable", "closed"],
    [ytHome(menuLink("/@ab")), "identity_unreadable", "closed"],
    [ytHome(""), "identity_unreadable", "skip"], // the menu never offered the link, so there was nothing to close
  ];
  for (const [home, reason, menuState] of cases) {
    const { w, page } = await boot();
    setYT(w.fb, home, null);
    page.clear();
    const id = await discoverAndWait(page, ["youtube"]);
    assert.deepEqual(plain(found(page, id)), [{ discoveryId: id, provider: "youtube", status: "held", reason }], reason);
    assert.equal(w.fb.tabCreates.length, 0, `${reason}: no channel opened`);
    assert.equal(w.fb.windowCreates.length, 1);
    const tab = w.fb.tabs.get(Math.max(...w.fb.created));
    if (menuState === "closed") assert.equal(tab.document.querySelector("#account-menu").hasAttribute("hidden"), true, `${reason}: menu closed`);
    else if (menuState === "unopened") assert.equal(ops(w).includes("yt_home_menu_close"), false, `${reason}: nothing was opened, so nothing to close`);
  }
});

test("YouTube: a menu that cannot be closed holds as menu_close_failed instead of reporting success", async () => {
  const { w, page } = await boot();
  setYT(w.fb, ytHome(menuLink("/@fx_disc_yt"), { toggles: false, hidden: false }), [YT_OK_CHANNEL, YT_OK_CHANNEL]);
  page.clear();
  const id = await discoverAndWait(page, ["youtube"]);
  assert.deepEqual(plain(found(page, id)), [{ discoveryId: id, provider: "youtube", status: "held", reason: "menu_close_failed" }]);
  assert.equal(w.fb.tabCreates.length, 0);
});

test("YouTube: foreign, conflicting or changed channel identities hold", async () => {
  const run = async (home, channels, reason, status = "held") => {
    const { w, page } = await boot();
    setYT(w.fb, home, channels);
    page.clear();
    const id = await discoverAndWait(page, ["youtube"]);
    assert.deepEqual(plain(found(page, id)), [{ discoveryId: id, provider: "youtube", status, reason }], reason);
    assert.equal(page.of("discovery-done").length, 1);
    return w;
  };
  // The handle from the menu is not this account's own channel (no owner controls).
  const foreign = ytChannel({ studio: false, menu: menuLink("/@fx_other_yt") });
  await run(YT_OK_HOME, [foreign, foreign], "ownership_not_confirmed");
  // The channel page's own menu names someone else.
  const conflict = ytChannel({ menu: menuLink("/@fx_other_yt") });
  await run(YT_OK_HOME, [conflict, conflict], "identity_mismatch");
  // Two stable IDs on one channel page.
  const twoIds = ytChannel({ extraId: UC_B, menu: menuLink("/@fx_disc_yt") });
  await run(YT_OK_HOME, [twoIds, twoIds], "channel_id_conflict");
  // The fresh document disagrees: other stable ID, other menu handle, or no owner controls.
  await run(YT_OK_HOME, [YT_OK_CHANNEL, ytChannel({ id: UC_B, menu: menuLink("/@fx_disc_yt") })], "identity_changed");
  await run(YT_OK_HOME, [YT_OK_CHANNEL, conflict], "identity_changed");
  await run(YT_OK_HOME, [YT_OK_CHANNEL, foreign], "identity_changed");
});

// ---- permission, destination and message strictness ------------------------------
test("a missing permission returns permission_needed without opening or asking anything", async () => {
  const { w, page } = await boot({ grants: ["https://www.youtube.com", "https://myactivity.google.com"] });
  setBoth(w.fb);
  page.clear();
  const id = await discoverAndWait(page, ["x", "youtube"]);
  assert.deepEqual(found(page, id).map(r => [r.provider, r.status, r.reason || null]), [["x", "held", "permission_needed"], ["youtube", "ok", null]]);
  assert.equal(w.fb.tabCreates.some(t => t.url.startsWith("https://x.com")), false, "no X page was opened");
  assert.equal(w.fb.api.requests, 0);
  assert.equal(page.of("discovery-done").length, 1);
  const none = await boot({ grants: [] });
  setBoth(none.w.fb);
  none.page.clear();
  const id2 = await discoverAndWait(none.page, ["youtube", "x"]);
  assert.deepEqual(found(none.page, id2).map(r => [r.provider, r.reason]), [["youtube", "permission_needed"], ["x", "permission_needed"]]);
  assert.equal(none.w.fb.created.length + none.w.fb.windowCreates.length + none.w.fb.calls.length + none.w.fb.api.requests, 0);
});

test("a permission removed mid-discovery ends that provider as permission_lost and ignores what follows", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  w.fb.hooks.beforeRead = ({ count, fb }) => { if (count === 2) fb.revoke("https://x.com"); };
  page.clear();
  const id = await discoverAndWait(page, ["x", "youtube"]);
  assert.deepEqual(found(page, id).map(r => [r.provider, r.status, r.reason || null]), [["x", "error", "permission_lost"], ["youtube", "ok", null]]);
  assert.equal(w.fb.tabCreates.filter(t => t.url.startsWith("https://x.com")).length, 1, "the profile was never opened");
});

test("user selecting the owned tab ends that provider; the tab is left open and untouched", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  w.fb.hooks.beforeRead = ({ tab, cmd, fb }) => { if (cmd.op === "x_whoami") { fb.hooks.beforeRead = null; fb.userSelect(tab.id); } };
  page.clear();
  const id = await discoverAndWait(page, ["x"]);
  assert.deepEqual(found(page, id).map(r => [r.status, r.reason]), [["error", "tab_selected"]]);
  assert.deepEqual([w.fb.removed.length, w.fb.updates.length], [0, 0]);
});

test("no page-supplied target, Facebook, test or extra-key request reaches a provider", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  const worker = page.port().workerSide;
  const id = newId();
  for (const bad of [{ type: "DISCOVER", discoveryId: id, provider: "facebook" }, { type: "DISCOVER", discoveryId: id, provider: "x", url: "https://x.com/home" },
    { type: "DISCOVER", discoveryId: id, provider: "x", handle: "fx_disc_a" }, { type: "DISCOVER", discoveryId: id.slice(1), provider: "x" },
    { type: "DISCOVER", discoveryId: id }, { type: "DISCOVER", provider: "x" }, { type: "DISCOVER" }, { type: "CANCEL_DISCOVERY" },
    { type: "START", requestId: id, provider: "x", mode: "live", handle: "home", channelId: "" },
    { type: "START", requestId: id, provider: "x", mode: "live", handle: "settings", channelId: "" }]) worker.onMessage.emit(bad);
  await settleAll(30);
  assert.equal(w.fb.created.length + w.fb.windowCreates.length + w.fb.calls.length, 0);
  assert.deepEqual(worker.posted.filter(m => m.type === "DISCOVERY_RESULT" || m.type === "RESULT"), []);
  // From the page: every shape is exact and only x and youtube exist.
  for (const detail of [{ discoveryId: newId(), providers: ["facebook"] }, { discoveryId: newId(), providers: ["x"], handle: "fx_disc_a" },
    { discoveryId: newId(), providers: ["x"], url: "https://x.com/home" }, { discoveryId: newId(), providers: [] },
    { discoveryId: newId(), providers: ["x", "x"] }, { discoveryId: newId(), providers: ["x", "youtube", "x"] },
    { discoveryId: newId(), providers: "x" }, { providers: ["x"] }, { discoveryId: "abc", providers: ["x"] },
    { discoveryId: "A".repeat(32), providers: ["x"] }, { discoveryId: newId(), providers: [["x"]] }, [], null, "x"]) {
    page.click("res-find-accounts");
    page.send("discover", detail);
  }
  assert.deepEqual(discRejected(page), new Array(14).fill("invalid_input"));
  assert.equal(page.of("discovery-accepted").length, 0);
  assert.equal(w.fb.created.length + w.fb.windowCreates.length, 0);
  // Only the fixed discovery addresses ever opened, across all providers.
  const done = await discoverAndWait(page, ["x", "youtube"]);
  assert.equal(found(page, done).length, 2);
  const urls = [...w.fb.tabCreates.map(t => t.url), ...w.fb.windowCreates.map(c => c.url)];
  assert.deepEqual(urls.sort(), ["https://www.youtube.com/", "https://www.youtube.com/@fx_disc_yt", "https://www.youtube.com/@fx_disc_yt",
    "https://x.com/fx_disc_a", "https://x.com/home"]);
});

test("the worker keeps its sender checks and current-port rule for DISCOVER", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  const message = () => ({ type: "DISCOVER", discoveryId: newId(), provider: "x" });
  for (const over of [{ frameId: 1 }, { url: `${ORIGIN}/?a=1` }, { id: "other-extension" }, { origin: "https://evil.example" },
    { documentLifecycle: "prerender" }, { tab: { id: -1, windowId: 1 } }, { tab: undefined }]) {
    const stranger = link(w, { log: [], tamper: null }, "resonance-reader:root", over);
    w.fb.signals.onConnect.emit(stranger.workerSide);
    assert.equal(stranger.workerSide.closed, true, JSON.stringify(over));
    stranger.workerSide.onMessage.emit(message());
  }
  await settleAll(20);
  assert.equal(w.fb.created.length + w.fb.calls.length, 0);
  // A newer connection from the same tab replaces the old one; the old one can no longer start anything.
  const old = page.port().workerSide;
  const fresh = link(w, { log: [], tamper: null }, "resonance-reader:root", {});
  w.fb.signals.onConnect.emit(fresh.workerSide);
  await settleAll(5);
  old.onMessage.emit(message());
  await settleAll(20);
  assert.equal(w.fb.created.length, 0);
  fresh.workerSide.onMessage.emit(message());
  await settleAll(40);
  assert.ok(w.fb.created.length > 0, "the current port does start it");
});

// ---- gesture, exclusivity, replay ------------------------------------------------
test("discover needs an actual trusted click on button#res-find-accounts, used once, in the same task", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  const body = () => ({ discoveryId: newId(), providers: ["x"] });
  page.send("discover", body());                                   // no click
  page.click("res-find-accounts", false); page.send("discover", body()); // synthetic click
  page.click("res-check-now"); page.send("discover", body());       // another button
  page.click("res-reader-options"); page.send("discover", body());
  page.click("res-find-accounts", true, "div"); page.send("discover", body()); // not a button
  page.click("res-find-accounts"); page.expireGestures(); page.send("discover", body()); // the click's task is over
  page.click("res-find-accounts"); page.send("discover", { discoveryId: "short", providers: ["x"] }); // refused input spends the click
  page.send("discover", body());
  assert.deepEqual(discRejected(page), new Array(8).fill("gesture_required").map((r, i) => (i === 6 ? "invalid_input" : r)));
  // The find click gives no authority for a check, and a check click none for discovery.
  page.click("res-find-accounts");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "gesture_required");
  assert.equal(w.fb.created.length + w.fb.windowCreates.length + w.fb.calls.length, 0);
  // One click, one accepted discover; a second call in the same click is refused.
  const id = discover(page, ["x"]);
  page.send("discover", { discoveryId: newId(), providers: ["x"] });
  assert.equal(discRejected(page).pop(), "gesture_required");
  await until(discoveryEnded(page, id), "end");
  assert.equal(page.of("discovery-accepted").length, 1);
  // Wrong page: the bridge re-checks the exact root at the click.
  page.location.href = `${ORIGIN}/?a=1`;
  page.click("res-find-accounts");
  page.send("discover", body());
  assert.equal(discRejected(page).pop(), "wrong_page");
  page.location.href = ROOT_URL;
});

test("one job at a time: not alongside an armed or running check, another discovery, or a replayed ID", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  // An armed check blocks discovery.
  const batchId = arm(page, [req("x", "fx_x_ok")]);
  assert.equal(page.last("armed").batchId, batchId);
  discover(page, ["x"]);
  assert.deepEqual(discRejected(page), ["busy"]);
  page.send("cancel", { batchId });
  // A running discovery blocks a check, another discovery and a probe-free retry.
  w.gate.frozen = true;
  const id = discover(page, ["x", "youtube"]);
  await until(() => w.fb.calls.length > 0, "first read");
  page.click("res-check-now");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "busy");
  discover(page, ["youtube"]);
  assert.deepEqual(discRejected(page), ["busy", "busy"]);
  assert.equal(page.of("discovery-accepted").length, 1);
  // Cancel, then finish: the same ID is never accepted again, as a discovery or a batch.
  page.send("cancel-discovery", { discoveryId: id });
  w.gate.frozen = false;
  await settleAll(40);
  discover(page, ["x"], { discoveryId: id });
  assert.equal(discRejected(page).pop(), "replayed_discovery");
  page.click("res-check-now");
  page.send("arm", { batchId: id, requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  const again = await discoverAndWait(page, ["x"]);
  assert.equal(found(page, again)[0].status, "ok");
  discover(page, ["x"], { discoveryId: again });
  assert.equal(discRejected(page).pop(), "replayed_discovery");
});

test("providers run strictly one after the other, each with its own 130 s watchdog, and are never retried", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  const snapshots = [];
  page.document.addEventListener(`${PREFIX}discovery-result`, () => {
    snapshots.push({ asked: page.ports[0].bridgeSide.posted.filter(m => m.type === "DISCOVER").map(m => m.provider),
      watchdogs: page.pending(130000).length });
  });
  page.clear();
  const id = await discoverAndWait(page, ["youtube", "x"]);
  assert.deepEqual(plain(page.ports[0].bridgeSide.posted).map(m => [m.type, m.discoveryId, m.provider]),
    [["DISCOVER", id, "youtube"], ["DISCOVER", id, "x"]]);
  assert.deepEqual(found(page, id).map(r => r.provider), ["youtube", "x"]);
  // At each result the bridge had asked only for the providers up to and including the current one.
  assert.deepEqual(snapshots.map(s => s.asked), [["youtube"], ["youtube", "x"]]);
  assert.deepEqual(snapshots.map(s => s.watchdogs), [0, 0], "each watchdog is cleared before its result is shown");
  assert.equal(page.pending(130000).length, 0);
  // Exactly one 130 s watchdog exists while a provider is being read.
  w.gate.frozen = true;
  const before = w.fb.calls.length;
  discover(page, ["x", "youtube"]);
  await until(() => w.fb.calls.length > before, "read");
  assert.equal(page.pending(130000).length, 1);
  assert.equal(page.pending(480000).length + page.pending(5000).length, 0);
});

// ---- cancellation, late results, disconnect, deadline -----------------------------
test("cancel-discovery needs no gesture, revokes work, emits cancelled once and nothing after", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  w.gate.frozen = true;
  const id = discover(page, ["x", "youtube"]);
  await until(() => w.fb.calls.length > 0, "first read");
  assert.equal(found(page, id).length, 0);
  page.send("cancel-discovery", { discoveryId: id });
  await settleAll(20);
  assert.deepEqual(plain(page.of("discovery-cancelled")), [{ discoveryId: id }]);
  assert.ok(page.ports[0].bridgeSide.posted.some(m => m.type === "CANCEL_DISCOVERY" && m.discoveryId === id));
  assert.equal(page.pending(130000).length, 0);
  // The worker's job ended silently: nothing about it is posted, and the second provider never starts.
  w.gate.frozen = false;
  await settleAll(60);
  assert.deepEqual(page.ports[0].bridgeSide.posted.filter(m => m.type === "DISCOVER").map(m => m.provider), ["x"]);
  assert.equal(page.ports[0].workerSide.posted.some(m => m.type === "DISCOVERY_RESULT"), false);
  // A late, even perfectly valid, result is ignored; so is a second cancel, a stale one and a malformed one.
  page.workerPost({ type: "DISCOVERY_RESULT", discoveryId: id, provider: "x", status: "ok",
    identity: { handle: "fx_disc_a", channel_id: null, profile_url: "https://x.com/fx_disc_a" }, observed_at: new Date().toISOString() });
  page.send("cancel-discovery", { discoveryId: id });
  page.send("cancel-discovery", { discoveryId: newId() });
  page.send("cancel-discovery", { discoveryId: id, extra: 1 });
  page.send("cancel-discovery", {});
  await settleAll(10);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-cancelled", "discovery-rejected", "discovery-rejected"]);
  assert.deepEqual(discRejected(page), ["invalid_input", "invalid_input"]);
  assert.equal(w.fb.removed.length + w.fb.updates.length, 0, "provider tabs stay open and unowned");
  // The worker is free at once.
  const next = await discoverAndWait(page, ["x"]);
  assert.equal(found(page, next)[0].status, "ok");
});

test("forged, stale or malformed worker replies never become results", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  w.gate.frozen = true;
  const freeWorker = async () => { w.timers[w.timers.length - 1](); await settleAll(5); };
  const id = discover(page, ["x"]);
  await until(() => w.fb.calls.length > 0, "read");
  const okx = { type: "DISCOVERY_RESULT", discoveryId: id, provider: "x", status: "ok",
    identity: { handle: "fx_disc_a", channel_id: null, profile_url: "https://x.com/fx_disc_a" }, observed_at: "2026-11-01T10:00:00.000Z" };
  // Ignored outright (not for this discovery, this provider, or not a discovery message).
  for (const stray of [{ ...okx, discoveryId: newId() }, { ...okx, provider: "youtube" }, { ...okx, type: "RESULT" },
    { type: "RESULT", requestId: id, provider: "x", mode: "live", status: "held", tabs: [], reason: "signed_out" },
    { type: "PROGRESS", requestId: id, provider: "x", mode: "live", stage: "identity" }, null, "DISCOVERY_RESULT", [], {}]) {
    page.workerPost(stray);
  }
  await settleAll(10);
  assert.deepEqual(dnames(page), ["discovery-accepted"]);
  // Addressed correctly but not valid: the provider ends as result_invalid, once, then done.
  page.workerPost({ ...okx, extra: 1 });
  await until(discoveryEnded(page, id), "end");
  assert.deepEqual(plain(found(page, id)), [{ discoveryId: id, provider: "x", status: "error", reason: "result_invalid" }]);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-result", "discovery-done"]);
  page.workerPost(okx); // Late, after done.
  await settleAll(10);
  assert.equal(found(page, id).length, 1);
  await freeWorker();
  // A bad identity of every kind is refused the same way.
  const bads = [{ ...okx, identity: { ...okx.identity, profile_url: "https://x.com/someone_else" } }, { ...okx, observed_at: "soon" },
    { ...okx, status: "cancelled" }, { ...okx, identity: { ...okx.identity, channel_id: UC_A } }];
  for (const bad of bads) {
    page.clear();
    const next = discover(page, ["x"]);
    await settleAll(20);
    assert.equal(page.of("discovery-accepted").length, 1);
    page.workerPost({ ...bad, discoveryId: next });
    await until(discoveryEnded(page, next), "end");
    assert.deepEqual(found(page, next).map(r => [r.status, r.reason]), [["error", "result_invalid"]], JSON.stringify(bad));
    await freeWorker();
  }
});

test("a worker restart or disconnect abandons the discovery, once, and late replies are ignored", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  w.gate.frozen = true;
  const id = discover(page, ["x", "youtube"]);
  await until(() => w.fb.calls.length > 0, "read");
  w.kill();
  w.gate.frozen = false;
  await settleAll(40);
  assert.deepEqual(plain(page.of("disconnected")), [{ reason: "transport_disconnected", batchId: null }]);
  assert.deepEqual(plain(page.of("discovery-aborted")), [{ discoveryId: id, reason: "transport_disconnected" }]);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-aborted"]);
  assert.equal(page.pending(130000).length, 0);
  assert.equal(w.connects, 1, "nothing reconnects by itself");
  assert.equal(page.ports[0].bridgeSide.posted.filter(m => m.type === "DISCOVER").length, 1);
  // Late replies on the dead connection find nothing to match.
  page.ports[0].bridgeSide.onMessage.emit({ type: "DISCOVERY_RESULT", discoveryId: id, provider: "x", status: "held", reason: "signed_out" });
  await settleAll(5);
  assert.equal(page.of("discovery-result").length, 0);
  // Only a fresh trusted click with a new ID reconnects and runs again.
  const next = await discoverAndWait(page, ["x"]);
  assert.equal(w.connects, 2);
  assert.equal(found(page, next)[0].status, "ok");
});

test("a root address change or reload abandons the discovery and starts nothing more", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  w.gate.frozen = true;
  const id = discover(page, ["x", "youtube"]);
  await until(() => w.fb.calls.length > 0, "read");
  // The app tab leaves its exact root: the worker ends the provider; the bridge starts no other.
  page.location.href = `${ORIGIN}/?moved=1`;
  w.fb.userNavigate(w.rootTab, `${ORIGIN}/?moved=1`);
  w.gate.frozen = false;
  await until(discoveryEnded(page, id), "end");
  await settleAll(20);
  assert.deepEqual(found(page, id).map(r => [r.provider, r.status, r.reason]), [["x", "error", "root_navigated"]]);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-result", "discovery-cancelled"]);
  assert.equal(page.of("discovery-done").length, 0);
  assert.equal(page.ports[0].bridgeSide.posted.filter(m => m.type === "DISCOVER").length, 1);
  assert.equal(w.fb.windowCreates.length, 0);
  page.location.href = ROOT_URL;
  // A document that starts loading again silently ends the worker's job; the watchdog then settles the page.
  const second = await boot();
  setBoth(second.w.fb);
  second.page.clear();
  second.w.gate.frozen = true;
  const id2 = discover(second.page, ["x", "youtube"]);
  await until(() => second.w.fb.calls.length > 0, "read");
  second.w.fb.signals.onUpdated.emit(second.w.rootTab, { status: "loading" });
  second.w.gate.frozen = false;
  await settleAll(30);
  assert.equal(second.page.of("discovery-result").length, 0, "the abandoned job reports nothing");
  second.page.fire(130000);
  assert.deepEqual(found(second.page, id2).map(r => [r.provider, r.status, r.reason]),
    [["x", "error", "reader_unresponsive"], ["youtube", "error", "reader_unresponsive"]]);
  assert.deepEqual(dnames(second.page), ["discovery-accepted", "discovery-result", "discovery-result", "discovery-done"]);
});

test("the deadline: a silent reader ends each remaining provider, drops the connection and ignores late replies", async () => {
  const { w, page } = await boot();
  setBoth(w.fb);
  page.clear();
  w.gate.frozen = true;
  const id = discover(page, ["x", "youtube"]);
  await until(() => w.fb.calls.length > 0, "read");
  assert.equal(page.pending(130000).length, 1);
  page.fire(130000);
  assert.deepEqual(found(page, id).map(r => [r.provider, r.status, r.reason]),
    [["x", "error", "reader_unresponsive"], ["youtube", "error", "reader_unresponsive"]]);
  assert.deepEqual(dnames(page), ["discovery-accepted", "discovery-result", "discovery-result", "discovery-done"]);
  assert.ok(page.ports[0].bridgeSide.closed);
  assert.equal(page.pending(130000).length, 0);
  page.ports[0].bridgeSide.onMessage.emit({ type: "DISCOVERY_RESULT", discoveryId: id, provider: "x", status: "held", reason: "signed_out" });
  w.gate.frozen = false;
  await settleAll(40);
  assert.equal(found(page, id).length, 2);
  assert.equal(page.of("discovery-done").length, 1);
  assert.equal(page.of("disconnected").length, 0, "an expiry is not a transport report");
  // The worker's own limit (two minutes) ends the provider with timeout, and the next one still runs.
  const second = await boot();
  setBoth(second.w.fb);
  second.page.clear();
  second.w.gate.frozen = true;
  const id2 = discover(second.page, ["x", "youtube"]);
  await until(() => second.w.fb.calls.length > 0, "read");
  assert.ok(second.w.timers.length > 0);
  second.w.timers[second.w.timers.length - 1]();
  second.w.gate.frozen = false;
  await until(discoveryEnded(second.page, id2), "end");
  assert.deepEqual(found(second.page, id2).map(r => [r.provider, r.status, r.reason || null]), [["x", "error", "timeout"], ["youtube", "ok", null]]);
});

// ---- the page-side controller ----------------------------------------------------
const adCtx = vm.createContext({ console, Date });
vm.runInContext(fs.readFileSync(path.join(ROOT, "static", "account-discovery.js"), "utf8"), adCtx, { filename: "account-discovery.js" });
const AD = adCtx.ResonanceAccountDiscovery;
const T0 = Date.parse("2026-11-01T12:00:00.000Z");
const MIN = 60 * 1000;

function rigController({ onEmit } = {}) {
  const state = { now: T0, sent: [], handlers: new Map(), workspace: null, ids: [] };
  const fire = (name, detail) => { const handler = state.handlers.get(PREFIX + name); if (handler) handler(plain(detail)); };
  const ctl = AD.create({
    emit(type, detail) { state.sent.push({ type, detail: plain(detail) }); if (onEmit) onEmit(type, plain(detail), fire); },
    on(type, handler) { state.handlers.set(type, handler); return () => state.handlers.delete(type); },
    change(handler) { state.workspace = handler; return () => { state.workspace = null; }; },
    clock: () => state.now,
    randomId: () => { const id = newId(); state.ids.push(id); return id; },
  });
  return { ctl, state, fire };
}
const xOk = (id, at = T0, handle = "fx_disc_a") => ({ discoveryId: id, provider: "x", status: "ok",
  identity: { handle, channel_id: null, profile_url: `https://x.com/${handle}` }, observed_at: new Date(at).toISOString() });
const ytOk = (id, at = T0, channel = UC_A) => ({ discoveryId: id, provider: "youtube", status: "ok",
  identity: { handle: "fx_disc_yt", channel_id: channel, profile_url: `https://www.youtube.com/channel/${channel}` }, observed_at: new Date(at).toISOString() });
const heldRes = (id, provider, reason = "signed_out") => ({ discoveryId: id, provider, status: "held", reason });

test("controller: starts only on an explicit call, emits the exact discover event and tracks accepted, results and done", () => {
  const { ctl, state, fire } = rigController();
  assert.equal(state.sent.length, 0, "nothing starts by itself");
  assert.equal(ctl.state().status, "idle");
  for (const bad of [[], ["x", "x"], ["facebook"], ["x", "youtube", "x"], "x", null, undefined, [{}], ["X"]]) {
    assert.deepEqual(plain(ctl.start(bad)), { ok: false, reason: "invalid_input" });
  }
  assert.equal(state.sent.length, 0);
  const started = ctl.start(["x", "youtube"]);
  assert.deepEqual(plain(started), { ok: true, discoveryId: state.ids[0] });
  assert.deepEqual(state.sent, [{ type: `${PREFIX}discover`, detail: { discoveryId: state.ids[0], providers: ["x", "youtube"] } }]);
  assert.equal(ctl.state().status, "starting");
  assert.deepEqual(plain(ctl.start(["x"])), { ok: false, reason: "busy" });
  const id = state.ids[0];
  fire("discovery-result", xOk(id));
  assert.equal(ctl.results().length, 0, "a result before accepted is ignored");
  fire("discovery-accepted", { discoveryId: id });
  assert.equal(ctl.state().status, "running");
  fire("discovery-result", xOk(id, T0 + 1000));
  fire("discovery-done", { discoveryId: id });
  assert.equal(ctl.state().status, "aborted", "done before every provider reported is a protocol error");
  assert.equal(ctl.state().reason, "incomplete_results");
  assert.equal(ctl.results().length, 0);
  // A clean run.
  const two = ctl.start(["x", "youtube"]);
  fire("discovery-accepted", { discoveryId: two.discoveryId });
  fire("discovery-result", xOk(two.discoveryId, T0 + 1000));
  fire("discovery-result", heldRes(two.discoveryId, "youtube", "current_handle_unavailable"));
  assert.equal(ctl.state().status, "running");
  fire("discovery-done", { discoveryId: two.discoveryId });
  const snapshot = plain(ctl.state());
  assert.equal(snapshot.status, "done");
  assert.deepEqual(snapshot.results.map(r => [r.provider, r.status]), [["x", "ok"], ["youtube", "held"]]);
  assert.deepEqual(plain(ctl.candidates()).map(r => r.provider), ["x"]);
  assert.deepEqual(snapshot.results[0].identity, { handle: "fx_disc_a", channel_id: null, profile_url: "https://x.com/fx_disc_a" });
  assert.equal(Object.hasOwn(snapshot.results[0], "discoveryId"), false);
  assert.equal(ctl.start(["x"]).ok, true, "a finished discovery does not block the next click");
});

test("controller: results are validated exactly (shape, identity, derived URL, timestamp, provider, duplicates)", () => {
  const { ctl, state, fire } = rigController();
  const go = providers => { const r = ctl.start(providers); fire("discovery-accepted", { discoveryId: r.discoveryId }); return r.discoveryId; };
  const id = go(["x", "youtube"]);
  const base = xOk(id, T0 + 1000);
  const bads = [
    { ...base, extra: 1 }, { ...base, tabs: [] }, { ...base, discoveryId: newId() }, { ...base, status: "cancelled" }, { ...base, status: "partial" },
    { ...base, provider: "facebook" }, { ...base, identity: { ...base.identity, profile_url: "https://x.com/other" } },
    { ...base, identity: { ...base.identity, profile_url: "https://x.com/fx_disc_a?x=1" } }, { ...base, identity: { ...base.identity, channel_id: UC_A } },
    { ...base, identity: { ...base.identity, handle: "settings", profile_url: "https://x.com/settings" } },
    { ...base, identity: { ...base.identity, handle: "a".repeat(16), profile_url: `https://x.com/${"a".repeat(16)}` } },
    { ...base, identity: { handle: "fx_disc_a", channel_id: null } }, { ...base, identity: null }, { ...base, identity: [] },
    { ...base, observed_at: "yesterday" }, { ...base, observed_at: 5 }, { ...base, observed_at: "2026-13-40T00:00:00Z" },
    { ...base, observed_at: new Date(T0 + 2 * MIN).toISOString() }, { ...base, observed_at: new Date(T0 - 2 * MIN).toISOString() },
    { ...ytOk(id), identity: { ...ytOk(id).identity, channel_id: null } },
    { ...ytOk(id), identity: { ...ytOk(id).identity, profile_url: "https://www.youtube.com/@fx_disc_yt" } },
    { ...ytOk(id), identity: { ...ytOk(id).identity, profile_url: `https://www.youtube.com/channel/${UC_B}` } },
    { ...ytOk(id), identity: { ...ytOk(id).identity, channel_id: "UCtooShort" } },
    { ...heldRes(id, "x"), reason: "Signed out" }, { ...heldRes(id, "x"), reason: "" }, { ...heldRes(id, "x"), identity: base.identity },
    { ...heldRes(id, "x"), observed_at: base.observed_at }, null, "ok", [], 7];
  for (const bad of bads) fire("discovery-result", bad);
  assert.equal(ctl.results().length, 0, "nothing invalid was kept");
  assert.equal(ctl.state().ignored, bads.length);
  fire("discovery-result", base);
  fire("discovery-result", xOk(id, T0 + 2000, "fx_disc_b")); // second result for the same provider
  assert.equal(ctl.results().length, 1);
  assert.equal(ctl.results()[0].identity.handle, "fx_disc_a");
  // A provider that was not selected is ignored.
  ctl.clear();
  const only = go(["youtube"]);
  fire("discovery-result", xOk(only, T0 + 3000));
  fire("discovery-result", ytOk(only, T0 + 3000));
  assert.deepEqual(plain(ctl.results()).map(r => r.provider), ["youtube"]);
});

test("controller: the AD validator agrees with the extension's validator on every sample", () => {
  const id = newId();
  const samples = [xOk(id), ytOk(id), heldRes(id, "x"), heldRes(id, "youtube", "x"), { ...xOk(id), extra: 1 },
    { ...xOk(id), identity: { ...xOk(id).identity, handle: "Home", profile_url: "https://x.com/Home" } },
    { ...xOk(id), identity: { ...xOk(id).identity, handle: "abc_DEF_123", profile_url: "https://x.com/abc_DEF_123" } },
    { ...ytOk(id), identity: { ...ytOk(id).identity, handle: "a.b-c_d" } }, { ...ytOk(id), identity: { ...ytOk(id).identity, handle: "a b c" } },
    { ...ytOk(id), identity: { ...ytOk(id).identity, handle: "ab" } }, { ...ytOk(id), observed_at: "2026-11-01T12:00:00Z" },
    { ...xOk(id), observed_at: "2026-11-01T12:00:00.1234Z" }, { ...heldRes(id, "x"), reason: "A" }, { ...heldRes(id, "x"), status: "error" }];
  for (const sample of samples) {
    const message = { type: "DISCOVERY_RESULT", ...sample };
    const mine = AD.validateResult(plain(sample));
    const theirs = S.validateDiscoveryResult(plain(message), { discoveryId: id, provider: sample.provider });
    assert.equal(mine, theirs, JSON.stringify(sample));
  }
});

test("controller: results live five minutes in memory, then are gone", () => {
  const { ctl, state, fire } = rigController();
  const { discoveryId: id } = ctl.start(["x", "youtube"]);
  fire("discovery-accepted", { discoveryId: id });
  fire("discovery-result", xOk(id, T0));
  state.now = T0 + 2 * MIN;
  fire("discovery-result", ytOk(id, state.now));
  fire("discovery-done", { discoveryId: id });
  assert.equal(AD.TTL_MS, 5 * MIN);
  assert.equal(ctl.nextExpiry(), T0 + 5 * MIN);
  state.now = T0 + 5 * MIN - 1;
  assert.deepEqual(plain(ctl.results()).map(r => r.provider), ["x", "youtube"]);
  state.now = T0 + 5 * MIN;
  assert.deepEqual(plain(ctl.results()).map(r => r.provider), ["youtube"]);
  assert.deepEqual(plain(ctl.candidates()).map(r => r.provider), ["youtube"]);
  assert.equal(ctl.nextExpiry(), T0 + 7 * MIN);
  state.now = T0 + 7 * MIN;
  assert.deepEqual([ctl.results().length, ctl.candidates().length, ctl.nextExpiry()], [0, 0, null]);
  // Copies: changing what was returned changes nothing held.
  const second = ctl.start(["x"]);
  fire("discovery-accepted", { discoveryId: second.discoveryId });
  fire("discovery-result", xOk(second.discoveryId, state.now));
  ctl.candidates()[0].identity.handle = "changed";
  ctl.results()[0].status = "error";
  assert.equal(ctl.candidates()[0].identity.handle, "fx_disc_a");
  assert.equal(ctl.discard("x"), true);
  assert.equal(ctl.discard("x"), false);
  assert.equal(ctl.results().length, 0);
});

test("controller: cancel revokes active work; late replies, done and cancelled echoes are ignored", () => {
  const { ctl, state, fire } = rigController();
  assert.equal(ctl.cancel(), false);
  const { discoveryId: id } = ctl.start(["x", "youtube"]);
  fire("discovery-accepted", { discoveryId: id });
  fire("discovery-result", xOk(id));
  assert.equal(ctl.cancel(), true);
  assert.deepEqual(state.sent.pop(), { type: `${PREFIX}cancel-discovery`, detail: { discoveryId: id } });
  const snapshot = ctl.state();
  assert.deepEqual([snapshot.status, snapshot.results.length, snapshot.discoveryId], ["cancelled", 0, id]);
  const before = ctl.state().ignored;
  fire("discovery-result", ytOk(id));
  fire("discovery-done", { discoveryId: id });
  fire("discovery-cancelled", { discoveryId: id });
  assert.equal(ctl.results().length, 0);
  assert.equal(ctl.state().status, "cancelled");
  assert.equal(ctl.state().ignored, before + 3);
  assert.equal(ctl.cancel(), false);
  // A finished discovery is not cancelled; clear forgets it.
  const next = ctl.start(["x"]);
  fire("discovery-accepted", { discoveryId: next.discoveryId });
  fire("discovery-result", xOk(next.discoveryId));
  fire("discovery-done", { discoveryId: next.discoveryId });
  assert.equal(ctl.cancel(), false);
  assert.equal(ctl.results().length, 1);
  ctl.clear();
  assert.deepEqual([ctl.state().status, ctl.results().length, ctl.candidates().length], ["idle", 0, 0]);
  // The extension's own cancelled event ends an active one.
  const third = ctl.start(["x"]);
  fire("discovery-accepted", { discoveryId: third.discoveryId });
  fire("discovery-cancelled", { discoveryId: third.discoveryId });
  assert.equal(ctl.state().status, "cancelled");
});

test("controller: a workspace change cancels active work and clears every result", () => {
  const { ctl, state, fire } = rigController();
  const a = ctl.start(["x", "youtube"]);
  fire("discovery-accepted", { discoveryId: a.discoveryId });
  fire("discovery-result", xOk(a.discoveryId));
  assert.equal(typeof state.workspace, "function");
  state.workspace();
  assert.equal(state.sent.pop().type, `${PREFIX}cancel-discovery`);
  assert.deepEqual([ctl.state().status, ctl.results().length], ["idle", 0]);
  fire("discovery-result", ytOk(a.discoveryId));
  fire("discovery-done", { discoveryId: a.discoveryId });
  assert.equal(ctl.results().length, 0, "late results after the change are dropped");
  const b = ctl.start(["x"]);
  fire("discovery-accepted", { discoveryId: b.discoveryId });
  fire("discovery-result", xOk(b.discoveryId));
  fire("discovery-done", { discoveryId: b.discoveryId });
  assert.equal(ctl.candidates().length, 1);
  const sentBefore = state.sent.length;
  state.workspace();
  assert.equal(state.sent.length, sentBefore, "nothing to cancel once finished");
  assert.deepEqual([ctl.state().status, ctl.candidates().length], ["idle", 0]);
});

test("controller: rejected, aborted and disconnected end an active discovery without keeping results", () => {
  // Rejected synchronously inside emit (the bridge answers in the same call).
  const sync = rigController({ onEmit: (type, detail, fire) => { if (type === `${PREFIX}discover`) fire("discovery-rejected", { discoveryId: detail.discoveryId, reason: "gesture_required" }); } });
  assert.deepEqual(plain(sync.ctl.start(["x"])), { ok: false, reason: "gesture_required" });
  assert.deepEqual([sync.ctl.state().status, sync.ctl.state().reason], ["rejected", "gesture_required"]);
  assert.equal(sync.ctl.start(["x"]).ok === false, true, "the same trusted-click rule applies again");
  // Accepted synchronously inside emit.
  const quick = rigController({ onEmit: (type, detail, fire) => { if (type === `${PREFIX}discover`) fire("discovery-accepted", { discoveryId: detail.discoveryId }); } });
  assert.equal(quick.ctl.start(["x"]).ok, true);
  assert.equal(quick.ctl.state().status, "running");
  // A rejection with no ID (invalid input) also ends a start; a stranger's does not.
  const { ctl, fire, state } = rigController();
  const r = ctl.start(["x"]);
  fire("discovery-rejected", { discoveryId: newId(), reason: "busy" });
  assert.equal(ctl.state().status, "starting");
  fire("discovery-rejected", { discoveryId: null, reason: "busy" });
  assert.deepEqual([ctl.state().status, ctl.state().reason], ["rejected", "busy"]);
  assert.equal(r.ok, true);
  // Aborted by the extension keeps nothing.
  const b = ctl.start(["x", "youtube"]);
  fire("discovery-accepted", { discoveryId: b.discoveryId });
  fire("discovery-result", xOk(b.discoveryId));
  fire("discovery-aborted", { discoveryId: newId(), reason: "transport_disconnected" });
  assert.equal(ctl.state().status, "running");
  fire("discovery-aborted", { discoveryId: b.discoveryId, reason: "transport_disconnected" });
  assert.deepEqual([ctl.state().status, ctl.state().reason, ctl.results().length], ["aborted", "transport_disconnected", 0]);
  // The generic disconnected report ends an active one, and is ignored otherwise.
  const c = ctl.start(["x"]);
  fire("discovery-accepted", { discoveryId: c.discoveryId });
  fire("disconnected", { reason: "extension_context_invalidated", batchId: null });
  assert.deepEqual([ctl.state().status, ctl.state().reason], ["aborted", "extension_context_invalidated"]);
  const d = ctl.start(["x"]);
  fire("discovery-accepted", { discoveryId: d.discoveryId });
  fire("discovery-result", xOk(d.discoveryId));
  fire("discovery-done", { discoveryId: d.discoveryId });
  fire("disconnected", { reason: "transport_disconnected", batchId: null });
  assert.equal(ctl.state().status, "done", "a finished discovery's results are not discarded by a later disconnect");
  fire("disconnected", { reason: "Bad Reason", extra: 1 });
  assert.equal(ctl.state().status, "done");
  // An older reader that never answers: the app ends the wait itself.
  const stalled = ctl.start(["x"]);
  assert.equal(ctl.expireStalled(1000), false);
  state.now += 1500;
  assert.equal(ctl.expireStalled(1000), true);
  assert.deepEqual([ctl.state().status, ctl.state().reason], ["aborted", "no_response"]);
  assert.equal(state.sent.pop().detail.discoveryId, stalled.discoveryId);
});

test("controller: bad construction, clock, ID and emit failures start nothing; destroy unhooks everything", () => {
  const noop = () => {};
  for (const missing of ["emit", "on", "change", "clock"]) {
    const options = { emit: noop, on: () => noop, change: () => noop, clock: () => T0 };
    delete options[missing];
    assert.throws(() => AD.create(options), new RegExp(`needs a ${missing}`));
  }
  assert.throws(() => AD.create(), /needs a emit/);
  const mk = over => AD.create({ emit: noop, on: () => noop, change: () => noop, clock: () => T0, randomId: newId, ...over });
  assert.deepEqual(plain(mk({ clock: () => NaN }).start(["x"])), { ok: false, reason: "clock_unavailable" });
  assert.deepEqual(plain(mk({ clock: () => "now" }).start(["x"])), { ok: false, reason: "clock_unavailable" });
  assert.deepEqual(plain(mk({ randomId: () => "not-an-id" }).start(["x"])), { ok: false, reason: "id_unavailable" });
  assert.deepEqual(plain(mk({ randomId: () => null }).start(["x"])), { ok: false, reason: "id_unavailable" });
  const failing = mk({ emit() { throw new Error("no"); } });
  assert.deepEqual(plain(failing.start(["x"])), { ok: false, reason: "emit_failed" });
  assert.equal(failing.state().status, "aborted");
  assert.equal(failing.start(["x"]).ok, false, "a failed start leaves no hidden active session behind");
  const { ctl, state } = rigController();
  assert.equal(state.handlers.size, 7);
  const seen = [];
  const off = ctl.subscribe(s => seen.push(s.status));
  const r = ctl.start(["x"]);
  assert.equal(r.ok, true);
  assert.deepEqual(seen, ["starting"]);
  off();
  ctl.cancel();
  assert.deepEqual(seen, ["starting"], "an unsubscribed listener hears nothing");
  ctl.subscribe(() => { throw new Error("listener failure"); });
  assert.equal(ctl.start(["x"]).ok, true, "a throwing listener cannot break the controller");
  ctl.destroy();
  assert.equal(state.handlers.size, 0);
  assert.equal(state.workspace, null);
  assert.equal(ctl.state().status, "idle");
});

test("controller: no DOM, network, storage, timers or saving; sources stay out of the extension", () => {
  const source = fs.readFileSync(path.join(ROOT, "static", "account-discovery.js"), "utf8");
  for (const pattern of [/\bdocument\b/, /\bwindow\b/, /\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /sendBeacon/, /localStorage|sessionStorage|indexedDB/,
    /\bsetTimeout\b|\bsetInterval\b/, /addEventListener|dispatchEvent|CustomEvent/, /innerHTML|querySelector/, /\bimport\b|\brequire\s*\(/,
    /\/api\//, /\bchrome\./, /\beval\s*\(|new Function/]) assert.equal(pattern.test(source), false, String(pattern));
  assert.equal(AD.PREFIX, PREFIX);
  assert.deepEqual(plain(AD.PROVIDERS), ["x", "youtube"]);
  assert.equal(Object.isFrozen(AD), true);
  assert.equal(fs.existsSync(path.join(PKG, "account-discovery.js")), false);
});

// ---- the build tool still accepts the package --------------------------------------
test("the build tool validates the package with the discovery markers and refuses it without them", () => {
  const tool = path.join(ROOT, "tools", "build_reader.py");
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "res-disc-build-"));
  try {
    const ok = childProcess.spawnSync("python3", ["-I", tool, "--out", path.join(base, "a")], { encoding: "utf8" });
    assert.equal(ok.status, 0, ok.stderr);
    const source = fs.readFileSync(tool, "utf8");
    for (const marker of ["res-find-accounts", "cancel-discovery", "DISCOVERY_RESULT", "validDiscoveryDestination"]) assert.ok(source.includes(marker), marker);
    assert.equal(/\bimport (requests|urllib|http|socket)/.test(source), false);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (error) {
      failed += 1;
      console.log(`not ok - ${name}\n${error && error.stack ? error.stack : error}`);
    }
  }
  console.log(`${tests.length - failed}/${tests.length} reader discovery checks passed`);
  process.exit(failed ? 1 : 0);
})();
