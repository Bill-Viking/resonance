"use strict";

// Deterministic checks for the production Resonance Reader package in
// browser-reader/. The real worker, bridge, shared parsers and page reader from
// that folder run as shipped, in Node VM contexts, wired to each other through
// linked fake ports. Only the browser (tabs, windows, permissions, scripting,
// ports) and the DOM are simulated, using the fake-browser harness and the
// invented fixture pages vendored in tests/reader-fixtures/ (tests only; the
// shipped package never imports them, and a public export carries them). This
// does NOT replace native Chrome/Edge runs, a store install, or any real
// provider read.
//
// Required as a module (test_reader_pipeline.cjs does this) it only exports the
// rig below and runs nothing.
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const ROOT = path.resolve(__dirname, "..");
const PKG = path.join(ROOT, "browser-reader");
const HARNESS = path.join(__dirname, "reader-fixtures");
const { FakeBrowser, signal } = require(path.join(HARNESS, "fake_browser.cjs"));

const ORIGIN = "http://127.0.0.1:4317";
const ROOT_URL = `${ORIGIN}/`;
const PREFIX = "resonance:reader:";
const PAGE_EVENTS = new Set(["arm", "start", "cancel", "probe", "options"]);
const CH_B = "UCfixtureChannel0000002B";
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
  const opened = [];
  fb.chrome.runtime.openOptionsPage = () => { opened.push(true); return Promise.resolve(); };
  const timers = [];
  // Everything in this rig is microtask-driven, so a whole account normally
  // finishes inside one test tick. Freezing the gate parks a job at its first
  // wait (after its progress events) so a test can act in the middle of a read.
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
  const w = { fb, ctx, opened, timers, gate, workerSides, rootTab: fb.addUserTab(options.rootUrl || ROOT_URL), connects: 0,
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
        if (event.type === `${PREFIX}result`) log.push(`result:${event.detail.result.requestId}`);
      }
      for (const l of listeners.get(event.type) || []) l(event);
    },
  };
  const timers = [];
  // A browser location carries every part of the address, so a bridge that looks
  // at pathname, search or hash (an app page such as /#overview) sees them.
  const parsed = new URL(url);
  const location = { origin, href: url, protocol: parsed.protocol, host: parsed.host, hostname: parsed.hostname,
    port: parsed.port, pathname: parsed.pathname, search: parsed.search, hash: parsed.hash };
  const window = {};
  window.top = top ? window : {};
  const page = { w, events, log, timers, location, document, tamper: null, failPost: null, failConnect: null, ports: [] };
  const chrome = { runtime: { id: "test-extension", connect({ name }) {
    if (page.failConnect) throw page.failConnect;
    w.connects += 1;
    // The browser reports the document's own address, fragment included.
    const pair = link(w, page, name, { url: location.href });
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
async function check(page, requests, options) {
  const batchId = arm(page, requests, options);
  assert.equal(page.last("armed") && page.last("armed").batchId, batchId, "armed");
  page.send("start", { batchId });
  return batchId;
}
const finished = (page, batchId) => () => page.of("done").some(d => d.batchId === batchId) ||
  page.of("cancelled").some(d => d.batchId === batchId) || page.of("disconnected").some(d => d.batchId === batchId);
const resultsOf = (page, batchId) => page.of("result").filter(r => r.batchId === batchId).map(r => r.result);
const reasonsOf = page => page.of("rejected").map(r => r.reason);

// ---- static audit of the shipped package ----------------------------------
const SHIPPED = ["bridge.js", "manifest.json", "options.html", "options.js", "popup.html", "popup.js",
  "reader.js", "shared.js", "worker.js", "icon-16.png", "icon-32.png", "icon-128.png"];

test("the vendored harness is self-contained, synthetic and loads the production package", () => {
  const files = ["fake_browser.cjs", "dom_adapter.cjs", "fx.js", path.join("fixtures", "routes.json")];
  for (const name of files) assert.ok(fs.existsSync(path.join(HARNESS, name)), `${name} is vendored`);
  const harness = fs.readFileSync(path.join(HARNESS, "fake_browser.cjs"), "utf8");
  assert.equal(/experiments|live-reader-spike/.test(harness), false, "no path into the development experiment");
  assert.match(harness, /path\.join\(__dirname, "\.\.", "\.\.", "browser-reader"\)/, "injects the production package");
  const privateHome = new RegExp("/" + "Users/|/" + "home/|[A-Z]:[\\\\/]+" + "Users", "i");
  const table = JSON.parse(fs.readFileSync(path.join(HARNESS, "fixtures", "routes.json"), "utf8"));
  assert.equal(Object.keys(table.routes).some(route => /facebook/i.test(route)), false);
  const pages = fs.readdirSync(path.join(HARNESS, "fixtures")).filter(name => name.endsWith(".html"));
  assert.ok(pages.length > 0);
  for (const name of [...files, ...pages.map(page => path.join("fixtures", page))]) {
    assert.equal(privateHome.test(fs.readFileSync(path.join(HARNESS, name), "utf8")), false, `${name} has no machine path`);
  }
  // Every route and activity page the table names is present in the vendored folder.
  const named = new Set();
  for (const entry of [...Object.values(table.routes), ...Object.values(table.activity)]) {
    for (const file of [].concat(entry)) if (typeof file === "string") named.add(file);
  }
  for (const file of named) assert.ok(pages.includes(file), `${file} is vendored`);
});

test("manifest, file set and static source audit", () => {
  const m = JSON.parse(read("manifest.json"));
  assert.equal(m.manifest_version, 3);
  assert.equal(m.name, "Resonance Reader");
  assert.equal(m.version, "0.1.0");
  assert.deepEqual(m.permissions, ["scripting"]);
  assert.deepEqual(m.host_permissions, [`${ORIGIN}/*`]);
  assert.deepEqual(m.optional_host_permissions, ["https://x.com/*", "https://www.youtube.com/*", "https://myactivity.google.com/*"]);
  for (const key of ["externally_connectable", "web_accessible_resources", "content_security_policy",
    "optional_permissions", "declarative_net_request", "commands", "update_url"]) assert.equal(Object.hasOwn(m, key), false, key);
  assert.deepEqual(m.icons, {16:"icon-16.png",32:"icon-32.png",128:"icon-128.png"});
  assert.equal(m.content_scripts.length, 1);
  assert.deepEqual(m.content_scripts[0].matches, [ROOT_URL]);
  assert.deepEqual(m.content_scripts[0].js, ["shared.js", "bridge.js"]);
  assert.equal(m.content_scripts[0].all_frames, false);
  assert.equal(m.incognito, "not_allowed");
  assert.equal(m.minimum_chrome_version, "106");
  assert.equal(m.options_page, "options.html");
  assert.equal(m.action.default_popup, "popup.html");
  assert.equal(m.background.service_worker, "worker.js");
  // The folder holds exactly the shipped files plus its README, nothing else.
  const present = fs.readdirSync(PKG).filter(name => name !== ".DS_Store").sort();
  assert.deepEqual(present, SHIPPED.concat(["README.md"]).sort());
  for (const name of fs.readdirSync(PKG)) assert.equal(fs.lstatSync(path.join(PKG, name)).isSymbolicLink(), false, name);
  const forbidden = [/\bfetch\s*\(/, /XMLHttpRequest/, /WebSocket/, /EventSource/, /sendBeacon/, /\beval\s*\(/,
    /new Function/, /chrome\.storage/, /chrome\.cookies/, /chrome\.webRequest/, /chrome\.downloads/, /chrome\.history/,
    /chrome\.bookmarks/, /chrome\.identity/, /chrome\.management/, /chrome\.debugger/, /chrome\.tabCapture/,
    /localStorage|sessionStorage|indexedDB/, /document\.cookie/, /world\s*:/, /MAIN/, /executeScript\s*\(\s*\{[^}]*code/,
    /chrome\.tabs\.remove/, /tabs\.update/, /tabs\.executeScript/, /webNavigation/, /\.submit\s*\(/,
    /chrome\.windows\.(?!create\()/, /windows\.(update|remove|get|getAll|getCurrent|getLastFocused|onCreated|onRemoved|onFocusChanged)/,
    /chrome\.tabs\.(query|move|group|highlight|duplicate|reload|goBack|goForward|discard|captureVisibleTab)/,
    /runtime\.reload|runtime\.sendMessage|chrome\.runtime\.onMessage|chrome:\/\//,
    /\bimport\s*[({]|\bimport\s+[\w{*]|\brequire\s*\(|\.\.\//];
  for (const file of SHIPPED.filter(name => name.endsWith(".js"))) {
    const source = read(file);
    for (const pattern of forbidden) assert.equal(pattern.test(source), false, `${file} ${pattern}`);
    if (file !== "options.js") assert.equal(/permissions\.(request|remove)/.test(source), false, `${file} changes permissions`);
  }
  assert.match(read("options.js"), /permissions\.request/);
  assert.match(read("options.js"), /permissions\.remove/);
  // No Facebook, fixture or development-port capability anywhere in what ships.
  for (const file of SHIPPED.filter(name => !name.endsWith('.png'))) {
    const source = read(file);
    assert.equal(/facebook|\bfb[A-Z_]|fixture|4322|live-reader|ResonanceLive\b/i.test(source), false, `${file} dev/Facebook residue`);
  }
  // Only the app origin and the three provider hosts are ever named.
  for (const file of SHIPPED.filter(name => name.endsWith(".js") || name.endsWith(".html"))) {
    for (const host of read(file).match(/https?:\/\/[A-Za-z0-9.:-]+/g) || []) {
      assert.ok(["http://127.0.0.1:4317", "https://x.com", "https://www.youtube.com", "https://myactivity.google.com",
        "https://studio.youtube.com", "https://accounts.google.com"].includes(host), `${file} ${host}`);
    }
  }
  const worker = read("worker.js");
  const reader = read("reader.js");
  assert.equal([...reader.matchAll(/\.click\(\)/g)].length, 3); // expand(), menu open and menu close only
  assert.equal([...reader.matchAll(/scrollIntoView\(/g)].length, 1);
  assert.equal([...worker.matchAll(/tabs\.create\(/g)].length, 2);
  assert.equal([...worker.matchAll(/windows\.create\(/g)].length, 1);
  assert.match(worker, /chrome\.windows\.create\(\{ url, type: "normal", focused: true \}\)/);
  assert.match(worker, /chrome\.tabs\.create\(\{ url, active: true, windowId: job\.readerWindowId \}\)/);
  assert.match(worker, /chrome\.tabs\.create\(\{ url, active: false \}\)/);
  assert.equal([...worker.matchAll(/openOptionsPage\(/g)].length, 1);
  for (const literal of worker.match(/target: \{[^}]*\}/g)) assert.equal(/frameIds/.test(literal) && /documentIds/.test(literal), false, literal);
  assert.equal((worker.match(/importScripts\(/g) || []).length, 1);
  assert.match(worker, /importScripts\("shared\.js"\)/);
  // The pages: exact app link, honest scope, no store link, no generic extension-management URL.
  const options = read("options.html");
  const popup = read("popup.html");
  assert.match(options, /<a id="return-link" href="http:\/\/127\.0\.0\.1:4317\/">/);
  for (const page of [options, popup]) {
    assert.equal(/chromewebstore|microsoftedge\.microsoft\.com|addons\.|chrome:\/\/|edge:\/\/|about:/i.test(page), false);
    assert.match(page, /read-only|Read-only/i);
    assert.match(page, /three/i);
    assert.match(page, /photos and video|photos or video/i);
    assert.match(page, /separate visible/i);
  }
  assert.match(options, /never posts, edits, deletes/i);
  assert.match(options, /whole site/i);
  assert.match(options, /saves what was read/i);
  assert.match(options, /does not sign you in|do not sign you in/i);
  assert.match(popup, /saves nothing/i);
  assert.equal((options.match(/id="allow-/g) || []).length, 2);
  assert.equal((options.match(/id="remove-/g) || []).length, 2);
  assert.equal(/<script[^>]*>[^<]/.test(options + popup), false, "no inline script");
});

test("shared surface has no Facebook or fixture entry points", () => {
  assert.deepEqual(Object.keys(S.SITES), ["live"]);
  assert.deepEqual(Object.keys(S.PERMISSION_ORIGINS), ["x", "youtube"]);
  assert.equal(Object.keys(S).some(key => /fb|facebook|fixture/i.test(key)), false);
  const ok = { type: "START", requestId: "a".repeat(32), provider: "x", mode: "live", handle: "fx_user", channelId: "" };
  assert.ok(S.validateStart(ok));
  for (const bad of [{ ...ok, mode: "fixture" }, { ...ok, provider: "facebook", handle: "fx.user1" }, { ...ok, extra: 1 },
    { ...ok, handle: "messages" }, { ...ok, channelId: CH_B }, { ...ok, provider: "youtube", handle: "ab" }])
    assert.equal(S.validateStart(bad), null);
  assert.equal(S.validDestination("https://www.facebook.com/fx.user1", "live", "facebook"), false);
  assert.equal(S.validDestination("http://127.0.0.1:4317/fixture/x/fx_user", "live", "x"), false);
  assert.equal(S.validDestination("https://x.com/fx_user", "fixture", "x"), false);
  assert.equal(S.validDestination("https://x.com/fx_user", "live", "x"), true);
  assert.equal(S.validSetup({ type: "SETUP", version: "0.1.0", x: true, youtube: null }), true);
  assert.equal(S.validSetup({ type: "SETUP", version: "0.1.0", x: true, youtube: null, facebook: false }), false);
});

// ---- exact root, port and frame ------------------------------------------------
test("bridge attaches only to the exact app root on the exact port in the top frame", async () => {
  for (const [url, top] of [[`${ORIGIN}/`, false], ["http://127.0.0.1:4322/", true], ["http://127.0.0.1:80/", true],
    [`${ORIGIN}/?a=1`, true], [`${ORIGIN}/?a=1#overview`, true], [`${ORIGIN}/index.html`, true], [`${ORIGIN}//`, true],
    ["http://localhost:4317/", true], ["https://127.0.0.1:4317/", true], ["http://127.0.0.1/", true]]) {
    const w = makeWorker();
    const page = makePage(w, { url, top });
    await settleAll(5);
    assert.equal(page.ports.length, 0, `${url} top=${top} must not connect`);
    page.click("res-check-now");
    page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_ok")] });
    page.send("probe", {});
    assert.deepEqual(page.events, [], `${url} top=${top} must stay silent`);
  }
  const { page } = await boot();
  assert.equal(page.ports.length, 1);
  assert.equal(page.ports[0].bridgeSide.name, "resonance-reader:root");
});

test("worker accepts only a connection from the exact root document", async () => {
  const w = makeWorker();
  const attempt = async over => {
    const page = { log: [], tamper: null };
    const pair = link(w, page, "resonance-reader:root", over);
    w.fb.signals.onConnect.emit(pair.workerSide);
    await settleAll(5);
    return pair;
  };
  for (const over of [{ url: `${ORIGIN}/?x=1` }, { url: "http://127.0.0.1:4322/" }, { url: `${ORIGIN}/?x=1#a` }, { origin: "http://127.0.0.1:4322" },
    { frameId: 1 }, { id: "other-extension" }, { documentLifecycle: "prerender" }, { tab: undefined }, { tab: { id: -1 } }]) {
    const pair = await attempt(over);
    assert.equal(pair.workerSide.closed, true, JSON.stringify(over));
    assert.deepEqual(pair.workerSide.posted, []);
  }
  const other = link(w, { log: [] }, "someone-else", {});
  w.fb.signals.onConnect.emit(other.workerSide);
  await settleAll(3);
  assert.equal(other.workerSide.closed, true);
  const good = await attempt({});
  assert.equal(good.workerSide.closed, false);
  assert.equal(good.workerSide.posted[0].type, "READY");
});

// ---- trusted gestures ----------------------------------------------------------
test("only actual trusted clicks on the four known buttons give authority", async () => {
  const { page } = await boot();
  page.clear();
  const plan = () => [req("x", "fx_x_ok")];
  // Untrusted (synthetic) click.
  page.click("res-check-now", false);
  page.send("arm", { batchId: newId(), requests: plan() });
  assert.deepEqual(reasonsOf(page), ["gesture_required"]);
  // No click at all.
  page.send("arm", { batchId: newId(), requests: plan() });
  // A trusted click on an unknown button, on a non-button element, or on the wrong button.
  page.click("some-other-button");
  page.send("arm", { batchId: newId(), requests: plan() });
  page.click("res-check-now", true, "div");
  page.send("arm", { batchId: newId(), requests: plan() });
  page.click("res-reader-options");
  page.send("arm", { batchId: newId(), requests: plan() });
  page.click("res-check-cancel");
  page.send("arm", { batchId: newId(), requests: plan() });
  assert.deepEqual(reasonsOf(page), Array(6).fill("gesture_required"));
  assert.equal(page.of("armed").length, 0);
  // An expired gesture (its task ended) gives nothing.
  page.click("res-check-now");
  page.expireGestures();
  page.send("arm", { batchId: newId(), requests: plan() });
  assert.equal(reasonsOf(page).length, 7);
  // Both check buttons count; a trusted keyboard-generated click is the same event.
  for (const id of ["res-check-now", "res-check-account"]) {
    const batchId = arm(page, plan(), { click: id });
    assert.equal(page.last("armed").batchId, batchId, id);
    page.send("cancel", { batchId });
  }
  // One chance per click: the second arm from the same click is refused.
  const first = newId();
  page.click("res-check-now");
  page.send("arm", { batchId: first, requests: plan() });
  page.send("arm", { batchId: newId(), requests: plan() });
  assert.equal(page.last("rejected").reason, "gesture_required");
  assert.equal(page.last("armed").batchId, first);
  // Start and cancel need no gesture of their own.
  page.expireGestures();
  page.send("cancel", { batchId: first });
  assert.equal(page.last("cancelled").batchId, first);
});

// ---- arm, start, expiry ---------------------------------------------------------
test("arm pins the plan and starts nothing; start runs it once", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok")];
  const batchId = arm(page, requests);
  await settleAll(20);
  assert.deepEqual(page.names(), ["armed"]);
  assert.deepEqual(plain(page.events[0].detail), { batchId });
  assert.equal(w.fb.created.length, 0);
  assert.equal(w.fb.calls.length, 0);
  assert.equal(page.port().bridgeSide.posted.length, 0, "arm sends nothing to the worker");
  assert.equal(page.pending(5000).length, 1, "a five second pin");
  // The page cannot change the plan after arming: its own array is not the pinned one.
  requests[0].handle = "evil";
  requests.push(req("x", "someone_else"));
  page.send("start", { batchId });
  await until(finished(page, batchId), "done");
  const results = resultsOf(page, batchId);
  assert.equal(results.length, 1);
  assert.equal(results[0].identity.handle, "fx_x_ok");
  assert.equal(page.pending(5000).length, 0, "the pin ends at start");
  // Replaying the same start or the same plan starts nothing.
  const before = w.fb.created.length;
  page.send("start", { batchId });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  page.click("res-check-now");
  page.send("arm", { batchId, requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  await settleAll(20);
  assert.equal(w.fb.created.length, before);
});

test("an armed plan expires after five seconds and can no longer start", async () => {
  const { w, page } = await boot();
  page.clear();
  const batchId = arm(page, [req("x", "fx_x_ok")]);
  page.fire(5000);
  page.send("start", { batchId });
  assert.equal(page.last("rejected").reason, "expired");
  await settleAll(20);
  assert.equal(w.fb.created.length, 0);
  assert.equal(page.port().bridgeSide.posted.length, 0);
  assert.equal(page.of("done").length + page.of("result").length + page.of("progress").length, 0);
  // Expiry frees the slot; a new click with new IDs works and the old plan stays dead.
  const second = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, second), "second batch");
  page.send("start", { batchId });
  assert.equal(page.last("rejected").reason, "expired");
  // Unknown, malformed and extra-key starts start nothing either.
  page.send("start", { batchId: newId() });
  assert.equal(page.last("rejected").reason, "unknown_batch");
  page.send("start", { batchId: second, extra: 1 });
  page.send("start", { batchId: "nope" });
  page.send("start", {});
  page.send("start", null);
  assert.deepEqual(reasonsOf(page).slice(-4), Array(4).fill("invalid_input"));
});

test("a begin failure: cancelling an armed plan aborts it and starts nothing", async () => {
  const { w, page } = await boot();
  page.clear();
  const batchId = arm(page, [req("x", "fx_x_ok"), req("youtube", "fx_yt_ok")]);
  page.send("cancel", { batchId }); // no gesture needed: cancel only removes authority
  assert.deepEqual(page.names(), ["armed", "cancelled"]);
  assert.equal(page.pending(5000).length, 0);
  page.send("start", { batchId });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  page.send("cancel", { batchId }); // already over: says nothing more
  await settleAll(20);
  assert.equal(page.of("cancelled").length, 1);
  assert.equal(w.fb.created.length + w.fb.calls.length + w.fb.windowCreates.length, 0);
  assert.equal(page.of("done").length, 0);
});

// ---- plan validation -----------------------------------------------------------
test("a plan is validated whole before anything is accepted", async () => {
  const { w, page } = await boot();
  page.clear();
  const bad = [];
  const many = n => Array.from({ length: n }, (_, i) => req("x", `u${String(i).padStart(2, "0")}`));
  const a = req("x", "fx_x_ok");
  bad.push(
    { batchId: newId(), requests: [] },
    { batchId: newId(), requests: many(21) },
    { batchId: newId() },
    { batchId: "short", requests: [a] },
    { batchId: "A".repeat(32), requests: [a] },
    { batchId: newId(), requests: [a], extra: 1 },
    { batchId: newId(), requests: "x" },
    { batchId: newId(), requests: [{ ...a, extra: 1 }] },
    { batchId: newId(), requests: [{ requestId: a.requestId, provider: "x", handle: "fx_x_ok" }] },
    { batchId: newId(), requests: [{ ...a, mode: "live" }] },
    { batchId: newId(), requests: [{ ...a, provider: "facebook", handle: "fx.user1" }] },
    { batchId: newId(), requests: [{ ...a, provider: "reddit" }] },
    { batchId: newId(), requests: [{ ...a, handle: "https://x.com/fx_x_ok" }] },
    { batchId: newId(), requests: [{ ...a, handle: "@fx_x_ok" }] },
    { batchId: newId(), requests: [{ ...a, handle: "messages" }] },
    { batchId: newId(), requests: [{ ...a, channelId: CH_B }] },
    { batchId: newId(), requests: [{ ...a, requestId: "Z".repeat(32) }] },
    { batchId: newId(), requests: [a, { ...req("x", "other_user"), requestId: a.requestId }] }, // duplicate request ID
    { batchId: newId(), requests: [a, req("x", "FX_X_OK")] }, // duplicate identity, any case
    { batchId: newId(), requests: [req("youtube", "fx_yt_ok", CH_B), req("youtube", "fx_yt_two", CH_B)] }, // duplicate channel
    { batchId: newId(), requests: [req("youtube", "ab")] },
    { batchId: newId(), requests: [req("youtube", "fx_yt_ok", "UCshort")] },
    null, "arm", 7, [],
  );
  const same = newId();
  bad.push({ batchId: same, requests: [{ ...req("x", "fx_x_ok"), requestId: same }] }); // batch ID reused as request ID
  for (const detail of bad) {
    page.click("res-check-now");
    page.send("arm", detail);
  }
  assert.equal(page.of("armed").length, 0);
  assert.equal(reasonsOf(page).length, bad.length);
  assert.ok(reasonsOf(page).every(reason => reason === "invalid_input"), reasonsOf(page).join());
  assert.ok(page.of("rejected").every(r => r.batchId === null || /^[a-f0-9]{32}$/.test(r.batchId)), "a rejection never echoes free input");
  assert.equal(JSON.stringify(page.events).includes("evil"), false);
  assert.equal(page.port().bridgeSide.posted.length, 0);
  // The maximum of twenty unique accounts is accepted, and a mixed plan too.
  const full = arm(page, many(20));
  assert.equal(page.last("armed").batchId, full);
  page.send("cancel", { batchId: full });
  const mixed = arm(page, [req("x", "fx_x_ok"), req("youtube", "fx_yt_ok", "UCfixtureChannel0000001A"), req("youtube", "fx_yt_two")]);
  assert.equal(page.last("armed").batchId, mixed);
  page.send("cancel", { batchId: mixed });
  assert.equal(w.fb.created.length, 0);
});

// ---- running ---------------------------------------------------------------------
test("two accounts are read one after the other and forwarded as validated original results", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok"), req("youtube", "fx_yt_ok")];
  const batchId = await check(page, requests);
  await until(finished(page, batchId), "done");
  const names = page.names();
  assert.equal(names[0], "armed");
  assert.equal(names[names.length - 1], "done");
  assert.equal(page.of("done").length, 1);
  assert.deepEqual(plain(page.last("done")), { batchId });
  assert.equal(names.filter(n => n === "result").length, 2);
  // Strictly sequential: the second START waits for the first terminal result.
  assert.deepEqual(page.log.filter(e => /^(start|result):/.test(e)), [
    `start:${requests[0].requestId}`, `result:${requests[0].requestId}`,
    `start:${requests[1].requestId}`, `result:${requests[1].requestId}`]);
  const results = resultsOf(page, batchId);
  assert.deepEqual(results.map(r => r.requestId), requests.map(r => r.requestId));
  for (const [i, result] of results.entries()) {
    assert.equal(result.type, "RESULT");
    assert.equal(result.mode, "live");
    assert.equal(result.status, "ok");
    assert.equal(result.items.length, 3);
    assert.ok(Array.isArray(result.tabs) && result.tabs.length > 0);
    assert.ok(S.validateResult(result, { requestId: requests[i].requestId, provider: requests[i].provider, mode: "live",
      handle: requests[i].handle, channelId: null }));
  }
  assert.equal(results[0].items[0].text, "A short original statement.");
  assert.equal(results[0].items[0].url, "https://x.com/fx_x_ok/status/5001");
  assert.equal(results[1].identity.channel_id, "UCfixtureChannel0000001A");
  // Progress carries exactly batch, request, provider and a known stage.
  const progress = page.of("progress");
  assert.ok(progress.length >= 6);
  for (const p of progress) {
    assert.deepEqual(Object.keys(p).sort(), ["batchId", "provider", "requestId", "stage"]);
    assert.equal(p.batchId, batchId);
    assert.ok(["permission", "identity", "listing", "source", "recheck", "final_identity"].includes(p.stage));
  }
  assert.ok(progress.some(p => p.provider === "x") && progress.some(p => p.provider === "youtube"));
  assert.deepEqual(plain(page.of("result")[0]), plain({ batchId, result: results[0] }));
  // Owned-tab rules held: nothing closed, updated or windowed except the YouTube window.
  assert.deepEqual(w.fb.removed, []);
  assert.deepEqual(w.fb.updates, []);
  assert.equal(w.fb.windowCreates.length, 1);
  assert.deepEqual(w.fb.windowCalls, []);
  assert.deepEqual(w.fb.api, { requests: 0, reloads: 0 });
  for (const id of w.fb.userTabs.keys()) assert.equal(w.fb.created.includes(id), false);
  // A later batch needs a new click and new IDs and runs on the same connection.
  const again = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, again), "second done");
  assert.equal(w.connects, 1);
});

test("a held, errored or invalid account never carries items and does not stop the others", async () => {
  const { page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_signedout"), req("x", "fx_x_other"), req("x", "fx_x_nopage"), req("x", "fx_x_ok")];
  const batchId = await check(page, requests);
  await until(finished(page, batchId), "done");
  const results = resultsOf(page, batchId);
  assert.deepEqual(results.map(r => r.status), ["held", "held", "error", "ok"]);
  assert.deepEqual(results.slice(0, 2).map(r => r.reason), ["signed_out", "identity_mismatch"]);
  assert.match(results[2].reason, /^[a-z_]+$/);
  for (const r of results.slice(0, 3)) {
    assert.equal(Object.hasOwn(r, "items"), false);
    assert.equal(Object.hasOwn(r, "identity"), false);
    assert.equal(r.mode, "live");
    assert.equal(r.type, "RESULT");
  }
  assert.equal(JSON.stringify(results.slice(0, 2)).includes("Public post"), false);
  assert.equal(results[3].items.length, 3);
  assert.equal(page.of("done").length, 1);
  // A worker reply that is not the original validated shape becomes a plain error, then the batch goes on.
  const second = await boot();
  second.page.clear();
  let tampered = false;
  second.page.tamper = message => {
    if (message.type === "RESULT" && !tampered && message.status === "ok") {
      tampered = true;
      message.identity.handle = "someone_else";
    }
    return message;
  };
  const two = [req("x", "fx_x_ok"), req("x", "fx_x_skips")];
  const id = await check(second.page, two);
  await until(finished(second.page, id), "tampered done");
  const out = resultsOf(second.page, id);
  assert.deepEqual([out[0].status, out[0].reason, out[0].items, out[0].tabs], ["error", "result_invalid", undefined, []]);
  assert.equal(out[1].status, "ok");
  assert.ok(S.validateResult(out[0], { requestId: two[0].requestId, provider: "x", mode: "live", handle: "fx_x_ok", channelId: null }));
});

test("mismatched or unrequested worker output is ignored", async () => {
  const { page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok")];
  const batchId = await check(page, requests);
  // Unsolicited, old and wrong-request messages never reach the page.
  const wrongId = newId();
  page.workerPost({ type: "PROGRESS", requestId: wrongId, provider: "x", mode: "live", stage: "identity" });
  page.workerPost({ type: "RESULT", requestId: wrongId, provider: "x", mode: "live", status: "error", reason: "x", tabs: [] });
  page.workerPost({ type: "PROGRESS", requestId: requests[0].requestId, provider: "x", mode: "live", stage: "bogus" });
  page.workerPost({ type: "PROGRESS", requestId: requests[0].requestId, provider: "youtube", mode: "live", stage: "identity" });
  page.workerPost({ type: "PROGRESS", requestId: requests[0].requestId, provider: "x", mode: "live", stage: "identity", extra: 1 });
  page.workerPost({ type: "SETUP", version: "0.1.0", x: true, youtube: true, facebook: true });
  page.workerPost({ type: "WHATEVER" });
  await until(finished(page, batchId), "done");
  assert.equal(page.of("setup").filter(s => Object.hasOwn(s, "facebook")).length, 0);
  assert.equal(resultsOf(page, batchId).length, 1);
  assert.equal(resultsOf(page, batchId)[0].status, "ok");
  assert.ok(page.of("progress").every(p => p.requestId === requests[0].requestId));
});

// ---- timeouts, cancel, disconnect -----------------------------------------------------
test("per-request watchdog: a silent worker ends the account and the batch with explicit errors", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok"), req("x", "fx_x_skips"), req("youtube", "fx_yt_ok")];
  const batchId = arm(page, requests);
  page.tamper = () => null; // the worker's answers never arrive
  page.send("start", { batchId });
  await settleAll(30);
  assert.equal(page.pending(130000).length, 1);
  assert.equal(page.pending(480000).length, 1);
  assert.deepEqual(page.of("result"), []);
  page.fire(130000);
  const results = resultsOf(page, batchId);
  assert.deepEqual(results.map(r => [r.requestId, r.status, r.reason, r.items, r.tabs]), [
    [requests[0].requestId, "error", "reader_unresponsive", undefined, []],
    [requests[1].requestId, "error", "reader_unresponsive", undefined, []],
    [requests[2].requestId, "error", "reader_unresponsive", undefined, []]]);
  assert.equal(page.of("done").length, 1);
  assert.equal(page.pending(130000).length + page.pending(480000).length, 0);
  assert.equal(page.port().bridgeSide.closed, true, "the silent worker connection is dropped");
  assert.ok(page.port().bridgeSide.posted.some(m => m.type === "CANCEL" && m.requestId === requests[0].requestId));
  // Nothing late is accepted and nothing restarts by itself.
  const connects = w.connects;
  page.tamper = null;
  await settleAll(60);
  assert.equal(w.connects, connects);
  assert.equal(page.of("result").length, 3);
  assert.equal(page.of("disconnected").length, 0);
  // Only a fresh click reconnects.
  const next = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, next), "next");
  assert.equal(w.connects, connects + 1);
  assert.equal(resultsOf(page, next)[0].status, "ok");
});

test("batch timeout: current and remaining accounts end with batch_timeout and no late item is accepted", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok"), req("x", "fx_x_skips"), req("x", "fx_x_recheck")];
  const batchId = arm(page, requests);
  const held = [];
  page.tamper = message => { held.push(message); return null; }; // the first account never finishes
  page.send("start", { batchId });
  await settleAll(30);
  page.fire(480000);
  const results = resultsOf(page, batchId);
  assert.deepEqual(results.map(r => [r.requestId, r.status, r.reason, r.items]), requests.map(r =>
    [r.requestId, "error", "batch_timeout", undefined]));
  assert.ok(results.every(r => r.type === "RESULT" && r.mode === "live" && Array.isArray(r.tabs)));
  assert.deepEqual(page.names().slice(-4), ["result", "result", "result", "done"]);
  assert.ok(page.port().bridgeSide.posted.some(m => m.type === "CANCEL" && m.requestId === requests[0].requestId));
  assert.equal(page.pending(130000).length, 0);
  // A late answer that looks like a complete success changes nothing.
  page.tamper = null;
  const late = { type: "RESULT", requestId: requests[0].requestId, provider: "x", mode: "live", status: "ok", tabs: [101],
    items: [{ provider: "x", source_id: "5001", url: "https://x.com/fx_x_ok/status/5001", author: "fx_x_ok", channel_id: null,
      text: "A short original statement.", date_iso: null, date_label: "Oct 16", date_unknown: true, links_shortened: false }],
    coverage: { limit: 3, accepted: 1, examined: 1, skipped: [], more_available: false, partial: true, notes: [] },
    identity: { handle: "fx_x_ok", channel_id: null } };
  page.workerPost(late);
  await settleAll(40);
  assert.equal(page.of("result").length, 3);
  assert.equal(page.of("done").length, 1);
  // The worker was told to cancel and is free for the next check.
  const next = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, next), "next");
  assert.equal(resultsOf(page, next)[0].status, "ok");
  assert.equal(w.timers.length >= 1, true);
});

test("cancel after the first result discards the queue and never says done", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok"), req("x", "fx_x_skips"), req("youtube", "fx_yt_ok")];
  const batchId = arm(page, requests);
  page.document.addEventListener(`${PREFIX}result`, () => {
    if (page.of("result").length === 1) page.send("cancel", { batchId });
  });
  page.send("start", { batchId });
  await until(() => page.of("cancelled").length > 0, "cancelled");
  await settleAll(60);
  assert.deepEqual(page.names().filter(n => ["result", "cancelled", "done"].includes(n)), ["result", "cancelled"]);
  assert.deepEqual(plain(page.last("cancelled")), { batchId });
  assert.equal(resultsOf(page, batchId)[0].requestId, requests[0].requestId);
  assert.equal(page.log.filter(e => e.startsWith("start:")).length, 1, "the queue was discarded");
  assert.equal(w.fb.windowCreates.length, 0);
  assert.equal(page.pending(130000).length + page.pending(480000).length + page.pending(5000).length, 0);
  // Cancelling again, or a stale batch ID, is silent; a new click starts clean with new IDs.
  page.send("cancel", { batchId });
  assert.equal(page.of("cancelled").length, 1);
  const next = await check(page, [req("x", "fx_x_skips")]);
  await until(finished(page, next), "next");
  assert.equal(page.of("done").length, 1);
});

test("cancel during a read stops the worker's account and a late result is ignored", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("youtube", "fx_yt_ok"), req("x", "fx_x_ok")];
  w.gate.frozen = true;
  const batchId = await check(page, requests);
  await until(() => page.of("progress").length > 0, "first progress");
  await settleAll(10);
  assert.equal(page.of("result").length, 0, "the account is still being read");
  page.send("cancel", { batchId });
  await settleAll(80);
  assert.equal(page.of("cancelled").length, 1);
  assert.equal(page.of("done").length + page.of("result").length, 0, "the worker's cancelled result is not forwarded");
  assert.ok(page.port().bridgeSide.posted.some(m => m.type === "CANCEL" && m.requestId === requests[0].requestId));
  assert.equal(page.log.filter(e => e.startsWith("start:")).length, 1);
  // The worker is free again straight away.
  w.gate.frozen = false;
  const next = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, next), "next");
  assert.equal(resultsOf(page, next)[0].status, "ok");
  assert.equal(w.fb.removed.length, 0);
});

test("a worker restart or disconnect aborts the whole batch, once, with no restart", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("youtube", "fx_yt_ok"), req("x", "fx_x_ok")];
  w.gate.frozen = true;
  const batchId = await check(page, requests);
  await until(() => page.of("progress").length > 0, "progress");
  await settleAll(10);
  w.kill();
  w.gate.frozen = false;
  await settleAll(40);
  assert.deepEqual(plain(page.of("disconnected")), [{ reason: "transport_disconnected", batchId }]);
  assert.equal(page.of("done").length + page.of("cancelled").length + page.of("result").length, 0);
  assert.equal(page.pending(130000).length + page.pending(480000).length, 0);
  assert.equal(w.connects, 1, "nothing reconnects by itself");
  assert.equal(page.log.filter(e => e.startsWith("start:")).length, 1);
  // The aborted batch cannot be resumed; a fresh click reconnects with new IDs.
  page.send("start", { batchId });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  const next = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, next), "next");
  assert.equal(w.connects, 2);
  assert.equal(resultsOf(page, next)[0].status, "ok");
  // An idle disconnect says so with no batch, and an armed plan is lost with the connection.
  page.clear();
  w.kill();
  await settleAll(10);
  assert.deepEqual(plain(page.of("disconnected")), [{ reason: "transport_disconnected", batchId: null }]);
  const armed = arm(page, [req("x", "fx_x_ok")]);
  w.kill();
  await settleAll(10);
  assert.deepEqual(plain(page.last("disconnected")), { reason: "transport_disconnected", batchId: armed });
  page.send("start", { batchId: armed });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  page.send("start", { batchId: newId() });
  assert.equal(page.last("rejected").reason, "unknown_batch");
});

test("connection failures are told apart and need a page reload only when the extension was reloaded", async () => {
  const { w, page } = await boot();
  page.clear();
  w.kill();
  await settleAll(10);
  page.clear();
  page.failConnect = new Error("Extension context invalidated.");
  page.click("res-check-now");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "extension_context_invalidated");
  page.failConnect = new Error("Could not establish connection.");
  page.click("res-check-now");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "transport_unavailable");
  page.send("probe", {});
  assert.deepEqual(plain(page.last("disconnected")), { reason: "transport_unavailable", batchId: null });
  page.failConnect = new Error("Extension context invalidated.");
  page.send("probe", {});
  assert.equal(page.last("disconnected").reason, "extension_context_invalidated");
  assert.equal(page.of("armed").length, 0);
  // A failure to post the first START ends the batch as a disconnect.
  page.failConnect = null;
  const batchId = arm(page, [req("x", "fx_x_ok")]);
  page.failPost = new Error("Extension context invalidated.");
  page.send("start", { batchId });
  assert.deepEqual(plain(page.last("disconnected")), { reason: "extension_context_invalidated", batchId });
  assert.equal(page.of("done").length + page.of("result").length, 0);
});

// ---- busy, replay -------------------------------------------------------------------
test("another arm while armed or running refuses; replayed IDs refuse", async () => {
  const { w, page } = await boot();
  page.clear();
  const first = [req("x", "fx_x_ok")];
  const batchId = arm(page, first);
  page.click("res-check-now");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_skips")] });
  assert.equal(page.last("rejected").reason, "busy");
  page.send("start", { batchId });
  page.click("res-check-account");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_skips")] });
  assert.equal(page.last("rejected").reason, "busy");
  await until(finished(page, batchId), "done");
  assert.equal(resultsOf(page, batchId).length, 1);
  // Starting a running batch's ID again, or arming with any used batch or request ID, is a replay.
  page.send("start", { batchId });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  page.click("res-check-now");
  page.send("arm", { batchId: newId(), requests: [{ ...req("x", "fx_x_skips"), requestId: first[0].requestId }] });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  page.click("res-check-now");
  page.send("arm", { batchId, requests: [req("x", "fx_x_skips")] });
  assert.equal(page.last("rejected").reason, "replayed_batch");
  assert.equal(w.fb.created.filter(() => true).length > 0, true);
  // The worker also refuses a replayed request and a second concurrent account, even from another app tab.
  const worker = page.port().workerSide;
  const rid = newId();
  const start = (id, handle, provider = "x", mode = "live") => worker.onMessage.emit({ type: "START", requestId: id, provider, mode, handle, channelId: "" });
  const before = worker.posted.length;
  start(first[0].requestId, "fx_x_ok");
  await settleAll(5);
  assert.deepEqual(plain(worker.posted.slice(before)).map(m => [m.type, m.status, m.reason]), [["RESULT", "error", "replayed_request"]]);
  w.gate.frozen = true; // park the first account mid-read
  start(rid, "fx_x_ok");
  start(newId(), "fx_x_skips");
  await settleAll(5);
  assert.ok(worker.posted.some(m => m.type === "RESULT" && m.reason === "busy"));
  const other = w.fb.addUserTab(ROOT_URL);
  const second = link(w, { log: [], tamper: null }, "resonance-reader:root", { tab: { id: other, windowId: 1 } });
  w.fb.signals.onConnect.emit(second.workerSide);
  second.workerSide.onMessage.emit({ type: "START", requestId: newId(), provider: "x", mode: "live", handle: "fx_x_skips", channelId: "" });
  await settleAll(5);
  assert.ok(second.workerSide.posted.some(m => m.type === "RESULT" && m.reason === "busy"));
  assert.equal(worker.posted.some(m => m.type === "RESULT" && m.requestId === rid), false, "the first account is still parked");
});

test("the worker starts nothing for Facebook, fixtures, extra keys or any page-supplied target", async () => {
  const { w, page } = await boot();
  page.clear();
  const worker = page.port().workerSide;
  const base = { type: "START", requestId: newId(), provider: "x", mode: "live", handle: "fx_x_ok", channelId: "" };
  for (const bad of [{ ...base, provider: "facebook", handle: "fx.user1" }, { ...base, mode: "fixture" },
    { ...base, url: "https://x.com/fx_x_ok" }, { ...base, selector: "a" }, { ...base, code: "1" }, { ...base, requestId: "short" },
    { ...base, handle: "https://x.com/fx_x_ok" }, { type: "START" }, { type: "NAVIGATE", url: "https://example.com/" }]) {
    worker.onMessage.emit(bad);
  }
  await settleAll(30);
  assert.equal(w.fb.created.length + w.fb.windowCreates.length + w.fb.calls.length, 0);
  assert.deepEqual(worker.posted.filter(m => m.type === "RESULT"), []);
  // The bridge refuses Facebook and fixtures at arm, before the worker is involved.
  for (const requests of [[{ ...req("x", "fx_x_ok"), provider: "facebook" }], [{ ...req("x", "fx_x_ok"), mode: "fixture" }],
    [{ requestId: newId(), provider: "x", handle: "fx_x_ok", channelId: "", url: "https://x.com/" }]]) {
    page.click("res-check-now");
    page.send("arm", { batchId: newId(), requests });
  }
  assert.deepEqual(reasonsOf(page), ["invalid_input", "invalid_input", "invalid_input"]);
  // The injected page reader has no Facebook or unknown commands either.
  const id = await check(page, [req("x", "fx_x_ok")]);
  await until(finished(page, id), "done");
  const tab = [...w.fb.tabs.values()].find(t => t.ctx && t.ctx.ResonanceReaderPage);
  assert.ok(tab);
  for (const cmd of [{ op: "fb_identity", mode: "live", url: "https://www.facebook.com/fx.user1", handle: "fx.user1" },
    { op: "x_profile", mode: "fixture", url: "http://127.0.0.1:4317/fixture/x/fx_x_ok", handle: "fx_x_ok" },
    { op: "eval", mode: "live", url: "https://x.com/fx_x_ok" }]) {
    assert.equal(plain(tab.ctx.ResonanceReaderPage.run(cmd)).state, "bad_command", cmd.op);
  }
});

test("a permission removed or missing ends only the affected account", async () => {
  let { w, page } = await boot({ grants: ["https://x.com"] });
  page.clear();
  const requests = [req("youtube", "fx_yt_ok"), req("x", "fx_x_ok")];
  let batchId = await check(page, requests);
  await until(finished(page, batchId), "done");
  let results = resultsOf(page, batchId);
  assert.deepEqual([results[0].status, results[0].reason, results[0].tabs], ["held", "permission_missing", []]);
  assert.equal(results[1].status, "ok");
  assert.equal(w.fb.created.length > 0 && w.fb.windowCreates.length === 0, true, "the held account opened nothing");
  // Revoked in the middle of a read: that account ends, the batch carries on.
  ({ w, page } = await boot());
  page.clear();
  w.fb.hooks.beforeRead = ({ count, fb }) => { if (count === 4) fb.revoke("https://x.com"); };
  const two = [req("x", "fx_x_ok"), req("youtube", "fx_yt_ok")];
  batchId = await check(page, two);
  await until(finished(page, batchId), "done");
  results = resultsOf(page, batchId);
  assert.deepEqual([results[0].status, results[0].reason], ["error", "permission_lost"]);
  assert.equal(results[1].status, "ok");
  assert.equal(page.of("done").length, 1);
});

test("owned-tab rules still stop a read: a selected or navigated tab ends the account without items", async () => {
  const { w, page } = await boot();
  page.clear();
  w.fb.hooks.beforeRead = ({ tab, cmd, count, fb }) => { if (count === 2 && cmd.op === "x_profile") fb.userSelect(tab.id); };
  const requests = [req("x", "fx_x_ok"), req("x", "fx_x_skips")];
  const batchId = await check(page, requests);
  await until(finished(page, batchId), "done");
  const results = resultsOf(page, batchId);
  assert.equal(results[0].status, "error");
  assert.equal(Object.hasOwn(results[0], "items"), false);
  assert.equal(results[1].status, "ok");
});

test("the app tab leaving its exact root stops the read and any queue", async () => {
  const { w, page } = await boot();
  page.clear();
  const requests = [req("x", "fx_x_ok"), req("x", "fx_x_skips")];
  w.gate.frozen = true;
  const batchId = await check(page, requests);
  await until(() => page.of("progress").length > 0, "progress");
  await settleAll(10);
  assert.equal(page.of("result").length, 0, "the account is still being read");
  page.location.href = `${ORIGIN}/?changed=1`; // the app changed its own address
  w.fb.signals.onUpdated.emit(w.rootTab, { url: `${ORIGIN}/?changed=1` });
  await until(finished(page, batchId), "ended");
  assert.deepEqual(page.names().filter(n => ["done", "cancelled"].includes(n)), ["cancelled"]);
  assert.equal(page.log.filter(e => e.startsWith("start:")).length, 1);
  const results = resultsOf(page, batchId);
  assert.ok(results.every(r => r.status !== "ok"));
  page.click("res-check-now");
  page.send("arm", { batchId: newId(), requests: [req("x", "fx_x_ok")] });
  assert.equal(page.last("rejected").reason, "wrong_page");
});

// ---- setup, version, protocol, probe ---------------------------------------------------
test("setup reports version, protocol and permission state only, and follows permission changes", async () => {
  const w = makeWorker({ grants: [] });
  const page = makePage(w);
  await settleAll(10);
  assert.deepEqual(plain(page.of("ready")), [{ protocol: 1 }]);
  assert.deepEqual(plain(page.of("setup")), [{ version: "0.1.0", protocol: 1, x: false, youtube: false }]);
  assert.deepEqual(Object.keys(page.last("setup")), ["version", "protocol", "x", "youtube"]);
  page.clear();
  w.fb.grant("https://x.com");
  await settleAll(10);
  assert.deepEqual(plain(page.last("setup")), { version: "0.1.0", protocol: 1, x: true, youtube: false });
  w.fb.grant("https://www.youtube.com");
  await settleAll(10);
  assert.equal(page.last("setup").youtube, false, "YouTube needs both origins");
  w.fb.grant("https://myactivity.google.com");
  await settleAll(10);
  assert.equal(page.last("setup").youtube, true);
  w.fb.revoke("https://x.com");
  await settleAll(10);
  assert.deepEqual(plain(page.last("setup")), { version: "0.1.0", protocol: 1, x: false, youtube: true });
  // Rapid changes coalesce: the last answer is the newest state, never an older one.
  page.clear();
  w.fb.grant("https://x.com");
  w.fb.revoke("https://x.com");
  w.fb.grant("https://x.com");
  await settleAll(20);
  assert.equal(page.last("setup").x, true);
  // Failed checks are unavailable (null), never false or allowed; an unreadable version is null.
  const failing = makeWorker({ grants: GRANTS, manifestVersion: new Error("no manifest"),
    prepare(fb) { fb.hooks.contains = ({ origins }) => { if (origins[0] === "https://x.com/*") throw new Error("unavailable"); return undefined; }; } });
  const other = makePage(failing);
  await settleAll(10);
  assert.deepEqual(plain(other.last("setup")), { version: null, protocol: 1, x: null, youtube: true });
});

test("probe is a read-only fresh connection check that opens no provider page", async () => {
  const { w, page } = await boot();
  page.clear();
  page.send("probe", {});
  await settleAll(20);
  assert.deepEqual(plain(page.of("ready")), [{ protocol: 1 }]);
  assert.deepEqual(plain(page.of("setup")), [{ version: "0.1.0", protocol: 1, x: true, youtube: true }]);
  assert.equal(w.fb.created.length + w.fb.windowCreates.length + w.fb.calls.length + w.fb.injections.length, 0);
  assert.equal(w.connects, 1);
  assert.deepEqual(page.port().bridgeSide.posted, [{ type: "PROBE" }]);
  // After a drop, a probe makes one fresh connection and the worker answers it.
  w.kill();
  await settleAll(10);
  page.clear();
  page.send("probe", {});
  await settleAll(20);
  assert.equal(w.connects, 2);
  assert.deepEqual(page.names().filter(n => n !== "disconnected").sort(), ["ready", "setup"]);
  // Probe takes nothing but an empty detail; it also works during a batch without touching it.
  page.clear();
  for (const detail of [{ a: 1 }, null, "x", [], undefined]) page.send("probe", detail);
  assert.deepEqual(reasonsOf(page), Array(5).fill("invalid_input"));
  const batchId = await check(page, [req("x", "fx_x_ok")]);
  page.send("probe", {});
  await until(finished(page, batchId), "done");
  assert.equal(resultsOf(page, batchId)[0].status, "ok");
});

// ---- options ------------------------------------------------------------------------------
test("the options button opens only the extension's own options page, after a trusted click", async () => {
  const { w, page } = await boot();
  page.clear();
  page.send("options", {});
  assert.equal(page.last("rejected").reason, "gesture_required");
  page.click("res-reader-options", false);
  page.send("options", {});
  page.click("res-check-now");
  page.send("options", {});
  assert.equal(reasonsOf(page).length, 3);
  await settleAll(10);
  assert.equal(w.opened.length, 0);
  page.click("res-reader-options");
  page.send("options", { url: "https://example.com/" }); // the page cannot name a target
  assert.equal(page.last("rejected").reason, "invalid_input");
  page.send("options", {});
  await settleAll(10);
  assert.equal(w.opened.length, 1);
  page.send("options", {}); // the gesture was used
  await settleAll(10);
  assert.equal(w.opened.length, 1);
  assert.equal(page.last("rejected").reason, "gesture_required");
  assert.equal(w.fb.tabCreates.length + w.fb.windowCreates.length, 0);
  // The worker honours it only from its current app connection.
  const stale = link(w, { log: [] }, "resonance-reader:root", {});
  w.fb.signals.onConnect.emit(stale.workerSide);
  await settleAll(5);
  const old = page.port().workerSide;
  old.onMessage.emit({ type: "OPEN_OPTIONS" });
  await settleAll(5);
  assert.equal(w.opened.length, 1, "a superseded connection opens nothing");
  stale.workerSide.onMessage.emit({ type: "OPEN_OPTIONS", url: "https://example.com/" });
  await settleAll(5);
  assert.equal(w.opened.length, 1, "extra keys open nothing");
  stale.workerSide.onMessage.emit({ type: "OPEN_OPTIONS" });
  await settleAll(5);
  assert.equal(w.opened.length, 2, "the current connection can");
});

function optionsEnvironment(trusted) {
  const handlers = new Map();
  const labels = new Map();
  const calls = [];
  const granted = { x: false, youtube: false };
  const element = id => {
    if (!labels.has(id)) labels.set(id, { textContent: "", addEventListener(type, fn) { handlers.set(id, fn); } });
    return labels.get(id);
  };
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const key = origins => (same(origins, ["https://x.com/*"]) ? "x"
    : same(origins, ["https://www.youtube.com/*", "https://myactivity.google.com/*"]) ? "youtube" : null);
  const chrome = { permissions: {
    async contains({ origins }) { calls.push(["contains", plain(origins)]); return granted[key(origins)]; },
    async request({ origins }) { calls.push(["request", plain(origins)]); granted[key(origins)] = true; return true; },
    async remove({ origins }) { calls.push(["remove", plain(origins)]); granted[key(origins)] = false; return true; },
  } };
  vm.runInContext(read("options.js"), vm.createContext({ chrome, document: { getElementById: element }, console }));
  return { handlers, labels, calls, trusted };
}
test("options page: Allow and Remove act only on trusted clicks, per provider, with exact origins", async () => {
  const env = optionsEnvironment();
  await settleAll(5);
  assert.equal(env.labels.get("state-x").textContent.startsWith("Not allowed"), true);
  await env.handlers.get("allow-x")({ isTrusted: false });
  assert.deepEqual(env.calls.filter(c => c[0] !== "contains"), []);
  await env.handlers.get("allow-x")({ isTrusted: true });
  assert.deepEqual(env.calls.filter(c => c[0] === "request"), [["request", ["https://x.com/*"]]]);
  assert.equal(env.labels.get("state-x").textContent.startsWith("Allowed"), true);
  assert.equal(env.labels.get("state-youtube").textContent.startsWith("Not allowed"), true);
  await env.handlers.get("allow-youtube")({ isTrusted: true });
  assert.deepEqual(env.calls.filter(c => c[0] === "request")[1], ["request", ["https://www.youtube.com/*", "https://myactivity.google.com/*"]]);
  await env.handlers.get("remove-x")({ isTrusted: true });
  assert.deepEqual(env.calls.filter(c => c[0] === "remove"), [["remove", ["https://x.com/*"]]]);
  assert.equal(env.labels.get("state-x").textContent.startsWith("Not allowed"), true);
  assert.equal(env.labels.get("state-youtube").textContent.startsWith("Allowed"), true);
});

test("popup: two trusted buttons, the exact app root and the own options page", () => {
  const handlers = new Map();
  const calls = [];
  const chrome = { tabs: { create: options => { calls.push(["create", options]); return Promise.resolve(); } },
    runtime: { openOptionsPage: () => { calls.push(["options"]); return Promise.resolve(); } } };
  vm.runInContext(read("popup.js"), vm.createContext({ chrome,
    document: { getElementById: id => ({ addEventListener(type, fn) { handlers.set(id, fn); } }) } }));
  assert.deepEqual([...handlers.keys()].sort(), ["open-app", "open-options"]);
  handlers.get("open-app")({ isTrusted: false });
  handlers.get("open-options")({ isTrusted: false });
  assert.deepEqual(calls, []);
  handlers.get("open-app")({ isTrusted: true });
  handlers.get("open-options")({ isTrusted: true });
  assert.deepEqual(plain(calls), [["create", { url: ROOT_URL }], ["options"]]);
});

// ---- packaging --------------------------------------------------------------------------------
test("build_reader.py makes a deterministic zip and SHA256 manifest in a fresh folder", () => {
  const tool = path.join(ROOT, "tools", "build_reader.py");
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "res-reader-build-"));
  const run = out => childProcess.spawnSync("python3", ["-I", tool, "--out", out], { encoding: "utf8", cwd: base });
  try {
    const a = path.join(base, "a");
    const b = path.join(base, "b");
    const first = run(a);
    assert.equal(first.status, 0, first.stderr + first.stdout);
    assert.equal(run(b).status, 0);
    const files = fs.readdirSync(a).sort();
    assert.deepEqual(files, ["resonance-reader-0.1.0.sha256.json", "resonance-reader-0.1.0.zip"]);
    assert.deepEqual(fs.readFileSync(path.join(a, files[1])), fs.readFileSync(path.join(b, files[1])), "same bytes both times");
    assert.equal(fs.readFileSync(path.join(a, files[0]), "utf8"), fs.readFileSync(path.join(b, files[0]), "utf8"));
    const manifest = JSON.parse(fs.readFileSync(path.join(a, files[0]), "utf8"));
    assert.equal(manifest.name, "Resonance Reader");
    assert.equal(manifest.version, "0.1.0");
    assert.equal(manifest.protocol, 1);
    assert.deepEqual(manifest.files.map(f => f.path), SHIPPED.slice().sort());
    for (const f of manifest.files) {
      assert.equal(f.sha256, require("node:crypto").createHash("sha256").update(fs.readFileSync(path.join(PKG, f.path))).digest("hex"), f.path);
    }
    const zipBytes = fs.readFileSync(path.join(a, files[1]));
    assert.equal(manifest.zip.sha256, require("node:crypto").createHash("sha256").update(zipBytes).digest("hex"));
    assert.equal(manifest.zip.bytes, zipBytes.length);
    const listing = childProcess.spawnSync("python3", ["-I", "-c",
      "import sys,zipfile\nz=zipfile.ZipFile(sys.argv[1])\nfor i in z.infolist(): print(i.filename, i.date_time[0], i.external_attr>>16)",
      path.join(a, files[1])], { encoding: "utf8" });
    assert.equal(listing.status, 0, listing.stderr);
    assert.deepEqual(listing.stdout.trim().split("\n").map(line => line.split(" ")[0]), SHIPPED.slice().sort());
    assert.ok(listing.stdout.trim().split("\n").every(line => line.split(" ")[1] === "1980"));
    // It refuses an output that already exists, so nothing is overwritten.
    const again = run(a);
    assert.notEqual(again.status, 0);
    assert.match(again.stderr, /new|fresh|exist/i);
    // No publishing, network or git is involved.
    const source = fs.readFileSync(tool, "utf8");
    assert.equal(/\bimport (requests|urllib|http|socket|subprocess)|\bfrom (urllib|http|socket|subprocess)/.test(source), false);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

// The production worker + bridge rig, for test_reader_pipeline.cjs. Nothing above
// runs a test by itself; the tests only run when this file is the entry point.
module.exports = { ORIGIN, ROOT_URL, PREFIX, GRANTS, FakeBrowser, signal, S, plain, newId, tick, settleAll, until,
  makeWorker, makePage, boot, link, workerSender, req, arm, check, finished, resultsOf, reasonsOf };

if (require.main === module) (async () => {
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
  console.log(`${tests.length - failed}/${tests.length} browser reader checks passed`);
  process.exit(failed ? 1 : 0);
})();
