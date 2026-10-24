/* Transport-neutral, dependency-free model. Never infer identity from display/cwd. */
(function (scope) {
  'use strict';
  const fail = (condition, message) => { if (!condition) throw new Error(message); };
  const nonempty = value => typeof value === 'string' && value.trim().length > 0;

  function forest(snapshot) {
    fail(snapshot?.schema === 'codoxear-owner-groups/v1' && snapshot.ok === true,
      snapshot?.ok === false ? '负责人目录不可用；原生会话列表仍可使用。' : '目录格式不兼容。');
    fail(Array.isArray(snapshot.owners) && snapshot.owners.length <= 500, '目录大小或格式不兼容。');
    const nodes = new Map(), children = new Map(), roots = [], unresolved = new Map();
    for (const owner of snapshot.owners) {
      fail(owner && nonempty(owner.id) && !nodes.has(owner.id) && nonempty(owner.name) &&
        nonempty(owner.role) && nonempty(owner.parent), '目录含重复或无效负责人。');
      fail(owner.registration_order === undefined || Number.isSafeInteger(owner.registration_order) && owner.registration_order >= 0,
        '目录登记顺序无效。');
      fail(['current-session', 'intercom-status', 'unavailable'].includes(owner.identity_source) &&
        (owner.pi_session_id === null || nonempty(owner.pi_session_id)) &&
        (owner.identity_source === 'current-session' ? nonempty(owner.pi_session_id) : owner.pi_session_id === null),
      '目录会话身份来源无效。');
      fail(['active', 'ended', 'handed-over', 'mixed', 'no-scope'].includes(owner.state) &&
        Array.isArray(owner.scopes) && owner.scopes.every(s => s && typeof s.task === 'string' &&
          typeof s.project === 'string' && ['active', 'ended', 'handed-over'].includes(s.status)), '目录职责格式无效。');
      nodes.set(owner.id, owner); children.set(owner.id, []);
    }
    for (const owner of nodes.values()) {
      if (owner.parent === 'user') roots.push(owner.id);
      else {
        fail(owner.parent.startsWith('owner:') && nonempty(owner.parent.slice(6)) &&
          owner.parent.slice(6) === owner.parent.slice(6).trim(), '目录上级格式无效。');
        const parent = owner.parent.slice(6);
        if (nodes.has(parent)) children.get(parent).push(owner.id);
        else {
          if (!unresolved.has(owner.parent)) unresolved.set(owner.parent, []);
          unresolved.get(owner.parent).push(owner.id);
        }
      }
    }
    const sort = ids => ids.sort((a, b) =>
      (nodes.get(a).registration_order ?? Number.MAX_SAFE_INTEGER) -
      (nodes.get(b).registration_order ?? Number.MAX_SAFE_INTEGER) || a.localeCompare(b));
    sort(roots); for (const ids of children.values()) sort(ids); for (const ids of unresolved.values()) sort(ids);
    const visited = new Set();
    function visit(id, depth) {
      fail(!visited.has(id) && depth <= 64, '目录存在循环或层级过深；未展示部分树。');
      visited.add(id); children.get(id).forEach(child => visit(child, depth + 1));
    }
    roots.forEach(id => visit(id, 1));
    for (const ids of unresolved.values()) ids.forEach(id => visit(id, 1));
    fail(visited.size === nodes.size, '目录存在循环或无法挂载的负责人。');
    return { nodes, children, roots, unresolved };
  }

  function catalog(payload) {
    fail(Array.isArray(payload?.sessions), '会话 API 格式不兼容；原生列表不受影响。');
    const ids = new Set();
    return payload.sessions.map(row => {
      fail(row && nonempty(row.session_id) && !ids.has(row.session_id), '会话 API 含重复或无效路由。');
      ids.add(row.session_id);
      // Retain only linkage. Paths, titles and transcript contents never leave this transform.
      return { session_id: row.session_id, thread_id: row.thread_id,
        pi: row.agent_backend === 'pi', bound: row.has_bound_log === true };
    });
  }

  function bindings(tree, sessions) {
    const claims = new Map(), threads = new Map();
    for (const owner of tree.nodes.values()) {
      if (owner.identity_source === 'current-session' && owner.pi_session_id) {
        if (!claims.has(owner.pi_session_id)) claims.set(owner.pi_session_id, []);
        claims.get(owner.pi_session_id).push(owner.id);
      }
    }
    for (const row of sessions) {
      if (row.pi && nonempty(row.thread_id)) {
        if (!threads.has(row.thread_id)) threads.set(row.thread_id, []);
        threads.get(row.thread_id).push(row);
      }
    }
    const result = new Map();
    for (const owner of tree.nodes.values()) {
      let reason = '未记录已核验 Pi 会话身份', session = null;
      if (owner.pi_session_id) {
        const matches = threads.get(owner.pi_session_id) || [];
        if (claims.get(owner.pi_session_id)?.length !== 1) reason = '身份冲突：多位负责人使用同一 Pi 身份';
        else if (matches.length > 1) reason = '会话冲突：多个原生会话使用同一 Pi 身份';
        else if (matches.length === 0) reason = '当前会话列表中无对应记录';
        else if (!matches[0].bound) reason = '对应会话尚未绑定可用日志';
        else { session = matches[0].session_id; reason = '可定位到原生会话（不代表在线）'; }
      } else if (owner.identity_note) reason = owner.identity_note;
      result.set(owner.id, { session, reason });
    }
    return result;
  }

  function route(hash, session) {
    const params = new URLSearchParams(hash.replace(/^#/, ''));
    params.set('session', session); return '#' + params.toString();
  }
  // rank is captured from an upstream render, never from our regrouped DOM.
  function groups(tree, bound, nativeIds, priorOrder = []) {
    const rank = new Map(nativeIds.map((id, index) => [id, index]));
    const walk = id => [id, ...tree.children.get(id).flatMap(walk)];
    const teams = tree.roots.map(id => ({ id, root: id, owners: walk(id), unresolved: false }));
    for (const [parent, ids] of tree.unresolved) {
      teams.push({ id: parent, root: null, owners: ids.flatMap(walk), unresolved: true });
    }
    const current = new Set(teams.map(t => t.id));
    const order = [...new Set([...priorOrder.filter(id => current.has(id)), ...current])];
    teams.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    for (const team of teams) {
      team.sessions = team.owners.map(id => ({ owner: id, session: bound.get(id)?.session }))
        .filter(item => rank.has(item.session))
        .sort((a, b) => Number(b.owner === team.root) - Number(a.owner === team.root) ||
          rank.get(a.session) - rank.get(b.session));
    }
    return { teams, order };
  }
  const api = { forest, catalog, bindings, route, groups };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.CodoxearOwnerCore = api;
})(globalThis);
