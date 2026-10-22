'use strict';

// Dependency-free B2 renderer and refresh checks. Browser layout remains a separate acceptance pass.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  return {innerHTML:'',textContent:'',value:'',content:'',dataset:{},style:{},hidden:false,open:false,isConnected:true,disabled:false,
    classList:{add(){},remove(){}},querySelector(){return null;},querySelectorAll(){return [];},childNodes:[],
    focus(){this.focused=true;},select(){this.selected=true;},scrollIntoView(){},setAttribute(){},removeAttribute(){},
    contains(node){return node === this;},replaceChildren(){},showModal(){this.open=true;},close(){this.open=false;}};
}

function fixture() {
  const accounts = [
    {id:'mail',platform:'Gmail',handle:'primary@example.test',display_name:'Primary mail',identity_mode:'named',daring:'careful',sfw_only:true},
    {id:'x',platform:'X',handle:'exact-x',display_name:'Exact X identity',identity_mode:'alias',daring:'balanced',sfw_only:true},
    {id:'linked',platform:'LinkedIn',handle:'exact-linked',display_name:'Exact LinkedIn identity',identity_mode:'named',daring:'careful',sfw_only:true},
  ];
  const reviews = ['need','wait','verify','unknown'].map((name,index) => ({id:`r-${name}`,account_id:index ? 'x' : 'linked',kind:'post',title:`${name} source`,excerpt:`${name} excerpt`,source_url:`https://example.test/${name}`,severity:'attention',finding:`${name} finding`,suggestion:'',sample_count:1,origin:'manual',status:'open',updated_at:`2026-09-1${index}T10:00:00Z`}));
  const source = (name,status,extra={}) => ({id:`source-${name}`,source_key:`key-${name}`,account_id:name === 'need' ? 'linked' : 'x',source_url:`https://example.test/${name}`,precise_source:true,identity_note:'Exact source',review_ids:[`r-${name}`],review_id:`r-${name}`,title:`${name} source`,excerpt:`${name} excerpt`,findings:[{id:`f-${name}`,title:`${name} finding`,detail:'Saved evidence',severity:'attention',kind:'review',review_ids:[`r-${name}`],flag_ids:[],provenance:{}}],evidence_fingerprint:`fp-${name}`,status,status_label:status,needs_attention:status === 'needs_you',request_ids:[],active_request_id:null,attestation_ids:[],snoozed_until:null,source_changed:false,...extra});
  const requests = [
    {id:'q-wait',review_id:'r-wait',account_id:'x',action:'edit',status:'pending',confirmation_required:false,original_excerpt:'wait excerpt',source_url:'https://example.test/wait',proposed_text:'new',source_key:'key-wait',evidence_fingerprint:'fp-wait',execution_state:'queued',started_at:null,attempt_id:null,blocker_reason:'',next_move:'none',can_cancel:true,can_execute:true,scheduled_for:'2026-09-16T13:00:00Z',created_at:'2026-09-15T10:00:00Z',updated_at:'2026-09-15T10:00:00Z',completed_at:null,receipt_url:'',receipt_note:''},
    {id:'q-unknown',review_id:'r-unknown',account_id:'x',action:'delete',status:'pending',confirmation_required:false,original_excerpt:'unknown excerpt',source_url:'https://example.test/unknown',proposed_text:'',source_key:'key-unknown',evidence_fingerprint:'fp-unknown',execution_state:'needs_verification',started_at:'2026-09-15T10:00:00Z',attempt_id:'attempt-1',blocker_reason:'',next_move:'none',can_cancel:false,can_execute:false,scheduled_for:null,created_at:'2026-09-15T09:00:00Z',updated_at:'2026-09-15T10:00:00Z',completed_at:null,receipt_url:'',receipt_note:''},
  ];
  const attestation = {id:'att-verify',review_id:'r-verify',account_id:'x',source_key:'key-verify',evidence_fingerprint:'fp-verify',action:'edit',note:'Changed by me.',proposed_text:'Updated text',result_url:'',original_excerpt:'verify excerpt',source_url:'https://example.test/verify',created_at:'2026-09-15T11:00:00Z',done_at:'2026-09-15T11:00:00Z',execution_state:'needs_verification',blocker_reason:'',next_move:'none'};
  const messages = [0,1,2,3].map(index => ({id:`m${index}`,account_id:'mail',provider_id:`p${index}`,thread_id:`t${index}`,subject:index === 0 ? 'Hello <script>' : `Subject ${index}`,sender:index === 0 ? 'A & B' : `Sender ${index}`,received_at:`2026-09-${15-index}T08:00:00Z`,source_url:`https://mail.example.test/${index}`,preview:index === 0 ? 'Saved <img> preview' : `Saved preview ${index}`,is_read:index % 2 === 0,imported_at:`2026-09-${15-index}T09:00:00Z`}));
  return {
    settings:{display_name:'Example',theme:'dark'},accounts,reviews,policy_flags:[],
    source_items:[source('need','needs_you'),source('wait','waiting',{request_ids:['q-wait'],active_request_id:'q-wait'}),source('verify','verification_pending',{attestation_ids:['att-verify']}),source('unknown','verification_pending',{request_ids:['q-unknown'],active_request_id:'q-unknown'})],
    attestations:[attestation],action_requests:requests,activity:[],
    communication:{brand:{description:'Grounded',audience:'Clients',voice:'Clear',avoid:'Hype',updated_at:'2026-09-15T09:00:00Z'},access:[
      {account_id:'mail',status:'verified',method:'connector',checked_at:new Date(Date.now()-3600000).toISOString(),detail:'Mailbox verified.',capabilities:['read_mail']},
      {account_id:'x',status:'blocked',method:'browser',checked_at:'2026-09-15T08:00:00Z',detail:'Sign-in unavailable.',capabilities:[]},
      {account_id:'linked',status:'verified',method:'browser',checked_at:'2026-09-10T08:00:00Z',detail:'Older access evidence.',capabilities:['view_profile']},
    ],setup_requests:[],mail_syncs:[{account_id:'mail',checked_at:'2026-09-15T09:00:00Z',scope:'Latest four inbox previews',imported_count:4,has_more:true,total_count:null,unread_count:2}],messages,drafts:[{id:'d0',message_id:'m0',account_id:'mail',text:'Saved reply',created_at:'2026-09-15T09:00:00Z',updated_at:'2026-09-15T09:00:00Z'}]},
    monitoring:{config:{enabled:true,cadence_minutes:1440,daily_time:'06:00',timezone:'America/Los_Angeles',ends_at:null},next_scheduled_check:'2026-09-16T13:00:00Z',runs:[{id:'old-partial',started_at:'2026-09-10T12:00:00Z',completed_at:'2026-09-10T12:10:00Z',summary:'Old partial report with explicit limits.',accounts:[{account_id:'mail',status:'checked',checked_at:'2026-09-10T12:05:00Z',summary:'Mailbox profile checked.',coverage:'Saved mailbox identity.',items_reviewed:1,deleted_count:0,pending_deletion_count:0,findings:[]},{account_id:'x',status:'partial',checked_at:'2026-09-10T12:06:00Z',summary:'Only visible profile context.',coverage:'Profile header only.',items_reviewed:1,deleted_count:0,pending_deletion_count:0,findings:[]}]}]},
  };
}

