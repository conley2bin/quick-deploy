/* Independent same-origin extension; no upstream bundle state or session-card reads. */
(function (global) {
  'use strict';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const labels = Object.freeze({ creating: '正在创建', launching: '正在启动', recovering: '新会话独立恢复中',
    waiting_source: '等待旧会话审阅', reviewing: '旧会话审阅中', revising: '新会话核对并修正中',
    ready: '新会话已接续', unreviewed: '尚未校对', conflict: '目标分歧，需处理',
    creation_unknown: '创建结果不明，请勿重复创建', delivery_unknown: '消息投递结果不明，请勿重发',
    interrupted: '接力已中断', failed: '接力失败' });
  const active = new Set(['creating', 'launching', 'recovering', 'waiting_source', 'reviewing', 'revising']);
  const bounded = (s, n) => typeof s === 'string' && s.length > 0 && s.length <= n && !/[\u0000-\u001f]/.test(s);
  function requestUUID(crypto) {
    // getRandomValues also works on native HTTP origins where randomUUID is absent.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function selected(hash) {
    const values = new URLSearchParams(String(hash).replace(/^#/, '')).getAll('session');
    return values.length === 1 && bounded(values[0], 512) ? values[0] : null;
  }
  function namespace(src, origin) {
    const u = new URL(src, origin);
    const marker = '/__session_handoff__/';
    const at = u.pathname.lastIndexOf(marker);
    if (u.origin !== origin || at < 0) throw Error('接力脚本地址不兼容');
    return new URL(u.pathname.slice(0, at) + marker, origin).href;
  }
  function targetURL(href, id) {
    if (!bounded(id, 512)) throw Error('目标会话标识无效');
    const u = new URL(href), hash = new URLSearchParams(u.hash.slice(1));
    hash.set('session', id); u.hash = hash.toString(); return u.href;
  }
  function operation(value) {
    if (!value || !UUID.test(value.id) || !UUID.test(value.request_id) || !bounded(value.source_session_id, 512)
      || !Object.hasOwn(labels, value.status) || typeof value.reviewed !== 'boolean'
      || typeof value.can_continue_unreviewed !== 'boolean'
      || (value.target_session_id !== null && (!bounded(value.target_session_id, 512) || value.target_session_id === value.source_session_id))) throw Error('接力状态格式不兼容');
    return { ...value, message: typeof value.message === 'string' ? value.message.slice(0, 1000) : '' };
  }
  function retained(raw) {
    try {
      const r = JSON.parse(raw);
      if (r && UUID.test(r.operation_id)) return { operation_id: r.operation_id, ...(r.pending_action === 'continue' ? { pending_action: 'continue' } : {}) };
      if (r && UUID.test(r.request_id) && bounded(r.source_session_id, 512))
        return { request_id: r.request_id, source_session_id: r.source_session_id };
    } catch (_) { /* Invalid local reference is not a request. */ }
    return null;
  }
  // Transport/state are injected so failure and refresh recovery can be tested without agents.
  function createController({ post, storage, key, uuid, changed = () => {}, authLost = () => {} }) {
    let reference = null, pendingBody = null, op = null, busy = false, stopped = false, error = '', uncertainContinue = false;
    try { reference = retained(storage.getItem(key)); } catch (_) { error = '无法读取接力恢复记录'; }
    uncertainContinue = reference?.pending_action === 'continue';
    const view = () => ({ reference, op, busy, error, uncertainContinue, stopped });
    const notify = () => changed(view());
    function persist(next) {
      // Save before mutation: without this reference a reload could create a second successor.
      if (next) storage.setItem(key, JSON.stringify(next)); else storage.removeItem(key);
      reference = next;
    }
    async function request(kind, body) {
      if (busy || stopped) return;
      busy = true; error = ''; notify();
      try {
        const reply = await post(kind, body);
        if (stopped) return;
        const next = operation(reply.operation);
        if ((kind === 'start' && next.source_session_id !== body.source_session_id)
          || (kind !== 'start' && next.id !== body.operation_id)) throw Error('接力响应身份不匹配');
        // A server can coalesce another active request for the same source; request_id may differ.
        op = next;
        uncertainContinue = uncertainContinue && next.status === 'unreviewed';
        persist({ operation_id: next.id, ...(uncertainContinue ? { pending_action: 'continue' } : {}) }); pendingBody = null;
      } catch (e) {
        if (stopped) return;
        error = String(e.message || '网络异常').slice(0, 1000);
        if (e.status === 401 || e.status === 403) { authLost(); return; }
        if (kind === 'start' && e.definitive) {
          try { persist(null); pendingBody = null; } catch (_) { error += '；恢复记录无法更新'; }
        } else if (kind === 'start') error += '；结果未知，请核对原请求，勿新建接力。';
        if (kind === 'continue') {
          if (e.definitive) {
            try { persist({ operation_id: body.operation_id }); uncertainContinue = false; }
            catch (_) { error += '；恢复记录无法更新，请刷新查询'; }
          }
          else { uncertainContinue = true; error += '；继续请求结果未知，仅查询状态，不会重发。'; }
        }
      } finally { busy = false; if (!stopped) notify(); }
    }
    return {
      view,
      async start(source, note = '') {
        if (busy || stopped || reference) return;
        if (!bounded(source, 512) || typeof note !== 'string' || note.length > 1000) {
          error = '请选择当前会话；补充说明最多 1000 字'; notify(); return;
        }
        let request_id;
        try { request_id = uuid(); } catch (_) { /* Crypto unavailable: no mutation. */ }
        if (!UUID.test(request_id)) { error = '浏览器无法生成请求标识'; notify(); return; }
        try { persist({ source_session_id: source, request_id }); }
        catch (_) { error = '无法保存恢复标识，未发送接力请求'; notify(); return; }
        pendingBody = { source_session_id: source, request_id, ...(note ? { note } : {}) };
        await request('start', pendingBody);
      },
      async recover() {
        if (!reference || stopped || busy) return;
        if (reference.operation_id) return request('status', { operation_id: reference.operation_id });
        // Explicit user reconciliation only, same UUID. No automatic start replay on page load.
        return request('start', pendingBody || reference);
      },
      async poll() { if (reference?.operation_id) return request('status', { operation_id: reference.operation_id }); },
      async continueUnreviewed() {
        if (op?.status !== 'unreviewed' || !op.can_continue_unreviewed || !op.target_session_id || uncertainContinue) return;
        if (busy || stopped) return;
        // Retain the pending action identity across reload, not a note or operation payload.
        try { persist({ operation_id: op.id, pending_action: 'continue' }); }
        catch (_) { error = '无法保存恢复标识，未发送继续请求'; notify(); return; }
        uncertainContinue = true;
        await request('continue', { operation_id: op.id });
      },
      forget() {
        if (busy || uncertainContinue || (op && !['ready', 'failed', 'conflict'].includes(op.status)) || (reference && !op)) return;
        try { persist(null); op = null; error = ''; notify(); } catch (_) { error = '无法清除恢复记录'; notify(); }
      },
      stop(clear = false) {
        stopped = true; pendingBody = null;
        if (clear) { try { storage.removeItem(key); } catch (_) { /* No further writes after logout. */ } }
      },
    };
  }

  const api = { requestUUID, selected, namespace, targetURL, retained, operation, labels, active, createController };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CodoxearHandoffCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
