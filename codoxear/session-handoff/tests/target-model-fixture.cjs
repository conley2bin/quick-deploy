/* Actual native ESM, copied byte-for-byte to TEMP; only application URL/boot graph is stubbed. */
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { pathToFileURL } = require('node:url');
async function actualNative(t, storage) {
  const source = process.env.CODOXEAR_STATIC_DIR || path.resolve(__dirname, '../../.runtime/upstream/codoxear/static');
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'handoff-native-picker-'));
  t.after(() => fs.rm(temp, { recursive: true, force: true }));
  await fs.writeFile(path.join(temp, 'package.json'), '{"type":"module"}');
  for (const name of ['app_new_session.js', 'app_launch.js', 'app_display.js', 'app_dialog_menu.js', 'app_storage.js'])
    await fs.copyFile(path.join(source, name), path.join(temp, name));
  await fs.writeFile(path.join(temp, 'app_application.js'), 'export const resolveAppUrl = p => new URL(p, "http://fixture/").href;');
  global.window = { localStorage: storage };
  const [session, display, menu] = await Promise.all(['app_new_session.js', 'app_display.js', 'app_dialog_menu.js'].map(name => import(pathToFileURL(path.join(temp, name)))));
  const controllers = [], forbidden = ['setNewSessionProvider', 'selectNewSessionModel', 'syncNewSessionProviderFromModelInput', 'applyNewSessionLaunchPreset'];
  return { ...display, ...menu, controllers, createNewSessionController(options) {
    const controller = session.createNewSessionController(options); controllers.push(controller);
    return new Proxy(controller, { get(target, key) {
      if (forbidden.includes(key)) return () => { throw Error('Forbidden native preference method: ' + key); };
      return target[key];
    } });
  } };
}
function catalog() {
  return { new_session_defaults: { default_backend: 'pi', backends: { pi: {
    provider_choices: ['provider-a', 'provider-b'], provider_choice: 'provider-a', model: 'shared',
    provider_models: { 'provider-a': ['shared', 'a-only', ...Array.from({ length: 15 }, (_, i) => 'model-' + i)], 'provider-b': ['shared', 'restricted'] },
    reasoning_effort: 'high', reasoning_efforts: ['low', 'high', 'xhigh'],
    reasoning_efforts_by_model: { 'provider-b/restricted': ['low', 'high'] },
  } } }, sessions: [
    { session_id: 'A', agent_backend: 'pi', model_provider: 'provider-a', model: 'a-only', reasoning_effort: 'xhigh' },
    { session_id: 'B', agent_backend: 'pi', model_provider: 'provider-b', model: 'shared', reasoning_effort: 'low' },
    { session_id: 'ghost', agent_backend: 'pi', model_provider: 'gone', model: 'ghost', reasoning_effort: 'high' },
  ] };
}
function fakeDOM() {
  const nodes = [];
  class Node {
    constructor(tag) {
      Object.assign(this, { tag, events: {}, children: [], value: '', textContent: '', style: {}, hidden: false, isConnected: true });
      const classes = new Set(); this.classList = { toggle: (v, yes) => { if (yes) classes.add(v); else classes.delete(v); }, remove: v => classes.delete(v), contains: v => classes.has(v) }; nodes.push(this);
    }
    setAttribute(k, v) { this[k] = String(v); }
    removeAttribute(k) { delete this[k]; }
    append(...ns) { this.children.push(...ns); for (const n of ns) { n.parentNode = this; n.parentElement = this; } }
    appendChild(n) { this.append(n); return n; }
    prepend(...ns) { this.append(...ns); this.children = [...ns, ...this.children.filter(n => !ns.includes(n))]; }
    replaceChildren(...ns) { for (const n of this.children) n.remove(); this.children = []; this.append(...ns); }
    after(n) { this.parentNode?.append(n); }
    remove() { this.isConnected = false; for (const n of [...this.children]) n.remove(); if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(n => n !== this); }
    focus() {} setSelectionRange() {} scrollIntoView() {}
    contains(n) { return n === this || this.children.some(c => c.contains(n)); }
    addEventListener(k, f, options) {
      const entry = { f, signal: options?.signal }; (this.events[k] ||= []).push(entry);
    }
    querySelector(s) { return this.queries?.[s] || null; }
    getBoundingClientRect() { return { left: 0, top: 0, bottom: 100, width: 390, height: 800 }; }
    fire(k, extra = {}) { for (const e of this.events[k] || []) if (!e.signal?.aborted) e.f({ preventDefault() {}, stopPropagation() {}, target: this, ...extra }); }
    click() { if (!this.disabled) this.fire('click'); }
  }
  const doc = new Node('doc'), head = new Node('head'), body = new Node('body');
  Object.assign(doc, { head, body, hidden: false, createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag),
    getElementById: id => nodes.find(n => n.id === id && n.isConnected) || null });
  const win = new Node('win'); Object.assign(win, { document: doc, innerHeight: 800 });
  return { nodes, doc, win, Node };
}
module.exports = { actualNative, catalog, fakeDOM };
