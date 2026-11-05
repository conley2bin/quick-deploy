const { test } = require('node:test');
const assert = require('node:assert/strict');
const Core = require('../web/core.js');
const OldCore = require('./core-9d.fixture.cjs');
const { createHash } = require('node:crypto');
const { readFileSync } = require('node:fs');
const rid = '12345678-1234-4123-8123-123456789012', oid = '87654321-1234-4123-8123-123456789012';
const ridB = '22345678-1234-4123-8123-123456789012', oidB = '97654321-1234-4123-8123-123456789012';
const op = (status = 'recovering', extra = {}) => ({ id: oid, request_id: rid, source_session_id: 'source', target_session_id: 'target', status, message: '合成状态', reviewed: false, can_continue_unreviewed: false, ...extra });
const sourceKey = source => 'key:source:' + encodeURIComponent(source);
function rig(post, raw, shared) {
  const values = shared || new Map(raw ? [['key', raw]] : []), calls = [], renders = [];
  const storage = { getItem: k => values.get(k), setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k), get length() { return values.size; }, key: i => [...values.keys()][i] };
  let seq = 0;
  const c = Core.createController({ storage, key: 'key', uuid: () => seq++ ? ridB : rid, changed: s => renders.push(s), post: async (route, body) => { calls.push({ route, body }); return post(route, body); } });
  c.select('source');
  return { c, values, calls, storage, renders };
}
test('regression: one source reference must not globally suppress another start', async () => {
  const calls = [], values = new Map();
  const c = Core.createController({ key: 'key', storage: { getItem: k => values.get(k), setItem: (k,v) => values.set(k,v) }, uuid: () => rid,
    post: async (route, body) => { calls.push(body); return { operation: op('recovering', { source_session_id: body.source_session_id }) }; } });
  await c.start('source'); await c.start('other'); assert.equal(calls.length, 2);
});
test('A pending start never blocks B; late A cannot render or replace B', async () => {
  let resolveA;
  const r = rig((route, body) => body.source_session_id === 'source' ? new Promise(resolve => resolveA = resolve) : { operation: op('recovering', { id: oidB, request_id: ridB, source_session_id: 'other' }) });
  const a = r.c.start('source', 'private A'); r.c.select('other');
  assert.equal(r.c.view().busy, false); await r.c.start('other', 'private B');
  assert.equal(r.calls.length, 2); assert.notEqual(r.calls[0].body.request_id, r.calls[1].body.request_id);
  assert.equal(r.c.view().op.id, oidB); const rendered = r.renders.length;
  resolveA({ operation: op() }); await a;
  assert.equal(r.c.view().op.id, oidB); assert.equal(r.renders.length, rendered);
  r.c.select('source'); assert.equal(r.c.view().op.id, oid);
  assert.equal(r.values.size, 2); assert(![...r.values.values()].join('').includes('private'));
});
test('late A success cannot clear B unknown-start error or recovery UUID', async () => {
  let resolveA;
  const r = rig((route, body) => body.source_session_id === 'source' ? new Promise(resolve => resolveA = resolve) : Promise.reject(Error('B offline')));
  const a = r.c.start('source'); r.c.select('other'); await r.c.start('other');
  const before = r.c.view(), persisted = r.values.get(sourceKey('other'));
  resolveA({ operation: op() }); await a;
  assert.equal(r.c.view().error, before.error); assert.match(r.c.view().error, /结果未知/);
  assert.equal(r.values.get(sourceKey('other')), persisted); await r.c.start('other'); assert.equal(r.calls.length, 2);
});
test('prefix is from own namespace script and rejects cross origin', () => {
  assert.equal(Core.namespace('https://x/nested/__session_handoff__/a.hash.js', 'https://x'), 'https://x/nested/__session_handoff__/');
  assert.throws(() => Core.namespace('https://evil/__session_handoff__/asset.js', 'https://x'));
  assert.throws(() => Core.namespace('https://x/__other_feature__/asset.js', 'https://x'));
});
test('hash selection and navigation preserve opaque identifiers and other hash params', () => {
  assert.equal(Core.selected('#session=a%2Fb%20%26%E4%B8%AD'), 'a/b &中');
  for (const hash of ['', '#session=', '#session=a&session=b', '#session=%00', '#session=' + 'x'.repeat(513)]) assert.equal(Core.selected(hash), null);
  const u = new URL(Core.targetURL('https://x/p/?q=1#other=yes&session=old', 'new/&中'));
  assert.equal(Core.selected(u.hash), 'new/&中'); assert.equal(new URLSearchParams(u.hash.slice(1)).get('other'), 'yes');
});
test('multiple clicks produce one mutation and source-associated minimal reference persists', async () => {
  let resolve; const r = rig(() => new Promise(r => resolve = r));
  const first = r.c.start('source', 'private note'); await r.c.start('source');
  assert.equal(r.calls.length, 1); assert(!r.values.get(sourceKey('source')).includes('private'));
  resolve({ operation: op() }); await first;
  assert.deepEqual(JSON.parse(r.values.get(sourceKey('source'))), { operation_id: oid, source_session_id: 'source' });
});
test('uncertain start keeps same UUID on explicit recovery; no replay on reload; B allowed', async () => {
  const r = rig(() => { throw Error('offline'); }); await r.c.start('source', 'not retained');
  assert.match(r.c.view().error, /结果未知/);
  const next = rig((route, body) => ({ operation: body.source_session_id === 'other' ? op('recovering', { id: oidB, source_session_id: 'other' }) : op() }), null, r.values);
  assert.equal(next.calls.length, 0); await next.c.start('source'); assert.equal(next.calls.length, 0);
  next.c.select('other'); await next.c.start('other'); assert.equal(next.calls.length, 1);
  next.c.select('source'); await next.c.recover(); assert.deepEqual(next.calls[1], { route: 'start', body: { request_id: rid, source_session_id: 'source' } });
});
test('server coalesces another active request for the same source', async () => {
  const r = rig(() => ({ operation: op('recovering', { request_id: oid }) }));
  await r.c.start('source'); assert.equal(r.c.view().error, ''); assert.equal(r.c.view().op.id, oid);
});
test('status validates both source and operation identities', async () => {
  for (const status of Object.keys(Core.labels)) assert.equal(Core.operation(op(status)).status, status);
  assert.throws(() => Core.operation(op('invented')));
  for (const extra of [{ id: rid }, { source_session_id: 'other' }]) {
    const r = rig(() => ({ operation: op('ready', extra) }), null, new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]));
    await r.c.poll(); assert.match(r.c.view().error, /身份不匹配/); assert.equal(r.c.view().op, null);
  }
});
test('explicit unreviewed continuation is one-shot across reload', async () => {
  let fail = false;
  const post = route => { if (route === 'continue' && fail) throw Error('offline'); return { operation: op('unreviewed', { can_continue_unreviewed: true }) }; };
  const r = rig(post); await r.c.start('source'); fail = true; await r.c.continueUnreviewed();
  assert(r.c.view().uncertainContinue); await r.c.continueUnreviewed(); assert.equal(r.calls.length, 2);
  const next = rig(post, null, r.values); await next.c.poll(); await next.c.continueUnreviewed();
  assert.equal(next.calls.length, 1); assert(next.c.view().uncertainContinue);
  for (const extra of [{ can_continue_unreviewed: false }, { target_session_id: null }]) {
    const denied = rig(() => ({ operation: op('unreviewed', extra) })); await denied.c.start('source'); await denied.c.continueUnreviewed(); assert.equal(denied.calls.length, 1);
  }
});
test('definitive rejection only releases its own source', async () => {
  const r = rig((route, body) => { if (body.source_session_id === 'other') throw Object.assign(Error('not Pi'), { definitive: true }); return { operation: op() }; });
  await r.c.start('source'); r.c.select('other'); await r.c.start('other'); assert.equal(r.c.view().reference, null);
  r.c.select('source'); assert.equal(r.c.view().op.id, oid); assert(r.values.has(sourceKey('source')));
});
test('storage refusal prevents mutation without changing another source', async () => {
  assert.equal(Core.retained('{bad'), null); assert.equal(Core.retained(JSON.stringify({ operation_id: 'x'.repeat(10000) })), null);
  const r = rig(() => ({ operation: op() })); await r.c.start('source'); r.c.select('other');
  r.storage.setItem = () => { throw Error('quota'); }; await r.c.start('other');
  assert.equal(r.calls.length, 1); assert.match(r.c.view().error, /未发送/); assert(r.values.has(sourceKey('source')));
});
test('cleanup ignores late replies and clears all feature identities on logout', async () => {
  let resolve; const r = rig(() => new Promise(r => resolve = r));
  r.values.set(sourceKey('unloaded'), JSON.stringify({ operation_id: oidB, source_session_id: 'unloaded' }));
  const task = r.c.start('source'); r.c.stop(true); resolve({ operation: op() }); await task;
  assert.equal(r.values.size, 0); assert.equal(r.c.view().op, null); await r.c.recover(); assert.equal(r.calls.length, 1);
});
test('unknown A holds same-source lease but allows B; forget only terminal selected record', async () => {
  const r = rig((route, body) => ({ operation: body.source_session_id === 'other' ? op('ready', { id: oidB, source_session_id: 'other' }) : op('creation_unknown') }));
  await r.c.start('source'); r.c.forget(); await r.c.start('source'); assert.equal(r.calls.length, 1);
  r.c.select('other'); await r.c.start('other'); r.c.forget(); assert(!r.values.has(sourceKey('other'))); assert(r.values.has(sourceKey('source')));
});
test('request UUID works without secure-context randomUUID and is version 4', () => {
  assert.equal(Core.requestUUID({ getRandomValues: a => a.fill(255) }), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
});
test('in-flight status cannot erase a same-operation continuation persisted by another tab', async () => {
  let resolveStatus;
  const r = rig(route => route === 'status' ? new Promise(resolve => resolveStatus = resolve) : { operation: op('unreviewed', { can_continue_unreviewed: true }) });
  await r.c.start('source'); const polling = r.c.poll();
  r.values.set(sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source', pending_action: 'continue' }));
  resolveStatus({ operation: op('unreviewed', { can_continue_unreviewed: true }) }); await polling;
  await r.c.continueUnreviewed(); assert.equal(r.calls.filter(c=>c.route==='continue').length,0);
  assert.equal(JSON.parse(r.values.get(sourceKey('source'))).pending_action,'continue');
});
test('accepted continuation without state advancement cannot be sent twice', async () => {
  const r = rig(() => ({ operation: op('unreviewed', { can_continue_unreviewed: true }) }));
  await r.c.start('source'); await r.c.continueUnreviewed(); await r.c.continueUnreviewed();
  assert.equal(r.calls.filter(c => c.route === 'continue').length, 1);
  assert.equal(JSON.parse(r.values.get(sourceKey('source'))).pending_action, 'continue');
});
test('source/note bounds and invalid targets reject before action/navigation', async () => {
  const r = rig(() => ({ operation: op() }));
  await r.c.start('x'.repeat(513)); await r.c.start('source', 'x'.repeat(1001));
  assert.equal(r.calls.length, 0); assert.throws(() => Core.operation(op('ready', { target_session_id: 'source' })));
});
test('different-source tabs persist without map lost updates; refresh restores both', async () => {
  const shared = new Map(), a = rig(() => ({ operation: op() }), null, shared), b = rig(() => ({ operation: op('ready', { id: oidB, source_session_id: 'other' }) }), null, shared);
  await a.c.start('source'); b.c.select('other'); await b.c.start('other');
  const refresh = rig((route, body) => ({ operation: body.operation_id === oid ? op() : op('ready', { id: oidB, source_session_id: 'other' }) }), null, shared);
  await refresh.c.poll(); assert.equal(refresh.c.view().op.id, oid);
  refresh.c.select('other'); await refresh.c.poll(); assert.equal(refresh.c.view().op.id, oidB);
  refresh.c.forget(); assert(shared.has(sourceKey('source'))); assert(!shared.has(sourceKey('other')));
});
test('legacy pending source moves after successful write, never replays automatically', async () => {
  const raw = JSON.stringify({ request_id: rid, source_session_id: 'source' }), r = rig(() => ({ operation: op() }), raw);
  await r.c.migrateLegacy(); assert.equal(r.calls.length, 0); assert(!r.values.has('key')); assert(r.values.has(sourceKey('source')));
  const failed = rig(() => ({ operation: op() }), raw); failed.storage.setItem = () => { throw Error('quota'); };
  await failed.c.migrateLegacy(); assert.equal(failed.values.get('key'), raw);
  await failed.c.start('source'); assert.equal(failed.calls.length, 0); // Same A remains locked on failed migration.
  assert.equal(failed.values.get('key'), raw);
});
test('unsourced legacy operation asynchronously adopts returned A while B selected', async () => {
  let resolve; const raw = JSON.stringify({ operation_id: oid, pending_action: 'continue' });
  const r = rig(() => new Promise(r => resolve = r), raw); r.c.select('other'); const migration = r.c.migrateLegacy();
  assert.equal(r.c.view().reference, null); assert.equal(r.calls[0].route, 'status');
  resolve({ operation: op('unreviewed', { can_continue_unreviewed: true }) }); await migration;
  assert.equal(r.c.view().reference, null); assert.equal(r.values.has('key'), false);
  r.c.select('source'); assert.equal(r.c.view().op.source_session_id, 'source'); assert(r.c.view().uncertainContinue);
  await r.c.continueUnreviewed(); assert.equal(r.calls.length, 1);
});
test('failed legacy lookup/write keeps original and does not block unrelated B', async () => {
  const raw = JSON.stringify({ operation_id: oid });
  for (const writeFailure of [false, true]) {
    const r = rig(route => { if (route === 'status' && !writeFailure) throw Error('offline'); return { operation: op() }; }, raw);
    r.c.select('other'); if (writeFailure) r.storage.setItem = () => { throw Error('quota'); };
    await r.c.migrateLegacy(); assert.equal(r.values.get('key'), raw); assert.equal(r.c.view().reference, null);
    assert.match(r.c.view().legacyError, /旧接力/);
  }
});
test('migration never overwrites a different existing source reference', async () => {
  const raw = JSON.stringify({ operation_id: oid }), shared = new Map([['key', raw], [sourceKey('source'), JSON.stringify({ operation_id: oidB, source_session_id: 'source' })]]);
  const r = rig(() => ({ operation: op() }), null, shared); await r.c.migrateLegacy();
  assert.equal(shared.get('key'), raw); assert.equal(JSON.parse(shared.get(sourceKey('source'))).operation_id, oidB);
});

for (const stale of ['recovering', 'ready']) test('old ' + stale + ' observation cannot release another tab unknown Continue; fresh ready can', async () => {
  const shared = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' })]]);
  let release;
  const old = rig(() => new Promise(resolve => release = resolve), null, shared);
  const held = old.c.poll();
  const post = route => { if (route === 'continue') throw Error('lost Continue response'); return { operation: op('unreviewed', { can_continue_unreviewed: true }) }; };
  const actor = rig(post, null, shared); await actor.c.poll(); await actor.c.continueUnreviewed();
  assert.equal(JSON.parse(shared.get(sourceKey('source'))).pending_action, 'continue');
  release({ operation: op(stale) }); await held;
  assert.equal(JSON.parse(shared.get(sourceKey('source'))).pending_action, 'continue');
  assert(old.c.view().uncertainContinue);
  const reload = rig(post, null, shared); await reload.c.poll(); await reload.c.continueUnreviewed();
  assert.equal([...actor.calls, ...reload.calls].filter(c => c.route === 'continue').length, 1);
  assert(reload.c.view().uncertainContinue);
  const confirmed = rig(() => ({ operation: op('ready') }), null, shared); await confirmed.c.poll();
  assert.equal(confirmed.c.view().uncertainContinue, false); confirmed.c.forget();
  assert(!shared.has(sourceKey('source')));
});

test('old responses and definitive errors cannot replace or resurrect current same-source ownership', async () => {
  for (const outcome of ['ready', 'definitive-status', 'definitive-start']) {
    for (const replace of [false, true]) {
      const initial = outcome === 'definitive-start' ? {} : { operation_id: oid, source_session_id: 'source' };
      const shared = new Map(Object.keys(initial).length ? [[sourceKey('source'), JSON.stringify(initial)]] : []);
      let release, reject;
      const old = rig(() => new Promise((yes, no) => { release = yes; reject = no; }), null, shared);
      const held = outcome === 'definitive-start' ? old.c.start('source') : old.c.poll();
      const actor = rig(route => ({ operation: route === 'start' ? op('recovering', { id: oidB, request_id: ridB }) : op('ready') }), null, shared);
      if (outcome === 'definitive-start') shared.delete(sourceKey('source')); // A later tab explicitly retired its failed start.
      else { await actor.c.poll(); actor.c.forget(); }
      if (replace) await actor.c.start('source');
      const expected = shared.get(sourceKey('source'));
      if (outcome === 'ready') release({ operation: op('ready') });
      else reject(Object.assign(Error('rejected old request'), { definitive: true }));
      await held;
      assert.equal(shared.get(sourceKey('source')), expected, outcome + ', replacement=' + replace);
      assert.equal(old.c.view().reference?.operation_id, replace ? oidB : undefined);
      assert.equal(old.c.view().op, null); assert.equal(old.c.view().busy, false);
      assert.equal(old.c.view().error, '');
    }
  }
});

test('a renewed Continue marker cannot be resolved by an observation of the previous marker', async () => {
  for (const kind of ['status', 'continue']) {
    const shared = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source',
      ...(kind === 'status' ? { pending_action: 'continue', pending_id: rid } : {}) })]]);
    let release, reject;
    const old = rig(route => route === 'status' && kind === 'continue'
      ? { operation: op('unreviewed', { can_continue_unreviewed: true }) }
      : new Promise((yes, no) => { release = yes; reject = no; }), null, shared);
    if (kind === 'continue') await old.c.poll();
    const held = kind === 'status' ? old.c.poll() : old.c.continueUnreviewed();
    // Another tab definitively retires the old intent and persists a new one.
    shared.set(sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source', pending_action: 'continue', pending_id: ridB }));
    if (kind === 'status') release({ operation: op('ready') });
    else reject(Object.assign(Error('old Continue rejected'), { definitive: true }));
    await held;
    const saved = JSON.parse(shared.get(sourceKey('source')));
    assert.equal(saved.pending_action, 'continue'); assert.equal(saved.pending_id, ridB);
  }
});
test('fresh unknown delivery status retains explicit Continue uncertainty', async () => {
  for (const status of ['unreviewed', 'creation_unknown', 'delivery_unknown', 'interrupted']) {
    const shared = new Map([[sourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source', pending_action: 'continue' })]]);
    const r = rig(() => ({ operation: op(status) }), null, shared); await r.c.poll(); r.c.forget();
    assert(r.c.view().uncertainContinue); assert.equal(JSON.parse(shared.get(sourceKey('source'))).pending_action, 'continue');
  }
});

const chosenTarget = () => ({ model_provider: 'provider-b', model: 'shared', reasoning_effort: 'xhigh' });
const pendingV2 = (source = 'source', request_id = rid, target = chosenTarget()) => ({
  version: 2, pending_start: { request_id, source_session_id: source, target },
});
// Both shipped 9d and current Core use this exact key and one instrumented memory store.
const compatKey = 'codoxear:session-handoff:/demo/__session_handoff__/';
const compatSourceKey = source => compatKey + ':source:' + encodeURIComponent(source);
function compatStore() {
  const values = new Map(), writes = []; let reads = 0;
  const storage = { getItem(k) { reads++; return values.get(k); },
    setItem(k, v) { writes.push(['set', k, v]); values.set(k, v); },
    removeItem(k) { writes.push(['remove', k]); values.delete(k); },
    get length() { reads++; return values.size; }, key(i) { reads++; return [...values.keys()][i]; } };
  function tab(core, post = () => { throw Error('never arrived'); }, uuid = () => rid) {
    const calls = [];
    const c = core.createController({ storage, key: compatKey, uuid,
      post: async (route, body) => { calls.push({ route, body }); return post(route, body); } });
    c.select('source'); return { c, calls };
  }
  return { values, writes, tab, get reads() { return reads; } };
}
test('v2 never-arrived: immutable actual 9d cannot downgrade explicit intent; fresh Core recovers exact request', async () => {
  assert.equal(createHash('sha256').update(readFileSync(require.resolve('./core-9d.fixture.cjs'))).digest('hex'),
    'b0721a8e7cfe8ace9e5d83f9663d8f2abb63aeaa25605d9f3e9e19dd812a10ca');
  const s = compatStore(), writer = s.tab(Core);
  await writer.c.start('source', 'not persisted', chosenTarget());
  const raw = s.values.get(compatSourceKey('source')), old = s.tab(OldCore), writes = s.writes.length;
  old.c.select('source'); await old.c.start('source'); await old.c.recover(); await old.c.poll();
  await old.c.continueUnreviewed(); old.c.forget();
  const legacy = JSON.stringify({ request_id: rid, source_session_id: 'source' });
  s.values.set(compatKey, legacy); await old.c.migrateLegacy();
  const reads = s.reads; old.c.stop(false); assert.equal(s.reads, reads, 'pagehide does no storage I/O');
  assert.deepEqual(old.calls, [], 'actual 9d must never POST, especially targetless start');
  assert.equal(s.writes.length, writes); assert.equal(s.values.get(compatKey), legacy);
  assert.equal(s.values.get(compatSourceKey('source')), raw); assert.deepEqual(JSON.parse(raw), pendingV2());
  assert.equal(OldCore.retained(raw), null);
  const fresh = s.tab(Core, () => ({ operation: op() }));
  assert.deepEqual(fresh.calls, []); await fresh.c.recover();
  assert.deepEqual(fresh.calls, [{ route: 'start', body: { request_id: rid, source_session_id: 'source', target: chosenTarget() } }]);
});
test('v2 accepted-unknown: new recovery deduplicates one create; ACK restores old status-only observation', async () => {
  const s = compatStore(), accepted = new Map(); let creates = 0, loseReply = true;
  const server = (route, body) => {
    if (route === 'start') {
      assert.deepEqual(body, { source_session_id: 'source', request_id: rid, target: chosenTarget() });
      if (!accepted.has(body.request_id)) { accepted.set(body.request_id, body); creates++; }
      assert.deepEqual(body, accepted.get(body.request_id));
      if (loseReply) { loseReply = false; throw Error('accepted, response lost'); }
    } else { assert.equal(route, 'status'); assert.deepEqual(body, { operation_id: oid }); }
    return { operation: op('recovering', { target_settings: { ...chosenTarget(), origin: 'explicit' } }) };
  };
  const writer = s.tab(Core, server); await writer.c.start('source', '', chosenTarget());
  const raw = s.values.get(compatSourceKey('source')), old = s.tab(OldCore, server);
  await old.c.recover(); assert.deepEqual(old.calls, []); assert.equal(s.values.get(compatSourceKey('source')), raw);
  const fresh = s.tab(Core, server); await fresh.c.recover(); assert.equal(creates, 1);
  assert.deepEqual(JSON.parse(s.values.get(compatSourceKey('source'))), { operation_id: oid, source_session_id: 'source' });
  assert.equal(old.c.select('source'), true); await old.c.recover(); await old.c.start('source');
  assert.deepEqual(old.calls, [{ route: 'status', body: { operation_id: oid } }]); assert.equal(creates, 1);
});
test('v2 legacy compatibility: actual 9d inherit/operation/continue records read without migration writes', async () => {
  for (const shape of ['inherit', 'operation', 'continue']) {
    const s = compatStore(), old = s.tab(OldCore, route => {
      if (shape === 'inherit' || route === 'continue') throw Error('lost response');
      return { operation: op('unreviewed', { can_continue_unreviewed: true }) };
    });
    await old.c.start('source'); if (shape === 'continue') await old.c.continueUnreviewed();
    const raw = s.values.get(compatSourceKey('source')), writes = s.writes.length;
    const fresh = s.tab(Core), oldReload = s.tab(OldCore);
    assert.deepEqual(fresh.c.view().reference, oldReload.c.view().reference);
    assert.equal(s.writes.length, writes); assert.equal(s.values.get(compatSourceKey('source')), raw);
    if (shape === 'inherit') {
      const other = compatStore(), newWriter = other.tab(Core); await newWriter.c.start('source');
      assert.equal(other.values.get(compatSourceKey('source')), raw, 'inherit bytes are unchanged');
      await fresh.c.recover(); await oldReload.c.recover();
      assert.deepEqual(fresh.calls, oldReload.calls);
      assert.deepEqual(fresh.calls, [{ route: 'start', body: { request_id: rid, source_session_id: 'source' } }]);
    } else {
      await fresh.c.recover(); assert.deepEqual(fresh.calls, [{ route: 'status', body: { operation_id: oid } }]);
      if (shape === 'continue') { await fresh.c.continueUnreviewed(); assert(fresh.c.view().uncertainContinue); }
    }
  }
});
test('v2 malformed/unknown wrappers fail closed; unshipped plain-target b2 is rejected only by new Core', async () => {
  const valid = pendingV2(), pending = valid.pending_start;
  const malformed = [
    { ...valid, version: 3 }, { ...valid, version: '2' }, { pending_start: pending }, { version: 2 },
    { ...valid, extra: true }, { ...valid, pending_start: { ...pending, extra: true } },
    { ...valid, pending_start: { operation_id: oid, source_session_id: 'source', target: chosenTarget() } },
    { ...valid, pending_start: { ...pending, request_id: 'not-a-uuid' } },
    { ...valid, pending_start: { ...pending, request_id: [rid] } },
    { ...valid, pending_start: { ...pending, source_session_id: 'other' } },
    { ...valid, pending_start: { ...pending, source_session_id: 'x'.repeat(513) } },
    { ...valid, pending_start: { ...pending, source_session_id: 'bad\u0000source' } },
    ...[null, {}, { ...chosenTarget(), extra: true }, { ...chosenTarget(), reasoning_effort: 'HIGH' },
      { ...chosenTarget(), model: 'default' }, { ...chosenTarget(), model: ' leading' },
      { ...chosenTarget(), model_provider: 'x\u007f' }].map(target => ({ ...valid, pending_start: { ...pending, target } })),
  ];
  for (const value of malformed) for (const core of [Core, OldCore]) {
    const raw = JSON.stringify(value), s = compatStore(); s.values.set(compatSourceKey('source'), raw);
    const t = s.tab(core); assert.equal(t.c.select('source'), false);
    await t.c.start('source'); await t.c.recover(); await t.c.poll(); await t.c.continueUnreviewed(); t.c.forget();
    assert.deepEqual(t.calls, []); assert.deepEqual(s.writes, []);
    assert.equal(s.values.get(compatSourceKey('source')), raw); assert.match(t.c.view().error, /恢复/);
  }
  // A corrupted envelope with old-readable IDs is rejected by new Core, but cannot be protected from 9d.
  const hybrid = JSON.stringify({ ...valid, request_id: rid, source_session_id: 'source' });
  assert.equal(Core.retained(hybrid), null); assert.notEqual(OldCore.retained(hybrid), null);
  const b2 = JSON.stringify(pending), s = compatStore(); s.values.set(compatSourceKey('source'), b2);
  const fresh = s.tab(Core); await fresh.c.start('source'); await fresh.c.recover();
  assert.deepEqual(fresh.calls, []); assert.equal(Core.retained(b2), null); assert.deepEqual(s.writes, []);
  assert.equal(s.values.get(compatSourceKey('source')), b2);
  assert.deepEqual(OldCore.retained(b2), { request_id: rid, source_session_id: 'source' }, 'known unsafe negative baseline');
  const old = s.tab(OldCore); await old.c.recover();
  assert.deepEqual(old.calls, [{ route: 'start', body: { request_id: rid, source_session_id: 'source' } }]);
});
test('v2 source isolation: A explicit stays byte-identical while old and new B recover inheritance', async () => {
  const s = compatStore(), a = s.tab(Core), b = s.tab(Core, undefined, () => ridB);
  await a.c.start('source', '', chosenTarget()); const rawA = s.values.get(compatSourceKey('source'));
  b.c.select('other'); await b.c.start('other');
  const old = s.tab(OldCore); assert.equal(old.c.select('source'), false); await old.c.recover();
  assert.deepEqual(old.calls, []); assert.equal(old.c.select('other'), true); await old.c.recover();
  assert.deepEqual(old.calls, [{ route: 'start', body: { request_id: ridB, source_session_id: 'other' } }]);
  old.c.forget(); assert.equal(s.values.get(compatSourceKey('source')), rawA);
  const fresh = s.tab(Core); await fresh.c.recover(); fresh.c.select('other'); await fresh.c.recover();
  assert.deepEqual(fresh.calls, [
    { route: 'start', body: { request_id: rid, source_session_id: 'source', target: chosenTarget() } },
    { route: 'start', body: { request_id: ridB, source_session_id: 'other' } },
  ]);
  assert.equal(s.values.get(compatSourceKey('source')), rawA);
});
test('v2 old stale/null cache cannot poll/start/continue/forget/migrate; explicit logout still clears', async () => {
  for (const status of [null, 'ready', 'unreviewed']) {
    const s = compatStore();
    if (status) s.values.set(compatSourceKey('source'), JSON.stringify({ operation_id: oid, source_session_id: 'source' }));
    const old = s.tab(OldCore, () => ({ operation: op(status, { can_continue_unreviewed: true }) }));
    if (status) await old.c.poll();
    s.values.delete(compatSourceKey('source')); // Previously completed/retired record, still cached in old tab.
    const writer = s.tab(Core); await writer.c.start('source', '', chosenTarget());
    const raw = s.values.get(compatSourceKey('source')), writes = s.writes.length, posts = old.calls.length;
    await old.c.poll(); await old.c.recover(); await old.c.start('source');
    await old.c.continueUnreviewed(); old.c.forget(); assert.equal(old.c.select('source'), false);
    s.values.set(compatKey, JSON.stringify({ request_id: rid, source_session_id: 'source' }));
    await old.c.migrateLegacy(); const reads = s.reads; old.c.stop(false);
    assert.equal(s.reads, reads); assert.equal(old.calls.length, posts); assert.equal(s.writes.length, writes);
    assert.equal(s.values.get(compatSourceKey('source')), raw); assert.match(old.c.view().legacyError, /旧接力/);
    s.values.set(compatSourceKey('unloaded'), JSON.stringify(pendingV2('unloaded')));
    s.values.set('unrelated', 'kept'); old.c.stop(true);
    assert.deepEqual([...s.values], [['unrelated', 'kept']], 'intentional old logout clears all feature refs');
  }
});
test('v2 definite 400/422 clear only explicit pending; 503/network/abort keep exact original bytes', async () => {
  for (const outcome of [400, 422, 503, 'network', 'abort']) {
    const s = compatStore(); let raw;
    const t = s.tab(Core, () => {
      raw = s.values.get(compatSourceKey('source')); assert.deepEqual(JSON.parse(raw), pendingV2());
      throw outcome === 'abort' ? new DOMException('aborted', 'AbortError')
        : Object.assign(Error(String(outcome)), typeof outcome === 'number' ? { status: outcome, definitive: outcome < 500 } : {});
    });
    await t.c.start('source', '', chosenTarget());
    if (outcome === 400 || outcome === 422) {
      assert.equal(s.values.has(compatSourceKey('source')), false); assert.equal(t.c.view().reference, null);
    } else {
      assert.equal(s.values.get(compatSourceKey('source')), raw); assert.equal(s.writes.length, 1);
      await t.c.recover(); assert.equal(s.values.get(compatSourceKey('source')), raw); assert.equal(s.writes.length, 1);
    }
  }
});
test('v2 explicit pending cannot be overwritten by matching legacy base pending or operation', async () => {
  for (const legacy of [{ request_id: rid, source_session_id: 'source' },
    { operation_id: oid, source_session_id: 'source' }, { operation_id: oid }]) {
    const s = compatStore(), t = s.tab(Core, () => ({ operation: op() }));
    // First start never arrives; status-only migration of an unsourced legacy op may still observe its source.
    const writer = s.tab(Core); await writer.c.start('source', '', chosenTarget());
    const raw = s.values.get(compatSourceKey('source')), base = JSON.stringify(legacy), writes = s.writes.length;
    s.values.set(compatKey, base); await t.c.migrateLegacy();
    assert.equal(s.values.get(compatSourceKey('source')), raw); assert.equal(s.values.get(compatKey), base);
    assert.equal(s.writes.length, writes); assert.match(t.c.view().legacyError, /来源已有另一条恢复记录/);
    assert(t.calls.every(call => call.route === 'status'), 'migration never creates');
  }
});

test('explicit target is frozen and persisted before POST, then recovered unchanged after reload', async () => {
  const target = chosenTarget(); let release;
  const r = rig((route, body) => {
    assert.deepEqual(JSON.parse(r.values.get(sourceKey('source'))), pendingV2());
    return new Promise(resolve => release = resolve);
  });
  const pending = r.c.start('source', 'private', target);
  target.model = 'changed-after-click';
  assert.deepEqual(r.calls[0].body.target, chosenTarget());
  release(Promise.reject(Error('offline'))); await pending;
  const next = rig(() => ({ operation: op() }), null, r.values);
  assert.equal(next.calls.length, 0); await next.c.start('source', '', { ...chosenTarget(), model: 'another' });
  assert.equal(next.calls.length, 0); await next.c.recover();
  assert.deepEqual(next.calls[0].body, { source_session_id: 'source', request_id: rid, target: chosenTarget() });
});
test('malformed pending target keeps recovery lock and raw record without POST or inheritance', async () => {
  for (const target of [null, {}, { ...chosenTarget(), extra: true }, { ...chosenTarget(), reasoning_effort: 'HIGH' }, { ...chosenTarget(), model: ' leading' }]) {
    const raw = JSON.stringify({ source_session_id: 'source', request_id: rid, target });
    const r = rig(() => ({ operation: op() }), null, new Map([[sourceKey('source'), raw]]));
    await r.c.start('source'); await r.c.recover();
    assert.equal(r.calls.length, 0); assert.equal(r.values.get(sourceKey('source')), raw);
    assert.equal(r.c.view().invalidReference, true); assert.match(r.c.view().error, /恢复/);
  }
});
test('invalid explicit start values never persist or POST; legacy start has no target key', async () => {
  for (const target of [null, {}, { ...chosenTarget(), model: 'DEFAULT' }, { ...chosenTarget(), reasoning_effort: 'High' }, { ...chosenTarget(), model: 'x\u007f' }]) {
    const r = rig(() => ({ operation: op() })); await r.c.start('source', '', target);
    assert.equal(r.calls.length, 0); assert.equal(r.values.size, 0); assert.match(r.c.view().error, /模型/);
  }
  const legacy = rig(() => ({ operation: op() })); await legacy.c.start('source');
  assert.equal(Object.hasOwn(legacy.calls[0].body, 'target'), false);
});
test('target-only cross-tab replacement cannot accept old response under same request UUID', async () => {
  let release; const r = rig(() => new Promise(resolve => release = resolve));
  const pending = r.c.start('source', '', chosenTarget());
  const replacement = { source_session_id: 'source', request_id: rid, target: { ...chosenTarget(), model: 'replacement' } };
  r.values.set(sourceKey('source'), JSON.stringify(pendingV2('source', rid, replacement.target))); release({ operation: op() }); await pending;
  assert.equal(r.c.view().op, null); assert.deepEqual(r.c.view().reference, replacement);
});
test('optional frozen settings and asymmetric target checks validate strictly without runtime claims', () => {
  const target_settings = { ...chosenTarget(), origin: 'explicit' };
  const target_check = { result: 'effort_differs', stage: 'launch', observed: { ...chosenTarget(), reasoning_effort: 'high' } };
  assert.deepEqual(Core.operation(op('ready', { target_settings, target_check })).target_check, target_check);
  assert.equal(Core.operation(op()).target_settings, undefined);
  for (const value of [null, { ...target_settings, origin: 'current' }, { ...target_settings, model: '' }, { ...target_settings, extra: 1 }])
    assert.throws(() => Core.operation(op('ready', { target_settings: value })), /格式/);
  for (const value of [null, { ...target_check, stage: 'status' }, { ...target_check, result: 'verified' }, { ...target_check, observed: {} }, { ...target_check, extra: 1 }])
    assert.throws(() => Core.operation(op('ready', { target_check: value })), /格式/);
});
