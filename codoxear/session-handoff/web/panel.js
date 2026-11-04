/* Session-scoped native UI, transport, polling and targeted topbar remounts. */
(function (global) {
  'use strict';
  const Core = typeof module !== 'undefined' && module.exports ? require('./core.js') : global.CodoxearHandoffCore;
  const { namespace, selected, targetURL, createController, labels, active } = Core;
  const TargetModel = typeof module !== 'undefined' && module.exports ? require('./target-model.js') : global.CodoxearHandoffTargetModel;
  function mount(win, src, onLogout = () => {}, loadNative = TargetModel.loadNative) {
    const doc = win.document, base = namespace(src, win.location.origin);
    const key = 'codoxear:session-handoff:' + new URL(base).pathname;
    let controller, ui, timer, selectionTimer, panelAbort, disposed = false, shell = null, main = null, actions = null;
    let currentSource, nativePromise, nativeModules, catalogRequest, catalogSeq = 0;
    const appBase = new URL('../', base).href;
    const drafts = new Map();
    const draft = () => {
      if (!drafts.has(currentSource)) drafts.set(currentSource, { open: false, note: '', confirmedSource: null, reconfirm: false,
        choice: null, query: null, choiceStale: false, effortNote: '', catalog: null });
      return drafts.get(currentSource);
    };
    function syncSelection() {
      const next = selected(win.location.hash);
      if (next === currentSource) return false;
      cancelCatalog(); ui?.field?.close();
      currentSource = next;
      controller.select(next);
      const s = controller.view();
      if (next && draft().open && !s.reference && !s.invalidReference) readCatalog(next, draft());
      if (!doc.hidden && controller.view().reference?.operation_id && !controller.view().op) controller.poll();
      return true;
    }
    function monitorSelection() {
      win.clearTimeout(selectionTimer);
      if (disposed || doc.hidden) return;
      syncSelection();
      // Native replaceState has no event, including async transcript navigation.
      // Compare only the URL while visible; never inspect native cards/transcripts.
      selectionTimer = win.setTimeout(monitorSelection, 250);
    }
    const abort = new AbortController(), requests = new Set();
    const style = doc.createElement('style'); style.id = 'session-handoff-style';
    style.textContent = `#handoffButton svg{display:block;width:20px;height:20px;pointer-events:none}
@media(max-width:420px){#handoffButton svg{width:18px;height:18px}}
#handoffPanel{flex:none;font:inherit;background:var(--panel);color:var(--text);border-bottom:1px solid var(--border);padding:8px 12px;overflow-wrap:anywhere}
#handoffPanel[hidden]{display:none} #handoffPanel .handoffRow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#handoffPanel button,#handoffPanel a{font:inherit;min-height:36px} #handoffPanel a{color:var(--accent)}
#handoffPanel textarea{display:block;box-sizing:border-box;width:100%;max-width:560px;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin:6px 0;padding:6px}
#handoffPanel p{margin:4px 0} #handoffPanel .handoffHint{font-size:12px;color:var(--muted)}
#handoffMenuHost{position:fixed;inset:0;pointer-events:none;z-index:60}#handoffMenuHost>*{pointer-events:auto}
#handoffMenuHost [hidden]{display:none}#handoffMenuHost .fileMenuItem{min-height:44px;font:inherit}
#handoffPanel .newSessionRunConfigRow{max-width:560px;grid-template-columns:minmax(0,1.4fr) minmax(96px,auto)}
#handoffPanel .newSessionRunConfigRow>*{min-width:0}#handoffModelInput{min-width:0;width:100%;font:inherit}
#handoffModelMenu .fileMenuItem{align-items:flex-start}
#handoffModelMenu .modelMenuGroup{position:sticky;top:0;z-index:1;padding:var(--space-2) var(--space-5);border-bottom:1px solid var(--border-faint);background:var(--paper);color:var(--muted);font-family:var(--font-mono);font-size:var(--font-xs)}
#handoffModelMenu .fileMenuPath,#handoffModelMenu .fileMenuHint{min-width:0;white-space:normal;overflow:visible;overflow-wrap:anywhere;font-family:inherit}
#handoffModelMenu .fileMenuPath{flex:1 1 auto}#handoffModelMenu .fileMenuHint{flex:0 1 40%;text-align:right}
#handoffModelMenu .fileMenuItem.active .fileMenuHint{color:inherit;opacity:.72}
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
      timer = win.setTimeout(() => { syncSelection(); if (!doc.hidden) controller.poll(); }, 1800);
    }
    const identity = s => JSON.stringify([s.source, s.reference?.operation_id, s.reference?.request_id,
      s.reference?.pending_action, s.reference?.pending_id, s.reference?.target, s.invalidReference, s.op?.id, s.op?.target_session_id]);
    function actionCurrent() {
      // Own the action by what was painted before synchronizing native URL or
      // cross-tab storage. A stale A button may repaint B, never act on B.
      const displayed = ui.identity;
      syncSelection();
      const readable = controller.select(currentSource);
      return readable && displayed === identity(controller.view());
    }
    function render(s) {
      if (disposed || !ui) return;
      if (s.source !== selected(win.location.hash)) { syncSelection(); return; }
      ui.identity = identity(s);
      const d = draft();
      const locked = s.busy || Boolean(s.reference) || s.invalidReference;
      ui.form.hidden = !d.open || Boolean(s.reference) || s.invalidReference;
      renderModel(d, locked);
      ui.note.value = d.note;
      ui.source.textContent = d.reconfirm ? '来源已切换，请再次确认：' + (currentSource || '未选择会话')
        : currentSource ? '来源会话：' + currentSource : '请先在原生页面选择一个会话。';
      const has = Boolean(s.reference || s.error || s.legacyError);
      ui.panel.hidden = !has && ui.form.hidden;
      ui.status.textContent = s.op ? `${labels[s.op.status]} · 来源：${s.op.source_session_id}${s.op.message ? ' · ' + s.op.message : ''}`
        : s.reference ? (s.busy ? '正在核对接力请求…' : '请求结果未确认。请核对原请求；刷新后补充说明不会保留。') : '';
      const tuple = t => `${t.model} · ${t.model_provider} · ${t.reasoning_effort}`;
      if (s.op?.target_settings) ui.status.textContent += ` · 创建设置（${s.op.target_settings.origin === 'inherited' ? '沿用原会话列表设置' : '显式选择'}）：${tuple(s.op.target_settings)}`;
      const check = s.op?.target_check;
      ui.targetCheck.textContent = check && check.result !== 'not_contradicted'
        ? `接力期间列表曾报告 ${tuple(check.observed)}，与创建选择 ${s.op.target_settings ? tuple(s.op.target_settings) : '（未记录）'} 不同（${check.stage}）。`
        : '';
      ui.targetCheck.setAttribute('role', check?.result === 'model_differs' ? 'alert' : 'status');
      ui.error.textContent = [s.error || (s.uncertainContinue ? '继续请求结果尚未确认；仅查询状态，不会重发。' : ''), s.legacyError].filter(Boolean).join('；');
      ui.start.disabled = !currentSource || locked || Boolean(modelBlock(d));
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
    function modelBlock(d) {
      return ui?.field ? ui.field.blockReason() : d.choice || d.query != null ? '原生模型选择器不可用；请明确选择沿用原会话设置' : '';
    }
    function renderModel(d, locked) {
      if (!ui.field && nativeModules) {
        try {
          ui.field = TargetModel.createTargetModelField({ doc, win, native: nativeModules, menuHost: ui.menuHost,
            actionCurrent, onChange: () => render(controller.view()) });
          ui.modelBox.append(ui.field.element);
        } catch (e) { nativeModules = null; nativePromise = null; d.catalog = { ...d.catalog, loading: false, error: String(e.message || e).slice(0, 300), data: null }; }
      }
      if (ui.field) {
        try { ui.field.bind({ draft: d, source: currentSource, ...d.catalog, visible: !ui.form.hidden, locked }); }
        catch (e) {
          ui.field.dispose(); ui.field = null; nativeModules = null; nativePromise = null;
          d.catalog = { ...d.catalog, loading: false, error: String(e.message || e).slice(0, 300), data: null };
        }
      }
      ui.modelFallback.hidden = ui.resetModel.hidden = Boolean(ui.field);
      ui.resetModel.disabled = locked;
      ui.modelFallback.textContent = `沿用原会话设置。${d.catalog?.loading ? '正在读取原生可用模型…' : '无法读取原生可用模型：' + (d.catalog?.error || '尚未读取') + '；仅可沿用原会话设置。'}${modelBlock(d)}`;
    }
    function cancelCatalog() { ++catalogSeq; catalogRequest?.abort(); catalogRequest = null; }
    async function readCatalog(source, d) {
      cancelCatalog(); const seq = catalogSeq, ac = new AbortController(); catalogRequest = ac; requests.add(ac);
      const deadline = win.setTimeout(() => ac.abort(), 20000);
      d.catalog = { seq, loading: true, data: null, error: '' }; render(controller.view());
      const current = () => !disposed && seq === catalogSeq && source === currentSource && d.open && !controller.view().reference;
      try {
        nativePromise ||= loadNative(appBase, win).catch(e => { nativePromise = null; throw e; });
        const [native, data] = await Promise.all([nativePromise, (async () => {
          const res = await win.fetch(new URL('api/sessions', appBase), { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: ac.signal });
          if (!res.ok) {
            if (current() && (res.status === 401 || res.status === 403)) authLost();
            throw Error(`原生模型读取失败（${res.status}）`);
          }
          return res.json();
        })()]);
        if (!current()) return;
        nativeModules = native; d.catalog = { seq, loading: false, data, error: '' };
      } catch (e) {
        if (!current()) return;
        d.catalog = { seq, loading: false, data: null, error: String(e.message || '原生模型读取失败').slice(0, 300) };
      } finally {
        win.clearTimeout(deadline); requests.delete(ac); ac.abort(); if (catalogRequest === ac) catalogRequest = null;
        if (current()) render(controller.view());
      }
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
      controller.stop(clear); drafts.clear(); win.clearTimeout(timer); win.clearTimeout(selectionTimer); abort.abort(); panelAbort?.abort();
      for (const ac of requests) ac.abort(); requests.clear();
      rootObserver.disconnect(); shellObserver.disconnect(); mainObserver.disconnect(); actionsObserver.disconnect();
      removeUI(); nativeModules = nativePromise = null; doc.getElementById('handoffCompatibility')?.remove(); style.remove();
      if (clear) onLogout();
    }
    function removeUI() {
      ui?.field?.dispose(); ui?.menuHost.remove(); ui?.button.remove(); ui?.panel.remove(); ui = null;
    }
    function authLost() {
      if (disposed) return;
      dispose(true);
      const warning = el('p', '会话接力不可用：登录已失效或没有权限，请重新登录。', { id: 'handoffCompatibility', role: 'alert' });
      doc.querySelector('#root > .app > .main')?.prepend(warning);
    }
    controller = createController({ post, storage: {
      get length() { return win.localStorage.length; }, key: i => win.localStorage.key(i),
      getItem: k => win.localStorage.getItem(k), setItem: (k, v) => win.localStorage.setItem(k, v), removeItem: k => win.localStorage.removeItem(k),
    }, key, uuid: () => Core.requestUUID(win.crypto),
      changed: render, authLost });
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
        panelAbort?.abort(); removeUI(); win.clearTimeout(timer);
        if (nextShell && !doc.getElementById('handoffCompatibility')) {
          const warning = el('p', '会话接力不可用：此版本缺少兼容的 Details 操作栏。', { id: 'handoffCompatibility', role: 'status' });
          (main || nextShell).append(warning);
        }
        return;
      }
      doc.getElementById('handoffCompatibility')?.remove();
      if (ui?.button.isConnected && ui.button.parentNode === details.parentNode) return;
      panelAbort?.abort(); removeUI(); panelAbort = new AbortController();
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
      const hint = el('p', '新 Pi 独立恢复 → 旧会话审阅 → 新会话核对一次后接续。仅支持 Pi；既有负责人身份和关系自动由新会话继承，无负责人则不创建。', { class: 'handoffHint' });
      const label = el('label', '补充说明（可选，最多 1000 字）', { for: 'handoffNote' });
      const note = el('textarea', '', { id: 'handoffNote', rows: '2', maxlength: '1000' });
      const modelBox = el('div'), modelFallback = el('p', '', { class: 'handoffHint' });
      const resetModel = el('button', '沿用原会话设置', { type: 'button' }); modelBox.append(modelFallback, resetModel);
      const menuHost = el('div', '', { id: 'handoffMenuHost' }); doc.body.append(menuHost);
      const start = el('button', '确认接力', { type: 'submit' }), cancel = el('button', '取消', { type: 'button' });
      const buttons = el('div', '', { class: 'handoffRow' }); buttons.append(start, cancel);
      form.append(source, hint, modelBox, label, note, buttons);
      const status = el('p', '', { role: 'status', 'aria-live': 'polite' }), error = el('p', '', { role: 'alert' });
      const retry = el('button', '核对原请求', { type: 'button' }), bypass = el('button', '未校对，仍继续', { type: 'button' });
      const link = el('a', '打开新会话'), done = el('button', '收起记录', { type: 'button' });
      const row = el('div', '', { class: 'handoffRow' }); row.append(retry, bypass, link, done);
      const targetCheck = el('p', '', { id: 'handoffTargetCheck', role: 'status' }); panel.append(form, status, targetCheck, error, row);
      ui = { button, panel, form, source, note, start, status, error, retry, bypass, link, done, targetCheck, modelBox, modelFallback, resetModel, menuHost, field: null };
      onUI(panel, 'scroll', () => ui?.field?.position());
      onUI(resetModel, 'click', () => {
        if (!actionCurrent() || controller.view().reference || controller.view().busy || controller.view().invalidReference) return;
        Object.assign(draft(), { choice: null, query: null, choiceStale: false, effortNote: '' }); render(controller.view());
      });
      onUI(button, 'click', () => {
        if (!actionCurrent()) return;
        const s = controller.view(), d = draft();
        if (!s.reference && !s.invalidReference) {
          const opening = !d.open; d.open = true; d.confirmedSource = currentSource; d.reconfirm = false;
          if (opening && currentSource) readCatalog(currentSource, d);
        }
        render(s);
        if (!s.reference) note.focus();
      });
      onUI(note, 'input', () => {
        const value = note.value;
        if (actionCurrent()) { draft().note = value; note.value = value; }
      });
      onUI(form, 'submit', e => {
        e.preventDefault();
        const confirmed = draft().confirmedSource;
        const fresh = actionCurrent();
        const d = draft(), now = currentSource;
        if (!fresh || now !== confirmed || now !== d.confirmedSource) {
          const opening = !d.open; d.open = true; d.confirmedSource = now; d.reconfirm = true;
          if (opening && now && !controller.view().reference && !controller.view().invalidReference) readCatalog(now, d);
          render(controller.view()); return;
        }
        if (modelBlock(d) || controller.view().invalidReference) { render(controller.view()); return; }
        ui.field?.close();
        controller.start(now, d.note, d.choice || undefined);
      });
      onUI(cancel, 'click', () => {
        if (!actionCurrent()) return;
        const d = draft(); cancelCatalog(); ui.field?.close();
        Object.assign(d, { open: false, note: '', choice: null, query: null, choiceStale: false, effortNote: '', catalog: null });
        render(controller.view()); button.focus();
      });
      onUI(form, 'keydown', e => { if (e.key === 'Escape') { e.preventDefault(); cancel.click(); } });
      onUI(retry, 'click', () => { if (actionCurrent()) controller.recover(); });
      onUI(bypass, 'click', () => { if (actionCurrent()) controller.continueUnreviewed(); });
      onUI(link, 'click', e => {
        e.preventDefault(); if (!actionCurrent()) return;
        const id = controller.view().op?.target_session_id; if (id) win.location.replace(targetURL(win.location.href, id));
      });
      onUI(done, 'click', () => {
        if (!actionCurrent()) return;
        controller.forget();
        if (!controller.view().reference) {
          cancelCatalog(); Object.assign(draft(), { note: '', open: false, choice: null, query: null, choiceStale: false, effortNote: '', catalog: null });
        }
        render(controller.view()); button.focus();
      });
      render(controller.view());
      if (controller.view().reference?.operation_id && !controller.view().op && !doc.hidden) controller.poll();
    }
    const rootObserver = new MutationObserver(install), shellObserver = new MutationObserver(install),
      mainObserver = new MutationObserver(install), actionsObserver = new MutationObserver(install);
    const root = doc.getElementById('root');
    if (root) rootObserver.observe(root, { childList: true }); // only shell replacements, never the transcript tree
    on(doc, 'click', e => { if (e.target.closest?.('#logoutBtnSide')) dispose(true); });
    on(win, 'pagehide', () => dispose());
    on(doc, 'visibilitychange', () => {
      win.clearTimeout(timer); win.clearTimeout(selectionTimer);
      if (!doc.hidden && !disposed) { monitorSelection(); if (controller.view().reference?.operation_id) controller.poll(); }
    });
    const selectionEvent = () => win.queueMicrotask(() => { if (!disposed && !doc.hidden) syncSelection(); });
    for (const type of ['click', 'keyup', 'pointerup']) on(doc, type, selectionEvent);
    for (const type of ['hashchange', 'popstate', 'focus']) on(win, type, selectionEvent);
    on(win, 'storage', e => {
      if (e.key === key || e.key === null) controller.migrateLegacy();
      if (e.key === null || e.key === key + ':source:' + encodeURIComponent(currentSource)) {
        controller.select(currentSource);
        if (!doc.hidden && controller.view().reference?.operation_id && !controller.view().op) controller.poll();
      }
    });
    syncSelection(); install(); monitorSelection(); controller.migrateLegacy();
    return { controller, dispose };
  }
  const api = { mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CodoxearHandoffPanel = api;
})(typeof window !== 'undefined' ? window : globalThis);
