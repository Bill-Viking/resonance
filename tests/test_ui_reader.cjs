'use strict';

// Dependency-free checks of the browser-reader client (static/reader-client.js) and the
// reader pieces of the page (static/app.js). They drive the real protocol handler with a
// fake document and a fake server; they are not browser, extension or provider proof.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const PREFIX = 'resonance:reader:';
const hex = char => char.repeat(32);
const CHANNEL = 'UC' + 'a'.repeat(22);
const plain = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const flush = async () => { for (let index = 0; index < 6; index++) await tick(); };
const readerSource = fs.readFileSync(path.join(__dirname, '../static/reader-client.js'), 'utf8');
const discoverySource = fs.readFileSync(path.join(__dirname, '../static/account-discovery.js'), 'utf8');
const appSource = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

const baseAccount = {display_name:'', identity_mode:'named', daring:'careful', sfw_only:true};
const defaultAccounts = () => [
  {...baseAccount, id:'ax', platform:'X', handle:'alice', url:'https://x.com/alice'},
  {...baseAccount, id:'ay', platform:'YouTube', handle:'alicetube', url:'https://www.youtube.com/@alicetube', identity_mode:'alias', daring:'balanced'},
  {...baseAccount, id:'ai', platform:'Instagram', handle:'alice', url:'https://instagram.com/alice'},
];
const SNAPSHOT = ['id', 'platform', 'handle', 'display_name', 'url', 'identity_mode', 'daring', 'sfw_only'];
const snapshot = account => Object.fromEntries(SNAPSHOT.map(key => [key, account[key] ?? null]));

function loadReader() {
  const context = vm.createContext({URL, console});
  vm.runInContext(readerSource, context);
  return context.ResonanceReaderClient;
}

function harness(options = {}) {
  const Reader = loadReader();
  const listeners = new Map();
  const log = [];
  const store = new Map();
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  const h = {Reader, log, requests, store, timers, accounts: options.accounts || defaultAccounts(), samples: false, token: 'workspace-one', autoArm: true, now: Date.parse('2026-11-01T10:00:05Z')};
  const doc = {
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push(fn); },
    dispatchEvent(event) {
      const name = event.type.slice(PREFIX.length);
      log.push({kind:'event', name, detail:plain(event.detail)});
      if (name === 'arm' && h.autoArm) h.emit('armed', {batchId: event.detail.batchId});
      return true;
    },
  };
  h.emit = (name, detail) => { for (const fn of listeners.get(PREFIX + name) || []) fn({target: doc, detail}); };
  class FakeCustomEvent { constructor(type, init) { this.type = type; this.detail = init.detail; } }
  h.respond = (url, method, body) => {
    if (url === '/api/checks/begin') return beginJob(h.accounts, body);
    if (url === '/api/checks/cancel') return {check_id: body.check_id, pending: false};
    if (url === '/api/checks') return {receipt: receiptFor(body.check_id), replayed: false};
    throw new Error('Unexpected request ' + method + ' ' + url);
  };
  const request = (url, method = 'GET', body) => {
    log.push({kind:'request', path: url, method});
    requests.push({path: url, method, json: body === undefined ? undefined : JSON.stringify(body), body: body === undefined ? undefined : plain(body)});
    return Promise.resolve().then(() => h.respond(url, method, body));
  };
  h.client = Reader.create({
    document: doc, location: {origin: 'http://127.0.0.1:4317'}, crypto: {getRandomValues(array) { for (let index = 0; index < array.length; index++) array[index] = Math.floor(Math.random() * 256); return array; }},
    CustomEvent: FakeCustomEvent, setTimeout: (fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; }, clearTimeout: id => { timers.delete(id); },
    now: () => h.now, storage: {getItem: key => store.has(key) ? store.get(key) : null, setItem: (key, value) => store.set(key, String(value)), removeItem: key => store.delete(key)},
    request, getAccounts: () => h.accounts, getWorkspaceToken: () => h.token, isSamples: () => h.samples,
  });
  h.client.attach();
  h.setupReady = (detail = {}) => h.emit('setup', {version: '0.1.0', protocol: 1, x: true, youtube: true, ...detail});
  h.events = name => log.filter(entry => entry.kind === 'event' && entry.name === name);
  h.calls = (url, method) => requests.filter(item => item.path === url && (!method || item.method === method));
  h.arm = () => h.events('arm')[0].detail;
  h.batchId = () => h.arm().batchId;
  h.result = (index, extra = {}) => {
    const item = h.arm().requests[index];
    return {type: 'RESULT', requestId: item.requestId, provider: item.provider, mode: 'live', status: 'ok', tabs: [1], identity: {handle: item.handle, channel_id: item.channelId}, items: [], coverage: {}, ...extra};
  };
  h.deliver = (index, extra) => h.emit('result', {batchId: h.batchId(), result: h.result(index, extra)});
  return h;
}

function beginJob(accounts, body, checkChar = '1') {
  return {check_id: hex(checkChar), created_at: '2026-11-01T10:00:00Z', expires_at: '2026-11-01T10:10:00Z', accounts: body.account_ids.map(id => {
    const account = accounts.find(item => item.id === id);
    const identity = loadReaderOnce().deriveIdentity(account);
    return {account: snapshot(account), provider: identity.provider,
      expected: identity.provider && !identity.problem ? {handle: identity.handle ? identity.handle.toLowerCase() : null, channel_id: identity.channelId} : null,
      expected_problem: identity.problem ? 'The saved handle is not valid.' : null};
  })};
}
let readerOnce;
const loadReaderOnce = () => readerOnce || (readerOnce = loadReader());
function receiptFor(checkId) {
  return {id: checkId, origin: 'live', reader_version: '0.1.0', created_at: '2026-11-01T10:00:00Z', completed_at: '2026-11-01T10:00:05Z', committed_at: '2026-11-01T10:00:06Z',
    statement: 'Reader-reported evidence.', scope_note: 'At most 3 recent items.', accounts: [], totals: {accounts: 3, ok: 2, held: 0, error: 0, unsupported: 1, sources: 4, findings: 1}};
}

// A started, running check with X and YouTube planned and Instagram unsupported.
async function running(h) {
  h.setupReady();
  const outcome = h.client.start(['ax', 'ay', 'ai']);
  assert.equal(outcome.ok, true);
  await flush();
  assert.equal(h.client.check.phase, 'reading');
  return outcome;
}
async function finishReading(h) {
  h.deliver(0);
  h.deliver(1);
  h.emit('done', {batchId: h.batchId()});
}

const tests = [];
const test = (name, fn) => tests.push({name, fn});

test('arm is dispatched in the click task before the begin request, with an exact plan', () => {
  const h = harness();
  h.setupReady();
  const outcome = h.client.start(['ax', 'ay', 'ai']);
  assert.equal(outcome.ok, true);
  // No await has happened yet: both calls are already in the log, in this order.
  assert.deepEqual(h.log.filter(entry => entry.name === 'arm' || entry.path === '/api/checks/begin').map(entry => entry.name || entry.path), ['arm', '/api/checks/begin']);
  const arm = h.arm();
  assert.deepEqual(Object.keys(arm).sort(), ['batchId', 'requests']);
  assert.match(arm.batchId, /^[a-f0-9]{32}$/);
  assert.equal(arm.requests.length, 2, 'Instagram is never sent to the reader');
  for (const item of arm.requests) {
    assert.deepEqual(Object.keys(item).sort(), ['channelId', 'handle', 'provider', 'requestId']);
    assert.match(item.requestId, /^[a-f0-9]{32}$/);
    assert.ok(!item.handle.startsWith('@'));
  }
  assert.deepEqual(arm.requests.map(item => [item.provider, item.handle, item.channelId]), [['x', 'alice', null], ['youtube', 'alicetube', null]]);
  assert.equal(new Set([arm.batchId, ...arm.requests.map(item => item.requestId)]).size, 3);
  assert.equal(h.client.check.phase, 'beginning');
  assert.equal(h.events('start').length, 0, 'start waits for the server-issued check');
  assert.deepEqual(h.calls('/api/checks/begin')[0].body, {account_ids: ['ax', 'ay', 'ai']});
});

test('a full check reads, saves exactly once and clears the recovery pointer', async () => {
  const h = harness();
  const inner = h.respond;
  let phaseAtPost = null;
  h.respond = (url, method, body) => { if (url === '/api/checks' && method === 'POST') phaseAtPost = h.client.check.phase; return inner(url, method, body); };
  await running(h);
  assert.equal(h.events('start').length, 1);
  assert.equal(h.events('start')[0].detail.batchId, h.batchId());
  assert.deepEqual(JSON.parse(h.store.get('resonance:reader:pending-check')), {check_id: hex('1'), workspace: 'workspace-one'}, 'only an ID and workspace token are stored');
  h.emit('progress', {batchId: h.batchId(), requestId: h.arm().requests[0].requestId, provider: 'x', stage: 'listing'});
  assert.equal(h.client.check.entries[0].state, 'reading');
  await finishReading(h);
  assert.equal(phaseAtPost, 'saving', 'reading and saving are different phases');
  const save = h.calls('/api/checks', 'POST')[0];
  assert.equal(save.body.check_id, hex('1'));
  assert.equal(save.body.reader_version, '0.1.0');
  assert.deepEqual(save.body.accounts.map(row => [row.account_id, row.status]), [['ax', 'ok'], ['ay', 'ok'], ['ai', 'unsupported']]);
  assert.deepEqual(save.body.accounts[2].result, {reason: 'provider_not_supported'});
  assert.equal(save.body.accounts[0].result.requestId, h.arm().requests[0].requestId);
  await flush();
  assert.equal(h.client.check.phase, 'saved');
  assert.equal(h.client.check.receipt.id, hex('1'));
  assert.equal(h.store.size, 0, 'the pointer is cleared once the receipt is confirmed');
  assert.equal(h.calls('/api/checks', 'POST').length, 1);
});

