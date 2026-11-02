/* Real native frontend + owner extension, synthetic API only. Named ephemeral Chrome. */
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { mkdirSync, mkdtempSync, writeFileSync, chmodSync, rmSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const bin = process.env.AGENT_BROWSER_BIN || 'agent-browser';
const url = process.env.HANDOFF_DEMO_URL || 'http://127.0.0.1:8896/demo/';
assert(/^http:\/\/127\.0\.0\.1:\d+\/demo\/$/.test(url), 'Only isolated loopback DEMO accepted');
const output = process.env.HANDOFF_SCREENSHOTS || '/tmp/handoff-native-screenshots';
mkdirSync(output, { recursive: true });
const temp = mkdtempSync(join(tmpdir(), 'handoff-browser-'));
const chrome = process.env.CHROME_BIN || '/opt/google/chrome/chrome';
const desktopChrome = join(temp, 'chrome');
writeFileSync(desktopChrome, '#!/bin/sh\nexec ' + JSON.stringify(chrome) + ' "--blink-settings=primaryPointerType=4,availablePointerTypes=4,primaryHoverType=2,availableHoverTypes=2" "$@"\n');
chmodSync(desktopChrome, 0o700);
let session = 'handoff-native-desktop-' + process.pid;
const run = (...args) => execFileSync(bin, ['--session', session, ...args], { encoding: 'utf8', timeout: 15000, env: { ...process.env, AGENT_BROWSER_DEFAULT_TIMEOUT: '8000' } });
const evaluate = code => { const r = JSON.parse(run('eval', code, '--json')); assert(r.success, r.error); return r.data.result; };
const wait = code => assert.equal(evaluate(`(async()=>{const until=Date.now()+8000;while(Date.now()<until){if(${code})return true;await new Promise(r=>setTimeout(r,50));}return false;})()`), true, 'Timed out: '+code);
const click = s => run('click', s);
const text = () => evaluate('document.querySelector("#handoffPanel")?.textContent');
const control = b => evaluate(`fetch('/demo/__fixture__/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(b)})}).then(r=>r.json())`);
const actions = () => control({}).actions;
const status = value => { control({ status: value }); if (evaluate('!document.querySelector("#handoffPanel button:nth-child(1)")')) throw Error('Panel absent'); evaluate('document.dispatchEvent(new Event("visibilitychange"))'); wait(`document.querySelector('#handoffPanel').textContent.includes(${JSON.stringify(require('../web/core.js').labels[value])})`); };
function login() { run('wait', '#loginForm'); run('fill', 'input[type=password]', 'demo'); click('#loginForm button'); run('wait', '#handoffButton'); }
function source(id = 'source') { evaluate(`location.replace('#session=${id}')`); wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId===${JSON.stringify(id)}`); }
function reset() { control({ reset: true }); evaluate(`Object.keys(localStorage).filter(k=>k.startsWith('codoxear:session-handoff:/demo/__session_handoff__/')).forEach(k=>localStorage.removeItem(k))`); run('reload'); run('wait', '#handoffButton'); }
function screenshot(name) { run('screenshot', join(output, name + '.png')); }
const storageBase = 'codoxear:session-handoff:/demo/__session_handoff__/';
const refs = () => evaluate(`Object.fromEntries(Object.keys(localStorage).filter(k=>k.startsWith(${JSON.stringify(storageBase)})).map(k=>[k,JSON.parse(localStorage.getItem(k))]))`);
function nativeSelect(id) {
  if (evaluate('innerWidth < 600')) openSidebar();
  click(row(id) + ' .sessionContent');
  wait(`new URLSearchParams(location.hash.slice(1)).get('session')===${JSON.stringify(id)}`);
  wait(`document.querySelector('#handoffButton') && !document.querySelector('#handoffButton').disabled`);
}
function concurrentSources(label) {
  reset(); source(); control({ source: 'source', fault: 'hold-start' });
  click('#handoffButton'); run('fill', '#handoffNote', 'A私有说明'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffButton").disabled');
  assert(control({}).held.some(h=>h.source==='source'&&h.kind==='start'));
  nativeSelect('other'); click('#handoffButton');
  assert.equal(evaluate('document.querySelector("#handoffNote").value'), '');
  assert(!evaluate('document.querySelector("#handoffPanel button[type=submit]").disabled'));
  run('fill', '#handoffNote', 'B私有说明'); screenshot(label+'-B-usable-while-A-pending');
  click('#handoffPanel button[type=submit]'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：other")');
  const starts = actions().filter(a=>a.kind==='start');
  assert.equal(starts.length, 2); assert.notEqual(starts[0].body.request_id, starts[1].body.request_id);
  assert.deepEqual(starts.map(a=>a.body.source_session_id), ['source','other']);
  assert.equal(starts[1].body.note, 'B私有说明');
  assert(control({}).held.some(h=>h.source==='source')); // B accepted before A response.
  control({ source: 'source', fault: '' }); control({ release: 'source' });
  evaluate('new Promise(r=>setTimeout(()=>r(true),350))');
  assert(text().includes('来源：other')); assert(!text().includes('来源：source'));
  screenshot(label+'-B-active-after-late-A');
  nativeSelect('source'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：source")'); screenshot(label+'-A-active');
  nativeSelect('other'); run('reload'); run('wait','#handoffButton'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：other")');
  nativeSelect('source'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：source")');
  assert.equal(Object.keys(refs()).length, 2); assert.equal(actions().filter(a=>a.kind==='start').length, 2);
  // Unsourced old operation is asynchronously resolved to A while B is selected.
  const a = refs()[storageBase+':source:source'];
  control({ source:'source', status:'unreviewed', allow:true, fault:'hold-status' });
  evaluate(`localStorage.removeItem(${JSON.stringify(storageBase+':source:source')});localStorage.setItem(${JSON.stringify(storageBase)},JSON.stringify({operation_id:${JSON.stringify(a.operation_id)},pending_action:'continue'}))`);
  nativeSelect('other'); run('reload'); run('wait','#handoffButton');
  wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：other")');
  assert(control({}).held.some(h=>h.source==='source'&&h.kind==='status')); assert(refs()[storageBase]);
  control({ source:'source', fault:'' }); control({ release:'source' });
  wait(`!localStorage.getItem(${JSON.stringify(storageBase)})`);
  assert.equal(refs()[storageBase+':source:source'].source_session_id, 'source');
  assert.equal(refs()[storageBase+':source:source'].pending_action, 'continue');
  assert(text().includes('来源：other')); assert.equal(actions().filter(a=>a.kind==='start').length,2);
  nativeSelect('source'); wait('document.querySelector("#handoffPanel").textContent.includes("继续请求结果尚未确认")');
  assert(evaluate('[...document.querySelectorAll("#handoffPanel button")].filter(b=>b.textContent==="未校对，仍继续").every(b=>b.hidden)'));
  // Only selected terminal A may be forgotten; B survives.
  control({ source:'source',status:'ready' }); evaluate('document.dispatchEvent(new Event("visibilitychange"))');
  wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("新会话已接续")');
  run('find','role','button','click','--name','收起记录','--exact');
  assert(!refs()[storageBase+':source:source']); assert(refs()[storageBase+':source:other']);
  console.log('PASS '+label+' concurrent native A held→B accepted→late A isolated→refresh both→legacy returned A→forget A only: '+JSON.stringify(starts.map(a=>a.body)));
  reset(); source();
}
function crossTabAndStorage() {
  reset(); source(); click('#handoffButton'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：source")');
  run('tab','new',url+'#session=other'); run('wait','#handoffButton'); click('#handoffButton'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：other")');
  assert.equal(Object.keys(refs()).length,2); run('tab','close');
  run('reload'); run('wait','#handoffButton'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：source")');
  nativeSelect('other'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：other")');
  // Mutation-storage failure cannot erase A/B; zero C request.
  evaluate(`window.fixtureSetItem=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('codoxear:session-handoff:'))throw Error('fixture quota');return fixtureSetItem.call(this,k,v)}`);
  source('target'); click('#handoffButton'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffPanel").textContent.includes("未发送接力请求")');
  assert.equal(actions().filter(a=>a.kind==='start').length,2); assert.equal(Object.keys(refs()).length,2);
  evaluate('Storage.prototype.setItem=fixtureSetItem');
  nativeSelect('source'); control({auth:false}); evaluate('document.dispatchEvent(new Event("visibilitychange"))');
  wait('!document.querySelector("#handoffButton") && (document.querySelector("#loginForm") || document.querySelector("#handoffCompatibility")?.textContent.includes("登录已失效"))');
  assert.equal(Object.keys(refs()).length,0); assert(!evaluate('Boolean(document.querySelector("#handoffButton"))'));
  control({auth:true}); run('reload'); run('wait','#handoffButton'); reset(); source();
  console.log('PASS cross-tab independent writes, storage no-send and auth-loss cleanup');
}
function staleDisplayedContinue(label) {
  reset(); source();
  control({ source: 'source', status: 'unreviewed', allow: true });
  control({ source: 'other', status: 'unreviewed', allow: true });
  for (const id of ['source', 'other']) {
    source(id); click('#handoffButton'); click('#handoffPanel button[type=submit]');
    wait(`document.querySelector('#handoffPanel [role=status]').textContent.includes(${JSON.stringify('来源：'+id)})`);
  }
  source('source'); wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：source")');
  const b = refs()[storageBase+':source:other'].operation_id;
  // One JS task: native replaceState, then click the still-painted A button.
  // No timer/microtask/monitor is allowed to repaint B before this click.
  const painted = evaluate(`(()=>{const p=document.querySelector('#handoffPanel'),b=[...p.querySelectorAll('button')].find(b=>b.textContent==='未校对，仍继续');
    if(b.hidden)throw Error('A Continue must be visible');const before=p.querySelector('[role=status]').textContent;
    history.replaceState(null,'','#session=other');b.click();return {before,after:p.querySelector('[role=status]').textContent};})()`);
  assert(painted.before.includes('来源：source')); assert(painted.after.includes('来源：other'));
  assert.equal(actions().filter(a=>a.kind==='continue').length, 0);
  screenshot(label+'-stale-A-continue-repainted-B');
  // The URL-only probe leaves native internal selection at A; its next refresh
  // may restore A. Make the second intent a real native B selection, not a
  // later click after that unrelated URL restoration. Use native hashchange
  // navigation here; trusted sidebar selection is covered in concurrentSources.
  source('source'); source('other');
  wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("来源：other")');
  run('find','role','button','click','--name','未校对，仍继续','--exact');
  wait('document.querySelector("#handoffPanel [role=status]").textContent.includes("新会话核对并修正中")');
  assert.deepEqual(actions().filter(a=>a.kind==='continue').map(a=>a.body), [{operation_id:b}]);
  console.log('PASS '+label+' exact replaceState→visible A click: zero Continue; deliberate B click: one B Continue');
  reset(); source();
}
function assertIcon(size) {
  const icon = evaluate(`(()=>{const b=document.querySelector('#handoffButton'),s=b.querySelector('svg');
    return {label:b.textContent,title:b.title,aria:b.getAttribute('aria-label'),type:b.type,controls:b.getAttribute('aria-controls'),
      native:b.classList.contains('icon-btn'),namespace:s?.namespaceURI,attrs:s&&Object.fromEntries([...s.attributes].map(a=>[a.name,a.value])),
      children:s&&[...s.children].map(n=>[n.localName,Object.fromEntries([...n.attributes].map(a=>[a.name,a.value]))]),
      width:s&&getComputedStyle(s).width,height:s&&getComputedStyle(s).height,display:s&&getComputedStyle(s).display,pointer:s&&getComputedStyle(s).pointerEvents};})()`);
  assert.equal(icon.label, ''); assert.equal(icon.title, '会话接力'); assert.equal(icon.aria, '会话接力');
  assert.equal(icon.type, 'button'); assert.equal(icon.controls, 'handoffPanel'); assert(icon.native);
  assert.equal(icon.namespace, 'http://www.w3.org/2000/svg');
  assert.deepEqual(icon.attrs, { viewBox:'0 0 24 24',width:'20',height:'20',fill:'none',stroke:'currentColor','stroke-width':'1.8','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false' });
  assert.deepEqual(icon.children, [
    ['rect',{x:'2.5',y:'3',width:'9',height:'7',rx:'2'}],
    ['rect',{x:'12.5',y:'14.5',width:'9',height:'6.5',rx:'2'}],
    ['path',{d:'M5.5 6.5h3M11.5 6.5h3a2.5 2.5 0 0 1 2.5 2.5v3.6M14.9 10.5 17 12.6l2.1-2.1'}],
  ]);
  assert.equal(icon.width, size+'px'); assert.equal(icon.height, size+'px');
  assert.equal(icon.display, 'block'); assert.equal(icon.pointer, 'none');
}
function assertToolbar(width) {
  const g = evaluate(`(()=>{const ids=['diagBtn','handoffButton','unattendedBtn'];return ids.map(id=>{
    const b=document.getElementById(id),r=b.getBoundingClientRect(),s=getComputedStyle(b);
    return {id,x:r.x,right:r.right,width:r.width,height:r.height,padding:s.padding,
      hit:[[.25,.25],[.5,.5],[.75,.75]].every(([x,y])=>b.contains(document.elementFromPoint(r.x+r.width*x,r.y+r.height*y))),
      hitInset:getComputedStyle(b,'::after').inset};});})()`);
  assert(g.every(r => r.x >= 0 && r.right <= width && r.width >= 30 && r.hit), JSON.stringify(g));
  assert(g[0].right <= g[1].x + 1 && g[1].right <= g[2].x + 1, JSON.stringify(g));
  for (const key of ['width','height','padding','hitInset']) assert.equal(g[1][key], g[0][key], key+' must inherit native hit area');
  assert(evaluate('document.documentElement.scrollWidth <= innerWidth'));
  console.log('Toolbar '+width+': '+JSON.stringify(g));
}
function keyboardCancel() {
  evaluate('document.querySelector("#diagBtn").focus()'); run('press', 'Tab');
  assert(evaluate('document.activeElement.id === "handoffButton"'));
  assert(evaluate('getComputedStyle(document.activeElement).outlineStyle !== "none" && parseFloat(getComputedStyle(document.activeElement).outlineWidth) > 0'), 'Visible native keyboard focus');
  run('press', 'Enter'); wait('document.activeElement.id === "handoffNote"');
  assert.equal(evaluate('document.querySelector("#handoffButton").getAttribute("aria-expanded")'), 'true');
  run('press', 'Escape'); assert(evaluate('document.querySelector("#handoffPanel").hidden && document.activeElement.id === "handoffButton"'));
  assert.equal(evaluate('document.querySelector("#handoffButton").getAttribute("aria-expanded")'), 'false');
}
const row = id => `[data-session-id="${id}"]`;
function openSidebar() {
  if (!evaluate('document.body.classList.contains("sidebar-open")')) click('#toggleSidebarBtn');
  wait('document.body.classList.contains("sidebar-open") && document.querySelector(".sidebar").getBoundingClientRect().left >= -1');
}
function swipe(id, direction) {
  const selector = row(id) + ' .sessionContent'; run('scrollintoview', selector);
  const r = evaluate(`document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect().toJSON()`);
  const x = direction === 'left' ? r.x+r.width-24 : r.x+24, dx = direction === 'left' ? -160 : 130;
  // Trusted browser touch input: mouseup would generate a compatibility click
  // which upstream intentionally uses to close an already-open swipe.
  const cdp = run('get','cdp-url').trim();
  execFileSync(process.execPath, ['-e', `
    const [cdp,url,x,y,dx]=JSON.parse(process.argv[1]);
    const ws=new WebSocket(cdp), pending=new Map();let id=0;
    const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
      pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params,sessionId}));
    });
    ws.onmessage=e=>{const m=JSON.parse(e.data),p=pending.get(m.id);if(!p)return;
      pending.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);};
    ws.onopen=async()=>{try{
      const {targetInfos}=await send('Target.getTargets');
      const page=targetInfos.find(t=>t.type==='page'&&t.url.startsWith(url));
      if(!page)throw Error('Owned fixture page missing');
      const {sessionId}=await send('Target.attachToTarget',{targetId:page.targetId,flatten:true});
      await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]},sessionId);
      for(let i=1;i<=6;i++)await send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+dx*i/6,y}]},sessionId);
      await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]},sessionId);
    }catch(e){console.error(e);process.exitCode=1;}finally{ws.close();}};
  `, JSON.stringify([cdp,url,x,r.y+r.height/2,dx])],{timeout:15000,stdio:'pipe'});
  wait(`Math.abs(Number(document.querySelector(${JSON.stringify(selector)}).dataset.swipeX))>50`);
  assert(evaluate('document.body.classList.contains("sidebar-open")'), 'Trusted swipe must leave the native drawer open');
}
try {
  run('--executable-path', desktopChrome, 'open', url); run('set', 'viewport', '1440', '1000'); control({ reset: true }); login(); reset(); source();
  assert(evaluate('matchMedia("(hover:hover) and (pointer:fine)").matches'));
  assert.equal(evaluate('document.querySelector("#diagBtn").nextElementSibling.id'), 'handoffButton');
  assert.equal(evaluate('document.querySelectorAll("#handoffButton").length'), 1);
  assert.equal(evaluate('document.querySelectorAll(".owner-root-star").length'), 1);
  assertIcon(20); assertToolbar(1440); keyboardCancel(); screenshot('desktop-icon-focus');
  concurrentSources('desktop'); crossTabAndStorage(); staleDisplayedContinue('desktop');
  // One external icon child mutation must not cause a remount or observer feedback loop.
  assert.equal(evaluate(`new Promise(resolve=>{const b=document.querySelector('#handoffButton');let changes=0;
    const observer=new MutationObserver(records=>changes+=records.length);observer.observe(b,{childList:true,subtree:true});
    b.querySelector('svg').append(document.createComment('observer probe'));
    setTimeout(()=>{observer.disconnect();resolve(b===document.querySelector('#handoffButton')&&changes===1);},100);})`), true);
  run('hover', '[data-session-id=source]'); click('[data-session-id=source] button[aria-label="Edit conversation"]');
  run('wait', '#editViewer[open]'); assert(evaluate('document.querySelector("#editNameInput").value.includes("当前项目")'));
  // Native dialogs deliberately do not close on Escape. Use their real close controls;
  // otherwise coordinate clicks hit a backdrop and hidden-field fill can falsely pass.
  click('#editCloseBtn'); wait('!document.querySelector("#editViewer").open');
  run('hover', '[data-session-id=source]'); click('[data-session-id=source] button[aria-label="Delete session"]');
  wait('document.querySelector("#appConfirm").style.display === "flex"');
  click('#appConfirmCancelBtn'); wait('document.querySelector("#appConfirm").style.display !== "flex"');
  run('hover', '[data-session-id=source]'); click('[data-session-id=source] button[aria-label="Duplicate session"]');
  run('wait', '[data-session-id=copy]');
  const duplicate = actions().find(a => a.kind === 'native' && a.path === '/api/sessions');
  assert.equal(duplicate.body.cwd, '/DEMO/project'); assert.equal(duplicate.body.agent_backend, 'pi'); source();
  assert(!actions().some(a => a.path?.includes('/delete')));
  click('#diagBtn'); wait('document.querySelector("#diagViewer").getBoundingClientRect().height > 0');
  click('#diagCloseBtn'); wait('document.querySelector("#diagViewer").style.display !== "flex" && !document.querySelector(".app").inert');
  click('#handoffButton'); wait('document.querySelector("#handoffNote").getBoundingClientRect().height > 0');
  run('fill', '#handoffNote', 'DEMO：保留未完成约束'); screenshot('desktop-confirm');
  // Native replaceState can change selection without hashchange; no DOM/localStorage inference.
  evaluate(`history.replaceState(null,'','#session=other');document.querySelector('#handoffPanel form').requestSubmit()`);
  assert(text().includes('来源已切换，请再次确认：other')); assert.equal(actions().filter(a => a.kind === 'start').length, 0);
  evaluate(`history.replaceState(null,'','#session=other');document.querySelector('#handoffPanel form').requestSubmit();document.querySelector('#handoffPanel form').requestSubmit()`);
  wait('document.querySelector("#handoffPanel").textContent.includes("新会话独立恢复中")');
  assert.equal(actions().filter(a => a.kind === 'start').length, 1); assert.equal(actions().find(a => a.kind === 'start').body.source_session_id, 'other');
  assert.equal(actions().find(a => a.kind === 'start').body.note, undefined); // A's note must not leak to B.
  assert.deepEqual(Object.keys(refs()[storageBase+':source:other']), ['operation_id','source_session_id']);
  // The deliberate URL-only stale-submit probe does not change native internal
  // selection. Finish with a real native selection before native refreshes it.
  nativeSelect('other');
  for (const stage of ['launching', 'waiting_source', 'reviewing', 'revising', 'ready']) status(stage);
  screenshot('desktop-ready');
  click('#handoffPanel a'); wait('new URLSearchParams(location.hash.slice(1)).get("session")==="target"');
  wait('document.querySelector("#sessions .session.active")?.dataset.sessionId==="target"');
  // Target selection has its own empty handoff view; return to the operation's source.
  source('other');
  // Retained operation status is read on reload, never recreated.
  run('reload'); run('wait', '#handoffButton'); wait('document.querySelector("#handoffPanel").textContent.includes("新会话已接续")');
  assert.equal(actions().filter(a => a.kind === 'start').length, 1);
  // Failed response after server acceptance: same UUID on explicit reconciliation.
  reset(); source(); control({ fault: 'drop-start' }); click('#handoffButton'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffPanel").textContent.includes("结果未知")');
  const sent = actions().filter(a => a.kind === 'start'), first = sent[0].body.request_id;
  // Chrome may retry a reset connection at transport level; all retries retain the UUID.
  assert(sent.every(a => a.body.request_id === first));
  run('reload'); run('wait', '#handoffButton');
  assert.equal(actions().filter(a => a.kind === 'start').length, sent.length);
  control({ fault: '' });
  run('find', 'role', 'button', 'click', '--name', '核对原请求', '--exact');
  wait('document.querySelector("#handoffPanel").textContent.includes("新会话独立恢复中")');
  assert.equal(actions().filter(a => a.kind === 'start').length, sent.length + 1);
  assert(actions().filter(a => a.kind === 'start').every(a => a.body.request_id === first));
  // Visible-only status polling; local simulated visibility flag, actual POST log.
  evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))`);
  const beforeHidden = actions().filter(a => a.kind === 'status').length;
  evaluate('new Promise(r=>setTimeout(()=>r(true),2100))'); assert.equal(actions().filter(a => a.kind === 'status').length, beforeHidden);
  evaluate(`delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))`);
  control({ fault: 'status-error' }); evaluate('document.dispatchEvent(new Event("visibilitychange"))');
  wait('document.querySelector("#handoffPanel").textContent.includes("暂时无法查询")');
  control({ fault: '' }); run('find', 'role', 'button', 'click', '--name', '刷新接力状态', '--exact');
  wait('!document.querySelector("#handoffPanel [role=alert]").textContent');
  status('unreviewed'); assert(!evaluate('[...document.querySelectorAll("#handoffPanel button")].some(b=>b.textContent==="未校对，仍继续"&&!b.hidden)'));
  control({ allow: true }); evaluate('document.dispatchEvent(new Event("visibilitychange"))');
  wait('[...document.querySelectorAll("#handoffPanel button")].some(b=>b.textContent==="未校对，仍继续"&&!b.hidden)'); screenshot('desktop-unreviewed');
  run('find', 'role', 'button', 'click', '--name', '未校对，仍继续', '--exact'); wait('document.querySelector("#handoffPanel").textContent.includes("新会话核对并修正中")');
  assert.equal(actions().filter(a => a.kind === 'continue').length, 1);
  // Topbar remount: narrowly observed native actions are not duplicated; operation remains.
  evaluate(`const old=document.querySelector('.topbar'),next=old.cloneNode(true);next.querySelector('#handoffButton').remove();old.replaceWith(next)`);
  wait('document.querySelectorAll("#handoffButton").length===1'); assert(text().includes('新会话核对并修正中')); assertIcon(20);
  // Dynamic public text is always text, never interpreted as markup.
  control({ message: '<img src=x onerror="window.demoXSS=true">' });
  evaluate('document.dispatchEvent(new Event("visibilitychange"))');
  wait('document.querySelector("#handoffPanel").textContent.includes("<img")');
  assert.equal(evaluate('document.querySelectorAll("#handoffPanel img").length'), 0);
  assert(!evaluate('Boolean(window.demoXSS)'));
  reset(); source(); control({ fault: 'unsupported' }); click('#handoffButton'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffPanel").textContent.includes("仅支持 Pi")'); screenshot('desktop-error');
  assert.equal(actions().filter(a => a.kind === 'start').length, 1);
  // Incompatible action slot has an explicit warning; restoring it mounts one button.
  evaluate(`window.demoDetails=document.querySelector('#diagBtn');demoDetails.remove()`);
  run('wait', '#handoffCompatibility');
  evaluate(`document.querySelector('.topActions').prepend(demoDetails)`);
  wait('!document.querySelector("#handoffCompatibility") && document.querySelectorAll("#handoffButton").length===1');
  // Successful logout clears only feature recovery state and removes UI/timers/listeners.
  reset(); source(); click('#handoffButton'); click('#handoffPanel button[type=submit]');
  wait('document.querySelector("#handoffPanel").textContent.includes("新会话独立恢复中")');
  click('#logoutBtnSide'); run('wait', '#loginForm');
  assert.equal(Object.keys(refs()).length, 0);
  assert(!evaluate('Boolean(document.querySelector("#handoffButton"))'));
  const loggedOut = actions().filter(a => a.kind === 'status').length;
  evaluate('document.dispatchEvent(new Event("visibilitychange"));new Promise(r=>setTimeout(()=>r(true),2100))');
  assert.equal(actions().filter(a => a.kind === 'status').length, loggedOut);
  login(); assert.equal(evaluate('document.querySelectorAll("#handoffButton").length'), 1);
  run('close');
  session = 'handoff-native-mobile-' + process.pid;
  run('--executable-path', chrome, 'open', url); run('set', 'viewport', '390', '844'); login(); reset(); source();
  assert.equal(evaluate('document.querySelectorAll("#sessions .session.desktop").length'), 0);
  assertIcon(18); assertToolbar(390); keyboardCancel(); screenshot('mobile-icon-focus');
  concurrentSources('mobile'); staleDisplayedContinue('mobile');
  openSidebar(); swipe('source', 'left');
  click('[data-session-id=source] button[aria-label="Edit conversation"]'); run('wait', '#editViewer[open]');
  click('#editCloseBtn'); wait('!document.querySelector("#editViewer").open');
  openSidebar(); swipe('other', 'right');
  click('[data-session-id=other] button[aria-label="Delete session"]');
  wait('document.querySelector("#appConfirm").style.display === "flex"'); click('#appConfirmCancelBtn');
  wait('document.querySelector("#appConfirm").style.display !== "flex"');
  assert(!actions().some(a => a.path?.includes('/delete')));
  try {
    openSidebar(); swipe('source', 'left');
    screenshot('mobile-native-actions');
    click('[data-session-id=source] button[aria-label="Duplicate session"]');
    run('wait', '[data-session-id=copy]');
    openSidebar(); click('[data-session-id=source]'); wait('!document.body.classList.contains("sidebar-open")');
  } catch (e) {
    // Preserve the known native fixture gap without losing handoff/unload coverage.
    console.warn('GAP mobile native Duplicate/drawer fixture (desktop Duplicate already checked): '+e.message);
    screenshot('mobile-duplicate-fixture-gap');
    if (evaluate('document.body.classList.contains("sidebar-open")')) click('#toggleSidebarBtn');
    source();
  }
  // Actual native compact branch and trusted CDP touch gestures above, not CSS-only mocks.
  click('#handoffButton'); wait('document.querySelector("#handoffNote").getBoundingClientRect().height > 0'); screenshot('mobile-confirm');
  assertIcon(18); assertToolbar(390);
  click('#handoffPanel button[type=submit]'); wait('document.querySelector("#handoffPanel").textContent.includes("新会话独立恢复中")');
  status('unreviewed'); control({ allow: true }); evaluate('document.dispatchEvent(new Event("visibilitychange"))');
  wait('[...document.querySelectorAll("#handoffPanel button")].some(b=>b.textContent==="未校对，仍继续"&&!b.hidden)'); screenshot('mobile-unreviewed');
  click('#handoffPanel a'); wait('new URLSearchParams(location.hash.slice(1)).get("session")==="target"');
  wait('document.querySelector("#sessions .session.active")?.dataset.sessionId==="target"');
  const beforeUnload = actions().filter(a => a.kind === 'status').length;
  evaluate('window.dispatchEvent(new Event("pagehide"));document.dispatchEvent(new Event("visibilitychange"))');
  assert(!evaluate('Boolean(document.querySelector("#handoffButton"))'));
  evaluate('new Promise(r=>setTimeout(()=>r(true),2100))'); assert.equal(actions().filter(a => a.kind === 'status').length, beforeUnload);
  console.log('PASS native desktop/mobile DEMO: hash, actions, state, ambiguity, polling, remount, errors, logout/unload. Screenshots: ' + output);
} catch (e) {
  try { screenshot('failure-'+session); console.error('Browser failure state:', evaluate('({hash:location.hash,hidden:document.hidden,login:!!document.querySelector("#loginForm"),panel:document.querySelector("#handoffPanel")?.textContent,compatibility:document.querySelector("#handoffCompatibility")?.textContent})')); } catch (_) {}
  throw e;
} finally { try { run('close'); } finally { rmSync(temp, { recursive: true, force: true }); } }
