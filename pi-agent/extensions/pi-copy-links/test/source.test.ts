import assert from "node:assert/strict";
import test from "node:test";
import { browserCommand, webUrl } from "../src/browser.ts";
import {
  alignCodeBlocks, codeBlocks, codeSyntax, CopyStore, sourceCodeBlocks,
  type RenderedCodeBlock, type SourceCodeBlock,
} from "../src/source.ts";

test("logical code preserves tabs, blank lines, significant spaces and container indentation", () => {
  const cases = [
    ['```sh\nprintf "%s\\n" hello  \n\n```', 'printf "%s\\n" hello  \n'],
    ['- ```make\n  target:\n  \techo done\n  ```', 'target:\n\techo done'],
    ['> ```python\n> if True:\n>     print("中文")\n> ```', 'if True:\n    print("中文")'],
    ['    a\n    \tb', 'a\n\tb'],
    ['~~~sh\necho "```"\n~~~', 'echo "```"'],
    ['```\n```', ''],
  ];
  for (const [source, expected] of cases) assert.equal(codeBlocks(source!)[0]!.value, expected);
});

test("inline code and images are not code blocks; all fenced blocks are returned", () => {
  assert.equal(codeBlocks('`inline`\n\n![image](x.png)').length, 0);
  assert.deepEqual(codeBlocks('```\na\n```\n\n```\nb\n```').map(b => b.value), ['a', 'b']);
});

test("source spans retain container paths for fail-closed native alignment", () => {
  const source = '> > nested\nlazy\n> ```sh\n> \tcmd\n> ```\n\n```sh\n   cmd\n```';
  const blocks = sourceCodeBlocks(source);
  assert.deepEqual(blocks.map((block) => ({
    value: block.value, path: block.path, raw: block.raw, syntax: block.syntax,
  })), [
    {
      value: '\tcmd', path: ['blockquote'], raw: '```sh\n> \tcmd\n> ```',
      syntax: { kind: 'fenced', marker: '`', markerLength: 3, info: 'sh', closed: true },
    },
    {
      value: '   cmd', path: [], raw: '```sh\n   cmd\n```',
      syntax: { kind: 'fenced', marker: '`', markerLength: 3, info: 'sh', closed: true },
    },
  ]);
});

test("sequence alignment resolves strong paths, rejects ambiguity, and preserves duplicates", () => {
  const native = (text: string, path: RenderedCodeBlock['path']): RenderedCodeBlock => ({
    text, lang: 'sh', raw: `\`\`\`sh\n${text}\n\`\`\``, path,
    syntax: codeSyntax(`\`\`\`sh\n${text}\n\`\`\``),
  });
  const divergent = sourceCodeBlocks(
    '> > nested\nlazy\n> ```sh\n> \tcmd\n> ```\n\n```sh\n   cmd\n```',
  );
  assert.deepEqual(alignCodeBlocks(divergent, [native('   cmd', [])]), {
    sourceIndexes: [1], complete: true, ambiguous: false, comparisons: 3,
  });

  const ambiguous = sourceCodeBlocks(
    '> > nested\nlazy\n> ```sh\n> \tcmd\n> ```\n\n' +
    '> > other\n> ```sh\n>    cmd\n> ```',
  );
  assert.deepEqual(alignCodeBlocks(ambiguous, [native('   cmd', ['blockquote'])]), {
    sourceIndexes: [undefined], complete: true, ambiguous: true, comparisons: 2,
  });

  const repeated = sourceCodeBlocks('```sh\n\tcmd\n```\n\n```sh\n   cmd\n```');
  assert.deepEqual(alignCodeBlocks(repeated, [native('   cmd', []), native('   cmd', [])]), {
    sourceIndexes: [0, 1], complete: true, ambiguous: false, comparisons: 4,
  });
});