test('an account edit during begin refuses the stale snapshot and cancels both sides', async () => {
  const h = harness();
  h.setupReady();
  h.client.start(['ax', 'ay']);
  h.accounts[0].daring = 'bold';
  await flush();
  assert.equal(h.client.check.phase, 'failed');
  assert.equal(h.events('start').length, 0, 'nothing is read from a stale selection');
  assert.equal(h.events('cancel').length, 1);
  assert.equal(h.events('cancel')[0].detail.batchId, h.batchId());
  assert.equal(h.calls('/api/checks/cancel').length, 1, 'the issued check is cancelled on the server');
});

test('a server snapshot that disagrees about boundaries or identity is refused', async () => {
  for (const mutate of [job => { job.accounts[0].account.sfw_only = false; }, job => { job.accounts[0].expected.handle = 'someone_else'; }, job => { job.accounts[0].expected_problem = 'bad'; }, job => { job.accounts.pop(); }]) {
    const h = harness();
    h.respond = (url, method, body) => { if (url === '/api/checks/begin') { const job = beginJob(h.accounts, body); mutate(job); return job; } return {}; };
    h.setupReady();
    h.client.start(['ax', 'ay']);
    await flush();
    assert.equal(h.client.check.phase, 'failed');
    assert.equal(h.events('start').length, 0);
    assert.equal(h.events('cancel').length, 1);
  }
});

test('an account changing while reading stops the check before any save', async () => {
  const h = harness();
  await running(h);
  h.accounts[1].url = 'https://www.youtube.com/@elsewhere';
  h.client.revalidate();
  assert.equal(h.client.check.phase, 'failed');
  assert.equal(h.events('cancel').length, 1);
  h.deliver(0);
  h.emit('done', {batchId: h.batchId()});
  await flush();
  assert.equal(h.calls('/api/checks', 'POST').length, 0, 'late bridge events cannot resurrect a stopped check');
});

test('late, stale and malformed events are ignored', async () => {
  const h = harness();
  await running(h);
  const [first] = h.arm().requests;
  h.emit('result', {batchId: hex('9'), result: h.result(0)});                    // wrong batch
  h.emit('result', {batchId: h.batchId(), result: h.result(1)});                 // not the request being read
  h.emit('result', {batchId: h.batchId(), result: h.result(0, {provider: 'youtube'})}); // wrong provider
  h.emit('result', {batchId: h.batchId(), result: h.result(0, {mode: 'sample'})});
  h.emit('result', {batchId: h.batchId(), result: h.result(0, {requestId: hex('7')})});
  h.emit('result', {batchId: h.batchId(), result: h.result(0, {status: 'held'})});   // held needs a reason token
  h.emit('result', {batchId: h.batchId(), result: h.result(0), extra: true});
  h.emit('progress', {batchId: h.batchId(), requestId: first.requestId, provider: 'x', stage: 'rm -rf'});
  assert.equal(h.client.check.entries.filter(entry => entry.state === 'read').length, 0);
  h.deliver(0);
  assert.equal(h.client.check.entries[0].state, 'read');
  h.deliver(0); // a duplicate of the same result no longer matches the request being read
  assert.equal(h.client.check.entries[1].state, 'queued');
  h.emit('done', {batchId: h.batchId()});
  await flush();
  assert.equal(h.client.check.phase, 'failed', 'done before every account answered discards the batch');
  assert.equal(h.calls('/api/checks', 'POST').length, 0, 'partial delivery is never saved');
});

test('cancelling discards everything and a new click never receives the old result', async () => {
  const h = harness();
  await running(h);
  const oldBatch = h.batchId();
  h.deliver(0);
  assert.equal(h.client.cancel(), true);
  assert.equal(h.client.check.phase, 'cancelled');
  assert.equal(h.events('cancel')[0].detail.batchId, oldBatch);
  assert.equal(h.calls('/api/checks/cancel').length, 0, 'the server cancel is queued behind the click task, never inside it');
  await flush();
  assert.equal(h.calls('/api/checks/cancel').length, 1);
  assert.equal(h.store.size, 0);
  assert.ok(h.client.check.entries.every(entry => entry.result === null));
  h.deliver(1);
  h.emit('done', {batchId: oldBatch});
  await flush();
  assert.equal(h.calls('/api/checks', 'POST').length, 0);
  h.client.dismiss();
  h.respond = (url, method, body) => url === '/api/checks/begin' ? beginJob(h.accounts, body, '2') : {};
  assert.equal(h.client.start(['ax']).ok, true);
  await flush();
  const secondBatch = h.events('arm')[1].detail.batchId;
  assert.notEqual(secondBatch, oldBatch);
  h.emit('result', {batchId: oldBatch, result: h.result(0)});
  assert.equal(h.client.check.entries[0].state, 'queued', 'the old result is not accepted by the new click');
});

test('a bridge cancelled event or disconnect discards the whole unsaved batch', async () => {
  const h = harness();
  await running(h);
  h.deliver(0);
  h.emit('disconnected', {reason: 'transport_disconnected', batchId: h.batchId()});
  assert.equal(h.client.check.phase, 'failed');
  assert.equal(h.client.setup.status, 'offline');
  await flush();
  assert.equal(h.calls('/api/checks/cancel').length, 1);
  h.deliver(1);
  h.emit('done', {batchId: h.batchId()});
  await flush();
  assert.equal(h.calls('/api/checks', 'POST').length, 0);
  const g = harness();
  await running(g);
  g.emit('cancelled', {batchId: g.batchId()});
  assert.equal(g.client.check.phase, 'cancelled');
  assert.equal(g.events('cancel').length, 0, 'the bridge already cancelled; the page does not repeat it');
  await flush();
  assert.equal(g.calls('/api/checks/cancel').length, 1);
});

test('request and batch timers end a silent reader without saving', async () => {
  const h = harness();
  await running(h);
  assert.ok([...h.timers.values()].some(timer => timer.ms >= 480000 && timer.ms < 500000), 'an eight-minute batch timer exists');
  const request130 = [...h.timers.values()].find(timer => timer.ms >= 130000 && timer.ms < 140000);
  assert.ok(request130, 'a per-request timer near 130 seconds exists');
  request130.fn();
  assert.equal(h.client.check.phase, 'failed');
  assert.equal(h.events('cancel').length, 1);
  assert.equal(h.calls('/api/checks', 'POST').length, 0);
});

test('an unexpected reader version or protocol blocks checks', () => {
  for (const detail of [{version: '0.0.6'}, {version: null}, {version: '0.1.1'}]) {
    const h = harness();
    h.setupReady(detail);
    assert.equal(h.client.setup.status, 'mismatch');
    const outcome = h.client.start(['ax']);
    assert.equal(outcome.ok, false);
    assert.equal(outcome.setup, true);
    assert.equal(h.events('arm').length, 0);
    assert.equal(h.calls('/api/checks/begin').length, 0);
  }
  const bad = harness();
  bad.emit('setup', {version: '0.1.0', protocol: 2, x: true, youtube: true});
  assert.equal(bad.client.setup.status, 'mismatch');
  bad.emit('setup', {version: '0.1.0', protocol: 1, x: true, youtube: true, extra: 1});
  assert.equal(bad.client.setup.status, 'mismatch');
  const ok = harness();
  ok.setupReady({x: false});
  assert.equal(ok.client.setup.status, 'installed');
  assert.equal(ok.client.providerState('x'), 'permission_needed');
});

test('detection: absent, offline and wrong page are different states', () => {
  const h = harness();
  h.client.probe();
  assert.equal(h.client.setup.status, 'checking');
  assert.equal(h.events('probe').length, 1);
  [...h.timers.values()].find(timer => timer.ms === 1500).fn();
  assert.equal(h.client.setup.status, 'absent');
  h.emit('ready', {protocol: 1});
  assert.equal(h.client.setup.status, 'connected');
  h.emit('disconnected', {reason: 'transport_disconnected', batchId: null});
  assert.equal(h.client.setup.status, 'offline');
  assert.equal(h.client.start(['ax']).ok, false);
});

test('unsupported-only and unidentifiable selections never reach the reader', async () => {
  const h = harness();
  h.setupReady();
  assert.equal(h.client.start(['ai']).ok, false);
  assert.equal(h.events('arm').length, 0);
  assert.equal(h.calls('/api/checks/begin').length, 0);
  h.accounts.push({...baseAccount, id: 'au', platform: 'YouTube', handle: CHANNEL, url: ''});
  h.accounts.push({...baseAccount, id: 'ab', platform: 'X', handle: 'bad handle!', url: ''});
  assert.equal(h.client.start(['au', 'ab']).ok, false, 'a channel-only record and a malformed handle are held, not guessed');
  assert.equal(h.events('arm').length, 0);
  assert.equal(h.client.start(['ax', 'au', 'ab', 'ai']).ok, true);
  assert.equal(h.arm().requests.length, 1, 'only the clearly identified account is planned');
  assert.equal(h.arm().requests[0].handle, 'alice');
});

