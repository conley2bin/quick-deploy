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
      if (epoch !== viewEpoch || !token) return;
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
  // A session bearer exists only in this closure. No storage, query token,
  // location reload, automatic POST retry, or client-side credential conversion.
  let token = '';
  let current = null;
  let synchronized = false;
  let expiresAt = 0;
  let expiryTimer;
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
  const clock = ms => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
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
  const canWrite = () => Boolean(token && expiresAt - Date.now() >= 1230000 && synchronized && current && current.writable && !current.busy && !current.pending.present && !activeJob && !submitting && !unresolved && digestPattern.test(current.generation || ''));
  const updateControls = () => {
    const allowed = canWrite();
    byId('add-device').disabled = !allowed;
    for (const id of ['rename-device', 'reset-device', 'remove-device']) byId(id).disabled = !allowed || !selectedDevice();
    if (devices.length < 2) byId('remove-device').disabled = true;
    const pending = current && current.pending;
    byId('recover-operation').hidden = !(pending && pending.present);
    // Writable is false for pending on some adapters; only the specific carrier
    // read-only reason must also disable recovery (backend independently checks).
    byId('recover-operation').disabled = !token || !synchronized || !pending || !pending.present || !pending.operation_id || current.busy || Boolean(activeJob) || submitting || expiresAt - Date.now() < 1230000 || !(current.writable || current.writable_reason === 'pending');
    byId('refresh-state').disabled = !token || submitting;
    byId('edit-submit').disabled = !allowed;
  };
  const cutoffText = () => `已停止接收新操作，已提交任务继续执行；仍可查看/复制链接，至 ${clock(expiresAt)}。`;
  const timeNote = () => `${expiresAt - Date.now() >= 1230000 ? `新增/改名/重置/删除可提交至 ${clock(expiresAt - 1230000)}；` : `${cutoffText()}`}管理页会话于 ${clock(expiresAt)} 结束。20 分钟是单项操作的超时上限，不代表需要等 20 分钟；会话结束不影响节点/已导入配置使用。此页不检测节点在线状态。`;
  const expire = text => {
    token = ''; synchronized = false; current = null; unresolved = null; activeJob = null;
    clearTimeout(expiryTimer); clearTimeout(stateTimer); clearTimeout(pollTimer);
    clearSecrets(); updateControls();
    byId('time-note').textContent = '管理页会话已结束；节点/已导入配置不受影响。';
    byId('session-status').textContent = '会话不可用';
    byId('carrier-status').textContent = '管理会话已断开';
    byId('write-reason').textContent = '连接资料已清除；已有导出副本仍需自行保管。';
    errorPanel(text || '会话已过期。请重新启动管理命令并打开 vps/index.html。');
    byId('state-note').textContent = '未读取设备名册，并非空设备列表。';
  };
  const api = async (path, body) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const headers = {'Accept': 'application/json'};
      if (token) headers.Authorization = `Bearer ${token}`;
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal});
      if (!response.ok) {
        const error = new Error('request'); error.status = response.status; throw error;
      }
      return await response.json();
    } finally { clearTimeout(timer); }
  };
  const applyState = state => {
    if (!state || state.schema !== 1 || !state.pending || typeof state.busy !== 'boolean' || typeof state.writable !== 'boolean') throw new Error('schema');
    const previousName = selectedDevice() && selectedDevice().name;
    clearSecrets();
    current = state; synchronized = true;
    const pending = state.pending.present;
    byId('carrier-status').textContent = state.writable ? '独立管理通道 · 已由启动者确认' : '只读 / 写入受限';
    const reasons = {independent_carrier_required: '只读：启动时未确认独立管理通道。请使用独立路线重新启动 manage --independent-carrier。', reconciliation_required: '上次操作结果需要核对，请检查终端并保留原操作记录。', busy: '模块正在执行原操作，暂时禁止新变更。', pending: '原操作待恢复；用下方恢复按钮核对原操作，不能开始新变更。', insufficient_session_time: cutoffText()};
    byId('write-reason').textContent = expiresAt - Date.now() < 1230000 ? cutoffText() : String(reasons[state.writable_reason] || state.writable_reason || (state.writable ? '管理路线在启动时选定，页面不能更改。独立性由操作者确认，不是自动探测。' : '未具备写入条件，请检查启动终端的独立 SSH 通道与会话剩余时间。'));
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
      byId('state-note').textContent = state.busy ? '模块忙，暂未提供资料。请稍后核对；不是空名册。' : '已提交资料不可用。请检查管理终端与备份，不会显示候选凭据。';
    }
    if (unresolved && state.last_job && state.last_job.request_id === unresolved.request_id) unresolved = null;
    updateControls();
  };
  const handleReadError = error => {
    if (error.status === 401 || error.status === 403 || error.status === 410) { expire(); return; }
    synchronized = false; clearSecrets(); updateControls();
    errorPanel('无法核对管理状态，连接资料已隐藏。点击“核对已提交状态”读取原任务/状态；不会自动重发变更。');
  };
  const refreshState = async () => {
    if (!token) return;
    try {
      const state = await api('/api/state');
      if (!token) return;
      applyState(state); errorPanel('');
      noteJob(state.last_job);
      if (unresolved) jobMessage(`上次提交的“${operationText(submitted.get(unresolved.request_id))}”响应丢失，尚未找到匹配任务。禁止再次变更；请保持终端并核对该操作。此按钮只读取状态，不重发 POST。`);
      if (state.last_job && (state.last_job.state === 'running' || (activeJob && state.last_job.job_id === activeJob))) startPolling(state.last_job.job_id);
      return state;
    } catch (error) { handleReadError(error); }
  };
  const outcomes = {
    committed: '原生变更已提交，正在读取新代次资料。',
    committed_export_failed: '原生变更已经提交；本地便利导出失败。请检查终端并修复导出，不要重做此操作。',
    rolled_back: '已回滚，正在核对已提交状态。',
    recovery_required: '结果需要恢复核对。不要重复提交变更。',
    rejected: '被拒绝；请核对当前状态和终端提示，不会自动重试。',
    unchanged: '未更改已提交状态。'
  };
  const pollJob = async () => {
    const id = activeJob;
    if (!token || !id) return;
    if (++pollCount > 610 || Date.now() - pollStarted > 1220000) {
      jobMessage('已达到 20 分钟观察上限。任务未被重复提交，请核对原任务/终端。');
      synchronized = false; clearSecrets(); updateControls(); return;
    }
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (!token || activeJob !== id) return;
      noteJob({job_id: id, ...job});
      const ctx = jobContext.get(id);
      if (job.state === 'running') {
        jobMessage(`${operationText(ctx, true)} · 已用 ${Math.floor(Number(job.elapsed_s) || 0)} 秒（超时上限 20 分钟）。请勿关闭启动终端或重复提交。`);
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
      if (token) jobMessage(`任务状态读取中断。“${operationText(jobContext.get(id))}”可能仍在执行；点击核对状态查看该任务，禁止重复提交。`);
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
    if (submitting || activeJob || !token) return;
    submitting = true; unresolved = payload; updateControls();
    submitted.set(payload.request_id, payload.action ? {action: payload.action, names: payload.names} : {action: 'recover', names: []});
    if (byId('edit-dialog').open) byId('edit-dialog').close();
    jobMessage(`正在提交一次“${operationText(submitted.get(payload.request_id), true)}”；确认结果前不会重复发送。`);
    try {
      const job = await api(path, payload);
      if (!token) return;
      if (!job || typeof job.job_id !== 'string') throw new Error('job');
      unresolved = null; noteJob({...job, request_id: payload.request_id}); startPolling(job.job_id);
    } catch (error) {
      // A failed HTTP status is an explicit rejection; a lost response is unknown.
      if (error.status) unresolved = null;
      if ([401, 403, 410].includes(error.status)) { expire(); return; }
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
  byId('refresh-state').addEventListener('click', refreshState);
  const tick = () => {
    if (!token) return;
    const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    if (!remaining) { expire(); return; }
    byId('session-status').textContent = `本机会话 · 剩余 ${Math.ceil(remaining / 60)} 分钟`;
    byId('time-note').textContent = timeNote();
    updateControls();
    if (remaining < 1230) byId('write-reason').textContent = cutoffText();
    expiryTimer = setTimeout(tick, 1000);
  };
  const observeState = async () => {
    if (!token) return;
    if (!activeJob && !submitting && !byId('edit-dialog').open) await refreshState();
    if (token) stateTimer = setTimeout(observeState, 30000);
  };
  window.addEventListener('pagehide', () => expire('请重新打开 vps/index.html 进入本机会话。'));
  const connect = async () => {
    const launchToken = location.hash.slice(1);
    history.replaceState(null, '', location.pathname); // Clear capability before any network request.
    if (!launchToken || !/^http:\/\/127\.0\.0\.1:\d+$/.test(location.origin)) {
      expire('请先运行 bash vps/manage.sh，再重新打开 vps/index.html 并点击进入。刷新此页不会自动恢复认证，也不是空设备列表。'); return;
    }
    try {
      const session = await api('/api/session', {launch_token: launchToken});
      if (typeof session.token !== 'string' || !session.token || !Number.isFinite(session.expires_in) || session.expires_in <= 0) throw new Error('session');
      token = session.token; expiresAt = Date.now() + Math.min(session.expires_in, 2700) * 1000;
      tick(); await refreshState();
      if (token) stateTimer = setTimeout(observeState, 30000);
    } catch (_) { expire('无法进入本机会话。请检查启动终端，重新打开 vps/index.html；会话过期时需重新启动 manage。'); }
  };
  connect();
})();
