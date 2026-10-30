// Real manage.js, in-memory HTTP/DOM only. No GUI, screenshots or clipboard read.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const fixture = JSON.parse(readFileSync(0, 'utf8'));
const source = readFileSync(new URL('../assets/manage.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../assets/manage.html', import.meta.url), 'utf8');
const flush = async () => { for (let i=0; i<12; i++) await new Promise(resolve => setImmediate(resolve)); };
const checks = [];
async function harness(options={}) {
  const elements = new Map(), clipboard=[], downloads=[], blobs=new Map(), requests=[], timers=new Map(), events=new Map();
  let timer=0, doc;
  class Element {
    constructor(tag='div') { Object.assign(this,{tagName:tag,children:[],listeners:new Map(),attributes:new Map(),value:'',hidden:false,open:false,disabled:false,isConnected:true,checked:false}); this.classList={toggle(){}}; }
    set textContent(v) { this.text=String(v); this.children=[]; }
    get textContent() { return (this.text||'')+this.children.map(c=>c.textContent).join(''); }
    set innerHTML(v) { throw Error('unsafe HTML sink'); }
    get childElementCount() { return this.children.length; }
    append(...children) { for(const child of children) {child.parent=this;this.children.push(child);} }
    replaceChildren(...children) {this.text='';this.children=[];this.append(...children);}
    addEventListener(type,fn) {if(!this.listeners.has(type))this.listeners.set(type,[]);this.listeners.get(type).push(fn);}
    async emit(type,extra={}) { if(type==='click'&&this.disabled)return;const event={target:this,preventDefault(){this.prevented=true;},...extra};if(type==='click'&&this.onclick)await this.onclick(event);for(const fn of this.listeners.get(type)||[])await fn(event);await flush();return event; }
    click() {if(this.tagName==='a')downloads.push({name:this.download,blob:blobs.get(this.href)});}
    setAttribute(k,v){this.attributes.set(k,String(v));} getAttribute(k){return this.attributes.get(k)??null;}
    removeAttribute(k){this.attributes.delete(k);if(['src','href'].includes(k))this[k]='';}
    focus(){doc.activeElement=this;} select(){this.focus();this.selectionEnd=this.value.length;}
    setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}
    showModal(){this.open=true;} close(){this.open=false;for(const fn of this.listeners.get('close')||[])fn({target:this});}
    remove(){this.parent.children=this.parent.children.filter(c=>c!==this);}
    getClientRects(){return this.hidden?[]:[{}];} getBoundingClientRect(){return {left:0,right:500,top:0,bottom:500};}
    querySelector(selector){assert.equal(selector,'[aria-current="true"]');return this.children.find(c=>c.getAttribute('aria-current')==='true');}
    querySelectorAll(){return this.focusables||[];}
  }
  for(const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)){const e=new Element(match[1]);e.hidden=/\bhidden\b/.test(match[0]);elements.set(match[2],e);}
  const get=id=>{assert.ok(elements.has(id),'known HTML element '+id);return elements.get(id);};
  doc={body:new Element('body'),getElementById:get,createElement:tag=>new Element(tag),execCommand:()=>false,querySelector:()=>[...elements.values()].find(e=>e.open)};
  const location={hash:options.noFragment?'':'#FAKE_LAUNCH',pathname:'/',origin:'http://127.0.0.1:43123'};
  let state={schema:1,session_id:'FAKE_SESSION',generation:'a'.repeat(64),pending:{present:false},busy:false,writable:true,writable_reason:null,last_job:null,model:structuredClone(fixture.model)};
  if(options.readonly)Object.assign(state,{writable:false,writable_reason:'independent_carrier_required'});
  let mode='running', failPost=false, status=200;
  const fetch=async(path,request)=>{
    assert.equal(location.hash,'','fragment cleared before HTTP');
    requests.push({path,...request,body:request.body?JSON.parse(request.body):undefined});
    if(path==='/api/session')return {ok:true,json:async()=>({token:'FAKE_BEARER',expires_in:options.ttl||2700,session_id:'FAKE_SESSION'})};
    assert.equal(request.headers.Authorization,'Bearer FAKE_BEARER');
    if(path==='/api/state')return {ok:status===200,status,json:async()=>structuredClone(state)};
    if(request.method==='POST'){
      if(failPost)throw Error('lost response');
      state.last_job={job_id:'FAKE_JOB',request_id:requests.at(-1).body.request_id,state:'running'};
      return {ok:true,status:202,json:async()=>({job_id:'FAKE_JOB',state:'running'})};
    }
    return {ok:true,json:async()=>({job_id:'FAKE_JOB',state:mode==='running'?'running':'finished',elapsed_s:4,outcome:mode==='running'?undefined:mode})};
  };
  class LocalURL extends URL {static createObjectURL(blob){const id='blob:fake-'+blobs.size;blobs.set(id,blob);return id;}static revokeObjectURL(id){blobs.delete(id);}}
  const context={document:doc,navigator:{clipboard:{writeText:async value=>clipboard.push(value)}},URL:LocalURL,Blob,Uint8Array,Date,AbortController,crypto:webcrypto,location,history:{replaceState(a,b,path){assert.equal(path,'/');location.hash='';}},window:{addEventListener:(k,v)=>events.set(k,v)},fetch,atob:v=>Buffer.from(v,'base64').toString('binary'),setTimeout:(fn,delay)=>{timers.set(++timer,{fn,delay});return timer;},clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source,context,{filename:'manage.js'});await flush();
  return {get,clipboard,downloads,blobs,requests,context,events,get state(){return state;},set state(v){state=v;},set mode(v){mode=v;},set failPost(v){failPost=v;},set status(v){status=v;},async poll(){const entry=[...timers].find(([,t])=>t.delay===0||t.delay===2000);assert.ok(entry,'job timer exists');timers.delete(entry[0]);await entry[1].fn();await flush();}};
}
const h=await harness();
assert.equal(h.get('node-links').children.length,3);assert.equal(h.get('subscription-links').children.length,2);
assert.equal(h.requests.filter(r=>r.path==='/api/session').length,1);
assert.equal(h.requests[0].body.launch_token,'FAKE_LAUNCH');
assert.equal(h.get('add-device').disabled,false);
checks.push('fragment cleared before exchange; bearer-only authenticated reads; three nodes and two subscriptions');
for(const container of ['node-links','subscription-links']){
  const links=fixture.model.devices[0].links.filter(l=>l.kind===(container==='node-links'?'node':'subscription'));
  for(const [i,card] of h.get(container).children.entries()){
    assert.equal(card.children[1].textContent,links[i].label);
    await card.children[3].children[1].emit('click');assert.equal(h.clipboard.at(-1),links[i].uri);
    await card.children[3].children[0].emit('click');
    assert.deepEqual(Buffer.from(await h.blobs.get(h.get('qr-image').src).arrayBuffer()),Buffer.from(fixture.model.files[links[i].qr],'base64'));
    await h.get('qr-download').emit('click');assert.deepEqual(Buffer.from(await h.downloads.at(-1).blob.arrayBuffer()),Buffer.from(fixture.model.files[links[i].qr],'base64'));
    h.get('qr-dialog').close();assert.equal(h.get('qr-image').src,'');
  }
}
for(const [i,anchor] of h.get('config-downloads').children.entries()){
  await anchor.emit('click');assert.deepEqual(Buffer.from(await h.downloads.at(-1).blob.arrayBuffer()),Buffer.from(fixture.model.files[fixture.model.devices[0].downloads[i].path],'base64'));
}
checks.push('all native URI/QR/config bytes exact, including shared VLESS UUID, percent case and injection-like text');
h.context.navigator.clipboard.writeText=async()=>{throw Error('denied');};
await h.get('node-links').children[1].children[3].children[1].emit('click');assert.equal(h.get('copy-dialog').open,true);assert.equal(h.get('manual-copy').value,fixture.model.devices[0].links[1].uri);
h.get('copy-dialog').close();assert.equal(h.get('manual-copy').value,'');
const d=h.get('edit-dialog'), first=h.get('edit-name'), last=h.get('edit-submit');d.focusables=[first,last];last.focus();let ev=await d.emit('keydown',{key:'Tab'});assert.equal(ev.prevented,true);assert.equal(h.context.document.activeElement,first);
first.focus();ev=await d.emit('keydown',{key:'Tab',shiftKey:true});assert.equal(h.context.document.activeElement,last);
await h.get('add-device').emit('click');await h.get('edit-cancel').emit('click');assert.equal(d.open,false);
h.get('device-search').value='beta';await h.get('device-search').emit('input');assert.equal(h.get('device-list').children.length,1);
await h.get('device-list').children[0].emit('click');assert.equal(h.get('device-name').textContent,'beta');
h.get('device-search').value='';await h.get('device-search').emit('input');
checks.push('manual copy, modal Tab wrap, cancel, search and device switching');
await h.get('add-device').emit('click');h.get('edit-name').value='BAD';await h.get('edit-form').emit('submit');assert.ok(h.get('edit-error').textContent);assert.equal(h.requests.filter(r=>r.path==='/api/jobs').length,0);
h.get('edit-name').value='gamma';await h.get('edit-form').emit('submit');
const request=h.requests.find(r=>r.path==='/api/jobs').body;
assert.equal(request.action,'add');assert.deepEqual(request.names,['gamma']);assert.match(request.request_id,/^[a-f0-9]{32}$/);assert.equal(request.expected_generation,'a'.repeat(64));assert.equal(h.get('add-device').disabled,true);
h.mode='committed';h.state.model=structuredClone(fixture.model);h.state.model.devices[0].name='gamma';h.state.generation='b'.repeat(64);h.state.model.snapshot.backup_sha256=h.state.generation;h.state.last_job=null;
await h.poll();assert.equal(h.get('add-device').disabled,false);assert.match(h.get('job-status').textContent,/已提交/);assert.equal(h.get('backup-digest').title,'b'.repeat(64));
checks.push('validated add submits once with generation and nonce; terminal job refreshes new committed state');
for(const action of ['reset','remove']){
  const x=await harness();await x.get(action==='reset'?'reset-device':'remove-device').emit('click');
  await x.get('edit-form').emit('submit');assert.equal(x.requests.filter(r=>r.path==='/api/jobs').length,0);
  x.get('confirm-impact').checked=true;x.get('confirm-name').value='wrong';await x.get('edit-form').emit('submit');assert.equal(x.requests.filter(r=>r.path==='/api/jobs').length,0);
  x.get('confirm-name').value='alpha';await x.get('edit-form').emit('submit');const body=x.requests.find(r=>r.path==='/api/jobs').body;assert.deepEqual(body.confirm,{action,name:'alpha'});assert.equal(x.get('device-name').textContent,'alpha','no optimistic deletion');
}
checks.push('reset and delete require impact acknowledgement plus exact target; never optimistic deletion');
const renamed=await harness();await renamed.get('rename-device').emit('click');renamed.get('edit-name').value='renamed';await renamed.get('edit-form').emit('submit');assert.deepEqual(renamed.requests.find(r=>r.path==='/api/jobs').body.names,['alpha','renamed']);checks.push('rename uses whole native device action with old/new ordered names');
const p=await harness();await p.get('node-links').children[0].children[3].children[0].emit('click');p.state={...p.state,pending:{present:true,mode:'devices',operation_id:'ORIGINAL_OPERATION'},model:null,writable:false,writable_reason:'pending'};await p.get('refresh-state').emit('click');assert.equal(p.get('qr-image').src,'');assert.equal(p.get('node-links').childElementCount,0);assert.equal(p.get('qr-dialog').open,false);assert.equal(p.get('add-device').disabled,true);assert.equal(p.get('recover-operation').disabled,false);await p.get('recover-operation').emit('click');assert.equal(p.requests.at(-1).path,'/api/recover');assert.equal(p.requests.at(-1).body.expected_operation_id,'ORIGINAL_OPERATION');
checks.push('pending wipes credential DOM and QR; explicit recovery references original operation');
const l=await harness();l.failPost=true;await l.get('add-device').emit('click');l.get('edit-name').value='gamma';await l.get('edit-form').emit('submit');await l.get('refresh-state').emit('click');assert.equal(l.requests.filter(r=>r.path==='/api/jobs').length,1);assert.equal(l.get('add-device').disabled,true);assert.match(l.get('job-status').textContent,/响应丢失/);
checks.push('lost POST response is never retried and blocks new mutations until original job reconciliation');
const e=await harness();await e.get('add-device').emit('click');e.get('edit-name').value='gamma';await e.get('edit-form').emit('submit');e.mode='committed_export_failed';e.state.last_job=null;await e.poll();assert.match(e.get('job-status').textContent,/已经提交/);assert.equal(e.requests.filter(r=>r.path==='/api/jobs').length,1);
e.status=401;await e.get('refresh-state').emit('click');assert.equal(e.get('node-links').childElementCount,0);assert.equal(e.get('device-count').textContent,'—');assert.match(e.get('load-error').textContent,/会话/);
checks.push('postcommit export failure remains committed; expired session wipes secrets without fake empty roster');
const ro=await harness({readonly:true});assert.equal(ro.get('add-device').disabled,true);assert.equal(ro.get('node-links').children.length,3);
const short=await harness({ttl:1200});assert.equal(short.get('add-device').disabled,true);
const missing=await harness({noFragment:true});assert.equal(missing.requests.length,0);assert.match(missing.get('load-error').textContent,/vps\/index.html/);
checks.push('read-only preserves committed materials; short TTL prevents admission; F5 without fragment explains entry');

