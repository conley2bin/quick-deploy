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
      if (r && UUID.test(r.operation_id)) return { operation_id: r.operation_id,
        ...(bounded(r.source_session_id, 512) ? { source_session_id: r.source_session_id } : {}),
        ...(r.pending_action === 'continue' ? { pending_action: 'continue' } : {}) };
      if (r && UUID.test(r.request_id) && bounded(r.source_session_id, 512))
        return { request_id: r.request_id, source_session_id: r.source_session_id };
    } catch (_) { /* Invalid local reference is not a request. */ }
    return null;
  }
  // A source owns its reference, in-flight request and uncertainty. Selection only
  // chooses the view; it never transfers operation ownership between sources.
  function createController({ post, storage, key, uuid, changed = () => {}, authLost = () => {} }) {
    const states = new Map(), prefix = key + ':source:';
    let current = null, stopped = false, migrating = false, legacyError = '';
    const sourceKey = source => prefix + encodeURIComponent(source);
    function read(source) {
      const raw = storage.getItem(sourceKey(source)), ref = retained(raw);
      if (raw && (!ref || ref.source_session_id !== source)) throw Error('接力恢复记录来源不匹配');
      return ref;
    }
    function state(source) {
      if (!states.has(source)) {
        const s = { source, reference: null, pendingBody: null, op: null, busy: false, error: '', uncertainContinue: false };
        try { s.reference = read(source); s.uncertainContinue = s.reference?.pending_action === 'continue'; }
        catch (e) { s.error = String(e.message || '无法读取接力恢复记录'); }
        states.set(source, s);
      }
      return states.get(source);
    }
    const view = () => ({ ...(current ? state(current) : { source: null, reference: null, op: null, busy: false, error: '', uncertainContinue: false }), stopped, legacyError });
    const notify = s => { if (!stopped && (!s || s.source === current)) changed(view()); };
    function persist(s, next) {
      // Persist only one source key: tabs operating on other sources cannot erase it.
      if (next) storage.setItem(sourceKey(s.source), JSON.stringify(next)); else storage.removeItem(sourceKey(s.source));
      s.reference = next;
    }
    function sync(s) {
      const next = read(s.source);
      if (['operation_id', 'request_id', 'source_session_id', 'pending_action'].some(k => next?.[k] !== s.reference?.[k])) {
        s.reference = next; s.op = null; s.pendingBody = null;
        s.uncertainContinue = next?.pending_action === 'continue'; s.error = '';
      }
    }
    async function migrateLegacy() {
      if (stopped || migrating) return;
      migrating = true;
      try {
        const raw = storage.getItem(key), old = retained(raw);
        if (!raw) { legacyError = ''; return; }
        if (!old) throw Error('记录格式不兼容');
        let next = old, recovered = null;
        if (!old.source_session_id) {
          // Never associate an old operation with the currently selected session.
          recovered = operation((await post('status', { operation_id: old.operation_id })).operation);
          if (stopped) return;
          if (recovered.id !== old.operation_id) throw Error('接力响应身份不匹配');
          next = { ...old, source_session_id: recovered.source_session_id };
        }
        const s = state(next.source_session_id), existing = read(s.source);
        if (s.busy || (existing && !(existing.operation_id === next.operation_id && next.operation_id)
          && !(existing.request_id && existing.request_id === (recovered?.request_id || next.request_id)))) throw Error('来源已有另一条恢复记录，保留旧记录待核对');
        // A pending continuation is never weakened by a migrated older reference.
        if (existing?.pending_action === 'continue') next = { ...next, pending_action: 'continue' };
        persist(s, next);
        s.uncertainContinue = next.pending_action === 'continue';
        if (recovered) s.op = recovered;
        // The legacy identity survives a failed new-key write or a concurrent edit.
        if (storage.getItem(key) === raw) storage.removeItem(key);
        legacyError = ''; notify(s);
      } catch (e) {
        if (!stopped) {
          legacyError = '旧接力恢复记录仍保留，暂无法迁移：' + String(e.message || '查询失败').slice(0, 500);
          if (e.status === 401 || e.status === 403) authLost();
        }
      } finally { migrating = false; notify(); }
    }
    async function request(s, kind, body) {
      if (s.busy || stopped) return;
      s.busy = true; s.error = ''; notify(s);
      try {
        const next = operation((await post(kind, body)).operation);
        if (stopped) return;
        if (next.source_session_id !== s.source || (kind !== 'start' && next.id !== body.operation_id)) throw Error('接力响应身份不匹配');
        // Same-source server coalescing may legitimately return a different UUID.
        s.op = next;
        s.uncertainContinue = s.uncertainContinue && next.status === 'unreviewed';
        persist(s, { operation_id: next.id, source_session_id: s.source, ...(s.uncertainContinue ? { pending_action: 'continue' } : {}) });
        s.pendingBody = null;
      } catch (e) {
        if (stopped) return;
        s.error = String(e.message || '网络异常').slice(0, 1000);
        if (e.status === 401 || e.status === 403) { authLost(); return; }
        if (kind === 'start' && e.definitive) {
          try { persist(s, null); s.pendingBody = null; } catch (_) { s.error += '；恢复记录无法更新'; }
        } else if (kind === 'start') s.error += '；结果未知，请核对原请求，勿新建接力。';
        if (kind === 'continue') {
          if (e.definitive) {
            try { persist(s, { operation_id: body.operation_id, source_session_id: s.source }); s.uncertainContinue = false; }
            catch (_) { s.error += '；恢复记录无法更新，请刷新查询'; }
          } else { s.uncertainContinue = true; s.error += '；继续请求结果未知，仅查询状态，不会重发。'; }
        }
      } finally { s.busy = false; notify(s); }
    }
    return {
      view, migrateLegacy,
      select(source) {
        current = bounded(source, 512) ? source : null;
        if (current && !state(current).busy) {
          try { sync(state(current)); } catch (e) { state(current).error = String(e.message || '无法读取接力恢复记录'); }
        }
        notify();
      },
      async start(source, note = '') {
        if (stopped || !bounded(source, 512)) return;
        const s = state(source);
        if (s.busy) return;
        try { sync(s); } catch (_) { s.error = '无法读取恢复标识，未发送接力请求'; notify(s); return; }
        // A sourced legacy pending identity still locks A if migration storage failed.
        try {
          const old = retained(storage.getItem(key));
          if (old?.source_session_id === source) { await migrateLegacy(); if (retained(storage.getItem(key))?.source_session_id === source) return; }
        } catch (_) { s.error = '无法读取恢复标识，未发送接力请求'; notify(s); return; }
        if (s.reference || stopped) return;
        if (typeof note !== 'string' || note.length > 1000) { s.error = '补充说明最多 1000 字'; notify(s); return; }
        let request_id;
        try { request_id = uuid(); } catch (_) { /* No identity means no mutation. */ }
        if (!UUID.test(request_id)) { s.error = '浏览器无法生成请求标识'; notify(s); return; }
        try { persist(s, { source_session_id: source, request_id }); }
        catch (_) { s.error = '无法保存恢复标识，未发送接力请求'; notify(s); return; }
        s.pendingBody = { source_session_id: source, request_id, ...(note ? { note } : {}) };
        await request(s, 'start', s.pendingBody);
      },
      async recover() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.busy || !s.reference) return;
        if (s.reference.operation_id) return request(s, 'status', { operation_id: s.reference.operation_id });
        // Explicit same-UUID reconciliation only; never replay on mount.
        return request(s, 'start', s.pendingBody || s.reference);
      },
      async poll() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.reference?.operation_id) return request(s, 'status', { operation_id: s.reference.operation_id });
      },
      async continueUnreviewed() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.busy) return;
        try { sync(s); } catch (_) { s.error = '无法读取恢复标识，未发送继续请求'; notify(s); return; }
        if (s.op?.status !== 'unreviewed' || !s.op.can_continue_unreviewed || !s.op.target_session_id || s.uncertainContinue) return;
        try { persist(s, { operation_id: s.op.id, source_session_id: s.source, pending_action: 'continue' }); }
        catch (_) { s.error = '无法保存恢复标识，未发送继续请求'; notify(s); return; }
        s.uncertainContinue = true;
        await request(s, 'continue', { operation_id: s.op.id });
      },
      forget() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.busy) return;
        try { sync(s); } catch (_) { s.error = '无法读取恢复记录'; notify(s); return; }
        if (s.uncertainContinue || (s.op && !['ready', 'failed', 'conflict'].includes(s.op.status)) || (s.reference && !s.op)) return;
        try { persist(s, null); s.op = null; s.error = ''; notify(s); } catch (_) { s.error = '无法清除恢复记录'; notify(s); }
      },
      stop(clear = false) {
        stopped = true;
        for (const s of states.values()) s.pendingBody = null;
        if (clear) {
          const keys = new Set([key, ...[...states.keys()].map(sourceKey)]);
          try { for (let i = 0; i < storage.length; i++) { const k = storage.key(i); if (k?.startsWith(prefix)) keys.add(k); } } catch (_) { /* Known keys still clear. */ }
          for (const k of keys) { try { storage.removeItem(k); } catch (_) { /* No further writes after logout. */ } }
        }
      },
    };
  }

  const api = { requestUUID, selected, namespace, targetURL, retained, operation, labels, active, createController };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CodoxearHandoffCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
