"use strict";

// SYNTHETIC INTEGRATION PROOF of the reader pipeline, end to end:
//
//   trusted Check click -> static/reader-client.js (the real page client)
//   -> browser-reader/bridge.js -> browser-reader/worker.js (+ shared.js, reader.js)
//   -> invented fixture pages -> back through the bridge -> the client
//   -> the real begin/commit HTTP API of server.make_server() -> durable receipt, corpus, backup.
//
// What is real: the page client, the bridge, the worker, the shared parsers, the page reader,
// and the Resonance server with its SQLite store (a Python subprocess on an ephemeral
// loopback port with a throwaway temporary folder). What is simulated: the browser (tabs,
// windows, permissions, ports, scripting), the DOM, and the provider pages, which are invented
// fixtures in tests/reader-fixtures/. The page keeps its fixed 127.0.0.1:4317 origin; only the
// test's own API requests are mapped to the server's ephemeral port.
//
// This is NOT native Chrome/Edge proof, NOT a store-installed extension, and NOT a read of any
// real provider or account. No real data is read, no network beyond loopback is used, no GUI.
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");
const rig = require("./test_browser_reader.cjs");

const { ORIGIN, ROOT_URL, PREFIX, plain, settleAll, boot } = rig;
const ROOT = path.resolve(__dirname, "..");
const FIXTURE_PAGES = path.join(__dirname, "reader-fixtures", "fixtures");
const CLIENT_SOURCE = fs.readFileSync(path.join(ROOT, "static", "reader-client.js"), "utf8");
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Real I/O (sockets, a subprocess) needs real time, unlike the microtask-driven rig.
async function untilReal(predicate, label, diagnose = () => "", ms = 30000) {
  const end = Date.now() + ms;
  for (;;) {
    if (predicate()) return;
    if (Date.now() > end) throw new Error(`timed out waiting for ${label} ${diagnose()}`);
    await sleep(2);
  }
}

// ---- the real server, in a subprocess ---------------------------------------------------------
// Isolated mode (-I) with the repository root added by argument. The data folder is a fresh
// temporary one that the subprocess deletes in its own finally block when stdin closes.
const SERVER_SCRIPT = [
  "import json, sys, tempfile, threading",
  "from pathlib import Path",
  "sys.path.insert(0, sys.argv[1])",
  "import server",
  "with tempfile.TemporaryDirectory(prefix='resonance-pipeline-') as temp:",
  "    folder = Path(temp) / 'data'",
  "    httpd = server.make_server(folder, 0)",
  "    threading.Thread(target=httpd.serve_forever, daemon=True).start()",
  "    print(json.dumps({'port': httpd.server_address[1], 'directory': str(folder)}), flush=True)",
  "    try:",
  "        sys.stdin.read()",
  "    finally:",
  "        httpd.shutdown()",
  "        httpd.server_close()",
].join("\n");

function startServer() {
  const child = childProcess.spawn("python3", ["-I", "-c", SERVER_SCRIPT, ROOT], { stdio: ["pipe", "pipe", "pipe"] });
  let out = "";
  let err = "";
  let exited = false;
  child.stdin.on("error", () => {}); // the subprocess may already be gone when stdin is closed
  const gone = new Promise(resolve => child.on("exit", code => { exited = true; resolve(code); }));
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`the server did not start in time: ${err}`)), 30000);
    child.stderr.on("data", chunk => { err += chunk; });
    child.stdout.on("data", chunk => {
      out += chunk;
      if (!out.includes("\n")) return;
      clearTimeout(timer);
      try { resolve(JSON.parse(out.split("\n")[0])); } catch (error) { reject(error); }
    });
    child.on("error", error => { clearTimeout(timer); reject(error); });
    child.on("exit", code => { clearTimeout(timer); reject(new Error(`the server exited with ${code}: ${err}`)); });
  });
  return ready.then(info => ({
    port: info.port,
    directory: info.directory,
    async stop() {
      if (!exited) {
        child.stdin.end();
        const killer = setTimeout(() => child.kill("SIGKILL"), 15000);
        await gone;
        clearTimeout(killer);
      }
    },
  }), async error => { child.kill("SIGKILL"); throw error; });
}

