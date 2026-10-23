/* Run against demo_server.py only. Uses agent-browser CLI, no npm project deps. */
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const bin = process.env.AGENT_BROWSER_BIN || 'agent-browser';
const url = process.env.OWNER_GROUPS_DEMO_URL || 'http://127.0.0.1:8875/demo/';
assert(/^http:\/\/127\.0\.0\.1:\d+\/demo\/$/.test(url), 'Only isolated loopback demo accepted');
const session = 'owner-groups-test-' + process.pid;
const output = process.env.OWNER_GROUPS_SCREENSHOTS || '/tmp/owner-groups-screenshots';
mkdirSync(output, { recursive: true });
const run = (...args) => execFileSync(bin, ['--session', session, ...args], { encoding: 'utf8', timeout: 40000 });
const evaluate = code => {
  const result = JSON.parse(run('eval', code, '--json'));
  assert(result.success, result.error); return result.data.result;
};
const shadow = 'document.getElementById("quick-deploy-owner-groups").shadowRoot';
const control = options => evaluate(`fetch('/demo/__fixture__/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(options)})}).then(r=>r.json())`);
const refresh = () => evaluate('CodoxearOwnerPanel.controller.refresh().then(()=>true)');
const click = selector => {
  if (selector.startsWith('[data-owner=') || selector.startsWith('[data-open-owner=')) {
    // Native CLI CSS does not pierce shadow roots. Measured real pointer events
    // avoid both DOM .click() simulation and stale accessibility references.
    const point = evaluate(`new Promise(resolve=>{const n=${shadow}.querySelector(${JSON.stringify(selector)});if(!n)throw Error('Missing click target');n.scrollIntoView({block:'center',behavior:'instant'});requestAnimationFrame(()=>{const r=n.getBoundingClientRect();resolve({x:r.x+r.width/2,y:r.y+r.height/2})})})`);
    run('mouse', 'move', String(Math.round(point.x)), String(Math.round(point.y))); run('mouse', 'down'); run('mouse', 'up');
    return;
  }
  run('click', selector);
};
const shot = name => run('screenshot', join(output, name + '.png'));
try {
  run('--executable-path', process.env.CHROME_BIN || '/opt/google/chrome/chrome', 'open', url);
  run('set', 'viewport', '1440', '1100');
  run('wait', '#loginForm'); run('fill', 'input[type=password]', 'demo'); click('#loginForm button');
  run('wait', '#sessions[data-codoxear-sessions-rendered=true]');
  run('wait', '--fn', '!!document.getElementById("quick-deploy-owner-groups")?.shadowRoot?.querySelector("[data-owner=ops]")');
  run('wait', '--fn', '!!navigator.serviceWorker.controller');
  const shape = evaluate(`({owners:${shadow}.querySelectorAll('details.owner').length,
    stars:[...${shadow}.querySelectorAll('.root-star')].map(s=>[s.closest('[data-owner]').dataset.owner,s.title,s.getAttribute('aria-label')]),
    qaParent:${shadow}.querySelector('[data-owner=qa]').parentElement.closest('[data-owner]').dataset.owner,
    orphan:${shadow}.querySelector('[data-owner=orphan]').closest('.unresolved').textContent,
    native:document.querySelectorAll('#sessions .session').length,
    nativeHeight:document.querySelector('#sessions').getBoundingClientRect().height,
    overflow:document.body.scrollWidth>innerWidth})`);
  assert.equal(shape.owners, 11); assert.equal(shape.native, 8); assert(shape.nativeHeight > 150); assert(!shape.overflow);
  assert.deepEqual(shape.stars.map(s=>s[0]).sort(), ['ops', 'other']);
  assert(shape.stars.every(s=>s[1]==='最高负责人'&&s[2]==='最高负责人'));
  assert.equal(shape.qaParent, 'office'); assert(shape.orphan.includes('上级待确认'));
  console.log('MEASURE desktop ' + JSON.stringify(shape));
  shot('desktop-team-overview');
  // Expand a valid descendant, then let the native hash listener perform actual selection.
  click('[data-open-owner=office]');
  run('wait', '--fn', 'document.querySelector("#sessions .session.active")?.dataset.sessionId === "demo-office-opaque-123"');
  assert(evaluate(`${shadow}.querySelector('[data-owner=office]').getAttribute('aria-current') === 'true'`));
  assert(evaluate(`(()=>{const star=${shadow}.querySelector('[data-owner=ops] .root-star').getBoundingClientRect();const host=document.getElementById('quick-deploy-owner-groups').getBoundingClientRect();return star.top>=host.top&&star.bottom<=host.bottom})()`));
  shot('desktop-hierarchy-root-star');
  console.log('PASS: real upstream shell/router: 11 unique owners, 2 true-root stars, 3 levels, unresolved parent, 8 original rows; exact office route selected.');

  // Actual upstream service worker caches no-store GETs. Poison only this isolated profile's GET cache.
  evaluate(`(async()=>{const c=await caches.open('codoxear-static-v1');await c.put('/demo/__owner_groups__/snapshot',new Response(JSON.stringify({stale:true}),{headers:{'Content-Type':'application/json'}}));return true})()`);
  const transport = evaluate(`(async()=>({get:await (await fetch('/demo/__owner_groups__/snapshot')).json(),post:await (await fetch('/demo/__owner_groups__/snapshot',{method:'POST'})).json()}))()`);
  assert.equal(transport.get.stale, true); assert.equal(transport.post.owners.owners.length, 11); assert(!transport.post.stale);
  control({ variant: 'rebind' });
  const priorHash = evaluate('location.hash'); click('[data-open-owner=office]');
  run('wait', '--fn', `${shadow}.textContent.includes('本次未跳转')`);
  assert.equal(evaluate('location.hash'), priorHash);
  assert.equal(evaluate(`${shadow}.querySelectorAll('[data-open-owner=office]').length`), 0);
  console.log('PASS: actual upstream SW returned deliberately stale cached GET, empty POST stayed fresh; changed thread on same opaque broker did not navigate.');
  control({ variant: 'normal' }); refresh();

  // Native renderer rebuilds #sessions on its own polling tick; grouping keeps collapse and never moves rows.
  click('[data-owner=ops] > summary');
  evaluate('window.fixtureOldNativeRow = document.querySelector("#sessions .session")');
  control({ variant: 'normal' });
  evaluate(`new Promise((resolve,reject)=>{const n=document.getElementById('sessions');const timeout=setTimeout(()=>{o.disconnect();reject(Error('No native rerender'))},15000);const o=new MutationObserver(()=>{if(window.fixtureOldNativeRow&&!window.fixtureOldNativeRow.isConnected){clearTimeout(timeout);o.disconnect();resolve(true)}});o.observe(n,{childList:true,subtree:true})})`);
  assert.equal(evaluate(`${shadow}.querySelector('[data-owner=ops]').open`), false);
  assert.equal(evaluate('document.querySelectorAll("#quick-deploy-owner-groups").length'), 1);
  // Original Edit control still opens its native modal (no delete/send endpoints used).
  assert.equal(evaluate(`document.querySelectorAll('#sessions button[aria-label="Edit conversation"]').length`), 8);
  assert.equal(evaluate('document.querySelector(".sessionRestoreLead")?.nextElementSibling?.id'), 'sessions');
  click('#threadTitle');
  run('wait', '#editViewer[open]');
  assert(evaluate('document.getElementById("editViewer").open'));
  run('press', 'Escape');
  console.log('PASS: native polling reconstructed row nodes; one panel retained collapse; native Edit conversation modal remained functional.');

  control({ variant: 'bridge-down' }); refresh();
  assert.equal(evaluate(`${shadow}.querySelectorAll('[data-open-owner]').length`), 0);
  assert.equal(evaluate('document.querySelectorAll("#sessions .session").length'), 8);
  shot('bridge-unavailable-native-preserved');
  control({ variant: 'normal', auth: false }); refresh();
  assert.equal(evaluate(`${shadow}.querySelectorAll('[data-open-owner]').length`), 0);
  control({ variant: 'empty', auth: true }); refresh();
  assert(evaluate(`${shadow}.textContent.includes('尚无已登记负责人')`)); shot('empty-registry');
  control({ variant: 'cycle' }); refresh();
  assert.equal(evaluate(`${shadow}.querySelectorAll('details.owner').length`), 0);
  control({ variant: 'xss' }); refresh();
  assert.equal(evaluate('window.fixtureXSS === true'), false);
  assert.equal(evaluate(`${shadow}.querySelectorAll('img').length`), 0);
  console.log('PASS: unavailable bridge, revoked auth, invalid graph removed all navigation; empty registry is discoverable; registry markup rendered as text.');

  // Incompatible shell removes enhancement navigation only, and remounts once on recovery.
  control({ variant: 'normal' }); refresh();
  evaluate('document.getElementById("sessions").classList.remove("sessions")');
  run('wait', '--fn', `${shadow}.textContent.includes('兼容性提示')`);
  shot('compatibility-warning');
  evaluate('document.getElementById("sessions").classList.add("sessions")');
  run('wait', '--fn', '!!document.getElementById("quick-deploy-owner-groups")?.shadowRoot?.querySelector("[data-owner=ops]")');
  // Reinjection tears down previous controller/timer; root replacement is tested via native logout/login.
  evaluate(`new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=document.querySelector('script[src*="__owner_groups__/app.js"]').src;s.onload=()=>resolve(true);s.onerror=reject;document.body.append(s)})`);
  run('wait', '--fn', '!!document.getElementById("quick-deploy-owner-groups")?.shadowRoot?.querySelector("[data-owner=ops]")');
  assert.equal(evaluate('document.querySelectorAll("#quick-deploy-owner-groups").length'), 1);
  click('#logoutBtnSide'); run('wait', '#loginForm');
  run('fill', 'input[type=password]', 'demo'); click('#loginForm button'); run('wait', '--fn', '!!document.getElementById("quick-deploy-owner-groups")?.shadowRoot?.querySelector("[data-owner=ops]")');
  assert.equal(evaluate('document.querySelectorAll("#quick-deploy-owner-groups").length'), 1);
  console.log('PASS: unsupported native anchor shows warning, recovery/reinjection/logout-login shell rebuild mount exactly once.');

  run('set', 'viewport', '390', '844');
  if (!evaluate('document.body.classList.contains("sidebar-open")')) click('#toggleSidebarBtn');
  run('wait', '--fn', '!!document.getElementById("quick-deploy-owner-groups")?.shadowRoot?.querySelector("[data-owner=ops]")');
  // Close compact independent root if still open; open team and Office for visible 3-level detail.
  evaluate(`${shadow}.querySelector('[data-owner=other]').open=false;${shadow}.querySelector('[data-owner=ops]').open=true;${shadow}.querySelector('[data-owner=office]').open=true`);
  evaluate(`new Promise(resolve=>{${shadow}.querySelector('[data-owner=office] summary').scrollIntoView({block:'center'});requestAnimationFrame(()=>resolve(true))})`);
  const mobile = evaluate(`({width:innerWidth,overflow:document.body.scrollWidth>innerWidth,star:${shadow}.querySelector('[data-owner=ops] .root-star').getBoundingClientRect().width,native:document.querySelector('#sessions').getBoundingClientRect().height})`);
  assert.equal(mobile.width, 390); assert(!mobile.overflow); assert(mobile.star > 10); assert(mobile.native > 100);
  console.log('MEASURE mobile ' + JSON.stringify(mobile));
  shot('mobile-hierarchy-root-star');
  click('[data-open-owner=office]');
  run('wait', '--fn', '!document.body.classList.contains("sidebar-open")');
  console.log('PASS: 390px mobile true-root star visible, no horizontal overflow, native list retained; grouped navigation closes native sidebar.');
  console.log('SCREENSHOTS ' + output);
} finally {
  try { control({ variant: 'normal', auth: true }); } catch (_) {}
  run('close');
}
