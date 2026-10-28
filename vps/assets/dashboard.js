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
  let snapshot;
  try {
    snapshot = JSON.parse(byId('dashboard-data').textContent);
    if (snapshot.schema !== 1 || !snapshot.snapshot || !Array.isArray(snapshot.devices)) throw new Error('schema');
    if (!snapshot.devices.every(device => typeof device.name === 'string' && typeof device.enabled === 'boolean' && Array.isArray(device.links) && Array.isArray(device.downloads))) throw new Error('devices');
  } catch (_) {
    const error = byId('load-error');
    error.textContent = '无法读取连接快照。请通过 VPS 模块重新生成完整页面及其资源文件。';
    error.hidden = false;
    return;
  }
  const devices = snapshot.devices;
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
    const uri = link.uri;
    const description = `${device.name} · ${link.label}`;
    let copied = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(uri);
        copied = true;
      }
    } catch (_) { /* file:// clipboard permissions vary; try local selection next. */ }
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
      anchor.title = '快照缺少有效下载内容，请重新生成。';
      anchor.textContent += '（文件缺失）';
      anchor.onclick = null;
      return;
    }
    anchor.title = '';
    anchor.href = '#';
    anchor.onclick = event => {
      event.preventDefault();
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
        announce('无法准备下载文件，请重新生成完整快照。');
      } finally {
        if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    };
  };
  const showQR = (link, device) => {
    const path = localPath(link.qr, true);
    if (!path) { announce('缺少本地二维码文件，请重新生成。'); return; }
    qrLink = link;
    qrDevice = device;
    byId('qr-device').textContent = device.name;
    byId('qr-title').textContent = link.label;
    byId('qr-image').alt = `${device.name} · ${link.label} 的导入二维码`;
    byId('qr-error').hidden = true;
    byId('qr-image').hidden = false;
    byId('qr-image').src = path;
    byId('qr-download').textContent = '下载二维码';
    setDownload(byId('qr-download'), path);
    openQRDialog();
  };
  byId('qr-image').addEventListener('error', () => { byId('qr-error').hidden = false; byId('qr-image').hidden = true; });
  byId('qr-dialog').addEventListener('close', () => {
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
      return { name: parts.join(' / '), icon: transport === 'xhttp' ? 'X' : security === 'reality' ? 'R' : 'V', scheme };
    }
    if (/^(hy2|hysteria2|hysteria)$/i.test(protocol)) return { name: 'Hysteria2', icon: 'H2', scheme };
    return { name: protocol, icon: 'URI', scheme };
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
    card.append(topline, make('h3', '', link.label), make('p', 'card-description', isSubscription ? '使用对应客户端的订阅导入功能。' : '原生链接 · 连接凭据默认隐藏'));
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
      card.append(make('p', 'muted', '二维码文件缺失，请重新生成。'));
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
      text.append(make('span', 'device-option-name', device.name), make('span', 'device-option-state', device.enabled ? '快照中已启用' : '快照中已停用'));
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
    if (!devices.length) return;
    const device = devices[selectedIndex];
    byId('device-content').hidden = false;
    byId('device-name').textContent = device.name;
    byId('device-state').textContent = device.enabled ? '快照中已启用' : '快照中已停用';
    byId('device-state').className = `state-badge${device.enabled ? '' : ' disabled'}`;
    byId('disabled-notice').hidden = device.enabled;
    const nodes = byId('node-links');
    const subscriptions = byId('subscription-links');
    const downloads = byId('config-downloads');
    nodes.replaceChildren(); subscriptions.replaceChildren(); downloads.replaceChildren();
    if (device.enabled) {
      device.links.forEach(link => {
        if (!['node', 'subscription'].includes(link.kind) || typeof link.uri !== 'string' || typeof link.label !== 'string') return;
        (link.kind === 'node' ? nodes : subscriptions).append(renderCard(link, device));
      });
      device.downloads.forEach(download => {
        const path = localPath(download.path);
        if (!path) return;
        const anchor = make('a', 'button secondary download-link', download.label);
        setDownload(anchor, path);
        anchor.setAttribute('aria-label', `${device.name}：下载配置 ${download.label}`);
        downloads.append(anchor);
      });
    }
    if (!nodes.childElementCount) nodes.append(make('p', 'empty-state', device.enabled ? '此快照没有原生节点。' : '设备已停用，不提供节点。'));
    if (!subscriptions.childElementCount) subscriptions.append(make('p', 'empty-state', device.enabled ? '此快照没有客户端订阅。' : '设备已停用，不提供订阅。'));
    if (!downloads.childElementCount) downloads.append(make('p', 'muted', '此设备没有可下载的配置。'));
  };
  byId('device-search').addEventListener('input', renderDevices);
  const meta = snapshot.snapshot;
  const timestamp = String(meta.generated_at || '未记录');
  byId('generated-at').textContent = timestamp;
  byId('generated-at').dateTime = timestamp;
  const digest = String(meta.backup_sha256 || '');
  byId('backup-digest').textContent = digest ? `${digest.slice(0, 12)}…` : '未记录';
  byId('backup-digest').title = digest;
  byId('endpoint').textContent = String(meta.endpoint || '未记录');
  renderDevices();
  renderDevice();
  if (!devices.length) {
    byId('load-error').textContent = '此快照中没有设备。请确认账户状态后重新生成。';
    byId('load-error').hidden = false;
  }
})();
