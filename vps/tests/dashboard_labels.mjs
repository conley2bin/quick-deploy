// Run real dashboard.js against a bounded DOM. All credentials/hosts are fake.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const fixture = JSON.parse(readFileSync(0, 'utf8'));
const source = readFileSync(new URL('../assets/dashboard.js', import.meta.url), 'utf8');
const elements = new Map();
const clipboard = [];
const downloads = [];
const blobs = new Map();
let document;
class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.value = '';
    this.hidden = false;
    this.open = false;
    this.className = '';
    this.isConnected = true;
    this.classList = {toggle: () => {}};
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML injection sink used'); }
  get childElementCount() { return this.children.length; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.text = ''; this.children = []; this.append(...children); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  async emit(type) {
    const event = {target: this, preventDefault() {}};
    const results = [];
    if (type === 'click' && this.onclick) results.push(this.onclick(event));
    for (const listener of this.listeners.get(type) || []) results.push(listener(event));
    await Promise.all(results);
  }
  click() { if (this.tagName === 'a') downloads.push({name: this.download, blob: blobs.get(this.href)}); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'href' || name === 'src') this[name] = ''; }
  focus() { document.activeElement = this; }
  select() { this.focus(); this.selectionStart = 0; this.selectionEnd = this.value.length; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit('close'); }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
  querySelector(selector) {
    if (selector === '[aria-current="true"]') return this.children.find(child => child.getAttribute('aria-current') === 'true');
    throw new Error('Unexpected selector: ' + selector);
  }
}
document = {
  body: new Element('body'),
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement(tag) { return new Element(tag); },
  querySelector(selector) {
    assert.equal(selector, 'dialog[open]');
    return ['qr-dialog', 'copy-dialog'].map(id => elements.get(id)).find(element => element.open);
  },
  execCommand: () => false,
};
const get = id => document.getElementById(id);
get('dashboard-data').textContent = JSON.stringify(fixture.model);
class LocalURL extends URL {
  static createObjectURL(blob) { const name = 'blob:fake-' + blobs.size; blobs.set(name, blob); return name; }
  static revokeObjectURL() {}
}
const navigator = {clipboard: {writeText: async value => { clipboard.push(value); }}};
vm.runInNewContext(source, {document, navigator, URL: LocalURL, Blob, Uint8Array,
  atob: value => Buffer.from(value, 'base64').toString('binary'), setTimeout: () => 0, clearTimeout() {}},
  {filename: 'dashboard.js'});

const checks = [];
const check = (name, fn) => { fn(); checks.push(name); };
const links = fixture.model.devices[0].links.filter(link => link.kind === 'node');
const cards = get('node-links').children;
check('native query determines VLESS labels and icons without new metadata', () => {
  assert.equal(cards.length, fixture.expected.length);
  cards.forEach((card, index) => {
    assert.equal(card.children[0].children[1].textContent, fixture.expected[index].tag);
    assert.equal(card.children[0].children[0].textContent, fixture.expected[index].icon);
    assert.equal(card.children[1].textContent, links[index].label);
  });
  assert.ok(!get('node-links').textContent.includes('CDN'));
});
check('malicious native fragment remains a literal heading', () => {
  const heading = cards[fixture.maliciousIndex].children[1];
  assert.equal(heading.textContent, fixture.maliciousLabel);
  assert.equal(heading.children.length, 0);
});
for (let index = 0; index < cards.length; index++) {
  const actions = cards[index].children[3].children;
  await actions[1].emit('click');
  assert.equal(clipboard.at(-1), links[index].uri, 'copy must retain exact native UTF-8 string');
  await actions[0].emit('click');
  assert.equal(get('qr-title').textContent, links[index].label);
  assert.equal(get('qr-image').src, links[index].qr);
  await get('qr-copy').emit('click');
  assert.equal(clipboard.at(-1), links[index].uri);
  await get('qr-download').emit('click');
  assert.equal(downloads.at(-1).name, links[index].qr);
  assert.deepEqual(Buffer.from(await downloads.at(-1).blob.arrayBuffer()), Buffer.from(fixture.model.files[links[index].qr], 'base64'));
  get('qr-dialog').close();
}
checks.push('every card and QR dialog copies exact original URI and downloads unchanged QR bytes');
navigator.clipboard.writeText = async () => { throw new Error('fake denied permission'); };
let fallback;
document.execCommand = command => { assert.equal(command, 'copy'); fallback = document.activeElement.value; return true; };
await cards[1].children[3].children[1].emit('click');
check('selection clipboard fallback keeps XHTTP percent/case/plus bytes', () => assert.equal(fallback, links[1].uri));
document.execCommand = () => false;
await cards[1].children[3].children[1].emit('click');
check('manual copy fallback keeps exact XHTTP URI', () => {
  assert.equal(get('copy-dialog').open, true);
  assert.equal(get('manual-copy').value, links[1].uri);
  assert.equal(get('manual-copy').selectionEnd, links[1].uri.length);
});
get('copy-dialog').close();
for (let index = 0; index < get('config-downloads').children.length; index++) {
  await get('config-downloads').children[index].emit('click');
  const download = fixture.model.devices[0].downloads[index];
  assert.deepEqual(Buffer.from(await downloads.at(-1).blob.arrayBuffer()), Buffer.from(fixture.model.files[download.path], 'base64'));
}
checks.push('file map native downloads retain exact bytes');
check('subscription client names remain unchanged', () => {
  const tags = get('subscription-links').children.map(card => card.children[0].children[1].textContent);
  assert.deepEqual(tags, ['mihomo · https://', 'shadowrocket · https://']);
});
await cards[1].children[3].children[0].emit('click');
await get('device-list').children[1].emit('click');
check('device change closes old QR and scopes payloads', () => {
  assert.equal(get('qr-dialog').open, false);
  assert.equal(get('qr-image').src, '');
  assert.equal(get('device-name').textContent, fixture.model.devices[1].name);
  assert.equal(get('node-links').children.length, 1);
  assert.equal(get('node-links').children[0].children[0].children[1].textContent, 'Hysteria2 · hysteria2://');
});
console.log(JSON.stringify({status: 'passed', checks}));