test('held and unsupported accounts get explicit terminal entries in the save', async () => {
  const h = harness();
  h.accounts.push({...baseAccount, id: 'au', platform: 'YouTube', handle: CHANNEL, url: ''});
  h.setupReady();
  h.client.start(['ax', 'au', 'ai']);
  await flush();
  h.deliver(0);
  h.emit('done', {batchId: h.batchId()});
  await flush();
  const rows = h.calls('/api/checks', 'POST')[0].body.accounts;
  assert.deepEqual(rows.map(row => [row.account_id, row.status]), [['ax', 'ok'], ['au', 'held'], ['ai', 'unsupported']]);
  assert.equal(rows[1].result.reason, 'identity_unconfirmed');
  assert.equal(rows[1].result.type, 'RESULT');
  assert.match(rows[1].result.requestId, /^[a-f0-9]{32}$/);
});

test('sample mode never reads, begins or opens reader settings', () => {
  const h = harness();
  h.setupReady();
  h.samples = true;
  assert.equal(h.client.start(['ax']).ok, false);
  assert.equal(h.client.openOptions(), false);
  assert.equal(h.client.canStart(), false);
  assert.equal(h.events('arm').length + h.events('options').length, 0);
  assert.equal(h.requests.length, 0);
});

test('a lost save response resolves through the original receipt before any retry', async () => {
  const h = harness();
  await running(h);
  let posts = 0;
  h.respond = (url, method, body) => {
    if (url === '/api/checks' && method === 'POST') { posts++; if (posts === 1) throw new Error('The local workspace is unavailable.'); return {receipt: receiptFor(body.check_id), replayed: true}; }
    if (url === '/api/checks/' + hex('1')) return {status: 'pending'};
    return {};
  };
  await finishReading(h);
  await flush();
  const check = h.client.check;
  assert.equal(check.phase, 'unknown');
  assert.equal(check.canRetry, true);
  assert.ok(/unknown/i.test(check.message) && !/^Saved\b/.test(check.message), 'no success claim');
  assert.equal(h.calls('/api/checks/' + hex('1'), 'GET').length, 1, 'the receipt endpoint is read first');
  assert.equal(h.client.retrySave(), true);
  await flush();
  assert.equal(h.calls('/api/checks', 'POST').length, 2);
  assert.equal(h.calls('/api/checks', 'POST')[1].json, h.calls('/api/checks', 'POST')[0].json, 'retry sends byte-identical content');
  assert.equal(h.client.check.phase, 'saved');
  assert.equal(h.client.check.receipt.id, hex('1'));
  assert.equal(h.store.size, 0);
});

test('an existing receipt is accepted without a second save; 404 discards; network keeps it unknown', async () => {
  const cases = [
    {lookup: () => receiptFor(hex('1')), phase: 'saved', retry: false},
    {lookup: () => { throw Object.assign(new Error('Check not found.'), {status: 404}); }, phase: 'discarded', retry: false},
    {lookup: () => { throw new Error('The local workspace is unavailable.'); }, phase: 'unknown', retry: false},
  ];
  for (const item of cases) {
    const h = harness();
    await running(h);
    h.respond = (url, method) => {
      if (url === '/api/checks' && method === 'POST') throw new Error('The local workspace is unavailable.');
      if (url === '/api/checks/' + hex('1')) return item.lookup();
      return {};
    };
    await finishReading(h);
    await flush();
    assert.equal(h.client.check.phase, item.phase);
    assert.equal(h.client.check.canRetry, false);
    assert.equal(h.calls('/api/checks', 'POST').length, 1, 'no duplicate attempt without a pending receipt answer');
    if (item.phase === 'discarded') { assert.equal(h.store.size, 0); assert.ok(h.client.check.payload === null); }
    if (item.phase === 'unknown') { assert.ok(h.client.check.payload, 'the payload stays in memory for a later safe retry'); assert.ok(/unknown/i.test(h.client.check.message)); }
  }
});

test('an expired or refused save never claims success and asks for a fresh check', async () => {
  const h = harness();
  await running(h);
  h.respond = (url, method) => {
    if (url === '/api/checks' && method === 'POST') throw Object.assign(new Error('This check is not waiting or has expired. Start a new check.'), {status: 400});
    if (url === '/api/checks/' + hex('1')) return {status: 'pending'};
    return {};
  };
  await finishReading(h);
  await flush();
  assert.equal(h.client.check.phase, 'failed');
  assert.match(h.client.check.message, /Start a new check/);
  assert.equal(h.client.retrySave(), false);
  assert.equal(h.calls('/api/checks/cancel').length, 1);
});

test('cancel is refused once the final save has begun', async () => {
  const h = harness();
  await running(h);
  h.respond = () => new Promise(() => {});
  await finishReading(h);
  assert.equal(h.client.check.phase, 'saving');
  assert.equal(h.client.cancel(), false);
  assert.equal(h.events('cancel').length, 0);
});

test('reload recovery looks the check up and never resumes reading', async () => {
  const pointer = JSON.stringify({check_id: hex('3'), workspace: 'workspace-one'});
  const saved = harness();
  saved.store.set('resonance:reader:pending-check', pointer);
  saved.respond = url => url === '/api/checks/' + hex('3') ? receiptFor(hex('3')) : {};
  assert.equal(await saved.client.recover(), 'saved');
  assert.equal(saved.client.check.phase, 'saved');
  assert.equal(saved.store.size, 0);

  const pending = harness();
  pending.store.set('resonance:reader:pending-check', pointer);
  pending.respond = (url, method) => url === '/api/checks/' + hex('3') ? {status: 'pending'} : {check_id: hex('3'), pending: false};
  assert.equal(await pending.client.recover(), 'discarded');
  assert.equal(pending.client.check.phase, 'discarded');
  assert.equal(pending.calls('/api/checks/cancel').length, 1, 'a payload-less pending check is cancelled');
  assert.equal(pending.events('arm').length + pending.events('start').length, 0);
  assert.equal(pending.store.size, 0);

  const other = harness();
  other.store.set('resonance:reader:pending-check', JSON.stringify({check_id: hex('3'), workspace: 'workspace-two'}));
  assert.equal(await other.client.recover(), null);
  assert.equal(other.requests.length, 0);
  assert.equal(other.store.size, 0, 'a pointer from another workspace is dropped without a lookup');

  const junk = harness();
  junk.store.set('resonance:reader:pending-check', JSON.stringify({check_id: hex('3'), workspace: 'workspace-one', text: 'a post'}));
  assert.equal(await junk.client.recover(), null);
  assert.equal(junk.requests.length, 0);
});

test('a workspace switch discards an unsaved check and clears the pointer', async () => {
  const h = harness();
  await running(h);
  h.client.reset();
  assert.equal(h.client.check.phase, 'cancelled');
  assert.equal(h.store.size, 0);
  assert.equal(h.events('cancel').length, 1);
});

test('bridge rejections stop the check with a plain reason', async () => {
  const h = harness();
  h.setupReady();
  h.autoArm = false;
  h.client.start(['ax']);
  assert.equal(h.client.check, null, 'no armed event means the bridge is absent');
  assert.equal(h.client.setup.status, 'absent');
  const g = harness();
  g.setupReady();
  g.autoArm = false;
  const original = g.emit;
  g.log.push = function (entry) { if (entry.name === 'arm') original('rejected', {batchId: entry.detail.batchId, reason: 'gesture_required'}); return Array.prototype.push.call(this, entry); };
  const outcome = g.client.start(['ax']);
  assert.equal(outcome.ok, false);
  assert.match(outcome.message, /Check button/);
  assert.equal(g.calls('/api/checks/begin').length, 0, 'a refused arm never begins');
});

