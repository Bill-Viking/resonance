'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icons = {
    overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    accounts:'<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 4v2"/>',
    review:'<path d="M8 4H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3"/><rect x="8" y="2" width="8" height="4" rx="1"/><path d="m7 13 3 3 7-7"/>',
    draft:'<path d="m16 3 5 5-12 12-6 1 1-6Z"/><path d="m13 6 5 5"/>',
    mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/>',
    monitoring:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2M3 12h2m14 0h2M12 3v2m0 14v2"/>',
    settings:'<path d="m9 3 1-1h4l1 1 .5 2 2 .8 2-.5 2 3-1.5 1.7v2l1.5 1.7-2 3-2-.5-2 .8L15 21l-1 1h-4l-1-1-.5-2-2-.8-2 .5-2-3L4 13v-2L2.5 9.3l2-3 2 .5 2-.8Z"/><circle cx="12" cy="12" r="3"/>',
    arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',
    external:'<path d="M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5"/>',
    upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    shield:'<path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Z"/><path d="m8 12 3 3 5-6"/>',
    sparkle:'<path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3Z"/>',
    warning:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    undo:'<path d="M3 10h10a7 7 0 0 1 0 14M3 10l5-5M3 10l5 5"/>',
    youtube:'<rect x="2" y="5" width="20" height="14" rx="4"/><path d="m10 9 5 3-5 3Z"/>',
  };
  const icon = (name, cls = '') => `<svg ${cls ? `class="${cls}"` : ''} viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.info}</svg>`;
  const platforms = ['YouTube','Facebook','Instagram','X','Reddit','TikTok','LinkedIn','Discord','Substack','Gmail','Outlook'];
  const WELCOME_PITCH = 'Resonance is a private notebook of what you have said online. Your own AI assistant does the reading and writes what it finds here — this page never touches your accounts.';
  const platformMarks = {YouTube:icon('youtube'), Facebook:'f', Instagram:'◎', X:'𝕏', Reddit:'r', TikTok:'♪', LinkedIn:'in', Discord:'D', Substack:'S', Gmail:'M', Outlook:'O'};
  const platformIcon = account => `<span class="platform-icon" aria-hidden="true">${platformMarks[account?.platform] || '·'}</span>`;
  const nameOf = account => account ? (account.display_name || account.handle || account.platform) : 'Unknown account';
  const handleOf = account => account?.handle || '';
  const kindLabel = {post:'Post',comment:'Comment',reply:'Reply',sample:'Sample'};
  const severityLabel = {attention:'Worth attention',suggestion:'Tone suggestion',strength:'Working well'};
  const statusLabel = {open:'Open',resolved:'Resolved',snoozed:'Snoozed'};
  const requestActionLabel = {keep:'Keep',edit:'Edit',delete:'Delete'};
  const requestStatusLabel = {pending:'Pending',completed:'Completed',cancelled:'Cancelled'};
  const sourceStatusLabel = {needs_you:'Needs you',waiting:'Waiting',verification_pending:'Verification pending',kept:'Kept',verified:'Verified',snoozed:'Snoozed',checked:'Checked',resolved:'Resolved'};
  const boundaryRuleLabel = {'sfw-profanity':'Strong language','sfw-explicit':'Explicit language','sfw-graphic':'Graphic violence','targeted-threat':'Threatening language','group-attack':'Attack on a group','personal-attack':'Personal attack','self-criticism':'Harsh self-label','sweeping-claim':'Absolute language','heated-punctuation':'Repeated punctuation',sfw:'SFW boundary',risk:'Safety concern',tone:'Tone and clarity'};
  const identityLabel = {named:'Real name',alias:'Alias',unclassified:'Not set'};
  const daringLabel = {careful:'Careful',balanced:'Balanced',bold:'Bold'};
  // One table drives the Appearance choices and the applied theme. color = the browser chrome colour.
  const THEMES = {
    dark:{label:'Amber terminal',color:'#080b0d',scheme:'dark'},
    light:{label:'Paper',color:'#f2f3f0',scheme:'light'},
    system:{label:'Match this device',color:'',scheme:'system'},
    phosphor:{label:'Green phosphor',color:'#050806',scheme:'dark'},
    midnight:{label:'Midnight blue',color:'#080e1c',scheme:'dark'},
  };
  // Retraction wording offered when a post is withdrawn. The user's own text always wins.
  const RETRACTIONS = [
    'NOTICE: This comment has been withdrawn by its author following a routine self-audit. Original sentiment deprecated. No further action is required. Have a compliant day.',
    'ERROR 410: Opinion no longer supported. The author has rolled back to a previous stable version of himself.',
    'This post has been replaced by an automated retraction. The original take failed quality assurance and was returned to the manufacturer.',
    "SYSTEM MESSAGE: Upon review, the author's earlier remarks were found to be running outdated firmware. A patch has been applied. Thank you for your patience.",
    '[RETRACTED] Statement recalled by its author. Reason code 7: seemed like a good idea at the time.',
  ];
  const state = {data:null,view:'overview',connected:false,lastLoaded:null,monitorRun:'',monitorSignature:'',communicationSignature:'',sourceSignature:'',refreshPending:false,communicationRefreshPending:false,sourceRefreshPending:false,filters:{account:'all',severity:'all',status:'needs_you',query:'',type:'all'},accountQuery:'',mailQuery:'',mailAccount:'all',draft:'',draftAccount:'',draftResult:null,draftBusy:false,draftError:'',draftSequence:0,fixDraft:null,retraction:null,followUpDrafts:{},followUpKey:null};
  let toastTimer;
  let draftTimer;
  let dialogReturnFocus;
  let importPayload = null;
  let stateReadSequence = 0;
  let monitoringRefreshBusy = false;
  let accountWalk = null;
  const dialog = $('#dialog');
  const accountById = id => state.data?.accounts.find(account => account.id === id);
  const reviewsFor = id => (state.data?.reviews || []).filter(review => review.account_id === id);
  const flagsFor = id => (state.data?.policy_flags || []).filter(flag => flag.account_id === id);
  const allFlags = () => state.data?.policy_flags || [];
  const openReviews = () => (state.data?.reviews || []).filter(review => review.status === 'open');
  const openFlags = () => allFlags().filter(flag => flag.status === 'open');
  const actionRequests = () => Array.isArray(state.data?.action_requests) ? state.data.action_requests : [];
  const sourceItems = () => Array.isArray(state.data?.source_items) ? state.data.source_items : [];
  const attestations = () => Array.isArray(state.data?.attestations) ? state.data.attestations : [];
  const sourceItemById = id => sourceItems().find(item => item.id === id);
  const sourceFilterFor = item => item?.status === 'needs_you' ? 'needs_you' : item?.status === 'waiting' ? 'waiting' : item?.status === 'verification_pending' ? 'verification_pending' : item?.status === 'snoozed' ? 'snoozed' : ['kept','verified','checked','resolved'].includes(item?.status) ? 'history' : 'all';
  const requestForReview = reviewId => actionRequests().filter(request => request.review_id === reviewId).sort((a,b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0));
  const pendingRequestFor = reviewId => requestForReview(reviewId).find(request => request.status === 'pending');
  const requestById = id => actionRequests().find(request => request.id === id);
  const activeRequestForSource = item => requestById(item?.active_request_id) || actionRequests().find(request => request.source_key && request.source_key === item?.source_key && ['queued','running','needs_verification','blocked','reopened'].includes(request.execution_state));
  const reviewById = id => state.data?.reviews.find(review => review.id === id);
  const flagCount = id => flagsFor(id).filter(flag => flag.status === 'open').length;
  const communication = () => state.data?.communication || {};
  const communicationList = key => Array.isArray(communication()[key]) ? communication()[key] : [];
  const accessFor = id => communicationList('access').find(item => item.account_id === id);
  const syncFor = id => communicationList('mail_syncs').filter(item => item.account_id === id).sort((a,b) => new Date(b.checked_at || 0) - new Date(a.checked_at || 0))[0];
  const setupRequestsFor = id => communicationList('setup_requests').filter(item => item.account_id === id).sort((a,b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0));
  const mailAccount = account => ['Gmail','Outlook'].includes(account?.platform);
  const draftForMessage = id => communicationList('drafts').find(item => item.message_id === id);
  function dateText(value, withTime = false) {
    if (!value || Number.isNaN(new Date(value).getTime())) return 'Not recorded';
    return new Date(value).toLocaleDateString(undefined, {month:'short',day:'numeric',year:'numeric',...(withTime ? {hour:'numeric',minute:'2-digit'} : {})});
  }
  function shortDate(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return 'Undated';
    return new Date(value).toLocaleDateString(undefined, {month:'short',day:'numeric'});
  }
  function relativeDate(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return 'Not recorded';
    const days = Math.floor((Date.now() - new Date(value).getTime()) / 86400000);
    if (days < 0) return shortDate(value);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7) return `${days} days ago`;
    return shortDate(value);
  }
  function safeUrl(value) {
    if (typeof value !== 'string' || !value) return '';
    try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) ? url.href : ''; } catch { return ''; }
  }
  function sourceLink(value, label = 'Open original') {
    const url = safeUrl(value);
    if (url && showingSamples()) return `<span class="sample-link-note">Sample original · no platform is opened</span>`;
    return url ? `<a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ${icon('external')}</a>` : '';
  }
  function badge(severity, label) {
    return `<span class="badge ${esc(severity)}">${icon(severity === 'attention' ? 'warning' : severity === 'strength' ? 'check' : 'info')}${esc(label || severityLabel[severity] || severity)}</span>`;
  }
  function toast(message) {
    clearTimeout(toastTimer);
    $('#toast').textContent = message;
    $('#toast').hidden = false;
    toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4800);
  }
  async function api(path, method = 'GET', body) {
    let response;
    try {
      response = await fetch(path, {method,credentials:'same-origin',cache:'no-store',headers:{...(body === undefined ? {} : {'Content-Type':'application/json'}),...(state.data?.workspace?.token ? {'X-Resonance-Workspace':state.data.workspace.token} : {})},...(body === undefined ? {} : {body:JSON.stringify(body)})});
    } catch { throw new Error('The local workspace is unavailable. Start Resonance on this computer, then try again.'); }
    let result;
    try { result = await response.json(); } catch { throw new Error('The workspace returned an unreadable response. Please try again.'); }
    if (!response.ok) throw new Error(result.error || 'That change could not be saved. Please try again.');
    return result;
  }
  const showingSamples = () => state.data?.workspace?.mode === 'samples';
  const downloadAttributes = path => showingSamples() ? 'aria-disabled="true" title="Clear samples to export your own data"' : `href="${esc(downloadPath(path))}" download`;
  const downloadPath = path => `${path}?workspace=${encodeURIComponent(state.data?.workspace?.token || '')}`;
  function resetWorkspaceEditors() {
    clearTimeout(toastTimer);$('#toast').hidden = true;
    clearTimeout(draftTimer);
    state.draftSequence++;
    state.draft = '';state.draftAccount = '';state.draftResult = null;state.draftError = '';state.draftBusy = false;
    state.followUpDrafts = {};state.followUpKey = null;
    // Practice drafts must disappear with their workspace; real drafts stay local.
    try {
      for (let index = sessionStorage.length - 1; index >= 0; index--) {
        const key = sessionStorage.key(index);
        if (key?.startsWith('resonance:practice-draft:')) sessionStorage.removeItem(key);
      }
    } catch {}
    state.fixDraft = null;state.retraction = null;accountWalk = null;importPayload = null;
    closeDialogIfOpen();
  }
  function renderSampleBanner() {
    const banner = $('#sample-banner');
    if (!banner) return;
    banner.hidden = !showingSamples();
    const queued = actionRequests().find(request => request.execution_state === 'queued' && (request.can_prepare ?? request.can_execute));
    banner.innerHTML = showingSamples() ? `<strong>Sample data — nothing here is yours.</strong><div>${queued ? `<button class="button quiet small" data-action="play-sample" data-id="${esc(queued.id)}">Play the assistant’s next check</button>` : ''}<button class="text-button" data-action="clear-samples">Clear samples</button></div>` : '';
  }
  async function switchSamples(button, clear = false) {
    button.disabled = true;
    try {
      await api(clear ? '/api/samples/clear' : '/api/samples','POST',{});
      resetWorkspaceEditors();
      if (!await reload()) throw new Error('The workspace changed. Refresh to see it before continuing.');
      navigate(clear ? 'overview' : 'review',clear ? undefined : {status:'all'});
    } finally {if (button.isConnected) button.disabled = false;}
  }
  function playSample(id) {
    if (!showingSamples()) return;
    const request = requestById(id);
    if (!(request?.can_prepare ?? request?.can_execute) || request.execution_state !== 'queued') {toast('Choose a queued sample request first.');return;}
    const action = request.action === 'delete' ? 'delete' : 'edit';
    openDialog(dialogShell(`Your assistant is about to ${action} this sample post.`,'A practice check; nothing real will change.',`<blockquote class="review-excerpt">${esc(request.original_excerpt)}</blockquote>${action === 'edit' ? `<div class="detail-label">THE NEW WORDS</div><blockquote class="review-excerpt">${esc(request.proposed_text)}</blockquote>` : ''}<p>With your real posts your assistant asks you this in your chat with it, not here.</p>`,`<button class="button quiet" data-action="sample-decision" data-id="${esc(id)}" data-decision="not_now">Not now</button><button class="button primary" data-action="sample-decision" data-id="${esc(id)}" data-decision="yes">Yes, ${action}</button>`));
  }
  async function sampleDecision(button) {
    if (!showingSamples()) return;
    button.disabled = true;
    try {
      const result = await api('/api/samples/rehearse','POST',{request_id:button.dataset.id,decision:button.dataset.decision});
      if (!await reload()) throw new Error('The practice result was saved. Refresh to see it.');
      closeDialog();
      navigate('review',{status:button.dataset.decision === 'yes' ? 'history' : 'needs_you'});
      toast(result.sample_proof);
    } catch(error) {dialogError(error);}
    finally {if (button.isConnected) button.disabled = false;}
  }
  function applyTheme() {
    const saved = state.data?.settings.theme || 'dark';
    const chosen = THEMES[saved] ? saved : 'dark';
    const applied = chosen === 'system' ? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : chosen;
    document.documentElement.dataset.theme = applied;
    $('meta[name="theme-color"]').content = THEMES[applied].color;
  }
  function showConnection() {
    const notice = $('#connection-notice');
    notice.hidden = state.connected;
    if (!state.connected) notice.innerHTML = `<span>${state.data ? `Workspace unavailable. Showing the last loaded view from ${esc(dateText(state.lastLoaded,true))}. Changes need the local app.` : 'Start the Resonance app on this computer to open your saved workspace.'}</span><button class="text-button" data-action="reload">Try again ${icon('arrow')}</button>`;
  }
  async function reload({renderView = true} = {}) {
    const sequence = ++stateReadSequence;
    try {
      const data = await api('/api/state');
      if (sequence !== stateReadSequence) return false;
      if (!Array.isArray(data.accounts) || !Array.isArray(data.reviews) || !data.settings) throw new Error('The workspace data is incomplete. Please try again.');
      if (state.data?.workspace?.token && state.data.workspace.token !== data.workspace?.token) resetWorkspaceEditors();
      state.data = data;
      state.connected = true;
      state.lastLoaded = new Date().toISOString();
      applyTheme();
      showConnection();
      if (renderView) render();
      return true;
    } catch (error) {
      if (sequence !== stateReadSequence) return false;
      state.connected = false;
      showConnection();
      if (!state.data) $('#main').innerHTML = `<div class="empty-state panel"><div class="empty-icon">${icon('info')}</div><h1>Your workspace is waiting.</h1><p>${esc(error.message)}</p><button class="button primary" data-action="reload">Try again ${icon('arrow')}</button></div>`;
      return false;
    }
  }
  const navNames = {overview:'Today',mail:'Mail',review:'Posts',accounts:'Accounts',monitoring:'Checks',draft:'Compose',setup:'Setup'};
  function renderNav() {
    const count = sourceItems().filter(item => item.needs_attention).length;
    document.querySelectorAll('[data-nav]').forEach(link => {
      const key = link.dataset.nav;
      link.innerHTML = `${icon(key)}<span>${navNames[key]}</span>${key === 'review' && count ? `<span class="nav-count">${count}</span>` : ''}`;
      if (state.view === key) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
    });
    $('#current-view').textContent = navNames[state.view];
    $('#profile-button').textContent = (state.data?.settings.display_name?.trim().charAt(0) || 'S').toUpperCase();
    $('.settings-icon').innerHTML = icon('settings');
    document.title = `${navNames[state.view]} · Resonance`;
  }
  function render() {
    if (!state.data) return;
    renderNav();
    renderSampleBanner();
    $('#main').innerHTML = ({overview:renderOverview,mail:renderMail,review:renderReview,accounts:renderAccounts,monitoring:renderMonitoring,draft:renderDraft,setup:renderSetup})[state.view]();
    state.communicationSignature = communicationSignature();
    state.sourceSignature = sourceStateSignature();
    if (state.view === 'draft') updateDraftResult();
    if (['overview','monitoring'].includes(state.view)) {
      state.monitorSignature = monitoringSignature();
      state.refreshPending = false;
    }
  }
  function navigate(view, filter) {
    if (filter) Object.assign(state.filters, {account:'all',severity:'all',status:'needs_you',query:'',type:'all'}, filter);
    const target = navNames[view] ? view : 'overview';
    if (location.hash === `#${target}`) { state.view = target; render(); } else location.hash = target;
    window.scrollTo({top:0,behavior:'instant'});
  }
  function heading(title, subtitle, actions = '', eyebrow = '') {
    return `<div class="page-heading"><div>${eyebrow ? `<div class="eyebrow">${esc(eyebrow)}</div>` : ''}<h1>${esc(title)}</h1><p class="subheading">${esc(subtitle)}</p></div>${actions ? `<div class="heading-actions">${actions}</div>` : ''}</div>`;
  }
  function emptyState(title, text, action = '', symbol = 'review') {
    return `<div class="empty-state panel"><div class="empty-icon">${icon(symbol)}</div><h2>${esc(title)}</h2><p>${esc(text)}</p>${action}</div>`;
  }
  const addAccountButton = (label = 'Add account') => `<button class="button primary" data-action="add-account">${icon('plus')}${esc(label)}</button>`;
  const addReviewButton = () => `<button class="button quiet" data-action="add-review">${icon('plus')}Add feedback</button><button class="button primary" data-action="add-excerpt">${icon('review')}Check a post</button>`;
  function todaySourceBuckets() {
    const buckets = {needs_you:[],waiting:[],verification_pending:[]}, seen = new Set();
    sourceItems().forEach(item => {
      if (!item?.id || seen.has(item.id) || !buckets[item.status]) return;
      seen.add(item.id);
      buckets[item.status].push(item);
    });
    return buckets;
  }
  function todaySummary() {
    const buckets = todaySourceBuckets();
    return `<section class="today-summary terminal-panel" aria-label="Today at a glance"><button data-action="review-filter" data-status="needs_you"><span>Needs you</span><strong>${buckets.needs_you.length}</strong></button><button data-action="review-filter" data-status="waiting"><span>Waiting</span><strong>${buckets.waiting.length}</strong></button><button data-action="review-filter" data-status="verification_pending"><span>Verification</span><strong>${buckets.verification_pending.length}</strong></button><button data-action="navigate" data-view="mail"><span>Saved mail</span><strong>${communicationList('messages').length}</strong></button></section>`;
  }
  function todayReportContent() {
    const run = monitorRuns()[0];
    if (!run) return `<div class="today-report-empty"><div><strong>No result recorded.</strong><span>${state.data.monitoring ? 'Ask your assistant for the first check; its report will appear here.' : 'Check history is unavailable in the current saved state.'}</span></div><div class="today-report-timing"><span>Report date · not recorded</span><span>${esc(nextScheduledCheckText())}</span></div><button class="text-button" data-action="navigate" data-view="monitoring">Check history ${icon('arrow')}</button></div>`;
    const totals = monitorTotals(run);
    const reportDate = run.completed_at || run.started_at;
    const completion = run.completed_at ? 'Completed' : 'Completion not recorded';
    return `<div class="today-report-meta"><time datetime="${esc(reportDate)}" aria-label="Report date ${esc(dateText(reportDate,true))}">${esc(dateText(reportDate,true))}</time><span>${esc(completion)}</span></div><p class="today-report-summary">${esc(trimText(run.summary || 'No summary was recorded for this report.',420))}</p>${coverageCounts(run,'today-report-counts')}<div class="today-report-footer"><span>${esc(nextScheduledCheckText())}</span><button class="text-button" data-action="latest-check">Full report ${icon('arrow')}</button></div>`;
  }
  function todayReport() {
    return `<section class="today-report terminal-panel"><div class="section-heading"><div><div class="eyebrow">LATEST SAVED REPORT</div><h2>Latest saved check</h2></div></div><div id="today-report-content">${todayReportContent()}</div></section>`;
  }
  function todayNeedsYou() {
    const items = todaySourceBuckets().needs_you, previews = items.slice(0,3);
    return `<div class="section-heading"><div><h2>Needs you <button class="section-count count-button" data-action="review-filter" data-status="needs_you" aria-label="Open needs-you posts">${items.length}</button></h2><p>Saved sources that need a decision.</p></div><button class="text-button" data-action="review-filter" data-status="needs_you">All needs-you posts ${icon('arrow')}</button></div>${previews.length ? `<div class="review-stack">${previews.map(item => sourceCard(item,true)).join('')}</div>${items.length > previews.length ? `<p class="preview-limit">Showing ${previews.length} of ${items.length}. Posts has the complete list.</p>` : ''}` : `<div class="today-empty"><strong>No needs-you items recorded.</strong><p>This describes the saved source evidence. It is not an account audit. Check a post to add something you want to consider.</p><button class="text-button" data-action="add-excerpt">Check a post</button></div>`}`;
  }
  function todayWaiting() {
    const {waiting,verification_pending:verification} = todaySourceBuckets();
    const group = (title,copy,status,items) => `<section class="today-work-group"><div class="today-work-heading"><div><h3>${esc(title)} <button class="section-count count-button" data-action="review-filter" data-status="${esc(status)}" aria-label="Open ${esc(title)} posts">${items.length}</button></h3><p>${esc(copy)}</p></div><button class="text-button" data-action="review-filter" data-status="${esc(status)}">View all ${icon('arrow')}</button></div>${items.length ? `<div class="review-stack compact">${items.slice(0,2).map(item => sourceCard(item,true)).join('')}</div>${items.length > 2 ? `<p class="preview-limit">Showing 2 of ${items.length}. Posts has the complete list.</p>` : ''}` : `<p class="today-empty-inline">No ${status === 'waiting' ? 'waiting work' : 'verification work'} recorded. Open Posts to choose your next move.</p><button class="text-button" data-action="navigate" data-view="review">Open Posts</button>`}</section>`;
    return group('Waiting','Queued or running assistant work. A schedule is not a completion promise.','waiting',waiting) + group('Verification','Completed-by-you or uncertain attempts still awaiting evidence.','verification_pending',verification);
  }
  function todayMail() {
    const messages = communicationList('messages').slice().sort((a,b) => new Date(b.received_at || 0) - new Date(a.received_at || 0));
    const previews = messages.slice(0,3);
    if (!previews.length) return `<div class="today-empty"><strong>No saved mail previews.</strong><p>Mailbox profiles alone do not provide inbox access. Ask your assistant to bring in a mail preview.</p><button class="text-button" data-action="connect-assistant">Connect your assistant</button></div>`;
    return `<div class="today-mail-list">${previews.map(message => { const account = accountById(message.account_id), draft = draftForMessage(message.id); return `<button class="today-mail-row" data-action="mail-message" data-id="${esc(message.id)}"><span class="today-mail-meta"><strong>${esc(message.sender || 'Sender not recorded')}</strong><time datetime="${esc(message.received_at)}">Received ${esc(dateText(message.received_at,true))}</time></span><span class="today-mail-subject">${esc(message.subject || 'No subject')}</span><span class="today-mail-preview">${esc(trimText(message.preview || 'No preview was saved.',120))}</span><span class="today-mail-account">${esc(account?.platform || 'Mail account')} · ${esc(handleOf(account) || 'Mailbox not recorded')} · Saved ${esc(dateText(message.imported_at || message.received_at,true))}${draft ? ' · Draft saved' : ''}</span></button>`; }).join('')}</div>${messages.length > previews.length ? `<p class="preview-limit">Showing ${previews.length} of ${messages.length} saved previews.</p>` : ''}`;
  }
  function todayAccountAttention() {
    const items = state.data.accounts.filter(account => accessState(account).key !== 'verified' || !account.identity_mode || account.identity_mode === 'unclassified');
    if (!items.length) return `<div class="today-empty"><strong>No setup item recorded in current account evidence.</strong><p>This does not mean every account was audited or is currently reachable. Open Accounts to review the saved evidence.</p><button class="text-button" data-action="navigate" data-view="accounts">Open Accounts</button></div>`;
    return `<div class="today-access-list">${items.slice(0,4).map(account => { const access = accessState(account), unset = !account.identity_mode || account.identity_mode === 'unclassified'; return `<button class="today-access-row" data-action="setup-account" data-id="${esc(account.id)}"><span>${platformIcon(account)}<span><strong>${esc(account.platform)} · ${esc(handleOf(account) || 'Identity not recorded')}</strong>${account.display_name && account.display_name !== account.handle ? `<small>${esc(account.display_name)}</small>` : ''}</span></span><span><em>${esc(access.label)}${access.record?.checked_at ? ` · ${esc(relativeDate(access.record.checked_at))}` : ''}</em>${unset ? '<small>Boundaries not set</small>' : ''}</span></button>`; }).join('')}</div>${items.length > 4 ? `<p class="preview-limit">Showing 4 of ${items.length} setup items.</p>` : ''}`;
  }
  function renderOverview() {
    if (!state.data.accounts.length && !state.data.reviews.length && !sourceItems().length && !monitorRuns().length && !communicationList('messages').length) return renderWelcome();
    const top = heading('Today.','Saved evidence. Clear next steps.', `<button class="button quiet" data-action="navigate" data-view="accounts">${icon('accounts')}Accounts</button><button class="button primary" data-action="add-excerpt">${icon('review')}Check a post</button><a class="button quiet" ${downloadAttributes('/api/brief')}>${icon('download')}<span class="desktop-label">Export brief</span><span class="sr-only">Download brief</span></a>`, new Date().toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'}));
    return top + todayReport() + `<div id="today-summary-content">${todaySummary()}</div><div class="today-grid"><div class="today-main"><section class="terminal-panel" id="today-needs-you"><div id="today-needs-you-content">${todayNeedsYou()}</div></section></div><aside class="today-side"><section class="terminal-panel" id="today-waiting"><div id="today-waiting-content">${todayWaiting()}</div></section><section class="terminal-panel" id="today-mail"><div class="section-heading"><div><h2>Saved mail <button class="section-count count-button" data-action="navigate" data-view="mail" aria-label="Open saved mail" id="today-mail-count">${communicationList('messages').length}</button></h2><p>Local previews and reply drafts.</p></div><button class="text-button" data-action="navigate" data-view="mail">Open Mail ${icon('arrow')}</button></div><div id="today-mail-content">${todayMail()}</div></section><section class="terminal-panel" id="today-accounts"><div class="section-heading"><div><h2>Account attention</h2><p>Saved access or boundary setup that needs a look.</p></div><button class="text-button" data-action="navigate" data-view="accounts">Open Accounts ${icon('arrow')}</button></div><div id="today-accounts-content">${todayAccountAttention()}</div></section></aside></div>`;
  }
  function renderWelcome() {
    return `<section class="welcome-panel panel"><div class="welcome-intro"><div class="eyebrow">WELCOME</div><h1>Resonance</h1><p class="welcome-pitch">${esc(WELCOME_PITCH)}</p></div><div class="welcome-doors"><button class="welcome-door" data-action="start-samples"><span class="step-number">01</span><span><strong>See it with sample posts</strong><small>Ten seconds, nothing to set up.</small></span>${icon('arrow')}</button><button class="welcome-door" data-action="welcome-paste"><span class="step-number">02</span><span><strong>Check something I wrote</strong><small>Paste your own post, comment or reply.</small></span>${icon('arrow')}</button><button class="welcome-door" data-action="add-account"><span class="step-number">03</span><span><strong>Bring in my accounts</strong><small>One account at a time.</small></span>${icon('arrow')}</button></div><button class="welcome-privacy text-button" data-action="your-data">Everything stays in a folder on this computer. No account, no cloud, no tracking. ${icon('arrow')}</button></section>`;
  }
  function requestOverview() {
    const requests = actionRequests();
    const pendingRows = requests.filter(request => ['queued','running','needs_verification','blocked','reopened'].includes(request.execution_state) || (request.status === 'pending' && request.execution_state !== 'cancelled'));
    const pending = pendingRows.length;
    const held = pendingRows.filter(request => request.confirmation_required === true).length;
    if (!requests.length) return '';
    const waitingTitle = held ? `${pending} ${pending === 1 ? 'request is' : 'requests are'} waiting · ${held} ${held === 1 ? 'needs' : 'need'} your review first.` : `${pending} ${pending === 1 ? 'request is' : 'requests are'} waiting.`;
    const waitingCopy = held ? `${held === pending ? (held === 1 ? 'This imported request is' : 'These imported requests are') : `${held} imported ${held === 1 ? 'request is' : 'requests are'}`} held until you confirm the exact action. Sources and requested changes stay visible here.` : 'Queued, running, blocked and verification work remain visible until their exact outcome is known.';
    return `<section class="request-overview panel" aria-label="Saved action requests"><div><div class="eyebrow">ASSISTANT WORK</div><h2>${pending ? waitingTitle : 'Your request history is saved.'}</h2><p>${pending ? waitingCopy : `${requests.length} ${requests.length === 1 ? 'decision or request' : 'decisions and requests'} in history.`}</p></div><button class="button quiet" data-action="requests-view">View assistant work ${icon('arrow')}</button></section>`;
  }
  const monitorRuns = () => Array.isArray(state.data?.monitoring?.runs) ? state.data.monitoring.runs : [];
  const monitorStatusLabels = {checked:'Checked',partial:'Partial',blocked:'Blocked',missing:'Not reported'};
  const recordedCount = value => Number.isInteger(value) && value >= 0 ? value : null;
  function monitorEntries(run) {
    const reports = Array.isArray(run?.accounts) ? run.accounts : [];
    const entries = state.data.accounts.map(account => ({account,report:reports.find(item => item.account_id === account.id) || null}));
    reports.filter(report => !accountById(report.account_id)).forEach(report => entries.push({account:null,report}));
    return entries;
  }
  function monitorStatus(report) { return ['checked','partial','blocked'].includes(report?.status) ? report.status : 'missing'; }
  function monitorBadge(report) {
    const status = monitorStatus(report);
    return `<span class="check-status ${status}">${icon(status === 'checked' ? 'check' : status === 'partial' ? 'info' : 'warning')}${monitorStatusLabels[status]}</span>`;
  }
  function scheduleLabel() {
    const config = state.data.monitoring?.config;
    if (!config) return 'Schedule unavailable';
    if (!config.enabled) return 'Reports not scheduled';
    if (config.ends_at && new Date(config.ends_at).getTime() <= Date.now()) return 'Reporting window ended';
    if (/^\d{2}:\d{2}$/.test(config.daily_time || '') && config.timezone) {
      const [hour,minute] = config.daily_time.split(':').map(Number);
      if (hour >= 0 && hour < 24 && minute >= 0 && minute < 60) {
        const clock = `${hour % 12 || 12}${minute ? `:${String(minute).padStart(2,'0')}` : ''} ${hour < 12 ? 'a.m.' : 'p.m.'}`;
        let zone = String(config.timezone).replace(/_/g,' ').replace(/^.*\//,'');
        try {
          zone = new Intl.DateTimeFormat(undefined,{timeZone:config.timezone,timeZoneName:'longGeneric'}).formatToParts(new Date()).find(part => part.type === 'timeZoneName')?.value || zone;
        } catch {}
        return `Daily at ${clock} ${zone}`;
      }
    }
    const minutes = recordedCount(config.cadence_minutes);
    if (!minutes) return 'Reports scheduled';
    if (minutes === 1440) return 'Once a day';
    const frequency = minutes % 60 === 0 ? `${minutes / 60} ${minutes === 60 ? 'hour' : 'hours'}` : `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
    return `Reports scheduled · every ${frequency}`;
  }
  function nextScheduledCheckText() {
    const next = state.data?.monitoring?.next_scheduled_check;
    return next ? `Next scheduled check · ${dateText(next,true)}` : 'Next scheduled check · not available';
  }
  function monitoringScope() {
    return 'Agent-assisted checks are saved here after each report. The app displays those results; it does not scan accounts or carry out platform actions. Times are shown in your device’s time zone.';
  }
  function monitorTotals(run) {
    const entries = monitorEntries(run);
    const count = status => entries.filter(entry => monitorStatus(entry.report) === status).length;
    const findings = entries.reduce((total,entry) => total + (Array.isArray(entry.report?.findings) ? entry.report.findings.length : 0),0);
    return {checked:count('checked'),partial:count('partial'),blocked:count('blocked'),missing:count('missing'),findings,total:entries.length};
  }
  function coverageCounts(run,className = 'check-coverage-counts') {
    const totals = monitorTotals(run);
    const labels = {checked:'checked',partial:'partial',blocked:'blocked',missing:'not reported',findings:'findings'};
    return `<div class="${className}" aria-label="Reported account coverage">${Object.entries(labels).filter(([key]) => key !== 'findings' || className !== 'today-report-counts').map(([key,label]) => `<button class="coverage-count ${['partial','blocked','missing'].includes(key) && totals[key] ? 'needs-look' : ''}" data-action="coverage-detail" data-run="${esc(run.id)}" data-coverage="${key}"><strong>${totals[key]}</strong> ${label}</button>`).join('')}</div>`;
  }
  function monitorCoverageLine(run) {return coverageCounts(run);}
  function coverageDetail(runId,status) {
    const run = monitorRuns().find(item => item.id === runId);
    if (!run) {toast('Open Checks to see the available reports.');return;}
    const explanations = {checked:'Checked means the assistant reviewed the scope written in this report. It is not a full account audit.',partial:'Partial means the assistant saw only part of the intended scope. Open an account below to see what was missed.',blocked:'Blocked means the assistant could not complete that account’s check. Open the account below to see the reason and next move.',missing:'Not reported means a saved account has no result in this report. Ask your assistant to include it in the next check.',findings:'Findings are observations saved by the assistant. Open an account below to read the evidence before deciding what to do.'};
    const entries = monitorEntries(run).filter(({report}) => status === 'all' || (status === 'findings' ? report?.findings?.length : monitorStatus(report) === status));
    const body = `<p class="detail-text">${esc(explanations[status] || 'Coverage says what the assistant could actually inspect. Open an account to read its saved limits.')}</p><p>Report: ${esc(dateText(run.completed_at || run.started_at,true))}</p>${entries.length ? `<div class="check-account-grid coverage-explanation">${entries.map(({account,report}) => `<button class="check-account-row" data-action="monitor-account" data-run="${esc(run.id)}" data-account="${esc(account?.id || report?.account_id)}"><span><strong>${esc(account?.platform || 'Account no longer saved')} · ${esc(handleOf(account))}</strong><span>${esc(report?.coverage || report?.summary || 'No result recorded for this account.')}</span></span>${monitorBadge(report)}</button>`).join('')}</div>` : '<p>No accounts have this result in this report. Open the full report to see the recorded coverage.</p>'}`;
    openDialog(dialogShell(status === 'findings' ? 'Saved findings' : monitorStatusLabels[status] || 'Reported coverage','What this saved report means.',body,`<button class="button quiet" data-action="close-dialog">Done</button><button class="button primary" data-action="view-check" data-run="${esc(run.id)}">Full report</button>`));
  }
  function reportNextStep(report) {
    const status = monitorStatus(report);
    if (status === 'missing') return 'This account has no result in this report. Ask for it to be included in the next check.';
    if (status === 'blocked') return 'Read the blocker before relying on this account’s coverage. The report needs a follow-up check.';
    if ((recordedCount(report.pending_deletion_count) || 0) > 0) return 'Review the pending items and their sources. Pending does not mean deleted.';
    if (status === 'partial') return 'Read what was visible and what was missed before deciding on a next move.';
    if (report.findings?.length) return 'Read each finding with its source before deciding what to change.';
    return 'Use the coverage below as the limit of this check. Unseen content remains unreviewed.';
  }
  function youtubeReceipts(run) {
    const entries = monitorEntries(run).filter(entry => entry.account?.platform === 'YouTube' && entry.report);
    const counts = field => {
      const values = entries.map(entry => recordedCount(entry.report[field])).filter(value => value !== null);
      return values.length ? {total:values.reduce((a,b) => a + b,0),complete:values.length === entries.length} : null;
    };
    const deleted = counts('deleted_count'), pending = counts('pending_deletion_count');
    if (!deleted && !pending) return '';
    return `<div class="youtube-receipts"><span class="youtube-receipt-label">${icon('youtube')}YouTube · recorded actions</span><span><strong>${deleted ? deleted.total : '—'}</strong> deleted</span><span class="${pending?.total ? 'needs-look' : ''}"><strong>${pending ? pending.total : '—'}</strong> pending deletion</span><span class="receipt-note">This report only${deleted && !deleted.complete || pending && !pending.complete ? ' · counts incomplete' : ''}. Pending items remain unresolved.</span></div>`;
  }
  function latestCheckPanel() {
    const run = monitorRuns()[0];
    if (!run) {
      return `<section class="latest-check-panel panel"><div class="section-heading"><div><div class="eyebrow">AGENT-ASSISTED REPORTS</div><h2>Latest check</h2></div><span class="check-schedule-label">${esc(nextScheduledCheckText())}</span></div><div class="check-empty"><div class="empty-icon">${icon('monitoring')}</div><div><h3>No result recorded.</h3><p>${state.data.monitoring ? 'There is no account coverage to show yet. A scheduled check time is not a completion promise.' : 'Check history is unavailable. Earlier saved feedback is still available below.'}</p><button class="text-button" data-action="navigate" data-view="monitoring">View reporting details ${icon('arrow')}</button></div></div><p class="check-scope-note">${esc(monitoringScope())}</p></section>`;
    }
    const totals = monitorTotals(run);
    const coverageStatus = totals.blocked || totals.missing ? 'Incomplete coverage' : totals.partial ? 'Partial coverage' : 'Reported coverage';
    const completion = run.completed_at ? `Completed ${dateText(run.completed_at,true)}` : `Started ${dateText(run.started_at,true)} · completion not recorded`;
    const entries = monitorEntries(run);
    return `<section class="latest-check-panel panel"><div class="section-heading"><div><div class="eyebrow">AGENT-ASSISTED REPORTS</div><h2>Latest check</h2></div><button class="text-button" data-action="latest-check">Full report ${icon('arrow')}</button></div><div class="latest-check-meta"><span class="check-status ${totals.blocked || totals.missing ? 'blocked' : totals.partial ? 'partial' : 'checked'}">${icon(totals.blocked || totals.missing ? 'warning' : 'info')}${coverageStatus}</span><time datetime="${esc(run.completed_at || run.started_at)}">${esc(completion)}</time></div><p class="latest-check-summary">${esc(trimText(run.summary || 'No summary was recorded for this check.',650))}</p>${monitorCoverageLine(run)}${youtubeReceipts(run)}<details class="latest-account-details"><summary>Account coverage details <span>${entries.length}</span></summary><div class="check-account-grid" aria-label="Latest reported coverage by account">${entries.map(({account,report}) => `<button class="check-account-row" data-action="monitor-account" data-run="${esc(run.id)}" data-account="${esc(account?.id || report?.account_id)}">${platformIcon(account)}<span class="check-account-copy"><strong>${esc(account?.platform || 'Account no longer saved')}<span>${esc(handleOf(account))}</span></strong><span class="check-coverage-preview">${esc(trimText(report?.coverage || report?.summary || 'No result was recorded for this account.',120))}</span></span>${monitorBadge(report)}</button>`).join('')}</div></details><div class="check-panel-footer"><span>${esc(scheduleLabel())}</span><button class="text-button" data-action="navigate" data-view="monitoring">Check history ${icon('arrow')}</button></div><p class="check-scope-note">Checked means the stated coverage was reviewed. It does not mean the account is clear or fully audited.</p></section>`;
  }
  function selectedMonitorRun() { return monitorRuns().find(run => run.id === state.monitorRun) || monitorRuns()[0]; }
  function monitorAccountCard(account,report,run) {
    const items = recordedCount(report?.items_reviewed);
    const findings = Array.isArray(report?.findings) ? report.findings : [];
    const isYouTube = account?.platform === 'YouTube';
    const deleted = recordedCount(report?.deleted_count), pending = recordedCount(report?.pending_deletion_count);
    return `<article class="check-detail-card panel"><div class="check-account-heading">${platformIcon(account)}<div><h3>${esc(account?.platform || 'Account no longer saved')}</h3><p>${esc(handleOf(account))}</p></div>${monitorBadge(report)}</div><p class="check-account-summary">${esc(report?.summary || 'This saved account is not covered by this report.')}</p><div class="check-detail-coverage"><div class="detail-label">WHAT WAS COVERED</div><p>${esc(report?.coverage || 'No coverage was recorded.')}</p><div class="check-evidence-meta"><span>${items === null ? 'Item count not recorded' : `${items} ${items === 1 ? 'item' : 'items'} reviewed`}</span><span>${report?.checked_at ? `Checked ${esc(dateText(report.checked_at,true))}` : 'Check time not recorded'}</span></div></div>${isYouTube ? `<div class="check-account-receipt"><span>YouTube actions in this report</span><strong>${deleted === null ? '—' : deleted} deleted <span>·</span> ${pending === null ? '—' : pending} pending deletion</strong><p>${deleted === null || pending === null ? 'A dash means no count was recorded. ' : ''}Pending is not a deletion receipt.</p></div>` : ''}<div class="check-next-step"><strong>${icon('arrow')}Next useful move</strong><p>${esc(reportNextStep(report))}</p></div>${findings.length ? `<div class="check-findings"><div class="detail-label">REPORTED FINDINGS · ${findings.length}</div>${findings.map(finding => `<div class="check-finding">${badge(finding.severity, {attention:"Needs review",suggestion:"Suggested action",strength:"Working well"}[finding.severity])}<h4>${esc(finding.title)}</h4><p>${esc(finding.detail)}</p>${sourceLink(finding.source_url,'View source')}</div>`).join('')}</div>` : `<p class="check-no-findings">${monitorStatus(report) === 'blocked' || !report ? 'No findings are available to assess this account.' : 'No findings were recorded for the stated coverage. Unseen content is outside this result.'}</p>`}${sourceLink(account?.url,'Open account profile')}<button class="text-button check-report-context" data-action="monitor-account" data-run="${esc(run.id)}" data-account="${esc(account?.id || report?.account_id)}">Report details ${icon('arrow')}</button></article>`;
  }
  function renderMonitoring() {
    const run = selectedMonitorRun();
    const headingHtml = heading('Your check history.','What was seen, what was missed, and what happened next.',`<button class="button quiet" data-action="refresh-checks">${icon('monitoring')}Refresh reports</button>`,'SAVED REPORTS');
    const config = state.data.monitoring?.config;
    const configPanel = `<section class="check-config panel"><div class="check-config-heading"><div>${icon('clock')}<strong>${esc(nextScheduledCheckText())}</strong></div>${config?.ends_at ? `<span>Window ${new Date(config.ends_at).getTime() <= Date.now() ? 'ended' : 'ends'} ${esc(dateText(config.ends_at,true))}</span>` : ''}</div><p>${esc(scheduleLabel())}. A check time is not an action-completion promise.</p>${config?.scope ? `<p>${esc(config.scope)}</p>` : ''}${config?.action_scope ? `<p class="check-action-scope"><strong>Action scope</strong>${esc(config.action_scope)}</p>` : ''}<p class="check-scope-note">${esc(monitoringScope())}</p><p class="check-assistant-note">Stay signed in in Chrome; keep this computer awake and unlocked, with your assistant available. Saved accounts are not live connections.</p>${!config?.enabled || !monitorRuns().length ? '<p class="check-setup-note">Your assistant manages the schedule in chat. This screen shows recorded setup and saved results.</p>' : ''}</section>`;
    const selector = monitorRuns().length ? `<div class="check-history-toolbar"><label for="check-run">Report</label><select id="check-run">${monitorRuns().map((item,index) => `<option value="${esc(item.id)}" ${item.id === run?.id ? 'selected' : ''}>${index === 0 ? 'Latest · ' : ''}${esc(dateText(item.completed_at || item.started_at,true))}${item.completed_at ? '' : ' · completion not recorded'}</option>`).join('')}</select><span>${monitorRuns().length} saved ${monitorRuns().length === 1 ? 'report' : 'reports'}</span></div>` : '';
    return headingHtml + `<div id="monitoring-content">${configPanel}${selector}${run ? `<section class="check-run-summary"><div class="section-heading"><h2>${run.id === monitorRuns()[0]?.id ? 'Latest check' : 'Earlier check'}</h2><span class="check-completion">${run.completed_at ? 'Completed' : 'Completion not recorded'}</span></div><p>${esc(run.summary || 'No summary was recorded.')}</p><div class="check-run-times"><span>Started ${esc(dateText(run.started_at,true))}</span><span>${run.completed_at ? `Completed ${esc(dateText(run.completed_at,true))}` : 'No completion time saved'}</span></div>${monitorCoverageLine(run)}${youtubeReceipts(run)}</section><section aria-label="Account reports" class="check-detail-grid">${monitorEntries(run).map(({account,report}) => monitorAccountCard(account,report,run)).join('')}</section>` : emptyState(state.data.monitoring ? 'The first report will appear here.' : 'Monitoring data is unavailable.',state.data.monitoring ? 'No check has been saved yet. A scheduled request is not proof that an account was checked. Ask your assistant for the first report in chat.' : 'The local app did not return a monitoring record. Try refreshing after the app has been updated. Your saved accounts and reviews are still available.',`<button class="button quiet" data-action="refresh-checks">Refresh reports ${icon('arrow')}</button>`,'monitoring')}</div>`;
  }
  function monitoringAccountDialog(runId,accountId) {
    const run = monitorRuns().find(item => item.id === runId);
    if (!run) {toast('This report is no longer in the current history. Open Checks for available reports.');return;}
    const account = accountById(accountId);
    const report = run.accounts?.find(item => item.account_id === accountId);
    const items = recordedCount(report?.items_reviewed);
    const findings = Array.isArray(report?.findings) ? report.findings : [];
    const deleted = recordedCount(report?.deleted_count), pending = recordedCount(report?.pending_deletion_count);
    const body = `<div class="detail-status">${monitorBadge(report)}<span class="check-completion">${run.completed_at ? 'Saved report' : 'Completion not recorded'}</span></div><div class="detail-block"><div class="detail-label">THE ACCOUNT SUMMARY</div><p class="detail-text">${esc(report?.summary || 'No result was saved for this account in this report.')}</p></div><div class="detail-block"><div class="detail-label">COVERAGE</div><p class="detail-text">${esc(report?.coverage || 'No coverage was recorded.')}</p></div><div class="detail-block"><dl class="detail-meta-list"><dt>Account check</dt><dd>${esc(dateText(report?.checked_at,true))}</dd><dt>Report started</dt><dd>${esc(dateText(run.started_at,true))}</dd><dt>Completed</dt><dd>${esc(dateText(run.completed_at,true))}</dd><dt>Items reviewed</dt><dd>${items === null ? 'Not recorded' : items}</dd>${account?.platform === 'YouTube' ? `<dt>Deleted</dt><dd>${deleted === null ? 'Not recorded' : deleted}</dd><dt>Pending deletion</dt><dd>${pending === null ? 'Not recorded' : pending} · pending is not deleted</dd>` : ''}</dl></div><div class="check-next-step"><strong>${icon('arrow')}Next useful move</strong><p>${esc(reportNextStep(report))}</p></div>${findings.length ? `<div class="dialog-subsection"><h3>Reported findings</h3>${findings.map(finding => `<div class="check-finding">${badge(finding.severity, {attention:"Needs review",suggestion:"Suggested action",strength:"Working well"}[finding.severity])}<h4>${esc(finding.title)}</h4><p>${esc(finding.detail)}</p>${sourceLink(finding.source_url,'View source')}</div>`).join('')}</div>` : `<div class="scope-strip">${icon('info')}<span>${monitorStatus(report) === 'blocked' || !report ? 'No findings are available to assess this account.' : 'No findings were recorded for the stated coverage. This is not an account-wide clearance.'}</span></div>`}${sourceLink(account?.url,'Open account profile')}`;
    openDialog(dialogShell(account?.platform || 'Account report',handleOf(account) || 'Account no longer saved',body,`<button class="button quiet" data-action="close-dialog">Done</button><button class="button primary" data-action="view-check" data-run="${esc(run.id)}">Full report ${icon('arrow')}</button>`));
  }
  function monitoringSignature() {
    return JSON.stringify({monitoring:state.data?.monitoring || null,accounts:state.data?.accounts.map(account => ({id:account.id,platform:account.platform,handle:account.handle,url:account.url})),schedule:scheduleLabel()});
  }
  function communicationSignature(data = state.data) {
    const access = Array.isArray(data?.communication?.access) ? data.communication.access : [];
    const accessFreshness = access.map(record => {
      const checked = record.checked_at && !Number.isNaN(new Date(record.checked_at).getTime());
      const key = record.status === 'verified' && (!checked || Date.now() - new Date(record.checked_at).getTime() > 86400000) ? 'stale' : record.status;
      return [record.account_id,key];
    });
    return JSON.stringify({accounts:data?.accounts || [],communication:data?.communication || null,access_freshness:accessFreshness});
  }
  function sourceStateSignature(data = state.data) {
    return JSON.stringify({source_items:data?.source_items || [],action_requests:data?.action_requests || [],attestations:data?.attestations || []});
  }
  function updateSourceView() {
    if (!['overview','review','accounts'].includes(state.view)) {state.sourceSignature = sourceStateSignature();state.sourceRefreshPending = false;return;}
    if (dialog.open) {state.sourceRefreshPending = true;return;}
    if (state.view === 'overview') {
      const replacements = [
        [$('#today-summary-content'),() => todaySummary()],
        [$('#today-needs-you-content'),() => todayNeedsYou()],
        [$('#today-waiting-content'),() => todayWaiting()],
      ].filter(([target]) => target);
      if (replacements.some(([target]) => target.contains(document.activeElement))) {state.sourceRefreshPending = true;return;}
      replacements.forEach(([target,html]) => {target.innerHTML = html();});
      state.sourceSignature = sourceStateSignature();
      renderNav();
    } else if (state.view === 'accounts') {
      const target = $('#account-results');
      if (!target) {
        if ($('#main')?.contains(document.activeElement)) {state.sourceRefreshPending = true;return;}
        render();
      } else {
        if (target.contains(document.activeElement)) {state.sourceRefreshPending = true;return;}
        target.innerHTML = accountResults();
        state.sourceSignature = sourceStateSignature();
        renderNav();
      }
    } else {
      if ($('#main')?.contains(document.activeElement)) {state.sourceRefreshPending = true;return;}
      render();
    }
    state.sourceRefreshPending = false;
  }
  function updateCommunicationView() {
    if (!state.data || dialog.open) {state.communicationRefreshPending = true;return;}
    const replacements = [];
    if (state.view === 'overview') replacements.push([$('#today-summary-content'),() => todaySummary()],[$('#today-mail-count'),() => String(communicationList('messages').length)],[$('#today-mail-content'),() => todayMail()],[$('#today-accounts-content'),() => todayAccountAttention()]);
    else if (state.view === 'accounts') {
      const results = $('#account-results');
      if (!results || !state.data.accounts.length) {
        if ($('#main')?.contains(document.activeElement)) {state.communicationRefreshPending = true;return;}
        render();
        state.communicationSignature = communicationSignature();
        state.communicationRefreshPending = false;
        return;
      }
      replacements.push([results,() => accountResults()],[$('#accounts-count'),() => String(state.data.accounts.length)]);
    }
    else if (state.view === 'setup') replacements.push([$('#setup-accounts-summary'),() => setupAccountsSummary()],[$('#setup-schedule'),() => scheduleLabel()]);
    else if (state.view === 'mail') {
      const accounts = state.data.accounts.filter(mailAccount);
      replacements.push([$('#mailbox-summary'),() => mailboxCards(accounts)],[$('#mail-results'),() => mailResults()],[$('#mail-sync-empty-note'),() => communicationList('mail_syncs').length ? '' : `<div class="inline-note">${icon('info')}<p>No mail check receipt has been saved yet. Account profiles alone do not provide inbox access.</p></div>`]);
    }
    const targets = replacements.filter(([target]) => target);
    if (targets.some(([target]) => target.contains(document.activeElement))) {state.communicationRefreshPending = true;return;}
    targets.forEach(([target,html]) => {target.innerHTML = html();});
    state.communicationSignature = communicationSignature();
    state.communicationRefreshPending = false;
  }
  function updateMonitoringView() {
    const target = state.view === 'overview' ? $('#today-report-content') : state.view === 'monitoring' ? $('#monitoring-content') : state.view === 'accounts' ? $('#account-results') : null;
    if (state.view === 'accounts') {
      if (!target) {
        if (!state.data.accounts.length) {state.monitorSignature = monitoringSignature();state.refreshPending = false;return;}
        if ($('#main')?.contains(document.activeElement)) {state.refreshPending = true;return;}
        render();
      } else {
        if (target.contains(document.activeElement)) {state.refreshPending = true;return;}
        target.innerHTML = accountResults();
      }
      state.monitorSignature = monitoringSignature();
      state.refreshPending = false;
      return;
    }
    if (!target) return;
    // Never replace a control being used, an open report, or an unfinished form.
    if (dialog.open || target.contains(document.activeElement)) {state.refreshPending = true;return;}
    if (state.view === 'overview') target.innerHTML = todayReportContent();
    else {
      const fragment = document.createElement('div');
      fragment.innerHTML = renderMonitoring();
      target.replaceChildren(...$('#monitoring-content',fragment).childNodes);
    }
    state.monitorSignature = monitoringSignature();
    state.refreshPending = false;
  }
  async function refreshMonitoring(manual = false) {
    if (monitoringRefreshBusy || document.visibilityState !== 'visible' || dialog.open) return;
    if (!state.data) {await reload();return;}
    monitoringRefreshBusy = true;
    const sequence = ++stateReadSequence;
    try {
      const data = await api('/api/state');
      if (sequence !== stateReadSequence) return;
      if (!Array.isArray(data.accounts) || !Array.isArray(data.reviews) || !data.settings) throw new Error('The workspace data is incomplete. Please try again.');
      const communicationChanged = communicationSignature(data) !== state.communicationSignature;
      const sourcesChanged = sourceStateSignature(data) !== state.sourceSignature;
      const switched = state.data?.workspace?.token && state.data.workspace.token !== data.workspace?.token;
      if (switched) resetWorkspaceEditors();
      state.data = data;
      state.connected = true;
      state.lastLoaded = new Date().toISOString();
      showConnection();
      renderSampleBanner();
      if (switched) {render();return;}
      if (monitoringSignature() !== state.monitorSignature || manual) updateMonitoringView();
      if (communicationChanged || manual) updateCommunicationView();
      if (sourcesChanged || manual) updateSourceView();
      if (manual) toast(state.refreshPending ? 'Reports refreshed. The view will update when you finish this control or close the dialog.' : 'Saved reports refreshed. This does not start an account check.');
    } catch (error) {
      if (sequence !== stateReadSequence) return;
      state.connected = false;
      showConnection();
      if (manual) toast(error.message);
    } finally {monitoringRefreshBusy = false;}
  }
  function trimText(text, length) { const str = String(text || ''); return str.length > length ? `${str.slice(0,length).trim()}…` : str; }
  function renderActivity() {
    const activities = state.data.activity?.slice(0,3) || [];
    if (!activities.length) return '';
    return `<section class="activity-section"><div class="section-heading"><h2>In your workspace</h2></div><ul class="activity-list">${activities.map(item => `<li><time datetime="${esc(item.at)}">${esc(relativeDate(item.at))}</time><span>${esc(item.description)}</span></li>`).join('')}</ul></section>`;
  }
  function accountRow(account) {
    const count = reviewsFor(account.id).length;
    return `<button class="account-row" data-action="account-detail" data-id="${esc(account.id)}">${platformIcon(account)}<span class="account-row-info"><span class="account-row-name">${esc(account.platform)}</span><span class="account-row-handle">${esc(handleOf(account))}</span></span><span class="account-row-state ${count ? 'has-evidence' : ''}">${count ? `${count} ${count === 1 ? 'observation' : 'observations'}` : 'No evidence yet'}</span>${icon('chevron','chevron')}</button>`;
  }
  function snapshotText(account) { return account.access_checked_at ? `Access snapshot · ${dateText(account.access_checked_at)}` : 'No access snapshot recorded'; }
  const signInUrls = {Gmail:'https://accounts.google.com/',Outlook:'https://outlook.live.com/mail/',YouTube:'https://accounts.google.com/',Facebook:'https://www.facebook.com/login/',Instagram:'https://www.instagram.com/accounts/login/',X:'https://x.com/i/flow/login',Reddit:'https://www.reddit.com/login/',TikTok:'https://www.tiktok.com/login',LinkedIn:'https://www.linkedin.com/login',Discord:'https://discord.com/login',Substack:'https://substack.com/sign-in'};
  const accessLabels = {verified:'Verified snapshot',blocked:'Blocked',needs_login:'Sign-in needed',unverified:'Not verified'};
  function accessState(account) {
    const record = accessFor(account.id);
    if (!record) return {key:'unknown',label:'Not checked',detail:account.access_checked_at ? `A manual snapshot was recorded ${relativeDate(account.access_checked_at)}. No assistant access evidence is saved.` : 'No assistant access evidence is saved.',record:null};
    const checked = record.checked_at && !Number.isNaN(new Date(record.checked_at).getTime());
    const stale = record.status === 'verified' && (!checked || Date.now() - new Date(record.checked_at).getTime() > 86400000);
    return {key:stale ? 'stale' : record.status,label:stale ? 'Recheck due' : accessLabels[record.status] || 'Not checked',detail:record.detail || (stale ? 'The last verified snapshot is more than 24 hours old.' : 'No further detail was recorded.'),record};
  }
  function accountCoverage(account) {
    const sync = syncFor(account.id);
    if (sync) {
      const count = Number.isInteger(sync.imported_count) ? `${sync.imported_count} saved ${sync.imported_count === 1 ? 'preview' : 'previews'}` : 'Saved mail scope';
      return `${count} · ${sync.scope || 'scope not recorded'}`;
    }
    const latestReport = monitorRuns().map(run => ({run,report:(run.accounts || []).find(item => item.account_id === account.id)})).find(item => item.report);
    if (latestReport) return trimText(latestReport.report.coverage || latestReport.report.summary || 'Coverage detail not recorded',110);
    const count = reviewsFor(account.id).length;
    return count ? `${count} saved review ${count === 1 ? 'observation' : 'observations'}` : 'No saved content coverage';
  }
  function accessBadge(account) {
    const access = accessState(account);
    return `<span class="access-badge ${esc(access.key)}">${icon(access.key === 'verified' ? 'check' : ['blocked','needs_login','stale'].includes(access.key) ? 'warning' : 'info')}${esc(access.label)}</span>`;
  }
  function renderAccountMap(accounts = state.data.accounts) {
    return `<div class="account-map" aria-label="Account access map">${accounts.map(account => { const access = accessState(account); return `<button class="account-map-tile access-${esc(access.key)}" data-action="setup-account" data-id="${esc(account.id)}"><span class="account-map-top">${platformIcon(account)}${accessBadge(account)}</span><span class="account-map-name">${esc(account.platform)}</span><span class="account-map-handle">${esc(handleOf(account) || 'Handle not recorded')}</span>${account.display_name && account.display_name !== account.handle ? `<span class="account-map-display">${esc(account.display_name)}</span>` : ''}<span class="account-map-checked">${access.record?.checked_at ? `Checked ${esc(relativeDate(access.record.checked_at))}` : 'No assistant check date'}</span><span class="account-map-coverage">${esc(accountCoverage(account))}</span><span class="account-map-more">Details ${icon('arrow')}</span></button>`; }).join('')}</div><div class="access-legend" aria-label="Access status legend"><span><i class="verified"></i>Fresh verified snapshot</span><span><i class="stale"></i>Recheck due</span><span><i class="blocked"></i>Sign-in needed or blocked</span><span><i class="unknown"></i>Not checked</span></div><p class="account-footnote">Color describes access evidence only. A verified snapshot is not a permanent live connection, and content coverage has its own stated limit.</p>`;
  }
  function capabilityLabel(value) { return String(value || '').replace(/[_-]+/g,' ').replace(/^./,char => char.toUpperCase()); }
  function accountSetupDialog(id) {
    if (showingSamples()) {
      openDialog(dialogShell('This is a sample account.','Nothing here is connected.', '<p>Clear samples to bring in your own accounts.</p>','<button class="button primary" data-action="close-dialog">Done</button>'));
      return;
    }
    const account = accountById(id);
    if (!account) return;
    const access = accessState(account), request = setupRequestsFor(id)[0], pending = request?.status === 'pending';
    const capabilities = Array.isArray(access.record?.capabilities) ? access.record.capabilities : [];
    const checked = access.record?.checked_at ? `Checked ${dateText(access.record.checked_at,true)}` : 'No assistant check time saved';
    const signIn = signInUrls[account.platform];
    const nextAction = pending
      ? `<div class="setup-request-state">${icon('clock')}<div><strong>Access check requested</strong><span>Queued ${relativeDate(request.created_at)}. It stays pending until evidence is saved.</span></div><button class="text-button" data-action="cancel-access-check" data-id="${esc(request.id)}" data-account="${esc(id)}">Cancel request</button></div>`
      : access.key === 'needs_login' && signIn
        ? `<a class="button primary" href="${esc(signIn)}" target="_blank" rel="noopener noreferrer">Open sign-in ${icon('external')}</a><p class="next-action-note">Sign in to the intended account in Chrome, then return here and request a new access check. Opening this page does not switch an email connector identity.</p><button class="button quiet" data-action="request-access-check" data-id="${esc(id)}">Request access check</button>`
        : `<button class="button primary" data-action="request-access-check" data-id="${esc(id)}">${icon('monitoring')}Request access check</button>`;
    const body = `<div class="access-detail-hero"><div class="detail-account">${platformIcon(account)}<div><h3>${esc(nameOf(account))}</h3><p>${esc(handleOf(account))}</p></div></div>${accessBadge(account)}</div><div class="access-detail-grid"><section><div class="detail-label">ACCESS EVIDENCE</div><p class="detail-text">${esc(access.detail)}</p><p class="evidence-time">${esc(checked)} · ${esc(access.record?.method ? capabilityLabel(access.record.method) : 'Method not recorded')}</p></section><section><div class="detail-label">CONTENT IN VIEW</div><p class="detail-text">${esc(accountCoverage(account))}</p><p class="evidence-time">Saved coverage can remain visible even when current access is blocked.</p></section></div><div class="dialog-subsection"><h3>Available capabilities</h3>${capabilities.length ? `<div class="capability-list">${capabilities.map(item => `<span>${esc(capabilityLabel(item))}</span>`).join('')}</div>` : '<p>No capabilities were recorded for this access check.</p>'}</div>${mailAccount(account) ? `<div class="scope-strip">${icon('info')}<span>Email access belongs to the assistant’s configured tools. This web app does not inherit a connector or silently switch it to another mailbox.</span></div>` : ''}<div class="setup-next"><div class="detail-label">NEXT ACTION</div>${nextAction}</div>`;
    openDialog(dialogShell(account.platform,'Account setup and evidence',body,`<button class="button quiet" data-action="account-edit" data-id="${esc(id)}">Edit saved identity</button><button class="button quiet" data-action="close-dialog">Done</button>`));
  }
  function brandComplete() {
    const brand = communication().brand || {};
    return ['description','audience','voice','avoid'].some(key => String(brand[key] || '').trim());
  }
  function setupAccountsSummary() {
    const accounts = state.data.accounts;
    if (!accounts.length) return 'No accounts saved yet. Add each identity independently in Accounts.';
    const verified = accounts.filter(account => accessState(account).key === 'verified').length;
    return `${accounts.length} saved ${accounts.length === 1 ? 'identity' : 'identities'} · ${verified} with a fresh verified snapshot. Each account can be set up independently.`;
  }
  function renderSetup() {
    const brand = communication().brand || {};
    return heading('Preferences.','Set your personal brand, manage identities, and see the saved daily schedule in any order.',`<button class="button quiet" data-action="navigate" data-view="overview">Back to Today</button>`,'SETUP') + `<div class="setup-options"><section class="setup-section terminal-panel" id="setup-identity"><div class="setup-section-head"><div><h2>Personal brand</h2><p>Describe how you want your own writing to sound.</p></div></div><form id="brand-form"><div class="form-grid"><div class="field full"><label for="brand-description">How I want to come across</label><textarea id="brand-description" name="description" maxlength="4000" placeholder="Grounded, direct, thoughtful…">${esc(brand.description)}</textarea></div><div class="field full"><label for="brand-audience">Who I speak to</label><textarea id="brand-audience" name="audience" maxlength="4000" placeholder="People, communities, clients…">${esc(brand.audience)}</textarea></div><div class="field"><label for="brand-voice">My voice</label><textarea id="brand-voice" name="voice" maxlength="4000" placeholder="Warm, concise, candid…">${esc(brand.voice)}</textarea></div><div class="field"><label for="brand-avoid">What to avoid</label><textarea id="brand-avoid" name="avoid" maxlength="4000" placeholder="Phrases, habits, tones…">${esc(brand.avoid)}</textarea></div></div><div class="inline-save-row"><span class="saved-state">${brand.updated_at ? `Saved ${esc(relativeDate(brand.updated_at))}` : 'Not saved yet'}</span><button class="button primary" type="submit">Save personal brand</button></div></form><p class="account-footnote">Personal brand guides your own writing. Account identity and safety boundaries are edited on each account.</p></section><aside class="setup-side"><section class="setup-section terminal-panel" id="setup-accounts"><div class="setup-section-head"><div><h2>Accounts</h2><p id="setup-accounts-summary">${esc(setupAccountsSummary())}</p></div></div><div class="setup-actions"><button class="button primary" data-action="navigate" data-view="accounts">Open Accounts ${icon('arrow')}</button>${addAccountButton()}</div><p class="connector-note">Gmail or Outlook connector access belongs to the assistant tools where it was configured. Resonance records evidence here; this page does not become a live mailbox connection.</p></section><section class="setup-section terminal-panel" id="setup-daily"><div class="setup-section-head"><div><h2>Daily schedule</h2><p>Read saved evidence after a check arrives.</p></div><span class="schedule-chip" id="setup-schedule">${esc(scheduleLabel())}</span></div><p>Editing preferences does not reschedule the assistant or prove that an account was reached.</p><button class="button quiet" data-action="navigate" data-view="monitoring">Open Checks ${icon('arrow')}</button></section></aside></div>`;
  }
  function mailSyncSummary(account) {
    const sync = syncFor(account.id);
    if (!sync) return `<span class="mail-scope missing">No saved mail check</span><p>No inbox previews have been imported for this account.</p>`;
    const imported = Number.isInteger(sync.imported_count) ? sync.imported_count : 'Unknown';
    const totals = Number.isInteger(sync.total_count) ? ` · ${sync.total_count} in stated scope` : '';
    const unread = Number.isInteger(sync.unread_count) ? ` · ${sync.unread_count} unread` : '';
    return `<span class="mail-scope ${sync.has_more ? 'partial' : ''}">${sync.has_more ? 'Partial saved scope' : 'Saved scope'}</span><p>${esc(sync.scope || 'Scope not recorded')} · ${esc(imported)} imported${esc(totals)}${esc(unread)}</p><time datetime="${esc(sync.checked_at)}">Last checked ${esc(dateText(sync.checked_at,true))}</time>`;
  }
  function mailboxCards(accounts = state.data.accounts.filter(mailAccount)) {
    return accounts.map(account => `<button class="mailbox-card panel" data-action="setup-account" data-id="${esc(account.id)}"><span class="mailbox-card-head">${platformIcon(account)}<span><strong>${esc(account.platform)}</strong><small>${esc(handleOf(account) || 'Email not recorded')}</small>${account.display_name && account.display_name !== account.handle ? `<em>${esc(account.display_name)}</em>` : ''}</span>${accessBadge(account)}</span>${mailSyncSummary(account)}</button>`).join('');
  }
  function renderMail() {
    const accounts = state.data.accounts.filter(mailAccount), syncs = communicationList('mail_syncs');
    const top = heading('Recent mail.','Read saved inbox previews and shape a local reply without sending anything.',`<button class="button quiet" data-action="navigate" data-view="setup">Set up mail access ${icon('arrow')}</button>`,'MAIL');
    if (!accounts.length) return top + emptyState('Add a mail account first.','Save Gmail or Outlook in Accounts, then request an access check from its setup detail.',`<button class="button primary" data-action="add-account" data-platform="Gmail">Add mail account ${icon('arrow')}</button>`,'mail');
    return top + `<section id="mailbox-summary" class="mailbox-summary" aria-label="Saved mailbox scope">${mailboxCards(accounts)}</section><div class="scope-strip mail-scope-note">${icon('info')}<span>Initial mail checks cover up to the latest 25 inbox previews in each available mailbox. Other folders and older history are outside that scope unless a saved check says otherwise. Opening a preview here does not mark it read with the provider.</span></div><div class="toolbar"><label class="search-field">${icon('search')}<span class="sr-only">Search saved mail</span><input id="mail-search" type="search" placeholder="Find a sender, subject, or phrase…" value="${esc(state.mailQuery)}" autocomplete="off"></label><label class="sr-only" for="mail-account-filter">Filter by mail account</label><select id="mail-account-filter" class="filter-select"><option value="all">All mail accounts</option>${accounts.map(account => `<option value="${esc(account.id)}" ${state.mailAccount === account.id ? 'selected' : ''}>${esc(account.platform)} · ${esc(handleOf(account) || nameOf(account))}</option>`).join('')}</select></div><div id="mail-results">${mailResults()}</div><div id="mail-sync-empty-note">${!syncs.length ? `<div class="inline-note">${icon('info')}<p>No mail check receipt has been saved yet. Account profiles alone do not provide inbox access.</p></div>` : ''}</div>`;
  }
  function mailResults() {
    const query = state.mailQuery.trim().toLowerCase();
    const messages = communicationList('messages').filter(message => (state.mailAccount === 'all' || message.account_id === state.mailAccount) && [message.subject,message.sender,message.preview].join(' ').toLowerCase().includes(query)).sort((a,b) => new Date(b.received_at || 0) - new Date(a.received_at || 0));
    if (!messages.length) return emptyState(query || state.mailAccount !== 'all' ? 'No saved mail matches this view.' : 'No recent mail is saved.','Try another filter, or wait for a mail check to save its precise scope.',query || state.mailAccount !== 'all' ? `<button class="button quiet" data-action="clear-mail-filters">Clear filters</button>` : '','mail');
    return `<section><div class="review-results-header"><span>${messages.length} saved ${messages.length === 1 ? 'preview' : 'previews'}</span><span>Local view only</span></div><div class="mail-list">${messages.map(message => { const account = accountById(message.account_id), draft = draftForMessage(message.id); return `<button class="mail-row panel" data-action="mail-message" data-id="${esc(message.id)}"><span class="mail-unread ${message.is_read === false ? 'active' : ''}" aria-label="${message.is_read === false ? 'Unread in saved evidence' : 'Read state not marked unread'}"></span>${platformIcon(account)}<span class="mail-row-main"><span class="mail-row-line"><strong>${esc(message.sender || 'Sender not recorded')}</strong><time datetime="${esc(message.received_at)}">${esc(relativeDate(message.received_at))}</time></span><span class="mail-subject">${esc(message.subject || 'No subject')}</span><span class="mail-preview">${esc(trimText(message.preview || 'No preview was saved.',180))}</span></span>${draft ? `<span class="draft-chip">Draft saved</span>` : ''}${icon('chevron','chevron')}</button>`;}).join('')}</div></section>`;
  }
  function brandGuide() {
    const brand = communication().brand || {};
    if (!brandComplete()) return `<div class="brand-guide empty"><strong>Your personal brand is not set yet.</strong><button class="text-button" data-action="setup-identity">Add it in setup ${icon('arrow')}</button></div>`;
    return `<div class="brand-guide"><div class="detail-label">YOUR VOICE, FOR REFERENCE</div>${brand.description ? `<p><strong>How you want to come across</strong>${esc(trimText(brand.description,220))}</p>` : ''}${brand.voice ? `<p><strong>Voice</strong>${esc(trimText(brand.voice,220))}</p>` : ''}${brand.audience ? `<p><strong>Audience</strong>${esc(trimText(brand.audience,220))}</p>` : ''}${brand.avoid ? `<p><strong>Avoid</strong>${esc(trimText(brand.avoid,220))}</p>` : ''}</div>`;
  }
  function mailMessageDialog(id) {
    const message = communicationList('messages').find(item => item.id === id);
    if (!message) return;
    const account = accountById(message.account_id), draft = draftForMessage(id), url = safeUrl(message.source_url);
    const body = `<input type="hidden" name="message_id" value="${esc(id)}"><div class="message-meta">${platformIcon(account)}<div><strong>${esc(message.sender || 'Sender not recorded')}</strong><span>Reply from ${esc(account?.platform || 'Mail account')} · ${esc(handleOf(account) || 'Mailbox not recorded')}</span><span>${esc(dateText(message.received_at,true))}</span></div></div><div class="message-preview"><div class="detail-label">SAVED PREVIEW</div><p>${esc(message.preview || 'No preview was saved for this message.')}</p></div>${brandGuide()}<div class="field mail-reply-field"><label for="mail-reply">Your reply</label><textarea id="mail-reply" name="text" required maxlength="20000" spellcheck="true" placeholder="Write a reply to keep on this computer…">${esc(draft?.text)}</textarea><span class="field-help">Local only. Saving does not create a provider draft or send a message.</span></div><p class="saved-state">${draft?.updated_at ? `Draft saved ${esc(relativeDate(draft.updated_at))}` : 'No local reply saved yet'}</p>`;
    const footer = `${url ? `<a class="button quiet" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open original ${icon('external')}</a>` : ''}<button class="button quiet" type="button" data-action="copy-mail-reply">${icon('copy')}Copy reply</button><button class="button primary" type="submit">Save draft locally</button>`;
    openDialog(dialogShell(message.subject || 'No subject', 'Saved mail preview', body, footer, 'mail-draft-form'));
  }
  async function requestAccessCheck(button) {
    const id = button.dataset.id;
    button.disabled = true; button.setAttribute('aria-busy','true');
    try { await api('/api/access-checks','POST',{account_id:id}); if (!await reload()) throw new Error('The request was saved, but the workspace could not refresh. Reopen Resonance to see its current status.'); accountSetupDialog(id); toast('Access check requested for the daily assistant run.'); }
    catch(error) {dialogError(error);}
    finally {if (button.isConnected) {button.disabled = false;button.removeAttribute('aria-busy');}}
  }
  async function cancelAccessCheck(button) {
    button.disabled = true; button.setAttribute('aria-busy','true');
    try { await api(`/api/access-checks/${encodeURIComponent(button.dataset.id)}`,'PATCH',{status:'cancelled'}); if (!await reload()) throw new Error('The cancellation was saved, but the workspace could not refresh.'); accountSetupDialog(button.dataset.account); toast('Access-check request cancelled. Account access was not changed.'); }
    catch(error) {dialogError(error);}
    finally {if (button.isConnected) {button.disabled = false;button.removeAttribute('aria-busy');}}
  }
  async function copyMailReply() {
    const editor = $('#mail-reply');
    if (!editor?.value) {toast('Write a reply before copying it.');return;}
    try {await navigator.clipboard.writeText(editor.value);toast('Reply copied. Nothing was sent.');}
    catch {editor.focus();editor.select();toast('Your reply is selected. Use Copy from your device’s text menu.');}
  }
  function renderAccounts() {
    const {accounts} = state.data;
    const top = heading('Your accounts.','One identity list with separate access evidence, coverage, and boundaries.',`<button class="button quiet" data-action="navigate" data-view="setup">Personal brand and schedule</button>${addAccountButton()}`,'ACCOUNTS');
    if (!accounts.length) return top + emptyState('Start with one account.','Add a profile you want to keep in view. Choose its identity, daring level, and content boundaries.',addAccountButton('Add your first account'),'accounts');
    return top + `<div class="toolbar"><label class="search-field">${icon('search')}<span class="sr-only">Search accounts</span><input id="account-search" type="search" placeholder="Find an account…" value="${esc(state.accountQuery)}" autocomplete="off"></label><span class="toolbar-note"><span id="accounts-count">${accounts.length}</span> accounts · added by you</span></div><div id="account-results">${accountResults()}</div><div class="scope-strip">${icon('info')}<span>Saved accounts are not live connections. Access evidence, content coverage, and account boundaries describe different things.</span></div>`;
  }
  function accountResults() {
    const query = state.accountQuery.trim().toLowerCase();
    const matches = state.data.accounts.filter(account => [account.platform,account.handle,account.display_name].join(' ').toLowerCase().includes(query));
    if (!matches.length) return emptyState('No matching accounts.','Try a platform, name, or handle.',`<button class="button quiet" data-action="clear-account-search">Clear search</button>`,'search');
    return `<div class="account-grid">${matches.map(account => {
      const reviews = reviewsFor(account.id), flags = flagCount(account.id);
      const unset = !account.identity_mode || account.identity_mode === 'unclassified';
      const access = accessState(account);
      return `<article class="account-card panel" data-account-id="${esc(account.id)}"><div class="account-card-top">${platformIcon(account)}${accessBadge(account)}</div><h2>${esc(account.platform)}</h2><p class="account-handle">${esc(nameOf(account))}${nameOf(account) !== handleOf(account) ? `<br><span>${esc(handleOf(account))}</span>` : ''}</p><div class="account-evidence"><div><span>Access evidence</span><strong>${esc(access.label)}${access.record?.checked_at ? ` · ${esc(relativeDate(access.record.checked_at))}` : ''}</strong><p>${esc(trimText(access.detail,140))}</p></div><div><span>Content in view</span><strong>${esc(accountCoverage(account))}</strong></div></div><div class="account-card-stats"><div><strong>${reviews.length}</strong><span>review observations</span></div><div><strong>${flags}</strong><span>open boundary flags</span></div></div><p class="boundary-line"><span class="badge ${unset ? '' : 'suggestion'}">${esc(identityLabel[account.identity_mode] || 'Not set')}</span>${unset ? 'Choose how you want to show up.' : `${esc(daringLabel[account.daring] || 'Careful')} · ${account.sfw_only !== false ? 'SFW only' : 'Custom boundaries'}`}</p><div class="account-card-actions"><button class="button quiet" data-action="setup-account" data-id="${esc(account.id)}">Access and setup ${icon('arrow')}</button><button class="text-button account-more" data-action="${unset ? 'account-edit' : 'account-detail'}" data-id="${esc(account.id)}">${unset ? 'Set boundaries' : 'View identity details'}</button></div></article>`;
    }).join('')}</div>`;
  }
  function sourceRepresentative(item) {
    return reviewById(item?.review_id) || (item?.review_ids || []).map(reviewById).find(Boolean) || null;
  }
  function highlightRanges(item) {
    const text = String(item?.excerpt ?? '');
    const findings = Array.isArray(item?.findings) ? item.findings : [];
    const found = [];
    findings.forEach((finding,index) => {
      for (const quote of Array.isArray(finding?.quotes) ? finding.quotes : []) {
        if (typeof quote !== 'string' || !quote) continue;
        for (let at = text.indexOf(quote); at >= 0; at = text.indexOf(quote,at + 1)) found.push({start:at,end:at + quote.length,index,severity:finding.severity || 'suggestion',title:finding.title || 'Finding'});
      }
    });
    // An attention finding wins an overlap; otherwise the earlier, then the longer, range keeps the words.
    found.sort((a,b) => (a.severity === 'attention' ? 0 : 1) - (b.severity === 'attention' ? 0 : 1) || a.start - b.start || (b.end - b.start) - (a.end - a.start) || a.index - b.index);
    const kept = [];
    for (const range of found) if (!kept.some(saved => range.start < saved.end && saved.start < range.end)) kept.push(range);
    return kept.sort((a,b) => a.start - b.start);
  }
  function highlightedText(item) {
    // Ranges are found in the raw saved text; every emitted segment is escaped.
    const text = String(item?.excerpt ?? '');
    const ranges = highlightRanges(item);
    if (!ranges.length) return esc(text);
    let html = '', at = 0;
    for (const range of ranges) {
      html += `${esc(text.slice(at,range.start))}<mark class="flag-mark ${esc(range.severity)}" data-action="source-detail" data-id="${esc(item.id)}" data-finding="${range.index}" title="${esc(range.title)}">${esc(text.slice(range.start,range.end))}</mark>`;
      at = range.end;
    }
    return html + esc(text.slice(at));
  }
  function sourceStatusBadge(item, clickable = false) {
    const key = item?.status || 'needs_you';
    const tone = ['kept','verified','checked','resolved'].includes(key) ? 'strength' : ['waiting','verification_pending','snoozed'].includes(key) ? 'suggestion' : 'attention';
    const userPending = key === 'verification_pending' && sourceItemAttestations(item).some(attestation => ['needs_verification','blocked'].includes(attestation.execution_state));
    const label = userPending ? 'Done by you · verification pending' : item?.status_label || sourceStatusLabel[key] || key;
    const mark = `${icon(key === 'needs_you' ? 'warning' : ['kept','verified','checked','resolved'].includes(key) ? 'check' : 'clock')}${esc(label)}`;
    // On a card the status answers a click: it opens the source's explanation and next move.
    if (clickable && item?.id) return `<button type="button" class="badge badge-button ${tone}" data-action="source-detail" data-id="${esc(item.id)}" aria-label="${esc(label)} — open why and what to do next">${mark}</button>`;
    return `<span class="badge ${tone}">${mark}</span>`;
  }
  function sourceActions(item) {
    const review = sourceRepresentative(item);
    const actionable = Boolean(review);
    const exact = item.precise_source && actionable;
    return `<div class="source-actions" role="group" aria-label="Actions for ${esc(item.title || 'source item')}"><button class="button small quiet" data-action="source-keep" data-source="${esc(item.id)}" ${actionable ? '' : 'disabled'}>${icon('check')}Keep</button><button class="button small quiet" data-action="source-modify" data-source="${esc(item.id)}" ${exact ? '' : 'disabled'}>${icon('draft')}Modify…</button><button class="button small quiet" data-action="source-delete" data-source="${esc(item.id)}" ${exact ? '' : 'disabled'}>${icon('warning')}Delete…</button><button class="button small quiet" data-action="source-snooze" data-source="${esc(item.id)}" data-status="${item.status === 'snoozed' ? 'open' : 'snoozed'}">${icon(item.status === 'snoozed' ? 'undo' : 'clock')}${item.status === 'snoozed' ? 'Reopen' : 'Snooze'}</button></div>`;
  }
  function blockerMove(work,item,{attestation = false} = {}) {
    if (work?.execution_state !== 'blocked' || showingSamples()) return '';
    if (work.next_move === 'open_source') return safeUrl(work.source_url || item.source_url) ? sourceLink(work.source_url || item.source_url,'Open source to unblock') : '';
    if (work.next_move === 'fix_now') return `<button class="button small quiet" data-action="fix-now" data-source="${esc(item.id)}">Fix now ${icon('arrow')}</button>`;
    if (!attestation && work.next_move === 'confirm_request') return `<button class="button small quiet" data-action="request-detail" data-id="${esc(work.id)}">Review request ${icon('arrow')}</button>`;
    return '';
  }
  function editAvailabilityFor(item,request = null) {
    if (item?.edit_availability?.status === 'unavailable') return item.edit_availability;
    return request?.action === 'edit' && request.edit_preflight?.observation ? request.edit_preflight : item?.edit_availability || {status:'unknown',reason:'Your assistant has not checked whether this exact post can be edited.',observation:null};
  }
  function editingUnavailable(item) {return editAvailabilityFor(item).status === 'unavailable';}
  function editAvailabilityPanel(item,request = null,compact = false,offerNext = true) {
    if (!item?.precise_source) return '';
    const check = editAvailabilityFor(item,request), observed = check.observation;
    const labels = {available:'Reported editable',unavailable:'Editing unavailable',unknown:'Edit availability unknown',expired:'Edit check out of date',historical:'Edit history only',changed:'Edit availability changed'};
    const label = labels[check.status] || labels.unknown;
    if (compact) return `<p class="edit-availability-note"><strong>${esc(label)}.</strong> ${check.status === 'unavailable' ? esc(check.reason) : ''}<button class="text-button" data-action="edit-next-steps" data-source="${esc(item.id)}">${check.status === 'available' ? 'View check' : 'See next steps'}</button></p>`;
    return `<section class="identity-check-panel edit-availability-panel" aria-label="Edit availability"><div class="section-heading"><h3>Can this post be edited?</h3><span class="badge ${check.status === 'available' ? 'strength' : 'suggestion'}">${esc(label)}</span></div><p>${esc(check.reason)}</p>${observed ? `<dl class="detail-meta-list"><dt>Checked</dt><dd>${esc(dateText(observed.checked_at,true))}</dd>${observed.available_until ? `<dt>Observed edit deadline</dt><dd>${esc(dateText(observed.available_until,true))}</dd>` : ''}<dt>Exact source</dt><dd>${sourceLink(observed.source_url,'Post checked')}</dd></dl>` : ''}<p class="field-help">Checked by your assistant. Recheck the current controls before editing.</p>${offerNext && check.status !== 'available' ? `<button class="text-button" data-action="edit-next-steps" data-source="${esc(item.id)}">See next steps</button>` : ''}</section>`;
  }
  function editAlternatives(item) {
    return `<div class="fix-choice-grid stacked"><button class="decision-action" data-action="follow-up-draft" data-source="${esc(item.id)}" data-draft-kind="reply">${icon('draft')}<span><strong>Draft a follow-up reply</strong><small>Keep the original and explain beneath it</small></span></button><button class="decision-action" data-action="follow-up-draft" data-source="${esc(item.id)}" data-draft-kind="post">${icon('draft')}<span><strong>Draft a separate post</strong><small>Prepare a new message for separate review</small></span></button><button class="decision-action" data-action="source-keep" data-source="${esc(item.id)}">${icon('check')}<span><strong>Keep the original</strong><small>Record your choice in Res</small></span></button></div>`;
  }
  function editNextSteps(sourceId) {
    const item = sourceItemById(sourceId);
    if (!item) return;
    const request = activeRequestForSource(item);
    const body = `${editAvailabilityPanel(item,request,false,false)}${!editAvailabilityFor(item,request).observation ? `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}</div>` : ''}<p class="detail-text">Your assistant can recheck this post, or you can choose below.</p>${editAlternatives(item)}<p class="field-help">These are local drafts. Review and approve a follow-up separately before posting. The original stays in place.</p>${request ? `<p class="detail-text">Your existing request stays unchanged until you cancel or keep it.</p><button class="text-button" data-action="request-detail" data-id="${esc(request.id)}">Review existing request</button>` : ''}`;
    openDialog(dialogShell(editingUnavailable(item) ? 'This post cannot currently be edited' : 'Check editing before choosing','Choose the next step for this exact post.',body,`<button class="button quiet" data-action="source-detail" data-id="${esc(item.id)}">Back to source</button>`));
  }
  function followUpDraft(sourceId,kind) {
    const item = sourceItemById(sourceId), account = accountById(item?.account_id);
    if (!item?.precise_source || !account || !['reply','post'].includes(kind)) return;
    const key = [item.account_id,item.source_key,item.evidence_fingerprint,kind].join('|');
    if (!Object.hasOwn(state.followUpDrafts,key)) {
      if (Object.keys(state.followUpDrafts).length >= 20) {toast('Copy an existing follow-up draft before preparing another.');return;}
      const request = activeRequestForSource(item);
      state.followUpDrafts[key] = {text:request?.action === 'edit' ? request.proposed_text || '' : '',sourceId:item.id,accountId:item.account_id,parentUrl:item.source_url,fingerprint:item.evidence_fingerprint,kind};
    }
    state.followUpKey = key;
    const draft = state.followUpDrafts[key];
    const body = `<div class="request-alert"><strong>Unpublished draft · only in this tab</strong><p>The original remains unchanged. This does not replace or authorize an edit request.</p></div><dl class="detail-meta-list"><dt>Intended account</dt><dd>${esc(account.platform)} · ${esc(account.handle)}</dd><dt>Account boundary</dt><dd>${esc(identityLabel[account.identity_mode] || 'Not classified')} · ${account.sfw_only !== false ? 'SFW only' : 'Custom boundaries'}</dd><dt>${kind === 'reply' ? 'Reply beneath' : 'Original for context'}</dt><dd>${sourceLink(draft.parentUrl,'Exact original')}</dd></dl><div class="field request-editor"><label for="follow-up-text">${kind === 'reply' ? 'Follow-up reply draft' : 'Separate post draft'}</label><textarea id="follow-up-text" maxlength="20000">${esc(draft.text)}</textarea><span class="field-help">Review the account, destination, audience and any media with your assistant before publishing.${kind === 'reply' ? ' Reply availability has not been checked.' : ''}</span></div>`;
    openDialog(dialogShell(kind === 'reply' ? 'Draft a follow-up reply' : 'Draft a separate post','Prepare wording for a separate decision.',body,`<button class="button quiet" data-action="edit-next-steps" data-source="${esc(item.id)}">Back</button><button class="button primary" data-action="copy-follow-up" id="copy-follow-up" ${draft.text.trim() ? '' : 'disabled'}>Copy draft</button>`));
  }
  async function copyFollowUp() {
    const draft = state.followUpDrafts[state.followUpKey], item = sourceItemById(draft?.sourceId), editor = $('#follow-up-text');
    if (!draft || !item || !editor || draft.accountId !== item.account_id || draft.parentUrl !== item.source_url || draft.fingerprint !== item.evidence_fingerprint) {toast('The source changed. Review it again before copying this draft.');return;}
    draft.text = editor.value;
    if (!draft.text.trim()) return;
    try {await navigator.clipboard.writeText(draft.text);toast('Draft copied. Nothing was posted or changed.');}
    catch {editor.focus();editor.select();toast('Text selected. Use Copy from your device’s text menu.');}
  }
  function reviewContext(findings, compact = false) {
    const labels = {own_media:'Your attachment',parent_text:'Parent post',parent_media:'Parent image or video',thread:'Conversation'};
    const states = {inspected:'Inspected',not_inspected:'Not inspected',unavailable:'Unavailable'};
    const observations = [], seen = new Set();
    for (const finding of findings || []) for (const value of finding.context || []) {
      if (!labels[value?.kind] || !states[value?.inspection]) continue;
      const key = JSON.stringify(value);
      if (!seen.has(key)) {seen.add(key);observations.push(value);}
    }
    if (!observations.length) return '<p class="context-missing">Conversation and media context were not recorded for this review.</p>';
    const shown = observations.slice(0,compact ? 1 : 4);
    return `<section class="review-context" aria-label="Context for this review"><h4>Context for this review</h4>${shown.map(value => {
      const picture = value.image;
      const localImage = value.inspection === 'inspected' && ['own_media','parent_media'].includes(value.kind) &&
        ['image/png','image/jpeg','image/webp'].includes(picture?.mime_type) && typeof picture.base64 === 'string' &&
        picture.base64.length <= 349528 && /^[A-Za-z0-9+/]+={0,2}$/.test(picture.base64);
      return `<div class="context-observation"><div class="context-heading"><strong>${labels[value.kind]}</strong><span class="badge ${value.inspection === 'inspected' ? 'strength' : 'suggestion'}">${states[value.inspection]}</span></div><p>${esc(value.summary || '')}</p>${localImage && !compact ? `<figure><img src="data:${picture.mime_type};base64,${picture.base64}" alt="${esc(picture.alt || 'Saved context preview')}" loading="lazy" decoding="async" referrerpolicy="no-referrer"><figcaption>Saved preview · ${esc(picture.alt || '')}</figcaption></figure>` : ['own_media','parent_media'].includes(value.kind) ? `<p class="context-media-note">${localImage ? 'Saved image available in the full review.' : 'No image preview was saved.'}</p>` : ''}<div class="context-footer">${value.checked_at ? `<time datetime="${esc(value.checked_at)}">Checked ${esc(dateText(value.checked_at,true))}</time>` : '<span>Check date not recorded</span>'}${sourceLink(value.source_url,'Open context source')}</div></div>`;
    }).join('')}${observations.length > shown.length ? '<p class="context-media-note">More context is available in the full review.</p>' : ''}</section>`;
  }
  function sourceCard(item, compact = false) {
    const account = accountById(item.account_id);
    const findings = Array.isArray(item.findings) ? item.findings : [];
    const visibleFindings = compact ? findings.slice(0,2) : findings;
    const request = activeRequestForSource(item);
    const attestation = sourceItemAttestations(item).find(record => ['needs_verification','blocked'].includes(record.execution_state));
    const source = safeUrl(item.source_url);
    const blockedWork = request?.execution_state === 'blocked' ? request : attestation?.execution_state === 'blocked' ? attestation : null;
    const accountCheck = request?.execution_state === 'queued' && request.identity_preflight && request.identity_preflight.status !== 'matched' ? `<p class="source-work-note"><strong>Account check required.</strong> ${esc(request.identity_preflight.reason)}</p>` : '';
    const statusDetail = blockedWork ? `<div class="source-blocker"><p><strong>Blocked:</strong> ${esc(blockedWork.blocker_reason || (blockedWork === attestation ? 'Your recorded result could not be verified.' : 'The assistant could not finish this request.'))}</p>${blockerMove(blockedWork,item,{attestation:blockedWork === attestation})}</div>` : request?.execution_state === 'running' ? '<p class="source-work-note">Assistant work is running. The result still needs to be recorded.</p>' : ['needs_verification','reopened'].includes(request?.execution_state) ? '<p class="source-work-note">The earlier attempt needs verification before any retry.</p>' : '';
    return `<article class="review-card source-card panel${compact ? ' compact' : ''}" data-source-id="${esc(item.id)}"><div class="review-heading"><div class="review-metadata">${platformIcon(account)}<span>${esc(account?.platform || 'Account')}</span><span>·</span><span>${esc(handleOf(account) || item.identity_note || 'Identity not recorded')}</span></div>${sourceStatusBadge(item,true)}</div><div class="review-body"><h3><button class="review-title-button" data-action="source-detail" data-id="${esc(item.id)}">${esc(item.title || 'Saved source')}</button></h3>${item.excerpt ? `<blockquote class="review-excerpt" tabindex="0" aria-label="Saved text for ${esc(item.title || 'this source')}. Scrolls when it is longer than the box."><span>Saved excerpt · may be partial</span><div class="excerpt-text">${highlightedText(item)}</div></blockquote>` : '<p>No excerpt was saved for this source.</p>'}${reviewContext(findings,compact)}${editAvailabilityPanel(item,request,true)}<div class="finding-chips" aria-label="Findings">${visibleFindings.map((finding,index) => `<button class="finding-chip ${esc(finding.severity || '')}" data-action="source-detail" data-id="${esc(item.id)}" data-finding="${index}">${esc(finding.title || 'Finding')}</button>`).join('')}${compact && findings.length > visibleFindings.length ? `<button class="finding-chip more" data-action="source-detail" data-id="${esc(item.id)}">+${findings.length - visibleFindings.length} more</button>` : ''}</div>${attestation && item.status === 'verification_pending' ? `<div class="source-attestation"><strong>${esc(requestActionLabel[attestation.action] || attestation.action)} · ${esc(dateText(attestation.done_at || attestation.created_at,true))}</strong><p>${esc(attestation.note)}</p></div>` : ''}${item.source_changed ? '<p class="source-changed">Source or review evidence changed. Earlier closure does not apply to this snapshot.</p>' : ''}${statusDetail}${accountCheck}${queuedRequestText(request) ? `<p class="source-work-note queued-request-note">${esc(queuedRequestText(request))}</p>` : ''}${!item.precise_source ? `<p class="source-required-note">${esc(item.identity_note || 'A precise original link is required before edit or delete work can be saved.')}</p>` : ''}</div><div class="review-card-footer"><div>${source ? sourceLink(source,'Open original') : ''}${request ? `<button class="text-button card-request-link" data-action="request-detail" data-id="${esc(request.id)}">${requestBadge(request)}View work ${icon('arrow')}</button>` : ''}</div>${sourceActions(item)}</div></article>`;
  }
  function requestBadge(request) {
    const execution = request.execution_state;
    const needsReview = request.status === 'pending' && request.confirmation_required === true;
    const labels = {queued:'Queued',running:'Running',needs_verification:'Needs verification',blocked:'Blocked',verified:'Verified',reopened:'Reopened',cancelled:'Cancelled'};
    const status = needsReview ? 'Needs your review' : labels[execution] || requestStatusLabel[request.status] || request.status;
    const tone = needsReview ? 'needs-review' : ['verified'].includes(execution) || request.status === 'completed' ? 'completed' : ['blocked','reopened'].includes(execution) ? 'blocked' : esc(execution || request.status);
    return `<span class="request-status ${tone}">${needsReview || ['blocked','reopened'].includes(execution) ? icon('warning') : execution === 'verified' || request.status === 'completed' ? icon('check') : execution === 'cancelled' || request.status === 'cancelled' ? icon('undo') : icon('clock')}${esc(status)}</span>`;
  }
  function requestBadgeText(request) {
    if (request.status === 'pending' && request.confirmation_required === true) return 'Needs your review';
    return ({queued:'Queued',running:'Running',needs_verification:'Needs verification',blocked:'Blocked',verified:'Verified',reopened:'Reopened',cancelled:'Cancelled'})[request.execution_state] || requestStatusLabel[request.status] || request.status;
  }
  function queuedRequestText(request) {
    if (request?.execution_state !== 'queued' || !['edit','delete'].includes(request.action)) return '';
    const timing = request.scheduled_for ? `next check ${dateText(request.scheduled_for,true)}` : 'no next check is scheduled — ask your assistant to run your Resonance check';
    return `Asked your assistant to ${request.action} · ${request.action === 'delete' ? 'not deleted yet' : 'not changed yet'} · ${timing} · it asks your yes first`;
  }
  function scheduledText(request) {
    if (request.execution_state !== 'queued' || request.confirmation_required === true) return '';
    const value = request.scheduled_for;
    return value ? `Next scheduled check · ${dateText(value,true)}` : 'Next scheduled check · not available';
  }
  function renderRequests() {
    const requests = [...actionRequests()].sort((a,b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0));
    const active = requests.filter(request => ['queued','running','needs_verification','blocked','reopened'].includes(request.execution_state) || (request.status === 'pending' && request.execution_state !== 'cancelled'));
    const history = requests.filter(request => !active.includes(request));
    const waiting = sourceItems().filter(item => item.status === 'waiting').length;
    const verification = sourceItems().filter(item => item.status === 'verification_pending').length;
    const blocked = active.filter(request => ['blocked','reopened'].includes(request.execution_state)).length;
    return `<section class="request-section compact-work" id="action-requests" tabindex="-1" aria-labelledby="request-heading"><div><div class="eyebrow">ASSISTANT WORK</div><h2 id="request-heading"><button class="count-button" data-action="requests-explain">${active.length ? `${active.length} active ${active.length === 1 ? 'request' : 'requests'}` : 'No active requests'}</button></h2><p>Expected check times are not completion promises. Open a source for its exact request and history.</p></div><div class="compact-work-links"><button class="compact-work-link" data-action="review-status" data-status="waiting"><strong>${waiting}</strong><span>Waiting</span></button><button class="compact-work-link" data-action="review-status" data-status="verification_pending"><strong>${verification}</strong><span>Verification</span></button>${blocked ? `<button class="compact-work-link attention" data-action="review-status" data-status="needs_you"><strong>${blocked}</strong><span>Blocked or reopened</span></button>` : ''}${history.length ? `<button class="compact-work-link" data-action="review-status" data-status="history"><strong>${history.length}</strong><span>Request history</span></button>` : ''}</div></section>`;
  }
  function renderReview() {
    if (!state.data.accounts.length) return heading('A little perspective.','Keep feedback you can act on. Leave the rest.', '', 'YOUR POSTS') + emptyState('An account comes first.','Add an account, then save a specific review or excerpt to consider.',addAccountButton(),'review');
    const statusTabs = [['needs_you','Needs you'],['waiting','Waiting'],['verification_pending','Verification'],['snoozed','Snoozed'],['history','History'],['all','All']].map(([status,label]) => {
      const historyStates = ['kept','verified','checked','resolved'];
      const count = sourceItems().filter(item => status === 'all' || status === 'history' ? status === 'all' || historyStates.includes(item.status) : item.status === status).length;
      return `<button class="status-tab ${state.filters.status === status ? 'active' : ''}" data-action="review-status" data-status="${status}" role="tab" aria-selected="${state.filters.status === status}">${label}<span class="tab-count">${count}</span></button>`;
    }).join('');
    return heading('Your posts.','One source, all its findings, one next move.',addReviewButton(),'POSTS') + renderRequests() + `<div class="filters-panel"><div class="status-tabs" role="tablist" aria-label="Post status">${statusTabs}</div><div class="toolbar filter-controls"><label class="search-field">${icon('search')}<span class="sr-only">Search posts and findings</span><input id="review-search" type="search" value="${esc(state.filters.query)}" placeholder="Find a post or phrase…" autocomplete="off"></label><label class="sr-only" for="account-filter">Filter by account</label><select id="account-filter" class="filter-select"><option value="all">All accounts</option>${state.data.accounts.map(account => `<option value="${esc(account.id)}" ${state.filters.account === account.id ? 'selected' : ''}>${esc(account.platform)} · ${esc(handleOf(account))}</option>`).join('')}</select><label class="sr-only" for="severity-filter">Filter findings</label><select id="severity-filter" class="filter-select"><option value="all">All findings</option>${Object.entries(severityLabel).map(([value,label]) => `<option value="${value}" ${state.filters.severity === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div></div><div id="review-results">${reviewResults()}</div><div class="scope-strip">${icon('info')}<span>Each card groups saved evidence for one account and source. Findings remain inspectable. Only a verified outcome proves a platform result.</span></div>`;
  }
  function reviewResults() {
    const {status,account,severity,query} = state.filters;
    const q = query.trim().toLowerCase();
    const historyStates = ['kept','verified','checked','resolved'];
    const items = sourceItems().filter(item => (status === 'all' || status === 'history' ? status === 'all' || historyStates.includes(item.status) : item.status === status) && (account === 'all' || item.account_id === account) && (severity === 'all' || (item.findings || []).some(finding => finding.severity === severity)) && [item.title,item.excerpt,item.identity_note,...(item.findings || []).flatMap(finding => [finding.title,finding.detail])].join(' ').toLowerCase().includes(q));
    let html = `<div class="review-results-header"><button class="text-button" data-action="posts-view-explain">${items.length} ${items.length === 1 ? 'source' : 'sources'} in this view</button><button class="text-button" data-action="clear-filters">Reset filters</button></div>`;
    if (status === 'snoozed') html += '<p class="status-description">Snoozed sources return when reopened or when the saved snooze ends.</p>';
    if (items.length) html += `<div class="review-grid source-grid">${items.map(item => sourceCard(item)).join('')}</div>`;
    else html += emptyState('Nothing in this view.',sourceItems().length ? 'Try another filter, or add fresh context.' : 'Add a review or check a post. Clean checks stay in history without becoming attention work.',`<button class="button quiet" data-action="add-review">${icon('plus')}Add review</button>`, 'check');
    return html;
  }
  function renderDraft() {
    return heading('Make your point.','A quiet place to shape what you want to say.','','COMPOSE') + `<div class="draft-layout"><section class="draft-editor-panel panel" aria-label="Draft editor"><div class="draft-editor-header"><strong>Your next post, comment, or reply</strong><span>Only in this tab</span></div><label class="sr-only" for="draft-text">Write your draft</label><textarea id="draft-text" class="draft-editor" maxlength="20000" spellcheck="true" placeholder="What do you want to say?&#10;&#10;Start with the thought. We’ll help you look at the wording.">${esc(state.draft)}</textarea><div class="draft-editor-footer"><div class="draft-count" id="draft-count">${draftCount()}</div><div class="draft-actions"><button class="button small quiet" data-action="clear-draft" ${!state.draft ? 'disabled' : ''}>Clear</button><button class="button small quiet" data-action="copy-draft" ${!state.draft ? 'disabled' : ''}>${icon('copy')}Copy draft</button></div></div></section><aside class="draft-check-panel panel"><div class="eyebrow">${icon('shield')}A SECOND LOOK</div><h2>Clear words. Your voice.</h2><p>Check a few common writing patterns and your account’s saved boundaries.</p><div class="field draft-account-field"><label for="draft-account">Writing as</label><select id="draft-account"><option value="">General writing check</option>${state.data.accounts.map(account => `<option value="${esc(account.id)}" ${state.draftAccount === account.id ? 'selected' : ''}>${esc(account.platform)} · ${esc(handleOf(account))}</option>`).join('')}</select><div id="draft-policy-note" class="field-help">${draftPolicyNote()}</div></div><button class="button primary" data-action="check-draft" id="check-draft-button" ${!state.draft.trim() ? 'disabled' : ''}>${icon('review')}Check wording</button><div id="draft-results" aria-live="polite"></div><div class="draft-scope">Limited local checks, not AI or an audience prediction. Your text stays on this computer and isn’t saved by the check. Nothing is posted.</div></aside></div><div class="draft-prompts"><div class="draft-prompt"><h3>Keep your point.</h3><p>Let the main thought do the work. You don’t have to soften a clear opinion.</p></div><div class="draft-prompt"><h3>Give it context.</h3><p>A little explanation helps the reader hear what you actually mean.</p></div><div class="draft-prompt"><h3>Read it as a stranger.</h3><p>Would the words still land without your tone of voice?</p></div></div>`;
  }
  function draftCount() { const words = state.draft.trim() ? state.draft.trim().split(/\s+/u).length : 0; return `<span>${words} ${words === 1 ? 'word' : 'words'}</span><span>${state.draft.length.toLocaleString()} characters</span>`; }
  function draftPolicyNote() {
    const account = accountById(state.draftAccount);
    if (!account) return 'Choose an account to include its boundary checks.';
    if (!account.identity_mode || account.identity_mode === 'unclassified') return 'Identity not set. Careful, SFW defaults apply until you set boundaries.';
    return `${esc(identityLabel[account.identity_mode])} · ${esc(daringLabel[account.daring] || 'Careful')} · ${account.sfw_only !== false ? 'SFW only' : 'Custom boundaries'}. ${account.identity_mode === 'alias' ? 'An alias does not guarantee anonymity.' : 'Real-name accounts require SFW at every daring level.'}`;
  }
  function updateDraftResult() {
    const target = $('#draft-results');
    if (!target) return;
    if (state.draftBusy) target.innerHTML = '<p class="draft-result-summary">Checking the current wording…</p>';
    else if (state.draftError) target.innerHTML = `<p class="form-error draft-result-summary">${esc(state.draftError)}</p>`;
    else if (!state.draftResult) target.innerHTML = '<p class="draft-result-summary">Your words lead. The check follows.</p>';
    else target.innerHTML = `<p class="draft-result-summary">${esc(state.draftResult.summary)}</p>${state.draftResult.signals.length ? `<div class="draft-signals">${state.draftResult.signals.map((signal,index) => `<button class="draft-signal" data-action="draft-signal" data-index="${index}"><strong>${esc(signal.title)}</strong>${esc(signal.detail)}<span class="signal-jump">Show this wording ${icon('arrow')}</span></button>`).join('')}</div>` : '<div class="draft-clear-note">No flags from these limited checks. Give it one last read in context.</div>'}`;
    const button = $('#check-draft-button');
    if (button) button.disabled = state.draftBusy || !state.draft.trim();
  }
  async function checkDraft() {
    clearTimeout(draftTimer);
    if (!state.draft.trim()) { state.draftResult = null; state.draftBusy = false; updateDraftResult(); return; }
    const sequence = ++state.draftSequence;
    state.draftBusy = true;
    state.draftError = '';
    updateDraftResult();
    try {
      const result = await api('/api/draft-check','POST',{text:state.draft,...(state.draftAccount ? {account_id:state.draftAccount} : {})});
      if (sequence !== state.draftSequence) return;
      state.draftResult = result;
    } catch(error) { if (sequence === state.draftSequence) state.draftError = error.message; }
    finally { if (sequence === state.draftSequence) {state.draftBusy = false;updateDraftResult();} }
  }
  function dialogShell(title, subtitle, body, footer = '', form = '') {
    return `${form ? `<form id="${esc(form)}">` : ''}<div class="dialog-header"><div><h2 id="dialog-title">${esc(title)}</h2>${subtitle ? `<p>${esc(subtitle)}</p>` : ''}</div><button class="icon-button" type="button" data-action="close-dialog" aria-label="Close dialog">${icon('close')}</button></div><div class="dialog-body"><div class="form-error" id="dialog-error" role="alert" hidden></div>${body}</div>${footer ? `<div class="dialog-footer">${footer}</div>` : ''}${form ? '</form>' : ''}`;
  }
  function openDialog(html) {
    if (!dialog.open) dialogReturnFocus = document.activeElement;
    $('#dialog-content').innerHTML = html;
    if (!dialog.open) dialog.showModal();
    dialog.scrollTop = 0;
    const firstInput = $('input:not([type=hidden]),select,textarea',dialog);
    if (firstInput) firstInput.focus({preventScroll:true});
    else $('[data-action="close-dialog"]',dialog)?.focus({preventScroll:true});
  }
  function closeDialog() {
    dialog.close();
    if (dialogReturnFocus?.isConnected) dialogReturnFocus.focus({preventScroll:true});
  }
  function dialogError(error) {
    const target = $('#dialog-error');
    if (!target) {toast(error.message || String(error)); return;}
    target.textContent = error.message || String(error);
    target.hidden = false;
    target.scrollIntoView({block:'nearest',behavior:'instant'});
  }
  function boundaryFields(account = {}, isMail = false) {
    const identity = account.identity_mode || 'unclassified';
    const daring = account.daring || 'careful';
    return `<div class="boundary-settings"><div class="detail-label">HOW YOU WANT TO SHOW UP</div><div class="form-grid"><div class="field"><label for="identity-mode">Identity</label><select id="identity-mode" name="identity_mode"><option value="unclassified" ${identity === 'unclassified' ? 'selected' : ''}>Choose later · SFW defaults</option><option value="named" ${identity === 'named' ? 'selected' : ''}>My real name</option><option value="alias" ${identity === 'alias' ? 'selected' : ''}>An alias</option></select></div><div class="field"><label for="daring">How daring?</label><select id="daring" name="daring"><option value="careful" ${daring === 'careful' ? 'selected' : ''}>Careful</option><option value="balanced" ${daring === 'balanced' ? 'selected' : ''}>Balanced</option><option value="bold" ${daring === 'bold' ? 'selected' : ''}>Bold</option></select></div><div class="field full check-field"><label for="sfw-only"><input id="sfw-only" name="sfw_only" type="checkbox" ${identity !== 'alias' || account.sfw_only !== false ? 'checked' : ''} ${identity !== 'alias' ? 'disabled' : ''}><span>Keep this account safe for work (SFW).</span></label><span class="field-help" id="identity-help">${identity === 'alias' ? 'An alias does not guarantee anonymity. Turning this off relaxes content checks for this account.' : 'Real-name and unclassified accounts always require SFW, at every daring level.'}</span></div></div><p class="boundary-explainer" id="boundary-explainer">${isMail ? 'These choices guide checks on replies and other outgoing writing. Incoming mail previews are not reputation or boundary findings.' : 'Daring changes how much edge you want in your wording. SFW is separate: explicit, profane, or hostile language can still be flagged. Checks use saved excerpts only.'}</p></div>`;
  }
  function accountForm(account, initialPlatform = '') {
    if (!account) {startAccountWalk(initialPlatform);return;}
    const editing = Boolean(account);
    const profile = account || {platform:initialPlatform || platforms[0]};
    const isMail = mailAccount(profile);
    const body = `<div class="form-grid">${editing ? `<input type="hidden" name="id" value="${esc(profile.id)}"><div class="field full"><span class="field-label">Platform</span><span class="account-platform-readonly">${platformIcon(profile)}${esc(profile.platform)}</span></div>` : `<div class="field full"><label for="platform">Platform</label><select name="platform" id="platform">${platforms.map(platform => `<option value="${platform}" ${profile.platform === platform ? 'selected' : ''}>${platform}</option>`).join('')}</select></div>`}<div class="field"><label for="handle" id="account-handle-label">${isMail ? 'Email address' : 'Handle or username'}</label><input id="handle" name="handle" required maxlength="200" placeholder="${isMail ? 'name@example.com' : 'Your account handle'}" value="${esc(profile.handle)}" autocomplete="off"></div><div class="field"><label for="display-name">Display name <span>optional</span></label><input id="display-name" name="display_name" maxlength="200" placeholder="A name you recognize" value="${esc(profile.display_name)}" autocomplete="off"></div><div class="field full"><label for="profile-url" id="account-url-label">${isMail ? 'Mailbox link' : 'Profile link'} <span>optional</span></label><input id="profile-url" name="url" type="url" maxlength="2048" placeholder="https://…" value="${esc(profile.url)}" autocomplete="off"></div></div>${boundaryFields(profile,isMail)}${editing ? `<details class="form-details"><summary>Record an access snapshot</summary><p class="field-help snapshot-help">This records your own check. It does not verify access or create a connection.</p><div class="field check-field"><label for="access-check"><input id="access-check" name="record_access" type="checkbox"><span>I checked that I could access this account. Record a snapshot now.</span></label></div><div class="field"><label for="access-note">Snapshot note <span>optional</span></label><textarea id="access-note" name="access_note" maxlength="2000" placeholder="What you checked, and any limits">${esc(profile.access_note)}</textarea><span class="field-help">${esc(snapshotText(profile))}</span></div></details>` : '<div class="scope-strip">' + icon('info') + `<span id="account-kind-note">${isMail ? 'This saves a mailbox profile. It does not connect, read, or send mail.' : 'This adds a saved profile. Resonance won’t sign in, connect, or read the account.'}</span></div>`}`;
    openDialog(dialogShell(editing ? 'Your account. Your boundaries.' : 'Bring an account into view.',editing ? `${profile.platform} · ${handleOf(profile)}` : 'Start with a profile you want to keep in your brief.',body,`<button class="button quiet" type="button" data-action="${editing ? 'account-detail' : 'close-dialog'}" ${editing ? `data-id="${esc(profile.id)}"` : ''}>Cancel</button><button class="button primary" type="submit">${editing ? 'Save account' : 'Add account'} ${icon('arrow')}</button>`,'account-form'));
  }
  function readAccountIdentity(platform, input) {
    const raw = input.trim();
    if (!raw) throw new Error('Enter your profile link or handle.');
    if (['Gmail','Outlook'].includes(platform)) {
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)) throw new Error('Enter your email address; a mailbox link does not identify your account.');
      return {handle:raw,url:platform === 'Gmail' ? 'https://mail.google.com/' : 'https://outlook.live.com/mail/'};
    }
    const routes = {YouTube:['youtube.com','@'],Facebook:['facebook.com',''],Instagram:['instagram.com',''],X:['x.com',''],Reddit:['reddit.com','user/'],TikTok:['tiktok.com','@'],LinkedIn:['linkedin.com','in/'],Substack:['substack.com','@']};
    if (/^https?:\/\//i.test(raw)) {
      const parsed = new URL(raw), host = parsed.hostname.replace(/^www\./,'').toLowerCase();
      if (parsed.username || parsed.password || parsed.hash || parsed.protocol !== 'https:') throw new Error('Use the full https profile link, without a password or fragment.');
      const route = routes[platform];
      const allowed = route && (host === route[0] || platform === 'X' && host === 'twitter.com' || platform === 'Substack' && host.endsWith('.substack.com'));
      if (!allowed) throw new Error(`Use a ${platform} profile link, or enter your handle.`);
      const parts = parsed.pathname.split('/').filter(Boolean).map(decodeURIComponent);
      let handle = '';
      if (platform === 'Reddit' && ['user','u'].includes(parts[0]) && parts.length === 2) handle = parts[1];
      else if (platform === 'LinkedIn' && parts[0] === 'in' && parts.length === 2) handle = parts[1];
      else if (platform === 'YouTube' && parts[0] === 'channel' && parts.length === 2) handle = parts[1];
      else if (platform === 'Substack' && host.endsWith('.substack.com') && !parts.length) handle = host.slice(0,-13);
      else if (platform === 'Facebook' && parts[0] === 'profile.php' && parsed.searchParams.get('id')) handle = parsed.searchParams.get('id');
      else if (parts.length === 1 && !['Reddit','LinkedIn'].includes(platform)) handle = parts[0].replace(/^@/,'');
      if (!handle || /^(home|login|accounts|feed|watch|explore|search|settings|i|notifications|profile\.php)$/i.test(handle) || /[\s/?#]/.test(handle)) throw new Error('That link does not identify a profile. Paste the profile link or enter your handle.');
      return {handle,url:parsed.href};
    }
    const handle = raw.replace(/^@/,'').replace(/^u\//,'');
    if (!/^[\p{L}\p{N}_.-]{1,200}$/u.test(handle)) throw new Error('Use a handle without spaces, or paste the full profile link.');
    const route = routes[platform];
    return {handle,url:route ? `https://${route[0]}/${route[1]}${encodeURIComponent(handle)}` : ''};
  }
  function startAccountWalk(platform = '') {
    accountWalk = {step:platforms.includes(platform) ? 'identity' : 'platform',platform,handle:'',url:'',input:'',identity_mode:'named',daring:'careful',sfw_only:true,saved:null};
    accountWalkDialog();
  }
  function accountWalkDialog() {
    if (!accountWalk) return;
    const walk = accountWalk;
    const progress = `<div class="walk-progress" aria-label="Saved accounts">${state.data.accounts.length ? state.data.accounts.map(account => `<span>${platformIcon(account)}${esc(handleOf(account))}</span>`).join('') : '<span>No accounts saved yet</span>'}</div>`;
    let body, footer, form = '';
    if (walk.step === 'platform') {
      body = `<p>Pick the platform for this account.</p><div class="platform-picker">${platforms.map(platform => `<button type="button" data-action="walk-platform" data-platform="${esc(platform)}">${platformIcon({platform})}<strong>${esc(platform)}</strong></button>`).join('')}</div>`;
      footer = '<button type="button" class="button quiet" data-action="close-dialog">Cancel</button>';
    } else if (walk.step === 'identity') {
      form = 'walk-identity-form';
      const isMail = ['Gmail','Outlook'].includes(walk.platform);
      body = `<div class="field"><label for="walk-identity">${isMail ? 'Your email address' : 'Your profile link or handle'}</label><input id="walk-identity" name="identity" required maxlength="2048" value="${esc(walk.input)}" autocomplete="off"><p class="field-help">${isMail ? 'This saves a mailbox profile. It does not read or send mail.' : 'We will show what we understood before saving it.'}</p></div>`;
      footer = '<button type="button" class="button quiet" data-action="walk-back" data-step="platform">Back</button><button type="submit" class="button primary">Next</button>';
    } else if (walk.step === 'boundaries') {
      form = 'walk-boundaries-form';
      body = `<div class="identity-reading"><strong>${esc(walk.platform)} · ${esc(walk.handle)}</strong><p>${walk.url ? esc(walk.url) : 'A handle only; no public profile link was inferred.'}</p><button type="button" class="text-button" data-action="walk-back" data-step="identity">Change this</button></div><div class="field"><label for="identity-mode">Is this under your real name or an alias?</label><select id="identity-mode" name="identity_mode"><option value="named" ${walk.identity_mode === 'named' ? 'selected' : ''}>My real name</option><option value="alias" ${walk.identity_mode === 'alias' ? 'selected' : ''}>An alias</option></select><p class="field-help">Accounts under your real name always stay safe for work; an alias is not a promise of anonymity.</p></div><div class="field"><label for="walk-daring">How daring may this account be?</label><select id="walk-daring" name="daring">${Object.entries(daringLabel).map(([value,label]) => `<option value="${value}" ${walk.daring === value ? 'selected' : ''}>${label}</option>`).join('')}</select><p class="field-help">Daring changes how much edge you want in your wording; it never overrides safe-for-work boundaries.</p></div><div class="field check-field"><label><input id="sfw-only" name="sfw_only" type="checkbox" ${walk.sfw_only ? 'checked' : ''} ${walk.identity_mode !== 'alias' ? 'disabled' : ''}>Keep this account safe for work</label><p class="field-help" id="identity-help">${walk.identity_mode === 'alias' ? 'An alias does not guarantee anonymity. Turning this off relaxes content checks for this account.' : 'Real-name accounts always require safe-for-work wording.'}</p></div>`;
      footer = '<button type="button" class="button quiet" data-action="walk-back" data-step="identity">Back</button><button type="submit" class="button primary">Save account</button>';
    } else {
      const signIn = safeUrl(signInUrls[walk.platform]);
      body = `<p class="walk-saved">${icon('check')} ${esc(walk.handle)} is saved.</p><p>Resonance never sees your password; signing in in your browser lets your assistant read this account.</p><a class="button primary" href="${esc(signIn)}" target="_blank" rel="noopener noreferrer">Sign in to ${esc(walk.platform)} in your browser ${icon('external')}</a><p class="field-help">Saving this account does not verify a sign-in or read any posts.</p>`;
      footer = '<button type="button" class="button quiet" data-action="add-account">Add another</button><button type="button" class="button primary" data-action="account-walk-done">Done</button>';
    }
    const step = {platform:1,identity:2,boundaries:3,signin:4}[walk.step];
    openDialog(dialogShell('Bring in my accounts',`Step ${step} of 4${walk.platform ? ' · ' + walk.platform : ''}`,progress+body,footer,form));
  }
  async function howPostsArrive() {
    openDialog(dialogShell('How do my posts get here?','Your accounts are saved.',`<div class="arrival-doors"><button class="button primary" data-action="connect-assistant">Connect your assistant ${icon('copy')}</button><button class="button quiet" data-action="import-from-welcome">I have a Resonance backup</button><span>Import an archive <small>Coming next</small></span></div>`,'<button class="button quiet" data-action="close-dialog">Done</button>'));
  }
  async function connectAssistant() {
    const result = await api('/api/assistant');
    openDialog(dialogShell('Connect your assistant','Give this sentence to your own AI assistant.',`<p>Your assistant needs a browser and the ability to run commands on this computer.</p><div class="field"><label for="assistant-sentence">One sentence to copy</label><textarea id="assistant-sentence" readonly>${esc(result.prompt)}</textarea></div><p>The assistant reads the contract in that folder before it begins.</p>`,'<button class="button quiet" data-action="close-dialog">Done</button><button class="button primary" data-action="copy-assistant">Copy sentence</button>'));
  }
  function updateAccountFieldLabels(platform) {
    const isMail = ['Gmail','Outlook'].includes(platform);
    const handleLabel = $('#account-handle-label'), handle = $('#handle'), urlLabel = $('#account-url-label');
    if (handleLabel) handleLabel.textContent = isMail ? 'Email address' : 'Handle or username';
    if (handle) handle.placeholder = isMail ? 'name@example.com' : 'Your account handle';
    if (urlLabel) urlLabel.innerHTML = `${isMail ? 'Mailbox link' : 'Profile link'} <span>optional</span>`;
    const note = $('#account-kind-note');
    if (note) note.textContent = isMail ? 'This saves a mailbox profile. It does not connect, read, or send mail.' : 'This adds a saved profile. Resonance won’t sign in, connect, or read the account.';
    const explainer = $('#boundary-explainer');
    if (explainer) explainer.textContent = isMail ? 'These choices guide checks on replies and other outgoing writing. Incoming mail previews are not reputation or boundary findings.' : 'Daring changes how much edge you want in your wording. SFW is separate: explicit, profane, or hostile language can still be flagged. Checks use saved excerpts only.';
  }
  function accountDetail(id) {
    const account = accountById(id);
    if (!account) return;
    const reviews = reviewsFor(id), flags = flagsFor(id);
    const unset = !account.identity_mode || account.identity_mode === 'unclassified';
    const reviewedDates = reviews.map(review => review.reviewed_at).filter(Boolean).sort();
    const access = accessState(account);
    const body = `<div class="detail-account">${platformIcon(account)}<div><h3>${esc(nameOf(account))}</h3><p>${esc(handleOf(account))}</p>${sourceLink(account.url,'Open profile')}</div></div><div class="detail-stats"><div class="detail-stat"><strong>${reviews.length}</strong><span>observations</span></div><div class="detail-stat"><strong>${reviews.filter(review => review.status === 'open').length}</strong><span>open feedback</span></div><div class="detail-stat"><strong>${flags.filter(flag => flag.status === 'open').length}</strong><span>boundary flags</span></div></div><div class="detail-block"><div class="section-heading"><div class="detail-label">ACCOUNT BOUNDARIES</div><button class="text-button" data-action="account-edit" data-id="${esc(id)}">${unset ? 'Set boundaries' : 'Edit'} ${icon('arrow')}</button></div><p class="detail-text">${unset ? 'Identity is not set. Careful, SFW defaults apply until you choose how you want to show up.' : `${esc(identityLabel[account.identity_mode])} · ${esc(daringLabel[account.daring] || 'Careful')} · ${account.sfw_only !== false ? 'SFW only' : 'Custom boundaries'}. ${account.identity_mode === 'alias' ? 'An alias does not guarantee anonymity.' : 'Real-name accounts require SFW at every daring level.'}`}</p></div><div class="dialog-subsection"><h3>What’s in view</h3><p>${reviews.length ? `${reviews.length} saved review ${reviews.length === 1 ? 'observation' : 'observations'}. Latest review: ${esc(dateText(reviewedDates.at(-1)))}. This covers only the evidence saved here.` : 'No review evidence has been saved for this account. An added profile does not imply a reviewed account.'}</p><button class="button quiet small" data-action="account-reviews" data-id="${esc(id)}">View account posts ${icon('arrow')}</button></div><div class="dialog-subsection"><h3>Access evidence</h3><p><strong>${esc(access.label)}</strong>${access.record?.checked_at ? ` · ${esc(dateText(access.record.checked_at,true))}` : ''}. ${esc(access.detail)}</p><button class="text-button snapshot-edit" data-action="setup-account" data-id="${esc(id)}">View access evidence ${icon('arrow')}</button></div>`;
    openDialog(dialogShell(account.platform,'Account details',body,`<button class="button quiet" data-action="close-dialog">Done</button><button class="button primary" data-action="add-review" data-account="${esc(id)}">${icon('plus')}Add review</button>`));
  }
  function excerptForm(accountId = '') {
    if (!state.data.accounts.length) { accountForm(); return; }
    const body = `<div class="form-grid"><div class="field full"><label for="excerpt-account">Which account posted this?</label><select id="excerpt-account" name="account_id">${state.data.accounts.map(account => `<option value="${esc(account.id)}" ${accountId === account.id ? 'selected' : ''}>${esc(account.platform)} · ${esc(handleOf(account))}</option>`).join('')}</select></div><div class="field"><label for="excerpt-kind">Type of activity</label><select id="excerpt-kind" name="kind"><option value="post">Post</option><option value="comment">Comment</option><option value="reply">Reply</option></select></div><div class="field"><label for="excerpt-title">Label <span>optional</span></label><input id="excerpt-title" name="title" maxlength="240" placeholder="Something you will recognize"></div><div class="field full"><label for="excerpt-text">Your original text</label><textarea id="excerpt-text" name="text" required maxlength="20000" placeholder="Paste your own post, comment, or reply."></textarea><span class="field-help">Use your own authored text. Leave private messages out.</span></div><div class="field full"><label for="excerpt-url">Link to the original <span>optional</span></label><input id="excerpt-url" name="source_url" type="url" maxlength="2048" placeholder="https://…"></div></div><div class="scope-strip">${icon('shield')}<span>This saves the excerpt and checks it against the account’s boundaries. Results are prompts for your review. Images, video and surrounding conversation are not checked.</span></div>`;
    openDialog(dialogShell('Does this fit the account?','Save an excerpt for a check against its daring and SFW settings.',body,`<button class="button quiet" type="button" data-action="close-dialog">Cancel</button><button class="button primary" type="submit">Save & check ${icon('arrow')}</button>`,'excerpt-form'));
  }
  function reviewForm(accountId = '', severity = 'suggestion') {
    if (!state.data.accounts.length) {accountForm();return;}
    const body = `<div class="form-grid"><div class="field full"><label for="review-account">Account</label><select name="account_id" id="review-account">${state.data.accounts.map(account => `<option value="${esc(account.id)}" ${accountId === account.id ? 'selected' : ''}>${esc(account.platform)} · ${esc(handleOf(account))}</option>`).join('')}</select></div><div class="field"><label for="review-kind">What are you reviewing?</label><select id="review-kind" name="kind">${Object.entries(kindLabel).map(([key,label]) => `<option value="${key}">${label}</option>`).join('')}</select></div><div class="field"><label for="review-severity">What kind of feedback?</label><select id="review-severity" name="severity">${Object.entries(severityLabel).map(([key,label]) => `<option value="${key}" ${severity === key ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="field full"><label for="review-title">Give this observation a title</label><input id="review-title" name="title" required maxlength="240" placeholder="The main thing worth remembering"></div><div class="field full"><label for="review-excerpt">Your original wording <span>optional</span></label><textarea id="review-excerpt" name="excerpt" maxlength="12000" placeholder="Paste the part of your own post, comment, or reply that gives context."></textarea><span class="field-help">Boundary checks use this excerpt only. Don’t include private messages.</span></div><div class="field full"><label for="review-finding">What do you notice?</label><textarea id="review-finding" name="finding" required maxlength="5000" placeholder="Describe the feedback in your own words."></textarea></div><div class="field full"><label for="review-suggestion">A possible next move <span>optional</span></label><textarea id="review-suggestion" name="suggestion" maxlength="5000" placeholder="A useful change, question, or thing to keep doing."></textarea></div></div><details class="form-details"><summary>Add a source and evidence details</summary><div class="form-grid"><div class="field full"><label for="source-url">Link to the original <span>optional</span></label><input id="source-url" name="source_url" type="url" maxlength="2048" placeholder="https://…"></div><div class="field"><label for="observed-at">When was it observed? <span>optional</span></label><input id="observed-at" name="observed_at" type="date"></div><div class="field"><label for="sample-count">Items behind this observation</label><input id="sample-count" name="sample_count" type="number" min="1" max="100000" value="1" required></div></div></details><div class="scope-strip">${icon('info')}<span>This saves your own observation. It does not generate a new AI review or change anything on the platform.</span></div>`;
    openDialog(dialogShell('Keep a useful observation.','A little context makes feedback easier to act on.',body,`<button class="button quiet" type="button" data-action="close-dialog">Cancel</button><button class="button primary" type="submit">Save review ${icon('check')}</button>`,'review-form'));
  }
  function reviewDetail(id, isFlag = false) {
    const grouped = isFlag
      ? sourceItems().find(source => (source.findings || []).some(finding => (finding.flag_ids || []).includes(id)))
      : sourceItems().find(source => (source.review_ids || []).includes(id));
    if (grouped) {sourceDetail(grouped.id);return;}
    const item = (isFlag ? allFlags() : state.data.reviews).find(record => record.id === id);
    if (!item) return;
    const sourceReview = isFlag ? reviewById(item.review_id) : item;
    const account = accountById(sourceReview?.account_id || item.account_id);
    const original = sourceReview?.excerpt || item.excerpt || '';
    const sourceUrl = sourceReview?.source_url || item.source_url || '';
    const body = `<div class="detail-status">${isFlag ? `<span class="badge attention">${icon('shield')}Boundary check</span>` : badge(item.severity)}<span class="badge">${esc(statusLabel[item.status] || item.status)}</span></div><div class="detail-account">${platformIcon(account)}<div><h3>${esc(account?.platform || 'Account')}</h3><p>${esc(handleOf(account))}</p></div></div>${original ? `<div class="detail-block"><div class="detail-label">THE SAVED ORIGINAL</div><blockquote class="review-excerpt">${esc(original)}</blockquote>${sourceLink(sourceUrl)}</div>` : `<div class="detail-block"><div class="detail-label">THE SAVED ORIGINAL</div><p class="detail-text">No original excerpt is saved with this observation.</p>${sourceLink(sourceUrl)}</div>`}<div class="detail-block"><div class="detail-label">${isFlag ? 'WHY IT APPEARED' : 'THE OBSERVATION'}</div><p class="detail-text">${esc(isFlag ? item.detail : item.finding)}</p></div><div class="source-required">${icon('info')}<div><h3>Source actions are unavailable.</h3><p>This evidence is not present in the authoritative source-item state. Refresh Resonance before making a decision.</p></div></div>`;
    openDialog(dialogShell(item.title,'Saved evidence',body,'<button class="button quiet" data-action="close-dialog">Close</button>'));
  }
  function sourceItemAttestations(item) {
    return (item?.attestation_ids || []).map(id => attestations().find(attestation => attestation.id === id)).filter(Boolean);
  }
  function fixDraftPrefix(item) {
    const namespace = showingSamples() ? `resonance:practice-draft:${encodeURIComponent(state.data.workspace.token)}:` : 'resonance:fix-draft:v1:';
    return `${namespace}${encodeURIComponent(item?.account_id || '')}:${encodeURIComponent(item?.id || '')}:`;
  }
  function fixDraftEvidencePrefix(item) {
    return `${fixDraftPrefix(item)}${encodeURIComponent(item?.evidence_fingerprint || '')}:`;
  }
  function fixDraftKey(item,action) {
    return `${fixDraftEvidencePrefix(item)}${encodeURIComponent(action || '')}`;
  }
  function storedFixDraft(item,action) {
    try {
      if (typeof sessionStorage === 'undefined') return null;
      const key = fixDraftKey(item,action), prefix = fixDraftPrefix(item), evidencePrefix = fixDraftEvidencePrefix(item);
      for (let index = sessionStorage.length - 1; index >= 0; index--) {
        const candidate = sessionStorage.key(index);
        if (candidate?.startsWith(prefix) && !candidate.startsWith(evidencePrefix)) sessionStorage.removeItem(candidate);
      }
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      if (!saved || saved.sourceId !== item.id || saved.accountId !== item.account_id || saved.evidenceFingerprint !== item.evidence_fingerprint || saved.action !== action) return null;
      return {sourceId:item.id,accountId:item.account_id,evidenceFingerprint:item.evidence_fingerprint,action,proposedText:String(saved.proposedText || ''),note:String(saved.note || ''),resultUrl:String(saved.resultUrl || '')};
    } catch {return null;}
  }
  function persistFixDraft() {
    const draft = state.fixDraft, item = sourceItemById(draft?.sourceId);
    if (!draft || !item || draft.accountId !== item.account_id || draft.evidenceFingerprint !== item.evidence_fingerprint) return;
    try {if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(fixDraftKey(item,draft.action),JSON.stringify(draft));} catch {}
  }
  function clearFixDraft(item) {
    if (!item) return;
    try {
      if (typeof sessionStorage === 'undefined') return;
      const prefix = fixDraftPrefix(item);
      for (let index = sessionStorage.length - 1; index >= 0; index--) {
        const candidate = sessionStorage.key(index);
        if (candidate?.startsWith(prefix)) sessionStorage.removeItem(candidate);
      }
    } catch {}
  }
  function provenanceText(value) {
    if (!value) return 'Provenance not recorded';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(provenanceText).join(' · ');
    if (typeof value === 'object') return Object.entries(value).map(([key,detail]) => `${key.replaceAll('_',' ')}: ${typeof detail === 'object' ? JSON.stringify(detail) : detail}`).join(' · ');
    return String(value);
  }
  function sourceNextMove(item) {
    const account = accountById(item.account_id), access = accessState(account);
    const review = sourceRepresentative(item), work = activeRequestForSource(item);
    const attestation = unresolvedAttestationForSource(item);
    const blocked = work?.execution_state === 'blocked' ? work : attestation?.execution_state === 'blocked' ? attestation : null;
    const needsSignIn = ['blocked','needs_login'].includes(access.key) || (blocked && /sign[ -]?in|log[ -]?in|authentication/i.test(blocked.blocker_reason || ''));
    let body = blocked ? `<p>${esc(blocked.blocker_reason || 'The assistant could not finish this work.')}</p>` : '';
    if (needsSignIn) {
      const signIn = signInUrls[account?.platform];
      body += showingSamples() ? '<p>This sample shows a sign-in blocker. With your own account, this is where you open the platform’s sign-in page.</p><button class="button quiet" data-action="setup-account" data-id="'+esc(item.account_id)+'">Sample account details</button>' : `<p>Sign in to the intended account in your browser, then ask your assistant to check it again. Resonance never sees your password.</p>${signIn ? `<a class="button primary" href="${esc(signIn)}" target="_blank" rel="noopener noreferrer">Sign in to ${esc(account.platform)} ${icon('external')}</a>` : ''}<button class="button quiet" data-action="setup-account" data-id="${esc(item.account_id)}">Account details</button>`;
    }
    if (!item.precise_source) {
      if (review?.source_url_correction) body += `<p>The saved correction needs an account check before this item can be changed.</p><button class="button quiet" data-action="account-edit" data-id="${esc(item.account_id)}">Review saved account</button>`;
      else if (work || attestation) body += '<p>Finish or reconcile the saved work before adding a different link.</p>';
      else if (item.review_ids?.length === 1 && review?.kind !== 'sample' && (review?.sample_count ?? 1) === 1) body += `<p>An exact link opens this one post, comment or reply, rather than a profile or feed. The saved observation stays in its history.</p><form id="source-link-form"><input type="hidden" name="source_id" value="${esc(item.id)}"><input type="hidden" name="evidence_fingerprint" value="${esc(item.evidence_fingerprint)}"><div class="field"><label for="exact-source-link">Add the exact link</label><input id="exact-source-link" name="source_url" type="url" required maxlength="2048" placeholder="https://…"></div><button class="button primary" type="submit">Save exact link</button></form>`;
      else body += `<p>This observation covers more than one item. Save a review for the specific post you want to change.</p><button class="button quiet" data-action="add-review" data-account="${esc(item.account_id)}">Save a specific review</button>`;
    }
    if (work) body += `<button class="button quiet" data-action="request-detail" data-id="${esc(work.id)}">Review assistant work</button>`;
    if (attestation) body += `<button class="button quiet" data-action="source-verification" data-id="${esc(item.id)}">Review your recorded result</button>`;
    if (editingUnavailable(item) && item.status === 'needs_you') body = `<p>Editing is unavailable. Choose a follow-up draft or keep the original.</p><button class="button primary" data-action="edit-next-steps" data-source="${esc(item.id)}">Choose next step</button>` + body;
    if (!body) body = '<p>Read the saved text and findings, then choose Keep, Modify… or Delete… below. The chooser explains who will make a change.</p>';
    return `<section class="source-next-move"><h3>Your next move</h3>${body}</section>`;
  }
  function sourceDetail(id, findingIndex = null) {
    const item = sourceItemById(id);
    if (!item) return;
    const account = accountById(item.account_id);
    const request = activeRequestForSource(item);
    const findings = Array.isArray(item.findings) ? item.findings : [];
    const selectedIndex = findingIndex === null ? -1 : Number(findingIndex);
    const records = sourceItemAttestations(item);
    const evidence = (item.review_ids || []).map(reviewById).filter(Boolean);
    const requests = (item.request_ids || []).map(requestById).filter(Boolean);
    const body = `<div class="detail-status">${sourceStatusBadge(item)}${item.source_changed ? '<span class="badge attention">Source changed</span>' : ''}</div><div class="detail-account">${platformIcon(account)}<div><h3>${esc(account?.platform || 'Account')}</h3><p>${esc(handleOf(account) || item.identity_note || 'Identity not recorded')}</p>${sourceLink(item.source_url,'Open exact original')}</div></div><div class="detail-block"><div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${item.excerpt ? `<blockquote class="review-excerpt">${highlightedText(item)}</blockquote>` : '<p class="detail-text">No original excerpt was saved. Use the exact source link to inspect the original.</p>'}</div>${reviewContext(findings)}${editAvailabilityPanel(item,request)}${!item.precise_source ? `<div class="source-required">${icon('info')}<div><h3>A precise source is required for edit or delete work.</h3><p>${esc(item.identity_note || 'This evidence remains readable, but it cannot authorize a platform change.')}</p></div></div>` : ''}<div class="detail-block"><div class="detail-label">FINDINGS · ${findings.length}</div>${findings.length ? `<div class="source-findings">${findings.map((finding,index) => `<section class="source-finding ${selectedIndex === index ? 'selected' : ''}" id="finding-${index}">${badge(finding.severity || 'suggestion',finding.title || 'Finding')}<p>${esc(finding.detail || 'No detail was recorded.')}</p><small>${esc(finding.kind || 'evidence')} · ${(finding.review_ids || []).length} review ${(finding.review_ids || []).length === 1 ? 'record' : 'records'} · ${(finding.flag_ids || []).length} ${(finding.flag_ids || []).length === 1 ? 'flag' : 'flags'}</small><small>${esc(provenanceText(finding.provenance))}</small></section>`).join('')}</div>` : '<p class="detail-text">Checked locally; nothing matched in the saved scope.</p>'}</div>${request ? `<button class="current-request" data-action="request-detail" data-id="${esc(request.id)}"><span>${requestBadge(request)}${esc(requestActionLabel[request.action] || request.action)} work</span>${icon('arrow')}</button>` : ''}${records.length ? `<div class="dialog-subsection"><h3>Done by you</h3>${records.map(record => `<div class="attestation-record"><strong>${esc(requestActionLabel[record.action] || record.action)} · ${esc(record.execution_state === 'verified' ? 'Verified' : record.execution_state === 'reopened' ? 'Reopened' : 'Verification pending')}</strong><time>${esc(dateText(record.done_at || record.created_at,true))}</time><p>${esc(record.note)}</p>${record.blocker_reason ? `<p class="request-blocker">${esc(record.blocker_reason)}</p>` : ''}${record.execution_state === 'blocked' ? blockerMove(record,item,{attestation:true}) : ''}${record.result_url ? sourceLink(record.result_url,'Open result link') : ''}</div>`).join('')}</div>` : ''}${evidence.length || requests.length ? `<details class="source-history"><summary>Evidence and request history <span>${evidence.length + requests.length}</span></summary>${evidence.map(review => `<section><strong>${esc(review.title || 'Saved review')}</strong><small>${esc(review.origin || 'review')} · ${esc(dateText(review.reviewed_at || review.updated_at,true))}</small>${review.excerpt ? `<blockquote class="review-excerpt">${esc(review.excerpt)}</blockquote>` : '<p>No excerpt saved.</p>'}${review.finding ? `<p>${esc(review.finding)}</p>` : ''}${sourceLink(review.source_url,'Open recorded source')}</section>`).join('')}${requests.map(saved => `<button class="current-request quiet" data-action="request-detail" data-id="${esc(saved.id)}"><span>${requestBadge(saved)}${esc(requestActionLabel[saved.action] || saved.action)} · ${esc(dateText(saved.created_at,true))}</span>${icon('arrow')}</button>`).join('')}</details>` : ''}<div class="scope-strip">${icon('info')}<span>Findings preserve their saved evidence and provenance. The excerpt may be partial. Use the exact source before changing platform content.</span></div>${sourceNextMove(item)}<div class="source-dialog-actions">${sourceActions(item)}</div>`;
    openDialog(dialogShell(item.title || 'Saved source',item.identity_note || 'One source, all saved findings.',body,`<button class="button quiet" data-action="close-dialog">Close</button>`));
    if (selectedIndex >= 0) setTimeout(() => $(`#finding-${selectedIndex}`,dialog)?.scrollIntoView({block:'center'}),0);
  }
  function fixNowDialog(sourceId, action = '') {
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review || !item.precise_source || !safeUrl(item.source_url)) return;
    if (action === 'edit' && editingUnavailable(item)) {editNextSteps(sourceId);return;}
    if (!['edit','delete'].includes(action)) {
      state.retraction = null;
      const body = `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}<div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${item.excerpt ? `<blockquote class="review-excerpt">${highlightedText(item)}</blockquote>` : '<p class="detail-text">No excerpt was saved. Inspect the original before choosing.</p>'}</div><p class="detail-text">Open the original to see which changes are available.</p><div class="fix-choice-grid"><button class="decision-action" data-action="fix-now-choice" data-source="${esc(item.id)}" data-fix-action="edit">${icon('draft')}<span><strong>Edit it myself</strong><small>Prepare text, then open the original</small></span></button><button class="decision-action delete" data-action="fix-now-choice" data-source="${esc(item.id)}" data-fix-action="delete">${icon('warning')}<span><strong>Delete it myself</strong><small>Open the original, then record what happened</small></span></button></div>`;
      openDialog(dialogShell('Fix now','Work from the exact original and record your result.',body,`<button class="button quiet" data-action="source-detail" data-id="${esc(item.id)}">Back</button>`));
      return;
    }
    const retraction = action === 'edit' && state.retraction?.sourceId === sourceId && state.retraction.route === 'self' ? state.retraction : null;
    const prior = state.fixDraft?.sourceId === sourceId && state.fixDraft.accountId === item.account_id && state.fixDraft.evidenceFingerprint === item.evidence_fingerprint && state.fixDraft.action === action ? state.fixDraft : storedFixDraft(item,action);
    state.fixDraft = {sourceId,accountId:item.account_id,evidenceFingerprint:item.evidence_fingerprint,action,proposedText:retraction ? RETRACTIONS[retraction.index] : (prior?.proposedText ?? (action === 'edit' ? item.excerpt || '' : '')),note:prior?.note || '',resultUrl:prior?.resultUrl || ''};
    persistFixDraft();
    const guidance = communication().brand || {};
    const request = activeRequestForSource(item);
    const running = request && ['running','needs_verification'].includes(request.execution_state) ? `<div class="request-alert">Assistant work has already started or has an unknown result. Recording your own result will not claim that attempt was stopped.</div>` : '';
    const editor = action === 'edit' ? `<div class="field request-editor"><label for="fix-proposed-text">Your proposed text</label><textarea id="fix-proposed-text" maxlength="20000">${esc(state.fixDraft.proposedText)}</textarea>${retraction ? retractionNextButton('#fix-proposed-text') : ''}<span class="field-help">${retraction ? 'Suggested withdrawal wording. Edit it freely; nothing is posted from this page.' : 'This starts from the saved excerpt, which may be partial. Check the complete original before replacing anything.'}</span></div>${guidance.voice || guidance.avoid ? `<div class="brand-guide"><strong>Saved voice guidance</strong>${guidance.voice ? `<p><strong>Voice</strong>${esc(guidance.voice)}</p>` : ''}${guidance.avoid ? `<p><strong>What to avoid</strong>${esc(guidance.avoid)}</p>` : ''}<p>This is guidance, not a literal phrase matcher.</p></div>` : ''}<button class="button quiet" data-action="copy-fix-text">${icon('copy')}Copy proposed text</button><div id="copy-recovery" class="copy-recovery" hidden></div>` : '';
    const body = `${running}${identityCheckPanel(accountById(item.account_id))}<div class="action-source">${sourceLink(item.source_url,'Open exact original')}<div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${item.excerpt ? `<blockquote class="review-excerpt">${highlightedText(item)}</blockquote>` : '<p class="detail-text">No excerpt was saved. Inspect the original before acting.</p>'}</div>${editor}<p class="detail-text fix-source-help">Use the normal source link above if a new window is blocked. Return here after you finish on the source.</p>`;
    openDialog(dialogShell(action === 'edit' ? 'Edit it yourself' : 'Delete it yourself','The source decides which controls are available.',body,`<button class="button quiet" data-action="fix-now" data-source="${esc(item.id)}">Back</button><button class="button primary" data-action="attestation-step" data-source="${esc(item.id)}" data-fix-action="${action}">${action === 'edit' ? 'I changed it' : 'I deleted it'} ${icon('arrow')}</button>`));
  }
  function attestationDialog(sourceId, action) {
    const item = sourceItemById(sourceId);
    if (!item || !state.fixDraft || state.fixDraft.sourceId !== sourceId || state.fixDraft.action !== action) return;
    const body = `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}</div><div class="field"><label for="attestation-note">What happened?</label><textarea id="attestation-note" name="note" required maxlength="2000" placeholder="Briefly record what you changed or deleted.">${esc(state.fixDraft.note)}</textarea><span class="field-help">A note is required. Your report closes the local task and enters verification; it is not an assistant receipt.</span></div><div class="field"><label for="attestation-result-url">Result link <span>optional</span></label><input id="attestation-result-url" name="result_url" type="url" maxlength="2000" value="${esc(state.fixDraft.resultUrl)}" placeholder="https://…"></div><input type="hidden" name="review_id" value="${esc(sourceRepresentative(item)?.id || '')}"><input type="hidden" name="source_id" value="${esc(item.id)}"><input type="hidden" name="action" value="${esc(action)}">`;
    openDialog(dialogShell(action === 'edit' ? 'Record your edit' : 'Record your deletion','Done by you · verification pending',body,`<button class="button quiet" type="button" data-action="fix-now-choice" data-source="${esc(item.id)}" data-fix-action="${action}">Back</button><button class="button primary" type="submit">Save as done by you ${icon('check')}</button>`,'attestation-form'));
  }
  function unresolvedAttestationForSource(item) {
    return sourceItemAttestations(item).find(attestation => ['needs_verification','blocked'].includes(attestation.execution_state));
  }
  function attestationVerificationDialog(item,attestation) {
    const blocked = attestation.execution_state === 'blocked';
    const body = `<div class="detail-status"><span class="badge suggestion">${icon('clock')}Done by you · verification pending</span>${blocked ? '<span class="badge attention">Blocked</span>' : ''}</div><div class="action-source">${sourceLink(attestation.source_url || item.source_url,'Open exact original')}</div><div class="attestation-record"><strong>${esc(requestActionLabel[attestation.action] || attestation.action)} · ${esc(dateText(attestation.done_at || attestation.created_at,true))}</strong><p>${esc(attestation.note)}</p>${attestation.proposed_text ? `<div class="request-proposed"><span>Proposed text</span>${esc(attestation.proposed_text)}</div>` : ''}${attestation.result_url ? sourceLink(attestation.result_url,'Open result link') : ''}</div>${attestation.blocker_reason ? `<div class="request-alert">${esc(attestation.blocker_reason)}</div>` : ''}${blocked ? blockerMove(attestation,item,{attestation:true}) : ''}<div class="scope-strip">${icon('info')}<span>Verification must finish or reopen this source before another assistant edit or deletion can be queued. You can still use Fix now if you make another change yourself.</span></div>`;
    openDialog(dialogShell('Verification already pending','The saved user result is the current work for this source.',body,`<button class="button quiet" data-action="source-detail" data-id="${esc(item.id)}">Back to source</button><button class="button quiet" data-action="fix-now" data-source="${esc(item.id)}">Fix now</button><button class="button primary" data-action="close-dialog">Done</button>`));
  }
  function assistantRequestDialog(sourceId,action) {
    // The one guarded way into an assistant edit/delete request, shared by every entry point.
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    const active = activeRequestForSource(item);
    if (active) {requestDetail(active.id,'This source already has active work. Its current state is shown here.');return;}
    const pendingAttestation = unresolvedAttestationForSource(item);
    if (pendingAttestation) {attestationVerificationDialog(item,pendingAttestation);return;}
    if (!item.precise_source || !safeUrl(item.source_url)) {sourceRequiredDialog(review.id,action === 'delete' ? 'delete' : 'edit');return;}
    if (action === 'edit' && editingUnavailable(item)) {editNextSteps(sourceId);return;}
    if (action === 'delete') deleteRequestDialog(review.id); else editRequestDialog(review.id);
  }
  function assistantQueueNote(action) {
    const next = state.data?.monitoring?.next_scheduled_check;
    const gate = action === 'delete' ? 'Deletion still needs your yes at the time it happens.' : 'Some edits need your confirmation then.';
    return `Queued for the assistant’s next check${next ? `, expected ${esc(dateText(next,true))}` : ' (no next check time is recorded)'}. ${gate} This page never contacts the platform.`;
  }
  function sourceModifyDialog(sourceId) {
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    if (editingUnavailable(item)) {editNextSteps(sourceId);return;}
    state.retraction = null;
    const body = `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}<div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${item.excerpt ? `<blockquote class="review-excerpt">${highlightedText(item)}</blockquote>` : '<p class="detail-text">No excerpt was saved. Inspect the original before choosing.</p>'}</div><div class="fix-choice-grid stacked"><button class="decision-action" data-action="fix-now-choice" data-source="${esc(item.id)}" data-fix-action="edit" ${showingSamples() ? 'disabled' : ''}>${icon('draft')}<span><strong>Edit it myself</strong><small>${showingSamples() ? 'Real posts only. Practice by asking your assistant.' : 'Prepare text, then open the original'}</small></span></button><button class="decision-action" data-action="assistant-choice" data-source="${esc(item.id)}" data-request-action="edit">${icon('sparkle')}<span><strong>Ask my assistant to edit</strong><small>${assistantQueueNote('edit')}</small></span></button><button class="decision-action" data-action="source-retract" data-source="${esc(item.id)}">${icon('undo')}<span><strong>Retract it</strong><small>Replace the wording with a withdrawal notice</small></span></button></div>`;
    openDialog(dialogShell('Modify this post','Check availability, then prepare your wording.',editAvailabilityPanel(item) + body,`<button class="button quiet" data-action="source-detail" data-id="${esc(item.id)}">Back</button>`));
  }
  function sourceDeleteDialog(sourceId) {
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    state.retraction = null;
    const body = `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}<div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${item.excerpt ? `<blockquote class="review-excerpt">${highlightedText(item)}</blockquote>` : '<p class="detail-text">No excerpt was saved. Inspect the original before choosing.</p>'}</div><div class="fix-choice-grid"><button class="decision-action delete" data-action="fix-now-choice" data-source="${esc(item.id)}" data-fix-action="delete" ${showingSamples() ? 'disabled' : ''}>${icon('warning')}<span><strong>Delete it myself</strong><small>${showingSamples() ? 'Real posts only. Practice by asking your assistant.' : 'Open the original, then record what happened'}</small></span></button><button class="decision-action delete" data-action="assistant-choice" data-source="${esc(item.id)}" data-request-action="delete">${icon('sparkle')}<span><strong>Ask my assistant to delete</strong><small>${assistantQueueNote('delete')}</small></span></button></div>`;
    openDialog(dialogShell('Delete this post',showingSamples() ? 'Practice by asking your assistant. Nothing real is removed.' : 'Two ways to remove it. Nothing is removed from this page.',body,`<button class="button quiet" data-action="source-detail" data-id="${esc(item.id)}">Back</button>`));
  }
  function retractionDialog(sourceId) {
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    if (editingUnavailable(item)) {editNextSteps(sourceId);return;}
    state.retraction = null;
    const body = `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}</div><p class="detail-text">A retraction replaces the original wording with a short withdrawal notice. You can change the wording before anything is saved.</p><div class="fix-choice-grid"><button class="decision-action" data-action="retract-route" data-source="${esc(item.id)}" data-retract-route="self" ${showingSamples() ? 'disabled' : ''}>${icon('draft')}<span><strong>I’ll post it myself</strong><small>${showingSamples() ? 'Real posts only. Practice by asking your assistant.' : 'Prepare the notice, then open the original'}</small></span></button><button class="decision-action" data-action="retract-route" data-source="${esc(item.id)}" data-retract-route="assistant">${icon('sparkle')}<span><strong>Ask my assistant to post it</strong><small>${assistantQueueNote('edit')}</small></span></button></div>`;
    openDialog(dialogShell('Retract it','The withdrawal notice becomes the proposed replacement text.',body,`<button class="button quiet" data-action="source-modify" data-source="${esc(item.id)}">Back</button>`));
  }
  function retractionNextButton(target) {
    return `<button class="text-button retraction-next" type="button" data-action="retraction-next" data-target="${esc(target)}">${icon('undo')}Another one</button>`;
  }
  function startRetraction(sourceId,route) {
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    state.retraction = {sourceId,reviewId:review.id,route:route === 'assistant' ? 'assistant' : 'self',index:0};
    if (state.retraction.route === 'assistant') assistantRequestDialog(sourceId,'edit'); else fixNowDialog(sourceId,'edit');
  }
  function cycleRetraction(button) {
    const field = $(button.dataset.target || '#proposed-text');
    if (!field || !state.retraction) return;
    const current = String(field.value ?? '');
    // The user's own wording is never overwritten; only an empty box or an untouched template cycles.
    if (current.trim() && !RETRACTIONS.includes(current)) {toast('Your own wording is kept. Clear the box to see another notice.');return;}
    state.retraction = {...state.retraction,index:(state.retraction.index + 1) % RETRACTIONS.length};
    field.value = RETRACTIONS[state.retraction.index];
    if (button.dataset.target === '#fix-proposed-text' && state.fixDraft) {state.fixDraft.proposedText = field.value;persistFixDraft();}
  }
  function askAssistantDialog(sourceId) {
    const item = sourceItemById(sourceId);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    const active = activeRequestForSource(item);
    if (active) {requestDetail(active.id,'This source already has active work. Its current state is shown here.');return;}
    const pendingAttestation = unresolvedAttestationForSource(item);
    if (pendingAttestation) {attestationVerificationDialog(item,pendingAttestation);return;}
    if (!item.precise_source || !safeUrl(item.source_url)) {sourceRequiredDialog(review.id,'edit');return;}
    state.retraction = null;
    const next = state.data?.monitoring?.next_scheduled_check;
    const body = `<div class="action-source">${sourceLink(item.source_url,'Open exact original')}<div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${item.excerpt ? `<blockquote class="review-excerpt">${highlightedText(item)}</blockquote>` : '<p class="detail-text">No excerpt was saved.</p>'}</div><p class="detail-text">Choose the request. It will be queued for the assistant${next ? ` for the check expected ${esc(dateText(next,true))}` : '; no reliable next check time is recorded'}. That time is not a completion promise.</p><div class="fix-choice-grid"><button class="decision-action" data-action="assistant-choice" data-source="${esc(item.id)}" data-request-action="edit">${icon('draft')}<span><strong>Ask for an edit</strong><small>Review proposed wording before saving</small></span></button><button class="decision-action delete" data-action="assistant-choice" data-source="${esc(item.id)}" data-request-action="delete">${icon('warning')}<span><strong>Ask for deletion</strong><small>Review the exact source before saving</small></span></button></div>`;
    openDialog(dialogShell('Ask assistant','Queue one request for this source.',body,`<button class="button quiet" data-action="source-detail" data-id="${esc(item.id)}">Back</button>`));
  }
  function actionSource(review,request = {}) {
    const account = accountById(request.account_id || review?.account_id);
    const excerpt = request.original_excerpt || review?.excerpt || '';
    const url = request.source_url || review?.source_url_correction || review?.source_url || '';
    return {account,excerpt,url};
  }
  function identityCheckPanel(account,request = {}) {
    if (!account || request.action === 'keep') return '';
    const preflight = request.identity_preflight;
    const check = preflight?.observation || request.identity_check;
    const labels = {matched:'Reported match',mismatch:'Account mismatch',signed_out:'Signed out',unavailable:'Identity unavailable',expired:'Check expired',changed:'Account or request changed',historical:'History only',required:'Check required'};
    const status = preflight?.status || 'required';
    const observed = check ? `<dt>Observed active account</dt><dd>${esc(check.platform)} · ${esc(check.handle || 'Not available')}${check.profile_url ? sourceLink(check.profile_url,'Observed profile') : ''}</dd><dt>Observed source author</dt><dd>${esc(check.author_handle || 'Not available')}${check.author_profile_url ? sourceLink(check.author_profile_url,'Author profile') : ''}</dd><dt>Checked</dt><dd>${esc(dateText(check.checked_at,true))}</dd>` : '<dt>Observed active account</dt><dd>Not checked for this request</dd>';
    return `<section class="identity-check-panel" aria-label="Account identity check"><div class="section-heading"><h3>Account before action</h3><span class="badge ${status === 'matched' ? 'strength' : 'attention'}">${esc(labels[status] || 'Check required')}</span></div><dl class="detail-meta-list"><dt>Intended saved account</dt><dd>${esc(account.platform)} · ${esc(account.handle)}${account.url ? sourceLink(account.url,'Saved profile') : '<small>No saved profile link</small>'}</dd><dt>Account boundary</dt><dd>${esc(identityLabel[account.identity_mode] || 'Not classified')} · ${esc(daringLabel[account.daring] || 'Careful')} · ${account.sfw_only !== false ? 'SFW only' : 'Custom boundaries'}</dd>${observed}</dl><p>${esc(preflight?.reason || 'Before an assistant edit or removal starts, a fresh comparison must match this account and the source author.')}</p>${check?.detail ? `<p class="identity-observation">${esc(check.detail)}</p>` : ''}<p class="field-help">Assistant-reported observation, not a live connection. Matching checks expire after five minutes. Recheck after switching accounts.</p><button class="text-button" data-action="account-edit" data-id="${esc(account.id)}">Review saved account</button></section>`;
  }
  function actionSourcePreview(review,request = {}) {
    const {account,excerpt,url} = actionSource(review,request);
    return `<div class="action-source"><div class="detail-account">${platformIcon(account)}<div><h3>${esc(account?.platform || 'Account')}</h3><p>${esc(handleOf(account))}</p></div></div><div class="detail-label">SAVED EXCERPT · MAY BE PARTIAL</div>${excerpt ? `<blockquote class="review-excerpt">${esc(excerpt)}</blockquote>` : '<p class="detail-text">No original excerpt was saved.</p>'}${sourceLink(url,'Open exact original')}</div>${identityCheckPanel(account,request)}${request.action === 'delete' || request.action === 'keep' ? '' : editAvailabilityPanel(sourceItems().find(item => item.review_ids?.includes(review?.id)),request)}`;
  }
  function sourceRequiredDialog(reviewId,action) {
    const review = reviewById(reviewId);
    if (!review) return;
    const grouped = sourceItems().find(item => item.review_ids?.includes(reviewId));
    if (grouped) {sourceDetail(grouped.id);return;}
    const {account} = actionSource(review);
    const body = `${actionSourcePreview(review)}<div class="source-required">${icon('info')}<div><h3>A source link is required.</h3><p>Save a new review or excerpt with the original platform link, then choose ${esc(requestActionLabel[action])} from that item. Saved evidence stays unchanged, so this review cannot be turned into a runnable request.</p></div></div>`;
    openDialog(dialogShell(`Add the original link first.`,`${requestActionLabel[action]} needs a source your assistant can verify.`,body,`<button class="button quiet" data-action="review-detail" data-id="${esc(reviewId)}">Back</button><button class="button primary" data-action="add-sourced-review" data-account="${esc(account?.id || '')}">${icon('plus')}Save a sourced review</button>`));
  }
  function editRequestDialog(reviewId) {
    const review = reviewById(reviewId);
    if (!review) return;
    const item = sourceItems().find(value => value.review_ids?.includes(reviewId));
    if (editingUnavailable(item)) {editNextSteps(item.id);return;}
    const retraction = state.retraction?.reviewId === reviewId && state.retraction.route === 'assistant' ? state.retraction : null;
    const body = `${actionSourcePreview(review)}<div class="field request-editor"><label for="proposed-text">Proposed replacement text</label><textarea id="proposed-text" name="proposed_text" required maxlength="20000">${esc(retraction ? RETRACTIONS[retraction.index] : review.excerpt || '')}</textarea>${retraction ? retractionNextButton('#proposed-text') : ''}<span class="field-help">${retraction ? 'Suggested withdrawal wording. Edit it before saving; your assistant still verifies the full original on the platform.' : 'Change the wording before saving. The saved excerpt may be partial; your assistant verifies the full original on the platform before publishing anything.'}</span></div><input type="hidden" name="review_id" value="${esc(reviewId)}">`;
    openDialog(dialogShell('Request an edit.','Write the replacement you want your assistant to verify.',body,`<span class="footer-note">Some edits need your confirmation during the daily check.</span><button class="button quiet" type="button" data-action="review-detail" data-id="${esc(reviewId)}">Back</button><button class="button primary" type="submit">Save edit request ${icon('arrow')}</button>`,'action-request-form'));
    const textarea = $('#proposed-text');
    if (textarea) textarea.setSelectionRange(textarea.value.length,textarea.value.length);
  }
  function deleteRequestDialog(reviewId) {
    const review = reviewById(reviewId);
    if (!review) return;
    const body = `${actionSourcePreview(review)}<div class="delete-preview">${icon('warning')}<div><h3>Request removal from ${esc(accountById(review.account_id)?.platform || 'the platform')}.</h3><p>This saves a pending request. It does not mean the original was deleted. The request completes only after your assistant verifies the platform result.</p></div></div>`;
    openDialog(dialogShell('Request deletion?','Check the account, source, and original before saving.',body,`<button class="button quiet" data-action="review-detail" data-id="${esc(reviewId)}">Back</button><button class="button danger" data-action="confirm-delete-request" data-review="${esc(reviewId)}">Request deletion</button>`));
  }
  function requestDetail(id,message = '') {
    const request = actionRequests().find(item => item.id === id);
    if (!request) return;
    const review = reviewById(request.review_id);
    const pendingNote = request.confirmation_required === true ? `${request.blocker_reason ? `Held: ${request.blocker_reason} ` : ''}Review the exact action, source, excerpt, and proposed text. This work cannot run until you confirm it.` : request.execution_state === 'running' ? 'A durable attempt has started. Its outcome must be reconciled before another mutation can run.' : request.execution_state === 'needs_verification' ? 'The result is uncertain. The source must be checked before any retry.' : request.execution_state === 'blocked' ? `Blocked: ${request.blocker_reason || 'The assistant could not finish.'}${request.next_move ? ` Next move: ${request.next_move.replaceAll('_',' ')}.` : ''}` : request.execution_state === 'reopened' ? 'Verification found a renewed concern. Choose again from the source card before any new action.' : 'This is queued work. The platform has not been reported as changed. The expected check time is not a completion promise.';
    const body = `${message ? `<div class="request-alert">${esc(message)}</div>` : ''}<div class="detail-status"><span class="badge suggestion">${esc(requestActionLabel[request.action] || request.action)} request</span>${requestBadge(request)}</div>${actionSourcePreview(review,request)}${request.action === 'edit' ? `<div class="detail-block proposed-full"><div class="detail-label">PROPOSED REPLACEMENT</div><p class="detail-text">${esc(request.proposed_text || 'No replacement text was recorded.')}</p></div>` : ''}<div class="dialog-subsection"><dl class="detail-meta-list"><dt>Saved</dt><dd>${esc(dateText(request.created_at,true))}</dd><dt>Updated</dt><dd>${esc(dateText(request.updated_at,true))}</dd>${request.started_at ? `<dt>Attempt started</dt><dd>${esc(dateText(request.started_at,true))}</dd>` : ''}${scheduledText(request) ? `<dt>Next scheduled check</dt><dd>${esc(scheduledText(request).replace(/^Next scheduled check · /,''))}</dd>` : ''}${request.completed_at ? `<dt>Completed</dt><dd>${esc(dateText(request.completed_at,true))}</dd>` : ''}</dl>${request.receipt_url ? sourceLink(request.receipt_url,'Open verification receipt') : request.execution_state === 'verified' && request.action !== 'keep' ? '<p class="request-receipt-missing">No external receipt URL was recorded.</p>' : ''}${request.receipt_note ? `<p class="request-receipt-note">${esc(request.receipt_note)}</p>` : ''}</div><div class="scope-strip">${icon('info')}<span>${request.status === 'cancelled' || request.execution_state === 'cancelled' ? 'The queued request was cancelled. Cancellation does not undo a platform change already made elsewhere.' : request.action === 'keep' ? 'Keep is a completed local choice. The social platform was not changed.' : request.execution_state === 'verified' ? 'Verified means the assistant observed and recorded the stated platform result.' : pendingNote}</span></div>`;
    openDialog(dialogShell(`${requestActionLabel[request.action] || 'Action'} · ${requestBadgeText(request)}`,'Saved request details',body,`<button class="button quiet" data-action="close-dialog">Close</button>${request.confirmation_required === true && request.status === 'pending' ? `<button class="button primary" data-action="confirm-request" data-id="${esc(request.id)}">Confirm request</button>` : ''}${request.can_cancel ? `<button class="button danger quiet" data-action="cancel-request" data-id="${esc(request.id)}">Cancel request</button>` : ''}`));
  }
  async function loadDataFolder() {
    const target = $('#data-folder-details');
    try {
      const details = await api('/api/data-folder');
      if (target.isConnected) target.innerHTML = `<p class="detail-text"><code>${esc(details.path)}</code></p><p>${esc(new Intl.NumberFormat().format(details.size_bytes))} bytes · Last automatic backup: ${details.last_automatic_backup ? esc(dateText(details.last_automatic_backup,true)) : 'None yet'}</p><button type="button" class="button quiet small" data-action="open-data-folder">Open folder</button>`;
    } catch(error) {
      if (target.isConnected) target.textContent = `Folder details are unavailable. ${error.message}`;
    }
  }
  async function yourDataDialog() {
    openDialog(dialogShell('Your data','Everything stays in a folder on this computer.',`<div id="data-folder-details" aria-live="polite">Loading folder details…</div><p>A plain backup includes your saved account names, posts, drafts and requests. Keep it private.</p><div class="settings-data-actions"><a class="button quiet" ${downloadAttributes('/api/export')}>Export backup</a><button class="button quiet" data-action="choose-import">Import Resonance backup</button><input id="import-file" type="file" accept="application/json,.json" hidden></div>`,'<button class="button primary" data-action="close-dialog">Done</button>'));
    await loadDataFolder();
  }
  async function openDataFolder(button) {
    button.disabled = true;
    try {
      await api('/api/data-folder/open','POST',{});
      toast('Opened your data folder.');
    } catch(error) {dialogError(error);}
    finally {if (button.isConnected) button.disabled = false;}
  }
  function settingsDialog() {
    const {settings} = state.data;
    const body = `<div class="settings-section"><h3>Make it yours</h3><div class="form-grid"><div class="field"><label for="settings-name">Your name <span>optional</span></label><input id="settings-name" name="display_name" maxlength="120" value="${esc(settings.display_name)}" placeholder="What should we call you?" autocomplete="given-name"></div><div class="field"><label for="theme">Appearance</label><select id="theme" name="theme">${Object.entries(THEMES).map(([value,theme]) => `<option value="${esc(value)}" ${settings.theme === value ? 'selected' : ''}>${esc(theme.label)}</option>`).join('')}</select></div></div><button class="text-button" type="button" data-action="setup-identity">Edit personal brand ${icon('arrow')}</button></div><div class="settings-section"><h3>Your data</h3><div id="data-folder-details" aria-live="polite">Loading folder details…</div><p>Export a full Resonance backup, or bring one back into this workspace. Backups include your saved communication context and history.</p><div class="settings-data-actions"><a class="button quiet small" ${downloadAttributes('/api/export')}>${icon('download')}Export backup</a><button class="button quiet small" type="button" data-action="choose-import">${icon('upload')}Import Resonance backup</button><input id="import-file" type="file" accept="application/json,.json" hidden></div><p class="settings-backup-note">Import accepts Resonance’s own backup format. Matching IDs are checked for consistency; conflicting original evidence is rejected. Other saved records stay in place. Imported access must be checked again, and any imported pending request waits for you to confirm it here.</p></div><div class="settings-section"><h3>A private local workspace</h3><div class="settings-edition"><div><span>Account access</span><span>Saved evidence with dates · no live connection claim</span></div><div><span>Mail</span><span>Saved preview scope and local reply drafts · no sending</span></div><div><span>Review evidence</span><span>Saved feedback, excerpts, reports, and requests</span></div><div><span>Boundary and draft checks</span><span>Limited local wording rules</span></div><div><span>Scheduled reports</span><span>${esc(scheduleLabel())}</span></div><div><span>Platform requests</span><span>Queued here; verified outcomes appear in Requests</span></div></div><p class="settings-backup-note">Your data is saved on this computer. Stay signed in in Chrome; keep this computer awake and unlocked, with your assistant available. Saved accounts are not live connections.</p></div>`;
    openDialog(dialogShell('A workspace that feels like you.','Settings & data',body,`<button class="button quiet" type="button" data-action="close-dialog">Done</button><button class="button primary" type="submit">Save preferences ${icon('check')}</button>`,'settings-form'));
  }
  function aboutDialog() {
    const covered = state.data.accounts.filter(account => reviewsFor(account.id).length).length;
    const savedMail = communicationList('messages').length;
    const body = `<div class="detail-block"><div class="detail-label">THE VIEW RIGHT NOW</div><p class="detail-text">${state.data.reviews.length} saved review observations across ${covered} of ${state.data.accounts.length} accounts, ${savedMail} saved mail ${savedMail === 1 ? 'preview' : 'previews'}, and ${actionRequests().length} saved ${actionRequests().length === 1 ? 'choice or request' : 'choices and requests'}. Each record has its own stated scope.</p></div><div class="detail-block"><div class="detail-label">WHAT THIS BRIEF USES</div><p class="detail-text">Account and personal-brand details you save, recorded access evidence, imported mail previews, and saved assistant check reports. Boundary checks match limited local rules against saved excerpts. Compose checks only the text you enter.</p></div><div class="detail-block"><div class="detail-label">WHAT’S OUTSIDE THIS VIEW</div><p class="detail-text">Content outside each saved report or mail-sync scope, audience reactions, and anything that changed after the evidence was recorded. An access snapshot is evidence from a past check, not a live connection.</p></div><div class="detail-block"><div class="detail-label">YOU KEEP THE FINAL SAY</div><p class="detail-text">A flag is a reason to look, not a verdict. Mail replies stay local until you copy them elsewhere. Keep is a local decision. Edit and Delete are saved requests; the platform has changed only when a verified outcome appears in Requests.</p></div>`;
    openDialog(dialogShell('What Resonance can see','',`<p class="welcome-pitch">${esc(WELCOME_PITCH)}</p>`+body,'<button class="button primary" data-action="close-dialog">Got it</button>'));
  }
  async function importFile(file) {
    if (showingSamples()) {dialogError(new Error('Clear samples before importing your own backup.'));return;}
    if (!file) return;
    if (file.size > 32 * 1024 * 1024) {dialogError(new Error('This backup is larger than 32 MB. Choose a smaller Resonance JSON backup.'));return;}
    try {
      const payload = JSON.parse(await file.text());
      if (![1,2].includes(payload?.schema_version) || !Array.isArray(payload.accounts) || !Array.isArray(payload.reviews) || !payload.settings) throw new Error('Choose a Resonance backup exported from Settings. Other platform exports aren’t supported here.');
      importPayload = payload;
      const importedRequests = Array.isArray(payload.action_requests) ? payload.action_requests.length : 0;
      const body = `<p class="detail-text">${esc(file.name)}</p><div class="import-preview"><strong>${payload.accounts.length}</strong> accounts<br><strong>${payload.reviews.length}</strong> review observations<br><strong>${importedRequests}</strong> saved choices and requests<br>Saved preferences and boundary decisions, when included.</div><p class="detail-text">This adds new records and applies saved preferences and compatible updates. Conflicting original evidence is rejected. Other records already in this workspace stay in place.</p><div class="scope-strip">${icon('info')}<span>The whole backup is checked before anything is saved. Imported pending requests will wait for your confirmation. Only import a backup you trust.</span></div>`;
      openDialog(dialogShell('Bring your context back.','Import Resonance backup',body,'<button class="button quiet" data-action="settings">Cancel</button><button class="button primary" data-action="confirm-import">Import backup</button>'));
    } catch(error) {dialogError(new Error(error instanceof SyntaxError ? 'This file isn’t valid JSON. Choose a Resonance backup exported from Settings.' : error.message));}
  }
  async function createActionRequest(button,reviewId,action,proposedText) {
    button.disabled = true;
    button.setAttribute('aria-busy','true');
    try {
      const result = await api('/api/action-requests','POST',{review_id:reviewId,action,...(action === 'edit' ? {proposed_text:proposedText} : {})});
      if (!await reload()) throw new Error('The request was saved, but the workspace could not refresh. Reopen Resonance to see its current status.');
      if (action === 'keep') {
        closeDialogIfOpen();
        toast('Keep saved for this source. Eligible queued work was cancelled; social content was not changed.');
      } else {
        requestDetail(result.action_request.id);
        toast(result.created ? `${requestActionLabel[action]} request saved.` : 'That request was already saved. Showing its current status.');
      }
    } catch(error) {if (dialog.open) dialogError(error);else toast(error.message);}
    finally {if (button.isConnected) {button.disabled = false;button.removeAttribute('aria-busy');}}
  }
  async function chooseRequestAction(button) {
    const reviewId = button.dataset.review;
    const action = button.dataset.requestAction;
    const review = reviewById(reviewId);
    if (!review || !requestActionLabel[action]) return;
    const source = sourceItems().find(item => (item.review_ids || []).includes(reviewId));
    const pending = activeRequestForSource(source) || pendingRequestFor(reviewId);
    if (action === 'keep') {
      if (pending) {
        const body = `<div class="request-alert">A ${esc(requestActionLabel[pending.action])} request is waiting.</div>${actionSourcePreview(review,pending)}<p class="detail-text">Keeping the original cancels that pending request and records Keep as your completed local choice. It does not change anything on the platform.</p>`;
        openDialog(dialogShell('Keep the original?','This replaces the request that is waiting.',body,`<button class="button quiet" data-action="request-detail" data-id="${esc(pending.id)}">Back to request</button><button class="button primary" data-action="confirm-keep" data-review="${esc(reviewId)}">${icon('check')}Keep original</button>`));
      } else await createActionRequest(button,reviewId,'keep');
      return;
    }
    if (pending) {
      requestDetail(pending.id,`A ${requestActionLabel[pending.action]} request is already waiting. Cancel it before choosing ${requestActionLabel[action]}.`);
      return;
    }
    if (!safeUrl(review.source_url)) {sourceRequiredDialog(reviewId,action);return;}
    if (action === 'edit') editRequestDialog(reviewId);
    else deleteRequestDialog(reviewId);
  }
  async function updateSourceStatus(button) {
    const item = sourceItemById(button.dataset.source);
    if (!item) return;
    button.disabled = true;
    try {
      await api(`/api/source-items/${encodeURIComponent(item.id)}`,'PATCH',{status:button.dataset.status});
      if (!await reload()) throw new Error('The source decision was saved, but the workspace could not refresh.');
      closeDialogIfOpen();
      toast(button.dataset.status === 'snoozed' ? 'Source snoozed. Nothing on the platform changed.' : 'Source reopened.');
    } catch(error) {if (dialog.open) dialogError(error);else toast(error.message);}
    finally {if (button.isConnected) button.disabled = false;}
  }
  async function keepSource(button) {
    const item = sourceItemById(button.dataset.source);
    const review = sourceRepresentative(item);
    if (!item || !review) return;
    const active = activeRequestForSource(item);
    if (active && !active.can_cancel && ['running','needs_verification'].includes(active.execution_state)) {
      requestDetail(active.id,'This attempt may already have changed the platform. Keep cannot claim it was stopped; reconcile it first.');
      return;
    }
    if (active) {
      const body = `<div class="request-alert">${esc(requestActionLabel[active.action] || active.action)} work is waiting.</div>${actionSourcePreview(review,active)}<p class="detail-text">Keeping this source cancels eligible queued work and records one local decision across its current evidence. It does not change platform content.</p>`;
      openDialog(dialogShell('Keep this source?','This replaces work that has not started.',body,`<button class="button quiet" data-action="request-detail" data-id="${esc(active.id)}">Back to work</button><button class="button primary" data-action="confirm-keep" data-review="${esc(review.id)}">${icon('check')}Keep source</button>`));
    } else await createActionRequest(button,review.id,'keep');
  }
  async function copyFixText() {
    const editor = $('#fix-proposed-text');
    if (!editor?.value) {toast('Write proposed text before copying it.');return;}
    state.fixDraft.proposedText = editor.value;
    persistFixDraft();
    try {await navigator.clipboard.writeText(editor.value);toast('Proposed text copied. Nothing was posted.');}
    catch {
      editor.focus();editor.select();
      const recovery = $('#copy-recovery');
      if (recovery) {recovery.hidden = false;recovery.innerHTML = 'Clipboard access was denied. The text is selected; use Copy from your device’s text menu.';}
      toast('Text selected. Use Copy from your device’s text menu.');
    }
  }
  async function cancelRequest(button) {
    const request = actionRequests().find(item => item.id === button.dataset.id);
    if (!request || !request.can_cancel) return;
    button.disabled = true;
    button.setAttribute('aria-busy','true');
    try {
      await api(`/api/action-requests/${encodeURIComponent(request.id)}`,'PATCH',{status:'cancelled'});
      if (!await reload()) throw new Error('The cancellation was saved, but the workspace could not refresh. Reopen Resonance to see its current status.');
      closeDialogIfOpen();
      toast('Request cancelled before another platform attempt. This does not undo a change already made elsewhere.');
    } catch(error) {if (dialog.open) dialogError(error);else toast(error.message);}
    finally {if (button.isConnected) {button.disabled = false;button.removeAttribute('aria-busy');}}
  }
  async function confirmRequest(button) {
    const request = actionRequests().find(item => item.id === button.dataset.id);
    if (!request || request.status !== 'pending' || request.confirmation_required !== true) return;
    button.disabled = true;
    button.setAttribute('aria-busy','true');
    try {
      const result = await api('/api/action-requests','POST',{review_id:request.review_id,action:request.action,...(request.action === 'edit' ? {proposed_text:request.proposed_text} : {})});
      if (!await reload()) throw new Error('The confirmation was saved, but the workspace could not refresh. Reopen Resonance to see its current status.');
      requestDetail(result.action_request.id);
      toast('Request confirmed and queued. An expected check time is not a completion promise.');
    } catch(error) {if (dialog.open) dialogError(error);else toast(error.message);}
    finally {if (button.isConnected) {button.disabled = false;button.removeAttribute('aria-busy');}}
  }
  async function saveForm(form) {
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const submit = $('button[type="submit"]',form);
    if (submit) submit.disabled = true;
    const value = key => String(data.get(key) || '').trim();
    try {
      if (form.getAttribute('id') === 'source-link-form') {
        const result = await api(`/api/source-items/${encodeURIComponent(value('source_id'))}/link`,'POST',{source_url:value('source_url'),evidence_fingerprint:value('evidence_fingerprint')});
        if (!await reload()) throw new Error('The exact link was saved. Refresh to see its new source card.');
        sourceDetail(result.source_item.id);
        toast('Exact link saved. The original observation stays in history.');
      } else if (form.getAttribute('id') === 'walk-identity-form') {
        const identity = readAccountIdentity(accountWalk.platform,value('identity'));
        Object.assign(accountWalk,identity,{input:value('identity'),step:'boundaries'});
        accountWalkDialog();
      } else if (form.getAttribute('id') === 'walk-boundaries-form') {
        const identity = value('identity_mode');
        Object.assign(accountWalk,{identity_mode:identity,daring:value('daring'),sfw_only:identity !== 'alias' || data.has('sfw_only')});
        const {platform,handle,url,identity_mode,daring,sfw_only} = accountWalk;
        // Keep the saved ID before reloading so a failed refresh cannot add it twice.
        if (!accountWalk.saved) {
          const result = await api('/api/accounts','POST',{platform,handle,url,display_name:'',identity_mode,daring,sfw_only});
          accountWalk.saved = result.account.id;
        }
        if (!await reload()) throw new Error('The account was saved. Retry to refresh it before continuing.');
        accountWalk.step = 'signin';
        accountWalkDialog();
      } else if (form.getAttribute('id') === 'account-form') {
        const identity = value('identity_mode');
        const body = {handle:value('handle'),display_name:value('display_name'),url:value('url'),identity_mode:identity,daring:value('daring'),sfw_only:identity === 'alias' ? data.has('sfw_only') : true};
        let account;
        if (value('id')) {
          body.access_note = value('access_note');
          if (data.has('record_access')) body.access_checked_at = new Date().toISOString();
          ({account} = await api(`/api/accounts/${encodeURIComponent(value('id'))}`,'PATCH',body));
        } else ({account} = await api('/api/accounts','POST',{platform:value('platform'),...body}));
        await reload();
        closeDialog();
        if (state.draftAccount === account.id) {state.draftResult = null;if (state.draft.trim()) checkDraft();}
        toast(value('id') ? 'Account saved. Available excerpts have been checked against its boundaries.' : 'Account added. Your next move: save a little context.');
      } else if (form.getAttribute('id') === 'excerpt-form') {
        const result = await api('/api/excerpts','POST',{account_id:value('account_id'),kind:value('kind'),text:value('text'),source_url:value('source_url'),title:value('title')});
        if (!await reload()) throw new Error('The post was checked, but the workspace could not refresh. Reopen Resonance to see the saved result.');
        const reviewId = result.review?.id;
        const item = sourceItems().find(source => (source.review_ids || []).includes(reviewId));
        closeDialog();
        navigate('review',{account:value('account_id'),status:item ? sourceFilterFor(item) : result.flag_count ? 'needs_you' : 'history'});
        if (item) setTimeout(() => sourceDetail(item.id),0);
        toast(result.flag_count ? `Post checked. ${result.flag_count} possible boundary ${result.flag_count === 1 ? 'issue' : 'issues'} to review in context.` : 'Post checked. Nothing matched in the saved scope; the clean check is in History.');
      } else if (form.getAttribute('id') === 'review-form') {
        const body = {account_id:value('account_id'),kind:value('kind'),title:value('title'),excerpt:value('excerpt'),source_url:value('source_url'),sample_count:Number(value('sample_count')),severity:value('severity'),finding:value('finding'),suggestion:value('suggestion')};
        if (value('observed_at')) body.observed_at = new Date(`${value('observed_at')}T12:00:00`).toISOString();
        await api('/api/reviews','POST',body);
        await reload();
        closeDialog();
        toast('Review saved. Its excerpt is included in local boundary checks.');
      } else if (form.getAttribute('id') === 'brand-form') {
        await api('/api/brand','PATCH',{description:value('description'),audience:value('audience'),voice:value('voice'),avoid:value('avoid')});
        if (!await reload()) throw new Error('Your personal brand was saved, but the workspace could not refresh. Reopen Resonance to see it.');
        render();
        $('#setup-identity')?.scrollIntoView({block:'start'});
        toast('Personal brand saved.');
      } else if (form.getAttribute('id') === 'mail-draft-form') {
        const messageId = value('message_id'), text = value('text');
        if (!text) throw new Error('Write a reply before saving the draft.');
        await api('/api/mail-drafts','POST',{message_id:messageId,text});
        if (!await reload()) throw new Error('The local draft was saved, but the workspace could not refresh. Reopen Resonance to see it.');
        mailMessageDialog(messageId);
        toast('Draft saved locally. Nothing was sent.');
      } else if (form.getAttribute('id') === 'attestation-form') {
        const sourceId = value('source_id'), action = value('action'), note = value('note'), resultUrl = value('result_url');
        const source = sourceItemById(sourceId);
        if (!note) throw new Error('Add a brief note about what you changed or deleted.');
        const body = {review_id:value('review_id'),action,note,...(action === 'edit' && state.fixDraft?.proposedText ? {proposed_text:state.fixDraft.proposedText} : {}),...(resultUrl ? {result_url:resultUrl} : {})};
        const result = await api('/api/attestations','POST',body);
        if (!await reload()) throw new Error('Your result was saved, but the workspace could not refresh. Reopen Resonance to see verification status.');
        clearFixDraft(source);
        state.fixDraft = null;
        sourceDetail(sourceId);
        const running = Array.isArray(result.running_request_ids) && result.running_request_ids.length;
        toast(running ? 'Done by you saved. Verification is pending, and an earlier assistant attempt still needs reconciliation.' : 'Done by you saved. Verification is pending.');
      } else if (form.getAttribute('id') === 'settings-form') {
        await api('/api/settings','PATCH',{display_name:value('display_name'),theme:value('theme')});
        await reload();
        closeDialog();
        toast('Preferences saved.');
      } else if (form.getAttribute('id') === 'action-request-form') {
        const review = reviewById(value('review_id'));
        const proposed = value('proposed_text');
        if (!review) throw new Error('The source review is no longer available. Refresh the workspace and try again.');
        if (!proposed) throw new Error('Write the replacement text before saving this request.');
        if (proposed === String(review.excerpt || '').trim()) throw new Error('Change the proposed wording before saving the edit request.');
        const result = await api('/api/action-requests','POST',{review_id:review.id,action:'edit',proposed_text:proposed});
        if (!await reload()) throw new Error('The edit request was saved, but the workspace could not refresh. Reopen Resonance to see its current status.');
        requestDetail(result.action_request.id);
        toast(result.created ? 'Edit request saved.' : 'That edit request was already saved. Showing its current status.');
      }
    } catch(error) {dialogError(error);}
    finally {if (submit?.isConnected) submit.disabled = false;}
  }
  async function copyDraft() {
    if (!state.draft) return;
    try {await navigator.clipboard.writeText(state.draft);toast('Draft copied. You choose where it goes.');}
    catch {$('#draft-text')?.focus();$('#draft-text')?.select();toast('Your draft is selected. Use Copy from your device’s text menu.');}
  }
  async function handleAction(button) {
    const {action,id} = button.dataset;
    if (!state.data && action !== 'reload') return;
    if (action === 'reload') {button.disabled = true;await reload();if (button.isConnected) button.disabled = false;return;}
    if (action === 'navigate') {closeDialogIfOpen();navigate(button.dataset.view);}
    else if (action === 'close-dialog') closeDialog();
    else if (action === 'add-account') accountForm(null,button.dataset.platform || '');
    else if (action === 'setup-account') accountSetupDialog(id);
    else if (action === 'request-access-check') await requestAccessCheck(button);
    else if (action === 'cancel-access-check') await cancelAccessCheck(button);
    else if (action === 'mail-message') mailMessageDialog(id);
    else if (action === 'copy-mail-reply') await copyMailReply();
    else if (action === 'setup-identity') {closeDialogIfOpen();navigate('setup');setTimeout(() => $('#setup-identity')?.scrollIntoView({block:'start'}),0);}
    else if (action === 'start-samples') await switchSamples(button);
    else if (action === 'clear-samples') await switchSamples(button,true);
    else if (action === 'play-sample') playSample(id);
    else if (action === 'sample-decision') await sampleDecision(button);
    else if (action === 'welcome-paste') {closeDialogIfOpen();navigate('draft');}
    else if (action === 'walk-platform') {accountWalk.platform = button.dataset.platform;accountWalk.step = 'identity';accountWalkDialog();}
    else if (action === 'walk-back') {accountWalk.step = button.dataset.step;accountWalkDialog();}
    else if (action === 'account-walk-done') {accountWalk = null;await howPostsArrive();}
    else if (action === 'connect-assistant') await connectAssistant();
    else if (action === 'copy-assistant') {await navigator.clipboard.writeText($('#assistant-sentence').value);toast('Sentence copied. Give it to your assistant.');}
    else if (action === 'import-from-welcome') {settingsDialog();await loadDataFolder();$('#import-file')?.click();}
    else if (action === 'your-data') await yourDataDialog();
    else if (action === 'account-detail') accountDetail(id);
    else if (action === 'account-edit') accountForm(accountById(id));
    else if (action === 'add-excerpt') excerptForm(button.dataset.account || '');
    else if (action === 'add-review') reviewForm(button.dataset.account || '');
    else if (action === 'add-strength') reviewForm('','strength');
    else if (action === 'review-detail') reviewDetail(id);
    else if (action === 'flag-detail') reviewDetail(id,true);
    else if (action === 'posts-view-explain') openDialog(dialogShell('Posts in this view','One card for one saved source.', '<p>The count follows the status, account, finding and search filters above. A card brings together observations about the same original post; it does not count each observation as another post.</p>','<button class="button quiet" data-action="close-dialog">Done</button><button class="button primary" data-action="all-posts">Show all posts</button>'));
    else if (action === 'requests-explain') openDialog(dialogShell('Assistant work','Requests are saved instructions; they are not completed changes.', '<p>Waiting means a request is queued or running. Verification means its result still needs checking. Blocked or reopened work needs another look before anything else can happen.</p>','<button class="button quiet" data-action="close-dialog">Done</button><button class="button primary" data-action="all-posts">Open all posts</button>'));
    else if (action === 'all-posts') {closeDialogIfOpen();state.filters = {status:'all',account:'all',severity:'all',query:''};navigate('review');}
    else if (action === 'coverage-detail') coverageDetail(button.dataset.run,button.dataset.coverage);
    else if (action === 'source-verification') {const item = sourceItemById(id);const record = unresolvedAttestationForSource(item);if (item && record) attestationVerificationDialog(item,record);}
    else if (action === 'source-detail') sourceDetail(id,button.dataset.finding ?? null);
    else if (action === 'source-keep') await keepSource(button);
    else if (action === 'source-snooze') await updateSourceStatus(button);
    else if (action === 'fix-now') fixNowDialog(button.dataset.source);
    else if (action === 'fix-now-choice') fixNowDialog(button.dataset.source,button.dataset.fixAction);
    else if (action === 'attestation-step') attestationDialog(button.dataset.source,button.dataset.fixAction);
    else if (action === 'copy-fix-text') await copyFixText();
    else if (action === 'ask-assistant') askAssistantDialog(button.dataset.source);
    else if (action === 'assistant-choice') assistantRequestDialog(button.dataset.source,button.dataset.requestAction === 'delete' ? 'delete' : 'edit');
    else if (action === 'source-modify') sourceModifyDialog(button.dataset.source);
    else if (action === 'edit-next-steps') editNextSteps(button.dataset.source);
    else if (action === 'follow-up-draft') followUpDraft(button.dataset.source,button.dataset.draftKind);
    else if (action === 'copy-follow-up') await copyFollowUp();
    else if (action === 'source-delete') sourceDeleteDialog(button.dataset.source);
    else if (action === 'source-retract') retractionDialog(button.dataset.source);
    else if (action === 'retract-route') startRetraction(button.dataset.source,button.dataset.retractRoute);
    else if (action === 'retraction-next') cycleRetraction(button);
    else if (action === 'request-detail') requestDetail(id);
    else if (action === 'choose-request-action') await chooseRequestAction(button);
    else if (action === 'confirm-keep') await createActionRequest(button,button.dataset.review,'keep');
    else if (action === 'confirm-delete-request') await createActionRequest(button,button.dataset.review,'delete');
    else if (action === 'confirm-request') await confirmRequest(button);
    else if (action === 'cancel-request') await cancelRequest(button);
    else if (action === 'requests-view') {closeDialogIfOpen();navigate('review');setTimeout(() => $('#action-requests')?.focus({preventScroll:false}),0);}
    else if (action === 'add-sourced-review') {
      reviewForm(button.dataset.account || '');
      const details = $('.form-details',dialog);
      if (details) details.open = true;
      const source = $('#source-url');
      if (source) {source.required = true;source.closest('.field')?.querySelector('label span')?.remove();source.focus({preventScroll:true});}
    }
    else if (action === 'settings') {settingsDialog();await loadDataFolder();}
    else if (action === 'open-data-folder') await openDataFolder(button);
    else if (action === 'about') aboutDialog();
    else if (action === 'latest-check') {state.monitorRun = '';navigate('monitoring');}
    else if (action === 'view-check') {state.monitorRun = button.dataset.run;closeDialogIfOpen();navigate('monitoring');}
    else if (action === 'monitor-account') monitoringAccountDialog(button.dataset.run,button.dataset.account);
    else if (action === 'refresh-checks') {button.disabled = true;await refreshMonitoring(true);if (button.isConnected) button.disabled = false;}
    else if (action === 'account-reviews') {closeDialog();navigate('review',{account:id,status:'all'});}
    else if (action === 'review-filter') navigate('review',{...(button.dataset.severity ? {severity:button.dataset.severity} : {}),...(button.dataset.status ? {status:button.dataset.status} : {}),...(button.dataset.type ? {type:button.dataset.type} : {})});
    else if (action === 'review-status') {state.filters.status = button.dataset.status;render();const selected = $(`.status-tab[data-status="${button.dataset.status}"]`);selected?.focus({preventScroll:true});}
    else if (action === 'clear-filters') {Object.assign(state.filters,{account:'all',severity:'all',status:'needs_you',query:'',type:'all'});render();}
    else if (action === 'clear-account-search') {state.accountQuery='';render();$('#account-search')?.focus();}
    else if (action === 'clear-mail-filters') {state.mailQuery='';state.mailAccount='all';render();$('#mail-search')?.focus();}
    else if (action === 'check-draft') await checkDraft();
    else if (action === 'copy-draft') await copyDraft();
    else if (action === 'clear-draft') {
      const text = state.draft;
      openDialog(dialogShell('Clear this draft?','This text is only kept in the current tab.','<p class="detail-text">Clearing the editor removes the current draft. Copy it first if you want to keep it.</p>','<button class="button quiet" data-action="close-dialog">Keep writing</button><button class="button primary" data-action="confirm-clear-draft">Clear draft</button>'));
      state.draft = text;
    }
    else if (action === 'confirm-clear-draft') {clearTimeout(draftTimer);state.draftSequence++;state.draft='';state.draftResult=null;state.draftBusy=false;state.draftError='';closeDialog();render();$('#draft-text')?.focus();}
    else if (action === 'draft-signal') {const signal = state.draftResult?.signals[Number(button.dataset.index)];const editor = $('#draft-text');if (signal && editor) {editor.focus();editor.setSelectionRange(Math.max(0,signal.start),Math.min(editor.value.length,signal.end));editor.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});}}
    else if (action === 'use-in-draft') {
      const review = state.data.reviews.find(item => item.id === id);
      if (review) {
        if (state.draft && state.draft !== review.excerpt) {openDialog(dialogShell('Keep your current draft?','Compose already has writing in it.','<p class="detail-text">Opening this excerpt replaces the current draft. Copy your existing text first if you want to keep it.</p>',`<button class="button quiet" data-action="close-dialog">Keep current draft</button><button class="button primary" data-action="confirm-use-draft" data-id="${esc(id)}">Use this excerpt</button>`));}
        else useReviewDraft(review);
      }
    }
    else if (action === 'confirm-use-draft') {const review = state.data.reviews.find(item => item.id === id);if (review) useReviewDraft(review);}
    else if (action === 'choose-import') {if (showingSamples()) toast('Clear samples before importing your own backup.');else $('#import-file')?.click();}
    else if (action === 'confirm-import' && importPayload) {
      button.disabled = true;
      try {
        const result = await api('/api/import','POST',importPayload);
        importPayload = null;
        state.draftResult = null;
        await reload();
        closeDialog();
        toast(`Backup imported: ${result.accounts} accounts and ${result.reviews} review observations.`);
      } catch(error) {dialogError(error);if (button.isConnected) button.disabled=false;}
    }
  }
  function closeDialogIfOpen() {if (dialog.open) closeDialog();}
  function useReviewDraft(review) {
    clearTimeout(draftTimer);
    state.draftSequence++;
    state.draft = review.excerpt || '';
    state.draftAccount = review.account_id;
    state.draftResult = null;
    state.draftError = '';
    closeDialogIfOpen();
    navigate('draft');
    checkDraft();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    if (button.tagName === 'BUTTON') event.preventDefault();
    handleAction(button).catch(error => {if (dialog.open) dialogError(error);else toast(error.message);});
  });
  document.addEventListener('submit', event => {
    if (!['walk-identity-form','walk-boundaries-form','source-link-form','account-form','review-form','settings-form','excerpt-form','action-request-form','brand-form','mail-draft-form','attestation-form'].includes(event.target.getAttribute('id'))) return;
    event.preventDefault();
    saveForm(event.target);
  });
  document.addEventListener('input',event => {
    if (event.target.id === 'follow-up-text' && state.followUpDrafts[state.followUpKey]) {state.followUpDrafts[state.followUpKey].text=event.target.value;$('#copy-follow-up').disabled=!event.target.value.trim();}
    if (event.target.id === 'account-search') {state.accountQuery = event.target.value;$('#account-results').innerHTML = accountResults();}
    if (event.target.id === 'mail-search') {state.mailQuery = event.target.value;$('#mail-results').innerHTML = mailResults();}
    if (event.target.id === 'review-search') {state.filters.query = event.target.value;$('#review-results').innerHTML = reviewResults();}
    if (event.target.id === 'fix-proposed-text' && state.fixDraft) {state.fixDraft.proposedText = event.target.value;persistFixDraft();}
    if (event.target.id === 'attestation-note' && state.fixDraft) {state.fixDraft.note = event.target.value;persistFixDraft();}
    if (event.target.id === 'attestation-result-url' && state.fixDraft) {state.fixDraft.resultUrl = event.target.value;persistFixDraft();}
    if (event.target.id === 'draft-text') {
      state.draft = event.target.value;
      state.draftSequence++;
      state.draftBusy = false;
      state.draftResult = null;
      state.draftError = '';
      $('#draft-count').innerHTML = draftCount();
      document.querySelectorAll('[data-action="clear-draft"],[data-action="copy-draft"]').forEach(button => {button.disabled = !state.draft;});
      updateDraftResult();
      clearTimeout(draftTimer);
      if (state.draft.trim()) draftTimer = setTimeout(checkDraft,650);
    }
  });
  document.addEventListener('change',event => {
    if (event.target.id === 'check-run') {state.monitorRun = event.target.value;render();$('#check-run')?.focus({preventScroll:true});}
    if (event.target.id === 'platform') updateAccountFieldLabels(event.target.value);
    if (event.target.id === 'mail-account-filter') {state.mailAccount = event.target.value;$('#mail-results').innerHTML = mailResults();}
    const filterKeys = {'account-filter':'account','severity-filter':'severity','type-filter':'type'};
    if (filterKeys[event.target.id]) {state.filters[filterKeys[event.target.id]] = event.target.value;$('#review-results').innerHTML = reviewResults();}
    if (event.target.id === 'identity-mode') {
      const alias = event.target.value === 'alias';
      $('#sfw-only').disabled = !alias;
      if (!alias) $('#sfw-only').checked = true;
      $('#identity-help').textContent = alias ? 'An alias does not guarantee anonymity. Turning this off relaxes content checks for this account.' : 'Real-name and unclassified accounts always require SFW, at every daring level.';
    }
    if (event.target.id === 'draft-account') {state.draftAccount = event.target.value;state.draftResult = null;$('#draft-policy-note').innerHTML = draftPolicyNote();checkDraft();}
    if (event.target.id === 'import-file') importFile(event.target.files[0]);
  });
  document.addEventListener('keydown',event => {
    if (event.target.matches('.status-tab') && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
      event.preventDefault();
      const tabs = [...document.querySelectorAll('.status-tab')];
      const current = tabs.indexOf(event.target);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click();
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && state.view === 'draft' && !dialog.open) {event.preventDefault();checkDraft();}
  });
  dialog.addEventListener('click',event => {if (event.target === dialog) {const rect = dialog.getBoundingClientRect();if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();}});
  dialog.addEventListener('close',() => {if (dialogReturnFocus?.isConnected) dialogReturnFocus.focus({preventScroll:true});if (state.refreshPending) updateMonitoringView();if (state.communicationRefreshPending) updateCommunicationView();if (state.sourceRefreshPending) updateSourceView();refreshMonitoring();});
  window.addEventListener('hashchange',() => {state.view = navNames[location.hash.slice(1)] ? location.hash.slice(1) : 'overview';render();$('#main').focus({preventScroll:true});});
  window.addEventListener('online',() => {if (!state.connected) refreshMonitoring();});
  document.addEventListener('visibilitychange',() => {if (document.visibilityState === 'visible') refreshMonitoring();});
  document.addEventListener('focusout',() => {if (state.refreshPending) setTimeout(updateMonitoringView,0);if (state.communicationRefreshPending) setTimeout(updateCommunicationView,0);if (state.sourceRefreshPending) setTimeout(updateSourceView,0);});
  setInterval(refreshMonitoring,60000);
  matchMedia('(prefers-color-scheme: light)').addEventListener('change',applyTheme);
  state.view = navNames[location.hash.slice(1)] ? location.hash.slice(1) : 'overview';
  renderNav();
  reload();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();
