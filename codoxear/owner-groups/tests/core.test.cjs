const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const Core = require('../web/core.js');
const fixture = JSON.parse(execFileSync('python3', [path.join(__dirname, 'fixture.py')], { encoding: 'utf8' }));
const tree = Core.forest(fixture.owners);
const rows = () => Core.catalog(fixture.sessions);

test('parent forest spans projects, not repos/scopes; unique mount and unresolved placeholder', () => {
  assert.deepEqual([...tree.roots].sort(), ['ops', 'other']);
  assert.equal(tree.children.get('office')[0], 'qa');
  assert(tree.children.get('ops').includes('web'));
  assert.deepEqual(tree.unresolved.get('owner:not-registered'), ['orphan']);
  assert(!tree.roots.includes('orphan'));
  const all = [...tree.roots, ...[...tree.children.values()].flat(), ...[...tree.unresolved.values()].flat()];
  assert.equal(all.length, 11); assert.equal(new Set(all).size, 11);
});

test('exact verified identity only; offline ancestor and ended owner retained', () => {
  const bound = Core.bindings(tree, rows());
  assert.equal(bound.get('office').session, 'demo-office-opaque-123');
  assert.equal(bound.get('legacy').session, 'demo-legacy');
  assert.equal(bound.get('ops').session, null);
  assert.equal(bound.get('kitchen').session, null);
  assert.equal(bound.get('other').session, null); // same thread_id but wrong backend
  assert.equal(bound.get('unknown').session, null);
  assert.equal(bound.get('collision-a').session, null);
  assert.match(bound.get('collision-a').reason, /身份冲突/);
  const changed = structuredClone(fixture.owners);
  const owner = changed.owners.find(o => o.id === 'office');
  owner.pi_session_id = null; owner.identity_source = 'unavailable';
  assert.equal(Core.bindings(Core.forest(changed), rows()).get('office').session, null);
});

test('duplicate API thread, launch/unbound log, rebinding never navigate by broker alone', () => {
  const duplicate = rows(); duplicate.push({ ...duplicate[0], session_id: 'second-broker' });
  assert.equal(Core.bindings(tree, duplicate).get('office').session, null);
  const launch = rows(); launch[0].bound = false;
  assert.equal(Core.bindings(tree, launch).get('office').session, null);
  const rebound = rows(); rebound[0].thread_id = 'pi-synthetic-unrelated';
  assert.equal(Core.bindings(tree, rebound).get('office').session, null);
});

test('bad graph, duplicate owner, unknown schema, API shape or duplicate broker fail closed', () => {
  for (const mutate of [
    f => { f.owners[0].parent = 'owner:' + f.owners[0].id; },
    f => { f.owners.push(f.owners[0]); },
    f => { f.owners[0].parent = 'owner: '; },
    f => { f.schema = 'unknown'; },
    f => { f.ok = false; },
    f => { f.owners[0].identity_source = 'intercom-status'; },
  ]) { const data = structuredClone(fixture.owners); mutate(data); assert.throws(() => Core.forest(data)); }
  assert.throws(() => Core.catalog({}));
  assert.throws(() => Core.catalog({sessions: [fixture.sessions.sessions[0], fixture.sessions.sessions[0]]}));
});

test('hash adapter preserves parameters and opaque route IDs without suffix parsing', () => {
  const id = 'opaque Pi + ? / # -123';
  const params = new URLSearchParams(Core.route('#view=chat&session=old', id).slice(1));
  assert.equal(params.get('view'), 'chat'); assert.equal(params.get('session'), id);
});

test('fixed team order; only descendants follow authoritative native ranks', () => {
  const bound = Core.bindings(tree, rows());
  bound.set('ops', { session: 'root-a' }); bound.set('other', { session: 'root-b' });
  const first = Core.groups(tree, bound, ['demo-office-opaque-123', 'demo-legacy', 'demo-web', 'root-b', 'root-a']);
  assert.deepEqual(first.teams.slice(0, 2).map(t => t.id), ['ops', 'other']);
  assert.deepEqual(first.teams[0].sessions.map(x => x.session), ['root-a', 'demo-office-opaque-123', 'demo-legacy', 'demo-web']);
  const next = Core.groups(tree, bound, ['root-b', 'demo-web', 'demo-office-opaque-123', 'demo-legacy', 'root-a'], first.order);
  assert.deepEqual(next.teams.slice(0, 2).map(t => t.id), ['ops', 'other']);
  assert.deepEqual(next.teams[0].sessions.map(x => x.session), ['root-a', 'demo-web', 'demo-office-opaque-123', 'demo-legacy']);
  const unbound = Core.groups(tree, Core.bindings(tree, rows()), ['demo-office-opaque-123']);
  assert.equal(unbound.teams[0].root, 'ops');
  assert.equal(unbound.teams[0].sessions[0].owner, 'office'); // no promoted root
  assert(unbound.teams.find(t => t.unresolved).root === null);
});

test('registration ordinal beats names; new roots append to saved order', () => {
  const changed = structuredClone(fixture.owners);
  changed.owners.find(o => o.id === 'ops').name = 'ZZZ';
  changed.owners.find(o => o.id === 'other').name = 'AAA';
  const newer = {...changed.owners[0], id:'new-root', parent:'user', registration_order:999};
  changed.owners.push(newer);
  const t = Core.forest(changed);
  assert.deepEqual(t.roots, ['ops', 'other', 'new-root']);
  const g = Core.groups(t, Core.bindings(t, rows()), [], ['other', 'ops']);
  assert.deepEqual(g.teams.filter(x => x.root).map(x => x.id), ['other', 'ops', 'new-root']);
});