{ // Add: new generation's device appears only after refresh; export-failed is a commit, refreshed, never re-POSTed.
  const a=await harness();const before=a.get('device-list').children.length;
  await a.get('add-device').emit('click');a.get('edit-name').value='gamma';await a.get('edit-form').emit('submit');
  assert.equal(a.get('device-list').children.length,before,'no optimistic device before committed state');
  const next=structuredClone(fixture.model),clone=structuredClone(next.devices[0]);clone.name='gamma';next.devices.push(clone);
  a.state.model=next;a.state.generation='c'.repeat(64);next.snapshot.backup_sha256=a.state.generation;
  a.mode='committed_export_failed';a.state.last_job=null;const reads=a.requests.filter(r=>r.path==='/api/state').length;
  await a.poll();
  assert.equal(a.requests.filter(r=>r.path==='/api/jobs').length,1,'no re-POST');
  assert.ok(a.requests.filter(r=>r.path==='/api/state').length>reads,'state refetched after terminal job');
  assert.equal(a.get('device-list').children.length,before+1);assert.equal(a.get('backup-digest').title,'c'.repeat(64));
  assert.match(a.get('job-status').textContent,/已经提交/);assert.doesNotMatch(a.get('job-status').textContent,/回滚|未变更/);
  assert.equal(a.get('add-device').disabled,false,'new submission allowed after reconciliation');
  // QR/download bytes for the new device still come from model.files.
  const links=clone.links;assert.equal(links.filter(l=>l.kind==='node').length,3);assert.equal(links.filter(l=>l.kind==='subscription').length,2);
  await a.get('device-list').children[before].emit('click');assert.equal(a.get('node-links').children.length,3);assert.equal(a.get('subscription-links').children.length,2);
  const seen=[];for(const card of [...a.get('node-links').children,...a.get('subscription-links').children]){await card.children[3].children[1].emit('click');seen.push(a.clipboard.at(-1));}
  assert.deepEqual(seen,links.map(l=>l.uri),'five exact native URIs, shared UUID not deduplicated');
  checks.push('add: no optimistic device; export-failed shows committed, refetches, new device renders 3+2 exact URIs, no re-POST');
}
for(const mode of ['backup','future-mode']){ // pending identity is an opaque string the UI never interprets
  const q=await harness(),opaque='op/ id:'+mode+'-ü';
  q.state={...q.state,pending:{present:true,mode,operation_id:opaque},model:null,writable:false,writable_reason:'pending'};
  await q.get('refresh-state').emit('click');await q.get('recover-operation').emit('click');
  const r=q.requests.filter(x=>x.path==='/api/recover');assert.equal(r.length,1);assert.equal(r[0].body.expected_operation_id,opaque);assert.match(r[0].body.request_id,/^[a-f0-9]{32}$/);
}
checks.push('recover echoes backend-provided opaque operation_id verbatim for any pending mode');
console.log(JSON.stringify({status:'passed',checks}));
