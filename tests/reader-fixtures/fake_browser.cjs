"use strict";

// A fake `chrome` for the real worker, backed by the real fixture HTML and the
// real shared.js / reader.js running in a per-tab VM context over the fixture
// DOM adapter. Only the browser (windows, tabs, permissions, scripting) is
// simulated.
//
// Vendored from the development experiment so the reader proofs run from a
// public export. Everything here is invented: the fixture pages, routes and
// accounts are synthetic, the Facebook routes were left behind, and the files
// injected into a page are the production browser-reader/ files.
//
// Windows: window 1 is the user's own window (the harness page and any
// background tabs). Each window has exactly one active tab; creating or
// selecting a tab deactivates the others in that window. windows.create and
// tabs.create may be rigged through hooks (duringCreate, afterCreate, result)
// to deliver activation events before or after create returns.
//
// Deferred DOM (option hiddenRender): a page that LOADS while its tab is not
// the active tab renders without the YouTube own-channel avatar button and the
// comments section (the shape native 0.0.3 observed in a hidden tab). They
// appear in the same document once that tab is selected. This is a model of
// the observation, not of the provider's internals.
// Layout: elements have a sized fixture box and inline-style computed values (see
// dom_adapter.cjs); fixtures set data-fx-rect / style to model hidden or odd sections.
// Live-origin tabs are served as a "projection" of the same fixtures with the
// fixture prefixes rewritten to the provider origins; see the README.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { parseHtml, getComputedStyle } = require("./dom_adapter.cjs");

const spike = __dirname;
const extension = path.join(__dirname, "..", "..", "browser-reader");
const fixtures = path.join(__dirname, "fixtures");
const LOCAL = "http://127.0.0.1:4322";

function signal() {
  const listeners = [];
  return { addListener(l) { listeners.push(l); }, emit(...a) { for (const l of [...listeners]) l(...a); } };
}

function liveToFixturePath(url) {
  const u = new URL(url);
  if (u.origin === "https://x.com") return u.pathname === "/" ? null : "/fixture/x" + u.pathname + u.search;
  if (u.origin === "https://www.youtube.com") return "/fixture/youtube" + u.pathname + u.search;
  if (u.origin === "https://myactivity.google.com") return "/fixture/youtube/myactivity" + u.pathname + u.search;
  if (u.origin === LOCAL) return u.pathname + u.search;
  return null;
}
function project(html, origin) {
  if (origin === LOCAL) return html;
  const linkOrigin = origin === "https://myactivity.google.com" ? "https://www.youtube.com" : origin; // history links point at YouTube
  return html.split(LOCAL + "/fixture/youtube/").join(linkOrigin + "/")
    .split("/fixture/youtube/myactivity/").join("/").split("/fixture/youtube/").join("/")
    .split("/fixture/x/").join("/");
}

class FakeBrowser {
  constructor(options = {}) {
    this.table = JSON.parse(fs.readFileSync(path.join(fixtures, "routes.json"), "utf8"));
    this.scenario = this.table.default_activity;
    this.hits = {};
    this.granted = new Set(options.grants || []);
    this.tabs = new Map();
    this.nextTab = 100;
    this.calls = [];          // every executeScript command, in order
    this.created = [];
    this.removed = [];
    this.stall = new Set();   // URLs that never finish loading
    this.hooks = {};          // beforeRead({tab, cmd, count}) may mutate a tab
    this.readCount = 0;
    this.signals = { onRemoved: signal(), onUpdated: signal(), onActivated: signal(),
      onDetached: signal(), onAttached: signal(), onConnect: signal(), permRemoved: signal(), permAdded: signal() };
    this.userTabs = new Map(); // tabs the "user" owns; the worker must never touch them
    this.overrides = new Map(); // route -> [fixture file name | {html}], consumed per load
    this.injections = [];     // every executeScript call: {tabId, kind, target}
    this.updates = [];        // tabs.update must never be called
    this.windows = new Map([[1, { id: 1, type: "normal", user: true }]]);
    this.nextWindow = 10;
    this.windowCreates = [];  // every windows.create option object, in order
    this.tabCreates = [];     // every tabs.create option object, in order
    this.windowCalls = [];    // windows.update / remove / get must never be called
    this.activation = options.activation || "before"; // when a new active tab's own onActivated arrives: before | after | never
    this.hiddenRender = !!options.hiddenRender;
    this.lateQueue = [];      // callbacks that run on the worker's first browser call after a create returned
    this.manifestVersion = options.manifestVersion === undefined ? "0.0.6" : options.manifestVersion; // getManifest().version
    this.containsCalls = [];  // every permissions.contains argument, in order
    this.api = { requests: 0, reloads: 0 }; // must all stay 0 (see the setup checks)
    this.chrome = this.buildChrome();
  }

