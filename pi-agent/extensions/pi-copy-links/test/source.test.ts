import assert from "node:assert/strict";
import test from "node:test";
import { browserCommand, webUrl } from "../src/browser.ts";
import { codeBlocks, CopyStore } from "../src/source.ts";

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

test("copy references remain stable for mounted unchanged content; clear invalidates session", () => {
  const store = new CopyStore(); const owner = {};
  const first = store.set(owner, ['\techo a', '  b']);
  assert.equal(store.set(owner, ['\techo a', '  b'])[0], first[0]);
  assert.equal(store.get(first[0]!.url)?.text, '\techo a');
  assert.equal(store.get('pi-copy://foreign/1'), undefined);
  store.clear(); assert.equal(store.get(first[0]!.url), undefined);
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
