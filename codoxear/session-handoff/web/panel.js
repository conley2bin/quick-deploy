/* Session-scoped native UI, transport, polling and targeted topbar remounts. */
(function (global) {
  'use strict';
  const Core = typeof module !== 'undefined' && module.exports ? require('./core.js') : global.CodoxearHandoffCore;
  const { namespace, selected, targetURL, createController, labels, active } = Core;
  function mount(win, src, onLogout = () => {}) {
    const doc = win.document, base = namespace(src, win.location.origin);
    const key = 'codoxear:session-handoff:' + new URL(base).pathname;
    let controller, ui, timer, panelAbort, disposed = false, shell = null, main = null, actions = null;
    const abort = new AbortController(), requests = new Set();
    const style = doc.createElement('style'); style.id = 'session-handoff-style';
    style.textContent = `#handoffButton svg{display:block;width:20px;height:20px;pointer-events:none}
@media(max-width:420px){#handoffButton svg{width:18px;height:18px}}
#handoffPanel{flex:none;font:inherit;background:var(--panel);color:var(--text);border-bottom:1px solid var(--border);padding:8px 12px;overflow-wrap:anywhere}
#handoffPanel[hidden]{display:none} #handoffPanel .handoffRow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#handoffPanel button,#handoffPanel a{font:inherit;min-height:36px} #handoffPanel a{color:var(--accent)}
#handoffPanel textarea{display:block;box-sizing:border-box;width:100%;max-width:560px;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin:6px 0;padding:6px}
#handoffPanel p{margin:4px 0} #handoffPanel .handoffHint{font-size:12px;color:var(--muted)}
@media(max-width:600px){#handoffPanel{padding:6px 10px;max-height:40dvh;overflow:auto}#handoffPanel button,#handoffPanel a{min-height:44px}}`;
    doc.head.append(style);
    const el = (tag, text, attrs = {}) => {
      const n = doc.createElement(tag); if (text) n.textContent = text;
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      return n;
    };
    const on = (node, type, fn) => node.addEventListener(type, fn, { signal: abort.signal });
    const onUI = (node, type, fn) => node.addEventListener(type, fn, { signal: panelAbort.signal });
    function schedule() {
      win.clearTimeout(timer);
      const s = controller.view();
      if (disposed || doc.hidden || !s.reference?.operation_id || s.busy || (s.op && !active.has(s.op.status)) || s.error) return;
      timer = win.setTimeout(() => controller.poll(), 1800);
    }
    function render(s) {
      if (!ui) return;
      const has = Boolean(s.reference || s.error);
      ui.panel.hidden = !has && ui.form.hidden;
      ui.status.textContent = s.op ? `${labels[s.op.status]} · 来源：${s.op.source_session_id}${s.op.message ? ' · ' + s.op.message : ''}`
        : s.reference ? (s.busy ? '正在核对接力请求…' : '请求结果未确认。请核对原请求；刷新后补充说明不会保留。') : '';
      ui.error.textContent = s.error || (s.uncertainContinue ? '继续请求结果尚未确认；仅查询状态，不会重发。' : '');
      ui.start.disabled = s.busy || Boolean(s.reference);
      ui.button.disabled = s.busy;
      ui.retry.hidden = !s.reference || s.busy || (!s.error && s.op && active.has(s.op.status));
      ui.retry.textContent = s.reference?.operation_id ? '刷新接力状态' : '核对原请求';
      ui.bypass.hidden = !(s.op?.status === 'unreviewed' && s.op.can_continue_unreviewed && s.op.target_session_id && !s.uncertainContinue);
      ui.bypass.disabled = s.busy;
      ui.link.hidden = !s.op?.target_session_id;
      if (s.op?.target_session_id) ui.link.href = targetURL(win.location.href, s.op.target_session_id);
      ui.done.hidden = !s.op || !['ready', 'failed', 'conflict'].includes(s.op.status) || s.uncertainContinue || s.busy;
      ui.button.setAttribute('aria-expanded', String(!ui.panel.hidden));
      if (s.reference) ui.form.hidden = true;
      schedule();
    }
    async function post(kind, body) {
      const ac = new AbortController(); requests.add(ac);
      const deadline = win.setTimeout(() => ac.abort(), 20000);
      try {
        const res = await win.fetch(new URL(kind, base), { method: 'POST', credentials: 'same-origin', cache: 'no-store',
          redirect: 'error', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ac.signal });
        let data;
        try { data = await res.json(); } catch (_) { throw Error('接力服务返回无效响应'); }
        if (!res.ok) {
          const e = Error(typeof data.message === 'string' ? data.message : `接力请求失败（${res.status}）`);
          e.status = res.status; e.definitive = res.status >= 400 && res.status < 500 && typeof data.error === 'string'; throw e;
        }
        return data;
      } finally { win.clearTimeout(deadline); requests.delete(ac); }
    }
    function dispose(clear = false) {
      if (disposed) return; disposed = true;
      controller.stop(clear); win.clearTimeout(timer); abort.abort(); panelAbort?.abort();
      for (const ac of requests) ac.abort(); requests.clear();
      rootObserver.disconnect(); shellObserver.disconnect(); mainObserver.disconnect(); actionsObserver.disconnect();
      ui?.button.remove(); ui?.panel.remove(); doc.getElementById('handoffCompatibility')?.remove(); style.remove();
      if (clear) onLogout();
    }
    controller = createController({ post, storage: {
      getItem: k => win.localStorage.getItem(k), setItem: (k, v) => win.localStorage.setItem(k, v), removeItem: k => win.localStorage.removeItem(k),
    }, key, uuid: () => Core.requestUUID(win.crypto),
      changed: render, authLost: () => {
        dispose(true);
        const warning = el('p', '会话接力不可用：登录已失效或没有权限，请重新登录。', { id: 'handoffCompatibility', role: 'alert' });
        doc.querySelector('#root > .app > .main')?.prepend(warning);
      } });
    function install() {
      if (disposed) return;
      if (doc.getElementById('loginForm')) { if (ui) dispose(true); return; }
      const root = doc.getElementById('root');
      const nextShell = root?.querySelector(':scope > .app');
      const nextMain = nextShell?.querySelector(':scope > .main');
      if (shell !== nextShell) { shellObserver.disconnect(); shell = nextShell; if (shell) shellObserver.observe(shell, { childList: true }); }
      if (main !== nextMain) { mainObserver.disconnect(); main = nextMain; if (main) mainObserver.observe(main, { childList: true }); }
      const bar = main?.querySelector(':scope > .topbar');
      if (actions !== bar) { actionsObserver.disconnect(); actions = bar; if (bar) actionsObserver.observe(bar, { childList: true, subtree: true }); }
      const details = bar?.querySelector('.topActions > #diagBtn');
      if (!details) {
        panelAbort?.abort(); ui?.button.remove(); ui?.panel.remove(); ui = null; win.clearTimeout(timer);
        if (nextShell && !doc.getElementById('handoffCompatibility')) {
          const warning = el('p', '会话接力不可用：此版本缺少兼容的 Details 操作栏。', { id: 'handoffCompatibility', role: 'status' });
          (main || nextShell).append(warning);
        }
        return;
      }
      doc.getElementById('handoffCompatibility')?.remove();
      if (ui?.button.isConnected && ui.button.parentNode === details.parentNode) return;
      panelAbort?.abort(); ui?.button.remove(); ui?.panel.remove(); panelAbort = new AbortController();
      const button = el('button', '', { id: 'handoffButton', class: 'icon-btn', type: 'button', title: '会话接力', 'aria-label': '会话接力', 'aria-controls': 'handoffPanel', 'aria-expanded': 'false' });
      // Static Opus design: context in the old node flows into a fresh node.
      const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
      for (const [name, value] of Object.entries({ viewBox: '0 0 24 24', width: '20', height: '20', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false' })) svg.setAttribute(name, value);
      for (const [tag, attrs] of [
        ['rect', { x: '2.5', y: '3', width: '9', height: '7', rx: '2' }],
        ['rect', { x: '12.5', y: '14.5', width: '9', height: '6.5', rx: '2' }],
        ['path', { d: 'M5.5 6.5h3M11.5 6.5h3a2.5 2.5 0 0 1 2.5 2.5v3.6M14.9 10.5 17 12.6l2.1-2.1' }],
      ]) {
        const node = doc.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
        svg.append(node);
      }
      button.append(svg);
      details.after(button);
      const panel = el('section', '', { id: 'handoffPanel', 'aria-label': '会话接力' }); panel.hidden = true; bar.after(panel);
      const form = el('form'), source = el('p'); form.hidden = true;
      const hint = el('p', '新 Pi 独立恢复 → 旧会话审阅 → 新会话核对一次后接续。仅支持 Pi，不迁移负责人。', { class: 'handoffHint' });
      const label = el('label', '补充说明（可选，最多 1000 字）', { for: 'handoffNote' });
      const note = el('textarea', '', { id: 'handoffNote', rows: '2', maxlength: '1000' });
      const start = el('button', '确认接力', { type: 'submit' }), cancel = el('button', '取消', { type: 'button' });
      const buttons = el('div', '', { class: 'handoffRow' }); buttons.append(start, cancel);
      form.append(source, hint, label, note, buttons);
      const status = el('p', '', { role: 'status', 'aria-live': 'polite' }), error = el('p', '', { role: 'alert' });
      const retry = el('button', '核对原请求', { type: 'button' }), bypass = el('button', '未校对，仍继续', { type: 'button' });
      const link = el('a', '打开新会话'), done = el('button', '收起记录', { type: 'button' });
      const row = el('div', '', { class: 'handoffRow' }); row.append(retry, bypass, link, done); panel.append(form, status, error, row);
      ui = { button, panel, form, source, note, start, status, error, retry, bypass, link, done };
      let confirmedSource;
      onUI(button, 'click', () => {
        const s = controller.view();
        if (s.reference) { panel.hidden = false; render(s); return; }
        confirmedSource = selected(win.location.hash); // fresh even after native replaceState
        source.textContent = confirmedSource ? '来源会话：' + confirmedSource : '请先在原生页面选择一个会话。';
        form.hidden = false; panel.hidden = false; start.disabled = !confirmedSource;
        button.setAttribute('aria-expanded', 'true'); note.focus();
      });
      onUI(form, 'submit', e => {
        e.preventDefault();
        const now = selected(win.location.hash);
        if (now !== confirmedSource) {
          confirmedSource = now; source.textContent = '来源已切换，请再次确认：' + (now || '未选择会话'); start.disabled = !now; return;
        }
        controller.start(now, note.value);
      });
      onUI(cancel, 'click', () => { form.hidden = true; panel.hidden = true; note.value = ''; button.setAttribute('aria-expanded', 'false'); button.focus(); });
      onUI(form, 'keydown', e => { if (e.key === 'Escape') { e.preventDefault(); cancel.click(); } });
      onUI(retry, 'click', () => controller.recover());
      onUI(bypass, 'click', () => controller.continueUnreviewed());
      onUI(link, 'click', e => { e.preventDefault(); const id = controller.view().op?.target_session_id; if (id) win.location.replace(targetURL(win.location.href, id)); });
      onUI(done, 'click', () => { controller.forget(); note.value = ''; button.focus(); });
      render(controller.view());
      if (controller.view().reference?.operation_id && !controller.view().op && !doc.hidden) controller.poll();
    }
    const rootObserver = new MutationObserver(install), shellObserver = new MutationObserver(install),
      mainObserver = new MutationObserver(install), actionsObserver = new MutationObserver(install);
    const root = doc.getElementById('root');
    if (root) rootObserver.observe(root, { childList: true }); // only shell replacements, never the transcript tree
    on(doc, 'click', e => { if (e.target.closest?.('#logoutBtnSide')) dispose(true); });
    on(win, 'pagehide', () => dispose());
    on(doc, 'visibilitychange', () => { win.clearTimeout(timer); if (!doc.hidden && !disposed && controller.view().reference?.operation_id) controller.poll(); });
    install();
    return { controller, dispose };
  }
  const api = { mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CodoxearHandoffPanel = api;
})(typeof window !== 'undefined' ? window : globalThis);