// ---- the API adapter: the page's `request`, mapped to the ephemeral port -----------------------
// Same contract as the app's api(): JSON in, JSON out, the workspace token header, an Error with
// .status for a refusal and a status-less Error when the workspace could not be reached.
function makeAdapter(server) {
  const adapter = { log: [], token: "", rules: [] };
  adapter.dropNext = (apiPath, method) => adapter.rules.push({ apiPath, method });
  adapter.request = (apiPath, method = "GET", body = undefined) => new Promise((resolve, reject) => {
    // Only the test's own API calls are ever forwarded.
    if (typeof apiPath !== "string" || !/^\/api\/[A-Za-z0-9/_?=&.%-]*$/.test(apiPath) || apiPath.includes("//") || apiPath.includes("..")) {
      reject(new Error(`the test adapter forwards only /api/ paths: ${String(apiPath).slice(0, 40)}`));
      return;
    }
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body), "utf8");
    const headers = {};
    if (payload) Object.assign(headers, { "Content-Type": "application/json", "Content-Length": String(payload.length) });
    if (method !== "GET") headers.Origin = `http://127.0.0.1:${server.port}`;
    if (adapter.token) headers["X-Resonance-Workspace"] = adapter.token;
    const entry = { method, path: apiPath, status: null, dropped: false };
    adapter.log.push(entry);
    const unreachable = () => new Error("The local workspace is unavailable. Start Resonance on this computer, then try again.");
    const outgoing = http.request({ host: "127.0.0.1", port: server.port, path: apiPath, method, headers, agent: false, timeout: 20000 }, incoming => {
      const chunks = [];
      incoming.on("data", chunk => chunks.push(chunk));
      incoming.on("end", () => {
        entry.status = incoming.statusCode;
        const rule = adapter.rules.findIndex(item => item.apiPath === apiPath && item.method === method);
        if (rule >= 0) { // The server finished its work, but its reply never reaches the page.
          adapter.rules.splice(rule, 1);
          entry.dropped = true;
          reject(unreachable());
          return;
        }
        let result;
        try { result = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { reject(new Error("The workspace returned an unreadable response. Please try again.")); return; }
        if (incoming.statusCode < 200 || incoming.statusCode >= 300) {
          reject(Object.assign(new Error(result.error || "That change could not be saved. Please try again."), { status: incoming.statusCode }));
          return;
        }
        resolve(result);
      });
    });
    outgoing.on("timeout", () => outgoing.destroy(unreachable()));
    outgoing.on("error", () => reject(unreachable()));
    if (payload) outgoing.write(payload);
    outgoing.end();
  });
  adapter.sent = (method, apiPath) => adapter.log.filter(item => item.method === method && item.path === apiPath);
  return adapter;
}

// ---- the real page client in the same fake page ------------------------------------------------
class CustomEvent { constructor(type, options) { this.type = type; this.detail = options.detail; this.target = null; } }