  // ---- windows and selection ---------------------------------------------
  // Selecting a tab makes it the only active, highlighted tab of its window
  // and, if its page was held back while hidden, lets that page finish.
  activate(tab) {
    for (const other of this.tabs.values()) {
      if (other.windowId === tab.windowId && other !== tab) { other.active = false; other.highlighted = false; }
    }
    tab.active = true;
    tab.highlighted = true;
    for (const held of (tab.deferred || []).slice().reverse()) held.parent.childNodes.splice(held.index, 0, held.el);
    tab.deferred = null;
  }
  // Shared tail of tabs.create / windows.create. The worker's view is built from
  // fresh state AFTER the hooks ran, so anything a hook did during creation is
  // visible in what create returns.
  async finishCreate(kind, { tab, win, options }) {
    const announce = () => this.signals.onActivated.emit({ tabId: tab.id, windowId: tab.windowId });
    if (tab.active && this.activation === "before") announce();
    if (tab.active && this.activation === "after") this.lateQueue.push(announce);
    if (this.hooks.duringCreate) await this.hooks.duringCreate({ tab, win, fb: this, options, kind });
    if (this.hooks.afterCreate) {
      const hook = this.hooks.afterCreate;
      this.lateQueue.push(() => hook({ tab, win, fb: this, options, kind }));
    }
    const result = kind === "tab" ? this.snapshot(tab)
      : { id: win.id, type: win.type, focused: true, tabs: [this.snapshot(tab)] };
    const rigged = this.hooks.result ? this.hooks.result({ kind, tab, win, result, fb: this }) : undefined;
    return rigged === undefined ? result : rigged;
  }
  windowTabs(windowId) { return Array.from(this.tabs.values()).filter(tab => tab.windowId === windowId); }
  flushLate() { const queue = this.lateQueue.splice(0); for (const fn of queue) fn(); }

  resolve(target) {
    const u = new URL(target, LOCAL);
    const key = u.pathname === "/fixture/youtube/myactivity/page" ? null : u.pathname;
    if (u.pathname === "/fixture/youtube/myactivity/page") {
      if (u.search !== "?page=youtube_comments") return null;
      return this.table.activity[this.scenario] || null;
    }
    let route = key;
    const overrideKey = u.pathname === "/fixture/youtube/watch"
      ? `${u.pathname}?v=${u.searchParams.get("v")}&lc=${u.searchParams.get("lc")}` : u.pathname;
    if (this.overrides.has(overrideKey)) {
      const list = this.overrides.get(overrideKey);
      const n = this.hits["override:" + overrideKey] || 0;
      this.hits["override:" + overrideKey] = n + 1;
      return list[Math.min(n, list.length - 1)];
    }
    if (u.pathname === "/fixture/youtube/watch") {
      const v = u.searchParams.get("v");
      const lc = u.searchParams.get("lc");
      route = `/fixture/youtube/watch?v=${v}&lc=${lc}`;
    } else if (u.search) return null;
    let entry = this.table.routes[route];
    if (Array.isArray(entry)) {
      const n = this.hits[route] || 0;
      this.hits[route] = n + 1;
      entry = entry[Math.min(n, entry.length - 1)];
    }
    if (entry && u.pathname.startsWith("/fixture/youtube/@")) {
      const handle = u.pathname.split("@")[1];
      if (this.table.activity[handle]) this.scenario = handle;
    }
    return entry || null;
  }

  grantedFor(url) {
    try { const o = new URL(url).origin; return o === LOCAL || this.granted.has(o); } catch (_) { return false; }
  }

