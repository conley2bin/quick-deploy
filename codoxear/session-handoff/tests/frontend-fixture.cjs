/* Loopback-only DEMO. Read-only actual native assets; synthetic APIs, no agents/auth/registry. */
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const modelFixture = process.env.HANDOFF_MODEL_FIXTURE === '1';
const modelCatalog = require('./target-model-fixture.cjs').catalog();
const heldCatalog = []; let catalogFault = '';
const requestLog = [];
const staticDir = fs.realpathSync(process.env.CODOXEAR_STATIC_DIR || (() => { throw Error('Set CODOXEAR_STATIC_DIR to read-only upstream/installed static assets'); })());
const base = '/demo';
const assets = path.resolve(__dirname, '../web');
const operations = new Map(), settings = new Map(), held = [];
let actions = [], operation = null, fault = '', status = 'recovering', allow = false, auth = true, message = 'DEMO 合成状态，不会创建真实 Pi';
const rows = ['source', 'other', 'target'].map((id, i) => ({ session_id: id, thread_id: 'pi-demo-' + id, agent_backend: 'pi',
  alias: 'DEMO · ' + ({ source: '当前项目', other: '另一个会话', target: '接力新会话' }[id]), cwd: '/DEMO/project',
  log_path: '/DEMO/never-read/' + id + '.jsonl', owned: false, transport: 'socket', busy: false, lost: false,
  model: 'DEMO-model', start_ts: Date.now() / 1000 - i * 30, updated_ts: Date.now() / 1000 - i * 30, transcript_state: 'bound' }));