function setup() {
  const nodes = new Map();
  const missing = new Set();
  const get = key => {if (missing.has(key)) return null;if (!nodes.has(key)) nodes.set(key,element()); return nodes.get(key);};
  const nav = ['overview','mail','review','accounts','monitoring','draft'].map(key=>Object.assign(element(),{dataset:{nav:key}}));
  const document = {querySelector:get,querySelectorAll(selector){return selector === '[data-nav]' ? nav : [];},createElement(){return element();},documentElement:element(),activeElement:null,visibilityState:'visible'};
  const data = fixture(), calls = [];
  let handler = () => {throw new Error('Unexpected request');};
  const context = {document,URL,Intl,console,location:{hash:'#overview'},window:{scrollTo(){}},navigator:{clipboard:{writeText:async()=>{}}},matchMedia:()=>({matches:false}),setTimeout:()=>1,clearTimeout(){},fetch:async(url,options)=>{calls.push({url,...options});return handler(url,options);}};
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  const cutoff = source.indexOf("  document.addEventListener('click'");
  assert.ok(cutoff > 0);
  vm.runInContext(source.slice(0,cutoff) + '\n globalThis.ui={state,renderNav,renderOverview,renderAccounts,renderSetup,renderDraft,todayReportContent,todayMail,todayAccountAttention,communicationSignature,sourceStateSignature,monitoringSignature,updateSourceView,updateCommunicationView,updateMonitoringView,refreshMonitoring,aboutDialog,handleAction};\n})();',context);
  context.ui.state.data = structuredClone(data);
  context.ui.state.connected = true;
  context.ui.state.view = 'overview';
  context.ui.state.communicationSignature = context.ui.communicationSignature();
  context.ui.state.sourceSignature = context.ui.sourceStateSignature();
  context.ui.state.monitorSignature = context.ui.monitoringSignature();
  return {ui:context.ui,data,document,get,nav,calls,setHandler(fn){handler=fn;},setMissing(selector,value=true){if(value)missing.add(selector);else missing.delete(selector);}};
}

