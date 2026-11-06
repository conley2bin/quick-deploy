const { test } = require('node:test');
const assert = require('node:assert/strict');
const Core = require('../web/core.js');
const rid = '12345678-1234-4123-8123-123456789012', oid = '87654321-1234-4123-8123-123456789012';
const ridB = '22345678-1234-4123-8123-123456789012', oidB = '97654321-1234-4123-8123-123456789012';
const op = (status = 'waiting_source', extra = {}) => ({ id: oid, request_id: rid, source_session_id: 'source', target_session_id: null,
  status, message: '合成状态', ...extra });
const target = () => ({ model_provider: 'provider-b', model: 'shared', reasoning_effort: 'xhigh' });
const sourceKey = source => 'key:source:' + encodeURIComponent(source);
function rig(post, values = new Map()) {
  const calls = [], renders = [];
  const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k), get length() { return values.size; }, key: i => [...values.keys()][i] };
  let seq = 0;
  const c = Core.createController({ storage, key: 'key', uuid: () => seq++ ? ridB : rid, changed: s => renders.push(s),
    post: async (route, body) => { calls.push({ route, body }); return post(route, body); } });
  c.select('source'); return { c, values, calls, storage, renders };
}
test('only the four handover states validate and only waiting/creating poll', () => {
  assert.deepEqual([...Core.active], ['waiting_source', 'creating']);
  for (const status of Object.keys(Core.labels)) assert.equal(Core.operation(op(status)).status, status);
  for (const status of ['ready', 'unreviewed', 'creation_unknown', 'interrupted']) assert.throws(() => Core.operation(op(status)));
  assert.throws(() => Core.operation(op('completed', { target_session_id: 'source' })));
});
test('namespace selection and navigation retain opaque IDs and prefixes', () => {
  assert.equal(Core.namespace('https://x/prefix/__session_handoff__/app.js?v=1', 'https://x'), 'https://x/prefix/__session_handoff__/');
  assert.throws(() => Core.namespace('https://evil/__session_handoff__/app.js', 'https://x'));
  assert.equal(Core.selected('#session=a%2Fb%20%26%E4%B8%AD'), 'a/b &中');
  for (const hash of ['', '#session=', '#session=a&session=b', '#session=%00']) assert.equal(Core.selected(hash), null);
  const u = new URL(Core.targetURL('https://x/p/?q=1#other=yes&session=old', 'new/&中'));
  assert.equal(Core.selected(u.hash), 'new/&中'); assert.equal(new URLSearchParams(u.hash.slice(1)).get('other'), 'yes');
  assert.equal(Core.requestUUID({ getRandomValues: a => a.fill(255) }), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
});
test('multiple clicks send once and persist only source/request or operation reference', async () => {
  let release; const r = rig(() => new Promise(resolve => release = resolve));
  const first = r.c.start('source'); await r.c.start('source'); assert.equal(r.calls.length, 1);
  assert.deepEqual(JSON.parse(r.values.get(sourceKey('source'))), { source_session_id: 'source', request_id: rid });
  release({ operation: op() }); await first;
  assert.deepEqual(JSON.parse(r.values.get(sourceKey('source'))), { operation_id: oid, source_session_id: 'source' });
});
test('A pending never blocks B and a late A reply cannot repaint B', async () => {
  let release;
  const r = rig((route, body) => body.source_session_id === 'source' ? new Promise(resolve => release = resolve)
    : { operation: op('creating', { id: oidB, request_id: ridB, source_session_id: 'other' }) });
  const a = r.c.start('source'); r.c.select('other'); await r.c.start('other');
  assert.equal(r.calls.length, 2); assert.notEqual(r.calls[0].body.request_id, r.calls[1].body.request_id);
  const rendered = r.renders.length; release({ operation: op() }); await a;
  assert.equal(r.renders.length, rendered); assert.equal(r.c.view().op.id, oidB);
  r.c.select('source'); assert.equal(r.c.view().op.id, oid); assert.equal(r.values.size, 2);
});
test('lost start has no automatic replay; explicit query retains UUID and exact frozen model', async () => {
  const r = rig(() => { throw Error('offline'); }), chosen = target();
  await r.c.start('source', chosen); chosen.model = 'mutated';
  const raw = r.values.get(sourceKey('source'));
  assert.deepEqual(JSON.parse(raw), { version: 2, pending_start: { request_id: rid, source_session_id: 'source', target: target() } });
  const reload = rig(() => ({ operation: op() }), r.values); assert.equal(reload.calls.length, 0);
  await reload.c.start('source'); assert.equal(reload.calls.length, 0);
  await reload.c.recover(); assert.deepEqual(reload.calls, [{ route: 'start', body: { request_id: rid, source_session_id: 'source', target: target() } }]);
});
test('busy-source rejection releases local pending and shows its reason', async () => {
  const r = rig(() => { throw Object.assign(Error('源会话忙，稍后再试'), { status: 409, definitive: true }); });
  await r.c.start('source'); assert.equal(r.c.view().reference, null);
  assert.equal(r.c.view().error, '源会话忙，稍后再试'); assert.equal(r.values.size, 0);
});
test('malformed explicit targets never fall back to inheritance', async () => {
  for (const value of [null, {}, { ...target(), model: 'DEFAULT' }, { ...target(), reasoning_effort: 'High' }]) {
    const r = rig(() => ({ operation: op() })); await r.c.start('source', value);
    assert.equal(r.calls.length, 0); assert.equal(r.values.size, 0);
  }
  const r = rig(() => ({ operation: op() })); await r.c.start('source'); assert.equal(Object.hasOwn(r.calls[0].body, 'target'), false);
});
test('storage refusal prevents mutation and malformed stored references stay locked', async () => {
  const r = rig(() => ({ operation: op() })); r.storage.setItem = () => { throw Error('quota'); };
  await r.c.start('source'); assert.equal(r.calls.length, 0); assert.match(r.c.view().error, /未发送/);
  const raw = JSON.stringify({ request_id: rid, source_session_id: 'source', target: {} });
  r.values.set(sourceKey('source'), raw); await r.c.start('source'); await r.c.recover();
  assert.equal(r.calls.length, 0); assert.equal(r.values.get(sourceKey('source')), raw);
});
test('status checks both operation and source identity', async () => {
  for (const fields of [{ id: oidB }, { source_session_id: 'other' }]) {
    const r = rig(() => ({ operation: op('completed', fields) }), new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]));
    await r.c.poll(); assert.match(r.c.view().error, /身份不匹配/); assert.equal(r.c.view().op, null);
  }
});
test('failed/complete can be dismissed, active operation cannot, other source survives', async () => {
  let status = 'waiting_source'; const r = rig(() => ({ operation: op(status) }));
  await r.c.start('source'); r.c.forget(); assert(r.values.has(sourceKey('source')));
  r.values.set(sourceKey('other'), JSON.stringify({ operation_id: oidB, source_session_id: 'other' }));
  status = 'failed'; await r.c.poll(); r.c.forget(); assert(!r.values.has(sourceKey('source'))); assert(r.values.has(sourceKey('other')));
});
test('an ignored old server operation is released after authenticated 404 without new mutation', async () => {
  const values = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]);
  const r = rig(() => { throw Object.assign(Error('未找到接力操作'), { status: 404, definitive: true }); }, values);
  await r.c.poll(); assert.equal(r.values.size, 0); assert.equal(r.c.view().reference, null);
  assert.deepEqual(r.calls, [{ route: 'status', body: { operation_id: oid } }]);
});
test('late status cannot replace or resurrect a different current source reference', async () => {
  let release; const values = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]);
  const r = rig(() => new Promise(resolve => release = resolve), values); const held = r.c.poll();
  const replacement = JSON.stringify({ operation_id: oidB, source_session_id: 'source' }); values.set(sourceKey('source'), replacement);
  release({ operation: op('completed') }); await held; assert.equal(values.get(sourceKey('source')), replacement); assert.equal(r.c.view().op, null);
});
test('logout clears feature keys and ignores replies; pagehide keeps pending reference', async () => {
  let release; const r = rig(() => new Promise(resolve => release = resolve)); const held = r.c.start('source');
  r.c.stop(true); release({ operation: op() }); await held; assert.equal(r.values.size, 0); assert.equal(r.c.view().op, null);
  const other = rig(() => { throw Error('offline'); }); await other.c.start('source'); const raw = other.values.get(sourceKey('source'));
  other.c.stop(false); assert.equal(other.values.get(sourceKey('source')), raw);
});