test("alignment uses linear pointer scans for several thousand duplicate blocks", () => {
  const count = 5000;
  const syntax = { kind: 'fenced', marker: '`', markerLength: 3, info: 'sh', closed: true } as const;
  const sources: SourceCodeBlock[] = Array.from({ length: count }, (_value, index) => ({
    value: 'same', lang: 'sh', meta: null, raw: '```sh\nsame\n```',
    startOffset: index * 20, endOffset: index * 20 + 14, path: [], syntax,
  }));
  const rendered: RenderedCodeBlock[] = Array.from({ length: count }, () => ({
    text: 'same', lang: 'sh', raw: '```sh\nsame\n```', path: [], syntax,
  }));
  const originalFrom = Array.from;
  Array.from = function(items: ArrayLike<unknown> | Iterable<unknown>, ...args: unknown[]) {
    if (!(Symbol.iterator in Object(items)) && 'length' in Object(items) &&
        Number((items as ArrayLike<unknown>).length) > count) {
      throw new Error('dense alignment matrix allocation');
    }
    return Reflect.apply(originalFrom, Array, [items, ...args]);
  } as typeof Array.from;
  try {
    const result = alignCodeBlocks(sources, rendered);
    assert.equal(result.complete, true);
    assert.equal(result.ambiguous, false);
    assert.equal(result.sourceIndexes[0], 0);
    assert.equal(result.sourceIndexes.at(-1), count - 1);
    assert.equal(result.comparisons, count * 2, "each directional source pointer visits one match per token");
  } finally { Array.from = originalFrom; }
});

test("copy references remain stable for mounted unchanged content; clear invalidates session", () => {
  const store = new CopyStore(); const owner = {};
  const first = store.set(owner, ['\techo a', '  b']);
  assert.equal(store.set(owner, ['\techo a', '  b'])[0], first[0]);
  assert.equal(store.get(first[0]!.url)?.text, '\techo a');
  assert.equal(store.get('pi-copy://foreign/1'), undefined);
  store.clear(); assert.equal(store.get(first[0]!.url), undefined);
});

test("CopyStore failure retires its namespace instead of reusing an unsafe URL", () => {
  const store = new CopyStore();
  const owner = {};
  const originalSet = Map.prototype.set;
  let actionSets = 0;
  Map.prototype.set = function(this: Map<unknown, unknown>, key: unknown, value: unknown) {
    if (typeof key === 'string' && key.startsWith(store.prefix) && ++actionSets === 2) {
      throw new RangeError('Map maximum size exceeded');
    }
    return originalSet.call(this, key, value);
  } as typeof Map.prototype.set;
  try { assert.throws(() => store.set(owner, ['a', 'b']), /Map maximum size exceeded/); }
  finally { Map.prototype.set = originalSet; }

  assert.equal(store.isTrusted(), false);
  assert.throws(() => store.set(owner, ['a', 'b']), /generation is retired/);
  const replacement = new CopyStore();
  const entries = replacement.set(owner, ['a', 'b']);
  assert.notEqual(replacement.prefix, store.prefix);
  assert.ok(entries[0]?.url.endsWith('/1'));
  assert.ok(entries[1]?.url.endsWith('/2'));
  assert.equal(replacement.get(entries[0]!.url)?.text, 'a');
  assert.equal(replacement.get(entries[1]!.url)?.text, 'b');
});

test("committed failing finalizer is quarantined from a fresh namespace", () => {
  const RealFinalizationRegistry = globalThis.FinalizationRegistry;
  const registries: Array<{
    callback: (holdings: string) => void;
    records: Array<{ holdings: string; token?: object }>;
    failRegister: boolean;
    failUnregister: boolean;
    fireFirst(): void;
  }> = [];
  class FakeFinalizationRegistry {
    callback: (holdings: string) => void;
    records: Array<{ holdings: string; token?: object }> = [];
    failRegister = true;
    failUnregister = true;
    constructor(callback: (holdings: string) => void) {
      this.callback = callback;
      registries.push(this);
    }
    register(_target: object, holdings: string, token?: object) {
      this.records.push({ holdings, token });
      if (this.failRegister) throw new RangeError('register committed then failed');
    }
    unregister(token: object) {
      if (this.failUnregister) throw new RangeError('rollback unregister failed');
      this.records = this.records.filter((record) => record.token !== token);
      return true;
    }
    fireFirst() {
      const record = this.records.shift();
      if (record) this.callback(record.holdings);
    }
  }
  globalThis.FinalizationRegistry = FakeFinalizationRegistry as unknown as typeof FinalizationRegistry;
  try {
    const retired = new CopyStore();
    assert.throws(() => retired.set({}, ['old']), (error: unknown) => {
      assert.match(String(error), /register committed then failed/);
      assert.match(String(error), /rollback unregister failed/);
      return true;
    });
    assert.equal(retired.isTrusted(), false);

    registries[0]!.failRegister = false;
    registries[0]!.failUnregister = false;
    const replacement = new CopyStore();
    registries[1]!.failRegister = false;
    registries[1]!.failUnregister = false;
    const entry = replacement.set({}, ['new'])[0]!;
    assert.notEqual(replacement.prefix, retired.prefix);
    assert.ok(entry.url.endsWith('/1'));
    assert.equal(replacement.get(entry.url)?.text, 'new');
    registries[0]!.fireFirst();
    assert.equal(replacement.get(entry.url)?.text, 'new', 'retired callback only reaches the retired map');
  } finally { globalThis.FinalizationRegistry = RealFinalizationRegistry; }
});

