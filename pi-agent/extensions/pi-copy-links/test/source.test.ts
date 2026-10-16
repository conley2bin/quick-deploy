import assert from "node:assert/strict";
import test from "node:test";
import { inlineCodePaths, plainTextPaths } from "../src/paths.ts";
import { parseOpenTarget } from "../src/links.ts";
import {
  ActionStore, alignCodeBlocks, codeBlocks, codeSyntax, sourceAutolinkDestination,
  sourceCodeBlocks, sourceLinkEvidence, sourcePathMatches,
  type RenderedCodeBlock, type SourceCodeBlock,
} from "../src/source.ts";

const copyActions = (...texts: string[]) => texts.map((text) => ({ kind: "copy" as const, text }));
const copyText = (entry: ReturnType<ActionStore["get"]>) =>
  entry?.action.kind === "copy" ? entry.action.text : undefined;

test("source link evidence normalizes Pi spans and resolves the first definition", () => {
  const evidence = sourceLinkEvidence(
    "[tab](<a\tb.svg>) [entity](a&amp;b.svg) [ref][id]\r\n\r\n[id]: first.svg\r\n[id]: second.svg",
  );
  assert.deepEqual([...evidence.destinations].map(([raw, values]) => [raw, [...values]]), [
    ["[tab](<a   b.svg>)", ["a\tb.svg"]],
    ["[entity](a&amp;b.svg)", ["a&b.svg"]],
    ["[ref][id]", ["first.svg"]],
  ]);
  assert.deepEqual([...evidence.explicitAutolinks], []);

  const autolink = sourceLinkEvidence("before https://example.com/?a=1&amp;b=2 after");
  assert.equal(sourceAutolinkDestination(autolink, "https://example.com/?a=1&amp;b=2"),
    "https://example.com/?a=1&b=2");
  assert.equal(sourceAutolinkDestination(autolink, "https://missing.example/"), undefined);
});

test("automatic path syntax accepts screenshot/media/source/artifact and explicit directory evidence", () => {
  for (const path of [
    "state43_real_grid_1080p.mp4", "reports/state43_real_grid_1080p.mp4", "/tmp/output", "./output", "../output",
    "reports/", "src/components/", "./", "../", "README", "Dockerfile", "Makefile", "LICENSE", ".gitignore",
    "src/main.ts", "chart.png", "notes.pdf", "data.jsonl", "dataset.parquet", "weights.pt", "model.3mf",
  ]) {
    const plain = plainTextPaths(path);
    const code = inlineCodePaths(path);
    assert.equal(plain.length, 1, path);
    assert.deepEqual(code, plain, path);
    const expected = path.startsWith("/") ? path : `/click/${path.replace(/^\.\//u, "")}`;
    assert.equal(parseOpenTarget(code[0]!.href, "/click").kind, "local");
    if (!path.includes("..") && !path.endsWith("/")) {
      assert.equal((parseOpenTarget(code[0]!.href, "/click") as { path: string }).path, expected);
    }
  }
  for (const text of ["1.2", "v1.2.3", "example.com", "example.org", "example.dev", "a.b", "and/or", "input/output",
    "src/components", "~/report.png", "~other/report.png", "mailto:x.png", "javascript:report.png", "file:///tmp/x.png",
    "vscode://file/tmp/x.png", "https://example.com/(x.png)", "www.example.com/x.png", "x@y.png", "--out.png"]) {
    assert.deepEqual(plainTextPaths(text), [], text);
    assert.deepEqual(inlineCodePaths(text), [], text);
  }
});

test("plain filename punctuation retains whole resources and only strips prose wrappers", () => {
  for (const [source, path] of [
    ["./reports/final(1).png", "./reports/final(1).png"],
    ["./reports/final,1.png", "./reports/final,1.png"],
    ["(./reports/final(1).png),", "./reports/final(1).png"],
    ["（./reports/final，1.png），", "./reports/final，1.png"],
    ["reports/chart.png。", "reports/chart.png"],
  ]) {
    const spans = plainTextPaths(source!);
    assert.equal(spans.length, 1, source);
    assert.equal(source!.slice(spans[0]!.start, spans[0]!.end), path);
    assert.deepEqual(parseOpenTarget(spans[0]!.href, "/click"),
      { kind: "local", path: `/click/${path!.replace(/^\.\//u, "")}` });
  }
  for (const source of ["./reports/final(1.png", "./reports/final)1.png", "./reports/final[1].png",
    "./reports/final;1.png", "./reports/final|1.png", "./reports/final'1.png"]) {
    assert.deepEqual(plainTextPaths(source), [], `never authorize punctuation fragments: ${source}`);
  }
});

