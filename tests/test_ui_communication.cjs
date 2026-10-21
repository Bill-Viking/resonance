'use strict';

// Dependency-free renderer and handler checks. This is not browser or visual QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  return {innerHTML:'',textContent:'',value:'',dataset:{},style:{},hidden:false,open:false,isConnected:true,disabled:false,
    classList:{add(){},remove(){}},querySelector(){return null;},querySelectorAll(){return [];},
    focus(){},select(){},scrollIntoView(){},setAttribute(){},removeAttribute(){},contains(node){return node === this;},
    showModal(){this.open=true;},close(){this.open=false;}};
}
function setup() {
  const nodes = new Map();
  const get = key => { if (!nodes.has(key)) nodes.set(key,element()); return nodes.get(key); };
  const document = {querySelector:get,querySelectorAll(){return [];},documentElement:element(),activeElement:null,visibilityState:'visible'};
  const now = Date.now();
  const accounts = [
    {id:'gmail',platform:'Gmail',handle:'primary@example.test',display_name:'Primary mail',identity_mode:'named',daring:'careful',sfw_only:true},
    {id:'outlook',platform:'Outlook',handle:'other@example.test',display_name:'Other mail',identity_mode:'named',daring:'careful',sfw_only:true},
    {id:'social',platform:'LinkedIn',handle:'example',display_name:'Example profile',identity_mode:'named',daring:'balanced',sfw_only:true},
  ];
  const communication = {
    brand:{description:'Grounded',audience:'Clients',voice:'Clear & warm',avoid:'Hype',updated_at:new Date(now-3000).toISOString()},
    access:[
      {account_id:'gmail',status:'verified',method:'connector',checked_at:new Date(now-3600000).toISOString(),detail:'Primary connector identity verified.',capabilities:['read_mail']},
      {account_id:'outlook',status:'blocked',method:'browser',checked_at:new Date(now-7200000).toISOString(),detail:'Alternate mailbox unavailable.',capabilities:[]},
      {account_id:'social',status:'verified',method:'browser',checked_at:new Date(now-172800000).toISOString(),detail:'Old check.',capabilities:['view_profile']},
    ],
    setup_requests:[],
    mail_syncs:[{account_id:'gmail',checked_at:new Date(now-1000).toISOString(),scope:'Latest 25 inbox previews',imported_count:25,has_more:true,total_count:null,unread_count:4}],
    messages:[{id:'m1',account_id:'gmail',provider_id:'p1',thread_id:'t1',subject:'Hello <script>',sender:'A & B',received_at:new Date(now-2000).toISOString(),source_url:'https://mail.example.test/message/1',preview:'A saved <preview>',is_read:false,imported_at:new Date(now-1000).toISOString()}],
    drafts:[{id:'d1',message_id:'m1',account_id:'gmail',text:'Saved reply',created_at:new Date(now-1000).toISOString(),updated_at:new Date(now-1000).toISOString()}],
  };
  const data = {settings:{display_name:'Example',theme:'dark'},accounts,reviews:[],policy_flags:[],action_requests:[],activity:[],monitoring:{config:{enabled:true,cadence_minutes:1440,daily_time:'06:00',timezone:'America/Los_Angeles',ends_at:null},runs:[]},communication};
  const calls = [];
  let handler = () => { throw new Error('Unexpected request'); };
  const context = {document,URL,Intl,console,FormData:class {constructor(form){this.data=form.values;}get(key){return this.data[key];}has(key){return Object.hasOwn(this.data,key);}},location:{hash:'#mail'},window:{scrollTo(){}},navigator:{clipboard:{writeText:async()=>{}}},
    matchMedia:()=>({matches:false}),setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options)=>{calls.push({url,...options});return handler(url,options);}};
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  const cutoff = source.indexOf("  document.addEventListener('click'");
  assert.ok(cutoff > 0);
  vm.runInContext(source.slice(0,cutoff) + '\n globalThis.ui={state,accessState,renderAccounts,accountResults,renderSetup,renderMail,mailResults,mailMessageDialog,requestAccessCheck,accountForm,accountSetupDialog,communicationSignature,updateCommunicationView,refreshMonitoring,readAccountIdentity,handleAction,saveForm};\n})();',context);
  context.ui.state.data = structuredClone(data);
  context.ui.state.connected = true;
  context.ui.state.view = 'mail';
  return {ui:context.ui,get,calls,data,setHandler(fn){handler=fn;},html(){return get('#dialog-content').innerHTML;}};
}
const response = data => ({ok:true,json:async()=>structuredClone(data)});