const json = (res, value, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
const body = async req => { let s = ''; for await (const chunk of req) { s += chunk; if (s.length > 16000) throw Error('oversized'); } return s ? JSON.parse(s) : {}; };
const server = http.createServer(async (req, res) => {
  try {
    const u = new URL(req.url, 'http://localhost'), p = u.pathname.slice(base.length);
    requestLog.push({ method: req.method, path: u.pathname });
    if (!u.pathname.startsWith(base + '/')) return json(res, { error: 'demo_only' }, 404);
    if (p === '/__fixture__/state') {
      if (req.method === 'POST') {
        const b = await body(req);
        if ('catalogFault' in b) catalogFault = b.catalogFault;
        if (b.releaseCatalog) for (const reply of heldCatalog.splice(0)) reply();
        if (b.release) { for (const item of held.filter(item => item.source === b.release)) { json(item.res, { operation: item.operation }); held.splice(held.indexOf(item), 1); } }
        if (b.reset) { for (const item of held.splice(0)) item.res.destroy(); operations.clear(); settings.clear(); actions = []; operation = null; fault = ''; status = 'recovering'; allow = false; auth = true; message = 'DEMO 合成状态，不会创建真实 Pi'; rows.splice(3); }
        if (b.source) settings.set(b.source, { ...settings.get(b.source), ...Object.fromEntries(['status', 'message', 'allow', 'fault'].filter(k => k in b).map(k => [k, b[k]])) });
        else { if (b.status) status = b.status; if ('message' in b) message = b.message; if ('allow' in b) allow = b.allow; if ('fault' in b) fault = b.fault; }
        if ('auth' in b) auth = b.auth;
      }
      return json(res, { actions, operation, operations: [...operations.values()], held: held.map(({ source, kind }) => ({ source, kind })), fault, status, auth, requestLog, heldCatalog: heldCatalog.length });
    }
    if (p.startsWith('/__session_handoff__/') && !p.endsWith('.js')) {
      if (req.method !== 'POST') return json(res, { error: 'post_required' }, 405);
      if (!auth || !req.headers.cookie?.includes('fixture=demo')) return json(res, { error: 'login', message: 'DEMO 登录已过期' }, 401);
      const b = await body(req), kind = p.split('/').pop(); actions.push({ kind, body: b });
      operation = kind === 'start' ? operations.get(b.source_session_id) : [...operations.values()].find(op => op.id === b.operation_id);
      const source = kind === 'start' ? b.source_session_id : operation?.source_session_id;
      const config = { fault, status, allow, message, ...settings.get(source) };
      if (kind === 'start') {
        if (config.fault === 'unsupported') return json(res, { error: 'unsupported_backend', message: '仅支持 Pi；DEMO Codex 不受支持' }, 422);
        if (!operation) operation = { id: randomUUID(), request_id: b.request_id, source_session_id: source,
          target_session_id: 'target', status: config.status, message: config.message, reviewed: false, can_continue_unreviewed: config.allow, updated_at: new Date().toISOString() };
        if (modelFixture) operation.target_settings = { ...(b.target || { model_provider: 'provider-a', model: 'shared', reasoning_effort: 'high' }), origin: b.target ? 'explicit' : 'inherited' };
        operations.set(source, operation);
        if (config.fault === 'drop-start') { req.socket.destroy(); return; }
      } else if (kind === 'continue') {
        if (!operation || config.status !== 'unreviewed' || !config.allow) return json(res, { error: 'not_allowed', message: 'DEMO 不允许继续' }, 409);
        config.status = 'revising'; config.allow = false;
        if (settings.has(source)) settings.set(source, { ...settings.get(source), status: 'revising', allow: false });
        else { status = 'revising'; allow = false; }
      } else if (kind !== 'status') return json(res, { error: 'not_found' }, 404);
      if (!operation) return json(res, { error: 'not_found', message: 'DEMO 无此接力' }, 404);
      if (config.fault === 'status-error' && kind === 'status') return json(res, { error: 'unavailable', message: 'DEMO 暂时无法查询' }, 503);
      operation = { ...operation, status: config.status, message: config.message, can_continue_unreviewed: config.allow, reviewed: config.status === 'ready', updated_at: new Date().toISOString() };
      operations.set(source, operation);
      if (config.fault === 'hold-' + kind) { held.push({ source, kind, res, operation }); return; }
      return json(res, { operation });
    }
    if (p === '/api/login') { res.setHeader('Set-Cookie', 'fixture=demo; HttpOnly; SameSite=Strict; Path=/demo/'); return json(res, { ok: true }); }
    if (p.startsWith('/api/')) {
      if (!auth || !req.headers.cookie?.includes('fixture=demo')) return json(res, { error: 'DEMO login required' }, 401);
      if (p === '/api/logout') { res.setHeader('Set-Cookie', 'fixture=; Max-Age=0; Path=/demo/'); return json(res, { ok: true }); }
      if (p === '/api/me') return json(res, { ok: true });
      if (req.method === 'POST') actions.push({ kind: 'native', path: p, body: await body(req) });
      if (modelFixture && p === '/api/sessions' && req.method === 'POST') return json(res, { error: 'native_create_forbidden_in_model_fixture' }, 403);
      if (p === '/api/sessions' && req.method === 'POST') {
        if (rows.length === 3) rows.push({ ...rows[0], session_id: 'copy', thread_id: 'pi-demo-copy', alias: 'DEMO 原生复制', broker_pid: 424242 });
        return json(res, { broker_pid: 424242 });
      }
      if (p === '/api/sessions') {
        const data = { sessions: rows.map((r, i) => modelFixture ? { ...r, model_provider: i === 1 ? 'provider-b' : 'provider-a', model: i === 1 ? 'restricted' : 'shared', reasoning_effort: 'high' } : r), recent_cwds: [], new_session_defaults: modelFixture ? structuredClone(modelCatalog.new_session_defaults) : {}, tmux_available: false };
        if (catalogFault === 'warning') data.new_session_defaults.warnings = { pi: 'Synthetic reader failure' };
        if (catalogFault === 'empty') data.new_session_defaults.backends.pi.provider_models = {};
        if (catalogFault === 'no-efforts') data.new_session_defaults.backends.pi.reasoning_efforts_by_model['provider-b/restricted'] = [];
        if (catalogFault === 'hold') { heldCatalog.push(() => json(res, data)); return; }
        return json(res, data);
      }
      if (p.endsWith('/live')) { res.writeHead(200, { 'Content-Type': 'text/event-stream' }); return res.end(': DEMO\n\n'); }
      if (p.includes('/messages/')) return json(res, { events: [], transcript_state: 'bound', thread_id: 'pi-demo-' + p.split('/')[3], log_path: '/DEMO/never-read', live_cursor: null, history_cursor: null, has_older: false, busy: false, queue_len: 0 });
      return json(res, { ok: true, items: [], models: [], queue: [] });
    }
    if (['/', '/index.html', '/static/index.html'].includes(p)) {
      let html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8').replaceAll('__CODOXEAR_ASSET_VERSION__', 'handoff-demo').replaceAll('__CODOXEAR_ATTACH_MAX_BYTES__', '10485760');
      html = html.replace('<title>Codoxear</title>', '<title>Codoxear · DEMO 合成接力</title>').replace('</head>', '<script defer src="/demo/__session_handoff__/handoff.js"></script></head>');
      res.writeHead(200, { 'Content-Type': 'text/html', 'Cache-Control': 'no-store' }); return res.end(html);
    }
    if (p === '/__session_handoff__/handoff.js') { res.writeHead(200, { 'Content-Type': 'application/javascript' }); return res.end(['core.js', 'target-model.js', 'panel.js', 'bootstrap.js'].map(n => fs.readFileSync(path.join(assets, n), 'utf8')).join('\n')); }
    const relative = p.replace(/^\/static\//, '').replace(/^\//, ''), file = path.resolve(staticDir, relative);
    if (!file.startsWith(staticDir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) return json(res, { error: 'missing_asset' }, 404);
    const type = ({ '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json', '.woff2': 'font/woff2' })[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type }); fs.createReadStream(file).pipe(res);
  } catch (e) { json(res, { error: 'fixture_error', message: e.message }, 500); }
});
server.listen(Number(process.env.PORT || 8896), '127.0.0.1', () => console.log(`DEMO_READY http://127.0.0.1:${server.address().port}/demo/ (read-only native assets; synthetic APIs only)`));