function makeClient(page, adapter, view) {
  const context = vm.createContext({ console, URL });
  vm.runInContext(CLIENT_SOURCE, context, { filename: "static/reader-client.js" });
  const timers = [];
  const memory = new Map();
  const saved = [];
  const client = context.ResonanceReaderClient.create({
    document: page.document,
    location: page.location,
    crypto: webcrypto,
    CustomEvent,
    // Nothing runs by itself: client timers are recorded, never fired.
    setTimeout(fn, ms) { timers.push({ fn, ms, cleared: false }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cleared = true; },
    now: () => Date.now(),
    storage: { getItem: key => (memory.has(key) ? memory.get(key) : null), setItem: (key, value) => memory.set(key, String(value)),
      removeItem: key => memory.delete(key) },
    request: adapter.request,
    getAccounts: () => view.accounts,
    getWorkspaceToken: () => view.token,
    isSamples: () => false,
    onSaved: receipt => { saved.push(receipt); return view.refresh(); },
  });
  client.attach();
  return { client, timers, saved, memory };
}

const ACCOUNT_BASE = { identity_mode: "alias", daring: "balanced", sfw_only: true };
const SPECS = {
  x: { platform: "X", handle: "@fx_x_ok", url: "https://x.com/fx_x_ok", display_name: "Synthetic X", ...ACCOUNT_BASE },
  yt: { platform: "YouTube", handle: "@fx_yt_ok", url: "https://www.youtube.com/@fx_yt_ok", display_name: "Synthetic YouTube", ...ACCOUNT_BASE },
  other: { platform: "Facebook", handle: "moss.example", url: "https://www.facebook.com/moss.example", display_name: "Synthetic other", ...ACCOUNT_BASE },
};

async function makeEnv({ hash = "" } = {}) {
  const servers = [];
  const env = {
    servers,
    async close() { for (const server of servers.splice(0)) await server.stop(); },
    async startServer() { const server = await startServer(); servers.push(server); return server; },
  };
  try {
    env.server = await env.startServer();
    env.adapter = makeAdapter(env.server);
    const url = ROOT_URL + hash;
    const { w, page } = await boot({ rootUrl: url }, { url });
    env.w = w;
    env.page = page;
    const view = { accounts: [], token: "", async refresh() { const state = await env.adapter.request("/api/state"); view.accounts = state.accounts; view.token = state.workspace.token; env.adapter.token = view.token; return state; } };
    env.view = view;
    Object.assign(env, makeClient(page, env.adapter, view));
    await view.refresh();
    env.accounts = {};
    for (const [key, spec] of Object.entries(SPECS)) env.accounts[key] = (await env.adapter.request("/api/accounts", "POST", spec)).account;
    await view.refresh();
    return env;
  } catch (error) {
    await env.close();
    throw error;
  }
}

async function withEnv(options, fn) {
  const env = await makeEnv(options);
  try { await fn(env); } finally { await env.close(); }
}

const describe = env => JSON.stringify({ phase: env.client.check && env.client.check.phase, message: env.client.check && env.client.check.message,
  setup: env.client.setup, events: env.page.names(), ports: env.page.ports.length });

// The probe goes through the real bridge to the real worker and back to the real client.
async function install(env) {
  env.client.probe();
  await untilReal(() => env.client.setup.status === "installed", "the reader to report its setup", () => describe(env), 5000);
  assert.deepEqual([env.client.setup.x, env.client.setup.youtube], [true, true]);
}

// A trusted Check click, then client.start in the same task, before any await.
function check(env, ...keys) {
  env.page.click("res-check-now");
  const started = env.client.start(keys.map(key => env.accounts[key].id));
  assert.equal(started.ok, true, started.message);
  return env.client.check;
}
const phase = (env, ...names) => () => !!env.client.check && names.includes(env.client.check.phase);
async function saved(env, ...keys) {
  const current = check(env, ...keys);
  await untilReal(phase(env, "saved", "failed", "cancelled", "discarded", "unknown"), "the check to finish", () => describe(env), 60000);
  assert.equal(env.client.check, current);
  assert.equal(current.phase, "saved", describe(env));
  const receipt = current.receipt;
  env.client.dismiss();
  return receipt;
}

const counts = async env => { const summary = (await env.adapter.request("/api/state")).reader_check_summary; return { checks: summary.count, sources: summary.source_count, revisions: summary.revision_count }; };
const corpus = async (env, key) => (await env.adapter.request(`/api/corpus?account_id=${env.accounts[key].id}&limit=50`)).sources;
const accountReceipt = (receipt, env, key) => receipt.accounts.find(item => item.account_id === env.accounts[key].id);
const nothingSaved = async env => {
  assert.deepEqual(await counts(env), { checks: 0, sources: 0, revisions: 0 });
  const backup = await env.adapter.request("/api/export");
  assert.deepEqual([backup.reader_checks.length, backup.reader_sources.length, backup.reader_revisions.length], [0, 0, 0]);
  assert.deepEqual(env.adapter.sent("POST", "/api/checks"), [], "no save request was ever sent");
};

// ---- tests ---------------------------------------------------------------------------------------
test("the server is a throwaway temporary one on an ephemeral port and is cleaned up", async () => {
  const server = await startServer();
  const directory = server.directory;
  try {
    assert.notEqual(server.port, 4317);
    assert.ok(server.port > 0 && server.port < 65536);
    const base = fs.realpathSync(os.tmpdir());
    assert.ok(fs.realpathSync(path.dirname(directory)).startsWith(base), "the data folder is under the system temporary folder");
    assert.ok(fs.existsSync(directory));
    const adapter = makeAdapter(server);
    const state = await adapter.request("/api/state");
    assert.match(state.workspace.token, /^[a-f0-9]{32}$/);
    assert.ok(Array.isArray(state.accounts));
  } finally {
    await server.stop();
  }
  assert.equal(fs.existsSync(directory), false, "the temporary data folder was deleted");
});

test("the adapter forwards only the test's own /api/ requests", async () => {
  await withEnv({}, async env => {
    for (const bad of ["http://127.0.0.1:4317/api/state", "https://example.invalid/api/state", "/static/app.js", "//evil/api/state",
      "/api/../static/app.js", "api/state", "/apix", 7, null]) {
      await assert.rejects(() => env.adapter.request(bad), /forwards only/, String(bad));
    }
    assert.equal(env.page.location.origin, ORIGIN, "the page keeps the app's own fixed origin");
    assert.equal(env.page.location.href, ROOT_URL);
    assert.ok(env.adapter.log.every(item => item.path.startsWith("/api/")));
  });
});

test("X, YouTube and an unsupported account go through client, bridge, worker and server to a durable receipt and corpus", async () => {
  await withEnv({}, async env => {
    const { w, page, client } = env;
    await install(env);
    const before = Date.now();
    const receipt = await saved(env, "x", "yt", "other");
    const after = Date.now();
    // The receipt is the server's own, durable and readable again by its ID.
    assert.equal(receipt.origin, "live");
    assert.equal(receipt.reader_version, "0.1.0");
    assert.deepEqual(plain(receipt.totals), { accounts: 3, ok: 2, held: 0, error: 0, unsupported: 1, sources: 6, findings: receipt.totals.findings });
    assert.deepEqual(plain(await env.adapter.request(`/api/checks/${receipt.id}`)), plain(receipt));
    assert.deepEqual(env.saved.map(item => item.id), [receipt.id]);
    // Real current timestamps (the server records them in whole seconds).
    for (const key of ["created_at", "completed_at", "committed_at"]) {
      const at = Date.parse(receipt[key]);
      assert.ok(at >= before - 2000 && at <= after + 2000, `${key} ${receipt[key]} is current`);
    }
    // The real begin/commit calls happened, in order, on the ephemeral port.
    const calls = env.adapter.log.filter(item => ["/api/checks/begin", "/api/checks"].includes(item.path)).map(item => `${item.method} ${item.path} ${item.status}`);
    assert.deepEqual(calls, ["POST /api/checks/begin 201", "POST /api/checks 201"]);
    assert.notEqual(env.server.port, 4317);
    // Per account: X and YouTube read three items each, the other platform is unsupported.
    const x = accountReceipt(receipt, env, "x");
    const yt = accountReceipt(receipt, env, "yt");
    const other = accountReceipt(receipt, env, "other");
    assert.deepEqual([x.status, x.source_count, x.new_source_count], ["ok", 3, 3]);
    assert.deepEqual([yt.status, yt.source_count, yt.new_source_count], ["ok", 3, 3]);
    assert.deepEqual([other.status, other.source_count, other.reason], ["unsupported", 0, "provider_not_supported"]);
    assert.deepEqual(plain(yt.identity), { handle: "fx_yt_ok", channel_id: "UCfixtureChannel0000001A" });
    // The corpus holds exactly what the page reader read from its own fixture posts.
    const xs = await corpus(env, "x");
    assert.equal(xs.length, 3);
    assert.ok(xs.every(source => source.kind === "post" && source.revision_count === 1 && /^https:\/\/x\.com\/fx_x_ok\/status\/\d+$/.test(source.url)));
    const first = xs.find(source => source.url === "https://x.com/fx_x_ok/status/5001");
    assert.equal(first.current_revision.text, "A short original statement.");
    assert.match(first.date_iso, /^2026-10-16T09:30:00(\.000)?Z$/);
    const ys = await corpus(env, "yt");
    assert.equal(ys.length, 3);
    assert.ok(ys.every(source => source.kind === "comment" && source.channel_id === "UCfixtureChannel0000001A"));
    assert.deepEqual(await corpus(env, "other"), []);
    assert.deepEqual(await counts(env), { checks: 1, sources: 6, revisions: 6 });
    // The pipeline really ran the production worker on invented pages and touched nothing else.
    assert.equal(page.of("done").length, 1);
    assert.equal(w.connects, 1);
    assert.deepEqual(w.fb.removed, []);
    assert.deepEqual(w.fb.updates, []);
    assert.deepEqual(w.fb.api, { requests: 0, reloads: 0 });
    for (const id of w.fb.userTabs.keys()) assert.equal(w.fb.created.includes(id), false);
    // Nothing unsaved lingers in the page: no pointer, payload or raw result.
    assert.equal(client.check, null);
    assert.equal(env.memory.size, 0, "the recovery pointer is gone after a save");
  });
});

test("the same content again is deduplicated; changed content appends a revision and keeps the old one", async () => {
  await withEnv({}, async env => {
    await install(env);
    await saved(env, "x", "yt");
    const first = await corpus(env, "x");
    const ids = first.map(source => source.id).sort();
    // Same fixture posts again: nothing new, nothing changed.
    const second = await saved(env, "x", "yt");
    for (const key of ["x", "yt"]) {
      const row = accountReceipt(second, env, key);
      assert.deepEqual([row.source_count, row.new_source_count, row.changed_source_count, row.unchanged_source_count], [3, 0, 0, 3], key);
    }
    assert.deepEqual(await counts(env), { checks: 2, sources: 6, revisions: 6 });
    assert.deepEqual((await corpus(env, "x")).map(source => source.id).sort(), ids);
    assert.ok((await corpus(env, "x")).every(source => source.revision_count === 1));
    // The first post is edited on the provider side (the listing and the post page agree).
    const edit = name => {
      const html = fs.readFileSync(path.join(FIXTURE_PAGES, name), "utf8");
      const edited = html.replace("A short original statement.", "A revised original statement.");
      assert.notEqual(edited, html, `${name} contains the original sentence`);
      return [{ html: edited }];
    };
    env.w.fb.override("/fixture/x/fx_x_ok", edit("x_fx_x_ok.html"));
    env.w.fb.override("/fixture/x/fx_x_ok/status/5001", edit("x_fx_x_ok_5001.html"));
    const third = await saved(env, "x");
    const row = accountReceipt(third, env, "x");
    assert.deepEqual([row.source_count, row.new_source_count, row.changed_source_count, row.unchanged_source_count], [3, 0, 1, 2]);
    const sources = await corpus(env, "x");
    assert.equal(sources.length, 3);
    const edited = sources.find(source => source.url === "https://x.com/fx_x_ok/status/5001");
    assert.equal(edited.revision_count, 2);
    assert.equal(edited.current_revision.text, "A revised original statement.");
    const history = (await env.adapter.request(`/api/corpus/sources/${edited.id}/revisions`)).revisions;
    assert.deepEqual(history.map(item => item.text), ["A revised original statement.", "A short original statement."]);
    assert.deepEqual(await counts(env), { checks: 3, sources: 6, revisions: 7 });
  });
});

test("the format 3 backup of a live history round-trips into a second fresh server as historical receipts", async () => {
  await withEnv({}, async env => {
    await install(env);
    const one = await saved(env, "x", "yt", "other");
    const two = await saved(env, "x", "yt");
    const backup = await env.adapter.request("/api/export");
    assert.equal(backup.schema_version, 3);
    assert.deepEqual(backup.reader_checks.map(item => item.id).sort(), [one.id, two.id].sort());
    assert.deepEqual([backup.reader_sources.length, backup.reader_revisions.length], [6, 6]);
    assert.ok(backup.reader_checks.every(item => item.origin === "live"));
    // A second, separate, fresh server restores it.
    const other = await env.startServer();
    const target = makeAdapter(other);
    target.token = (await target.request("/api/state")).workspace.token;
    const imported = await target.request("/api/import", "POST", backup);
    assert.ok(imported && typeof imported === "object");
    const state = await target.request("/api/state");
    assert.deepEqual(plain(state.reader_check_summary), { count: 2, source_count: 6, revision_count: 6, recent: state.reader_check_summary.recent, limit: state.reader_check_summary.limit });
    const restored = await target.request("/api/export");
    assert.deepEqual(restored.reader_checks.map(item => item.receipt_hash).sort(), backup.reader_checks.map(item => item.receipt_hash).sort());
    assert.deepEqual(new Set(restored.reader_checks.map(item => item.origin)), new Set(["historical"]));
    const byId = list => JSON.stringify(list.slice().sort((a, b) => (a.id < b.id ? -1 : 1)));
    assert.equal(byId(restored.reader_sources), byId(backup.reader_sources));
    assert.equal(byId(restored.reader_revisions), byId(backup.reader_revisions));
    // The restored receipts and corpus are readable on the second server.
    assert.deepEqual(plain((await target.request(`/api/checks/${one.id}`)).totals), plain(one.totals));
    const restoredAccount = restored.accounts.find(item => item.handle === "@fx_x_ok");
    const restoredSources = (await target.request(`/api/corpus?account_id=${restoredAccount.id}&limit=50`)).sources;
    assert.deepEqual(restoredSources.map(item => item.current_revision.text).sort(), (await corpus(env, "x")).map(item => item.current_revision.text).sort());
    // Importing the same thing again changes nothing.
    await target.request("/api/import", "POST", restored);
    assert.deepEqual(plain(await target.request("/api/export")), plain(restored));
    // The first server never downgraded its own live receipts.
    assert.ok((await env.adapter.request("/api/export")).reader_checks.every(item => item.origin === "live"));
  });
});

test("cancel after the first account is read saves no partial result", async () => {
  await withEnv({}, async env => {
    const { w, page, client } = env;
    await install(env);
    // Park the second account once the first has produced its (unsaved) result.
    page.document.addEventListener(`${PREFIX}result`, () => { if (page.of("result").length === 1) w.gate.frozen = true; });
    const current = check(env, "x", "yt");
    await untilReal(() => current.entries[0].state === "read" && current.entries[1].state === "reading", "the first read and the second in progress", () => describe(env));
    assert.equal(current.phase, "reading");
    assert.equal(current.entries[0].result.status, "ok", "the first account's result is held, unsaved, in the page");
    assert.equal(client.cancel(), true);
    assert.equal(current.phase, "cancelled");
    assert.ok(current.entries.every(entry => entry.result === null), "no result survives a discard");
    assert.equal(current.payload, null);
    await untilReal(() => env.adapter.sent("POST", "/api/checks/cancel").some(item => item.status === 200), "the server cancel", () => JSON.stringify(env.adapter.log));
    await assert.rejects(() => env.adapter.request(`/api/checks/${current.checkId}`), error => error.status === 404);
    // A late finish of the parked read cannot save anything either.
    w.gate.frozen = false;
    await settleAll(60);
    await sleep(50);
    await nothingSaved(env);
    assert.equal(page.of("done").length, 0);
    assert.equal(env.memory.size, 0);
  });
});

test("a disconnect of the reader after the first account saves no partial result", async () => {
  await withEnv({}, async env => {
    const { w, page } = env;
    await install(env);
    page.document.addEventListener(`${PREFIX}result`, () => { if (page.of("result").length === 1) w.gate.frozen = true; });
    const current = check(env, "x", "yt");
    await untilReal(() => current.entries[0].state === "read" && current.entries[1].state === "reading", "the first read and the second in progress", () => describe(env));
    assert.equal(current.entries[0].result.status, "ok");
    w.kill(); // the service worker dies mid-batch
    w.gate.frozen = false;
    await untilReal(phase(env, "failed"), "the discard", () => describe(env));
    assert.match(current.message, /disconnected|discarded/i);
    assert.ok(current.entries.every(entry => entry.result === null));
    assert.equal(current.payload, null);
    assert.deepEqual(plain(page.of("disconnected")), [{ reason: "transport_disconnected", batchId: current.batchId }]);
    await untilReal(() => env.adapter.sent("POST", "/api/checks/cancel").some(item => item.status === 200), "the server cancel", () => JSON.stringify(env.adapter.log));
    await settleAll(60);
    await sleep(50);
    await nothingSaved(env);
    assert.equal(page.of("done").length + page.of("result").length, 1, "only the first account ever answered");
    assert.equal(w.connects, 1, "nothing reconnects by itself");
  });
});

test("a save whose HTTP reply is lost reconciles to the one durable receipt without a second save", async () => {
  await withEnv({}, async env => {
    await install(env);
    env.adapter.dropNext("/api/checks", "POST"); // the server saves, but the page never hears back
    const current = check(env, "x", "yt");
    await untilReal(phase(env, "saved", "failed", "cancelled", "discarded"), "the reconciled save", () => describe(env), 60000);
    assert.equal(current.phase, "saved", describe(env));
    assert.match(current.message, /already saved/i, "the page learned of the save by looking it up, not from the lost reply");
    const commits = env.adapter.sent("POST", "/api/checks");
    assert.deepEqual(commits.map(item => [item.status, item.dropped]), [[201, true]], "one save request, whose reply was dropped");
    assert.ok(env.adapter.sent("GET", `/api/checks/${current.checkId}`).some(item => item.status === 200), "the original receipt endpoint answered");
    const durable = await env.adapter.request(`/api/checks/${current.checkId}`);
    assert.deepEqual(plain(current.receipt), plain(durable));
    assert.equal(durable.totals.ok, 2);
    assert.deepEqual(await counts(env), { checks: 1, sources: 6, revisions: 6 });
    assert.equal(env.memory.size, 0);
  });
});

// The app serves from /#overview and /#accounts as well as from /. The pipeline must work from them
// too. This fails (and says why) for as long as the bridge or the worker refuses a URL with a fragment.
for (const hash of ["#overview", "#accounts"]) {
  test(`a check started from the app page at /${hash} reaches the durable receipt`, async () => {
    await withEnv({ hash }, async env => {
      assert.equal(env.page.location.href, ROOT_URL + hash);
      assert.equal(env.page.ports.length, 1, `the bridge attaches to ${ROOT_URL + hash}`);
      await install(env);
      const receipt = await saved(env, "x");
      assert.equal(receipt.totals.ok, 1);
      assert.deepEqual(await counts(env), { checks: 1, sources: 3, revisions: 3 });
    });
  });
}

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
  console.log(`${tests.length - failed}/${tests.length} reader pipeline checks passed (synthetic integration proof: not native, store or provider proof)`);
  process.exit(failed ? 1 : 0);
})();