test("native evidence distinguishes complete leaves from filename prefixes and suffixes", () => {
  for (const [first, second] of [["chart.png", "reports/chart.png"], ["src-a.ts", "src-a.tsx"], ["README", "README.md"]]) {
    const evidence = sourceLinkEvidence(`${first}\n\n${second}`);
    assert.equal(sourcePathMatches(evidence, "text", first!, first!).length, 1, first);
    assert.equal(sourcePathMatches(evidence, "text", second!, second!).length, 1, second);
  }
});

test("automatic literal encoding round-trips %, #, ?, Unicode and colon without selectors", () => {
  for (const path of ["100%.png", "literal%20.png", "数据/图#1?.png", "out.png#L12", "out.png?x=1",
    "./file.ts:12:3", "./a:b#L2?.txt", "/tmp/literal%2F.png"]) {
    const paths = inlineCodePaths(path);
    assert.equal(paths.length, 1, path);
    const target = parseOpenTarget(paths[0]!.href, "/click");
    assert.deepEqual(target, { kind: "local", path: path.startsWith("/") ? path : `/click/${path.replace(/^\.\//u, "")}` });
  }
  assert.deepEqual(inlineCodePaths("file.ts:12"), [], "bare scheme-shaped spelling remains ambiguous");
  assert.deepEqual(parseOpenTarget("./file.ts:12:3", "/click"),
    { kind: "local", path: "/click/file.ts", line: 12, column: 3 }, "explicit href grammar is unchanged");
});

test("single code paths may have spaces but commands and expressions are never partially scanned", () => {
  for (const path of ["reports/my video.mp4", "./a b.png", "/tmp/中文 文件.pdf"]) {
    assert.equal(inlineCodePaths(path).length, 1, path);
  }
  for (const command of ["cat report.png", "echo report.png", "printf report.png", "ffmpeg -i in.mp4 out.mp4", "python src/main.py", "npm run build",
    "./script.sh reports/out.png", "/usr/bin/tool output.png", "FOO=x python a.py", "cat x.png | head",
    "custom reports/out.png", "./tool reports/out.png", "x = output.png", "a.png + b.png"]) {
    assert.deepEqual(inlineCodePaths(command), [], command);
    const evidence = sourceLinkEvidence(`\`${command}\``);
    assert.deepEqual(sourcePathMatches(evidence, "codespan", `\`${command}\``, command), [], command);
  }
});

test("source path proof excludes command lines, code, links, images and HTML without changing source", () => {
  const path = "reports/result.png";
  for (const source of [`cat ${path}`, `echo ${path}`, `printf ${path}`, `python ${path}`, `$ custom ${path}`, `custom --out ${path}`,
    `run ffmpeg -i ${path} output.mp4`, `<span>${path}</span>`, `![${path}](image.png)`, `[${path}](other.png)`,
    `\`cat ${path}\``, `\`\`\`\n${path}\n\`\`\``, `    ${path}`]) {
    const evidence = sourceLinkEvidence(source);
    assert.deepEqual(sourcePathMatches(evidence, "text", path, path), [], source);
  }
  for (const [source, leaf] of [[`Open ${path}`, `Open ${path}`], [`输出文件 ${path}`, `输出文件 ${path}`],
    [`**${path}**`, path], [`> - ${path}`, path], [`| path |\n|---|\n| ${path} |`, path]]) {
    assert.equal(sourcePathMatches(sourceLinkEvidence(source!), "text", leaf!, leaf!).length, 1, source);
  }
  assert.deepEqual(sourcePathMatches(sourceLinkEvidence(`a&amp;b.png`), "text", "a&amp;b.png", "a&amp;b.png"), []);
  assert.equal(sourcePathMatches(sourceLinkEvidence(`${path}\n\ncat ${path}`), "text", path, path).length, 1,
    "a command operand substring is not a second native leaf");
  for (const blocked of [`cat **${path}**`, `<span>${path}</span>`]) {
    const ambiguous = sourceLinkEvidence(`${path}\n\n${blocked}`);
    assert.deepEqual(sourcePathMatches(ambiguous, "text", path, path), [], "identical feasible blocked leaf cannot borrow authority");
  }
});

