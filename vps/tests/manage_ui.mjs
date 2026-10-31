// Real manage.js, in-memory HTTP/DOM only. No GUI, screenshots or clipboard read.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto, createHmac, createHash, randomBytes} from 'node:crypto';
const fixture = JSON.parse(readFileSync(0, 'utf8'));
const source = readFileSync(new URL('../assets/manage.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../assets/manage.html', import.meta.url), 'utf8');
const flush = async () => { for (let i=0; i<12; i++) { await new Promise(resolve => setTimeout(resolve, 2)); await new Promise(resolve => setImmediate(resolve)); } };
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
  elements.set('manage-bootstrap',new Element('script'));
  const get=id=>{assert.ok(elements.has(id),'known HTML element '+id);return elements.get(id);};
  doc={body:new Element('body'),getElementById:get,createElement:tag=>new Element(tag),execCommand:()=>false,querySelector:()=>[...elements.values()].find(e=>e.open)};
  const ORIGIN='http://127.0.0.1:43123', BOOT_HEX='5a'.repeat(32), B=Buffer.from(BOOT_HEX,'hex');
  const hm=(key,text)=>createHmac('sha256',key).update(text).digest();
  const sha=raw=>createHash('sha256').update(raw).digest('hex');
  const server={epoch:randomBytes(16).toString('hex'),sessions:new Map(),seen:new Set(),offline:false,badProof:false,tamper:false,impostor:false,fakeUnsigned:false,hellos:0};
  const location={hash:'',pathname:'/',origin:'file://'};
  let state={schema:1,service_mode:'persistent',carrier:{configured:true,listening:true},generation:'a'.repeat(64),pending:{present:false},busy:false,writable:true,writable_reason:null,last_job:null,model:structuredClone(fixture.model)};
  if(options.readonly)Object.assign(state,{writable:false,writable_reason:options.readonly,carrier:{configured:options.readonly!=='carrier_not_configured',listening:false}});
  let mode='running', failPost=false, jobExtra={};
  const FakeDate=options.now?class extends Date{constructor(...a){a.length?super(...a):super(options.now);}static now(){return options.now;}}:Date;
  const answer=(status,bytes,headers={})=>({ok:status>=200&&status<300,status,headers:{get:k=>headers[k]??null},arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.length)});
  const fetch=async(url,request)=>{
    if(server.offline)throw Error('connection refused');
    assert.ok(url.startsWith(ORIGIN),'only the exact bootstrap origin is contacted');const path=url.slice(ORIGIN.length);
    assert.equal(request.credentials,'omit');assert.equal(request.headers['Content-Type'],'application/json','JSON content type forces preflight');
    assert.ok(!Object.keys(request.headers).some(k=>/^(origin|authorization|cookie)$/i.test(k)),'no forbidden/ambient credential headers');
    const rawBody=request.body??'';
    if(path==='/api/hello'){
      server.hellos++;requests.push({path,method:'POST',body:JSON.parse(rawBody)});
      const hello=JSON.parse(rawBody);assert.deepEqual(Object.keys(hello).sort(),['client_nonce','version']);assert.match(hello.client_nonce,/^[0-9a-f]{32}$/);
      const ns=randomBytes(16).toString('hex'),sid=randomBytes(16).toString('hex'),epoch=server.epoch;
      const origin=server.impostor?'http://127.0.0.1:1':ORIGIN;
      const ctx=['1',origin,epoch,sid,hello.client_nonce,ns].join('\n');
      server.sessions.set(sid,hm(B,'qd-session-v1\n'+ctx));
      const key=server.badProof?Buffer.alloc(32,1):B;
      return answer(200,Buffer.from(JSON.stringify({version:1,origin,epoch,session_id:sid,client_nonce:hello.client_nonce,server_nonce:ns,proof:hm(key,'qd-server-v1\n'+ctx).toString('hex')})));
    }
    if(request.method==='POST')server.postAttempts=(server.postAttempts||0)+1;
    if(server.evictOnPost&&request.method==='POST')server.sessions.clear();
    const sid=request.headers['X-QD-Session'],ks=server.sessions.get(sid),nonce=request.headers['X-QD-Nonce'];
    if(!ks||server.fakeUnsigned)return answer(401,Buffer.from('{"error":"fake: all fine, state is []"}'));
    const want=hm(ks,['qd-request-v1',server.epoch,nonce,request.method,path,sha(Buffer.from(rawBody))].join('\n')).toString('hex');
    assert.equal(request.headers['X-QD-MAC'],want,'request MAC covers exact method/path/raw body');
    assert.ok(!server.seen.has(sid+nonce),'nonce never reused');server.seen.add(sid+nonce);
    const parsed=rawBody?JSON.parse(rawBody):undefined;requests.push({path,method:request.method,body:parsed,raw:rawBody,sid});
    let status=200,reply;
    if(path==='/api/state')reply=structuredClone(state);
    else if(request.method==='POST'){
      if(failPost){server.sent=(server.sent||0)+1;throw Error('lost response');}
      state.last_job={job_id:'FAKE_JOB',request_id:parsed.request_id,state:'running',...jobExtra};status=202;reply={job_id:'FAKE_JOB',state:'running'};
    }else reply={job_id:'FAKE_JOB',state:mode==='running'?'running':'finished',elapsed_s:4,outcome:mode==='running'?undefined:mode,...jobExtra};
    const bytes=Buffer.from(JSON.stringify(reply));
    const mac=hm(ks,['qd-response-v1',server.epoch,nonce,String(status),sha(bytes)].join('\n')).toString('hex');
    return answer(status,(server.tamper||(server.tamperPost&&request.method==='POST'))?Buffer.from(JSON.stringify({...reply,model:{devices:[]}})):bytes,{'X-QD-MAC':mac});
  };
  elements.get('manage-bootstrap').text=JSON.stringify({version:1,origin:ORIGIN,bootstrap_key:BOOT_HEX});
  class LocalURL extends URL {static createObjectURL(blob){const id='blob:fake-'+blobs.size;blobs.set(id,blob);return id;}static revokeObjectURL(id){blobs.delete(id);}}
  const context={document:doc,navigator:{clipboard:{writeText:async value=>clipboard.push(value)}},URL:LocalURL,Blob,Uint8Array,Date:FakeDate,AbortController,TextEncoder,TextDecoder,crypto:webcrypto,location,window:{addEventListener:(k,v)=>events.set(k,v)},fetch,atob:v=>Buffer.from(v,'base64').toString('binary'),setTimeout:(fn,delay)=>{timers.set(++timer,{fn,delay});return timer;},clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source,context,{filename:'manage.js'});await flush();
  return {get,clipboard,downloads,blobs,requests,context,events,server,get state(){return state;},set state(v){state=v;},set mode(v){mode=v;},set failPost(v){failPost=v;},set jobExtra(v){jobExtra=v;},async poll(){const entry=[...timers].find(([,t])=>t.delay===0||t.delay===2000);assert.ok(entry,'job timer exists');timers.delete(entry[0]);await entry[1].fn();await flush();}};
}
const h=await harness();
assert.equal(fixture.model.devices[0].links.filter(l=>l.kind==='node').length,3);assert.ok(fixture.model.devices[0].downloads.length>0,'input model still carries nodes and downloads');
for(const id of ['node-links','config-downloads','nodes-heading','downloads-heading'])assert.ok(!html.includes(`id="${id}"`),id+' removed from HTML');
assert.throws(()=>h.get('node-links'));assert.throws(()=>h.get('config-downloads'));
assert.doesNotMatch(html,/下载配置|原生节点|\.download-link/);assert.doesNotMatch(readFileSync(new URL('../assets/manage.js',import.meta.url),'utf8'),/node-links|config-downloads/);
assert.equal(h.get('subscription-links').children.length,2);
assert.equal(h.requests.filter(r=>r.path==='/api/hello').length,1);
assert.equal(h.requests[0].path,'/api/hello');assert.equal(h.requests[1].path,'/api/state','no API request before a verified hello');
assert.equal(h.get('add-device').disabled,false);
checks.push('hello proof verified before signed reads; MAC-bound requests; three nodes and two subscriptions');
{
  const links=fixture.model.devices[0].links.filter(l=>l.kind==='subscription');
  for(const [i,card] of h.get('subscription-links').children.entries()){
    assert.equal(card.children[1].textContent,links[i].label);
    await card.children[3].children[1].emit('click');assert.equal(h.clipboard.at(-1),links[i].uri);
    await card.children[3].children[0].emit('click');
    assert.deepEqual(Buffer.from(await h.blobs.get(h.get('qr-image').src).arrayBuffer()),Buffer.from(fixture.model.files[links[i].qr],'base64'));
    await h.get('qr-download').emit('click');assert.deepEqual(Buffer.from(await h.downloads.at(-1).blob.arrayBuffer()),Buffer.from(fixture.model.files[links[i].qr],'base64'));
    h.get('qr-dialog').close();assert.equal(h.get('qr-image').src,'');
  }
}
checks.push('two subscription URI/QR bytes exact; node/download sections absent, including shared VLESS UUID, percent case and injection-like text');
h.context.navigator.clipboard.writeText=async()=>{throw Error('denied');};
const sub0=fixture.model.devices[0].links.filter(l=>l.kind==='subscription')[0];await h.get('subscription-links').children[0].children[3].children[1].emit('click');assert.equal(h.get('copy-dialog').open,true);assert.equal(h.get('manual-copy').value,sub0.uri);
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
const p=await harness();await p.get('subscription-links').children[0].children[3].children[0].emit('click');p.state={...p.state,pending:{present:true,mode:'devices',operation_id:'ORIGINAL_OPERATION'},model:null,writable:false,writable_reason:'pending'};await p.get('refresh-state').emit('click');assert.equal(p.get('qr-image').src,'');assert.equal(p.get('subscription-links').childElementCount,0);assert.equal(p.get('qr-dialog').open,false);assert.equal(p.get('add-device').disabled,true);assert.equal(p.get('recover-operation').disabled,false);await p.get('recover-operation').emit('click');assert.equal(p.requests.at(-1).path,'/api/recover');assert.equal(p.requests.at(-1).body.expected_operation_id,'ORIGINAL_OPERATION');
checks.push('pending wipes credential DOM and QR; explicit recovery references original operation');
const l=await harness();l.failPost=true;await l.get('add-device').emit('click');l.get('edit-name').value='gamma';await l.get('edit-form').emit('submit');await l.get('refresh-state').emit('click');assert.equal(l.requests.filter(r=>r.path==='/api/jobs').length,1);assert.equal(l.get('add-device').disabled,true);assert.match(l.get('job-status').textContent,/响应丢失/);
checks.push('lost POST response is never retried and blocks new mutations until original job reconciliation');
const e=await harness();await e.get('add-device').emit('click');e.get('edit-name').value='gamma';await e.get('edit-form').emit('submit');e.mode='committed_export_failed';e.state.last_job=null;await e.poll();assert.match(e.get('job-status').textContent,/已经提交/);assert.equal(e.requests.filter(r=>r.path==='/api/jobs').length,1);
e.server.sessions.clear();e.server.epoch='f'.repeat(32);await e.get('refresh-state').emit('click');assert.equal(e.get('subscription-links').childElementCount,2,'silent re-hello after service restart restores view');assert.equal(e.server.hellos,3,'initial, per-mutation, and post-restart hello');assert.equal(e.requests.filter(r=>r.path==='/api/jobs').length,1);
checks.push('postcommit export failure remains committed; service restart silently re-authenticates with a fresh hello');
const ro=await harness({readonly:'carrier_not_configured'});assert.match(ro.get('write-reason').textContent,/尚未配置独立 SSH 通道/);assert.equal(ro.get('carrier-status').textContent,'独立 SSH 通道未配置');assert.equal(ro.get('session-status').textContent,'本机后台服务已连接');assert.equal(ro.get('add-device').disabled,true);assert.equal(ro.get('subscription-links').children.length,2);
const inv=await harness({readonly:'carrier_invalid'});assert.match(inv.get('carrier-status').textContent,/损坏或无法读取/);assert.doesNotMatch(inv.get('carrier-status').textContent,/未配置/);assert.equal(inv.get('subscription-links').children.length,2);assert.equal(inv.get('add-device').disabled,true);
const dir=await harness();dir.state={...dir.state,carrier:{configured:true,mode:'direct',listening:null}};await dir.get('refresh-state').emit('click');assert.equal(dir.get('carrier-status').textContent,'直接SSH已配置（远端连接尚未验证）');
const down=await harness({readonly:'carrier_listener_down'});assert.equal(down.get('carrier-status').textContent,'独立 SSH 通道已配置，但当前不可用');assert.equal(down.get('add-device').disabled,true);assert.equal(down.get('subscription-links').children.length,2);
checks.push('no carrier / listener down: service shown connected, subscriptions viewable, writes disabled with explicit reason');
checks.push('');

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
  await a.get('device-list').children[before].emit('click');assert.equal(a.get('subscription-links').children.length,2);
  const seen=[];for(const card of [...a.get('subscription-links').children]){await card.children[3].children[1].emit('click');seen.push(a.clipboard.at(-1));}
  assert.deepEqual(seen,links.filter(l=>l.kind==='subscription').map(l=>l.uri),'two exact subscription URLs');
  checks.push('add: no optimistic device; export-failed shows committed, refetches, new device renders 2 exact subscription URLs, no re-POST');
}
for(const mode of ['backup','future-mode']){ // pending identity is an opaque string the UI never interprets
  const q=await harness(),opaque='op/ id:'+mode+'-ü';
  q.state={...q.state,pending:{present:true,mode,operation_id:opaque},model:null,writable:false,writable_reason:'pending'};
  await q.get('refresh-state').emit('click');await q.get('recover-operation').emit('click');
  const r=q.requests.filter(x=>x.path==='/api/recover');assert.equal(r.length,1);assert.equal(r[0].body.expected_operation_id,opaque);assert.match(r[0].body.request_id,/^[a-f0-9]{32}$/);
}
checks.push('recover echoes backend-provided opaque operation_id verbatim for any pending mode');

