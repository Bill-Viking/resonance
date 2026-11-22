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
  vm.runInContext(source.slice(0,cutoff) + '\n globalThis.ui={state,accessState,renderAccounts,accountResults,renderSetup,renderMail,mailResults,mailMessageDialog,requestAccessCheck,accountForm,accountSetupDialog,communicationSignature,updateCommunicationView,refreshMonitoring,readAccountIdentity,handleAction,saveForm,settingsDialog,importFile,render,reload};\n})();',context);
  context.ui.state.data = structuredClone(data);
  context.ui.state.connected = true;
  context.ui.state.view = 'mail';
  return {ui:context.ui,get,calls,data,document,setHandler(fn){handler=fn;},html(){return get('#dialog-content').innerHTML;}};
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

  for (const [platform,signIn] of [['Discord','https://discord.com/login'],['Substack','https://substack.com/sign-in'],['Threads','https://www.threads.com/']]) {
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
  for (const platform of ['X','Reddit','YouTube','Discord','Substack','Threads']) assert.ok(walk.html().includes(`data-platform="${platform}"`));
  const threadsCom = walk.ui.readAccountIdentity('Threads','https://www.threads.com/@synthetic.user');
  assert.equal(threadsCom.handle,'synthetic.user');
  assert.equal(threadsCom.url,'https://www.threads.com/@synthetic.user');
  const threadsNet = walk.ui.readAccountIdentity('Threads','https://www.threads.net/@synthetic_user');
  assert.equal(threadsNet.handle,'synthetic_user');
  assert.equal(threadsNet.url,'https://www.threads.net/@synthetic_user');
  const sharedThreads = walk.ui.readAccountIdentity('Threads','https://www.threads.com/@synthetic.user/?xmt=synthetic-value');
  assert.equal(sharedThreads.handle,'synthetic.user');
  assert.equal(sharedThreads.url,'https://www.threads.com/@synthetic.user');
  const threadsHandle = walk.ui.readAccountIdentity('Threads','@synthetic_user');
  assert.equal(threadsHandle.handle,'synthetic_user');
  assert.equal(threadsHandle.url,'https://threads.com/@synthetic_user');
  assert.throws(()=>walk.ui.readAccountIdentity('Threads','https://www.threads.com/@synthetic_user/post/abc'),/Threads profile/);
  assert.throws(()=>walk.ui.readAccountIdentity('Threads','https://other.example/@synthetic_user'),/Threads profile/);
  assert.throws(()=>walk.ui.readAccountIdentity('Threads','https://user:password@www.threads.com/@synthetic_user'),/password or fragment/);
  assert.throws(()=>walk.ui.readAccountIdentity('Threads','https://www.threads.com:8443/@synthetic_user'),/Threads profile/);
  assert.throws(()=>walk.ui.readAccountIdentity('Threads','https://www.threads.com/@synthetic_user#fragment'),/password or fragment/);
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

  // Optional RES credit on public drafts: real form render and save behaviour.
  const creditForm = html => {
    const start = html.indexOf('<form id="brand-form">');
    const form = html.slice(start,html.indexOf('</form>',start));
    const select = form.match(/<select id="brand-public-signature"[^>]*>([\s\S]*?)<\/select>/);
    const checkbox = form.match(/<input id="brand-public-banner"[^>]*>/);
    assert.ok(select && checkbox,'both credit controls are inside the brand form');
    return {form,select:select[1],checkbox:checkbox[0],
      selected:(select[1].match(/<option value="([a-z]+)" selected>/)||[])[1],
      checked:/\schecked(\s|>)/.test(checkbox[0])};
  };
  const creditSetup = brand => {
    const c = setup();
    c.ui.state.data.communication.brand = brand;
    c.ui.state.view = 'setup';
    return c;
  };
  const legacy = creditSetup({description:'Grounded',audience:'Clients',voice:'Clear',avoid:'Hype',updated_at:new Date().toISOString()});
  const legacyHtml = legacy.ui.renderSetup();
  const legacyForm = creditForm(legacyHtml);
  assert.ok(legacyForm.form.includes('<h3 id="brand-credit-heading">RES credit on public drafts</h3>'),'the credit heading is visible');
  assert.ok(legacyForm.form.includes('<label for="brand-public-signature">Text signature</label>'));
  assert.ok(legacyForm.form.includes('Include my approved RES banner'));
  assert.deepEqual([...legacyForm.select.matchAll(/<option value="([a-z]+)"[^>]*>([^<]*)<\/option>/g)].map(m=>[m[1],m[2]]),
    [['off','Off'],['compact','Short — RES'],['full','Full — RES and project link']]);
  assert.ok(legacyForm.form.includes('Optional instructions for your assistant when creating new public posts or replies. Nothing is added or posted automatically. A banner needs an approved image supplied separately.'));
  assert.equal(legacyForm.selected,'off','a record without the fields renders the signature off');
  assert.equal(legacyForm.checked,false,'a record without the fields renders the banner off');
  assert.ok(legacyForm.checkbox.includes('type="checkbox"'),'the banner is a native checkbox');
  assert.ok(legacyHtml.includes('Grounded') && legacyHtml.includes('Hype'),'existing brand text still renders');
  const odd = creditSetup({description:'',audience:'',voice:'',avoid:'',updated_at:null,public_signature:'surprise',public_banner:'yes'});
  const oddForm = creditForm(odd.ui.renderSetup());
  assert.equal(oddForm.selected,'off','an unknown stored signature falls back to off');
  assert.equal(oddForm.checked,false,'only a real true value checks the banner');

  const saveBrand = async (c, values) => {
    let saved = null;
    c.setHandler((url,options)=>{
      if (url === '/api/brand') {
        saved = JSON.parse(options.body);
        return response({brand:{...saved,updated_at:new Date().toISOString()}});
      }
      const next = structuredClone(c.data);
      next.communication.brand = {...c.ui.state.data.communication.brand,...saved,updated_at:new Date().toISOString()};
      return response(next);
    });
    await c.ui.saveForm({querySelector:()=>null,getAttribute:()=> 'brand-form',reportValidity:()=>true,values});
    return saved;
  };
  // Saving a legacy record untouched keeps both choices off (unchecked boxes are absent from FormData).
  const offSaved = await saveBrand(legacy,{description:'Grounded',audience:'Clients',voice:'Clear',avoid:'Hype',public_signature:'off'});
  assert.equal(offSaved.public_signature,'off');
  assert.strictEqual(offSaved.public_banner,false);
  // Opting in saves and shows the saved values after reload.
  const optIn = await saveBrand(legacy,{description:'Grounded',audience:'Clients',voice:'Clear',avoid:'Hype',public_signature:'full',public_banner:'on'});
  assert.equal(optIn.public_signature,'full');
  assert.strictEqual(optIn.public_banner,true);
  assert.equal(legacy.calls.filter(call=>call.url === '/api/brand').length,2);
  const reloaded = creditForm(legacy.ui.renderSetup());
  assert.equal(reloaded.selected,'full','the saved signature shows after reload');
  assert.equal(reloaded.checked,true,'the saved banner shows after reload');
  // Opt-in values are retained when the rendered form is saved again without changes.
  for (const [signature,banner] of [['compact',true],['full',false],['compact',false]]) {
    const kept = creditSetup({description:'D',audience:'A',voice:'V',avoid:'X',updated_at:new Date().toISOString(),public_signature:signature,public_banner:banner});
    const shown = creditForm(kept.ui.renderSetup());
    assert.equal(shown.selected,signature);
    assert.equal(shown.checked,banner);
    const resubmitted = await saveBrand(kept,{description:'D',audience:'A',voice:'V',avoid:'X',public_signature:shown.selected,...(shown.checked ? {public_banner:'on'} : {})});
    assert.equal(resubmitted.public_signature,signature,'saving the unchanged form retains the signature');
    assert.strictEqual(resubmitted.public_banner,banner,'saving the unchanged form retains the banner');
  }
  assert.ok(legacy.calls.every(call=>call.url.startsWith('/')),'saving credit choices makes no outside request');
  legacy.ui.settingsDialog();
  assert.ok(legacy.html().includes('data-action="setup-identity"') && legacy.html().includes('Preferences: personal brand and RES credit'),'Settings links to the Preferences brand form and names the credit choice');
  assert.ok(legacy.html().includes('>Settings &amp; data<') && legacy.html().includes('Save settings') && !legacy.html().includes('Save preferences') && !legacy.html().includes('A workspace that feels like you'),'the dialog is named Settings & data and saves with Save settings');
  assert.match(legacy.ui.renderSetup(),/<h1>Preferences<\/h1>/,'the page is named Preferences');

  // U5: an unrelated reload keeps unsaved Preferences edits and saves nothing; an explicit save shows the saved values and refocuses Save.
  const pending = setup();
  const resets = [];
  const control = (id,props,defaults) => { const node = pending.get('#'+id); Object.assign(node,props); resets.push(() => Object.assign(node,defaults)); return node; };
  control('brand-form',{},{});
  const description = control('brand-description',{value:'Grounded and direct',defaultValue:'Grounded'},{value:'Grounded'});
  const audience = control('brand-audience',{value:'Clients',defaultValue:'Clients'},{value:'Clients'});
  const signatureField = control('brand-public-signature',{tagName:'SELECT',value:'compact',options:[{value:'off',defaultSelected:true},{value:'compact',defaultSelected:false}]},{value:'off'});
  const bannerField = control('brand-public-banner',{type:'checkbox',checked:true,defaultChecked:false},{checked:false});
  const savedNote = control('brand-saved-state',{textContent:'Saved a minute ago'},{textContent:'Saved a minute ago'});
  let pageHtml = '';
  // A redraw replaces every control with a fresh one holding the saved values.
  Object.defineProperty(pending.get('#main'),'innerHTML',{get:() => pageHtml,set:value => {pageHtml = value;resets.forEach(restore => restore());}});
  pending.ui.state.view = 'setup';
  pending.setHandler(url => { if (url === '/api/state') return response(pending.data); throw new Error(`Unexpected ${url}`); });
  assert.equal(await pending.ui.reload(),true);
  assert.ok(pageHtml.includes('id="brand-form"'),'the page was redrawn from saved state');
  assert.equal(description.value,'Grounded and direct','an unrelated reload keeps the typed brand text');
  assert.equal(signatureField.value,'compact','an unsaved signature choice survives the reload');
  assert.equal(bannerField.checked,true,'an unsaved banner choice survives the reload');
  assert.equal(audience.value,'Clients','untouched fields still show saved state');
  assert.ok(savedNote.textContent.includes('unsaved edits are kept'),'the kept edits are named');
  assert.equal(pending.calls.filter(call => call.method && call.method !== 'GET').length,0,'a reload never saves the pending edits');
  const saveControl = pending.get('#brand-save');
  saveControl.focus = () => { saveControl.focused = true; };
  const brandValues = {description:'Grounded and direct',audience:'Clients',voice:'Clear & warm',avoid:'Hype',public_signature:'compact'};
  pending.setHandler(url => {
    if (url === '/api/brand') return response({brand:brandValues});
    const next = structuredClone(pending.data);
    next.communication.brand = {...next.communication.brand,...brandValues,public_banner:true,updated_at:new Date().toISOString()};
    return response(next);
  });
  await pending.ui.saveForm({querySelector:() => null,getAttribute:() => 'brand-form',reportValidity:() => true,values:{...brandValues,public_banner:'on'}});
  assert.equal(pending.calls.filter(call => call.url === '/api/brand').length,1,'only the explicit save writes');
  assert.equal(description.value,'Grounded','after a successful save the page shows freshly rendered saved state, not stale edits');
  assert.equal(saveControl.focused,true,'focus returns to the Save control after a successful save');

  // A successful PATCH followed by a failed state read must not poison later edits.
  pending.setHandler(url => {
    if (url === '/api/brand') return response({brand:brandValues});
    throw new Error('Synthetic state refresh failure');
  });
  await pending.ui.saveForm({querySelector:() => null,getAttribute:() => 'brand-form',reportValidity:() => true,values:brandValues});
  description.value = 'New words after the failed refresh';
  pending.setHandler(() => response(pending.data));
  assert.equal(await pending.ui.reload(),true);
  assert.equal(description.value,'New words after the failed refresh','a failed post-save refresh cannot discard edits typed later');

  // Retaining edits must still rescue focus when a different main-page control disappears.
  const oldControl = element(), mainRegion = pending.get('#main');
  pending.document.activeElement = oldControl;
  mainRegion.contains = node => node === oldControl && pending.document.activeElement === oldControl;
  mainRegion.focus = () => { pending.document.activeElement = mainRegion; };
  pending.document.querySelector = selector => selector === '#today-status' ? null : pending.get(selector);
  resets.push(() => { pending.document.activeElement = null; });
  pending.ui.render();
  assert.equal(description.value,'New words after the failed refresh');
  assert.equal(pending.document.activeElement,mainRegion,'kept edits do not suppress the focus fallback');

  // Import preview: public credit choices that would change are shown before confirmation.
  const previewImport = async (current,communicationRecord,{schema=4,omit=false}={}) => {
    const page = creditSetup({description:'D',audience:'A',voice:'V',avoid:'X',updated_at:new Date().toISOString(),...current});
    const payload = {schema_version:schema,accounts:[],reviews:[],settings:{}};
    if (!omit) payload.communication = communicationRecord;
    const before = page.calls.length;
    await page.ui.importFile({size:100,name:'synthetic.json',text:async()=>JSON.stringify(payload)});
    assert.equal(page.calls.length,before,'previewing a backup makes no request');
    return page;
  };
  const brandOf = (fields,extra={}) => ({brand:{description:'D',audience:'A',voice:'V',avoid:'X',updated_at:null,...fields},access:[],setup_requests:[],mail_syncs:[],messages:[],drafts:[],...extra});
  const plain = page => page.html().replace(/<[^>]*>/g,' ').replace(/\s+/g,' ');
  const offToFull = await previewImport({},brandOf({public_signature:'full',public_banner:true}));
  assert.ok(offToFull.html().includes('confirm-import'),'the existing confirmation remains');
  assert.ok(offToFull.html().includes('RES credit choices will change'));
  assert.match(plain(offToFull),/Text signature: Off → Full — RES and project link/);
  assert.match(plain(offToFull),/Banner: Off → On/);
  assert.ok(offToFull.html().includes('guide future public drafts. They are not permission to publish.'));
  assert.ok(!/type="checkbox"/.test(offToFull.html()),'no extra permission checkbox');
  assert.ok(offToFull.html().indexOf('RES credit choices will change') < offToFull.html().indexOf('confirm-import'),'the notice precedes the confirmation button');
  const onToOffOld = await previewImport({public_signature:'compact',public_banner:true},brandOf({}));
  assert.match(plain(onToOffOld),/Text signature: Short — RES → Off/,'a backup without the fields means off');
  assert.match(plain(onToOffOld),/Banner: On → Off/);
  const bannerOnly = await previewImport({public_signature:'compact',public_banner:false},brandOf({public_signature:'compact',public_banner:true}));
  assert.match(plain(bannerOnly),/Text signature: Short — RES → Short — RES/);
  assert.match(plain(bannerOnly),/Banner: Off → On/);
  for (const same of [[{},brandOf({})],[{},brandOf({public_signature:'off',public_banner:false})],[{public_signature:'full',public_banner:true},brandOf({public_signature:'full',public_banner:true})]]) {
    const unchanged = await previewImport(...same);
    assert.ok(unchanged.html().includes('confirm-import'));
    assert.ok(!unchanged.html().includes('RES credit choices'),'unchanged choices add no notice');
  }
  for (const schema of [1,2,3]) {
    const older = await previewImport({public_signature:'full',public_banner:true},brandOf({}),{schema});
    assert.match(plain(older),/Text signature: Full — RES and project link → Off/,`format ${schema} also previews`);
  }
  const noCommunication = await previewImport({public_signature:'full',public_banner:true},null,{omit:true});
  assert.ok(noCommunication.html().includes('confirm-import'));
  assert.ok(!noCommunication.html().includes('RES credit choices'),'a backup without communication leaves preferences alone');
  const malicious = '<img src=x onerror=alert(1)>';
  for (const bad of [{public_signature:'loud'},{public_signature:malicious},{public_signature:null},{public_signature:['full']},{public_signature:{}},{public_banner:'true'},{public_banner:1},{public_banner:null}]) {
    const refused = await previewImport({},brandOf(bad));
    assert.ok(!refused.html().includes('confirm-import'),`invalid ${JSON.stringify(bad)} is refused`);
    assert.ok(!refused.html().includes('RES credit choices'));
    assert.ok(refused.get('#dialog-error').textContent.includes('public credit choices are not valid'));
    assert.ok(!refused.get('#dialog-error').innerHTML.includes('<img'),'the invalid value is never echoed as markup');
  }
  for (const badCommunication of [{access:[]},{brand:null},{brand:[]},{brand:'x'},null]) {
    const refused = await previewImport({},badCommunication);
    assert.ok(!refused.html().includes('confirm-import'),'a communication record without a valid brand is refused');
  }
  // A hostile current value is never shown as given either.
  const hostile = await previewImport({public_signature:malicious},brandOf({public_signature:'compact'}));
  assert.ok(!hostile.html().includes('<img'));
  assert.match(plain(hostile),/Text signature: Off → Short — RES/);
  // Confirming still sends the file unchanged.
  offToFull.setHandler(url => url === '/api/import' ? response({accounts:0,reviews:0}) : url === '/api/state' ? response(offToFull.data) : (()=>{throw new Error('Unexpected '+url);})());
  await offToFull.ui.handleAction({dataset:{action:'confirm-import'},disabled:false,setAttribute(){},removeAttribute(){},isConnected:true});
  const sentImport = offToFull.calls.find(call=>call.url === '/api/import');
  assert.ok(sentImport,'the existing confirmation sends the import');
  assert.equal(JSON.parse(sentImport.body).communication.brand.public_signature,'full');

  console.log('Communication UI renderer/handler checks passed (no browser or visual claim).');
})().catch(error=>{console.error(error);process.exitCode=1;});