test("source path indexing is bounded linear work with cached queries and no-path fast exit", () => {
  for (const paths of [false, true]) {
    let previous = 0;
    for (const count of [100, 200, 400, 800]) {
      const source = Array.from({ length: count }, (_, i) =>
        `- **Item ${i}** describes ${paths ? `reports/out_${i}.png` : `the result ${i}`} and explanatory prose.`).join("\n");
      const evidence = sourceLinkEvidence(source);
      for (const span of evidence.pathSpans) sourcePathMatches(evidence, span.kind, span.raw, span.text);
      const first = { ...evidence.pathWork };
      assert.equal(first.commandLines, count, "classify each original source line once, not for each styled leaf");
      for (const span of evidence.pathSpans) sourcePathMatches(evidence, span.kind, span.raw, span.text);
      assert.equal(evidence.pathWork.lookups, first.lookups, "decorate/table repeat must reuse exact query results");
      assert.ok(first.lookups <= count * 2, "at most one map lookup per native leaf");
      assert.ok(first.indexedCharacters <= source.length * 4, "bounded indexed text, never all leaf pairs");
      if (paths) {
        assert.equal(first.lookups, count * 2);
        assert.equal(evidence.pathWork.cacheHits, count * 2);
        if (previous) assert.equal(first.lookups, previous * 2, "doubling the response doubles matching work");
        previous = first.lookups;
      } else {
        assert.equal(evidence.pathIndex.size, 0);
        assert.equal(first.lookups, 0, "no global source scan for path-free native text");
        assert.equal(first.queries, 0);
      }
    }
  }
});

test("many styled leaves on one line share one command-context classification", () => {
  const source = Array.from({ length: 800 }, (_, i) => `**item${i}** reports/out_${i}.png`).join(" ");
  const evidence = sourceLinkEvidence(source);
  assert.equal(evidence.pathWork.commandLines, 1);
  for (const span of evidence.pathSpans) sourcePathMatches(evidence, span.kind, span.raw, span.text);
  assert.ok(evidence.pathWork.lookups <= evidence.pathSpans.length);
});

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
  const store = new ActionStore(); const owner = {};
  const first = store.set(owner, copyActions('\techo a', '  b'));
  assert.equal(store.set(owner, copyActions('\techo a', '  b'))[0], first[0]);
  assert.equal(copyText(store.get(first[0]!.url)), '\techo a');
  assert.equal(store.get('pi-copy://foreign/1'), undefined);
  store.clear(); assert.equal(store.get(first[0]!.url), undefined);
});

test("mixed actions use field equality and explicitly retire superseded owner entries", () => {
  const store = new ActionStore();
  const owner = {};
  const first = store.set(owner, [
    { kind: "copy", text: "x" }, { kind: "open", href: "a.svg" },
    { kind: "diagnostic", message: "no" },
  ]);
  const stable = store.set(owner, [
    { kind: "copy", text: "x" }, { kind: "open", href: "a.svg" },
    { kind: "diagnostic", message: "no" },
  ]);
  assert.deepEqual(stable, first);
  const replacement = store.set(owner, [{ kind: "open", href: "b.svg" }]);
  for (const entry of first) assert.equal(store.get(entry!.url), undefined, "superseded URI is inert immediately");
  assert.equal(store.get(replacement[0]!.url)?.action.kind, "open");
});