test("CopyStore cleanup attempts are noexcept and poison the generation", () => {
  const originalWeakGet = WeakMap.prototype.get;
  const originalMapDelete = Map.prototype.delete;
  const originalUnregister = FinalizationRegistry.prototype.unregister;

  for (const fault of ['get', 'delete', 'unregister'] as const) {
    const store = new CopyStore();
    const owner = {};
    store.set(owner, ['value']);
    if (fault === 'get') {
      WeakMap.prototype.get = function() { throw new RangeError('rollback get failed'); };
    } else if (fault === 'delete') {
      Map.prototype.delete = function() { throw new RangeError('rollback delete failed'); };
    } else {
      FinalizationRegistry.prototype.unregister = function() { throw new RangeError('rollback unregister failed'); };
    }
    try {
      let issues: ReturnType<CopyStore['delete']> = [];
      assert.doesNotThrow(() => { issues = store.delete(owner); }, fault);
      assert.ok(issues.some((issue) => issue.operation.includes(fault === 'get' ? 'owners.get' : fault)));
      assert.equal(store.isTrusted(), false);
    } finally {
      WeakMap.prototype.get = originalWeakGet;
      Map.prototype.delete = originalMapDelete;
      FinalizationRegistry.prototype.unregister = originalUnregister;
    }
  }
});

test("CopyStore finalizer delete failure is noexcept and makes actions untrusted", () => {
  const RealFinalizationRegistry = globalThis.FinalizationRegistry;
  let callback: ((holdings: string) => void) | undefined;
  class CapturingFinalizationRegistry {
    constructor(current: (holdings: string) => void) { callback = current; }
    register() {}
    unregister() { return true; }
  }
  globalThis.FinalizationRegistry = CapturingFinalizationRegistry as unknown as typeof FinalizationRegistry;
  const originalDelete = Map.prototype.delete;
  try {
    const store = new CopyStore();
    const entry = store.set({}, ['value'])[0]!;
    Map.prototype.delete = function() { throw new RangeError('finalizer delete failed'); };
    assert.doesNotThrow(() => callback?.(entry.url));
    assert.equal(store.isTrusted(), false);
    assert.equal(store.get(entry.url), undefined);
    assert.match(String(store.lookup(entry.url).error), /finalizer delete failed/);
  } finally {
    Map.prototype.delete = originalDelete;
    globalThis.FinalizationRegistry = RealFinalizationRegistry;
  }
});

test("browser launches use one URL argument without a shell or executable schemes", () => {
  const url = 'https://example.com/path?q=a&cmd=$(touch%20evil)#中文';
  const target = webUrl(url)!;
  assert.deepEqual(browserCommand(url, 'linux'), ['xdg-open', [target]]);
  assert.deepEqual(browserCommand(url, 'darwin'), ['open', [target]]);
  assert.deepEqual(browserCommand(url, 'win32'), ['rundll32.exe', ['url.dll,FileProtocolHandler', target]]);
  for (const value of ['file:///etc/passwd', 'javascript:alert(1)', 'pi-copy://x/1', '--help', 'https://example.com/\x1b]52;c;bad\x07', 'https://a/\nx']) {
    assert.equal(webUrl(value), undefined, value);
    assert.throws(() => browserCommand(value));
  }
});