(async()=>{
  const t = setup();
  const accountsView = t.ui.renderAccounts();
  for (const id of ['gmail','outlook','social']) assert.equal((accountsView.match(new RegExp(`data-account-id="${id}"`,'g'))||[]).length,1,'every saved identity appears once');
  assert.ok(!accountsView.includes('account-map'),'Accounts has one searchable identity list');
  assert.ok(accountsView.includes('Verified snapshot'));
  assert.ok(accountsView.includes('Blocked'));
  assert.ok(accountsView.includes('Recheck due'),'old verified access is visibly stale');
  assert.ok(accountsView.includes('Latest 25 inbox previews'),'mail coverage remains distinct from access');
  assert.ok(accountsView.includes('primary@example.test'),'exact mailbox identity stays visible');

  const accountCards = t.ui.accountResults();
  assert.ok(accountCards.includes('Blocked'),'account cards use the same access record as the map');
  assert.ok(accountCards.includes('Recheck due'),'stale access agrees in both account entry points');
  assert.ok(!accountCards.includes('No access snapshot recorded'),'legacy profile fields cannot contradict communication access');

  const setupView = t.ui.renderSetup();
  for (const label of ['How I want to come across','Who I speak to','My voice','What to avoid']) assert.ok(setupView.includes(label));
  assert.ok(setupView.includes('Daily at 6 a.m. ' + new Intl.DateTimeFormat(undefined,{timeZone:'America/Los_Angeles',timeZoneName:'longGeneric'}).formatToParts(new Date()).find(part=>part.type==='timeZoneName').value));
  assert.ok(setupView.includes('does not become a live mailbox connection'));
  assert.ok(setupView.includes('data-view="accounts"'),'preferences links to independent account setup');
  assert.ok(!setupView.includes('setup-progress'),'preferences has no all-accounts-fresh completion gate');

  const mail = t.ui.renderMail();
  assert.ok(mail.includes('latest 25 inbox previews'));
  assert.ok(mail.includes('Partial saved scope'));
  assert.ok(mail.includes('Hello &lt;script&gt;'));
  assert.ok(mail.includes('A &amp; B'));
  assert.ok(mail.includes('Draft saved'));
  assert.ok(!mail.includes('<script>'),'saved mail text is escaped');

  t.ui.mailMessageDialog('m1');
  assert.ok(t.html().includes('Saved reply'));
  assert.ok(t.html().includes('Local only'));
  assert.ok(t.html().includes('Clear &amp; warm'));
  assert.ok(t.html().includes('Grounded'),'the description field appears in brand guidance');
  assert.ok(t.html().includes('Reply from Gmail · primary@example.test'));
  assert.ok(t.html().includes('Open original'));
  assert.ok(!/>\s*Send\s*</.test(t.html()),'mail dialog offers no send control');

  t.ui.accountForm(null,'Gmail');
  assert.ok(t.html().includes('Your email address'));
  assert.ok(t.html().includes('does not read or send mail'));
  assert.throws(()=>t.ui.readAccountIdentity('Gmail','https://mail.google.com/'),/email address/);

  for (const [platform,signIn] of [['Discord','https://discord.com/login'],['Substack','https://substack.com/sign-in']]) {
    const provider = setup();
    provider.ui.accountForm(null,platform);
    assert.ok(provider.html().includes(`Step 2 of 4 · ${platform}`));
    assert.ok(provider.html().includes('Your profile link or handle'));
    assert.ok(!provider.html().includes('Your email address'));
    const account = {...provider.ui.state.data.accounts[2],id:platform,platform,handle:'synthetic-user'};
    provider.ui.state.data.accounts.push(account);
    provider.ui.accountSetupDialog(platform);
    assert.ok(provider.html().includes('Not checked'));
    provider.ui.state.data.communication.access.push({account_id:platform,status:'needs_login',method:'browser',checked_at:new Date().toISOString(),detail:'Signed out.',capabilities:[]});
    provider.ui.accountSetupDialog(platform);
    assert.ok(provider.html().includes(`href="${signIn}"`));
    assert.ok(provider.html().includes('Sign-in needed'));
    assert.ok(provider.html().includes('No capabilities were recorded'));
  }

  const walk = setup();
  const click = (action,extra={}) => walk.ui.handleAction({dataset:{action,...extra}});
  const form = (id,values) => ({values,reportValidity:()=>true,getAttribute:()=>id,querySelector:()=>null});
  await click('add-account');
  for (const platform of ['X','Reddit','YouTube','Discord','Substack']) assert.ok(walk.html().includes(`data-platform="${platform}"`));
  await click('walk-platform',{platform:'X'});
  await walk.ui.saveForm(form('walk-identity-form',{identity:'https://x.com/river_example'}));
  assert.ok(walk.html().includes('X · river_example'));
  assert.ok(walk.html().includes('https://x.com/river_example'));
  assert.ok(walk.html().includes('always stay safe for work'));
  assert.ok(walk.html().includes('never overrides safe-for-work'));
  assert.throws(()=>walk.ui.readAccountIdentity('X','https://example.test/river'),/X profile/);
  assert.throws(()=>walk.ui.readAccountIdentity('X','https://x.com/river/status/100'),/does not identify a profile/);
  const savedAccount={id:'new-account',platform:'X',handle:'river_example',url:'https://x.com/river_example',identity_mode:'named',daring:'bold',sfw_only:true};
  walk.setHandler(url=>url === '/api/accounts' ? response({account:savedAccount}) : response({...walk.data,accounts:[...walk.data.accounts,savedAccount]}));
  await walk.ui.saveForm(form('walk-boundaries-form',{identity_mode:'named',daring:'bold'}));
  assert.equal(walk.calls.filter(call=>call.url === '/api/accounts').length,1);
  assert.equal(JSON.parse(walk.calls[0].body).sfw_only,true);
  assert.ok(walk.html().includes('https://x.com/i/flow/login'));
  assert.ok(walk.html().includes('Resonance never sees your password'));
  assert.ok(walk.html().includes('does not verify a sign-in'));
  await click('account-walk-done');
  assert.ok(walk.html().includes('Connect your assistant'));
  assert.ok(walk.html().includes('I have a Resonance backup'));
  assert.ok(walk.html().includes('Coming next'));
  walk.setHandler(()=>response({prompt:'Read ASSISTANT.md in /tmp/synthetic-resonance and run my Resonance check.'}));
  await click('connect-assistant');
  assert.ok(walk.html().includes('/tmp/synthetic-resonance'));

  const request = {id:'s1',account_id:'outlook',status:'pending',created_at:new Date().toISOString(),updated_at:new Date().toISOString(),note:''};
  t.setHandler((url,options)=>options.method === 'POST' ? response({setup_request:request}) : response({...t.data,communication:{...t.data.communication,setup_requests:[request]}}));
  await t.ui.requestAccessCheck({...element(),dataset:{id:'outlook'}});
  assert.deepEqual(JSON.parse(t.calls[0].body),{account_id:'outlook'});
  assert.equal(t.calls[1].url,'/api/state');
  assert.ok(t.html().includes('Access check requested'));
  assert.ok(!t.html().includes('Connected'),'a queued check does not claim a connection');

  const refresh = setup();
  refresh.ui.state.view = 'mail';
  refresh.ui.state.communicationSignature = refresh.ui.communicationSignature();
  refresh.get('#mailbox-summary').innerHTML = 'old access';
  refresh.get('#mail-results').innerHTML = 'old mail';
  const changed = structuredClone(refresh.data);
  changed.communication.access[0].detail = 'Fresh daily access evidence';
  changed.communication.messages[0].subject = 'New daily message';
  refresh.setHandler(()=>response(changed));
  await refresh.ui.refreshMonitoring();
  assert.ok(refresh.get('#mailbox-summary').innerHTML.includes('Verified snapshot'),'daily refresh updates mailbox evidence in place');
  assert.ok(refresh.get('#mail-results').innerHTML.includes('New daily message'),'daily refresh updates saved mail in place');

  refresh.ui.state.view = 'setup';
  refresh.get('#brand-description').value = 'Unsaved personal wording';
  refresh.get('#setup-accounts-summary').innerHTML = 'old summary';
  refresh.ui.updateCommunicationView();
  assert.equal(refresh.get('#brand-description').value,'Unsaved personal wording','targeted setup refresh preserves unsaved brand text');
  assert.ok(refresh.get('#setup-accounts-summary').innerHTML.includes('3 saved identities'));

  refresh.get('#dialog').open = true;
  refresh.get('#mail-reply').value = 'Unsaved reply in progress';
  await refresh.ui.refreshMonitoring();
  assert.equal(refresh.get('#mail-reply').value,'Unsaved reply in progress','an open reply dialog is never replaced by the background refresh');

  console.log('Communication UI renderer/handler checks passed (no browser or visual claim).');
})().catch(error=>{console.error(error);process.exitCode=1;});