test("superseded mixed-action cleanup attempts every operation and poisons the generation", () => {
  const store = new ActionStore();
  const owner = {};
  store.set(owner, [{ kind: "copy", text: "x" }, { kind: "open", href: "a.svg" }]);
  const originalDelete = Map.prototype.delete;
  const originalUnregister = FinalizationRegistry.prototype.unregister;
  Map.prototype.delete = function() { throw new RangeError("superseded delete failed"); };
  FinalizationRegistry.prototype.unregister = function() { throw new RangeError("superseded unregister failed"); };
  try {
    assert.throws(() => store.set(owner, [{ kind: "open", href: "b.svg" }]), (error: unknown) => {
      assert.match(String(error), /superseded delete failed/);
      assert.match(String(error), /superseded unregister failed/);
      return true;
    });
    assert.equal(store.isTrusted(), false);
  } finally {
    Map.prototype.delete = originalDelete;
    FinalizationRegistry.prototype.unregister = originalUnregister;
  }
});

test("ActionStore failure retires its namespace instead of reusing an unsafe URL", () => {
  const store = new ActionStore();
  const owner = {};
  const originalSet = Map.prototype.set;
  let actionSets = 0;
  Map.prototype.set = function(this: Map<unknown, unknown>, key: unknown, value: unknown) {
    if (typeof key === 'string' && key.startsWith(store.prefix) && ++actionSets === 2) {
      throw new RangeError('Map maximum size exceeded');
    }
    return originalSet.call(this, key, value);
  } as typeof Map.prototype.set;
  const mixed = () => [{ kind: "copy" as const, text: "a" }, { kind: "open" as const, href: "b.svg" }];
  try { assert.throws(() => store.set(owner, mixed()), /Map maximum size exceeded/); }
  finally { Map.prototype.set = originalSet; }

  assert.equal(store.isTrusted(), false);
  assert.throws(() => store.set(owner, mixed()), /generation is retired/);
  const replacement = new ActionStore();
  const entries = replacement.set(owner, mixed());
  assert.notEqual(replacement.prefix, store.prefix);
  assert.ok(entries[0]?.url.endsWith('/1'));
  assert.ok(entries[1]?.url.endsWith('/2'));
  assert.equal(copyText(replacement.get(entries[0]!.url)), 'a');
  const open = replacement.get(entries[1]!.url)?.action;
  assert.deepEqual(open, { kind: "open", href: "b.svg" });
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
    const retired = new ActionStore();
    assert.throws(() => retired.set({}, copyActions('old')), (error: unknown) => {
      assert.match(String(error), /register committed then failed/);
      assert.match(String(error), /rollback unregister failed/);
      return true;
    });
    assert.equal(retired.isTrusted(), false);

    registries[0]!.failRegister = false;
    registries[0]!.failUnregister = false;
    const replacement = new ActionStore();
    registries[1]!.failRegister = false;
    registries[1]!.failUnregister = false;
    const entry = replacement.set({}, copyActions('new'))[0]!;
    assert.notEqual(replacement.prefix, retired.prefix);
    assert.ok(entry.url.endsWith('/1'));
    assert.equal(copyText(replacement.get(entry.url)), 'new');
    registries[0]!.fireFirst();
    assert.equal(copyText(replacement.get(entry.url)), 'new', 'retired callback only reaches the retired map');
  } finally { globalThis.FinalizationRegistry = RealFinalizationRegistry; }
});

test("ActionStore cleanup attempts are noexcept and poison the generation", () => {
  const originalWeakGet = WeakMap.prototype.get;
  const originalMapDelete = Map.prototype.delete;
  const originalUnregister = FinalizationRegistry.prototype.unregister;

  for (const fault of ['get', 'delete', 'unregister'] as const) {
    const store = new ActionStore();
    const owner = {};
    store.set(owner, copyActions('value'));
    if (fault === 'get') {
      WeakMap.prototype.get = function() { throw new RangeError('rollback get failed'); };
    } else if (fault === 'delete') {
      Map.prototype.delete = function() { throw new RangeError('rollback delete failed'); };
    } else {
      FinalizationRegistry.prototype.unregister = function() { throw new RangeError('rollback unregister failed'); };
    }
    try {
      let issues: ReturnType<ActionStore['delete']> = [];
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

test("ActionStore finalizer delete failure is noexcept and makes actions untrusted", () => {
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
    const store = new ActionStore();
    const entry = store.set({}, copyActions('value'))[0]!;
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
