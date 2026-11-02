const { test } = require('node:test');
const assert = require('node:assert/strict');
const Core = require('../web/core.js');
const rid = '12345678-1234-4123-8123-123456789012', oid = '87654321-1234-4123-8123-123456789012';
const op = (status = 'recovering', extra = {}) => ({ id: oid, request_id: rid, source_session_id: 'source', target_session_id: 'target', status, message: '合成状态', reviewed: false, can_continue_unreviewed: false, ...extra });
function rig(post, raw) {
  const values = new Map(raw ? [['key', raw]] : []), calls = [];
  const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) };
  const c = Core.createController({ storage, key: 'key', uuid: () => rid, post: async (route, body) => { calls.push({ route, body }); return post(route, body); } });
  return { c, values, calls, storage };
}
test('prefix is from own namespace script and rejects cross origin', () => {
  assert.equal(Core.namespace('https://x/nested/__session_handoff__/a.hash.js', 'https://x'), 'https://x/nested/__session_handoff__/');
  assert.equal(Core.namespace('https://x/__session_handoff__/asset.js', 'https://x'), 'https://x/__session_handoff__/');
  assert.throws(() => Core.namespace('https://evil/__session_handoff__/asset.js', 'https://x'));
  assert.throws(() => Core.namespace('https://x/__owner_groups__/asset.js', 'https://x'));
});
test('hash selection and navigation preserve opaque identifiers and other hash params', () => {
  assert.equal(Core.selected('#session=a%2Fb%20%26%E4%B8%AD'), 'a/b &中');
  for (const hash of ['', '#session=', '#session=a&session=b', '#session=%00', '#session=' + 'x'.repeat(513)]) assert.equal(Core.selected(hash), null);
  const u = new URL(Core.targetURL('https://x/p/?q=1#other=yes&session=old', 'new/&中'));
  assert.equal(Core.selected(u.hash), 'new/&中'); assert.equal(new URLSearchParams(u.hash.slice(1)).get('other'), 'yes');
});
test('multiple clicks produce one mutation and only operation reference persists', async () => {
  let resolve; const r = rig(() => new Promise(r => resolve = r));
  const first = r.c.start('source', 'private note'); await r.c.start('source');
  assert.equal(r.calls.length, 1); assert(!r.values.get('key').includes('private'));
  resolve({ operation: op() }); await first;
  assert.deepEqual(JSON.parse(r.values.get('key')), { operation_id: oid });
});
test('uncertain start restores the same request UUID; reload never sends automatically', async () => {
  const r = rig(() => { throw Error('offline'); }); await r.c.start('source', 'not retained');
  assert.match(r.c.view().error, /结果未知/);
  const next = rig(() => ({ operation: op() }), r.values.get('key'));
  assert.equal(next.calls.length, 0); await next.c.start('other'); assert.equal(next.calls.length, 0);
  await next.c.recover(); assert.deepEqual(next.calls[0], { route: 'start', body: { request_id: rid, source_session_id: 'source' } });
});
test('server coalesces another active request for the same source', async () => {
  const r = rig(() => ({ operation: op('recovering', { request_id: oid }) }));
  await r.c.start('source'); assert.equal(r.c.view().error, ''); assert.equal(r.c.view().op.id, oid);
});
test('status validates identity and every public stage', async () => {
  for (const status of Object.keys(Core.labels)) assert.equal(Core.operation(op(status)).status, status);
  assert.throws(() => Core.operation(op('invented')));
  const r = rig(() => ({ operation: op('ready', { id: rid }) }), JSON.stringify({ operation_id: oid }));
  await r.c.poll(); assert.match(r.c.view().error, /身份不匹配/); assert.equal(r.c.view().op, null);
});
test('explicit unreviewed continuation only; uncertain continue is blocked across reload', async () => {
  let fail = false;
  const post = route => { if (route === 'continue' && fail) throw Error('offline'); return { operation: op('unreviewed', { can_continue_unreviewed: true }) }; };
  const r = rig(post); await r.c.start('source'); fail = true; await r.c.continueUnreviewed();
  assert(r.c.view().uncertainContinue); await r.c.continueUnreviewed(); assert.equal(r.calls.length, 2);
  const next = rig(post, r.values.get('key')); await next.c.poll(); await next.c.continueUnreviewed();
  assert.equal(next.calls.length, 1); assert(next.c.view().uncertainContinue);
  for (const extra of [{ can_continue_unreviewed: false }, { target_session_id: null }]) {
    const denied = rig(() => ({ operation: op('unreviewed', extra) })); await denied.c.start('source'); await denied.c.continueUnreviewed(); assert.equal(denied.calls.length, 1);
  }
});
test('HTTP definitive rejection releases request, transport/server failures retain it', async () => {
  for (const definitive of [true, false]) {
    const r = rig(() => { throw Object.assign(Error('not Pi'), { definitive }); });
    await r.c.start('source'); assert.equal(Boolean(r.c.view().reference), !definitive);
  }
});
test('storage refusal prevents mutation; malformed local reference is discarded', async () => {
  assert.equal(Core.retained('{bad'), null); assert.equal(Core.retained(JSON.stringify({ operation_id: 'x'.repeat(10000) })), null);
  const r = rig(() => ({ operation: op() })); r.storage.setItem = () => { throw Error('quota'); };
  await r.c.start('source'); assert.equal(r.calls.length, 0); assert.match(r.c.view().error, /未发送/);
});
test('cleanup ignores late replies and removes identity on logout', async () => {
  let resolve; const r = rig(() => new Promise(r => resolve = r));
  const task = r.c.start('source'); r.c.stop(true); resolve({ operation: op() }); await task;
  assert.equal(r.values.size, 0); assert.equal(r.c.view().op, null); await r.c.recover(); assert.equal(r.calls.length, 1);
});
test('unknown mutations cannot be dismissed into an accidental recreate', async () => {
  const r = rig(() => ({ operation: op('creation_unknown') })); await r.c.start('source'); r.c.forget(); await r.c.start('source'); assert.equal(r.calls.length, 1);
});
test('request UUID works without secure-context randomUUID and is version 4', () => {
  assert.equal(Core.requestUUID({ getRandomValues: a => a.fill(255) }), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
});
test('accepted continuation without state advancement cannot be sent twice', async () => {
  const r = rig(() => ({ operation: op('unreviewed', { can_continue_unreviewed: true }) }));
  await r.c.start('source'); await r.c.continueUnreviewed(); await r.c.continueUnreviewed();
  assert.equal(r.calls.filter(c => c.route === 'continue').length, 1);
  assert.equal(JSON.parse(r.values.get('key')).pending_action, 'continue');
});
test('source/note bounds and invalid targets reject before action/navigation', async () => {
  const r = rig(() => ({ operation: op() }));
  await r.c.start('x'.repeat(513)); await r.c.start('source', 'x'.repeat(1001));
  assert.equal(r.calls.length, 0); assert.throws(() => Core.operation(op('ready', { target_session_id: 'source' })));
});
