'use strict';

// Dependency-free source-card and handler checks, not browser/layout verification.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  return {innerHTML:'',textContent:'',value:'',dataset:{},style:{},hidden:false,open:false,isConnected:true,disabled:false,
    classList:{add(){},remove(){}},querySelector(){return null;},querySelectorAll(){return [];},contains(){return false;},
    focus(){this.focused=true;},select(){this.selected=true;},setSelectionRange(){},scrollIntoView(){},setAttribute(){},removeAttribute(){},
    insertAdjacentHTML(_position,html){this.innerHTML+=html;},showModal(){this.open=true;},close(){this.open=false;}};
}
function fixture() {
  const request={id:'q1',review_id:'r2',account_id:'a1',action:'edit',status:'pending',confirmation_required:false,
    original_excerpt:'Original <words>',source_url:'https://example.test/comment/real?item=1',proposed_text:'Revised wording.',
    source_key:'source-key',evidence_fingerprint:'fingerprint',execution_state:'queued',started_at:null,attempt_id:null,
    blocker_reason:'',next_move:'none',can_cancel:true,can_execute:true,scheduled_for:'2026-09-16T13:00:00Z',
    created_at:'2026-09-15T10:00:00Z',updated_at:'2026-09-15T10:00:00Z',completed_at:null,receipt_url:'',receipt_note:''};
  const reviews=[
    {id:'r1',account_id:'a1',kind:'comment',title:'First observation',excerpt:'Original <words>',source_url:'https://example.test/comment/real?item=1',severity:'suggestion',finding:'Consider the wording.',suggestion:'',sample_count:1,origin:'manual',status:'open',updated_at:'2026-09-15T09:00:00Z'},
    {id:'r2',account_id:'a1',kind:'comment',title:'Second observation',excerpt:'A different saved excerpt',source_url:'https://example.test/comment/real?item=1',severity:'attention',finding:'Check the context.',suggestion:'',sample_count:1,origin:'report',status:'open',updated_at:'2026-09-15T10:00:00Z'},
  ];
  const findings=[0,1,2,3].map(index=>({id:`finding-${index}`,title:`Finding ${index+1}`,detail:`Detail ${index+1}`,severity:index===0?'attention':'suggestion',kind:'review',review_ids:index<2?['r1']:['r2'],flag_ids:[`flag-${index}`],provenance:{report_id:'report-1'}}));
  const source={id:'source-1',source_key:'source-key',account_id:'a1',source_url:'https://example.test/comment/real?item=1',precise_source:true,identity_note:'Exact comment source',review_ids:['r1','r2'],review_id:'r1',title:'One authored comment',excerpt:'Original <words>',findings,evidence_fingerprint:'fingerprint',status:'waiting',status_label:'Waiting for assistant',needs_attention:false,request_ids:['q1'],active_request_id:'q1',attestation_ids:[],snoozed_until:null,source_changed:false};
  return {settings:{display_name:'Example',theme:'dark'},accounts:[{id:'a1',platform:'Reddit',handle:'example',identity_mode:'named',daring:'careful',sfw_only:true}],reviews,
    policy_flags:findings.map((finding,index)=>({id:`flag-${index}`,review_id:index<2?'r1':'r2',account_id:'a1',title:finding.title,excerpt:'Original <words>',source_url:source.source_url,detail:finding.detail,status:'open'})),
    source_items:[source],attestations:[],action_requests:[request],activity:[],communication:{brand:{voice:'Clear & warm',avoid:'Avoid hype, not a literal phrase list'}},
    monitoring:{config:{enabled:true,cadence_minutes:1440,daily_time:'06:00',timezone:'America/Los_Angeles',ends_at:null},next_scheduled_check:'2026-09-16T13:00:00Z',runs:[]}};
}
function setup({clipboardFails=false,storage=new Map()}={}) {
  const nodes=new Map();
  const get=key=>{if(!nodes.has(key))nodes.set(key,element());return nodes.get(key);};
  const nav=['overview','mail','review','accounts','monitoring','draft'].map(key=>Object.assign(element(),{dataset:{nav:key}}));
  const document={querySelector:get,querySelectorAll(selector){return selector==='[data-nav]'?nav:[];},documentElement:element(),activeElement:null,visibilityState:'visible'};
  class FakeFormData {constructor(form){this.fields=form.fields||{};}get(key){return this.fields[key]||'';}has(key){return Object.hasOwn(this.fields,key);}}
  const sessionStorage={get length(){return storage.size;},key(index){return [...storage.keys()][index]??null;},getItem(key){return storage.has(key)?storage.get(key):null;},setItem(key,value){storage.set(key,String(value));},removeItem(key){storage.delete(key);}};
  const data=fixture(),calls=[];
  let handler=()=>{throw new Error('Unexpected request');};
  const context={document,URL,Intl,console,FormData:FakeFormData,sessionStorage,location:{hash:'#review'},window:{scrollTo(){}},navigator:{clipboard:{writeText:async()=>{if(clipboardFails)throw new Error('denied');}}},
    matchMedia:()=>({matches:false}),setTimeout:fn=>{fn();return 1;},clearTimeout(){},
    fetch:async(url,options)=>{assert.match(url,/^\/api\//);calls.push({url,...options});return handler(url,options);}};
  vm.createContext(context);
  const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  const cutoff=source.indexOf("  document.addEventListener('click'");
  assert.ok(cutoff>0,'event wiring boundary is present');
  vm.runInContext(source.slice(0,cutoff)+ '\n globalThis.ui={loadDataFolder,openDataFolder,renderMonitoring,importFile,state,scheduleLabel,nextScheduledCheckText,renderNav,renderOverview,renderReview,reviewResults,sourceCard,sourceDetail,reviewDetail,fixNowDialog,attestationDialog,askAssistantDialog,editRequestDialog,deleteRequestDialog,requestDetail,renderRequests,createActionRequest,cancelRequest,confirmRequest,keepSource,updateSourceStatus,copyFixText,saveForm,storedFixDraft,persistFixDraft,clearFixDraft,highlightedText,sourceModifyDialog,sourceDeleteDialog,retractionDialog,startRetraction,cycleRetraction,assistantRequestDialog,settingsDialog,THEMES,RETRACTIONS,renderSampleBanner,playSample,sampleDecision,handleAction,resetWorkspaceEditors};\n})();',context);
  context.ui.state.data=structuredClone(data);
  context.ui.state.connected=true;
  context.ui.state.view='review';
  return {ui:context.ui,nodes,get,nav,data,calls,storage,setHandler(fn){handler=fn;},html(){return get('#dialog-content').innerHTML;}};
}
const response=data=>({ok:true,json:async()=>structuredClone(data)});

(async()=>{
  const t=setup();
  const practice=setup();
  practice.ui.state.data.workspace={mode:'samples',token:'sample-epoch'};
  practice.ui.renderSampleBanner();
  assert.equal(practice.get('#sample-banner').hidden,false);
  assert.ok(practice.get('#sample-banner').innerHTML.includes('Sample data — nothing here is yours.'));
  assert.ok(practice.get('#sample-banner').innerHTML.includes('Play the assistant’s next check'));
  assert.ok(!practice.ui.sourceCard(practice.ui.state.data.source_items[0]).includes('href="https://'),'sample cards never open platforms');
  practice.ui.playSample('q1');
  assert.ok(practice.html().includes('With your real posts your assistant asks you this in your chat with it, not here.'));
  assert.ok(practice.html().includes('Yes, edit'));
  assert.ok(practice.html().includes('Not now'));
  assert.ok(practice.html().includes('Revised wording.'));
  const finished=structuredClone(practice.ui.state.data);
  finished.action_requests[0].execution_state='verified';
  finished.action_requests[0].can_execute=false;
  finished.source_items[0].status='verified';
  practice.setHandler(url=>response(url==='/api/samples/rehearse'?{sample_proof:'Sample proof: verified.'}:finished));
  await practice.ui.sampleDecision({dataset:{id:'q1',decision:'yes'},isConnected:true});
  assert.equal(practice.calls[0].url,'/api/samples/rehearse');
  assert.equal(practice.calls[0].headers['X-Resonance-Workspace'],'sample-epoch');
  assert.equal(practice.ui.state.filters.status,'history');
  assert.ok(!practice.get('#sample-banner').innerHTML.includes('data-action="play-sample"'));
  const sampleDrafts=setup();
  sampleDrafts.ui.state.data.workspace={mode:'samples',token:'first-practice'};
  sampleDrafts.ui.sourceModifyDialog('source-1');
  assert.match(sampleDrafts.html(),/data-fix-action="edit" disabled/);
  assert.ok(sampleDrafts.html().includes('Real posts only. Practice by asking your assistant.'));
  sampleDrafts.storage.set('resonance:practice-draft:first-practice:a1:source-1:fp:edit','synthetic practice draft');
  sampleDrafts.storage.set('resonance:fix-draft:v1:a1:source-1:fp:edit','synthetic real draft');
  sampleDrafts.ui.resetWorkspaceEditors();
  assert.equal(sampleDrafts.storage.size,1);
  assert.ok(sampleDrafts.storage.has('resonance:fix-draft:v1:a1:source-1:fp:edit'),'clearing samples preserves real drafts');
  const ordinary=setup();
  ordinary.ui.playSample('q1');
  assert.equal(ordinary.html(),'','real mode has no rehearsal dialog');
  ordinary.ui.renderSampleBanner();
  assert.equal(ordinary.get('#sample-banner').hidden,true);
  const storage=setup();
  storage.ui.settingsDialog();
  assert.ok(storage.html().includes('data-folder-details'));
  storage.setHandler(()=>response({path:'/tmp/synthetic/<folder>',size_bytes:1024,last_automatic_backup:null}));
  await storage.ui.loadDataFolder();
  const storageHtml=storage.get('#data-folder-details').innerHTML;
  assert.ok(storageHtml.includes('/tmp/synthetic/&lt;folder&gt;'));
  assert.ok(storageHtml.includes('Last automatic backup: None yet'));
  assert.ok(storageHtml.includes('Open folder'));
  storage.setHandler(()=>response({opened:true}));
  const openButton=element();
  await storage.ui.openDataFolder(openButton);
  assert.equal(storage.calls.at(-1).url,'/api/data-folder/open');
  assert.equal(storage.calls.at(-1).method,'POST');
  assert.equal(storage.calls.at(-1).body,'{}');
  assert.equal(openButton.disabled,false);
  for (const version of [1,2]) {
    const importer=setup();
    const payload={schema_version:version,accounts:[],reviews:[],settings:{}};
    await importer.ui.importFile({size:100,name:'synthetic.json',text:async()=>JSON.stringify(payload)});
    assert.ok(importer.html().includes('confirm-import'),`backup v${version} reaches confirmation`);
  }
  for (const version of [true,0,3,'2']) {
    const importer=setup();
    const payload={schema_version:version,accounts:[],reviews:[],settings:{}};
    await importer.ui.importFile({size:100,name:'synthetic.json',text:async()=>JSON.stringify(payload)});
    assert.ok(importer.get('#dialog-error').textContent.includes('Choose a Resonance backup'));
    assert.ok(!importer.html().includes('confirm-import'));
  }
  assert.equal(t.ui.scheduleLabel(),'Daily at 6 a.m. ' + new Intl.DateTimeFormat(undefined,{timeZone:'America/Los_Angeles',timeZoneName:'longGeneric'}).formatToParts(new Date()).find(part=>part.type==='timeZoneName').value);
  t.ui.state.data.monitoring.config.timezone='Asia/Kolkata';
  assert.match(t.ui.scheduleLabel(),/Daily at 6 a.m. (India Standard Time|India Time)/);
  t.ui.state.data.monitoring.config.timezone='America/Los_Angeles';
  t.ui.state.data.monitoring.config.action_scope='Synthetic scope';
  assert.ok(t.ui.renderMonitoring().includes('Action scope'));
  assert.ok(t.ui.renderMonitoring().includes('Synthetic scope'));
  assert.ok(!/this Mac|Codex/.test(t.ui.renderOverview()));
  assert.match(t.ui.nextScheduledCheckText(),/^Next scheduled check · /);
  t.ui.renderNav();
  assert.ok(t.nav.find(item=>item.dataset.nav==='overview').innerHTML.includes('Today'));
  assert.ok(t.nav.find(item=>item.dataset.nav==='review').innerHTML.includes('Posts'));
  assert.ok(t.nav.find(item=>item.dataset.nav==='draft').innerHTML.includes('Compose'));
  assert.ok(!t.nav.find(item=>item.dataset.nav==='review').innerHTML.includes('nav-count'),'waiting work does not increase the attention count');
  const today=t.ui.renderOverview();
  assert.equal((today.match(/data-source-id=/g)||[]).length,1,'Today uses the same unique-source unit as Posts');
  assert.match(today,/data-status="waiting"[^>]*>[\s\S]*?<strong>1<\/strong>/,'Today reports the authoritative waiting-source count');

  t.ui.state.filters.status='all';
  const rendered=t.ui.reviewResults();
  assert.equal((rendered.match(/data-source-id=/g)||[]).length,1,'two reviews and four flags render as one source card');
  for(let index=1;index<=4;index++)assert.ok(rendered.includes(`Finding ${index}`));
  for(const label of ['Keep','Modify…','Delete…','Snooze'])assert.ok(rendered.includes(label));
  assert.ok(!rendered.includes('Fix now')&&!rendered.includes('Ask assistant'),'the tile offers one Modify and one Delete instead of the old pair');
  assert.match(rendered,/data-action="source-modify" data-source="source-1"/);
  assert.match(rendered,/data-action="source-delete" data-source="source-1"/);
  assert.match(rendered,/<button type="button" class="badge badge-button [a-z]+" data-action="source-detail" data-id="source-1"/,'the card status badge answers a click with the source explanation');
  assert.ok(!rendered.includes('Handled'));
  assert.ok(!rendered.includes('Reviewed'));
  assert.ok(rendered.includes('Saved excerpt · may be partial'));
  const queue=setup();
  const contextView=setup();
  const contextItem=contextView.ui.state.data.source_items[0];
  const imageData='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jm1sAAAAASUVORK5CYII=';
  contextItem.findings[0].context=[{kind:'parent_media',inspection:'inspected',source_url:'https://social.example/posts/parent',summary:'Parent <script>text</script>',checked_at:'2026-10-01T12:00:00Z',image:{mime_type:'image/png',base64:imageData,alt:'A shape "in context".'}}];
  let contextCard=contextView.ui.sourceCard(contextItem);
  assert.ok(contextCard.includes('Parent image or video'));
  assert.ok(contextCard.includes('Inspected'));
  assert.ok(contextCard.includes('Parent &lt;script&gt;text&lt;/script&gt;'));
  assert.ok(contextCard.includes('alt="A shape &quot;in context&quot;."'));
  assert.ok(contextCard.includes('src="data:image/png;base64,'));
  assert.ok(!/<img[^>]+src="https?:/.test(contextCard),'context previews never fetch external images');
  assert.ok(!contextCard.includes('<script>'));
  contextView.ui.sourceDetail(contextItem.id);
  assert.ok(contextView.html().includes('Saved preview'));
  assert.ok(contextView.html().includes('Open context source'));
  assert.ok(contextView.ui.sourceCard(contextItem,true).includes('Saved image available in the full review.'));
  contextItem.findings[0].context[0].inspection='not_inspected';
  contextCard=contextView.ui.sourceCard(contextItem);
  assert.ok(contextCard.includes('Not inspected'));
  assert.ok(!contextCard.includes('<img'),'uninspected media cannot display a preview even in malformed input');
  contextItem.findings[0].context[0].inspection='inspected';
  contextItem.findings[0].context[0].image.mime_type='image/svg+xml';
  assert.ok(!contextView.ui.sourceCard(contextItem).includes('<img'),'unsupported image types are not rendered');
  assert.ok(rendered.includes('Conversation and media context were not recorded'));

  const queuedCard=()=>queue.ui.sourceCard(queue.ui.state.data.source_items[0]);
  assert.match(queuedCard(),/Asked your assistant to edit · not changed yet · next check .+ · it asks your yes first/);
  queue.ui.state.data.action_requests[0].action='delete';
  assert.match(queuedCard(),/Asked your assistant to delete · not deleted yet · next check /);
  queue.ui.state.data.action_requests[0].scheduled_for=null;
  assert.ok(queuedCard().includes('no next check is scheduled — ask your assistant to run your Resonance check'),'the request does not borrow a later global schedule');
  for(const execution of ['running','blocked','needs_verification','reopened','verified','cancelled']) {
    queue.ui.state.data.action_requests[0].execution_state=execution;
    assert.ok(!queuedCard().includes('Asked your assistant to'),'only queued work gets the queued sentence');
  }
  const workSummary=t.ui.renderRequests();
  assert.ok(workSummary.includes('1 active request'));
  assert.ok(workSummary.includes('data-status="waiting"'));
  assert.ok(!workSummary.includes('Original &lt;words&gt;'),'assistant summary does not duplicate source excerpts');
  const imprecise={...t.data.source_items[0],id:'uncertain',precise_source:false,source_url:'',identity_note:'Profile evidence only',active_request_id:null,request_ids:[]};
  const impreciseCard=t.ui.sourceCard(imprecise);
  assert.match(impreciseCard,/data-action="source-keep"[^>]*>/,'Keep remains available for imprecise evidence');
  assert.match(impreciseCard,/data-action="source-modify"[^>]*disabled/,'imprecise evidence cannot authorize Modify');
  assert.match(impreciseCard,/data-action="source-delete"[^>]*disabled/,'imprecise evidence cannot authorize a deletion route');
  assert.ok(impreciseCard.includes('Profile evidence only'),'the precise-link note stays on the card');

  const linkRepair=setup();
  linkRepair.ui.state.data.action_requests=[];
  const repairItem=linkRepair.ui.state.data.source_items[0];
  Object.assign(repairItem,{precise_source:false,status:'needs_you',source_url:'https://example.test/profile',review_ids:['r1'],active_request_id:null,request_ids:[]});
  linkRepair.ui.sourceDetail('source-1');
  assert.ok(linkRepair.html().includes('Add the exact link'));
  assert.ok(linkRepair.html().includes('id="source-link-form"'));
  assert.ok(linkRepair.html().includes('name="evidence_fingerprint" value="fingerprint"'));
  const repaired=structuredClone(linkRepair.ui.state.data);
  Object.assign(repaired.source_items[0],{id:'new-source',precise_source:true,source_url:'https://reddit.com/r/example/comments/post/title/comment'});
  repaired.reviews[0].source_url_correction=repaired.source_items[0].source_url;
  linkRepair.setHandler(url=>response(url.endsWith('/link')?{source_item:repaired.source_items[0]}:repaired));
  await linkRepair.ui.saveForm({querySelector:()=>null,getAttribute:()=> 'source-link-form',reportValidity:()=>true,fields:{source_id:'source-1',source_url:repaired.source_items[0].source_url,evidence_fingerprint:'fingerprint'}});
  assert.equal(linkRepair.calls[0].url,'/api/source-items/source-1/link');
  assert.equal(JSON.parse(linkRepair.calls[0].body).evidence_fingerprint,'fingerprint');
  assert.ok(linkRepair.html().includes('data-source="new-source"'),'the dialog follows the rekeyed source');
  linkRepair.ui.deleteRequestDialog('r1');
  assert.ok(linkRepair.html().includes(repaired.source_items[0].source_url),'last look uses the corrected link');
  const blockedAccount=setup();
  blockedAccount.ui.state.data.action_requests=[];
  Object.assign(blockedAccount.ui.state.data.source_items[0],{status:'needs_you',active_request_id:null,request_ids:[]});
  blockedAccount.ui.state.data.communication.access=[{account_id:'a1',status:'needs_login',detail:'Sign-in expired.',checked_at:new Date().toISOString(),capabilities:[]}];
  blockedAccount.ui.sourceDetail('source-1');
  assert.ok(blockedAccount.html().includes('Sign in to Reddit'));
  assert.ok(blockedAccount.html().includes('https://www.reddit.com/login/'));
  const aggregate=setup();
  aggregate.ui.state.data.action_requests=[];
  Object.assign(aggregate.ui.state.data.source_items[0],{precise_source:false,active_request_id:null,request_ids:[]});
  aggregate.ui.sourceDetail('source-1');
  assert.ok(!aggregate.html().includes('id="source-link-form"'));
  assert.ok(aggregate.html().includes('Save a specific review'));

  t.ui.reviewDetail('r2');
  assert.ok(t.html().includes('Evidence and request history'),'a sibling entry point opens the shared source evidence');
  assert.ok(t.html().includes('First observation'));
  assert.ok(t.html().includes('Second observation'));
  assert.ok(t.html().includes('report id: report-1'),'finding provenance remains readable');
  assert.ok(t.html().includes('data-id="q1"'),'request history stays reachable');
  assert.equal(t.calls.length,0);

  t.ui.askAssistantDialog('source-1');
  assert.ok(t.html().includes('already has active work'),'Ask assistant routes a sibling to existing work');
  assert.equal(t.calls.length,0,'opening sibling work does not create a second request');
  assert.ok(t.html().includes('Next scheduled check'));
  assert.ok(t.html().includes('not a completion promise'));

  const blocked=structuredClone(t.data.action_requests[0]);
  blocked.execution_state='blocked';blocked.can_cancel=false;blocked.blocker_reason='Sign-in expired';blocked.next_move='open_source';
  t.ui.state.data.action_requests=[blocked];
  t.ui.state.data.source_items[0].active_request_id='q1';
  const blockedCard=t.ui.sourceCard(t.ui.state.data.source_items[0]);
  assert.ok(blockedCard.includes('Sign-in expired'));
  assert.ok(blockedCard.includes('Open source to unblock'));
  t.ui.requestDetail('q1');
  assert.ok(!t.html().includes('data-action="cancel-request"'),'non-cancellable blocked work cannot be presented as stopped');
  t.ui.state.data=structuredClone(t.data);

  t.ui.fixNowDialog('source-1','edit');
  assert.ok(t.html().includes('SAVED EXCERPT · MAY BE PARTIAL'));
  assert.ok(t.html().includes('not a literal phrase matcher'));
  assert.ok(t.html().includes('Open exact original'));
  t.get('#fix-proposed-text').value='My complete proposed text';
  await t.ui.copyFixText();
  assert.equal(t.ui.state.fixDraft.proposedText,'My complete proposed text');

  const denial=setup({clipboardFails:true});
  denial.ui.fixNowDialog('source-1','edit');
  denial.get('#fix-proposed-text').value='Selectable fallback';
  await denial.ui.copyFixText();
  assert.equal(denial.get('#fix-proposed-text').selected,true,'clipboard denial selects the text');
  assert.match(denial.get('#copy-recovery').innerHTML,/denied/i);
  assert.ok(denial.html().includes('Open exact original'),'normal source link remains available after popup or clipboard failure');

  const sharedStorage=new Map();
  const beforeReload=setup({storage:sharedStorage});
  beforeReload.ui.fixNowDialog('source-1','edit');
  Object.assign(beforeReload.ui.state.fixDraft,{proposedText:'Survives a real reload',note:'Changed the complete comment.',resultUrl:'https://example.test/comment/result'});
  beforeReload.ui.persistFixDraft();
  const afterReload=setup({storage:sharedStorage});
  afterReload.ui.fixNowDialog('source-1','edit');
  assert.equal(afterReload.ui.state.fixDraft.proposedText,'Survives a real reload');
  assert.equal(afterReload.ui.state.fixDraft.note,'Changed the complete comment.');
  assert.equal(afterReload.ui.state.fixDraft.resultUrl,'https://example.test/comment/result');
  afterReload.ui.attestationDialog('source-1','edit');
  assert.ok(afterReload.html().includes('Changed the complete comment.'));
  assert.ok(afterReload.html().includes('https://example.test/comment/result'));
  const changedEvidence=setup({storage:sharedStorage});
  changedEvidence.ui.state.data.source_items[0].evidence_fingerprint='different-fingerprint';
  changedEvidence.ui.fixNowDialog('source-1','edit');
  assert.equal(changedEvidence.ui.state.fixDraft.proposedText,'Original <words>','draft from different evidence is not restored');
  assert.equal(changedEvidence.ui.state.fixDraft.note,'');

  const missingNote=setup();
  missingNote.ui.state.fixDraft={sourceId:'source-1',action:'delete',proposedText:'',note:'',resultUrl:''};
  const missingForm={fields:{source_id:'source-1',review_id:'r1',action:'delete',note:'',result_url:''},reportValidity:()=>true,getAttribute:key=>key==='id'?'attestation-form':'',querySelector:()=>null};
  await missingNote.ui.saveForm(missingForm);
  assert.equal(missingNote.calls.length,0,'Done by you cannot be saved without a note');
  assert.match(missingNote.get('#dialog-error').textContent,/brief note/i);

  const saved=structuredClone(t.data);
  saved.source_items[0].status='verification_pending';
  saved.source_items[0].status_label='Verification pending';
  saved.source_items[0].active_request_id=null;
  saved.action_requests[0].status='cancelled';
  saved.action_requests[0].execution_state='cancelled';
  saved.action_requests[0].can_cancel=false;
  saved.action_requests[0].can_execute=false;
  saved.attestations=[{id:'att-1',review_id:'r1',account_id:'a1',source_key:'source-key',evidence_fingerprint:'fingerprint',action:'edit',note:'Changed the full comment.',proposed_text:'My complete proposed text',result_url:'',original_excerpt:'Original <words>',source_url:saved.source_items[0].source_url,created_at:'2026-09-15T12:00:00Z',done_at:'2026-09-15T12:00:00Z',execution_state:'needs_verification',blocker_reason:'',next_move:'none'}];
  saved.source_items[0].attestation_ids=['att-1'];
  t.ui.state.fixDraft={sourceId:'source-1',action:'edit',proposedText:'My complete proposed text',note:'',resultUrl:''};
  t.setHandler((url)=>url==='/api/attestations'?response({attestation:saved.attestations[0],cancelled_request_ids:['q1'],running_request_ids:[]}):response(saved));
  const form={fields:{source_id:'source-1',review_id:'r1',action:'edit',note:'Changed the full comment.',result_url:''},reportValidity:()=>true,getAttribute:key=>key==='id'?'attestation-form':'',querySelector:()=>null};
  await t.ui.saveForm(form);
  assert.deepEqual(JSON.parse(t.calls.at(-2).body),{review_id:'r1',action:'edit',note:'Changed the full comment.',proposed_text:'My complete proposed text'});
  assert.ok(t.html().includes('Done by you'));
  assert.ok(t.html().includes('Verification pending'));
  assert.equal(t.storage.size,0,'successful attestation clears the recoverable session draft');
  const callsBeforeVerification=t.calls.length;
  t.ui.askAssistantDialog('source-1');
  assert.ok(t.html().includes('Verification already pending'));
  assert.ok(t.html().includes('Verification must finish'));
  assert.ok(!t.html().includes('Ask for an edit'),'unresolved attestation cannot open a new assistant request editor');
  assert.equal(t.calls.length,callsBeforeVerification,'verification routing does not attempt a mutation');
  assert.ok(t.html().includes('Fix now'),'verification detail keeps the self-service path explicit');

  saved.attestations[0].execution_state='blocked';
  saved.attestations[0].blocker_reason='Synthetic signed-in session is unavailable; open the exact source to restore access.';
  saved.attestations[0].next_move='open_source';
  t.ui.state.data=structuredClone(saved);
  const blockedAttestationCard=t.ui.sourceCard(saved.source_items[0]);
  assert.ok(blockedAttestationCard.includes('Synthetic signed-in session is unavailable; open the exact source to restore access.'));
  assert.ok(blockedAttestationCard.includes('Open source to unblock'));
  t.ui.sourceDetail('source-1');
  assert.ok(t.html().includes('Synthetic signed-in session is unavailable; open the exact source to restore access.'));
  assert.ok(t.html().includes('Open source to unblock'),'blocked user verification exposes its valid next move in source detail');
  t.ui.askAssistantDialog('source-1');
  assert.ok(t.html().includes('Open source to unblock'),'Ask assistant routes blocked user verification to the same recovery action');
  assert.ok(!t.html().includes('Ask for an edit'));

  const legacyHold=setup();
  legacyHold.ui.state.data.action_requests[0].confirmation_required=true;
  legacyHold.ui.state.data.action_requests[0].blocker_reason='Imported authority requires a fresh local confirmation.';
  legacyHold.ui.requestDetail('q1');
  assert.ok(legacyHold.html().includes('Imported authority requires a fresh local confirmation.'));
  assert.ok(legacyHold.html().includes('cannot run until you confirm it'));

  const snooze=setup();
  snooze.setHandler((url)=>url==='/api/state'?response(snooze.data):response({source_item:{...snooze.data.source_items[0],status:'snoozed'}}));
  await snooze.ui.updateSourceStatus({...element(),dataset:{source:'source-1',status:'snoozed'}});
  assert.deepEqual(JSON.parse(snooze.calls[0].body),{status:'snoozed'});

  const clean=setup();
  const cleanState=fixture();
  cleanState.reviews=[{...cleanState.reviews[0],id:'clean-r',title:'Clean checked post',status:'resolved'}];
  cleanState.policy_flags=[];
  cleanState.action_requests=[];
  Object.assign(cleanState.source_items[0],{review_ids:['clean-r'],review_id:'clean-r',title:'Clean checked post',findings:[],status:'checked',status_label:'Checked',needs_attention:false,request_ids:[],active_request_id:null});
  clean.setHandler(url=>url==='/api/excerpts'?response({review:{id:'clean-r'},flag_count:0}):response(cleanState));
  const cleanForm={fields:{account_id:'a1',kind:'post',text:'A clean post.',source_url:'https://example.test/post/clean',title:'Clean checked post'},reportValidity:()=>true,getAttribute:key=>key==='id'?'excerpt-form':'',querySelector:()=>null};
  await clean.ui.saveForm(cleanForm);
  assert.equal(clean.ui.state.filters.status,'history','a clean check selects the B1 History filter');
  assert.equal(clean.ui.state.filters.account,'a1');
  assert.ok(clean.html().includes('Checked locally; nothing matched in the saved scope.'),'the returned clean source opens with its checked result');
  assert.match(clean.get('#toast').textContent,/clean check is in History/);

  const flagged=setup();
  const flaggedState=fixture();
  flaggedState.reviews=[{...flaggedState.reviews[0],id:'flagged-r',title:'Flagged checked post'}];
  flaggedState.action_requests=[];
  Object.assign(flaggedState.source_items[0],{review_ids:['flagged-r'],review_id:'flagged-r',title:'Flagged checked post',status:'needs_you',status_label:'Needs you',needs_attention:true,request_ids:[],active_request_id:null});
  flaggedState.source_items[0].findings=[{...flaggedState.source_items[0].findings[0],review_ids:['flagged-r']}];
  flagged.setHandler(url=>url==='/api/excerpts'?response({review:{id:'flagged-r'},flag_count:1}):response(flaggedState));
  const flaggedForm={fields:{account_id:'a1',kind:'post',text:'A flagged post.',source_url:'https://example.test/post/flagged',title:'Flagged checked post'},reportValidity:()=>true,getAttribute:key=>key==='id'?'excerpt-form':'',querySelector:()=>null};
  await flagged.ui.saveForm(flaggedForm);
  assert.equal(flagged.ui.state.filters.status,'needs_you','a flagged check selects the authoritative Needs you status');
  assert.ok(flagged.html().includes('Finding 1'),'the returned flagged source opens with its finding');
  assert.match(flagged.get('#toast').textContent,/1 possible boundary issue/);

  // B3 — the saved text in full, the marked quotes, and the Modify/Delete routes.
  const marks=setup();
  const quoted=(excerpt,findings)=>({...marks.data.source_items[0],excerpt,findings});
  const plain=marks.ui.highlightedText(quoted('Original <words> & "more"',[{...marks.data.source_items[0].findings[0],quotes:[]}]));
  assert.equal(plain,'Original &lt;words&gt; &amp; &quot;more&quot;','a finding with no quotes leaves plain escaped text');
  const escaped=marks.ui.highlightedText(quoted('say <script>alert("x")</script> now',[{id:'f',title:'Risky <tag>',severity:'attention',quotes:['<script>alert("x")</script>']}]));
  assert.ok(escaped.includes('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;'),'quoted markup renders inert');
  assert.ok(!escaped.includes('<script>'),'no raw tag survives the highlight');
  assert.ok(escaped.includes('title="Risky &lt;tag&gt;"'),'the finding title is escaped in the mark');
  assert.match(escaped,/<mark class="flag-mark attention" data-action="source-detail" data-id="source-1" data-finding="0"/);
  const repeated=marks.ui.highlightedText(quoted('ham and ham and ham',[{id:'f',title:'Repeat',severity:'suggestion',quotes:['ham']}]));
  assert.equal((repeated.match(/<mark /g)||[]).length,3,'every occurrence of a quote is marked');
  const overlap=marks.ui.highlightedText(quoted('one two three',[
    {id:'f1',title:'Soft',severity:'suggestion',quotes:['one two']},
    {id:'f2',title:'Hard',severity:'attention',quotes:['two three']}]));
  assert.equal((overlap.match(/<mark /g)||[]).length,1,'overlapping quotes produce one range');
  assert.ok(overlap.includes('flag-mark attention')&&overlap.includes('>two three</mark>'),'the attention finding wins an overlap');
  assert.ok(overlap.startsWith('one ')&&!overlap.includes('>one two<'));
  const tie=marks.ui.highlightedText(quoted('alpha beta gamma',[
    {id:'f1',title:'First',severity:'suggestion',quotes:['alpha beta']},
    {id:'f2',title:'Second',severity:'suggestion',quotes:['beta gamma']}]));
  assert.ok(tie.includes('>alpha beta</mark>')&&!tie.includes('>beta gamma</mark>'),'without an attention finding the earlier range wins');
  const longText='A saved post that is far longer than the old preview. '.repeat(12);
  const fullCard=marks.ui.sourceCard({...marks.data.source_items[0],excerpt:longText,findings:[{id:'f',title:'Long',severity:'suggestion',quotes:['far longer']}]});
  assert.equal((fullCard.match(/than the old preview\./g)||[]).length,12,'the card prints the saved text in full rather than a trimmed preview');
  assert.ok(!fullCard.match(/excerpt-text[^>]*>[^<]*…/),'no ellipsis truncation remains in the saved excerpt');
  assert.ok(fullCard.includes('Saved excerpt · may be partial'));
  assert.match(fullCard,/<blockquote class="review-excerpt" tabindex="0" aria-label="[^"]+"/,'the text box is reachable and labelled for keyboard scrolling');
  assert.equal((fullCard.match(/<mark /g)||[]).length,12,'each occurrence in the full text is marked');

  const routes=setup();
  routes.ui.state.data.action_requests=[];
  routes.ui.state.data.source_items[0].active_request_id=null;
  routes.ui.state.data.source_items[0].request_ids=[];
  routes.ui.sourceDeleteDialog('source-1');
  assert.match(routes.html(),/data-action="fix-now-choice" data-source="source-1" data-fix-action="delete"/,'Delete it myself reuses the existing fix-now delete choice');
  assert.match(routes.html(),/data-action="assistant-choice" data-source="source-1" data-request-action="delete"/,'Ask my assistant to delete reuses the existing assistant delete choice');
  assert.ok(routes.html().includes('Ask my assistant to delete'));
  assert.equal(routes.calls.length,0,'the Delete chooser saves nothing');
  assert.ok(routes.html().includes('Queued for the assistant’s next check'),'the queued deletion names when it is expected to run');
  assert.ok(routes.html().includes('Deletion still needs your yes at the time it happens.'),'the queued deletion keeps its confirmation gate');
  assert.ok(routes.html().includes('never contacts the platform'));
  routes.ui.sourceModifyDialog('source-1');
  assert.ok(routes.html().includes('Ask my assistant to edit'));
  assert.ok(routes.html().includes('Some edits need your confirmation then.'),'a queued edit does not claim a gate it may not have');
  assert.ok(!routes.html().includes('Deletion still needs your yes'),'the modify routes make no deletion claim');
  assert.match(routes.html(),/data-action="fix-now-choice" data-source="source-1" data-fix-action="edit"/);
  assert.match(routes.html(),/data-action="assistant-choice" data-source="source-1" data-request-action="edit"/);
  assert.match(routes.html(),/data-action="source-retract" data-source="source-1"/);
  routes.ui.retractionDialog('source-1');
  assert.match(routes.html(),/data-retract-route="self"/);
  assert.match(routes.html(),/data-retract-route="assistant"/);

  routes.ui.startRetraction('source-1','self');
  assert.equal(routes.ui.state.fixDraft.proposedText,routes.ui.RETRACTIONS[0],'the self route lands in the fix-now editor prefilled');
  assert.ok(routes.html().includes('data-action="retraction-next" data-target="#fix-proposed-text"'));
  const fixField=routes.get('#fix-proposed-text');
  fixField.value=routes.ui.RETRACTIONS[0];
  routes.ui.cycleRetraction({dataset:{target:'#fix-proposed-text'}});
  assert.equal(fixField.value,routes.ui.RETRACTIONS[1],'Another one cycles to the next template');
  assert.equal(routes.ui.state.fixDraft.proposedText,routes.ui.RETRACTIONS[1],'the cycled template is kept in the recoverable draft');
  fixField.value='My own careful wording.';
  routes.ui.cycleRetraction({dataset:{target:'#fix-proposed-text'}});
  assert.equal(fixField.value,'My own careful wording.','cycling never clobbers wording the user typed');
  assert.match(routes.get('#toast').textContent,/own wording is kept/);
  fixField.value='';
  routes.ui.cycleRetraction({dataset:{target:'#fix-proposed-text'}});
  assert.equal(fixField.value,routes.ui.RETRACTIONS[2],'an empty box accepts the next template');

  routes.ui.startRetraction('source-1','assistant');
  assert.ok(routes.html().includes('Proposed replacement text'),'the assistant route lands in the existing edit-request editor');
  assert.ok(routes.html().includes(routes.ui.RETRACTIONS[0].slice(0,40)),'the edit request opens prefilled with a retraction');
  assert.ok(routes.html().includes('data-action="retraction-next" data-target="#proposed-text"'));
  routes.ui.state.retraction=null;
  routes.ui.editRequestDialog('r1');
  assert.ok(!routes.html().includes('data-action="retraction-next"'),'an ordinary edit request shows no retraction control');
  assert.ok(routes.html().includes('Original &lt;words&gt;'),'an ordinary edit request still starts from the saved excerpt');

  const guarded=setup();
  guarded.ui.sourceDeleteDialog('source-1');
  guarded.ui.assistantRequestDialog('source-1','delete');
  assert.ok(guarded.html().includes('already has active work'),'an active request still routes to its current state');
  assert.equal(guarded.calls.length,0,'no mutation is attempted from the new routes');

  const themed=setup();
  themed.ui.settingsDialog();
  for(const [value,theme] of Object.entries(themed.ui.THEMES)){
    assert.ok(themed.html().includes(`<option value="${value}"`),`Appearance offers ${value}`);
    assert.ok(themed.html().includes(theme.label),`Appearance names ${value} in plain words`);
  }
  assert.deepEqual(Object.keys(themed.ui.THEMES),['dark','light','system','phosphor','midnight']);

  console.log('B1 source UI renderer/handler checks passed (no browser or visual claim).');
})().catch(error=>{console.error(error);process.exitCode=1;});
