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
  let mode='running', failPost=false, status=200, jobExtra={};
  const FakeDate=options.now?class extends Date{constructor(...a){a.length?super(...a):super(options.now);}static now(){return options.now;}}:Date;
  const fetch=async(path,request)=>{
    assert.equal(location.hash,'','fragment cleared before HTTP');
    requests.push({path,...request,body:request.body?JSON.parse(request.body):undefined});
    if(path==='/api/session')return {ok:true,json:async()=>({token:'FAKE_BEARER',expires_in:options.ttl||2700,session_id:'FAKE_SESSION'})};
    assert.equal(request.headers.Authorization,'Bearer FAKE_BEARER');
    if(path==='/api/state')return {ok:status===200,status,json:async()=>structuredClone(state)};
    if(request.method==='POST'){
      if(failPost)throw Error('lost response');
      state.last_job={job_id:'FAKE_JOB',request_id:requests.at(-1).body.request_id,state:'running',...jobExtra};
      return {ok:true,status:202,json:async()=>({job_id:'FAKE_JOB',state:'running'})};
    }
    return {ok:true,json:async()=>({job_id:'FAKE_JOB',state:mode==='running'?'running':'finished',elapsed_s:4,outcome:mode==='running'?undefined:mode,...jobExtra})};
  };
  class LocalURL extends URL {static createObjectURL(blob){const id='blob:fake-'+blobs.size;blobs.set(id,blob);return id;}static revokeObjectURL(id){blobs.delete(id);}}
  const context={document:doc,navigator:{clipboard:{writeText:async value=>clipboard.push(value)}},URL:LocalURL,Blob,Uint8Array,Date:FakeDate,AbortController,crypto:webcrypto,location,history:{replaceState(a,b,path){assert.equal(path,'/');location.hash='';}},window:{addEventListener:(k,v)=>events.set(k,v)},fetch,atob:v=>Buffer.from(v,'base64').toString('binary'),setTimeout:(fn,delay)=>{timers.set(++timer,{fn,delay});return timer;},clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source,context,{filename:'manage.js'});await flush();
  return {get,clipboard,downloads,blobs,requests,context,events,get state(){return state;},set state(v){state=v;},set mode(v){mode=v;},set failPost(v){failPost=v;},set jobExtra(v){jobExtra=v;},set status(v){status=v;},async poll(){const entry=[...timers].find(([,t])=>t.delay===0||t.delay===2000);assert.ok(entry,'job timer exists');timers.delete(entry[0]);await entry[1].fn();await flush();}};
}
const h=await harness();
assert.equal(fixture.model.devices[0].links.filter(l=>l.kind==='node').length,3);assert.ok(fixture.model.devices[0].downloads.length>0,'input model still carries nodes and downloads');
for(const id of ['node-links','config-downloads','nodes-heading','downloads-heading'])assert.ok(!html.includes(`id="${id}"`),id+' removed from HTML');
assert.throws(()=>h.get('node-links'));assert.throws(()=>h.get('config-downloads'));
assert.doesNotMatch(html,/下载配置|原生节点|\.download-link/);assert.doesNotMatch(readFileSync(new URL('../assets/manage.js',import.meta.url),'utf8'),/node-links|config-downloads/);
assert.equal(h.get('subscription-links').children.length,2);
assert.equal(h.requests.filter(r=>r.path==='/api/session').length,1);
assert.equal(h.requests[0].body.launch_token,'FAKE_LAUNCH');
assert.equal(h.get('add-device').disabled,false);
checks.push('fragment cleared before exchange; bearer-only authenticated reads; three nodes and two subscriptions');
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
e.status=401;await e.get('refresh-state').emit('click');assert.equal(e.get('subscription-links').childElementCount,0);assert.equal(e.get('device-count').textContent,'—');assert.match(e.get('load-error').textContent,/会话/);
checks.push('postcommit export failure remains committed; expired session wipes secrets without fake empty roster');
const ro=await harness({readonly:true});assert.equal(ro.get('add-device').disabled,true);assert.equal(ro.get('subscription-links').children.length,2);
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
  assert.equal(a.get('job-status').textContent,'正在新增设备 test · 已用 4 秒（超时上限 20 分钟）。请勿关闭启动终端或重复提交。');
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
{ // Exact deadlines at a fixed clock; 1230 s admission boundary; node validity never claimed.
  const hm=ms=>{const d=new Date(ms);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');};
  const now=Date.UTC(2026,9,30,9,0,0),t=await harness({now});
  const text=t.get('time-note').textContent;
  assert.ok(text.includes(`新增/改名/重置/删除可提交至 ${hm(now+1470000)}；管理页会话于 ${hm(now+2700000)} 结束`),text);
  assert.ok(text.includes('20 分钟是单项操作的超时上限，不代表需要等 20 分钟'));assert.ok(text.includes('会话结束不影响节点/已导入配置使用'));
  assert.doesNotMatch(text,/45 分钟|有效期|节点.*过期/);
  const late=await harness({now,ttl:1200});const lt=late.get('time-note').textContent;
  assert.ok(lt.startsWith(`已停止接收新操作，已提交任务继续执行；仍可查看/复制链接，至 ${hm(now+1200000)}。`),lt);
  assert.equal(late.get('add-device').disabled,true);assert.equal(late.get('subscription-links').children.length,2,'links stay visible');
  assert.equal(late.get('write-reason').textContent,`已停止接收新操作，已提交任务继续执行；仍可查看/复制链接，至 ${hm(now+1200000)}。`);
  checks.push('time copy shows exact submit/session deadlines, timeout-cap explanation and post-cutoff read-only wording');
}
{ // Import-option copy; QR/copy payloads (checked above) remain the same URI/URL.
  const c=await harness();
  assert.match(html,/包含本设备全部服务器入口，按客户端选一个，无需全部导入/);assert.doesNotMatch(html,/只导入一个入口|3 入口|5 条|五/);
  const subText=[...c.get('subscription-links').children].map(x=>x.children[2].textContent);
  assert.match(subText[0],/^Clash Verge 等 Mihomo 客户端/);assert.match(subText[1],/^Shadowrocket 客户端/);assert.ok(subText.every(t=>t.includes('同一个订阅 URL')));
  await c.get('subscription-links').children[0].children[3].children[0].emit('click');assert.match(c.get('qr-description').textContent,/同一条订阅 URL/);
  checks.push('subscription copy says all server entrances; no single-node or file-format controls');
}
console.log(JSON.stringify({status:'passed',checks}));