{ // Job identity: bound by request_id to the submitted payload, never to the selected card.
  const r=await harness();await r.get('rename-device').emit('click');r.get('edit-name').value='renamed';await r.get('edit-form').emit('submit');
  await r.get('device-list').children[1].emit('click');assert.equal(r.get('device-name').textContent,'beta');
  await r.poll();assert.match(r.get('job-status').textContent,/^正在将 alpha 改名为 renamed… · 已用 4 秒（超时上限 20 分钟）/);assert.doesNotMatch(r.get('job-status').textContent,/beta/);
  r.mode='committed';r.state.last_job=null;await r.poll();assert.match(r.get('job-status').textContent,/^将 alpha 改名为 renamed：原生变更已提交/);
  assert.equal(r.requests.filter(x=>x.method==='POST'&&x.path==='/api/jobs').length,1,'no re-POST');
  const a=await harness();await a.get('add-device').emit('click');a.get('edit-name').value='test';await a.get('edit-form').emit('submit');await a.poll();
  assert.equal(a.get('job-status').textContent,'正在新增设备 test · 已用 4 秒（超时上限 20 分钟）。请勿重复提交，可随时查看任务状态。');
  // Failure to read state after submit never re-POSTs and keeps naming the operation.
  a.mode='rejected';a.state.last_job=null;await a.poll();assert.match(a.get('job-status').textContent,/^新增设备 test：被拒绝/);
  for(const [action,names,text] of [['reset',['beta'],'正在重置设备 beta 的凭据'],['remove',['beta'],'正在删除设备 beta'],['add',['x'],'正在新增设备 x'],['rename',['beta','b2'],'正在将 beta 改名为 b2…'],['recover',[],'正在恢复上次未完成操作']]){
    // After a page restart the backend's flat fields name the job; the page never submitted it.
    const q=await harness();q.state.last_job={job_id:'OLD_JOB',request_id:'f'.repeat(32),state:'running',action,names};q.jobExtra={action,names};
    await q.get('refresh-state').emit('click');await q.poll();assert.ok(q.get('job-status').textContent.startsWith(text+' · 已用'),q.get('job-status').textContent);
  }
  const old=await harness();old.state.last_job={job_id:'OLD_JOB',request_id:'e'.repeat(32),state:'running'};await old.get('refresh-state').emit('click');await old.poll();
  assert.ok(old.get('job-status').textContent.startsWith('历史操作（未记录类型）'));assert.doesNotMatch(old.get('job-status').textContent,/alpha|新增|改名|重置|删除/);
  const bad=await harness();bad.state.last_job={job_id:'BAD',request_id:'d'.repeat(32),state:'running',action:'drop-all',names:['<b>']};bad.jobExtra={action:'drop-all',names:['<b>']};await bad.get('refresh-state').emit('click');await bad.poll();
  assert.ok(bad.get('job-status').textContent.startsWith('历史操作（未记录类型）'),'invalid backend fields are not invented into a label');
  checks.push('job status names action and target from request_id-bound payload or backend fields; legacy/invalid shows 历史操作（未记录类型）; no re-POST');
}
{ // Stale session at submit time: signed GET preflight re-hellos, then the POST is sent exactly once.
  const x=await harness();await x.get('reset-device').emit('click');x.get('confirm-impact').checked=true;x.get('confirm-name').value='alpha';
  x.server.sessions.clear();x.server.epoch='e'.repeat(32);const hellos=x.server.hellos;
  await x.get('edit-form').emit('submit');
  assert.equal(x.server.hellos,hellos+1,'preflight reauthenticated');
  assert.equal(x.requests.filter(r=>r.path==='/api/jobs').length,1,'POST exactly once');
  const idx=x.requests.findLastIndex(r=>r.path==='/api/jobs');assert.equal(x.requests[idx-1].path,'/api/state','signed GET precedes the POST');
  assert.equal(x.get('edit-dialog').open,false);assert.match(x.get('job-status').textContent,/重置设备 alpha/);
  await x.poll();x.mode='committed';x.state.last_job=null;await x.poll();assert.equal(x.get('add-device').disabled,false,'not stuck unresolved');
  // Every mutation POST is preceded by a fresh hello (new SID) and a signed state GET, even if the old SID is still valid.
  const w=await harness();const oldSid=[...w.server.sessions.keys()][0];await w.get('rename-device').emit('click');w.get('edit-name').value='renamed';await w.get('edit-form').emit('submit');
  const trace=w.requests.map(r=>r.path),pi=trace.lastIndexOf('/api/jobs');
  assert.deepEqual(trace.slice(pi-2,pi+1),['/api/hello','/api/state','/api/jobs']);assert.equal(w.server.hellos,2);assert.ok(w.server.sessions.has(oldSid),'old SID still valid');
  assert.notEqual(w.requests[pi].sid,oldSid);
  const pr=await harness();pr.state={...pr.state,pending:{present:true,mode:'devices',operation_id:'OP'},model:null,writable:false,writable_reason:'pending'};await pr.get('refresh-state').emit('click');
  await pr.get('recover-operation').emit('click');const tr=pr.requests.map(r=>r.path),ri=tr.lastIndexOf('/api/recover');assert.deepEqual(tr.slice(ri-2,ri+1),['/api/hello','/api/state','/api/recover']);
  // Offline preflight keeps the form, sends nothing, leaves nothing unresolved; later retry succeeds.
  const o=await harness();await o.get('add-device').emit('click');o.get('edit-name').value='gamma';o.server.offline=true;await o.get('edit-form').emit('submit');
  assert.equal(o.get('edit-dialog').open,true);assert.equal(o.get('edit-name').value,'gamma');assert.match(o.get('edit-error').textContent,/未发送/);assert.equal(o.requests.filter(r=>r.path==='/api/jobs').length,0);
  o.server.offline=false;await o.get('edit-form').emit('submit');assert.equal(o.requests.filter(r=>r.path==='/api/jobs').length,1);
  // Stale generation found by preflight: no POST, dialog retained.
  const g=await harness();await g.get('add-device').emit('click');g.get('edit-name').value='gamma';g.state.generation='9'.repeat(64);
  await g.get('edit-form').emit('submit');assert.equal(g.get('edit-dialog').open,true);assert.match(g.get('edit-error').textContent,/已变化/);assert.equal(g.requests.filter(r=>r.path==='/api/jobs').length,0);
  checks.push('submit preflights with a signed GET (re-hello on stale session), POSTs once; offline/stale/busy preflight keeps the form and sends nothing');
}
{ // Persistent service: no page expiry, no admission cutoff, no countdown text.
  const now=Date.UTC(2026,9,30,9,0,0)+3*3600e3,t=await harness({now});
  assert.equal(t.get('add-device').disabled,false,'writes admitted regardless of page age');
  const text=t.get('time-note').textContent;assert.doesNotMatch(text+html+source,/45 分钟|1230|剩余|expires_in|pagehide|sessionStorage|localStorage|document\.cookie|launch_token|请保持终端|关闭启动终端|\.hash\b/);
  assert.match(text,/常驻运行/);
  checks.push('no 45-minute/1230 s gate, countdown, fragment, pagehide or storage in the source; writes admitted');
}
{ // Identity failures never become an empty roster; mutations are sent once.
  const o=await harness();o.server.offline=true;await o.get('refresh-state').emit('click');
  assert.match(o.get('load-error').textContent,/后台服务未运行或身份无法验证/);assert.equal(o.get('device-count').textContent,'—');assert.equal(o.get('subscription-links').childElementCount,0);assert.equal(o.get('add-device').disabled,true);
  o.server.offline=false;await o.get('refresh-state').emit('click');assert.equal(o.get('load-error').hidden,true);assert.equal(o.get('subscription-links').children.length,2);
  const p=await harness({});p.server.badProof=true;p.server.sessions.clear();await p.get('refresh-state').emit('click');
  assert.match(p.get('load-error').textContent,/身份/);assert.equal(p.get('subscription-links').childElementCount,0);assert.equal(p.requests.at(-1).path,'/api/hello','nothing signed is sent to an impostor');
  const imp=await harness();imp.server.impostor=true;await imp.get('refresh-state').emit('click');assert.match(imp.get('load-error').textContent,/身份/);assert.ok(!imp.requests.slice(2).some(r=>r.path==='/api/state'),'wrong origin in hello rejected before API');
  const u=await harness();u.server.fakeUnsigned=true;u.server.sessions.clear();await u.get('refresh-state').emit('click');
  assert.ok(!u.get('load-error').textContent.includes('state is []'));assert.equal(u.get('subscription-links').childElementCount,0);
  const t=await harness();t.server.tamper=true;await t.get('refresh-state').emit('click');
  assert.equal(t.get('subscription-links').childElementCount,0,'response with invalid MAC is never shown');assert.match(t.get('load-error').textContent,/身份/);
  // Mutation with tampered response: not committed, no replay, original request awaits reconciliation.
  const m=await harness();m.server.tamperPost=true;await m.get('add-device').emit('click');m.get('edit-name').value='gamma';await m.get('edit-form').emit('submit');
  assert.equal(m.requests.filter(r=>r.path==='/api/jobs').length,1);assert.doesNotMatch(m.get('job-status').textContent,/原生变更已提交/);assert.equal(m.get('add-device').disabled,true);
  // Session lost between preflight and POST: exactly one attempt, never replayed, left unresolved.
  const r=await harness();r.server.evictOnPost=true;await r.get('add-device').emit('click');r.get('edit-name').value='gamma';await r.get('edit-form').emit('submit');
  assert.equal(r.server.postAttempts,1,'unknown-session POST never replayed');assert.equal(r.get('add-device').disabled,true,'blocked until original request is reconciled');
  // Preflight itself rejects a tampered state response: nothing is sent and the form stays.
  const f=await harness();f.server.tamper=true;await f.get('add-device').emit('click');f.get('edit-name').value='gamma';await f.get('edit-form').emit('submit');
  assert.equal(f.server.postAttempts||0,0);assert.equal(f.get('edit-dialog').open,true);
  checks.push('offline/impostor/unsigned/tampered responses show service-or-identity error, never an empty roster; mutations are never replayed; reconnect = fresh hello + GET');
}
{ // Import-option copy; QR/copy payloads (checked above) remain the same URI/URL.
  const c=await harness();
  assert.match(html,/包含本设备全部服务器入口，按客户端选一个，无需全部导入/);assert.doesNotMatch(html,/只导入一个入口|3 入口|5 条|五/);
  const subText=[...c.get('subscription-links').children].map(x=>x.children[2].textContent);
  assert.match(subText[0],/^Clash Verge 等 Mihomo 客户端/);assert.match(subText[1],/^Shadowrocket 客户端/);assert.ok(subText.every(t=>t.includes('同一个订阅 URL')));
  await c.get('subscription-links').children[0].children[3].children[0].emit('click');assert.match(c.get('qr-description').textContent,/同一条订阅 URL/);
  checks.push('subscription copy says all server entrances; no single-node or file-format controls');
}
{ // Foreground manage.sh: terminal lifetime copy, reconnect hint, direct-SSH truth, delete semantics unchanged.
  const f=await harness();f.state={...f.state,service_mode:'foreground',carrier:{configured:true,mode:'direct',listening:null}};await f.get('refresh-state').emit('click');
  const note=f.get('time-note').textContent;
  assert.match(note,/保持 manage\.sh 终端运行；关闭页面不会停止脚本；关闭终端或 Ctrl\+C 会停止管理页面，现有节点凭据不受影响；本页无固定到期，单项操作最长 20 分钟/);assert.doesNotMatch(note,/常驻|后台服务常驻|关闭终端或页面不会停止它/);
  assert.match(f.get('carrier-status').textContent,/修改会重启核心，独立性由操作者负责/);assert.doesNotMatch(f.get('carrier-status').textContent,/已通过SSH读取|已验证连接/);
  assert.equal(f.get('subscription-links').children.length,2);
  f.server.offline=true;await f.get('refresh-state').emit('click');
  assert.match(f.get('time-note').textContent,/重新运行 manage\.sh，再刷新\/重开页面/);assert.doesNotMatch(f.get('time-note').textContent,/后台服务启动/);
  f.server.offline=false;await f.get('refresh-state').emit('click');
  const unknown=await harness();unknown.server.offline=true;await unknown.get('refresh-state').emit('click');assert.match(unknown.get('time-note').textContent,/后台服务启动后点击/,'persistent legacy backend keeps its hint');delete unknown.state.service_mode;unknown.server.offline=false;await unknown.get('refresh-state').emit('click');unknown.server.offline=true;await unknown.get('refresh-state').emit('click');assert.match(unknown.get('time-note').textContent,/重新运行 manage\.sh/,'no service_mode defaults to foreground');
  for(const [action,id,label,desc,btn] of [['reset','reset-device','重置设备凭据',/轮换 UUID \/ Auth \/ SubID，旧导入配置和订阅将失效/,'确认撤销旧凭据并提交'],['remove','remove-device','删除整个设备',/撤销整个设备的全部入口和订阅，不是只隐藏卡片/,'确认撤销旧凭据并提交']]){
    await f.get(id).emit('click');assert.equal(f.get('edit-title').textContent,label);assert.match(f.get('edit-description').textContent,desc);assert.equal(f.get('edit-submit').textContent,btn);assert.equal(f.get('edit-name').hidden,true);
    f.get('edit-cancel').emit('click');await flush();
  }
  const n=f.requests.length;await f.get('remove-device').emit('click');f.get('confirm-impact').checked=true;f.get('confirm-name').value='alpha';await f.get('edit-form').emit('submit');
  const tail=f.requests.slice(n).map(r=>r.path);assert.deepEqual(tail,['/api/hello','/api/state','/api/jobs']);assert.equal(f.requests.at(-1).body.action,'remove');assert.deepEqual(f.requests.at(-1).body.confirm,{action:'remove',name:'alpha'});
  checks.push('foreground service_mode: terminal-lifetime copy, manage.sh restart hint (also before first connect), direct-SSH claim without read proof; reset/remove copy and hello→state→single POST unchanged; persistent legacy text kept');
}
console.log(JSON.stringify({status:'passed',checks}));
