'use strict';

// Browser-reader check client. It owns the check state machine and the page side of
// the resonance:reader: event protocol; app.js owns every pixel. Nothing here touches
// the network except through the injected `request`, and nothing persists except a
// bare check ID plus workspace token (sessionStorage) so a reload can look up the
// receipt. Reading is never resumed after a reload, cancel, disconnect or stale account.
(() => {
  const EXPECTED = Object.freeze({version:'0.1.0', protocol:1});
  const PREFIX = 'resonance:reader:';
  const BRIDGE_ROOT = 'http://127.0.0.1:4317/';
  const POINTER_KEY = 'resonance:reader:pending-check';
  const MAX_REQUESTS = 20;
  const DETECT_MS = 1500;
  // The bridge pins an armed plan for 5 s; start must go out well inside that.
  const BEGIN_MS = 4000;
  // The bridge enforces 130 s per request and 8 min per batch; ours are only backstops
  // that fire a little later so the bridge's own explicit error result wins the race.
  const REQUEST_MS = 135000;
  const BATCH_MS = 485000;
  const SNAPSHOT = ['id','platform','handle','display_name','url','identity_mode','daring','sfw_only'];
  const PROVIDERS = {X:'x', YouTube:'youtube'};
  const ID32 = /^[a-f0-9]{32}$/;
  const X_HANDLE = /^[A-Za-z0-9_]{1,15}$/;
  const X_RESERVED = new Set(['home','explore','notifications','messages','search','settings','i','compose','login','logout','signup','tos','privacy','intent','share','hashtag','account','jobs','help','about']);
  const YT_HANDLE = /^[A-Za-z0-9._-]{3,30}$/;
  const YT_CHANNEL = /^UC[A-Za-z0-9_-]{22}$/;
  const TOKEN = /^[a-z_]{1,48}$/;
  const STAGES = new Set(['permission','identity','listing','source','recheck','final_identity']);
  const BUSY = new Set(['arming','beginning','reading','saving','unknown']);

  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exactKeys = (value, keys) => isObject(value) && Object.keys(value).sort().join(',') === keys.slice().sort().join(',');
  const lower = value => String(value).toLowerCase();

  // Mirrors the server's expected_identity: what the saved handle and link say the account is.
  // A problem means the account is held before any reading, never guessed.
  function deriveIdentity(account) {
    const provider = PROVIDERS[account.platform] || null;
    if (!provider) return {provider:null, handle:null, channelId:null, problem:null};
    const raw = String(account.handle || '').trim();
    const bare = raw.startsWith('@') ? raw.slice(1) : raw;
    const handles = new Set(), channels = new Set();
    let problem = null;
    if (provider === 'youtube' && YT_CHANNEL.test(bare)) channels.add(bare);
    else if (provider === 'x' && X_HANDLE.test(bare) && !X_RESERVED.has(lower(bare))) handles.add(bare);
    else if (provider === 'youtube' && YT_HANDLE.test(bare)) handles.add(bare);
    else problem = 'identity_invalid';
    if (account.url) {
      let key = null;
      try {
        const url = new URL(account.url);
        const host = url.hostname.toLowerCase().replace(/^www\./, '').replace(/^m\./, '');
        const path = url.pathname.replace(/\/+$/, '');
        const hostOk = provider === 'x' ? ['x.com','twitter.com'].includes(host) : host === 'youtube.com';
        if (url.protocol === 'https:' && hostOk && !url.port && !url.hash && !url.search && !url.username) {
          const match = provider === 'x' ? /^\/([A-Za-z0-9_]+)$/.exec(path) : /^\/(@[^/@]+|channel\/[^/]+)$/.exec(path);
          if (match) key = match[1];
        }
      } catch { key = null; }
      if (key === null) problem = problem || 'identity_invalid';
      else if (provider === 'youtube' && key.startsWith('channel/')) {
        if (YT_CHANNEL.test(key.slice(8))) channels.add(key.slice(8)); else problem = problem || 'identity_invalid';
      } else {
        const handle = key.replace(/^@/, '');
        if (provider === 'x' ? X_HANDLE.test(handle) && !X_RESERVED.has(lower(handle)) : YT_HANDLE.test(handle)) handles.add(handle);
        else problem = problem || 'identity_invalid';
      }
    }
    const distinctHandles = new Set([...handles].map(lower));
    if (distinctHandles.size > 1 || channels.size > 1) problem = problem || 'identity_conflict';
    if (!handles.size && !channels.size) problem = problem || 'identity_invalid';
    if (problem) return {provider, handle:null, channelId:null, problem};
    // A YouTube record that only knows its channel ID has no current handle to give the
    // reader. That needs the user's confirmation; a handle is never guessed.
    if (!handles.size) return {provider, handle:null, channelId:[...channels][0], problem:'identity_unconfirmed'};
    return {provider, handle:[...handles][0], channelId:channels.size ? [...channels][0] : null, problem:null};
  }

  function create(deps) {
    const doc = deps.document;
    const pageRoot = () => {
      try { return deps.location.origin === 'http://127.0.0.1:4317'; } catch { return false; }
    };
    const timers = {};
    const listeners = new Set();
    const client = {
      expected: EXPECTED,
      setup: {status:'checking', version:null, x:null, youtube:null, reason:null},
      check: null,
      lastRejection: null,
    };

    const changed = () => { for (const fn of listeners) { try { fn(); } catch { /* a view error never stops the protocol */ } } };
    const set = (name, ms, fn) => { clear(name); timers[name] = deps.setTimeout(fn, ms); };
    function clear(name) { if (timers[name] !== undefined) { deps.clearTimeout(timers[name]); delete timers[name]; } }
    const clearAll = () => ['begin','batch','request'].forEach(clear); // never the detection timer
    const hex32 = () => Array.from(deps.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
    const emit = (name, detail) => doc.dispatchEvent(new deps.CustomEvent(PREFIX + name, {detail}));
    const accounts = () => deps.getAccounts() || [];
    const accountNow = id => accounts().find(account => account.id === id);
    const sameSnapshot = (a, b) => !!a && !!b && SNAPSHOT.every(key => (a[key] ?? null) === (b[key] ?? null));
    const workspace = () => deps.getWorkspaceToken() || '';
    const sampleMode = () => !!deps.isSamples();

    // ---- recovery pointer: check ID and workspace token only -----------------------------
    function writePointer(checkId) {
      try { deps.storage.setItem(POINTER_KEY, JSON.stringify({check_id:checkId, workspace:workspace()})); } catch { /* no storage: no recovery */ }
    }
    function readPointer() {
      try {
        const value = JSON.parse(deps.storage.getItem(POINTER_KEY) || 'null');
        return exactKeys(value, ['check_id','workspace']) && ID32.test(value.check_id) && typeof value.workspace === 'string' ? value : null;
      } catch { return null; }
    }
    function clearPointer() { try { deps.storage.removeItem(POINTER_KEY); } catch { /* ignore */ } }

    // ---- detection and setup --------------------------------------------------------------
    function setSetup(next) { client.setup = Object.assign({version:null, x:null, youtube:null, reason:null}, next); changed(); }
    client.probe = () => {
      if (!pageRoot()) { setSetup({status:'blocked', reason:'wrong_page'}); return; }
      if (['absent', 'offline', 'blocked'].includes(client.setup.status)) setSetup({status:'checking'});
      emit('probe', {});
      set('detect', DETECT_MS, () => {
        if (client.setup.status === 'checking') setSetup({status:'absent'});
        else if (client.setup.status === 'connected') setSetup({status:'mismatch', reason:'version_unconfirmed'});
      });
    };
    client.openOptions = () => {
      if (sampleMode() || !pageRoot()) return false;
      emit('options', {});
      return true;
    };
    client.providerState = provider => {
      const value = client.setup[provider];
      return value === true ? 'ready' : value === false ? 'permission_needed' : 'unknown';
    };
    client.routeFor = account => {
      const identity = deriveIdentity(account || {});
      return identity.provider ? {kind:'reader', provider:identity.provider, identity} : {kind:'manual', provider:null, identity};
    };
    client.canStart = () => !sampleMode() && client.setup.status === 'installed' && !(client.check && BUSY.has(client.check.phase));
    client.busy = () => !!client.check && BUSY.has(client.check.phase);

    // ---- check lifecycle ------------------------------------------------------------------
    function entryFor(account) {
      const identity = deriveIdentity(account);
      const entry = {accountId:account.id, snapshot:Object.fromEntries(SNAPSHOT.map(key => [key, account[key] ?? null])), provider:identity.provider,
        handle:identity.handle, channelId:identity.channelId, kind:'read', requestId:null, state:'queued', stage:null, result:null, reason:null};
      if (!identity.provider) { entry.kind = 'unsupported'; entry.state = 'unsupported'; entry.reason = 'provider_not_supported'; }
      else if (identity.problem) { entry.kind = 'held'; entry.state = 'held'; entry.reason = identity.problem; entry.requestId = hex32(); }
      return entry;
    }

    function finish(phase, message, extra) {
      const check = client.check;
      if (!check) return;
      clearAll();
      Object.assign(check, extra || {}, {phase, message});
      changed();
    }
    // Everything unsaved is gone: no result, request or snapshot survives a discard.
    function discard(phase, message, {tellBridge = true, tellServer = true} = {}) {
      const check = client.check;
      if (!check) return;
      if (tellBridge && ['arming','beginning','reading'].includes(check.phase)) emit('cancel', {batchId:check.batchId});
      if (tellServer) cancelOnServer(check);
      for (const entry of check.entries) { entry.result = null; entry.state = entry.kind === 'read' ? 'discarded' : entry.state; }
      check.payload = null;
      check.readerOutcomes = null;
      clearPointer();
      finish(phase, message, {receipt:null, canRetry:false});
    }
    function cancelOnServer(check) {
      if (check.checkId) {
        const id = check.checkId;
        Promise.resolve().then(() => deps.request('/api/checks/cancel', 'POST', {check_id:id})).catch(() => {});
      }
    }

    // Returns {ok:false, message} or {ok:true}. Runs synchronously in the click task:
    // the bridge only honours arm inside the same task as the trusted Check click.
    client.start = accountIds => {
      if (sampleMode()) return {ok:false, message:'Sample data is never read from a real account. Clear the samples first.'};
      if (client.busy()) return {ok:false, message:'A check is already in progress.'};
      if (!pageRoot()) return {ok:false, message:'Open Resonance at http://127.0.0.1:4317/ in the browser that has the reader.', setup:true};
      if (client.setup.status !== 'installed') return {ok:false, message:'The browser reader is not ready. Open account-check setup first.', setup:true};
      const chosen = [];
      for (const id of accountIds || []) {
        const account = accountNow(id);
        if (account && !chosen.some(entry => entry.accountId === id)) chosen.push(entryFor(account));
      }
      if (!chosen.length) return {ok:false, message:'Choose an account that is saved.'};
      chosen.sort((a, b) => (a.kind === 'read' ? 0 : 1) - (b.kind === 'read' ? 0 : 1));
      // Never trim a selection silently: the person decides which accounts are left out.
      if (chosen.length > MAX_REQUESTS) {
        return {ok:false, message:`A check covers at most ${MAX_REQUESTS} accounts and ${chosen.length} were selected, so nothing was started. Check one account at a time, or choose a smaller selection.`};
      }
      const note = '';
      const entries = chosen;
      const seenIdentity = new Set(), seenChannel = new Set();
      for (const entry of entries) {
        if (entry.kind !== 'read') continue;
        // A denied provider permission is an explicit held entry, never a silent skip.
        if (client.setup[entry.provider] === false) {
          Object.assign(entry, {kind:'held', state:'held', reason:'permission_needed', requestId:hex32()});
          continue;
        }
        const identity = `${entry.provider}:${lower(entry.handle)}`;
        if (seenIdentity.has(identity) || (entry.channelId && seenChannel.has(entry.channelId))) {
          Object.assign(entry, {kind:'held', state:'held', reason:'identity_duplicate', requestId:hex32()});
          continue;
        }
        seenIdentity.add(identity);
        if (entry.channelId) seenChannel.add(entry.channelId);
        entry.requestId = hex32();
      }
      const requests = entries.filter(entry => entry.kind === 'read').map(entry => ({requestId:entry.requestId, provider:entry.provider, handle:entry.handle, channelId:entry.channelId}));
      if (!requests.length) {
        return {ok:false, message:entries.some(entry => entry.kind === 'held')
          ? 'None of the selected accounts can be read right now: a site permission is missing, or a saved handle or link does not identify the account. Open account-check setup or edit the account.'
          : 'Automatic checking is not available yet for these accounts. Paste text or use your assistant instead.'};
      }
      const check = {phase:'arming', batchId:hex32(), checkId:null, createdAt:null, entries, note, armed:false, rejected:null,
        message:'Preparing the check…', receipt:null, payload:null, canRetry:false, startedAt:deps.now()};
      client.check = check;
      client.lastRejection = null;
      // Same synchronous click task, before any await.
      emit('arm', {batchId:check.batchId, requests});
      if (client.check !== check) return {ok:false, message:check.message};
      if (check.rejected) {
        const reason = check.rejected;
        client.check = null;
        client.lastRejection = reason;
        changed();
        return {ok:false, message:rejectionText(reason), setup:reason === 'transport_unavailable' || reason === 'extension_context_invalidated'};
      }
      if (!check.armed) {
        client.check = null;
        setSetup({status:'absent'});
        return {ok:false, message:rejectionText('transport_unavailable'), setup:true};
      }
      check.phase = 'beginning';
      check.message = 'Reserving this check…';
      set('begin', BEGIN_MS, () => {
        if (client.check === check && check.phase === 'beginning') {
          discard('failed', 'The check took too long to start, so nothing was read. Try again.');
        }
      });
      deps.request('/api/checks/begin', 'POST', {account_ids:entries.map(entry => entry.accountId)}).then(
        job => onBegin(check, job), error => onBeginFailed(check, error));
      changed();
      return {ok:true, note};
    };

    function onBeginFailed(check, error) {
      if (client.check !== check || check.phase !== 'beginning') return;
      discard('failed', `The check could not start: ${error && error.message ? error.message : 'the workspace did not answer.'} Nothing was read.`, {tellServer:false});
    }
    function planMatches(check, job) {
      if (!isObject(job) || !ID32.test(job.check_id || '') || !Array.isArray(job.accounts) || job.accounts.length !== check.entries.length) return 'The check came back with different accounts. Nothing was read.';
      for (const entry of check.entries) {
        const row = job.accounts.find(item => isObject(item) && isObject(item.account) && item.account.id === entry.accountId);
        const now = accountNow(entry.accountId);
        if (!row || !now) return 'An account changed while the check was starting. Nothing was read.';
        if (!sameSnapshot(row.account, entry.snapshot) || !sameSnapshot(row.account, now)) return 'An account or its boundaries changed while the check was starting. Nothing was read.';
        if ((row.provider || null) !== entry.provider) return 'The platform for an account changed. Nothing was read.';
        if (entry.kind === 'read') {
          const expected = row.expected;
          if (row.expected_problem || !isObject(expected)) return 'The saved handle or link for an account does not identify it clearly. Nothing was read.';
          const handleOk = expected.handle === null || lower(expected.handle) === lower(entry.handle);
          const channelOk = expected.channel_id === null || expected.channel_id === entry.channelId;
          if (!handleOk || !channelOk) return 'The saved identity for an account does not match what was prepared. Nothing was read.';
        }
      }
      return '';
    }
    function onBegin(check, job) {
      const issued = isObject(job) && ID32.test(job.check_id || '') ? job.check_id : null;
      if (client.check !== check || check.phase !== 'beginning') {
        if (issued) Promise.resolve().then(() => deps.request('/api/checks/cancel', 'POST', {check_id:issued})).catch(() => {});
        return;
      }
      if (issued) check.checkId = issued;
      const problem = planMatches(check, job);
      if (problem) { discard('failed', problem); return; }
      clear('begin');
      check.createdAt = job.created_at || null;
      check.expiresAt = job.expires_at || null;
      check.phase = 'reading';
      check.message = 'Reading in your browser…';
      writePointer(check.checkId);
      set('batch', BATCH_MS, () => { if (client.check === check && check.phase === 'reading') discard('failed', 'The check took too long, so nothing was saved.'); });
      armRequestTimer(check);
      emit('start', {batchId:check.batchId});
      if (client.check === check) changed();
    }
    function nextEntry(check) { return check.entries.find(entry => entry.kind === 'read' && entry.state !== 'read'); }
    function armRequestTimer(check) {
      set('request', REQUEST_MS, () => { if (client.check === check && check.phase === 'reading') discard('failed', 'The reader stopped answering, so nothing was saved.'); });
    }

    // ---- bridge events --------------------------------------------------------------------
    const rejectionTexts = {
      gesture_required:'Use the Check button itself to start a check.',
      wrong_page:'Open Resonance at http://127.0.0.1:4317/ in the browser that has the reader.',
      invalid_input:'The reader could not accept this check. Nothing was read.',
      busy:'The reader is already busy with another check.',
      replayed_batch:'That check was already used. Start a new one.',
      expired:'The check was not confirmed in time, so nothing was read. Try again.',
      unknown_batch:'The reader no longer knows this check. Nothing was read.',
      transport_unavailable:'The browser reader is not available in this browser.',
      extension_context_invalidated:'The browser reader was updated or removed. Reload this page.',
    };
    function rejectionText(reason) { return rejectionTexts[reason] || 'The reader refused this check. Nothing was read.'; }

    const handlers = {
      ready() { if (client.setup.status !== 'installed') setSetup({status:'connected'}); },
      setup(detail) {
        const tri = value => value === null || typeof value === 'boolean';
        if (!exactKeys(detail, ['version','protocol','x','youtube']) || detail.protocol !== EXPECTED.protocol ||
            !tri(detail.x) || !tri(detail.youtube) || (detail.version !== null && typeof detail.version !== 'string')) { setSetup({status:'mismatch', reason:'setup_invalid'}); return; }
        clear('detect');
        const base = {version:detail.version, x:detail.x, youtube:detail.youtube};
        if (detail.version !== EXPECTED.version) setSetup(Object.assign(base, {status:'mismatch', reason:'version_mismatch'}));
        else setSetup(Object.assign(base, {status:'installed'}));
      },
      disconnected(detail) {
        if (!exactKeys(detail, ['reason','batchId'])) return;
        clear('detect');
        const check = client.check;
        setSetup({status:'offline', reason:typeof detail.reason === 'string' ? detail.reason : 'transport_disconnected'});
        if (check && ['arming','beginning','reading'].includes(check.phase)) {
          discard('failed', 'The browser reader disconnected, so the whole check was discarded. Nothing was saved. Start a new check.', {tellBridge:false});
        }
      },
      armed(detail) {
        const check = client.check;
        if (check && check.phase === 'arming' && exactKeys(detail, ['batchId']) && detail.batchId === check.batchId) check.armed = true;
      },
      rejected(detail) {
        if (!exactKeys(detail, ['batchId','reason']) || typeof detail.reason !== 'string') return;
        const check = client.check;
        if (!check || detail.batchId !== check.batchId) { client.lastRejection = detail.reason; changed(); return; }
        if (check.phase === 'arming') { check.rejected = detail.reason; return; }
        if (['beginning','reading'].includes(check.phase)) discard('failed', rejectionText(detail.reason), {tellBridge:false});
      },
      progress(detail) {
        const check = client.check;
        if (!check || check.phase !== 'reading' || !exactKeys(detail, ['batchId','requestId','provider','stage']) || detail.batchId !== check.batchId) return;
        const entry = nextEntry(check);
        if (!entry || entry.requestId !== detail.requestId || entry.provider !== detail.provider || !STAGES.has(detail.stage)) return;
        entry.state = 'reading';
        entry.stage = detail.stage;
        changed();
      },
      result(detail) {
        const check = client.check;
        if (!check || check.phase !== 'reading' || !exactKeys(detail, ['batchId','result']) || detail.batchId !== check.batchId) return;
        const result = detail.result;
        const entry = nextEntry(check);
        if (!entry || !isObject(result) || result.type !== 'RESULT' || result.mode !== 'live' || result.requestId !== entry.requestId ||
            result.provider !== entry.provider || !['ok','held','error'].includes(result.status) ||
            (result.status !== 'ok' && !TOKEN.test(result.reason || ''))) return; // Stale, early or malformed: ignored.
        entry.result = JSON.parse(JSON.stringify(result));
        entry.state = 'read';
        entry.stage = null;
        entry.outcome = result.status;
        entry.reason = result.status === 'ok' ? null : result.reason;
        armRequestTimer(check);
        changed();
      },
      done(detail) {
        const check = client.check;
        if (!check || check.phase !== 'reading' || !exactKeys(detail, ['batchId']) || detail.batchId !== check.batchId) return;
        if (nextEntry(check)) { discard('failed', 'The reader finished without answering for every account, so nothing was saved. Start a new check.', {tellBridge:false}); return; }
        beginSave(check);
      },
      cancelled(detail) {
        const check = client.check;
        if (!check || !exactKeys(detail, ['batchId']) || detail.batchId !== check.batchId || !['arming','beginning','reading'].includes(check.phase)) return;
        discard('cancelled', 'The check was cancelled. Nothing was read into your workspace and nothing was saved.', {tellBridge:false});
      },
    };
    client.attach = () => {
      for (const name of Object.keys(handlers)) {
        doc.addEventListener(PREFIX + name, event => {
          if (event.target !== doc) return;
          handlers[name](event.detail);
        });
      }
    };
    client.handle = (name, detail) => { if (Object.hasOwn(handlers, name)) handlers[name](detail); };

    client.cancel = () => {
      const check = client.check;
      if (!check || !['arming','beginning','reading'].includes(check.phase)) return false;
      discard('cancelled', 'The check was cancelled. Nothing was saved.');
      return true;
    };

    // ---- saving ---------------------------------------------------------------------------
    function buildPayload(check) {
      return {check_id:check.checkId, reader_version:EXPECTED.version, completed_at:new Date(deps.now()).toISOString(), accounts:check.entries.map(entry => {
        if (entry.kind === 'unsupported') return {account_id:entry.accountId, status:'unsupported', result:{reason:'provider_not_supported'}};
        if (entry.kind === 'held') return {account_id:entry.accountId, status:'held', result:{type:'RESULT', requestId:entry.requestId, provider:entry.provider, mode:'live', status:'held', tabs:[], reason:entry.reason}};
        return {account_id:entry.accountId, status:entry.result.status, result:entry.result};
      })};
    }
    function beginSave(check) {
      for (const entry of check.entries) {
        if (!sameSnapshot(entry.snapshot, accountNow(entry.accountId))) {
          discard('failed', 'An account or its boundaries changed during the check, so nothing was saved. Start a new check.', {tellBridge:false});
          return;
        }
      }
      clear('request'); clear('batch');
      check.payload = buildPayload(check);
      check.phase = 'saving';
      check.message = 'Saving…';
      changed();
      send(check);
    }
    function send(check) {
      check.phase = 'saving';
      check.canRetry = false;
      check.message = 'Saving…';
      changed();
      deps.request('/api/checks', 'POST', check.payload).then(
        response => onSaved(check, response),
        error => onSaveFailed(check, error));
    }
    function accept(check, receipt, replayed) {
      if (client.check !== check) return;
      clearPointer();
      check.payload = null;
      for (const entry of check.entries) entry.result = null;
      finish('saved', replayed ? 'This check was already saved. Here is the saved receipt.' : 'Saved.', {receipt, canRetry:false});
      Promise.resolve(deps.onSaved && deps.onSaved(receipt)).catch(() => {});
    }
    function receiptShape(value, checkId) {
      return isObject(value) && value.id === checkId && Array.isArray(value.accounts) && isObject(value.totals);
    }
    function onSaved(check, response) {
      if (client.check !== check || check.phase !== 'saving') return;
      if (isObject(response) && receiptShape(response.receipt, check.checkId)) accept(check, response.receipt, response.replayed === true);
      else onSaveFailed(check, new Error('The workspace answered, but not with a receipt.'));
    }
    // Any failure leaves the outcome unknown until the original receipt endpoint answers.
    function onSaveFailed(check, error) {
      if (client.check !== check || check.phase !== 'saving') return;
      check.refusal = error && Number.isInteger(error.status) && error.status >= 400 && error.status < 500 ? (error.message || 'refused') : null;
      check.phase = 'unknown';
      check.canRetry = false;
      check.message = 'Save status unknown. Checking whether it was saved…';
      changed();
      lookup(check);
    }
    function lookup(check) {
      if (client.check !== check) return Promise.resolve();
      check.phase = 'unknown';
      check.looking = true;
      changed();
      return deps.request(`/api/checks/${check.checkId}`, 'GET').then(response => {
        if (client.check !== check) return;
        check.looking = false;
        if (receiptShape(response, check.checkId)) { accept(check, response, true); return; }
        if (isObject(response) && response.status === 'pending') {
          if (check.refusal) { discard('failed', `Nothing was saved: ${check.refusal} Start a new check.`); return; }
          check.canRetry = true;
          check.message = 'Save status unknown. The check was not saved yet, so you can retry the same save.';
          changed();
          return;
        }
        check.canRetry = false;
        check.message = 'Save status unknown. The workspace gave an answer that could not be read.';
        changed();
      }, error => {
        if (client.check !== check) return;
        check.looking = false;
        if (error && error.status === 404) { discard('discarded', 'This check expired before it was saved, so the unsaved result was discarded. Start a new check.', {tellServer:false}); return; }
        check.canRetry = false;
        check.message = 'Save status unknown. The workspace could not be reached, so nothing is claimed. Check again when it is back.';
        changed();
      });
    }
    client.retrySave = () => {
      const check = client.check;
      if (!check || check.phase !== 'unknown' || !check.canRetry || !check.payload) return false;
      send(check);
      return true;
    };
    client.checkSaveStatus = () => {
      const check = client.check;
      if (!check || check.phase !== 'unknown' || check.looking) return Promise.resolve(false);
      return lookup(check).then(() => true);
    };
    client.dismiss = () => {
      const check = client.check;
      if (!check || BUSY.has(check.phase)) return false;
      client.check = null;
      changed();
      return true;
    };

    // ---- workspace and account changes ----------------------------------------------------
    client.revalidate = () => {
      const check = client.check;
      if (!check || !['arming','beginning','reading'].includes(check.phase)) return;
      if (check.entries.some(entry => !sameSnapshot(entry.snapshot, accountNow(entry.accountId)))) {
        discard('failed', 'An account or its boundaries changed, so the check was stopped and nothing was saved. Start a new check.');
      }
    };
    client.reset = () => {
      const check = client.check;
      if (check && ['arming','beginning','reading'].includes(check.phase)) discard('cancelled', 'The workspace changed, so the check was discarded.');
      else if (check) { client.check = null; check.payload = null; clearAll(); }
      clearPointer();
      changed();
    };
    // After a reload: look up what happened to a check this tab started. Never resume reading.
    client.recover = () => {
      const pointer = readPointer();
      if (!pointer || client.check) return Promise.resolve(null);
      if (pointer.workspace !== workspace()) { clearPointer(); return Promise.resolve(null); }
      return deps.request(`/api/checks/${pointer.check_id}`, 'GET').then(response => {
        if (receiptShape(response, pointer.check_id)) {
          clearPointer();
          client.check = {phase:'saved', batchId:null, checkId:pointer.check_id, entries:[], note:'', receipt:response, message:'This check was saved before the page reloaded.', recovered:true, canRetry:false};
          changed();
          Promise.resolve(deps.onSaved && deps.onSaved(response)).catch(() => {});
          return 'saved';
        }
        if (isObject(response) && response.status === 'pending') {
          // The unsaved result lived only in the old page. It cannot be rebuilt.
          return deps.request('/api/checks/cancel', 'POST', {check_id:pointer.check_id}).catch(() => {}).then(() => {
            clearPointer();
            client.check = {phase:'discarded', batchId:null, checkId:pointer.check_id, entries:[], note:'', receipt:null, message:'The page reloaded before a check finished, so the unsaved result was discarded. Click Check now to start a fresh one.', recovered:true, canRetry:false};
            changed();
            return 'discarded';
          });
        }
        return null;
      }, error => {
        if (error && error.status === 404) { clearPointer(); return null; }
        return null; // Unreachable now: keep the pointer for the next load.
      });
    };

    client.subscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };
    return client;
  }

  globalThis.ResonanceReaderClient = Object.freeze({create, deriveIdentity, EXPECTED, PREFIX, BRIDGE_ROOT, MAX_REQUESTS});
})();