  load(tab, url) {
    tab.url = url;
    tab.docId = `doc-${this.nextTab}-${(tab.loads = (tab.loads || 0) + 1)}`;
    tab.ctx = null;
    const fixturePath = url === "about:blank" ? null : liveToFixturePath(url);
    const name = fixturePath ? this.resolve(fixturePath) : null;
    tab.status = this.stall.has(url) ? "loading" : "complete";
    if (!name) return;
    const origin = new URL(url).origin;
    const raw = typeof name === "object" ? name.html : fs.readFileSync(path.join(fixtures, name), "utf8");
    const html = project(raw, origin);
    const document = parseHtml(html);
    const u = new URL(url);
    const location = { href: url, origin, pathname: u.pathname, search: u.search };
    const ctx = vm.createContext({ document, location, URL, console, getComputedStyle });
    vm.runInContext("globalThis.window = globalThis; globalThis.top = globalThis;", ctx);
    for (const script of document.querySelectorAll("script[src]")) {
      if (script.getAttribute("src") === "/fx.js") {
        vm.runInContext(fs.readFileSync(path.join(__dirname, "fx.js"), "utf8"), ctx);
      }
    }
    tab.ctx = ctx;
    tab.document = document;
    tab.deferred = null;
    if (this.hiddenRender && !tab.active) {
      // Loaded hidden: the avatar button and comments section are not rendered yet.
      const held = [];
      for (const el of document.querySelectorAll("#avatar-btn, ytd-comments")) {
        const parent = el.parentNode;
        const index = parent.childNodes.indexOf(el);
        if (index < 0) continue;
        parent.childNodes.splice(index, 1);
        held.push({ el, parent, index });
      }
      tab.deferred = held;
    }
  }

  snapshot(tab) {
    const out = { id: tab.id, active: tab.active, highlighted: tab.highlighted, pinned: false,
      status: tab.status, windowId: tab.windowId === undefined ? 1 : tab.windowId };
    if (this.grantedFor(tab.url) || tab.url === "about:blank") out.url = tab.url;
    if (tab.pendingUrl) out.pendingUrl = tab.pendingUrl;
    return out;
  }

  override(route, entries) { this.overrides.set(route, entries); }

  addUserTab(url, windowId = 1) {
    const tab = { id: this.nextTab++, active: false, highlighted: false, url, status: "complete", windowId };
    this.tabs.set(tab.id, tab);
    this.userTabs.set(tab.id, url);
    this.activate(tab);
    return tab.id;
  }
  // A window the user already had open, with one user tab the worker must never touch.
  addUserWindow(url) {
    const id = this.nextWindow++;
    this.windows.set(id, { id, type: "normal", user: true });
    return { windowId: id, tabId: this.addUserTab(url, id) };
  }
  // The user opens a new tab in a window (it becomes the selected one).
  userOpenTab(windowId, url) {
    const id = this.addUserTab(url, windowId);
    this.signals.onActivated.emit({ tabId: id, windowId });
    return id;
  }
  // Selecting the tab that is already selected changes nothing and fires nothing.
  userSelect(id) {
    const tab = this.tabs.get(id);
    if (tab.active) return;
    this.activate(tab);
    this.signals.onActivated.emit({ tabId: id, windowId: tab.windowId });
  }
  userNavigate(id, url) { this.load(this.tabs.get(id), url); this.signals.onUpdated.emit(id, { url }); }
  userClose(id) { this.tabs.delete(id); this.signals.onRemoved.emit(id); }
  // Drag a tab into another window: detach, attach, and it is selected there.
  userMove(id, windowId) {
    const tab = this.tabs.get(id);
    const oldWindowId = tab.windowId;
    tab.windowId = windowId;
    this.signals.onDetached.emit(id, { oldWindowId });
    this.activate(tab);
    this.signals.onAttached.emit(id, { newWindowId: windowId });
  }
  grant(...origins) { origins.forEach(o => this.granted.add(o)); this.signals.permAdded.emit({ origins: origins.map(o => o + "/*") }); }
  revoke(...origins) { origins.forEach(o => this.granted.delete(o)); this.signals.permRemoved.emit({ origins: origins.map(o => o + "/*") }); }

