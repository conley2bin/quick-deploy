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
    #sessions .owner-team > .session.owner-root:not(.active){border:1px solid var(--border-subtle,#e4e4e4);
      border-radius:var(--radius-card);background:var(--surface-subtle,#f5f5f5)}
    #sessions .owner-team > .session.owner-root:not(.active) .sessionContent{background:var(--surface-subtle,#f5f5f5)}
    #sessions .owner-root-star{display:block;width:20px;height:20px;flex:0 0 20px;pointer-events:none}
    #sessions .owner-unavailable{font:inherit;font-size:11px;color:var(--muted,#777);padding:7px 10px}
    #sessions > .owner-team[data-unresolved]{border-style:dashed}
    #quick-deploy-owner-groups{font:11px/1.4 system-ui,sans-serif;color:var(--muted,#777);padding:4px 12px;flex:0 0 auto}
  `;
  function el(tag, cls, text) {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text; return n;
  }
  function rootStar() {
    const ns = 'http://www.w3.org/2000/svg', star = document.createElementNS(ns, 'svg');
    for (const [name, value] of Object.entries({ class: 'owner-root-star', viewBox: '0 0 100 104',
      role: 'img', 'aria-label': '最高负责人', title: '最高负责人', focusable: 'false' })) star.setAttribute(name, value);
    const title = document.createElementNS(ns, 'title'); title.textContent = '最高负责人'; star.append(title);
    // Rounded tips AND valleys, with ID-free gloss so multiple teams cannot collide.
    for (const attrs of [
      { d: 'M50 3C55 3 59 16 67 29Q70 34 76 35L91 38C102 40 102 47 94 55L82 67Q78 71 79 77L81 91C83 102 77 102 69 98L55 91Q50 88 45 91L31 98C23 102 17 102 19 91L21 77Q22 71 18 67L6 55C-2 47-2 40 9 38L24 35Q30 34 33 29C41 16 45 3 50 3Z',
        fill: '#ffdf00', stroke: '#efb900', 'stroke-width': '2', 'stroke-linejoin': 'round' },
      { d: 'M22 80L21 92Q20 99 28 96L45 88Q50 85 55 88L73 97Q80 101 79 92L77 79Q70 91 57 86Q50 83 43 87Q29 94 22 80Z',
        fill: '#ffc400', opacity: '.65' },
      { d: 'M47 10C42 17 38 29 32 35Q29 39 23 40L10 43Q4 44 10 50L21 61',
        fill: 'none', stroke: '#fff88a', 'stroke-width': '4', 'stroke-linecap': 'round', opacity: '.85' },
      { d: 'M25 74L23 90M91 44L94 46', fill: 'none', stroke: '#fffbd0',
        'stroke-width': '3', 'stroke-linecap': 'round', opacity: '.7' },
    ]) {
      const path = document.createElementNS(ns, 'path');
      for (const [name, value] of Object.entries(attrs)) path.setAttribute(name, value);
      star.append(path);
    }
    return star;
  }
  function start({ readSnapshot, pollMs = 10000 }) {
    scope.CodoxearOwnerPanel?.controller?.stop();
    document.getElementById(STYLE)?.remove(); document.getElementById(NOTICE)?.remove();
    const style = el('style', '', css); style.id = STYLE; document.head.append(style);
    let stopped = false, busy = false, pending = false, anchor = null, original = [], layout = [], tree = null, bound = new Map();
    let message = '', demo = false, order = [], era = null;
    const storageKey = 'quick-deploy.owner-groups.order:' + location.pathname;
    function resetOrder(nextEra, empty) {
      if (empty || nextEra === 'template') {
        order = []; era = nextEra;
        try { localStorage.removeItem(storageKey); } catch (_) { /* Optional storage. */ }
      } else if (era !== nextEra) {
        order = []; era = nextEra;
        try {
          const saved = JSON.parse(localStorage.getItem(storageKey));
          if (saved?.format === 2 && saved.era === era && Array.isArray(saved.order) &&
              saved.order.length <= 1000 && saved.order.every(x => typeof x === 'string')) order = saved.order;
          else localStorage.removeItem(storageKey);
        } catch (_) { /* Registration order remains deterministic. */ }
      }
    }
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
      n?.querySelectorAll('.session.owner-root').forEach(row => row.classList.remove('owner-root'));
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
      try { localStorage.setItem(storageKey, JSON.stringify({format: 2, era, order: order.slice(-1000)})); } catch (_) { /* No required storage writes. */ }
      const leading = original[0]?.matches('.sessionGroupHeader') ? original[0] : null;
      const desired = leading ? [leading] : [], used = new Set();
      cleanDecorations(anchor);
      for (const team of projection.teams) {
        // Keep its saved order slot, but don't accumulate frames for absent teams.
        if (!team.sessions.length) continue;
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
            row.classList.add('owner-root');
            const title = row.querySelector('.titleText');
            if (!title) throw Error('Unsupported native title');
            title.after(rootStar());
          }
        }
        sync(box, contents);
        for (const child of [...box.children]) if (!contents.includes(child)) child.remove();
        desired.push(box);
      }
      // Frames belong under the original leading section header. Count every
      // boxed row there, plus its unboxed rows; later sections count only leftovers.
      let header = null, bucket = [];
      function flush() {
        const renderedCount = bucket.length + (header === leading ? used.size : 0);
        if (!renderedCount && header !== leading) return;
        if (header) {
          const count = header.querySelector('.sessionGroupCount');
          headers.set(header, { count: count?.textContent, label: header.getAttribute('aria-label') });
          if (count) count.textContent = String(renderedCount);
          header.setAttribute('aria-label', `${header.querySelector('.sessionGroupLabel')?.textContent || ''}: ${renderedCount} sessions`);
          if (header !== leading) desired.push(header);
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
        if (!payload.owners.ok || typeof payload.owners.era !== 'string') throw Error('Unavailable document era');
        const nextTree = Core.forest(payload.owners), nextBound = Core.bindings(nextTree, Core.catalog(payload.sessions));
        if (!stopped) { resetOrder(payload.owners.era, payload.owners.owners.length === 0); tree = nextTree; bound = nextBound; demo = payload.demo === true; message = ''; }
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
