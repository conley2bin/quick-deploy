/* Reconcile complete ORIGINAL native cards. No cloned rows or navigation callbacks. */
(function (scope) {
  'use strict';
  const Core = scope.CodoxearOwnerCore;
  const STYLE = 'quick-deploy-owner-groups-style';
  const NOTICE = 'quick-deploy-owner-groups';
  const css = `
    #sessions > .owner-team{border:2px solid #fff;border-radius:16px;padding:5px;margin:3px 0 12px;
      box-shadow:0 1px 4px #00000024;min-width:0;background:transparent}
    #sessions > .owner-team > .session{position:relative}
    #sessions .owner-root-star{position:absolute;right:2px;top:-6px;z-index:3;color:#bea04b;
      font:20px/1 system-ui,sans-serif;pointer-events:none;text-shadow:0 1px 1px #fff8}
    #sessions .owner-unavailable{font:inherit;font-size:11px;color:var(--muted,#777);padding:7px 10px}
    #sessions > .owner-team[data-unresolved]{border-style:dashed}
    #quick-deploy-owner-groups{font:11px/1.4 system-ui,sans-serif;color:var(--muted,#777);padding:4px 12px;flex:0 0 auto}
  `;
  function el(tag, cls, text) {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text; return n;
  }
  function start({ readSnapshot, pollMs = 10000 }) {
    scope.CodoxearOwnerPanel?.controller?.stop();
    document.getElementById(STYLE)?.remove(); document.getElementById(NOTICE)?.remove();
    const style = el('style', '', css); style.id = STYLE; document.head.append(style);
    let stopped = false, busy = false, pending = false, anchor = null, original = [], layout = [], tree = null, bound = new Map();
    let message = '', demo = false, order = [];
    const storageKey = 'quick-deploy.owner-groups.order:' + location.pathname;
    try { const saved = JSON.parse(localStorage.getItem(storageKey)); if (Array.isArray(saved) && saved.every(x => typeof x === 'string') && saved.length <= 1000) order = saved; } catch (_) { /* Storage is optional. Registration order remains deterministic. */ }
    const headers = new Map();
    const observer = new MutationObserver(() => {
      if (stopped || pending) return;
      pending = true; queueMicrotask(() => { pending = false; reconcile(); });
    });
    function observe() {
      if (!stopped) observer.observe(document.documentElement, { childList: true, subtree: true,
        attributes: true, attributeFilter: ['class', 'data-session-id'] });
    }
    function mutate(fn) {
      observer.disconnect();
      try { fn(); } finally { observe(); }
    }
    function nativeAnchor() {
      const n = document.querySelector('.sidebar > #sessions.sessions');
      return n && document.querySelectorAll('#sessions').length === 1 ? n : null;
    }
    function cleanDecorations(n) {
      n?.querySelectorAll('.owner-root-star').forEach(s => s.remove());
      for (const [header, saved] of headers) {
        const count = header.querySelector('.sessionGroupCount'); if (count) count.textContent = saved.count;
        if (saved.label === null) header.removeAttribute('aria-label'); else header.setAttribute('aria-label', saved.label);
      }
      headers.clear();
    }
    function restore() {
      if (!anchor) return;
      cleanDecorations(anchor);
      // Disconnected old native rows were invalidated by upstream; never resurrect them.
      const retained = original.filter(n => anchor.contains(n) || n.matches?.('.sessionGroupHeader') && !n.isConnected);
      sync(anchor, retained);
      anchor.querySelectorAll(':scope > .owner-team').forEach(n => n.remove());
      original = [...anchor.children];
    }
    function notice() {
      document.getElementById(NOTICE)?.remove();
      if (document.getElementById('loginForm') || (!message && !demo)) return;
      const n = el('div', '', message || 'DEMO · 合成数据'); n.id = NOTICE; n.setAttribute('role', 'status');
      const sidebar = document.querySelector('.sidebar'); if (sidebar) sidebar.append(n);
    }
    function sync(parent, desired) {
      desired.forEach((node, index) => {
        if (parent.children[index] !== node) parent.insertBefore(node, parent.children[index] || null);
      });
    }
    function apply() {
      const rows = original.filter(n => n.matches?.('.session[data-session-id]'));
      const ids = rows.map(n => n.dataset.sessionId);
      if (new Set(ids).size !== ids.length || original.some(n => !n.matches?.('.session[data-session-id],.sessionGroupHeader,.sidebarEmptyHint'))) throw Error('Unsupported native item wrapper');
      const byId = new Map(rows.map(n => [n.dataset.sessionId, n]));
      const projection = Core.groups(tree, bound, ids, order); order = projection.order;
      try { localStorage.setItem(storageKey, JSON.stringify(order.slice(-1000))); } catch (_) { /* No required storage writes. */ }
      const desired = [], used = new Set();
      cleanDecorations(anchor);
      for (const team of projection.teams) {
        // Empty true roots remain an explicit unavailable label, never a fabricated live row.
        if (team.unresolved && !team.sessions.length) continue;
        let box = [...anchor.children].find(n => n.classList.contains('owner-team') && n.dataset.ownerGroup === team.id);
        if (!box) { box = el('div', 'owner-team'); box.dataset.ownerGroup = team.id; box.setAttribute('role', 'group'); }
        box.setAttribute('aria-label', team.root ? tree.nodes.get(team.root).name : '上级待确认');
        if (team.unresolved) box.dataset.unresolved = '';
        const contents = [];
        const rootSession = team.sessions.find(item => item.owner === team.root);
        if (!rootSession) {
          let label = box.querySelector(':scope > .owner-unavailable');
          if (!label) label = el('div', 'owner-unavailable');
          label.textContent = team.unresolved ? '上级待确认 · 未提升下级' : tree.nodes.get(team.root).name + ' · 会话不可用';
          contents.push(label);
        }
        for (const item of team.sessions) {
          const row = byId.get(item.session); used.add(row); contents.push(row);
          if (item.owner === team.root) {
            const star = el('span', 'owner-root-star', '★'); star.title = '最高负责人'; star.setAttribute('aria-label', '最高负责人'); row.append(star);
          }
        }
        sync(box, contents);
        for (const child of [...box.children]) if (!contents.includes(child)) child.remove();
        desired.push(box);
      }
      // Retain native date/status headers only for rows still outside the frames.
      let header = null, bucket = [];
      function flush() {
        if (!bucket.length) return;
        if (header) {
          const count = header.querySelector('.sessionGroupCount');
          headers.set(header, { count: count?.textContent, label: header.getAttribute('aria-label') });
          if (count) count.textContent = String(bucket.length);
          header.setAttribute('aria-label', `${header.querySelector('.sessionGroupLabel')?.textContent || ''}: ${bucket.length} sessions`);
          desired.push(header);
        }
        desired.push(...bucket);
      }
      for (const node of original) {
        if (node.matches('.sessionGroupHeader')) { flush(); header = node; bucket = []; }
        else if (!used.has(node)) bucket.push(node);
      }
      flush(); sync(anchor, desired);
      for (const child of [...anchor.children]) if (!desired.includes(child)) child.remove();
    }
    function reconcile() {
      if (stopped) return;
      mutate(() => {
        const next = nativeAnchor();
        if (anchor !== next) {
          restore(); anchor = next; original = []; layout = [];
          if (next) queueMicrotask(refresh);
        }
        if (!anchor) {
          if (document.getElementById('loginForm')) { tree = null; bound = new Map(); message = ''; demo = false; }
          else message = '负责人分组：原生侧栏不兼容，增强已停用。';
          notice(); return;
        }
        // Only direct ORIGINAL native cards mean a fresh upstream render. Our cards
        // are descendants of .owner-team, so our own layout cannot become activity.
        const direct = [...anchor.children];
        if (!anchor.querySelector(':scope > .owner-team') &&
            (direct.length !== layout.length || direct.some((n, i) => layout[i] !== n))) {
          cleanDecorations(anchor); original = [...anchor.children];
        } else if (!original.length) original = [...anchor.children];
        if (!tree) restore();
        else {
          try { apply(); } catch (_) { restore(); tree = null; bound = new Map(); message = '负责人分组：原生行结构不兼容，增强已停用。'; }
        }
        layout = [...anchor.children];
        notice();
      });
    }
    async function refresh() {
      if (stopped || busy) return;
      busy = true;
      try {
        const payload = await readSnapshot();
        const nextTree = Core.forest(payload.owners), nextBound = Core.bindings(nextTree, Core.catalog(payload.sessions));
        if (!stopped) { tree = nextTree; bound = nextBound; demo = payload.demo === true; message = ''; }
      } catch (_) {
        if (!stopped) { tree = null; bound = new Map(); message = '负责人分组暂不可用，请检查登录或桥接；已恢复原生列表。'; }
      } finally { busy = false; if (!stopped) reconcile(); }
    }
    function stop() {
      stopped = true; observer.disconnect(); clearInterval(timer);
      restore(); style.remove(); document.getElementById(NOTICE)?.remove();
      window.removeEventListener('pagehide', stop);
    }
    observe(); reconcile(); refresh();
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, pollMs);
    window.addEventListener('pagehide', stop);
    const controller = { stop, refresh }; scope.CodoxearOwnerPanel.controller = controller; return controller;
  }
  scope.CodoxearOwnerPanel = { start, controller: scope.CodoxearOwnerPanel?.controller || null };
})(globalThis);