const response = data => ({ok:true,json:async()=>structuredClone(data)});

(async()=>{
  const empty = setup();
  empty.ui.state.data = {settings:{theme:'dark'},accounts:[],reviews:[],source_items:[],action_requests:[],communication:{messages:[]},monitoring:{runs:[]}};
  const welcome = empty.ui.renderOverview();
  const pitch = 'Resonance is a private notebook of what you have said online. Your own AI assistant does the reading and writes what it finds here — this page never touches your accounts.';
  assert.ok(welcome.includes(pitch));
  for (const door of ['See it with sample posts','Check something I wrote','Bring in my accounts']) assert.ok(welcome.includes(door));
  assert.ok(welcome.includes('data-action="your-data"'));
  assert.ok(welcome.indexOf(pitch) < welcome.indexOf('See it with sample posts'));
  empty.ui.aboutDialog();
  assert.ok(empty.get('#dialog-content').innerHTML.includes(pitch));
  await empty.ui.handleAction({dataset:{action:'welcome-paste'}});
  assert.ok(empty.ui.renderDraft().includes('General writing check'),'the paste road works without an account');

  const t = setup();
  const today = t.ui.renderOverview();
  for (const hook of ['today-grid','today-main','today-side','terminal-panel','today-report','today-mail-row','today-access-row','today-summary']) assert.ok(today.includes(hook),`Today includes ${hook}`);
  for (const id of ['source-need','source-wait','source-verify','source-unknown']) assert.equal((today.match(new RegExp(`data-source-id="${id}"`,'g'))||[]).length,1,`${id} appears once on Today`);
  assert.ok(today.includes('Old partial report with explicit limits.'));
  assert.ok(today.includes('Sep 10, 2026'),'an old report keeps its actual date');
  assert.ok(today.includes('aria-label="Report date Sep 10, 2026'));
  assert.ok(!today.includes('>Report date · Sep 10, 2026'),'the visible report date stays compact');
  assert.match(today,/1<\/strong> partial/);
  assert.match(today,/1<\/strong> not reported/);
  assert.ok(today.includes('Next scheduled check'));
  for(const state of ['checked','partial','blocked','missing']) assert.ok(today.includes(`data-action="coverage-detail" data-run="old-partial" data-coverage="${state}"`));
  await t.ui.handleAction({dataset:{action:'coverage-detail',run:'old-partial',coverage:'partial'}});
  assert.ok(t.get('#dialog-content').innerHTML.includes('only part of the intended scope'));
  assert.ok(t.get('#dialog-content').innerHTML.includes('Profile header only.'));
  assert.ok(t.get('#dialog-content').innerHTML.includes('data-action="monitor-account"'));
  await t.ui.handleAction({dataset:{action:'coverage-detail',run:'old-partial',coverage:'blocked'}});
  assert.ok(t.get('#dialog-content').innerHTML.includes('No accounts have this result'));
  assert.ok(t.get('#dialog-content').innerHTML.includes('Full report'));

  assert.ok(today.includes('Waiting'));
  assert.ok(today.includes('Verification'));
  assert.ok(today.includes('Done by you · verification pending'));
  assert.ok(today.includes('earlier attempt needs verification'),'an uncertain attempt stays verification work');

  assert.equal((today.match(/class="today-mail-row"/g)||[]).length,3,'Today limits saved mail to three previews');
  assert.ok(today.includes('Hello &lt;script&gt;'));
  assert.ok(today.includes('A &amp; B'));
  assert.ok(today.includes('Saved &lt;img&gt; preview'));
  assert.ok(today.includes('Received Sep 15, 2026'));
  assert.ok(today.includes('Saved Sep 15, 2026'));
  assert.ok(today.includes('Draft saved'));
  assert.ok(!today.includes('<script>'));
  assert.ok(!/needs reply/i.test(today),'Today does not infer reply state');

  const accounts = t.ui.renderAccounts();
  assert.equal((accounts.match(/data-account-id=/g)||[]).length,3,'Accounts renders one entry per identity');
  assert.ok(!accounts.includes('account-map'));
  for (const identity of ['primary@example.test','exact-x','exact-linked']) assert.ok(accounts.includes(identity));
  assert.ok(accounts.includes('Sign-in unavailable.'));
  assert.ok(accounts.includes('Latest four inbox previews'));

  const setupView = t.ui.renderSetup();
  assert.ok(setupView.includes('id="brand-form"'));
  assert.ok(setupView.includes('id="brand-description"'));
  assert.ok(setupView.includes('data-view="accounts"'));
  assert.ok(setupView.includes('Daily at 6 a.m. ' + new Intl.DateTimeFormat(undefined,{timeZone:'America/Los_Angeles',timeZoneName:'longGeneric'}).formatToParts(new Date()).find(part=>part.type==='timeZoneName').value));
  assert.ok(!setupView.includes('setup-progress'));
  assert.match(setupView, /<button class="button primary" type="submit">Save personal brand<\/button>/, 'blocked accounts leave the brand save control enabled');
  assert.ok(t.ui.renderDraft().includes('COMPOSE'));
  assert.ok(!t.ui.renderDraft().includes('DRAFT STUDIO'));

  const noReport = setup();
  noReport.ui.state.data.monitoring.runs = [];
  const noReportHtml = noReport.ui.todayReportContent();
  assert.ok(noReportHtml.includes('No result recorded.'));
  assert.ok(noReportHtml.includes('Report date · not recorded'));
  noReport.ui.state.data.monitoring = null;
  assert.ok(noReport.ui.todayReportContent().includes('Check history is unavailable'));

  const accountRefresh = setup();
  accountRefresh.ui.state.view = 'accounts';
  accountRefresh.ui.state.accountQuery = 'exact-x';
  accountRefresh.get('#account-search').value = 'exact-x';
  accountRefresh.get('#account-results').innerHTML = 'old result';
  const changedAccounts = structuredClone(accountRefresh.data);
  changedAccounts.communication.access.find(item=>item.account_id === 'x').detail = 'Fresh blocked detail.';
  accountRefresh.setHandler(()=>response(changedAccounts));
  await accountRefresh.ui.refreshMonitoring();
  assert.equal(accountRefresh.get('#account-search').value,'exact-x','Accounts refresh preserves typed search text');
  assert.ok(accountRefresh.get('#account-results').innerHTML.includes('Fresh blocked detail.'));
  assert.ok(!accountRefresh.get('#account-results').innerHTML.includes('exact-linked'),'the active search still controls refreshed results');

  const mailRefresh = setup();
  mailRefresh.get('#today-mail-count').innerHTML = '4';
  mailRefresh.get('#today-mail-content').innerHTML = 'old mail';
  const changedMail = structuredClone(mailRefresh.data);
  changedMail.communication.messages.push({...changedMail.communication.messages[0],id:'m4',provider_id:'p4',subject:'Fifth saved message'});
  mailRefresh.setHandler(()=>response(changedMail));
  await mailRefresh.ui.refreshMonitoring();
  assert.equal(mailRefresh.get('#today-mail-count').innerHTML,'5','Today mail count refreshes with its preview list');
  assert.ok(mailRefresh.get('#today-mail-content').innerHTML.includes('Fifth saved message'));

  const sourceRefresh = setup();
  const changedSources = structuredClone(sourceRefresh.data);
  changedSources.source_items.push({...changedSources.source_items[0],id:'source-need-2',source_key:'key-need-2',title:'second needs-you source'});
  sourceRefresh.setHandler(()=>response(changedSources));
  await sourceRefresh.ui.refreshMonitoring();
  assert.match(sourceRefresh.nav.find(item=>item.dataset.nav === 'review').innerHTML,/nav-count[^>]*>2</,'targeted Today refresh updates the Posts badge');

  const accountSourceRefresh = setup();
  accountSourceRefresh.ui.state.view = 'accounts';
  accountSourceRefresh.ui.state.accountQuery = 'exact-x';
  accountSourceRefresh.get('#account-search').value = 'exact-x';
  accountSourceRefresh.get('#account-results').innerHTML = accountSourceRefresh.ui.renderAccounts();
  const changedAccountSources = structuredClone(accountSourceRefresh.data);
  changedAccountSources.reviews.push({...changedAccountSources.reviews[1],id:'r-extra',title:'extra source'});
  changedAccountSources.source_items.push({...changedAccountSources.source_items[1],id:'source-extra',source_key:'key-extra',review_ids:['r-extra'],review_id:'r-extra',title:'extra source',active_request_id:null,request_ids:[]});
  accountSourceRefresh.setHandler(()=>response(changedAccountSources));
  await accountSourceRefresh.ui.refreshMonitoring();
  assert.match(accountSourceRefresh.get('#account-results').innerHTML,/<strong>4<\/strong><span>review observations/,'Accounts refreshes source-derived counts');
  assert.equal(accountSourceRefresh.get('#account-search').value,'exact-x');

  const accountMonitorRefresh = setup();
  accountMonitorRefresh.ui.state.view = 'accounts';
  accountMonitorRefresh.ui.state.accountQuery = 'exact-x';
  accountMonitorRefresh.get('#account-search').value = 'exact-x';
  const changedCoverage = structuredClone(accountMonitorRefresh.data);
  changedCoverage.monitoring.runs[0].accounts.find(item=>item.account_id === 'x').coverage = 'Expanded monitored coverage.';
  accountMonitorRefresh.setHandler(()=>response(changedCoverage));
  await accountMonitorRefresh.ui.refreshMonitoring();
  assert.ok(accountMonitorRefresh.get('#account-results').innerHTML.includes('Expanded monitored coverage.'),'Accounts refreshes monitoring-derived coverage');
  assert.equal(accountMonitorRefresh.get('#account-search').value,'exact-x');

  const structuralRefresh = setup();
  structuralRefresh.ui.state.view = 'accounts';
  structuralRefresh.ui.state.data = {...structuredClone(structuralRefresh.data),accounts:[],reviews:[],policy_flags:[],source_items:[],action_requests:[],attestations:[],communication:{...structuredClone(structuralRefresh.data.communication),access:[],mail_syncs:[],messages:[],drafts:[]}};
  structuralRefresh.get('#main').innerHTML = 'empty account view';
  structuralRefresh.setMissing('#account-results');
  structuralRefresh.ui.state.data = structuredClone(structuralRefresh.data);
  const typing = element();
  structuralRefresh.document.activeElement = typing;
  structuralRefresh.get('#main').contains = node => node === typing;
  structuralRefresh.ui.updateCommunicationView();
  assert.equal(structuralRefresh.get('#main').innerHTML,'empty account view');
  assert.equal(structuralRefresh.ui.state.communicationRefreshPending,true,'zero-to-one structural refresh defers while the page is active');
  structuralRefresh.document.activeElement = null;
  structuralRefresh.get('#main').contains = () => false;
  structuralRefresh.ui.updateCommunicationView();
  assert.ok(structuralRefresh.get('#main').innerHTML.includes('data-account-id="mail"'),'zero-to-one account refresh rebuilds the empty view');

  const ageRefresh = setup();
  ageRefresh.ui.state.data.communication.access.find(item=>item.account_id === 'linked').checked_at = new Date(Date.now()-86400001).toISOString();
  const previousSignature = JSON.parse(ageRefresh.ui.communicationSignature());
  previousSignature.access_freshness = previousSignature.access_freshness.map(([id,key]) => [id,id === 'linked' ? 'verified' : key]);
  ageRefresh.ui.state.communicationSignature = JSON.stringify(previousSignature);
  ageRefresh.get('#today-accounts-content').innerHTML = 'fresh before threshold';
  ageRefresh.setHandler(()=>response(ageRefresh.ui.state.data));
  await ageRefresh.ui.refreshMonitoring();
  assert.ok(ageRefresh.get('#today-accounts-content').innerHTML.includes('Recheck due'),'age crossing refreshes derived account attention with unchanged API JSON');

  const deferred = setup();
  deferred.get('#today-report-content').innerHTML = 'old report in use';
  const active = element();
  deferred.document.activeElement = active;
  deferred.get('#today-report-content').contains = node => node === active;
  const changedReport = structuredClone(deferred.data);
  changedReport.monitoring.runs[0].summary = 'New report after focus leaves.';
  deferred.setHandler(()=>response(changedReport));
  await deferred.ui.refreshMonitoring();
  assert.equal(deferred.get('#today-report-content').innerHTML,'old report in use');
  assert.equal(deferred.ui.state.refreshPending,true,'focused report defers replacement');
  deferred.document.activeElement = null;
  deferred.ui.updateMonitoringView();
  assert.ok(deferred.get('#today-report-content').innerHTML.includes('New report after focus leaves.'));
  assert.equal(deferred.ui.state.refreshPending,false);

  const workerEvents={},removed=[];
  let activation;
  const workerContext={self:{addEventListener(name,handler){workerEvents[name]=handler;},clients:{claim:async()=>{}}},
    caches:{keys:async()=>['social-brief-shell-v12','resonance-shell-v15','resonance-shell-v16','resonance-shell-v18','unrelated-cache'],
      delete:async key=>{removed.push(key);return true;}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/sw.js'),'utf8'),workerContext);
  workerEvents.activate({waitUntil(promise){activation=promise;}});
  await activation;
  assert.deepEqual(removed.sort(),['resonance-shell-v15','resonance-shell-v16','social-brief-shell-v12']);

  console.log('B2 Today/Accounts renderer and refresh checks passed (no browser or visual claim).');
})().catch(error=>{console.error(error);process.exitCode=1;});
