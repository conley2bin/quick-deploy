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
const run = (...args) => execFileSync(bin, ['--session', session, ...args], { encoding: 'utf8', timeout: 40000 });
const evaluate = code => { const r = JSON.parse(run('eval', code, '--json')); assert(r.success, r.error); return r.data.result; };
const wait = code => run('wait', '--fn', code);
const click = s => run('click', s);
const text = () => evaluate('document.querySelector("#handoffPanel")?.textContent');
const control = b => evaluate(`fetch('/demo/__fixture__/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(b)})}).then(r=>r.json())`);
const actions = () => control({}).actions;
const status = value => { control({ status: value }); if (evaluate('!document.querySelector("#handoffPanel button:nth-child(1)")')) throw Error('Panel absent'); evaluate('document.dispatchEvent(new Event("visibilitychange"))'); wait(`document.querySelector('#handoffPanel').textContent.includes(${JSON.stringify(require('../web/core.js').labels[value])})`); };
function login() { run('wait', '#loginForm'); run('fill', 'input[type=password]', 'demo'); click('#loginForm button'); run('wait', '#handoffButton'); }
function source(id = 'source') { evaluate(`location.replace('#session=${id}')`); wait(`document.querySelector('#sessions .session.active')?.dataset.sessionId===${JSON.stringify(id)}`); }
function reset() { control({ reset: true }); evaluate(`localStorage.removeItem('codoxear:session-handoff:/demo/__session_handoff__/')`); run('reload'); run('wait', '#handoffButton'); }
function screenshot(name) { run('screenshot', join(output, name + '.png')); }
function geometry() {
  return evaluate(`(()=>{const ids=['diagBtn','handoffButton','unattendedBtn'];return ids.map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,x:r.x,right:r.right,width:r.width,top:r.top,bottom:r.bottom};})})()`);
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
  run('--executable-path', desktopChrome, 'open', url); run('set', 'viewport', '1440', '1000'); login(); reset(); source();
  assert(evaluate('matchMedia("(hover:hover) and (pointer:fine)").matches'));
  assert.equal(evaluate('document.querySelector("#diagBtn").nextElementSibling.id'), 'handoffButton');
  assert.equal(evaluate('document.querySelectorAll("#handoffButton").length'), 1);
  assert.equal(evaluate('document.querySelectorAll(".owner-root-star").length'), 1);
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
  assert.equal(actions().find(a => a.kind === 'start').body.note, 'DEMO：保留未完成约束');
  assert.deepEqual(Object.keys(JSON.parse(evaluate(`localStorage.getItem('codoxear:session-handoff:/demo/__session_handoff__/')`))), ['operation_id']);
  for (const stage of ['launching', 'waiting_source', 'reviewing', 'revising', 'ready']) status(stage);
  screenshot('desktop-ready');
  click('#handoffPanel a'); wait('new URLSearchParams(location.hash.slice(1)).get("session")==="target"');
  wait('document.querySelector("#sessions .session.active")?.dataset.sessionId==="target"');
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
  wait('document.querySelectorAll("#handoffButton").length===1'); assert(text().includes('新会话核对并修正中'));
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
  assert.equal(evaluate(`localStorage.getItem('codoxear:session-handoff:/demo/__session_handoff__/')`), null);
  assert(!evaluate('Boolean(document.querySelector("#handoffButton"))'));
  const loggedOut = actions().filter(a => a.kind === 'status').length;
  evaluate('document.dispatchEvent(new Event("visibilitychange"));new Promise(r=>setTimeout(()=>r(true),2100))');
  assert.equal(actions().filter(a => a.kind === 'status').length, loggedOut);
  login(); assert.equal(evaluate('document.querySelectorAll("#handoffButton").length'), 1);
  run('close');
  session = 'handoff-native-mobile-' + process.pid;
  run('--executable-path', chrome, 'open', url); run('set', 'viewport', '390', '844'); login(); reset(); source();
  assert.equal(evaluate('document.querySelectorAll("#sessions .session.desktop").length'), 0);
  openSidebar(); swipe('source', 'left');
  click('[data-session-id=source] button[aria-label="Edit conversation"]'); run('wait', '#editViewer[open]');
  click('#editCloseBtn'); wait('!document.querySelector("#editViewer").open');
  openSidebar(); swipe('other', 'right');
  click('[data-session-id=other] button[aria-label="Delete session"]');
  wait('document.querySelector("#appConfirm").style.display === "flex"'); click('#appConfirmCancelBtn');
  wait('document.querySelector("#appConfirm").style.display !== "flex"');
  assert(!actions().some(a => a.path?.includes('/delete')));
  openSidebar(); swipe('source', 'left');
  screenshot('mobile-native-actions');
  click('[data-session-id=source] button[aria-label="Duplicate session"]');
  run('wait', '[data-session-id=copy]');
  openSidebar(); click('[data-session-id=source]'); wait('!document.body.classList.contains("sidebar-open")');
  // Actual native compact branch and trusted CDP touch gestures above, not CSS-only mocks.
  click('#handoffButton'); wait('document.querySelector("#handoffNote").getBoundingClientRect().height > 0'); screenshot('mobile-confirm');
  const g = geometry(); assert(g.every(r => r.x >= 0 && r.right <= 390 && r.width >= 30), JSON.stringify(g));
  assert(g[0].right <= g[1].x + 1 && g[1].right <= g[2].x + 1, JSON.stringify(g));
  assert(evaluate('document.documentElement.scrollWidth <= innerWidth'));
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
} finally { try { run('close'); } finally { rmSync(temp, { recursive: true, force: true }); } }
