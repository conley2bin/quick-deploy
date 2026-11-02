/* Controlled native replaceState-to-monitor gap: no selection timer runs before click. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount } = require('../web/panel.js');
const rid = '12345678-1234-4123-8123-123456789012';
const ids = { A: '87654321-1234-4123-8123-123456789012', B: '97654321-1234-4123-8123-123456789012' };
const key = 'codoxear:session-handoff:/__session_handoff__/:source:';
const settle = () => new Promise(resolve => setImmediate(resolve));
async function fixture(t, status = 'unreviewed', references = true) {
  global.MutationObserver = class { observe() {} disconnect() {} };
  const nodes = [];
  class Node {
    constructor(tag) { this.tag = tag; this.events = {}; this.children = []; nodes.push(this); }
    setAttribute(k, v) { this[k] = v; }
    append(...ns) { this.children.push(...ns); for (const n of ns) n.parentNode = this; }
    after(n) { n.parentNode = this.parentNode; }
    remove() {} focus() {}
    addEventListener(k, f) { (this.events[k] ||= []).push(f); }
    querySelector(s) { return this.queries?.[s] || null; }
    fire(k, extra = {}) { for (const f of this.events[k] || []) f({ preventDefault() {}, target: this, ...extra }); }
    click() { this.fire('click'); }
  }
  const root = new Node('root'), shell = new Node('shell'), main = new Node('main'), bar = new Node('bar'), details = new Node('details'), head = new Node('head');
  details.parentNode = bar; root.queries = { ':scope > .app': shell }; shell.queries = { ':scope > .main': main };
  main.queries = { ':scope > .topbar': bar }; bar.queries = { '.topActions > #diagBtn': details };
  const doc = new Node('doc'); Object.assign(doc, { hidden: false, head, createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag),
    getElementById: id => id === 'root' ? root : nodes.find(n => n.id === id) || null, querySelector: () => main });
  const values = new Map(references ? Object.entries(ids).map(([source, id]) => [key + source, JSON.stringify({ operation_id: id, source_session_id: source })]) : []);
  const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k), get length() { return values.size; }, key: i => [...values.keys()][i] };
  const timers = new Map(), calls = [], navigations = []; let seq = 0;
  const win = new Node('win'); Object.assign(win, { document: doc, location: { origin: 'http://fixture', href: 'http://fixture/#session=A', hash: '#session=A', replace: href => navigations.push(href) },
    localStorage: storage, crypto: require('node:crypto').webcrypto, setTimeout: (fn, ms) => { timers.set(++seq, { fn, ms }); return seq; }, clearTimeout: id => timers.delete(id), queueMicrotask,
    fetch: async (url, opts) => { const route = url.pathname.split('/').pop(), body = JSON.parse(opts.body); calls.push({ route, body });
      const source = body.source_session_id || Object.keys(ids).find(s => ids[s] === body.operation_id);
      return { ok: true, json: async () => ({ operation: { id: ids[source], request_id: rid, source_session_id: source, target_session_id: 'T' + source,
        status, reviewed: status === 'ready', can_continue_unreviewed: status === 'unreviewed' } }) }; } });
  const instance = mount(win, 'http://fixture/__session_handoff__/app.js'); t.after(() => instance.dispose()); await settle();
  const button = text => nodes.find(n => n.tag === 'button' && n.textContent === text);
  const choose = async source => { win.location.hash = '#session=' + source;
    const [id, timer] = [...timers].find(([, v]) => v.ms === 250); timers.delete(id); timer.fn(); await settle(); };
  // Populate B, then paint A; a later URL-only change intentionally skips monitor.
  await choose('B'); await choose('A');
  return { instance, nodes, win, values, calls, navigations, button, choose, status: nodes.find(n => n.role === 'status') };
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
