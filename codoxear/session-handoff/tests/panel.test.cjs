/* Controlled native replaceState-to-monitor gap: no selection timer runs before click. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount } = require('../web/panel.js');
const { actualNative, catalog, fakeDOM } = require('./target-model-fixture.cjs');
const rid = '12345678-1234-4123-8123-123456789012';
const ids = { A: '87654321-1234-4123-8123-123456789012', B: '97654321-1234-4123-8123-123456789012' };
const key = 'codoxear:session-handoff:/__session_handoff__/:source:';
const settle = () => new Promise(resolve => setImmediate(resolve));
async function fixture(t, status = 'unreviewed', references = true, options = {}) {
  const observers = [];
  global.MutationObserver = class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} disconnect() {} };
  const { nodes, Node } = fakeDOM();
  const root = new Node('root'), shell = new Node('shell'), main = new Node('main'), bar = new Node('bar'), details = new Node('details'), head = new Node('head');
  details.parentNode = bar; root.queries = { ':scope > .app': shell }; shell.queries = { ':scope > .main': main };
  main.queries = { ':scope > .topbar': bar }; bar.queries = { '.topActions > #diagBtn': details };
  const doc = new Node('doc'); Object.assign(doc, { hidden: false, head, body: new Node('body'), createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag),
    getElementById: id => id === 'root' ? root : nodes.find(n => n.id === id && n.isConnected) || null, querySelector: () => main });
  const values = new Map(references ? Object.entries(ids).map(([source, id]) => [key + source, JSON.stringify({ operation_id: id, source_session_id: source })]) : []);
  const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k), get length() { return values.size; }, key: i => [...values.keys()][i] };
  const timers = new Map(), calls = [], catalogCalls = [], navigations = [], writes = []; let seq = 0;
  const originalWrite = storage.setItem; storage.setItem = (k, v) => { writes.push([k, v]); originalWrite(k, v); };
  const native = options.native ? await actualNative(t, storage) : null;
  const win = new Node('win'); Object.assign(win, { document: doc, location: { origin: 'http://fixture', href: 'http://fixture/#session=A', hash: '#session=A', replace: href => navigations.push(href) },
    localStorage: storage, innerHeight: 800, crypto: require('node:crypto').webcrypto, setTimeout: (fn, ms) => { timers.set(++seq, { fn, ms }); return seq; }, clearTimeout: id => timers.delete(id), queueMicrotask,
    fetch: async (url, opts) => {
      if (!opts.body) { catalogCalls.push({ url, opts }); return options.catalogReply ? options.catalogReply(url, opts) : { ok: true, json: async () => catalog() }; }
      const route = url.pathname.split('/').pop(), body = JSON.parse(opts.body); calls.push({ route, body });
      if (options.postReply) return options.postReply(route, body, storage);
      const source = body.source_session_id || Object.keys(ids).find(s => ids[s] === body.operation_id);
      return { ok: true, json: async () => ({ operation: { id: ids[source], request_id: rid, source_session_id: source, target_session_id: 'T' + source,
        status, reviewed: status === 'ready', can_continue_unreviewed: status === 'unreviewed', ...options.operationFields } }) }; } });
  const instance = mount(win, 'http://fixture/__session_handoff__/app.js', () => {}, options.nativeLoader || (native ? async () => native : async () => { throw Error('native unavailable in legacy fixture'); })); t.after(() => instance.dispose()); await settle();
  const button = text => nodes.find(n => n.tag === 'button' && n.textContent === text);
  const choose = async source => { win.location.hash = '#session=' + source;
    const [id, timer] = [...timers].find(([, v]) => v.ms === 250); timers.delete(id); timer.fn(); await settle(); };
  // Populate B, then paint A; a later URL-only change intentionally skips monitor.
  await choose('B'); await choose('A');
  return { instance, nodes, doc, win, values, calls, catalogCalls, native, writes, observers, navigations, button, choose, status: nodes.find(n => n.role === 'status') };
}
for (const action of ['未校对，仍继续', '刷新接力状态', '收起记录', '打开新会话']) {
  test('visible A ' + action + ' cannot act on URL B before selection monitor', async t => {
    const r = await fixture(t, action === '收起记录' ? 'ready' : 'unreviewed');
    assert.match(r.status.textContent, /来源：A/);
    const node = action === '打开新会话' ? r.nodes.find(n => n.tag === 'a') : r.button(action);
    assert.equal(node.hidden, false); const before = r.calls.length;
    r.win.location.hash = '#session=B'; node.click(); await settle();
    assert.equal(r.calls.length, before); assert.equal(r.navigations.length, 0);
    assert(r.values.has(key + 'A')); assert(r.values.has(key + 'B')); assert.match(r.status.textContent, /来源：B/);
    node.click(); await settle();
    if (action === '未校对，仍继续') assert.deepEqual(r.calls.slice(before), [{ route: 'continue', body: { operation_id: ids.B } }]);
    if (action === '刷新接力状态') assert.deepEqual(r.calls.slice(before), [{ route: 'status', body: { operation_id: ids.B } }]);
    if (action === '收起记录') { assert(!r.values.has(key + 'B')); assert(r.values.has(key + 'A')); }
    if (action === '打开新会话') { assert.equal(r.navigations.length, 1); assert.match(r.navigations[0], /session=TB/); }
  });
}
test('same-source replaced reference makes displayed Forget repaint rather than delete replacement', async t => {
  const r = await fixture(t, 'ready'); const done = r.button('收起记录');
  r.values.set(key + 'A', JSON.stringify({ operation_id: ids.B, source_session_id: 'A' }));
  done.click(); await settle();
  assert.equal(JSON.parse(r.values.get(key + 'A')).operation_id, ids.B);
  assert.equal(r.instance.controller.view().op, null); assert.match(r.status.textContent, /请求结果未确认/);
});
test('stale A form reconfirms B with no A note; stale cancel cannot close B draft', async t => {
  const r = await fixture(t, 'recovering', false), form = r.nodes.find(n => n.tag === 'form'), note = r.nodes.find(n => n.tag === 'textarea');
  const open = r.nodes.find(n => n.id === 'handoffButton');
  open.click(); note.value = 'B draft'; note.fire('input'); // Initially A; explicitly fill B below.
  await r.choose('B'); open.click(); note.value = 'B only'; note.fire('input');
  await r.choose('A'); note.value = 'A only'; note.fire('input');
  r.win.location.hash = '#session=B'; r.button('取消').click();
  assert.equal(form.hidden, false); assert.equal(note.value, 'B only');
  await r.choose('A'); r.win.location.hash = '#session=B'; form.fire('submit'); await settle();
  assert.equal(r.calls.length, 0); assert.equal(note.value, 'B only');
  assert(r.nodes.some(n => n.textContent === '来源已切换，请再次确认：B'));
  form.fire('submit'); await settle(); assert.equal(r.calls.length, 1);
  assert.equal(r.calls[0].body.source_session_id, 'B'); assert.equal(r.calls[0].body.note, 'B only');
});
test('target link checks current reference even while an old status request is busy', async t => {
  const r = await fixture(t, 'ready'), link = r.nodes.find(n => n.tag === 'a');
  let release;
  r.win.fetch = () => new Promise(resolve => release = resolve);
  const held = r.instance.controller.poll();
  r.values.set(key + 'A', JSON.stringify({ operation_id: ids.B, source_session_id: 'A' }));
  link.click(); assert.equal(r.navigations.length, 0); assert.equal(link.hidden, true);
  release({ ok: true, json: async () => ({ operation: { id: ids.A, request_id: rid, source_session_id: 'A', target_session_id: 'TA',
    status: 'ready', reviewed: true, can_continue_unreviewed: false } }) });
  await held; assert.equal(r.instance.controller.view().op, null);
  assert.equal(JSON.parse(r.values.get(key + 'A')).operation_id, ids.B);
});
test('stale topbar and note input repaint without opening or contaminating B draft', async t => {
  const r = await fixture(t, 'recovering', false), open = r.nodes.find(n => n.id === 'handoffButton');
  const form = r.nodes.find(n => n.tag === 'form'), note = r.nodes.find(n => n.tag === 'textarea');
  r.win.location.hash = '#session=B'; open.click(); assert.equal(form.hidden, true);
  open.click(); assert.equal(form.hidden, false);
  await r.choose('A'); open.click();
  r.win.location.hash = '#session=B'; note.value = 'A private input'; note.fire('input');
  assert.equal(note.value, ''); form.fire('submit'); await settle();
  assert.equal(r.calls.length, 1); assert.equal(r.calls[0].body.source_session_id, 'B'); assert.equal(r.calls[0].body.note, undefined);
});
test('visible A retry cannot replay B pending start; explicit repainted B retry keeps its UUID', async t => {
  const r = await fixture(t), retry = r.button('刷新接力状态');
  r.values.set(key + 'B', JSON.stringify({ request_id: rid, source_session_id: 'B' }));
  await r.choose('B'); await r.choose('A'); const before = r.calls.length;
  r.win.location.hash = '#session=B'; retry.click(); await settle();
  assert.equal(r.calls.length, before); assert.equal(retry.textContent, '核对原请求');
  retry.click(); await settle();
  assert.deepEqual(r.calls.slice(before), [{ route: 'start', body: { request_id: rid, source_session_id: 'B' } }]);
  assert(![...r.values.values()].join('').includes('note'));
});

const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };
const node = (r, id) => r.doc.getElementById(id);
const form = r => r.nodes.find(n => n.tag === 'form' && n.isConnected);
async function openPicker(r) { node(r, 'handoffButton').click(); await settle(); }
function pick(r, provider, model) {
  node(r, 'handoffModelInput').click();
  node(r, 'handoffModelMenu').children.find(n => n.role === 'option' && n.title === `${model} · ${provider}`).click();
}
test('panel actual-native choice freezes before POST and same-source lock prevents retargeting', async t => {
  const held = deferred(), target = { model_provider: 'provider-b', model: 'shared', reasoning_effort: 'high' };
  const r = await fixture(t, 'recovering', false, { native: true, postReply: (route, body, storage) => {
    assert.deepEqual(JSON.parse(storage.getItem(key + 'A')).target, target); return held.promise;
  } });
  await openPicker(r); assert.equal(r.catalogCalls.length, 1);
  const { url, opts } = r.catalogCalls[0]; assert.equal(url.pathname, '/api/sessions');
  assert.equal(opts.cache, 'no-store'); assert.equal(opts.credentials, 'same-origin'); assert.equal(opts.redirect, 'error');
  pick(r, 'provider-b', 'shared'); const input = node(r, 'handoffModelInput'); input.click();
  const staleRow = node(r, 'handoffModelMenu').children.find(n => n.title === 'restricted · provider-b');
  form(r).fire('submit'); assert.equal(r.calls.length, 1); assert.deepEqual(r.calls[0].body.target, target);
  staleRow.click(); node(r, 'handoffInheritModel').click(); form(r).fire('submit'); assert.equal(r.calls.length, 1);
  assert.deepEqual(JSON.parse(r.values.get(key + 'A')).target, target);
  assert.equal(node(r, 'handoffModelMenu').hidden, true);
  assert.equal(r.writes.filter(([k]) => !k.startsWith('codoxear:session-handoff:')).length, 0);
  held.resolve({ ok: false, status: 500, json: async () => ({ message: 'offline' }) }); await settle();
  assert.deepEqual(JSON.parse(r.values.get(key + 'A')).target, target);
});
test('A choice stays A; stale menu/submit repaints B and deliberate B submit inherits', async t => {
  const r = await fixture(t, 'recovering', false, { native: true }); await openPicker(r); pick(r, 'provider-b', 'shared');
  node(r, 'handoffModelInput').click(); const staleRow = node(r, 'handoffModelMenu').children.find(n => n.title === 'restricted · provider-b');
  r.win.location.hash = '#session=B'; staleRow.click(); await settle();
  assert.equal(r.calls.length, 0); await openPicker(r); assert.match(node(r, 'handoffModelSummary').textContent, /沿用原会话设置/);
  await r.choose('A'); await settle(); assert.match(node(r, 'handoffModelSummary').textContent, /shared · provider-b/);
  r.win.location.hash = '#session=B'; form(r).fire('submit'); await settle(); assert.equal(r.calls.length, 0);
  form(r).fire('submit'); await settle(); assert.equal(r.calls.length, 1);
  assert.equal(r.calls[0].body.source_session_id, 'B'); assert.equal(Object.hasOwn(r.calls[0].body, 'target'), false);
});
test('dirty query blocks submit; cancellation resets choice and every reopen refreshes native catalog', async t => {
  const r = await fixture(t, 'recovering', false, { native: true }); await openPicker(r); pick(r, 'provider-b', 'shared');
  const input = node(r, 'handoffModelInput'); input.value = 'typed not selected'; input.fire('input');
  assert.equal(r.button('确认接力').disabled, true); form(r).fire('submit'); await settle(); assert.equal(r.calls.length, 0);
  r.button('取消').click(); await openPicker(r); assert.equal(r.catalogCalls.length, 2);
  assert.match(node(r, 'handoffModelSummary').textContent, /将沿用原会话设置/);
  form(r).fire('submit'); await settle(); assert.equal(Object.hasOwn(r.calls[0].body, 'target'), false);
});
test('held A catalog and async import cannot paint B, resurrect cancelled forms or survive disposal', async t => {
  const a = deferred(), b = deferred(), module = deferred();
  const r = await fixture(t, 'recovering', false, { native: true, nativeLoader: () => module.promise,
    catalogReply: () => (r.catalogCalls.length === 1 ? a.promise : b.promise) });
  await openPicker(r); await r.choose('B'); await openPicker(r);
  assert.equal(r.catalogCalls[0].opts.signal.aborted, true);
  module.resolve(r.native); a.resolve({ ok: true, json: async () => catalog() }); await settle();
  assert.equal(node(r, 'handoffModelInput'), null);
  b.resolve({ ok: true, json: async () => catalog() }); await settle();
  assert.match(node(r, 'handoffModelSummary').textContent, /沿用原会话设置/);
  r.button('取消').click(); r.instance.dispose(); assert.equal(node(r, 'handoffMenuHost'), null);
  assert.equal(r.calls.length, 0);
});
test('catalog/import failure is honest inherit-only; warning key never offers explicit fallback', async t => {
  const r = await fixture(t, 'recovering', false, { native: true, catalogReply: async () => {
    const data = catalog(); data.new_session_defaults.warnings = { pi: '' }; return { ok: true, json: async () => data };
  } });
  await openPicker(r); assert.equal(node(r, 'handoffModelInput').disabled, true);
  assert.match(node(r, 'handoffModelSummary').textContent, /无法验证.*仅可沿用/);
  form(r).fire('submit'); await settle(); assert.equal(Object.hasOwn(r.calls[0].body, 'target'), false);
});
test('catalog auth loss disposes menus, aborts pending requests and prevents subsequent POST', async t => {
  const held = deferred(); const r = await fixture(t, 'recovering', false, { native: true, catalogReply: () => held.promise });
  await openPicker(r); held.resolve({ ok: false, status: 401, json: async () => ({}) }); await settle();
  assert.equal(r.instance.controller.view().stopped, true); assert.equal(node(r, 'handoffMenuHost'), null);
  assert.equal(r.catalogCalls[0].opts.signal.aborted, true); assert.equal(r.calls.length, 0);
  assert.match(node(r, 'handoffCompatibility').textContent, /登录已失效/);
});
test('remount keeps only one menu host and restores the same-source draft, not old menu callbacks', async t => {
  const r = await fixture(t, 'recovering', false, { native: true }); await openPicker(r); pick(r, 'provider-b', 'shared');
  node(r, 'handoffModelInput').click(); const staleRow = node(r, 'handoffModelMenu').children.find(n => n.title === 'restricted · provider-b');
  node(r, 'handoffButton').isConnected = false; r.observers[0].fn(); await settle();
  assert.equal(r.nodes.filter(n => n.id === 'handoffMenuHost' && n.isConnected).length, 1);
  staleRow.click(); assert.match(node(r, 'handoffModelSummary').textContent, /shared · provider-b/);
  form(r).fire('submit'); await settle(); assert.equal(r.calls[0].body.target.model, 'shared');
});
test('malformed saved target leaves form locked and cannot POST or quietly use inheritance', async t => {
  const r = await fixture(t, 'recovering', false, { native: true });
  const raw = JSON.stringify({ request_id: rid, source_session_id: 'A', target: { model: 'partial' } }); r.values.set(key + 'A', raw);
  node(r, 'handoffButton').click(); await settle(); form(r).fire('submit'); await r.instance.controller.recover();
  assert.equal(form(r).hidden, true); assert.equal(r.button('确认接力').disabled, true);
  assert.equal(r.calls.length, 0); assert.equal(r.values.get(key + 'A'), raw);
});
test('server frozen settings and sticky differences are labelled as observations without effective-settings claims', async t => {
  const expected = { model_provider: 'provider-b', model: 'shared', reasoning_effort: 'xhigh' };
  for (const result of ['model_differs', 'effort_differs', 'not_contradicted']) {
    const r = await fixture(t, 'ready', true, { operationFields: {
      target_settings: { ...expected, origin: 'inherited' }, target_check: { result, stage: 'named', observed: { ...expected, model: result === 'model_differs' ? 'other' : expected.model, reasoning_effort: 'high' } },
    } });
    assert.match(r.status.textContent, /沿用原会话列表设置.*shared · provider-b · xhigh/);
    const check = node(r, 'handoffTargetCheck');
    if (result !== 'not_contradicted') { assert.match(check.textContent, /接力期间列表曾报告.*high.*与创建选择.*xhigh.*不同/); }
    assert.equal(check.role, result === 'model_differs' ? 'alert' : 'status');
    assert.doesNotMatch(r.status.textContent + check.textContent, /已生效|verified|被.*调整/);
  }
});
