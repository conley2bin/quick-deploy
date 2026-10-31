(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  // Only generated local filenames may become resource URLs. Native URIs never do.
  const localPath = (value, svg = false) => {
    if (typeof value !== 'string' || !value || (svg && !value.endsWith('.svg'))) return null;
    const parts = value.split('/');
    return parts.every(part => part !== '.' && part !== '..' && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(part)) ? value : null;
  };
  let snapshot = {devices: [], files: {}};
  let devices = [];
  let viewEpoch = 0;
  const blobURLs = new Set();
  let selectedIndex = 0;
  let qrLink = null;
  let qrDevice = null;
  let statusTimer;
  const announce = text => {
    clearTimeout(statusTimer);
    byId('status-message').textContent = text;
    statusTimer = setTimeout(() => { byId('status-message').textContent = ''; }, 6000);
  };
  // Native dialog supplies inert background/focus containment; explicit Tab cycling
  // also keeps focus stable across browsers when the first/last action is reached.
  const setupDialog = (dialog, closeButton) => {
    let opener;
    dialog.addEventListener('close', () => {
      if (opener && opener.isConnected) opener.focus();
    });
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const focusable = [...dialog.querySelectorAll('button:not(:disabled), a[href], textarea, input')].filter(element => !element.hidden && element.getClientRects().length);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    closeButton.addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    return () => { opener = document.activeElement; if (!dialog.open) dialog.showModal(); };
  };
  const openQRDialog = setupDialog(byId('qr-dialog'), byId('qr-close'));
  const openCopyDialog = setupDialog(byId('copy-dialog'), byId('copy-close'));
  const selectManualCopy = () => {
    byId('manual-copy').focus();
    byId('manual-copy').select();
    byId('manual-copy').setSelectionRange(0, byId('manual-copy').value.length);
  };
  byId('select-copy').addEventListener('click', selectManualCopy);
  byId('copy-dialog').addEventListener('close', () => { byId('manual-copy').value = ''; });
  const copyURI = async (link, device) => {
    // Capture the selected link before awaiting permissions; never recalculate from
    // a possibly changed device selection after the clipboard promise settles.
    const epoch = viewEpoch;
    const uri = link.uri;
    const description = `${device.name} · ${link.label}`;
    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(uri);
        copied = true;
      }
    } catch (_) { /* file:// clipboard permissions vary; try local selection next. */ }
    if (epoch !== viewEpoch) return;
    if (!copied) {
      const previousFocus = document.activeElement;
      const buffer = make('textarea', 'copy-buffer');
      buffer.value = uri;
      buffer.setAttribute('readonly', '');
      buffer.setAttribute('aria-label', '临时复制链接');
      (document.querySelector('dialog[open]') || document.body).append(buffer);
      buffer.select();
      buffer.setSelectionRange(0, uri.length);
      try { copied = document.execCommand('copy') === true; } catch (_) { copied = false; }
      buffer.remove();
      if (previousFocus && previousFocus.isConnected) previousFocus.focus();
    }
    if (copied) { announce(`已复制：${description}`); return; }
    byId('manual-copy-label').textContent = description;
    byId('manual-copy').value = uri;
    openCopyDialog();
    selectManualCopy();
    announce('自动复制未完成，请手动复制所选链接。');
  };
  const setDownload = (anchor, path) => {
    const encoded = snapshot.files && snapshot.files[path];
    const available = typeof encoded === 'string' && encoded.length > 0 && encoded.length % 4 === 0 && /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded);
    anchor.download = path.split('/').pop();
    anchor.classList.toggle('unavailable', !available);
    anchor.setAttribute('aria-disabled', String(!available));
    if (!available) {
      anchor.removeAttribute('href');
      anchor.title = '快照缺少有效下载内容，请核对状态。';
      anchor.textContent += '（文件缺失）';
      anchor.onclick = null;
      return;
    }
    anchor.title = '';
    anchor.href = '#';
    const epoch = viewEpoch;
    anchor.onclick = event => {
      event.preventDefault();
      if (epoch !== viewEpoch) return;
      let url;
      try {
        const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
        // file:// treats adjacent files as separate origins and may ignore the
        // download attribute. Backend-supplied exact bytes avoid file AJAX and
        // preserve native formats; QR display still reads the local SVG file.
        url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
        const transfer = make('a');
        transfer.href = url;
        transfer.download = path.split('/').pop();
        (document.querySelector('dialog[open]') || document.body).append(transfer);
        transfer.click();
        transfer.remove();
      } catch (_) {
        announce('无法准备下载文件，请核对已提交资料。');
      } finally {
        if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    };
  };
  const showQR = (link, device) => {
    const path = localPath(link.qr, true);
    if (!path) { announce('缺少本地二维码文件，请核对状态。'); return; }
    qrLink = link;
    qrDevice = device;
    byId('qr-device').textContent = device.name;
    byId('qr-title').textContent = link.label;
    byId('qr-description').textContent = `在对应客户端中扫码导入。二维码与“复制链接”是同一条${link.kind === 'subscription' ? '订阅 URL' : ' URI'}，包含此设备的连接凭据。`;
    byId('qr-image').alt = `${device.name} · ${link.label} 的导入二维码`;
    byId('qr-error').hidden = true;
    byId('qr-image').hidden = false;
    try {
      const bytes = decodeFile(path);
      const url = URL.createObjectURL(new Blob([bytes], {type: 'image/svg+xml'}));
      blobURLs.add(url);
      byId('qr-image').src = url;
    } catch (_) { announce('已提交资料缺少二维码字节，请核对状态。'); return; }
    byId('qr-download').textContent = '下载二维码';
    setDownload(byId('qr-download'), path);
    openQRDialog();
  };
  byId('qr-image').addEventListener('error', () => { byId('qr-error').hidden = false; byId('qr-image').hidden = true; });
  byId('qr-dialog').addEventListener('close', () => {
    const url = byId('qr-image').src;
    if (blobURLs.has(url)) { URL.revokeObjectURL(url); blobURLs.delete(url); }
    byId('qr-image').removeAttribute('src');
    byId('qr-download').removeAttribute('href');
    qrLink = null;
    qrDevice = null;
  });
  byId('qr-copy').addEventListener('click', () => { if (qrLink && qrDevice) copyURI(qrLink, qrDevice); });
  const protocolDisplay = link => {
    const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(link.uri);
    const protocol = scheme ? scheme[1] : String(link.protocol || 'URI');
    if (/^vless$/i.test(protocol)) {
      const parts = ['VLESS'];
      let transport = '';
      let security = '';
      try {
        // Inspect native query fields for display only; never serialize the URL
        // back into the link, its QR, or the clipboard payload.
        const query = new URL(link.uri).searchParams;
        transport = (query.get('type') || '').toLowerCase();
        security = (query.get('security') || '').toLowerCase();
      } catch (_) { /* Unknown/malformed VLESS remains a generic VLESS node. */ }
      if (transport === 'xhttp') parts.push('XHTTP');
      if (security === 'reality') parts.push('REALITY');
      else if (security === 'tls') parts.push('TLS');
      const role = transport === 'xhttp' ? 'CF XHTTP · 外层 TCP（经 CDN）' : security === 'reality' ? 'REALITY 直连 · 外层 TCP' : '';
      return { name: parts.join(' / '), icon: transport === 'xhttp' ? 'X' : security === 'reality' ? 'R' : 'V', scheme, role };
    }
    if (/^(hy2|hysteria2|hysteria)$/i.test(protocol)) return { name: 'Hysteria2', icon: 'H2', scheme, role: 'H2 直连 · 外层 UDP' };
    return { name: protocol, icon: 'URI', scheme, role: '' };
  };
  const clients = {mihomo: 'Clash Verge 等 Mihomo 客户端', shadowrocket: 'Shadowrocket 客户端'};
  const cardDescription = (link, display) => {
    if (link.kind === 'subscription') {
      const client = clients[String(link.protocol).toLowerCase()] || '对应客户端';
      return `${client}：在订阅功能中添加；二维码与复制的是同一个订阅 URL，包含本设备全部入口。`;
    }
    return `${display.role ? display.role + ' · ' : ''}只导入这一个入口；二维码与复制的是同一条 URI，凭据默认隐藏。`;
  };
  const renderCard = (link, device) => {
    const card = make('article', `connection-card ${link.kind}`);
    const topline = make('div', 'card-topline');
    const isSubscription = link.kind === 'subscription';
    const display = protocolDisplay(link);
    topline.append(make('span', 'protocol-icon', isSubscription ? '订' : display.icon));
    // Subscription names describe the target client, not the HTTPS transport.
    const name = isSubscription ? String(link.protocol || 'URI') : display.name;
    topline.append(make('span', 'protocol-tag', `${name}${display.scheme ? ` · ${display.scheme[1]}://` : ''}`));
    card.append(topline, make('h3', '', link.label), make('p', 'card-description', cardDescription(link, display)));
    const actions = make('div', 'card-actions');
    const show = make('button', 'button primary', '显示二维码');
    show.type = 'button';
    show.setAttribute('aria-label', `${device.name} · ${link.label}：显示二维码`);
    show.addEventListener('click', () => showQR(link, device));
    const copy = make('button', 'button secondary', '复制链接');
    copy.type = 'button';
    copy.setAttribute('aria-label', `${device.name} · ${link.label}：复制链接`);
    copy.addEventListener('click', () => copyURI(link, device));
    actions.append(show, copy);
    card.append(actions);
    const path = localPath(link.qr, true);
    if (path) {
      const download = make('a', 'text-action', '↓ 下载二维码 SVG');
      download.setAttribute('aria-label', `${device.name} · ${link.label}：下载二维码`);
      setDownload(download, path);
      card.append(download);
    } else {
      show.disabled = true;
      card.append(make('p', 'muted', '二维码文件缺失，请核对状态。'));
    }
    return card;
  };
  const renderDevices = () => {
    const query = byId('device-search').value.trim().toLocaleLowerCase();
    const list = byId('device-list');
    list.replaceChildren();
    let count = 0;
    devices.forEach((device, index) => {
      if (!device.name.toLocaleLowerCase().includes(query)) return;
      count++;
      const button = make('button', 'device-option');
      button.type = 'button';
      button.setAttribute('aria-current', String(index === selectedIndex));
      button.setAttribute('aria-controls', 'device-content');
      const monogram = make('span', 'device-monogram', [...device.name][0] || 'D');
      monogram.setAttribute('aria-hidden', 'true');
      const text = make('span', 'device-option-text');
      text.append(make('span', 'device-option-name', device.name), make('span', 'device-option-state', device.enabled ? '已提交 · 启用' : '已提交 · 停用'));
      button.append(monogram, text);
      button.addEventListener('click', () => {
        // A programmatic/assistive selection must not leave another device's
        // credentials open, even though native modal dialogs make the list inert.
        if (byId('copy-dialog').open) byId('copy-dialog').close();
        if (byId('qr-dialog').open) byId('qr-dialog').close();
        selectedIndex = index;
        renderDevice();
        renderDevices();
        const selected = list.querySelector('[aria-current="true"]');
        if (selected) selected.focus();
        announce(`当前设备：${device.name}`);
      });
      list.append(button);
    });
    byId('device-count').textContent = query ? `${count}/${devices.length}` : devices.length;
    byId('search-empty').hidden = count !== 0;
  };
  const renderDevice = () => {
    if (!devices.length) { byId('device-content').hidden = true; return; }
    const device = devices[selectedIndex];
    byId('device-content').hidden = false;
    byId('device-name').textContent = device.name;
    byId('device-state').textContent = device.enabled ? '已提交 · 启用' : '已提交 · 停用';
    byId('device-state').className = `state-badge${device.enabled ? '' : ' disabled'}`;
    byId('disabled-notice').hidden = device.enabled;
    const subscriptions = byId('subscription-links');
    subscriptions.replaceChildren();
    if (device.enabled) {
      device.links.forEach(link => {
        if (link.kind !== 'subscription' || typeof link.uri !== 'string' || typeof link.label !== 'string') return;
        subscriptions.append(renderCard(link, device));
      });
    }
    if (!subscriptions.childElementCount) subscriptions.append(make('p', 'empty-state', device.enabled ? '此快照没有客户端订阅。' : '设备已停用，不提供订阅。'));
  };
  // The only secret is the private bootstrap key embedded in this local file. Each
  // service run is a fresh hello; session keys live in this closure, never in
  // storage, cookies, URLs, logs or the DOM.
  let current = null;
  let synchronized = false;
  let foreground = true; // until state says service_mode persistent
  let stateTimer;
  let activeJob = null;
  let pollTimer;
  let pollCount = 0;
  let pollStarted = 0;
  let submitting = false;
  let unresolved = null;
  let edit = null;
  const namePattern = /^[a-z][a-z0-9-]{0,31}$/;
  const digestPattern = /^[a-f0-9]{64}$/;
  const selectedDevice = () => devices[selectedIndex];
  const errorPanel = text => {
    byId('load-error').textContent = text;
    byId('load-error').hidden = !text;
  };
  // Operation identity comes only from backend job fields or the payload this page
  // submitted, bound by request_id/job_id; never from the selected device.
  const submitted = new Map();
  const jobContext = new Map();
  const actions = ['add', 'rename', 'reset', 'remove', 'recover'];
  const validContext = ctx => ctx && actions.includes(ctx.action) && Array.isArray(ctx.names) && ctx.names.length === (ctx.action === 'rename' ? 2 : ctx.action === 'recover' ? 0 : 1) && ctx.names.every(name => typeof name === 'string' && namePattern.test(name));
  const noteJob = job => {
    if (!job || typeof job.job_id !== 'string') return;
    const ctx = validContext(job) ? {action: job.action, names: job.names} : submitted.get(job.request_id);
    if (ctx) jobContext.set(job.job_id, ctx);
  };
  const operationText = (ctx, running) => {
    if (!ctx) return '历史操作（未记录类型）';
    const [a, b] = ctx.names;
    return {add: `${running ? '正在' : ''}新增设备 ${a}`, rename: running ? `正在将 ${a} 改名为 ${b}…` : `将 ${a} 改名为 ${b}`, reset: `${running ? '正在' : ''}重置设备 ${a} 的凭据`, remove: `${running ? '正在' : ''}删除设备 ${a}`, recover: `${running ? '正在' : ''}恢复上次未完成操作`}[ctx.action];
  };
  const jobMessage = text => {
    byId('job-panel').hidden = false;
    byId('job-status').textContent = text;
  };
  const decodeFile = path => {
    const encoded = snapshot.files[path];
    if (typeof encoded !== 'string' || !encoded || encoded.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) throw new Error('file');
    return Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
  };
  const clearSecrets = () => {
    viewEpoch++;
    for (const id of ['qr-dialog', 'copy-dialog', 'edit-dialog']) if (byId(id).open) byId(id).close();
    for (const url of blobURLs) URL.revokeObjectURL(url);
    blobURLs.clear();
    byId('qr-image').removeAttribute('src');
    byId('qr-image').alt = '';
    byId('qr-device').textContent = '';
    byId('qr-title').textContent = '连接二维码';
    byId('manual-copy').value = '';
    byId('manual-copy-label').textContent = '';
    byId('qr-download').removeAttribute('href');
    byId('qr-download').onclick = null;
    qrLink = null; qrDevice = null; edit = null;
    snapshot = {devices: [], files: {}}; devices = [];
    for (const id of ['subscription-links', 'device-list']) byId(id).replaceChildren();
    byId('device-content').hidden = true;
    byId('device-name').textContent = '';
    byId('device-count').textContent = '—';
    byId('endpoint').textContent = '—';
    byId('backup-digest').textContent = '—';
    byId('backup-digest').title = '';
    byId('generated-at').textContent = '—';
  };
  const canWrite = () => Boolean(synchronized && current && current.writable && !current.busy && !current.pending.present && !activeJob && !submitting && !unresolved && digestPattern.test(current.generation || ''));
  const updateControls = () => {
    const allowed = canWrite();
    byId('add-device').disabled = !allowed;
    for (const id of ['rename-device', 'reset-device', 'remove-device']) byId(id).disabled = !allowed || !selectedDevice();
    if (devices.length < 2) byId('remove-device').disabled = true;
    const pending = current && current.pending;
    byId('recover-operation').hidden = !(pending && pending.present);
    // Writable is false for pending on some adapters; only the specific carrier
    // read-only reason must also disable recovery (backend independently checks).
    byId('recover-operation').disabled = !synchronized || !pending || !pending.present || !pending.operation_id || current.busy || Boolean(activeJob) || submitting || !(current.writable || current.writable_reason === 'pending');
    byId('refresh-state').disabled = submitting;
    byId('edit-submit').disabled = !allowed;
  };
  const offlineText = '后台服务未运行或身份无法验证。连接资料已隐藏，这不是空设备列表。';
  const disconnect = text => {
    synchronized = false; current = null;
    clearTimeout(pollTimer);
    clearSecrets(); updateControls();
    byId('session-status').textContent = '后台服务未连接';
    byId('carrier-status').textContent = '独立 SSH 通道状态未知';
    byId('write-reason').textContent = '无法核对后台服务，所有变更已禁用。';
    byId('time-note').textContent = foreground ? '本页是私有本地文件。重新运行 manage.sh，再刷新/重开页面。' : '本页是私有本地文件，可随时重新打开；后台服务启动后点击“重新连接并核对”。';
    errorPanel(text || offlineText);
    byId('state-note').textContent = '未读取设备名册，并非空设备列表。';
  };
  // ---- Signed local protocol v1 (see contract): hello proof, per-request MAC,
  // response MAC verified before any body is parsed.
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const subtle = crypto.subtle;
  const hex = bytes => [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  const fromHex = text => Uint8Array.from(text.match(/../g), pair => parseInt(pair, 16));
  const randomHex = () => hex(crypto.getRandomValues(new Uint8Array(16)));
  const sha256 = async bytes => hex(await subtle.digest('SHA-256', bytes));
  const hmacKey = raw => subtle.importKey('raw', raw, {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  const hmac = async (key, text) => new Uint8Array(await subtle.sign('HMAC', key, encoder.encode(text)));
  const hex32 = /^[0-9a-f]{32}$/;
  const boot = (() => {
    try {
      const value = JSON.parse(byId('manage-bootstrap').textContent);
      if (value.version !== 1 || !/^http:\/\/127\.0\.0\.1:\d+$/.test(value.origin) || !/^[0-9a-f]{64}$/.test(value.bootstrap_key)) return null;
      return value;
    } catch (_) { return null; }
  })();
  let bootKey = null;
  let session = null;
  let helloPending = null;
  const identityError = () => Object.assign(new Error('identity'), {identity: true});
  const hello = async () => {
    if (!boot) throw identityError();
    bootKey = bootKey || await hmacKey(fromHex(boot.bootstrap_key));
    const nc = randomHex();
    const body = JSON.stringify({version: 1, client_nonce: nc});
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let reply;
    try {
      const response = await fetch(boot.origin + '/api/hello', {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, body, cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal});
      if (!response.ok) throw identityError();
      reply = JSON.parse(decoder.decode(await response.arrayBuffer()));
    } catch (error) { throw error.identity ? error : Object.assign(new Error('offline'), {offline: true}); } finally { clearTimeout(timer); }
    if (!reply || reply.version !== 1 || reply.origin !== boot.origin || reply.client_nonce !== nc || !hex32.test(reply.epoch) || !hex32.test(reply.session_id) || !hex32.test(reply.server_nonce) || typeof reply.proof !== 'string') throw identityError();
    const context = ['1', reply.origin, reply.epoch, reply.session_id, nc, reply.server_nonce].join('\n');
    if (hex(await hmac(bootKey, 'qd-server-v1\n' + context)) !== reply.proof) throw identityError();
    return {id: reply.session_id, epoch: reply.epoch, key: await hmacKey(await hmac(bootKey, 'qd-session-v1\n' + context))};
  };
  const ensureSession = async () => {
    if (session) return session;
    helloPending = helloPending || hello().finally(() => { helloPending = null; });
    session = await helloPending;
    return session;
  };
  const signedFetch = async (method, path, text) => {
    const used = await ensureSession();
    const nonce = randomHex();
    const raw = encoder.encode(text);
    const mac = hex(await hmac(used.key, ['qd-request-v1', used.epoch, nonce, method, path, await sha256(raw)].join('\n')));
    const headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-QD-Session': used.id, 'X-QD-Nonce': nonce, 'X-QD-MAC': mac};
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(boot.origin + path, {method, headers, body: method === 'POST' ? text : undefined, cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal});
      const bytes = new Uint8Array(await response.arrayBuffer());
      const expected = hex(await hmac(used.key, ['qd-response-v1', used.epoch, nonce, String(response.status), await sha256(bytes)].join('\n')));
      const verified = response.headers.get('X-QD-MAC') === expected;
      return {used, status: response.status, ok: response.ok, verified, bytes};
    } catch (_) { throw Object.assign(new Error('offline'), {offline: true}); } finally { clearTimeout(timer); }
  };
  // GET may transparently re-hello once after a restart or evicted session. A POST
  // is sent once; any uncertain outcome is reconciled by reading, never replayed.
  const api = async (path, body) => {
    const method = body === undefined ? 'GET' : 'POST';
    const text = body === undefined ? '' : JSON.stringify(body);
    for (let attempt = 0; ; attempt++) {
      const reply = await signedFetch(method, path, text);
      if (reply.verified && reply.ok) return JSON.parse(decoder.decode(reply.bytes));
      // The message of an unverified response is never trusted; 401 only means reset the session.
      if (reply.status === 401 || !reply.verified) { if (session === reply.used) session = null; }
      if (reply.verified) throw Object.assign(new Error('request'), {status: reply.status});
      if (method === 'GET' && reply.status === 401 && attempt === 0) continue;
      throw identityError();
    }
  };
  const applyState = state => {
    if (!state || state.schema !== 1 || !state.pending || typeof state.busy !== 'boolean' || typeof state.writable !== 'boolean') throw new Error('schema');
    const previousName = selectedDevice() && selectedDevice().name;
    clearSecrets();
    current = state; synchronized = true; foreground = state.service_mode !== 'persistent';
    const pending = state.pending.present;
    const carrier = state.carrier;
    const reason = state.writable_reason;
    byId('session-status').textContent = '本机后台服务已连接';
    byId('time-note').textContent = foreground ? '保持 manage.sh 终端运行；关闭页面不会停止脚本；关闭终端或 Ctrl+C 会停止管理页面，现有节点凭据不受影响；本页无固定到期，单项操作最长 20 分钟。此页不检测节点在线状态。' : '后台服务常驻运行，关闭终端或页面不会停止它；刷新或重新打开此文件会自动重新认证。单项操作最长等待 20 分钟。此页不检测节点在线状态。';
    const direct = carrier && carrier.configured && carrier.mode === 'direct';
    byId('carrier-status').textContent = reason === 'carrier_invalid' ? '独立 SSH 通道配置损坏或无法读取'
      : reason === 'carrier_not_configured' || (carrier && !carrier.configured) ? '独立 SSH 通道未配置'
      : direct ? (foreground ? '直接SSH已配置（远端连接尚未验证）；修改会重启核心，独立性由操作者负责' : '直接SSH已配置（远端连接尚未验证）')
      : reason === 'carrier_listener_down' || (carrier && carrier.configured && !carrier.listening) ? '独立 SSH 通道已配置，但当前不可用'
      : state.writable || (carrier && carrier.listening) ? '独立 SSH 通道已配置并可用' : '独立 SSH 通道：写入受其他状态限制';
    const reasons = {carrier_invalid: '只读：独立 SSH 通道配置损坏或无法读取（不是未配置）。资料仍可查看，修改已禁用；请修复或重新设置通道配置。', carrier_not_configured: '只读：尚未配置独立 SSH 通道。订阅和资料可查看，新增、改名、重置、删除已禁用。', carrier_listener_down: '只读：独立 SSH 通道已配置，但本机监听未运行。资料可查看，修改已禁用；通道恢复后点击“重新连接并核对”。', independent_carrier_required: '只读：尚未配置独立 SSH 通道。资料可查看，修改已禁用。', invalid_source: '已提交资料来源无效，修改已禁用。', reconciliation_required: '上次操作结果需要核对，请保留原操作记录并点击核对。', busy: '模块正在执行原操作，暂时禁止新变更。', pending: '原操作待恢复；用下方恢复按钮核对原操作，不能开始新变更。'};
    byId('write-reason').textContent = String(reasons[reason] || reason || (state.writable ? '可提交变更；独立通道由操作者配置，页面不能更改。' : '未具备写入条件。'));
    if (pending) {
      jobMessage(`原操作尚待核对（${String(state.pending.mode || '未知类型')}）。凭据已隐藏。请用独立 SSH 通道显式恢复原操作，不要重新新增、重置或删除。`);
      byId('state-note').textContent = '存在 pending；不展示候选或旧凭据。';
    } else if (state.model) {
      const model = state.model;
      if (model.schema !== 1 || !model.snapshot || !Array.isArray(model.devices) || !model.files || !digestPattern.test(state.generation || '') || model.snapshot.backup_sha256 !== state.generation || !model.devices.every(device => typeof device.name === 'string' && typeof device.enabled === 'boolean' && Array.isArray(device.links) && Array.isArray(device.downloads))) throw new Error('model');
      snapshot = model; devices = model.devices;
      selectedIndex = Math.max(0, devices.findIndex(device => device.name === previousName));
      renderDevices(); renderDevice();
      const meta = model.snapshot;
      byId('generated-at').textContent = String(meta.generated_at || '未记录');
      byId('backup-digest').textContent = `${state.generation.slice(0, 12)}…`;
      byId('backup-digest').title = state.generation;
      byId('endpoint').textContent = String(meta.endpoint || '未记录');
      byId('state-note').textContent = devices.length ? '已提交的原生资料；不是实时连通性判断。' : '权威已提交名册为空。';
    } else {
      byId('state-note').textContent = state.busy ? '模块忙，暂未提供资料。请稍后核对；不是空名册。' : '已提交资料不可用。请检查后台服务与备份，不会显示候选凭据。';
    }
    if (unresolved && state.last_job && state.last_job.request_id === unresolved.request_id) unresolved = null;
    updateControls();
  };
  const handleReadError = error => {
    if (error.identity || error.offline) { disconnect(error.identity ? '无法验证后台服务身份（密钥或响应签名不符）。连接资料已隐藏，这不是空设备列表。' : offlineText); return; }
    synchronized = false; clearSecrets(); updateControls();
    errorPanel('无法核对管理状态，连接资料已隐藏。点击“重新连接并核对”读取原任务/状态；不会自动重发变更。');
  };
  const refreshState = async () => {
    try {
      const state = await api('/api/state');
      applyState(state); errorPanel('');
      noteJob(state.last_job);
      if (unresolved) jobMessage(`上次提交的“${operationText(submitted.get(unresolved.request_id))}”响应丢失，尚未找到匹配任务。禁止再次变更；请核对该操作。此按钮只读取状态，不重发 POST。`);
      if (state.last_job && (state.last_job.state === 'running' || (activeJob && state.last_job.job_id === activeJob))) startPolling(state.last_job.job_id);
      return state;
    } catch (error) { handleReadError(error); }
  };
  const outcomes = {
    committed: '原生变更已提交，正在读取新代次资料。',
    committed_export_failed: '原生变更已经提交；本地便利导出失败。请检查后台服务日志并修复导出，不要重做此操作。',
    rolled_back: '已回滚，正在核对已提交状态。',
    recovery_required: '结果需要恢复核对。不要重复提交变更。',
    rejected: '被拒绝；请核对当前状态和任务结果，不会自动重试。',
    unchanged: '未更改已提交状态。'
  };
  const pollJob = async () => {
    const id = activeJob;
    if (!id) return;
    if (++pollCount > 610 || Date.now() - pollStarted > 1220000) {
      jobMessage('已达到 20 分钟观察上限。任务未被重复提交，请核对原任务状态。');
      synchronized = false; clearSecrets(); updateControls(); return;
    }
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (activeJob !== id) return;
      noteJob({job_id: id, ...job});
      const ctx = jobContext.get(id);
      if (job.state === 'running') {
        jobMessage(`${operationText(ctx, true)} · 已用 ${Math.floor(Number(job.elapsed_s) || 0)} 秒（超时上限 20 分钟）。请勿重复提交，可随时查看任务状态。`);
        pollTimer = setTimeout(pollJob, 2000); return;
      }
      if (job.state !== 'finished' || !outcomes[job.outcome]) throw new Error('job');
      activeJob = null;
      const prefix = `${operationText(ctx, false)}：`;
      jobMessage(prefix + outcomes[job.outcome]);
      const state = await refreshState();
      if (state && !state.pending.present) jobMessage(prefix + outcomes[job.outcome] + (job.outcome === 'committed' ? ' 已读取最新已提交状态。' : ''));
      updateControls();
    } catch (error) {
      handleReadError(error);
      jobMessage(`任务状态读取中断。“${operationText(jobContext.get(id))}”可能仍在执行；点击核对状态查看该任务，禁止重复提交。`);
    }
  };
  const startPolling = id => {
    if (typeof id !== 'string' || !id || !/^[A-Za-z0-9_-]+$/.test(id)) return;
    clearTimeout(pollTimer);
    if (activeJob !== id) { pollCount = 0; pollStarted = Date.now(); }
    activeJob = id; updateControls();
    pollTimer = setTimeout(pollJob, 0);
  };
  const requestID = () => [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('');
  const submit = async (path, payload) => {
    if (submitting || activeJob) return;
    submitting = true; updateControls();
    // A signed GET first: it may silently re-hello (stale/evicted session) and proves the
    // payload still applies. Nothing has been sent as a mutation yet, so a failure here
    // keeps the form and leaves no unresolved request.
    const refuse = text => {
      if (byId('edit-dialog').open) byId('edit-error').textContent = text; else jobMessage(text);
      submitting = false; updateControls();
    };
    try {
      // A brand-new session per mutation: a long-lived page may hold a session near the server's
      // per-session nonce cap, which would drop it between the preflight GET and the POST.
      session = await hello();
      const fresh = await api('/api/state');
      const pending = fresh && fresh.pending;
      if (!fresh || fresh.schema !== 1 || !pending || fresh.busy || !(fresh.writable || (payload.expected_operation_id && fresh.writable_reason === 'pending'))) { refuse('后台服务当前不允许提交（忙、只读或待核对）。操作未发送；请点击“重新连接并核对”。'); return; }
      if (payload.expected_operation_id ? !pending.present || pending.operation_id !== payload.expected_operation_id : pending.present || fresh.generation !== payload.expected_generation) { refuse('已提交状态已变化。操作未发送；请取消后核对最新状态。'); return; }
    } catch (error) {
      refuse(error.identity ? '无法验证后台服务身份。操作未发送。' : error.offline ? '后台服务未运行。操作未发送，表单已保留，服务恢复后可再次提交。' : '无法核对当前状态。操作未发送。');
      return;
    }
    unresolved = payload;
    submitted.set(payload.request_id, payload.action ? {action: payload.action, names: payload.names} : {action: 'recover', names: []});
    if (byId('edit-dialog').open) byId('edit-dialog').close();
    jobMessage(`正在提交一次“${operationText(submitted.get(payload.request_id), true)}”；确认结果前不会重复发送。`);
    try {
      const job = await api(path, payload);
      if (!job || typeof job.job_id !== 'string') throw new Error('job');
      unresolved = null; noteJob({...job, request_id: payload.request_id}); startPolling(job.job_id);
    } catch (error) {
      // A failed HTTP status is an explicit rejection; a lost response is unknown.
      if (error.status) unresolved = null;
      synchronized = false;
      jobMessage(error.status === 409 ? '状态已变化、忙或写入被拒绝。请核对最新代次；不会自动重试。' : '提交结果尚未确认。只读取原任务/已提交状态，不会再次 POST。');
      await refreshState();
    } finally { submitting = false; updateControls(); }
  };
  const openEditDialog = setupDialog(byId('edit-dialog'), byId('edit-close'));
  byId('edit-cancel').addEventListener('click', () => byId('edit-dialog').close());
  byId('edit-dialog').addEventListener('close', () => {
    edit = null; byId('edit-name').value = ''; byId('confirm-name').value = ''; byId('confirm-impact').checked = false; byId('edit-error').textContent = ''; byId('confirm-target').textContent = '';
  });
  const openEdit = action => {
    if (!canWrite()) return;
    const device = selectedDevice();
    if (action !== 'add' && !device) return;
    if (action === 'remove' && devices.length < 2) { announce('暂不支持删除最后一个设备。'); return; }
    edit = {action, name: action === 'add' ? null : device.name, generation: current.generation};
    const dangerous = ['reset', 'remove'].includes(action);
    const titles = {add: '新增设备', rename: '修改设备名称', reset: '重置设备凭据', remove: '删除整个设备'};
    byId('edit-title').textContent = titles[action];
    byId('edit-description').textContent = {add: '创建一个设备账户，提交后提供 Mihomo 和 Shadowrocket 两种客户端订阅。', rename: '修改设备账户名称，保留 UUID / Auth / SubID。全部客户端仍可能短暂重连。', reset: '轮换 UUID / Auth / SubID，旧导入配置和订阅将失效。此设备需要重新导入。', remove: '撤销整个设备的全部入口和订阅，不是只隐藏卡片。已导出的副本仍存在，但不再授权连接。'}[action];
    byId('edit-name').hidden = dangerous; byId('edit-name-label').hidden = dangerous;
    byId('edit-name').required = !dangerous;
    byId('edit-name').value = action === 'rename' ? device.name : '';
    byId('danger-confirm').hidden = !dangerous;
    byId('confirm-target').textContent = dangerous ? `操作目标：${device.name}` : '';
    byId('confirm-name').required = dangerous;
    byId('edit-submit').textContent = dangerous ? '确认撤销旧凭据并提交' : '提交操作';
    byId('edit-submit').className = dangerous ? 'button danger' : 'button primary';
    openEditDialog();
    byId(dangerous ? 'confirm-impact' : 'edit-name').focus();
  };
  byId('edit-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!edit || !canWrite()) return;
    const {action, name, generation} = edit;
    if (generation !== current.generation) { byId('edit-error').textContent = '代次已变化，请取消后核对状态。'; return; }
    const dangerous = ['reset', 'remove'].includes(action);
    const nextName = byId('edit-name').value;
    if (!dangerous && (!namePattern.test(nextName) || (action === 'rename' && nextName === name) || devices.some(device => device.name === nextName))) {
      byId('edit-error').textContent = '名称须为 1–32 位：小写字母开头，仅含小写字母、数字、连字符；不得重名或与原名相同。'; return;
    }
    if (dangerous && (!byId('confirm-impact').checked || byId('confirm-name').value !== name)) {
      byId('edit-error').textContent = '请勾选撤销影响，并准确输入目标设备名称。'; return;
    }
    const payload = {request_id: requestID(), action, names: action === 'rename' ? [name, nextName] : [action === 'add' ? nextName : name], expected_generation: generation};
    if (dangerous) payload.confirm = {action, name};
    submit('/api/jobs', payload);
  });
  byId('add-device').addEventListener('click', () => openEdit('add'));
  byId('rename-device').addEventListener('click', () => openEdit('rename'));
  byId('reset-device').addEventListener('click', () => openEdit('reset'));
  byId('remove-device').addEventListener('click', () => openEdit('remove'));
  byId('recover-operation').addEventListener('click', () => {
    updateControls();
    if (byId('recover-operation').disabled) return;
    submit('/api/recover', {request_id: requestID(), expected_operation_id: current.pending.operation_id});
  });
  byId('device-search').addEventListener('input', renderDevices);
  byId('device-list').addEventListener('click', updateControls);
  // Periodic verified reads notice a restarted or recovered service; the page itself never expires.
  const observeState = async () => {
    if (!activeJob && !submitting && !byId('edit-dialog').open) await refreshState();
    stateTimer = setTimeout(observeState, 30000);
  };
  byId('refresh-state').addEventListener('click', () => { session = null; refreshState(); });
  const connect = async () => {
    await refreshState();
    stateTimer = setTimeout(observeState, 30000);
  };
  connect();
})();
