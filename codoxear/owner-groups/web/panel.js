/* Own only the shadow panel. Native Codoxear DOM and listeners stay in place. */
(function (scope) {
  'use strict';
  const Core = scope.CodoxearOwnerCore;
  const HOST = 'quick-deploy-owner-groups';
  const states = { active: '责任进行中', ended: '责任已结束', 'handed-over': '已交接', mixed: '含多种责任状态', 'no-scope': '无已登记职责' };
  const css = `
    :host{display:block;flex:0 0 auto;max-height:55vh;max-height:55dvh;min-height:0;overflow:auto;color:#e7edf5;font:12px/1.5 system-ui,sans-serif;--muted:#a7b5c7;--border:#40516b}
    *{box-sizing:border-box}section{padding:12px 10px;border-bottom:1px solid var(--border);background:#172333}
    h2{font-size:14px;letter-spacing:.02em;margin:0}p{margin:5px 0;color:var(--muted)}
    .heading,.tools{display:flex;gap:8px;align-items:center;justify-content:space-between}.heading{position:sticky;top:0;z-index:4;background:#172333;padding:4px 0}.tools{justify-content:flex-start;margin:8px 0}
    button{cursor:pointer;border:1px solid #566d8c;border-radius:5px;background:#243750;color:#ecf3ff;padding:4px 8px;font:inherit}
    button:hover{background:#35506f}button:focus-visible,summary:focus-visible{outline:2px solid #88bfff;outline-offset:2px}
    button:disabled{opacity:.6;cursor:wait}.badge{font-size:10px;border:1px solid #71829c;border-radius:4px;padding:1px 5px;white-space:nowrap}
    .demo{color:#ffdc99;border-color:#ad8546}.count{font-size:11px;color:var(--muted)}
    details.owner{margin:7px 0;border:1px solid var(--border);border-radius:7px;background:#1c2a3c}
    summary{padding:8px;cursor:pointer;overflow-wrap:anywhere}summary .name{font-weight:650;font-size:13px}.availability{display:block;margin-left:14px;color:var(--muted);font-size:10px}.root-summary{position:sticky;top:32px;z-index:2;background:#1c2a3c;padding-left:29px;list-style:none}.root-summary::-webkit-details-marker{display:none}.root-star{position:absolute;left:8px;top:7px;font-size:17px;line-height:1.2;color:#ffd989}.root-summary .role,.root-summary .summary-count{margin-left:0}
    .role{display:block;color:#c0cfe1;margin-left:14px;font-size:11px}.summary-count{margin-left:14px;color:var(--muted);font-size:10px}
    .body{padding:0 9px 9px}.children{margin:7px 0 0 6px;padding-left:8px;border-left:2px solid #576b87}
    .children .owner{background:#223247}ul{padding-left:16px;margin:5px 0;color:var(--muted)}li{margin-bottom:4px;overflow-wrap:anywhere}
    .reason{overflow-wrap:anywhere;font-size:11px}.current{display:none;color:#b6dcff;margin-left:6px}
    details[aria-current="true"]>summary{background:#2c4e70;box-shadow:inset 3px 0 #8bc6ff}details[aria-current="true"]>summary .current{display:inline}
    .warning{border:1px solid #b48a4b;border-radius:6px;padding:8px;background:#3d3020;color:#ffdfa5}
    .empty{border:1px dashed #65748a;border-radius:7px;padding:10px;margin:8px 0}.native{border-top:1px solid var(--border);padding-top:8px;font-weight:600;color:#c2cfe0}
    .unresolved{border:1px dashed #bd954e;border-radius:7px;padding:8px;margin-top:8px}.unresolved>strong{color:#ffdfa5}
    :host([data-floating]){position:fixed;left:12px;bottom:12px;z-index:2147483647;max-width:340px;box-shadow:0 3px 18px #0008}
  `;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function start({ readSnapshot, demo = false, pollMs = 10000 }) {
    scope.CodoxearOwnerPanel?.controller?.stop();
    // A stale host from an invalidated extension must never cause two mount points.
    document.getElementById(HOST)?.remove();
    let stopped = false, pending = false, host = null, shadow = null, tree = null, bound = new Map();
    let status = '正在读取负责人目录…', compatibility = false, busy = false, activating = false;
    const expanded = new Map();
    let timer;

    function nativeAnchor() {
      const anchor = document.querySelector('.sidebar > #sessions.sessions');
      return anchor && document.querySelectorAll('#sessions').length === 1 ? anchor : null;
    }
    function ensureMount() {
      if (stopped || !document.body) return;
      const anchor = nativeAnchor();
      if (!anchor && document.getElementById('loginForm')) {
        host?.remove(); host = null; shadow = null; return;
      }
      const incompatible = !anchor;
      // Preserve native restore-lead + list adjacency as well as the list itself.
      const insertion = anchor?.parentElement.querySelector(':scope > .sessionRestoreLead') || anchor;
      if (!host || !host.isConnected || compatibility !== incompatible) {
        host?.remove(); host = element('div'); host.id = HOST;
        compatibility = incompatible;
        if (incompatible) { host.dataset.floating = ''; document.body.append(host); }
        else insertion.before(host);
        shadow = host.attachShadow({ mode: 'open' }); render();
      } else if (insertion && host.nextElementSibling !== insertion) insertion.before(host);
      updateActive();
    }
    function activeSession() {
      const row = nativeAnchor()?.querySelector('.session.active[data-session-id]');
      return row ? row.dataset.sessionId : new URLSearchParams(location.hash.slice(1)).get('session');
    }
    function updateActive() {
      if (!shadow) return;
      const current = activeSession();
      for (const node of shadow.querySelectorAll('[data-owner]')) {
        node.setAttribute('aria-current', String(Boolean(current && bound.get(node.dataset.owner)?.session === current)));
      }
    }
    function tool(label, fn) {
      const button = element('button', label); button.type = 'button'; button.addEventListener('click', fn); return button;
    }
    function renderOwner(id) {
      const owner = tree.nodes.get(id), binding = bound.get(id);
      const details = element('details', undefined, 'owner');
      details.dataset.owner = id;
      details.open = expanded.has(id) ? expanded.get(id) : tree.children.get(id).length > 0;
      details.addEventListener('toggle', () => expanded.set(id, details.open));
      const summary = element('summary');
      if (owner.parent === 'user') {
        summary.classList.add('root-summary');
        const star = element('span', '★', 'root-star');
        star.title = '最高负责人'; star.setAttribute('aria-label', '最高负责人');
        summary.append(star);
      }
      summary.append(element('span', owner.name, 'name'), element('span', '当前', 'badge current'), element('span', owner.role, 'role'));
      const descendants = count(id) - 1;
      if (descendants) summary.append(element('span', `${descendants} 位下级 · 跨项目按上级归组`, 'summary-count'));
      const availability = element('span', `${binding.session ? '会话可定位' : '未绑定会话'} · ${states[owner.state]}`, 'availability');
      availability.title = binding.reason; summary.append(availability);
      details.append(summary);
      const body = element('div', undefined, 'body');
      body.append(element('span', states[owner.state], 'badge'));
      if (owner.scopes.length) {
        const scopes = element('ul');
        owner.scopes.forEach(s => scopes.append(element('li', `${s.task} · ${s.project} · ${states[s.status]}`)));
        body.append(scopes);
      }
      body.append(element('p', binding.reason, 'reason'));
      if (binding.session) {
        const button = tool('打开会话', () => activate(id, binding.session));
        button.disabled = activating; button.dataset.openOwner = id;
        button.setAttribute('aria-label', `打开 ${owner.name} 的已登记会话`); body.append(button);
      }
      const children = element('div', undefined, 'children');
      tree.children.get(id).forEach(child => children.append(renderOwner(child)));
      if (children.childElementCount) body.append(children);
      details.append(body); return details;
    }
    function count(id) { return 1 + tree.children.get(id).reduce((sum, child) => sum + count(child), 0); }
    function render() {
      if (!shadow) return;
      const style = element('style', css), section = element('section');
      section.setAttribute('aria-label', '负责人分组');
      const heading = element('div', undefined, 'heading'); heading.append(element('h2', '负责人分组'));
      if (demo) heading.append(element('span', 'DEMO · 合成数据', 'badge demo'));
      section.append(heading, element('p', '按登记上级归组 · 会话可定位 ≠ 在线'));
      if (compatibility) {
        section.append(element('p', '兼容性提示：未找到受支持的 Codoxear 侧栏；增强已停用，原应用保持可用。', 'warning'));
      } else {
        const tools = element('div', undefined, 'tools');
        tools.append(tool(busy ? '读取中…' : '刷新', refresh), tool('全部展开', () => expand(true)), tool('全部折叠', () => expand(false)));
        tools.firstElementChild.disabled = busy; section.append(tools);
        if (status) section.append(element('p', status, 'warning'));
        if (tree) {
          const available = [...bound.values()].filter(b => b.session).length;
          section.append(element('p', `${tree.nodes.size} 位负责人 · ${available} 个会话可定位 · ${tree.unresolved.size} 个上级待登记`, 'count'));
          if (!tree.nodes.size) {
            const empty = element('div', undefined, 'empty');
            empty.append(element('strong', '尚无已登记负责人'), element('p', '明确任命后，责任登记中的顶层负责人及下级会出现在这里。普通会话仍在下方原生列表。不会按标题或目录猜测归属。'));
            section.append(empty);
          }
          tree.roots.forEach(id => section.append(renderOwner(id)));
          for (const [token, ids] of tree.unresolved) {
            const missing = element('div', undefined, 'unresolved');
            missing.append(element('strong', '上级待确认：' + token), element('p', '保留原上级关系；以下负责人未提升为顶层。'));
            ids.forEach(id => missing.append(renderOwner(id))); section.append(missing);
          }
        }
        section.append(element('div', '全部会话 · 原生列表 ↓', 'native'));
      }
      shadow.replaceChildren(style, section); updateActive();
    }
    function expand(open) {
      for (const node of shadow.querySelectorAll('details.owner')) {
        expanded.set(node.dataset.owner, open); node.open = open;
      }
    }
    async function snapshot() {
      const payload = await readSnapshot();
      demo = payload.demo === true;
      const nextTree = Core.forest(payload.owners), nextBound = Core.bindings(nextTree, Core.catalog(payload.sessions));
      return { nextTree, nextBound };
    }
    async function refresh() {
      if (stopped || busy || activating) return;
      busy = true;
      try {
        const { nextTree, nextBound } = await snapshot();
        if (stopped) return;
        tree = nextTree; bound = nextBound; status = '';
      } catch (_) {
        // Never keep an old clickable mapping after any failed read/validation.
        tree = null; bound = new Map();
        status = '目录桥接或会话 API 不可用 / 不兼容。增强已停止定位；请确认安装与登录，原生列表仍可使用。';
      } finally { busy = false; if (!stopped) { ensureMount(); render(); } }
    }
    async function activate(id, expectedSession) {
      if (stopped || activating || busy) return;
      activating = true; render();
      try {
        const { nextTree, nextBound } = await snapshot();
        if (stopped) return;
        const prior = tree.nodes.get(id), next = nextTree.nodes.get(id), selected = nextBound.get(id);
        tree = nextTree; bound = nextBound;
        if (!next || next.pi_session_id !== prior.pi_session_id || selected?.session !== expectedSession) {
          status = '会话身份或路由已变化；本次未跳转。请核对更新后的登记。';
        } else {
          status = '';
          // Assign hash: real hashchange invokes Codoxear's native selection lifecycle.
          location.hash = Core.route(location.hash, selected.session);
          if (document.body.classList.contains('sidebar-open') && window.matchMedia('(max-width: 880px)').matches) {
            document.getElementById('toggleSidebarBtn')?.click();
          }
        }
      } catch (_) {
        tree = null; bound = new Map(); status = '重新核验失败，未跳转。原生列表仍可使用。';
      } finally { activating = false; if (!stopped) render(); }
    }
    const observer = new MutationObserver(records => {
      if (stopped || pending || records.every(r => host && (r.target === host || host.contains(r.target)))) return;
      pending = true;
      queueMicrotask(() => { pending = false; ensureMount(); });
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true,
      attributeFilter: ['class', 'data-session-id'] });
    const onHash = () => updateActive();
    window.addEventListener('hashchange', onHash);
    const stop = () => {
      stopped = true; observer.disconnect(); clearInterval(timer);
      window.removeEventListener('hashchange', onHash); window.removeEventListener('pagehide', stop);
      host?.remove();
    };
    window.addEventListener('pagehide', stop);
    ensureMount(); refresh(); timer = setInterval(() => { if (!document.hidden) refresh(); }, pollMs);
    const controller = { stop, refresh };
    scope.CodoxearOwnerPanel.controller = controller;
    return controller;
  }
  scope.CodoxearOwnerPanel = { start, controller: scope.CodoxearOwnerPanel?.controller || null };
})(globalThis);
