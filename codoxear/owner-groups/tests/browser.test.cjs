/* Isolated actual installed frontend + service worker; real pointer/keyboard input. */
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { mkdirSync, mkdtempSync, writeFileSync, chmodSync, rmSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const bin = process.env.AGENT_BROWSER_BIN || 'agent-browser';
const url = process.env.OWNER_GROUPS_DEMO_URL || 'http://127.0.0.1:8875/demo/';
assert(/^http:\/\/127\.0\.0\.1:\d+\/demo\/$/.test(url), 'Only isolated loopback demo accepted');
let session = 'owner-native-desktop-' + process.pid;
const output = process.env.OWNER_GROUPS_SCREENSHOTS || '/tmp/owner-groups-native-screenshots';
mkdirSync(output, { recursive: true });
const temp = mkdtempSync(join(tmpdir(), 'owner-browser-'));
const chrome = process.env.CHROME_BIN || '/opt/google/chrome/chrome';
// Single exact Chrome argument; agent-browser --args splits comma-separated flags.
// Blink media emulation selects the REAL upstream desktop branch, without source edits.
const desktopChrome = join(temp, 'chrome');
writeFileSync(desktopChrome, '#!/bin/sh\nexec ' + JSON.stringify(chrome) +
  ' "--blink-settings=primaryPointerType=4,availablePointerTypes=4,primaryHoverType=2,availableHoverTypes=2" "$@"\n');
chmodSync(desktopChrome, 0o700);
const run = (...args) => execFileSync(bin, ['--session', session, ...args], { encoding: 'utf8', timeout: 40000 });
const evaluate = code => { const r = JSON.parse(run('eval', code, '--json')); assert(r.success, r.error); return r.data.result; };
const wait = code => run('wait', '--fn', code);
const click = selector => run('click', selector);
const shot = name => run('screenshot', join(output, name + '.png'));
const row = id => `.session[data-session-id="${id}"]`;
const group = id => `[data-owner-group="${id}"]`;
const sequences = () => evaluate(`Object.fromEntries([...document.querySelectorAll('#sessions > .owner-team')].map(n=>[n.dataset.ownerGroup,[...n.querySelectorAll('.session')].map(r=>r.dataset.sessionId)]))`);
const sectionCounts = () => evaluate(`Object.fromEntries([...document.querySelectorAll('#sessions > .sessionGroupHeader')].map(n=>[n.dataset.sessionGroup,Number(n.querySelector('.sessionGroupCount').textContent)]))`);
const refresh = () => evaluate('CodoxearOwnerPanel.controller.refresh().then(()=>true)');
const control = state => evaluate(`fetch('/demo/__fixture__/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(state)})}).then(r=>r.json())`);
function update(state) {
  control(state); refresh();
  // Each fixture state increments a native alias suffix, forcing the upstream signature.
  const expected = evaluate(`fetch('/demo/api/sessions').then(r=>r.json()).then(d=>d.sessions[0]?.alias || null)`);
  if (expected) wait(`[...document.querySelectorAll('#sessions .titleText')].some(n=>n.textContent===${JSON.stringify(expected)})`);
  else wait(`document.querySelectorAll('#sessions .session').length===0`);
  refresh();
}
function login() {
  run('wait', '#loginForm'); run('fill', 'input[type=password]', 'demo'); click('#loginForm button');
  wait(`document.querySelectorAll('#sessions > .owner-team').length>=2`);
}
function theme() {
  click('#settingsBtnSide'); run('find', 'role', 'radio', 'click', '--name', 'Slate', '--exact');
  run('find', 'role', 'radio', 'click', '--name', 'Light', '--exact');
  run('find', 'role', 'button', 'click', '--name', 'Close', '--exact');
}
function unique() {
  assert(evaluate(`(()=>{const ids=[...document.querySelectorAll('#sessions .session')].map(n=>n.dataset.sessionId);return ids.length===new Set(ids).size})()`));
  assert.equal(evaluate(`document.querySelectorAll('#quick-deploy-owner-groups-style').length`), 1);
}
function openSidebar() { if (!evaluate('document.body.classList.contains("sidebar-open")')) click('#toggleSidebarBtn'); }
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
}
try {
  run('--executable-path', desktopChrome, 'open', url); control({reset:true}); run('set', 'viewport', '1440', '1100'); login();
  control({ variant:'normal',auth:true,activity:'',add:false,new_group:false }); refresh(); theme();
  wait(`document.querySelectorAll('#sessions .session.desktop').length===12`);
  assert(evaluate('matchMedia("(hover:hover) and (pointer:fine)").matches'));
  const initial = sequences();
  assert.deepEqual(Object.keys(initial), ['ops','other']);
  assert.deepEqual(initial.ops, ['demo-ops','demo-office-opaque-123','demo-legacy','demo-web','demo-qa']);
  assert.deepEqual(initial.other, ['demo-other','demo-task-web','demo-task-office']);
  assert.deepEqual(evaluate(`[...document.querySelectorAll('.owner-root-star')].map(n=>n.parentElement.dataset.sessionId)`), ['demo-ops','demo-other']);
  assert(evaluate(`(()=>{const star=document.querySelector('svg.owner-root-star'),s=star.getBoundingClientRect(),r=star.parentElement.getBoundingClientRect();return s.width===20&&s.height===20&&s.top>=r.top&&s.bottom<=r.bottom&&s.right<=r.right&&s.left>r.right-25&&star.getAttribute('aria-label')==='最高负责人'&&!star.querySelector('[id]')})()`));
  assert.equal(evaluate('document.querySelector(".sessionRestoreLead")?.nextElementSibling?.id'), 'sessions');
  click(row('demo-office-opaque-123'));
  wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId==='demo-office-opaque-123'`);
  assert(evaluate(`document.querySelector('#sessions').firstElementChild.matches('.sessionGroupHeader')`));
  assert.deepEqual(sectionCounts(), {now:12});
  const rootCards = evaluate(`([...document.querySelectorAll('.owner-root:not(.active)')].map(n=>{
    const s=getComputedStyle(n),c=getComputedStyle(n.querySelector('.sessionContent'));
    return {id:n.dataset.sessionId,border:s.borderTopWidth,color:s.borderTopColor,radius:s.borderRadius,fill:s.backgroundColor,content:c.backgroundColor};
  }))`);
  assert.deepEqual(rootCards.map(n=>n.id),['demo-ops','demo-other']);
  assert(rootCards.every(n=>n.border==='1px'&&n.color!=='rgba(0, 0, 0, 0)'&&n.radius==='12px'&&n.fill===n.content));
  unique(); shot('native-desktop-initial');
  console.log('ORDER initial '+JSON.stringify(initial));
  console.log('ROOT CARDS '+JSON.stringify(rootCards));

  update({variant:'all-now-bound'});
  assert.deepEqual(sectionCounts(),{now:8});
  assert(evaluate(`document.querySelector('#sessions').firstElementChild.matches('.sessionGroupHeader')&&document.querySelectorAll('#sessions > .session').length===0`));
  shot('native-desktop-all-bound');
  update({variant:'empty-root'});
  assert.deepEqual(Object.keys(sequences()),['other']);
  assert.equal(evaluate(`document.querySelectorAll('${group('ops')}').length`),0);
  assert(evaluate(`JSON.parse(localStorage.getItem('quick-deploy.owner-groups.order:'+location.pathname)).indexOf('ops')<JSON.parse(localStorage.getItem('quick-deploy.owner-groups.order:'+location.pathname)).indexOf('other')`));
  shot('native-desktop-empty-root');
  update({variant:'normal',sections:'mixed'});
  assert.deepEqual(Object.keys(sequences()),['ops','other']); // returning root retains its slot
  assert.deepEqual(sectionCounts(),{now:10,waiting:1,later:1});
  control({variant:'bridge-down'}); refresh();
  assert.deepEqual(sectionCounts(),{now:8,waiting:2,later:2});
  assert.equal(evaluate(`document.querySelectorAll('.owner-root').length`),0);
  update({variant:'normal',sections:''});
  console.log('PASS leading NOW retains total even with every row boxed; empty root hidden and returns to saved slot; later section counts and native restore correct.');

  update({ activity:'demo-task-office' });
  let seq = sequences(); assert.deepEqual(Object.keys(seq), ['ops','other']); assert.deepEqual(seq.ops, initial.ops);
  assert.deepEqual(seq.other, ['demo-other','demo-task-office','demo-task-web']);
  assert(evaluate(`!!document.querySelector('${group('other')} ${row('demo-task-office')} .stateDot.busy')&&!!document.querySelector('${row('demo-task-office')} .badge.unread')`));
  console.log('ORDER B member update '+JSON.stringify(seq));
  update({activity:'demo-qa'}); seq = sequences();
  assert.deepEqual(seq.ops, ['demo-ops','demo-qa','demo-office-opaque-123','demo-legacy','demo-web']);
  assert.deepEqual(seq.other, initial.other); console.log('ORDER A grandchild update '+JSON.stringify(seq));
  update({activity:'demo-task-office',add:true,new_group:true}); seq=sequences();
  assert.deepEqual(Object.keys(seq), ['ops','other','third']);
  assert.equal(seq.other[0], 'demo-other'); assert.equal(seq.other[1], 'demo-new');
  const beforeReload = seq; run('reload'); wait(`!!document.querySelector('${group('third')} ${row('demo-third')}')`);
  assert.deepEqual(sequences(), beforeReload); console.log('ORDER additions/reload '+JSON.stringify(beforeReload)); unique();
  update({activity:'',add:false,new_group:false});

  // Native desktop row callbacks and keyboard controls remain on the same objects.
  evaluate(`window.originalNativeRow=document.querySelector('${row('demo-office-opaque-123')}')`); refresh();
  assert(evaluate(`window.originalNativeRow===document.querySelector('${row('demo-office-opaque-123')}')`));
  click(row('demo-ops')); wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId==='demo-ops'`);
  click(row('demo-office-opaque-123')); wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId==='demo-office-opaque-123'`);
  run('hover', row('demo-office-opaque-123')); click(row('demo-office-opaque-123')+' button[aria-label="Edit conversation"]');
  run('wait','#editViewer[open]'); assert.equal(evaluate('document.getElementById("editNameInput").value').startsWith('office 场景评测'),true);
  run('fill','#editNameInput','office 原生编辑'); click('#editSaveBtn'); wait('!document.getElementById("editViewer").open');
  assert(control({}).actions.some(a=>a.path==='/api/sessions/demo-office-opaque-123/edit'&&a.body.name==='office 原生编辑'));
  run('focus',row('demo-ops')); run('press','f'); run('press','1');
  wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId==='demo-ops'`);
  run('hover',row('demo-ops')); run('focus',row('demo-ops')); run('press','Tab');
  assert.equal(evaluate('document.activeElement.getAttribute("aria-label")'),'Edit conversation');
  run('press','Enter'); run('wait','#editViewer[open]'); run('press','Escape');
  run('hover',row('demo-ops')); click(row('demo-ops')+' button[aria-label="Delete session"]');
  run('find','role','button','click','--name','Cancel','--exact');
  run('hover',row('demo-ops')); click(row('demo-ops')+' button[aria-label="Duplicate session"]');
  wait(`!!document.querySelector('${row('demo-created')}')`);
  assert(evaluate(`!document.querySelector('${row('demo-created')}').closest('.owner-team')`));
  const spawned = control({}).actions.find(a=>a.path==='/api/sessions'); assert.equal(spawned.body.cwd,'/example/Dexathlon'); assert.equal(spawned.body.agent_backend,'pi');
  console.log('PASS desktop native selection, hover Edit+save exact broker, Tab/Enter+Escape, Delete cancel, Duplicate original backend/cwd; no real agent.');

  update({variant:'handover'}); assert(sequences().other.includes('demo-office-opaque-123')); assert(sequences().other.includes('demo-qa'));
  update({variant:'root-missing'}); assert.equal(evaluate(`document.querySelector('${group('ops')} .owner-root-star')`),null);
  assert(evaluate(`document.querySelector('${group('ops')} .owner-unavailable').textContent.includes('会话不可用')`));
  update({variant:'unresolved'}); assert.equal(evaluate(`document.querySelector('${row('demo-orphan')}').closest('.owner-team').dataset.ownerGroup`),'owner:not-registered');
  assert.equal(evaluate(`document.querySelector('${row('demo-orphan')}').closest('.owner-team').querySelectorAll('.owner-root-star').length`),0);
  update({variant:'duplicate-thread'}); assert(evaluate(`!document.querySelector('${row('demo-office-opaque-123')}').closest('.owner-team')`));
  update({variant:'rebind'}); assert(evaluate(`!document.querySelector('${row('demo-office-opaque-123')}').closest('.owner-team')`));
  update({variant:'normal'});
  // Authentic upstream SW caching behavior; POST still fresh.
  wait('!!navigator.serviceWorker.controller');
  evaluate(`(async()=>{const c=await caches.open('codoxear-static-v1');await c.put('/demo/__owner_groups__/snapshot',new Response('{"stale":true}',{headers:{'Content-Type':'application/json'}}));return true})()`);
  const transport=evaluate(`(async()=>({get:await(await fetch('/demo/__owner_groups__/snapshot')).json(),post:await(await fetch('/demo/__owner_groups__/snapshot',{method:'POST'})).json()}))()`);
  assert.equal(transport.get.stale,true); assert(transport.post.owners.ok);
  for (const variant of ['bridge-down','cycle','empty']) {
    control({variant}); refresh(); assert.equal(evaluate('document.querySelectorAll(".owner-team").length'),0);
    assert(evaluate('!!document.querySelector("#sessions > .session")')); unique();
    if (variant==='bridge-down') shot('native-failure-restored');
  }
  control({variant:'normal',auth:false}); refresh(); assert.equal(evaluate('document.querySelectorAll(".owner-team").length'),0);
  control({variant:'normal',auth:true}); run('reload');
  wait('!!document.getElementById("loginForm")||!!document.querySelector(".owner-team")');
  if (evaluate('!!document.getElementById("loginForm")')) login();
  evaluate('document.getElementById("sessions").classList.remove("sessions")'); wait('document.querySelectorAll(".owner-team").length===0');
  assert(evaluate('!!document.querySelector("#sessions > .session")'));
  evaluate('document.getElementById("sessions").classList.add("sessions")'); wait('document.querySelectorAll(".owner-team").length===2');
  evaluate(`new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=document.querySelector('script[src*="__owner_groups__/app.js"]').src;s.onload=()=>resolve(true);s.onerror=reject;document.body.append(s)})`);
  wait('document.querySelectorAll(".owner-team").length===2'); unique();
  const mutations=evaluate(`new Promise(resolve=>{let count=0;const o=new MutationObserver(r=>count+=r.length);o.observe(document.getElementById('sessions'),{childList:true,subtree:true});requestAnimationFrame(()=>requestAnimationFrame(()=>{o.disconnect();resolve(count)}))})`);
  assert.equal(mutations,0); // no self-triggering observer/reconciliation loop
  update({variant:'no-sessions'}); assert.equal(evaluate('document.querySelectorAll("#sessions .session").length'),0);
  update({variant:'normal'}); click('#logoutBtnSide'); run('wait','#loginForm'); assert.equal(evaluate('document.querySelectorAll(".owner-team").length'),0); login(); unique();
  console.log('PASS native rerenders/reinjection/logout, empty list, fresh SW POST, membership/handover/duplicate/rebind/unresolved, failure/unsupported restoration, no idle observer loop.');
  run('close');

  // Separate touch/no-hover browser. At 390px upstream must create real swipe cards.
  session='owner-native-mobile-'+process.pid; run('--executable-path',chrome,'open',url); run('set','viewport','390','844'); login(); openSidebar(); theme();
  openSidebar(); update({variant:'normal',activity:'',add:false,new_group:false});
  assert.equal(evaluate('document.querySelectorAll("#sessions .session.desktop").length'),0);
  assert(!evaluate('document.body.scrollWidth>innerWidth'));
  assert(evaluate(`document.querySelector('#sessions').firstElementChild.matches('.sessionGroupHeader')`));
  assert.equal(sectionCounts().now,evaluate(`document.querySelectorAll('#sessions .session').length`));
  evaluate('document.getElementById("sessions").scrollTop=0'); shot('native-mobile-initial');
  click(row('demo-ops')); wait('!document.body.classList.contains("sidebar-open")');
  wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId==='demo-ops'`); openSidebar();
  swipe('demo-office-opaque-123','left');
  shot('native-mobile-swipe');
  click(row('demo-office-opaque-123')+' button[aria-label="Edit conversation"]'); run('wait','#editViewer[open]'); run('press','Escape');
  openSidebar(); swipe('demo-ops','right'); click(row('demo-ops')+' button[aria-label="Delete session"]'); run('find','role','button','click','--name','Cancel','--exact');
  openSidebar(); click(row('demo-ops')); openSidebar();
  update({activity:'demo-task-office'}); assert.deepEqual(Object.keys(sequences()),['ops','other']); assert.equal(sequences().other[1],'demo-task-office');
  evaluate('document.getElementById("sessions").scrollTop=0'); shot('native-mobile-updated'); unique();
  console.log('PASS 390px native selection closes sidebar, real swipe left opens Edit, swipe right Delete cancel, B update stays second under B root, no overflow.');
  console.log('SCREENSHOTS '+output);
} finally {
  try { control({variant:'normal',auth:true,activity:'',add:false,new_group:false}); } catch (_) {}
  try { run('close'); } catch (_) {}
  rmSync(temp,{recursive:true,force:true});
}
