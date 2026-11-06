/* Independent same-origin extension; no upstream bundle state or session-card reads. */
(function (global) {
  'use strict';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const labels = Object.freeze({ waiting_source: '等待源会话写交接', creating: '创建新会话', completed: '完成', failed: '失败' });
  const active = new Set(['waiting_source', 'creating']);
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
  const targetKeys = ['model_provider', 'model', 'reasoning_effort'];
  const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(k => Object.hasOwn(value, k));
  function targetSettings(value, extra = []) {
    if (!exactKeys(value, [...targetKeys, ...extra]) || !targetKeys.every(k => bounded(value[k], 256)
      && value[k] === value[k].trim() && !/[\u007f-\u009f]/.test(value[k]))
      || value.reasoning_effort !== value.reasoning_effort.toLowerCase()) throw Error('目标模型设置格式不兼容');
    return Object.freeze(Object.fromEntries(targetKeys.map(k => [k, value[k]])));
  }
  function requestedTarget(value) {
    const target = targetSettings(value);
    if (target.model.toLowerCase() === 'default') throw Error('目标模型必须从原生可用列表选择');
    return target;
  }
  function operation(value) {
    if (!value || !UUID.test(value.id) || !UUID.test(value.request_id) || !bounded(value.source_session_id, 512)
      || !Object.hasOwn(labels, value.status)
      || (value.target_session_id !== null && (!bounded(value.target_session_id, 512) || value.target_session_id === value.source_session_id))) throw Error('接力状态格式不兼容');
    try {
      if (Object.hasOwn(value, 'target_settings')) {
        targetSettings(value.target_settings, ['origin']);
        if (!['inherited', 'explicit'].includes(value.target_settings.origin)) throw Error();
      }
    } catch (_) { throw Error('接力状态格式不兼容'); }
    return { ...value, message: typeof value.message === 'string' ? value.message.slice(0, 1000) : '' };
  }
  function encode(ref) {
    // Same key guards old tabs; only explicit pre-ACK intent needs an unreadable envelope.
    return JSON.stringify(ref.request_id && ref.target && !ref.operation_id
      ? { version: 2, pending_start: { request_id: ref.request_id, source_session_id: ref.source_session_id, target: ref.target } } : ref);
  }
  function retained(raw) {
    try {
      const r = JSON.parse(raw);
      if (r && (Object.hasOwn(r, 'version') || Object.hasOwn(r, 'pending_start'))) {
        const p = r.pending_start;
        if (!exactKeys(r, ['version', 'pending_start']) || r.version !== 2
          || !exactKeys(p, ['request_id', 'source_session_id', 'target'])
          || typeof p.request_id !== 'string' || !UUID.test(p.request_id) || !bounded(p.source_session_id, 512)) return null;
        return { request_id: p.request_id, source_session_id: p.source_session_id, target: requestedTarget(p.target) };
      }
      // Only the versioned explicit-start envelope carries a pending model choice.
      if (r && Object.hasOwn(r, 'target')) return null;
      if (r && UUID.test(r.operation_id) && bounded(r.source_session_id, 512))
        return { operation_id: r.operation_id, source_session_id: r.source_session_id };
      if (r && UUID.test(r.request_id) && bounded(r.source_session_id, 512))
        return { request_id: r.request_id, source_session_id: r.source_session_id };
    } catch (_) { /* Invalid local reference is not a request. */ }
    return null;
  }
  // A source owns its reference, in-flight request and uncertainty. Selection only
  // chooses the view; it never transfers operation ownership between sources.
  function createController({ post, storage, key, uuid, changed = () => {}, authLost = () => {} }) {
    const states = new Map(), prefix = key + ':source:';
    let current = null, stopped = false;
    const sourceKey = source => prefix + encodeURIComponent(source);
    function read(source) {
      const raw = storage.getItem(sourceKey(source)), ref = retained(raw);
      if (raw && (!ref || ref.source_session_id !== source)) throw Error('接力恢复记录来源不匹配');
      return ref;
    }
    function state(source) {
      if (!states.has(source)) {
        const s = { source, reference: null, pendingBody: null, op: null, busy: false, error: '', invalidReference: false };
        try { s.reference = read(source); }
        catch (e) { s.invalidReference = true; s.error = String(e.message || '无法读取接力恢复记录'); }
        states.set(source, s);
      }
      return states.get(source);
    }
    const view = () => ({ ...(current ? state(current) : { source: null, reference: null, op: null, busy: false, error: '' }), stopped });
    const notify = s => { if (!stopped && (!s || s.source === current)) changed(view()); };
    function persist(s, next) {
      // Persist only one source key: tabs operating on other sources cannot erase it.
      if (next) storage.setItem(sourceKey(s.source), encode(next)); else storage.removeItem(sourceKey(s.source));
      s.reference = next;
    }
    const sameOwner = (a, b) => ['operation_id', 'request_id', 'source_session_id'].every(k => a?.[k] === b?.[k])
      && JSON.stringify(a?.target) === JSON.stringify(b?.target);
    function sync(s) {
      let next;
      try { next = read(s.source); } catch (e) { s.invalidReference = true; throw e; }
      if (s.invalidReference || !sameOwner(next, s.reference)) {
        s.reference = next; s.op = null; s.pendingBody = null;
        s.error = '';
      }
      s.invalidReference = false;
    }
    async function request(s, kind, body) {
      if (s.busy || stopped) return;
      const observed = s.reference;
      // Storage is the current owner, not the reference retained by this tab.
      // Check again on completion; localStorage is not an atomic cross-tab lock.
      function stillOwned() {
        if (sameOwner(observed, read(s.source))) return true;
        sync(s); return false;
      }
      s.busy = true; s.error = ''; notify(s);
      try {
        if (!stillOwned()) return;
        const next = operation((await post(kind, body)).operation);
        if (stopped || !stillOwned()) return;
        if (next.source_session_id !== s.source || (kind !== 'start' && next.id !== body.operation_id)) throw Error('接力响应身份不匹配');
        s.op = next;
        persist(s, { operation_id: next.id, source_session_id: s.source });
        s.pendingBody = null;
      } catch (e) {
        if (stopped) return;
        // Late failures must not clear a replacement operation either. A failed
        // storage read leaves ownership unproven, so never write through it.
        try {
          if (!stillOwned()) return;
        } catch (_) { s.invalidReference = true; s.error = '无法核对当前恢复记录，请刷新查询'; return; }
        s.error = String(e.message || '网络异常').slice(0, 1000);
        if (e.status === 401 || e.status === 403) { authLost(); return; }
        if (kind === 'status' && e.status === 404) {
          persist(s, null); s.op = null;
        }
        if (kind === 'start' && e.definitive) {
          try { persist(s, null); s.pendingBody = null; } catch (_) { s.error += '；恢复记录无法更新'; }
        } else if (kind === 'start') s.error += '；结果未知，请核对原请求，勿新建接力。';
      } finally { s.busy = false; notify(s); }
    }
    return {
      view,
      select(source) {
        current = bounded(source, 512) ? source : null;
        if (current) {
          try { sync(state(current)); }
          catch (e) { state(current).error = String(e.message || '无法读取接力恢复记录'); notify(); return false; }
        }
        notify(); return true;
      },
      async start(source, target) {
        if (stopped || !bounded(source, 512)) return;
        const s = state(source);
        if (s.busy) return;
        try { sync(s); } catch (_) { s.error = '无法读取恢复标识，未发送接力请求'; notify(s); return; }
        if (s.reference || stopped) return;
        let frozenTarget;
        try {
          if (target !== undefined) {
            frozenTarget = requestedTarget(target);
          }
        } catch (e) { s.error = e.message; notify(s); return; }
        const selection = frozenTarget ? { target: frozenTarget } : {};
        let request_id;
        try { request_id = uuid(); } catch (_) { /* No identity means no mutation. */ }
        if (!UUID.test(request_id)) { s.error = '浏览器无法生成请求标识'; notify(s); return; }
        try { persist(s, { source_session_id: source, request_id, ...selection }); }
        catch (_) { s.error = '无法保存恢复标识，未发送接力请求'; notify(s); return; }
        s.pendingBody = { source_session_id: source, request_id, ...selection };
        await request(s, 'start', s.pendingBody);
      },
      async recover() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.busy) return;
        try { sync(s); } catch (_) { s.error = '无法读取接力恢复记录，未发送请求'; notify(s); return; }
        if (!s.reference) return;
        if (s.reference.operation_id) return request(s, 'status', { operation_id: s.reference.operation_id });
        // User-requested lookup with the same UUID; never replay on mount.
        return request(s, 'start', s.pendingBody || s.reference);
      },
      async poll() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.reference?.operation_id) return request(s, 'status', { operation_id: s.reference.operation_id });
      },
      forget() {
        if (!current || stopped) return;
        const s = state(current);
        if (s.busy) return;
        try { sync(s); } catch (_) { s.error = '无法读取恢复记录'; notify(s); return; }
        if ((s.op && !['completed', 'failed'].includes(s.op.status)) || (s.reference && !s.op)) return;
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

  const api = { requestUUID, selected, namespace, targetURL, targetSettings, retained, operation, labels, active, createController };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CodoxearHandoffCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