  buildChrome() {
    const fb = this;
    return {
      runtime: { id: "test-extension", onConnect: fb.signals.onConnect,
        getManifest() {
          if (fb.manifestVersion instanceof Error) throw fb.manifestVersion;
          return { version: fb.manifestVersion };
        },
        reload() { fb.api.reloads += 1; throw new Error("runtime.reload must never be used"); } },
      tabs: {
        onRemoved: fb.signals.onRemoved, onUpdated: fb.signals.onUpdated, onActivated: fb.signals.onActivated,
        onDetached: fb.signals.onDetached, onAttached: fb.signals.onAttached,
        async create(options) {
          if (!options || typeof options.url !== "string" || options.url === "about:blank") throw new Error("tabs.create must open the final destination");
          fb.tabCreates.push(JSON.parse(JSON.stringify(options)));
          const background = options.active === false;
          let windowId = 1;
          if (options.windowId !== undefined) {
            if (!fb.windows.has(options.windowId)) throw new Error("No window with id " + options.windowId);
            windowId = options.windowId;
          }
          const tab = { id: fb.nextTab++, active: false, highlighted: false, url: "about:blank", status: "complete", windowId };
          fb.tabs.set(tab.id, tab);
          fb.created.push(tab.id);
          if (!background) fb.activate(tab);
          fb.load(tab, options.url);
          return fb.finishCreate("tab", { tab, options });
        },
        async get(id) {
          fb.flushLate();
          if (!fb.tabs.has(id)) throw new Error("No tab");
          const result = fb.snapshot(fb.tabs.get(id));
          const rigged = fb.hooks.get ? fb.hooks.get({ id, result, fb }) : undefined; // may return an unrelated tab object
          return rigged === undefined ? result : rigged;
        },
        async update(id, options) { fb.updates.push({ id, options }); throw new Error("tabs.update must not be used"); },
        async remove(id) { fb.removed.push(id); fb.tabs.delete(id); },
      },
      windows: {
        async create(options) {
          const keys = options ? Object.keys(options).sort().join() : "";
          if (keys !== "focused,type,url" || options.type !== "normal" || options.focused !== true ||
              typeof options.url !== "string" || options.url === "about:blank") throw new Error("windows.create must open one focused normal window at the final destination");
          fb.windowCreates.push(JSON.parse(JSON.stringify(options)));
          const win = { id: fb.nextWindow++, type: "normal", user: false };
          fb.windows.set(win.id, win);
          const tab = { id: fb.nextTab++, active: false, highlighted: false, url: "about:blank", status: "complete", windowId: win.id };
          fb.tabs.set(tab.id, tab);
          fb.created.push(tab.id);
          fb.activate(tab);
          fb.load(tab, options.url);
          return fb.finishCreate("window", { tab, win, options });
        },
        async update(...args) { fb.windowCalls.push(["update", args]); throw new Error("windows.update must not be used"); },
        async remove(...args) { fb.windowCalls.push(["remove", args]); throw new Error("windows.remove must not be used"); },
        async get(...args) { fb.windowCalls.push(["get", args]); throw new Error("windows.get must not be used"); },
      },
      scripting: {
        async executeScript(injection) {
          fb.flushLate();
          const target = injection.target || {};
          if (target.documentIds !== undefined && target.frameIds !== undefined) throw new Error("Cannot specify both frameIds and documentIds");
          let tab = fb.tabs.get(target.tabId);
          if (target.documentIds !== undefined) {
            if (!Array.isArray(target.documentIds) || target.documentIds.length !== 1) throw new Error("bad documentIds");
            if (!tab || tab.docId !== target.documentIds[0]) throw new Error("No document with the given ID");
          } else if (target.frameIds === undefined || target.frameIds.join() !== "0") throw new Error("frames");
          if (!tab || fb.userTabs.has(tab.id)) throw new Error("worker touched a user tab");
          if (!fb.grantedFor(tab.url)) throw new Error("Cannot access contents of the page");
          if (!tab.ctx) throw new Error("No document");
          if (injection.files) {
            fb.injections.push({ tabId: tab.id, kind: "files", target, windowId: tab.windowId });
            for (const file of injection.files) vm.runInContext(fs.readFileSync(path.join(extension, file), "utf8"), tab.ctx);
            const result = [{ frameId: 0, documentId: tab.docId, result: null }];
            if (fb.hooks.afterFiles) await fb.hooks.afterFiles({ tab, fb, result });
            return result;
          }
          fb.injections.push({ tabId: tab.id, kind: "func", target, windowId: tab.windowId });
          const cmd = JSON.parse(JSON.stringify(injection.args[0]));
          fb.calls.push({ tabId: tab.id, cmd });
          fb.readCount += 1;
          if (fb.hooks.beforeRead) fb.hooks.beforeRead({ tab, cmd, count: fb.readCount, fb });
          const fn = vm.runInContext("(" + injection.func.toString() + ")", tab.ctx);
          const result = await fn(cmd);
          const out = [{ frameId: 0, documentId: tab.docId, result: JSON.parse(JSON.stringify(result)) }];
          if (fb.hooks.afterFunc) fb.hooks.afterFunc({ tab, fb, out });
          return out;
        },
      },
      permissions: {
        onRemoved: fb.signals.permRemoved,
        onAdded: fb.signals.permAdded,
        async contains({ origins }) {
          fb.containsCalls.push(origins.slice());
          if (fb.hooks.contains) {
            const rigged = await fb.hooks.contains({ origins, fb });
            if (rigged !== undefined) return rigged;
          }
          return origins.every(o => fb.granted.has(o.replace(/\/\*$/, "")));
        },
        async request() { fb.api.requests += 1; throw new Error("the worker must never request permissions"); },
      },
    };
  }
}

module.exports = { FakeBrowser, signal, LOCAL, extension, spike, fixtures };