// ---- page-level checks: the real app.js with the real client ----------------------------------
function element() {
  return {innerHTML: '', textContent: '', value: '', content: '', dataset: {}, style: {}, hidden: false, open: false, isConnected: true, disabled: false,
    classList: {add() {}, remove() {}}, querySelector() { return null; }, querySelectorAll() { return []; }, childNodes: [],
    focus() { this.focused = true; }, select() {}, scrollIntoView() {}, setAttribute() {}, removeAttribute() {}, contains(node) { return node === this; },
    replaceChildren() {}, showModal() { this.open = true; }, close() { this.open = false; }};
}
function pageFixture(overrides = {}) {
  const nodes = new Map();
  const get = key => { if (!nodes.has(key)) nodes.set(key, element()); return nodes.get(key); };
  const log = [];
  const sent = [];
  const listeners = new Map();
  const clock = {now: Date.parse('2026-11-01T10:00:00Z')};
  // A browser-faithful location: assigning a hash adds the leading '#', as the real setter does.
  let hash = '#overview';
  const location = {origin: 'http://127.0.0.1:4317', get hash() { return hash; }, set hash(value) { const text = String(value); hash = text === '' ? '' : text.startsWith('#') ? text : '#' + text; }};
  const doc = {querySelector: get, querySelectorAll() { return []; }, createElement() { return element(); }, documentElement: element(), activeElement: null, visibilityState: 'visible',
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push(fn); },
    dispatchEvent(event) {
      log.push(event.type);
      sent.push({type: event.type, detail: plain(event.detail)});
      if (event.type === PREFIX + 'arm') for (const fn of listeners.get(PREFIX + 'armed') || []) fn({target: doc, detail: {batchId: event.detail.batchId}});
      if (event.type === PREFIX + 'discover' && overrides.acceptDiscovery !== false) for (const fn of listeners.get(PREFIX + 'discovery-accepted') || []) fn({target: doc, detail: {discoveryId: event.detail.discoveryId}});
      return true;
    }};
  // The clock the page sees, so a result's five minutes can be crossed without waiting.
  class FakeDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(clock.now); }
    static now() { return clock.now; }
  }
  const timers = [];
  const calls = [];
  const store = new Map();
  let handler = () => { throw new Error('Unexpected request'); };
  const data = {settings: {display_name: 'Example', theme: 'dark'}, workspace: {token: 'workspace-one', mode: 'live'}, accounts: overrides.accounts || defaultAccounts(), reviews: [], policy_flags: [], source_items: [], action_requests: [], activity: [],
    communication: {messages: []}, monitoring: {runs: []}, reader_check_summary: overrides.summary || {count: 0, source_count: 0, revision_count: 0, recent: [], limit: 20}};
  const context = {document: doc, URL, Intl, console, Date: FakeDate, location, window: {scrollTo() {}}, navigator: {clipboard: {writeText: async () => {}}},
    matchMedia: () => ({matches: false}), setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout: id => { if (timers[id - 1]) timers[id - 1] = {fn() {}, ms: 0}; }, sessionStorage: {getItem: key => store.has(key) ? store.get(key) : null, setItem: (key, value) => store.set(key, String(value)), removeItem: key => store.delete(key), get length() { return store.size; }, key: index => [...store.keys()][index] ?? null},
    FormData: class { constructor(form) { this.values = form.values; } get(key) { return this.values[key]; } has(key) { return Object.hasOwn(this.values, key); } },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    crypto: {getRandomValues(array) { for (let index = 0; index < array.length; index++) array[index] = Math.floor(Math.random() * 256); return array; }},
    fetch: async (url, options) => { calls.push({url, ...options}); return handler(url, options); }};
  vm.createContext(context);
  if (!overrides.noReader) vm.runInContext(readerSource, context);
  if (!overrides.noDiscovery) vm.runInContext(discoverySource, context);
  const cutoff = appSource.indexOf("  document.addEventListener('click'");
  assert.ok(cutoff > 0);
  vm.runInContext(appSource.slice(0, cutoff) + '\n globalThis.ui={state,renderOverview,renderWelcome,handleAction,saveForm,readerClient,readerTodayContent,readerAccountBlock,readerReceiptHtml,readerSetupDialog,readerSetupLive,readerSetupState,readerCheckProgress,discovery,discoveryHtml,discoveryDialogClosed,resetWorkspaceEditors,importFile,renderReaderPanels,getWalk:() => accountWalk};\n})();', context);
  context.ui.state.data = structuredClone(data);
  context.ui.state.connected = true;
  context.ui.state.view = 'accounts';
  if (context.ui.readerClient) {
    context.ui.readerClient.subscribe(context.ui.renderReaderPanels);
    context.ui.readerClient.attach();
  }
  const emit = (name, detail) => { for (const fn of listeners.get(PREFIX + name) || []) fn({target: doc, detail}); };
  return {ui: context.ui, context, get, calls, log, sent, store, data, timers, clock, emit, setHandler(fn) { handler = fn; }, dialog: () => get('#dialog-content').innerHTML,
    fire: ms => timers.filter(timer => timer.ms === ms).forEach(timer => timer.fn())};
}
const respond = body => ({ok: true, status: 200, json: async () => structuredClone(body)});
const failure = (status, error) => ({ok: false, status, json: async () => ({error})});

test('Today leads with Check now once, with a latest saved receipt, and disables it for samples', () => {
  const summary = {count: 1, source_count: 4, revision_count: 4, limit: 20, recent: [{id: hex('4'), origin: 'live', completed_at: '2026-11-01T10:00:05Z', reader_version: '0.1.0', totals: {accounts: 2, ok: 1, held: 0, error: 0, unsupported: 1, sources: 3, findings: 1}, accounts: []}]};
  const page = pageFixture({summary});
  const today = page.ui.readerTodayContent();
  assert.equal((today.match(/id="res-check-now"/g) || []).length, 1);
  assert.ok(today.includes('Set up account checks'));
  assert.ok(today.includes('LATEST SAVED CHECK'));
  assert.ok(today.includes('data-action="reader-receipt" data-id="' + hex('4') + '"'));
  assert.ok(today.includes('Paste text') && today.includes('Ask my assistant'));
  assert.ok(!/safe|score/i.test(today.replace(/A check with no rule matches does not mean an account is safe\./, '')), 'no safety claim or score');
  assert.ok(!/id="res-check-cancel"/.test(today), 'no Cancel when nothing is running');
  page.ui.state.data.workspace.mode = 'samples';
  assert.match(page.ui.readerTodayContent(), /id="res-check-now" class="button primary" data-action="reader-check-all" disabled/);
  const bare = pageFixture({noReader: true});
  assert.match(bare.ui.readerTodayContent(), /reader-check-all" disabled/);
  assert.ok(bare.ui.readerTodayContent().includes('Reader helper not loaded'));
});

test('account detail offers a check for X/YouTube and an honest fallback for other platforms', () => {
  const page = pageFixture();
  const x = page.ui.readerAccountBlock(page.data.accounts[0]);
  assert.ok(x.includes('Check this account now') && x.includes('data-action="reader-account-dialog"'));
  assert.ok(!/id="res-check-account"/.test(x), 'the fixed ID lives only in the one selected-account dialog');
  const ig = page.ui.readerAccountBlock(page.data.accounts[2]);
  assert.ok(ig.includes('Saved · automatic checking not available yet'));
  assert.ok(ig.includes('data-action="add-excerpt"') && ig.includes('data-action="connect-assistant"'));
  assert.ok(!/connected/i.test(ig) && !/connected/i.test(x), 'saving a profile is never called connected');
});

test('the selected-account dialog carries the fixed IDs, one Cancel and distinct states', async () => {
  const page = pageFixture();
  const client = page.ui.readerClient;
  client.handle('setup', {version: '0.1.0', protocol: 1, x: true, youtube: true});
  await page.ui.handleAction({dataset: {action: 'reader-account-dialog', id: 'ax'}});
  const dialogHtml = page.dialog();
  assert.equal((dialogHtml.match(/id="res-check-account"/g) || []).length, 1);
  page.setHandler((url, options) => url === '/api/checks/begin' ? respond(beginJob(page.data.accounts, JSON.parse(options.body))) : respond({}));
  const before = page.log.length;
  const pending = page.ui.handleAction({dataset: {action: 'reader-check-account', id: 'ax'}});
  // Nothing has been awaited yet: arm and the begin request already happened, in that order.
  assert.deepEqual(page.log.slice(before), [PREFIX + 'arm']);
  assert.equal(page.calls.length, 1);
  assert.equal(page.calls[0].url, '/api/checks/begin');
  await pending;
  const progress = page.ui.readerCheckProgress(true);
  assert.equal((progress.match(/id="res-check-cancel"/g) || []).length, 1);
  assert.equal((page.ui.readerCheckProgress(false).match(/res-check-cancel/g) || []).length, 0, 'the hidden copy of the status has no duplicate ID');
  assert.ok(!progress.includes('>Saved<'), 'nothing is called saved before a receipt');
});

test('setup states: absent explains the preview without inventing a link; installed offers settings', () => {
  const page = pageFixture();
  page.ui.readerSetupDialog();
  const html = page.dialog();
  for (const phrase of ['not in a browser store yet', 'same browser', 'whole site', 'at most the 3 newest', 'images, video', 'separate visible browser window', 'never posts, edits, deletes']) assert.ok(html.includes(phrase), phrase);
  assert.ok(!/developer mode|unpacked|chrome:\/\/extensions|https?:\/\/(chrome\.google|chromewebstore|microsoftedge)/i.test(html), 'no developer-mode instruction and no invented store link');
  assert.ok(html.includes('Looking for the browser reader'), 'the first look is read-only detection');
  page.fire(1500);
  let live = page.ui.readerSetupLive();
  assert.ok(live.includes('Browser reader not found') && live.includes('Look again'));
  assert.ok(live.includes('data-action="add-excerpt"') && live.includes('data-action="connect-assistant"') && live.includes('data-action="add-account"'));
  assert.ok(!live.includes('res-reader-options'), 'nothing to open when the reader is absent');
  page.ui.state.data.accounts = [];
  assert.ok(page.ui.readerSetupLive().includes('data-action="welcome-paste"') && page.ui.readerSetupLive().includes('Paste my first post'));
  page.ui.state.data.accounts = structuredClone(page.data.accounts);
  page.ui.readerClient.handle('setup', {version: '0.1.0', protocol: 1, x: false, youtube: null});
  live = page.ui.readerSetupLive();
  assert.equal((live.match(/id="res-reader-options"/g) || []).length, 1);
  assert.ok(live.includes('Permission needed') && live.includes('X: permission needed') && live.includes('YouTube: not confirmed'));
  page.ui.readerClient.handle('setup', {version: '0.1.0', protocol: 1, x: true, youtube: true});
  live = page.ui.readerSetupLive();
  assert.ok(live.includes('>Ready<') && live.includes('res-reader-options') && live.includes('Look again'));
  page.ui.readerClient.handle('setup', {version: '0.0.6', protocol: 1, x: true, youtube: true});
  live = page.ui.readerSetupLive();
  assert.ok(live.includes('Reader version does not match') && live.includes('Found 0.0.6') && live.includes('res-reader-options'));
  page.ui.readerClient.handle('disconnected', {reason: 'transport_disconnected', batchId: null});
  assert.ok(page.ui.readerSetupLive().includes('Reader disconnected'));
  page.ui.state.data.workspace.mode = 'samples';
  assert.ok(page.ui.readerSetupLive().includes('Not used with sample data') && !page.ui.readerSetupLive().includes('res-reader-options'));
});

test('welcome offers the three doors and Paste my first post keeps its route through account creation', async () => {
  const page = pageFixture();
  page.ui.state.data.accounts = [];
  const welcome = page.ui.renderWelcome();
  for (const door of ['Set up account checks', 'Paste my first post', 'Try samples']) assert.ok(welcome.includes(door), door);
  assert.ok(welcome.indexOf('Set up account checks') < welcome.indexOf('Paste my first post') && welcome.indexOf('Paste my first post') < welcome.indexOf('Try samples'));
  await page.ui.handleAction({dataset: {action: 'welcome-paste'}});
  assert.ok(page.dialog().includes('Check wording without an account'), 'the old no-account route stays available');
  await page.ui.handleAction({dataset: {action: 'walk-platform', platform: 'X'}});
  await page.ui.saveForm({values: {identity: 'https://x.com/river_example'}, reportValidity: () => true, getAttribute: () => 'walk-identity-form', querySelector: () => null});
  const saved = {id: 'new-account', platform: 'X', handle: 'river_example', url: 'https://x.com/river_example', display_name: '', identity_mode: 'named', daring: 'careful', sfw_only: true};
  page.setHandler(url => url === '/api/accounts' ? respond({account: saved}) : respond({...page.data, accounts: [saved]}));
  await page.ui.saveForm({values: {identity_mode: 'named', daring: 'careful'}, reportValidity: () => true, getAttribute: () => 'walk-boundaries-form', querySelector: () => null});
  assert.ok(page.dialog().includes('Does this fit the account?'), 'the walk continues to the excerpt check instead of stopping at sign-in');
  assert.ok(page.dialog().includes('value="new-account" selected'), 'the new account is preselected');
  assert.ok(!page.dialog().includes('Bring in my accounts'));
  page.setHandler(url => url === '/api/excerpts' ? respond({review: {id: 'rv1'}, flag_count: 1}) : respond({...page.data, accounts: [saved]}));
  await page.ui.saveForm({values: {account_id: 'new-account', kind: 'post', text: 'my first post', source_url: '', title: ''}, reportValidity: () => true, getAttribute: () => 'excerpt-form', querySelector: () => null});
  assert.equal(page.context.location.hash, '#review', 'after saving the user lands on the actual review');
  assert.equal(page.ui.state.filters.account, 'new-account');
  assert.equal(page.ui.state.filters.status, 'needs_you');
});

test('receipts and findings escape every character and navigate to the account review', async () => {
  const hostile = '<img src=x onerror=alert(1)> & "quoted"';
  const receipt = {...receiptFor(hex('5')), accounts: [{account_id: 'ax', platform: 'X', handle: hostile, status: 'ok', reason: null, identity: {handle: hostile, channel_id: null}, coverage: {limit: 3, accepted: 2, examined: 5, skipped: [{reason: 'quote_<b>', count: 2}], more_available: true, partial: true, notes: []}, gaps: ['more_available', 'weird<token>'],
    source_count: 2, new_source_count: 1, changed_source_count: 0, unchanged_source_count: 1, finding_count: 2, flagged_source_count: 1, new_review_count: 1},
    {account_id: 'ai', platform: 'Instagram', handle: 'alice', status: 'unsupported', reason: 'provider_not_supported', identity: null, coverage: null, gaps: ['provider_not_supported'], source_count: 0, finding_count: 0}], totals: {accounts: 2, ok: 1, held: 0, error: 0, unsupported: 1, sources: 2, findings: 2}};
  const page = pageFixture();
  page.setHandler(url => url === '/api/checks/' + hex('5') ? respond(receipt) : respond({}));
  await page.ui.handleAction({dataset: {action: 'reader-receipt', id: hex('5')}});
  const html = page.dialog();
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt; &amp; &quot;quoted&quot;'));
  assert.ok(!html.includes('<img') && !html.includes('<b>'), 'receipt text is never markup');
  assert.ok(html.includes('More recent items exist') && html.includes('Not supported yet') && html.includes('This is not a safety rating'));
  assert.ok(html.includes('data-action="reader-findings" data-id="ax"') && html.includes('data-action="reader-corpus" data-id="ax"'));
  assert.ok(!html.includes('data-action="reader-corpus" data-id="ai"'), 'an unsupported account has no corpus link');
  assert.equal(page.calls[0].url, '/api/checks/' + hex('5'));
  await page.ui.handleAction({dataset: {action: 'reader-findings', id: 'ax'}});
  assert.equal(page.context.location.hash, '#review');
  assert.equal(page.ui.state.filters.account, 'ax');
  assert.equal(page.ui.state.filters.status, 'all');
});

test('the corpus view shows full escaped text, at most 20 per page, and More follows the cursor', async () => {
  const long = 'Line one <script>alert(1)</script>\n' + 'x'.repeat(6000) + ' END';
  const source = (index, text) => ({id: 's' + index, kind: 'post', url: 'https://x.com/alice/status/' + (100 + index), date_iso: '2026-10-31T09:00:00Z', date_label: null, date_unknown: false, first_seen_at: '2026-11-01T10:00:00Z', last_seen_at: '2026-11-01T10:00:00Z',
    revision_count: index === 0 ? 2 : 1, current_revision: {text, links_shortened: index === 1}});
  const page = pageFixture();
  const firstPage = Array.from({length: 20}, (_, index) => source(index, index === 0 ? long : 'plain ' + index));
  page.setHandler(url => url.includes('cursor=') ? respond({sources: [source(20, 'second page')], next_cursor: null}) : respond({sources: firstPage, next_cursor: 'cur sor/1'}));
  await page.ui.handleAction({dataset: {action: 'reader-corpus', id: 'ax'}});
  assert.match(page.calls[0].url, /^\/api\/corpus\?account_id=ax&limit=20$/);
  let html = page.get('#reader-corpus-live').innerHTML;
  assert.equal((html.match(/class="reader-source"/g) || []).length, 20);
  assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;') && !html.includes('<script>'));
  assert.ok(html.includes('x'.repeat(6000) + ' END'), 'the saved text is shown in full, never cut off');
  assert.ok(html.includes('2 saved versions') && html.includes('Open original') && html.includes('data-action="reader-corpus-more"'));
  await page.ui.handleAction({dataset: {action: 'reader-corpus-more'}});
  assert.match(page.calls[1].url, /&cursor=cur%20sor%2F1$/);
  html = page.get('#reader-corpus-live').innerHTML;
  assert.equal((html.match(/class="reader-source"/g) || []).length, 21);
  assert.ok(html.includes('second page') && !html.includes('reader-corpus-more'));
});

test('a failed corpus page shows the error and keeps what was already read', async () => {
  const page = pageFixture();
  page.setHandler(() => failure(404, 'Account not found.'));
  await page.ui.handleAction({dataset: {action: 'reader-corpus', id: 'ax'}});
  assert.ok(page.get('#reader-corpus-live').innerHTML.includes('Account not found.'));
});

// ---- Find my accounts: the page side of discovery and the explicit "Use this account" -----------
const installed = (page, detail = {}) => page.ui.readerClient.handle('setup', {version: '0.1.0', protocol: 1, x: true, youtube: true, ...detail});
const act = (page, dataset, extra = {}) => page.ui.handleAction({dataset, ...extra});
const discovers = page => page.sent.filter(item => item.type === PREFIX + 'discover');
const count = (html, text) => html.split(text).length - 1;
const stamp = page => new Date(page.clock.now).toISOString();
const foundX = (page, handle = 'river_example') => ({provider: 'x', status: 'ok', identity: {handle, channel_id: null, profile_url: 'https://x.com/' + handle}, observed_at: stamp(page)});
const foundYouTube = (page, handle = 'rivertube') => ({provider: 'youtube', status: 'ok', identity: {handle, channel_id: CHANNEL, profile_url: 'https://www.youtube.com/channel/' + CHANNEL}, observed_at: stamp(page)});
const heldResult = (provider, reason) => ({provider, status: 'held', reason});
const select = async (page, ...providers) => { for (const provider of providers) await act(page, {action: 'discovery-toggle', provider}, {checked: true}); };
// Opens setup with a ready reader, picks the sites, clicks Find my accounts and delivers the replies.
async function discover(page, providers, results, {done = true} = {}) {
  installed(page);
  page.ui.readerSetupDialog();
  await select(page, ...providers);
  await act(page, {action: 'discovery-find'});
  const id = discovers(page).at(-1).detail.discoveryId;
  for (const result of results) page.emit('discovery-result', {discoveryId: id, ...result});
  if (done) page.emit('discovery-done', {discoveryId: id});
  return id;
}
function useButton(page, provider) {
  const match = new RegExp(`data-action="discovery-use" data-provider="${provider}" data-discovery-id="([a-f0-9]{32})" data-observed-at="([^"]+)"`).exec(page.ui.discoveryHtml());
  assert.ok(match, 'a Use this account button for ' + provider);
  return {action: 'discovery-use', provider, discoveryId: match[1], observedAt: match[2]};
}
// A workspace that saves accounts and answers /api/state with them, recording each save.
function accountServer(page) {
  const saved = [];
  page.setHandler((url, options) => {
    if (url === '/api/accounts' && options.method === 'POST') {
      const body = JSON.parse(options.body);
      saved.push(body);
      const account = {id: 'new-' + saved.length, display_name: '', ...body};
      page.data.accounts = [...page.data.accounts, account];
      return respond({account});
    }
    if (url === '/api/state') return respond(page.data);
    throw new Error('Unexpected request ' + options.method + ' ' + url);
  });
  return saved;
}
const boundaries = (page, values) => page.ui.saveForm({values, reportValidity: () => true, getAttribute: () => 'walk-boundaries-form', querySelector: () => null});

test('Find my accounts starts only from its own button, in the click task, with an exact event', async () => {
  const page = pageFixture();
  installed(page);
  page.ui.readerSetupDialog();
  let html = page.dialog();
  for (const id of ['res-find-accounts', 'res-discovery-x', 'res-discovery-youtube', 'res-reader-options']) assert.equal(count(html, `id="${id}"`), 1, id + ' appears once');
  assert.equal(count(html, 'id="res-find-cancel"'), 0, 'no Cancel while nothing runs');
  assert.match(html, /id="res-find-accounts"[^>]*disabled/);
  assert.ok(html.includes('Choose X, YouTube, or both.'));
  assert.ok(html.includes('reads no posts') && html.includes('never switches accounts'));
  await select(page, 'x', 'youtube');
  html = page.ui.discoveryHtml();
  assert.doesNotMatch(html, /id="res-find-accounts"[^>]*disabled/);
  assert.equal(count(html, ' checked'), 2);
  const before = page.sent.length;
  const pending = act(page, {action: 'discovery-find'});
  // Nothing has been awaited: the event is already out, and nothing went to the network.
  assert.equal(page.sent.length, before + 1);
  assert.equal(page.sent[before].type, PREFIX + 'discover');
  assert.deepEqual(Object.keys(page.sent[before].detail).sort(), ['discoveryId', 'providers']);
  assert.match(page.sent[before].detail.discoveryId, /^[a-f0-9]{32}$/);
  assert.deepEqual(page.sent[before].detail.providers, ['x', 'youtube']);
  assert.equal(page.calls.length, 0);
  await pending;
  const live = page.get('#reader-discovery-live').innerHTML;
  assert.ok(live.includes('Looking in your browser: X, then YouTube'), 'the page re-renders from the controller');
  assert.equal(count(live, 'id="res-find-cancel"'), 1);
  assert.match(live, /id="res-find-accounts"[^>]*disabled/);
  assert.match(live, /id="res-discovery-x"[^>]*disabled/);
});

test('the site choices decide exactly which providers are asked', async () => {
  for (const choice of [['x'], ['youtube'], ['x', 'youtube']]) {
    const page = pageFixture();
    installed(page);
    page.ui.readerSetupDialog();
    await select(page, ...choice);
    await act(page, {action: 'discovery-find'});
    assert.deepEqual(discovers(page)[0].detail.providers, choice);
  }
  const page = pageFixture();
  installed(page);
  page.ui.readerSetupDialog();
  await select(page, 'x');
  await act(page, {action: 'discovery-toggle', provider: 'x'}, {checked: false});
  await act(page, {action: 'discovery-find'});
  assert.equal(discovers(page).length, 0, 'an unchecked site is never asked');
  assert.ok(page.get('#reader-discovery-live').innerHTML.includes('Choose X, YouTube, or both.'));
  await act(page, {action: 'discovery-toggle', provider: 'bogus'}, {checked: true});
  assert.deepEqual(plain(page.ui.discovery.state().providers), []);
});

test('missing reader, version, permission and helper states are stated and never start a search', async () => {
  const absent = pageFixture();
  absent.ui.readerSetupDialog();
  absent.fire(1500);
  await select(absent, 'x');
  await act(absent, {action: 'discovery-find'});
  assert.equal(discovers(absent).length, 0);
  assert.ok(absent.get('#reader-discovery-live').innerHTML.includes('The browser reader is not ready'));
  assert.match(absent.ui.discoveryHtml(), /id="res-find-accounts"[^>]*disabled/);

  const old = pageFixture();
  installed(old, {version: '0.0.6'});
  old.ui.readerSetupDialog();
  await select(old, 'x');
  await act(old, {action: 'discovery-find'});
  assert.equal(discovers(old).length, 0, 'a reader of the wrong version is never asked');

  const denied = pageFixture();
  installed(denied, {x: false});
  denied.ui.readerSetupDialog();
  const html = denied.dialog();
  assert.ok(html.includes('permission needed (open reader settings)'));
  assert.equal(count(html, 'id="res-reader-options"'), 1, 'one way to reader settings');
  assert.match(html, /id="res-discovery-x"[^>]*disabled/);
  await select(denied, 'x');
  await act(denied, {action: 'discovery-find'});
  assert.equal(discovers(denied).length, 0, 'no search is sent for a site without permission');
  assert.ok(denied.get('#reader-discovery-live').innerHTML.includes('Permission is needed for X'));
  await act(denied, {action: 'discovery-toggle', provider: 'x'}, {checked: false});
  await select(denied, 'youtube');
  await act(denied, {action: 'discovery-find'});
  assert.deepEqual(discovers(denied)[0].detail.providers, ['youtube'], 'the other site can still be asked');

  const held = pageFixture();
  await discover(held, ['youtube'], [heldResult('youtube', 'permission_needed')]);
  const cards = held.ui.discoveryHtml();
  assert.ok(cards.includes('YouTube was not found') && cards.includes('data-action="reader-options"'), 'a refused permission points to reader settings');
  assert.ok(!cards.includes('Use this account'));

  const noReader = pageFixture({noReader: true});
  noReader.ui.readerSetupDialog();
  await select(noReader, 'x');
  await act(noReader, {action: 'discovery-find'});
  assert.equal(discovers(noReader).length, 0);
  assert.ok(noReader.ui.discoveryHtml().includes('reader helper did not load'));

  const noFinder = pageFixture({noDiscovery: true});
  installed(noFinder);
  noFinder.ui.readerSetupDialog();
  assert.ok(noFinder.dialog().includes('account finder did not load'));
  await act(noFinder, {action: 'discovery-find'});
  assert.equal(discovers(noFinder).length, 0);
  noFinder.setHandler(url => url === '/api/checks/begin' ? respond(beginJob(noFinder.data.accounts, {account_ids: ['ax']})) : respond({}));
  await act(noFinder, {action: 'reader-check-account', id: 'ax'});
  assert.equal(noFinder.sent.filter(item => item.type === PREFIX + 'arm').length, 1, 'checks still work without the finder');
});

test('unsupported sites keep the manual, paste and assistant routes', () => {
  const page = pageFixture();
  installed(page);
  page.ui.readerSetupDialog();
  const html = page.dialog();
  assert.ok(html.includes('Other sites are not found automatically'));
  for (const route of ['data-action="add-account"', 'data-action="connect-assistant"']) assert.ok(html.includes(route), route);
  assert.ok(!/Facebook|Instagram|Reddit|TikTok/.test(page.ui.discoveryHtml()), 'no other site is offered for finding');
});

test('Cancel, a closed dialog and a workspace change revoke the search and forget what it found', async () => {
  const cancelled = pageFixture();
  const id = await discover(cancelled, ['x', 'youtube'], [foundX(cancelled)], {done: false});
  await act(cancelled, {action: 'discovery-cancel'});
  const revoked = cancelled.sent.filter(item => item.type === PREFIX + 'cancel-discovery');
  assert.equal(revoked.length, 1);
  assert.deepEqual(revoked[0].detail, {discoveryId: id});
  assert.ok(cancelled.get('#reader-discovery-live').innerHTML.includes('cancelled'));
  cancelled.emit('discovery-result', {discoveryId: id, ...foundYouTube(cancelled)});
  cancelled.emit('discovery-done', {discoveryId: id});
  assert.equal(cancelled.ui.discovery.candidates().length, 0, 'late replies after Cancel are ignored');
  assert.ok(!cancelled.ui.discoveryHtml().includes('Use this account'));

  const closed = pageFixture();
  const closedId = await discover(closed, ['x'], [], {done: false});
  closed.ui.discoveryDialogClosed();
  assert.deepEqual(closed.sent.filter(item => item.type === PREFIX + 'cancel-discovery').map(item => item.detail), [{discoveryId: closedId}]);
  assert.equal(closed.ui.discovery.state().status, 'idle');

  const running = pageFixture();
  const runningId = await discover(running, ['x'], [], {done: false});
  running.ui.resetWorkspaceEditors();
  assert.deepEqual(running.sent.filter(item => item.type === PREFIX + 'cancel-discovery').map(item => item.detail), [{discoveryId: runningId}]);
  running.emit('discovery-result', {discoveryId: runningId, ...foundX(running)});
  assert.equal(running.ui.discovery.candidates().length, 0);

  const finished = pageFixture();
  await discover(finished, ['x'], [foundX(finished)]);
  assert.ok(finished.ui.discoveryHtml().includes('Use this account'));
  finished.ui.resetWorkspaceEditors();
  assert.equal(finished.ui.discovery.candidates().length, 0, 'a workspace change forgets finished results');
  assert.ok(!finished.ui.discoveryHtml().includes('Use this account'));
});

test('a late result from an older search never reaches the next one', async () => {
  const page = pageFixture();
  const first = await discover(page, ['x'], [], {done: false});
  await act(page, {action: 'discovery-cancel'});
  await act(page, {action: 'discovery-find'});
  const second = discovers(page).at(-1).detail.discoveryId;
  assert.notEqual(first, second);
  page.emit('discovery-result', {discoveryId: first, ...foundX(page, 'older_one')});
  page.emit('discovery-done', {discoveryId: first});
  assert.equal(page.ui.discovery.state().status, 'running');
  assert.equal(page.ui.discovery.candidates().length, 0);
  page.emit('discovery-result', {discoveryId: second, ...foundX(page, 'current_one')});
  page.emit('discovery-done', {discoveryId: second});
  assert.deepEqual(plain(page.ui.discovery.candidates().map(item => item.identity.handle)), ['current_one']);
});

test('nothing is saved until Use this account, and then the normal boundary questions are asked', async () => {
  const page = pageFixture();
  const saved = accountServer(page);
  await discover(page, ['x', 'youtube'], [foundX(page), foundYouTube(page)]);
  assert.equal(saved.length, 0, 'finding an account never saves it');
  assert.equal(page.ui.state.data.accounts.length, 3);
  assert.equal(page.calls.length, 0);
  const cards = page.ui.discoveryHtml();
  assert.equal(count(cards, 'Use this account'), 2);
  for (const text of ['river_example', 'https://x.com/river_example', 'rivertube', 'https://www.youtube.com/channel/' + CHANNEL, 'Stable channel ID ' + CHANNEL, 'Nothing is saved until you choose']) assert.ok(cards.includes(text), text);
  await act(page, useButton(page, 'youtube'));
  const dialogHtml = page.dialog();
  assert.ok(dialogHtml.includes('Is this under your real name or an alias?') && dialogHtml.includes('How daring may this account be?'));
  assert.ok(dialogHtml.includes('YouTube · rivertube') && dialogHtml.includes('https://www.youtube.com/channel/' + CHANNEL));
  assert.ok(dialogHtml.includes('<option value="named" selected>') && dialogHtml.includes('<option value="careful" selected>'), 'named and careful are the defaults');
  assert.match(dialogHtml, /id="sfw-only" name="sfw_only" type="checkbox" checked disabled/);
  assert.ok(!dialogHtml.includes('Change this') && !dialogHtml.includes('data-action="walk-back"'), 'a verified identity is not retyped');
  assert.equal(saved.length, 0, 'choosing does not save; only Save account does');
  await boundaries(page, {identity_mode: 'named', daring: 'careful'});
  assert.equal(saved.length, 1);
  assert.deepEqual(saved[0], {platform: 'YouTube', handle: 'rivertube', url: 'https://www.youtube.com/channel/' + CHANNEL, display_name: '', identity_mode: 'named', daring: 'careful', sfw_only: true});
  await boundaries(page, {identity_mode: 'named', daring: 'careful'});
  assert.equal(saved.length, 1, 'a second submit of the same walk saves nothing more');
  assert.ok(page.dialog().includes('rivertube is saved'));
  assert.ok(page.calls.every(call => call.method !== 'PATCH' && call.method !== 'DELETE'), 'no existing account is modified');
  assert.deepEqual(plain(page.ui.discovery.candidates().map(item => item.provider)), ['x'], 'the used result is forgotten');
  assert.equal(count(page.ui.discoveryHtml(), 'Use this account'), 1, 'the other site is still waiting for its own choice');
  assert.equal(page.ui.state.data.accounts.length, 4);
});

test('a confirmed new account leads directly to its first bounded check', async () => {
  const page = pageFixture();
  accountServer(page);
  await discover(page, ['x'], [foundX(page)]);
  await act(page, useButton(page, 'x'));
  await boundaries(page, {identity_mode: 'named', daring: 'careful'});
  assert.match(page.dialog(), /id="res-check-account"[^>]*data-action="reader-first-check"/);
  assert.ok(page.dialog().includes('Run my first check'));
  assert.ok(!page.dialog().includes('Sign in to X'), 'discovered identity is not sent back to sign-in');
  page.setHandler((url, options) => url === '/api/checks/begin'
    ? respond(beginJob(page.data.accounts, JSON.parse(options.body))) : respond({}));
  await act(page, {action:'reader-first-check', id:'new-1'});
  await flush();
  assert.equal(page.ui.readerClient.check.phase, 'reading');
  assert.deepEqual(plain(page.ui.readerClient.check.entries.map(entry => entry.accountId)), ['new-1']);
  assert.match(page.get('#reader-dialog-live').innerHTML, /Reading/);
});

test('restored receipts are labeled imported history', () => {
  const page = pageFixture();
  assert.match(page.ui.readerReceiptHtml({...receiptFor(hex('4')), origin:'historical'}), /Imported history/);
});

test('an X account keeps its canonical profile address; an alias relaxes SFW only when asked', async () => {
  const cases = [
    [{identity_mode: 'alias', daring: 'bold'}, 'alias', 'bold', false],
    [{identity_mode: 'alias', daring: 'careful', sfw_only: 'on'}, 'alias', 'careful', true],
    [{identity_mode: 'named', daring: 'balanced', sfw_only: 'on'}, 'named', 'balanced', true],
    [{identity_mode: 'named', daring: 'balanced'}, 'named', 'balanced', true],
  ];
  for (const [values, mode, daring, sfw] of cases) {
    const page = pageFixture();
    const saved = accountServer(page);
    await discover(page, ['x'], [foundX(page, 'River_Example')]);
    await act(page, useButton(page, 'x'));
    await boundaries(page, values);
    assert.deepEqual(saved, [{platform: 'X', handle: 'River_Example', url: 'https://x.com/River_Example', display_name: '', identity_mode: mode, daring, sfw_only: sfw}]);
  }
});

test('an account that is already saved is offered as existing, once, and never overwritten', async () => {
  const accounts = [...defaultAccounts(),
    {...baseAccount, id: 'ah', platform: 'X', handle: '<img src=x onerror=alert(1)>', url: 'https://x.com/river_example'},
    {...baseAccount, id: 'ac', platform: 'YouTube', handle: CHANNEL, url: ''}];
  const page = pageFixture({accounts});
  const saved = accountServer(page);
  const id = await discover(page, ['x', 'youtube'], [foundX(page), foundYouTube(page)]);
  const html = page.ui.discoveryHtml();
  assert.equal(count(html, 'Already saved'), 2);
  assert.ok(!html.includes('discovery-use'), 'no second copy is offered');
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;') && !html.includes('<img'), 'saved text is escaped');
  assert.ok(html.includes('data-action="account-detail" data-id="ah"') && html.includes('data-action="account-detail" data-id="ac"'));
  await act(page, {action: 'discovery-use', provider: 'x', discoveryId: id, observedAt: stamp(page)});
  assert.equal(page.ui.getWalk(), null, 'a forged click on an existing account opens nothing');
  assert.equal(saved.length, 0);
  assert.equal(page.calls.length, 0);
  const sameLetters = pageFixture();
  await discover(sameLetters, ['x'], [foundX(sameLetters, 'ALICE')]);
  assert.ok(sameLetters.ui.discoveryHtml().includes('Already saved'), 'handles compare without case');
  const other = pageFixture();
  await discover(other, ['x'], [foundX(other, 'someone_else')]);
  assert.ok(other.ui.discoveryHtml().includes('Use this account'), 'a different handle is a new account');
});

test('an expired result is rejected at the click, at Save and by the timer', async () => {
  const page = pageFixture();
  const saved = accountServer(page);
  await discover(page, ['x'], [foundX(page)]);
  const button = useButton(page, 'x');
  const timer = page.timers.filter(item => item.ms > 0).find(item => item.ms === 300050);
  assert.ok(timer, 'a refresh is scheduled for when the five minutes end');
  page.clock.now += 5 * 60 * 1000 + 1;
  timer.fn();
  assert.ok(!page.get('#reader-discovery-live').innerHTML.includes('Use this account'), 'the card disappears by itself');
  await act(page, button);
  assert.equal(page.ui.getWalk(), null);
  assert.ok(page.get('#reader-discovery-live').innerHTML.includes('more than five minutes'));
  assert.equal(saved.length, 0);

  const late = pageFixture();
  const lateSaved = accountServer(late);
  await discover(late, ['x'], [foundX(late)]);
  await act(late, useButton(late, 'x'));
  late.clock.now += 5 * 60 * 1000 + 1;
  await boundaries(late, {identity_mode: 'named', daring: 'careful'});
  assert.equal(lateSaved.length, 0, 'a result that expired while the boundaries were open saves nothing');
  assert.ok(late.get('#dialog-error').textContent.includes('more than five minutes'));
  assert.equal(late.ui.getWalk().saved, null);
});

test('a replaced, cleared or foreign result can never be saved', async () => {
  const page = pageFixture();
  const saved = accountServer(page);
  const first = await discover(page, ['x'], [foundX(page, 'first_one')]);
  const stale = useButton(page, 'x');
  await act(page, {action: 'discovery-find'});
  const second = discovers(page).at(-1).detail.discoveryId;
  page.emit('discovery-result', {discoveryId: second, ...foundX(page, 'second_one')});
  page.emit('discovery-done', {discoveryId: second});
  assert.notEqual(first, second);
  await act(page, stale);
  assert.equal(page.ui.getWalk(), null, 'a button from the replaced search does nothing');
  assert.ok(page.get('#reader-discovery-live').innerHTML.includes('replaced by a newer search'));
  assert.equal(saved.length, 0);

  const open = pageFixture();
  const openSaved = accountServer(open);
  await discover(open, ['x'], [foundX(open, 'first_one')]);
  await act(open, useButton(open, 'x'));
  await act(open, {action: 'discovery-find'});
  const next = discovers(open).at(-1).detail.discoveryId;
  open.emit('discovery-result', {discoveryId: next, ...foundX(open, 'first_one')});
  open.emit('discovery-done', {discoveryId: next});
  await boundaries(open, {identity_mode: 'named', daring: 'careful'});
  assert.equal(openSaved.length, 0, 'the old walk cannot save once a newer search replaced its result');
  assert.ok(open.get('#dialog-error').textContent.includes('replaced by a newer search'));

  const gone = pageFixture();
  const goneSaved = accountServer(gone);
  await discover(gone, ['x'], [foundX(gone)]);
  await act(gone, useButton(gone, 'x'));
  gone.ui.resetWorkspaceEditors();
  await boundaries(gone, {identity_mode: 'named', daring: 'careful'});
  assert.equal(goneSaved.length, 0, 'a workspace change leaves nothing to save');

  const forged = pageFixture();
  const forgedSaved = accountServer(forged);
  const id = await discover(forged, ['x'], [foundX(forged)]);
  await act(forged, {action: 'discovery-use', provider: 'x', discoveryId: id, observedAt: '2026-11-01T09:00:00.000Z'});
  await act(forged, {action: 'discovery-use', provider: 'youtube', discoveryId: id, observedAt: stamp(forged)});
  assert.equal(forged.ui.getWalk(), null);
  assert.equal(forgedSaved.length, 0);
});

test('a search nobody answers or the reader refuses ends with a plain reason and no result', async () => {
  const stalled = pageFixture({acceptDiscovery: false});
  installed(stalled);
  stalled.ui.readerSetupDialog();
  await select(stalled, 'x');
  await act(stalled, {action: 'discovery-find'});
  assert.equal(stalled.ui.discovery.state().status, 'starting');
  stalled.clock.now += 5000;
  const timer = stalled.timers.find(item => item.ms === 5050);
  assert.ok(timer, 'the page gives the reader five seconds to accept');
  timer.fn();
  assert.equal(stalled.ui.discovery.state().status, 'aborted');
  assert.ok(stalled.get('#reader-discovery-live').innerHTML.includes('did not answer'));
  assert.equal(stalled.sent.filter(item => item.type === PREFIX + 'cancel-discovery').length, 1);

  const refused = pageFixture({acceptDiscovery: false});
  installed(refused);
  refused.ui.readerSetupDialog();
  await select(refused, 'x');
  await act(refused, {action: 'discovery-find'});
  refused.emit('discovery-rejected', {discoveryId: discovers(refused)[0].detail.discoveryId, reason: 'gesture_required'});
  assert.ok(refused.get('#reader-discovery-live').innerHTML.includes('Use the Find my accounts button itself'));
  assert.equal(refused.ui.discovery.candidates().length, 0);
});

test('sample data never starts a search', async () => {
  const page = pageFixture();
  installed(page);
  page.ui.state.data.workspace.mode = 'samples';
  page.ui.readerSetupDialog();
  const html = page.dialog();
  assert.match(html, /id="res-find-accounts"[^>]*disabled/);
  assert.ok(html.includes('Not used with sample data'));
  await select(page, 'x', 'youtube');
  await act(page, {action: 'discovery-find'});
  assert.equal(discovers(page).length, 0);
  assert.equal(page.ui.discovery.state().status, 'idle');
  assert.equal(page.calls.length, 0);
});

test('a check and a search never overlap', async () => {
  const searching = pageFixture();
  await discover(searching, ['x'], [], {done: false});
  assert.match(searching.ui.readerTodayContent(), /id="res-check-now"[^>]*disabled/);
  await act(searching, {action: 'reader-check-all'});
  assert.equal(searching.sent.filter(item => item.type === PREFIX + 'arm').length, 0, 'no check is armed during a search');
  assert.ok(searching.get('#toast').textContent.includes('still running'));
  assert.equal(searching.calls.length, 0);

  const checking = pageFixture();
  installed(checking);
  checking.setHandler(() => new Promise(() => {}));
  assert.equal(checking.ui.readerClient.start(['ax']).ok, true);
  checking.ui.readerSetupDialog();
  await select(checking, 'x');
  await act(checking, {action: 'discovery-find'});
  assert.equal(discovers(checking).length, 0, 'no search starts during a check');
  assert.match(checking.ui.discoveryHtml(), /id="res-find-accounts"[^>]*disabled/);
  assert.ok(checking.get('#reader-discovery-live').innerHTML.includes('A check is running'));
});

test('a backup written by this version (schema 3) reaches the server; unknown versions do not', async () => {
  const page = pageFixture();
  const payload = {schema_version: 3, accounts: [], reviews: [], settings: {}, reader_checks: []};
  await page.ui.importFile({size: 100, name: 'synthetic.json', text: async () => JSON.stringify(payload)});
  assert.ok(page.dialog().includes('confirm-import'), 'version 3 reaches the confirmation');
  page.setHandler(url => url === '/api/import' ? respond({accounts: 0, reviews: 0}) : url === '/api/state' ? respond(page.data) : failure(500, 'Unexpected request ' + url));
  await act(page, {action: 'confirm-import'});
  const sent = page.calls.find(call => call.url === '/api/import');
  assert.ok(sent, 'the import was sent');
  assert.equal(sent.method, 'POST');
  assert.deepEqual(JSON.parse(sent.body), payload, 'the file is sent as it was');
  for (const version of [4, 0, '3', true, null]) {
    const bad = pageFixture();
    await bad.ui.importFile({size: 100, name: 'synthetic.json', text: async () => JSON.stringify({...payload, schema_version: version})});
    assert.ok(bad.get('#dialog-error').textContent.includes('Choose a Resonance backup'), String(version));
    assert.ok(!bad.dialog().includes('confirm-import'));
  }
});

test('a selection of more than 20 accounts is refused, never trimmed', async () => {
  const many = count => Array.from({length: count}, (_, index) => ({...baseAccount, id: 'm' + index, platform: 'X', handle: 'user' + index, url: 'https://x.com/user' + index}));
  const h = harness({accounts: many(21)});
  h.setupReady();
  const refused = h.client.start(h.accounts.map(account => account.id));
  assert.equal(refused.ok, false);
  assert.match(refused.message, /at most 20/);
  assert.match(refused.message, /21 were selected/);
  assert.match(refused.message, /one account at a time/);
  assert.equal(h.client.check, null);
  assert.equal(h.events('arm').length, 0);
  assert.equal(h.calls('/api/checks/begin').length, 0);
  const allowed = harness({accounts: many(20)});
  allowed.setupReady();
  assert.equal(allowed.client.start(allowed.accounts.map(account => account.id)).ok, true);
  assert.equal(allowed.arm().requests.length, 20);

  const page = pageFixture({accounts: many(21)});
  installed(page);
  await act(page, {action: 'reader-check-all'});
  assert.equal(page.sent.filter(item => item.type === PREFIX + 'arm').length, 0);
  assert.ok(page.get('#toast').textContent.includes('at most 20'));
  assert.equal(page.calls.length, 0);
});

test('the browser location setter adds the leading # like a real browser', () => {
  const page = pageFixture();
  page.context.location.hash = 'review';
  assert.equal(page.context.location.hash, '#review');
  page.context.location.hash = '#accounts';
  assert.equal(page.context.location.hash, '#accounts');
  page.context.location.hash = '';
  assert.equal(page.context.location.hash, '');
});

(async () => {
  let failed = 0;
  for (const {name, fn} of tests) {
    try { await fn(); console.log('ok - ' + name); } catch (error) { failed++; console.error('not ok - ' + name + '\n' + (error && error.stack || error)); }
  }
  if (failed) { console.error(failed + ' of ' + tests.length + ' reader UI tests failed'); process.exit(1); }
  console.log('reader UI checks passed (' + tests.length + ')');
})();
