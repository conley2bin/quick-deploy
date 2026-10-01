// Evaluate the installed Code main-process resource resolver without starting its GUI.
// Input: JSON array of argv arrays from the helper's fake `code` recorder.
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.env.CODE_MAIN_JS || '/usr/share/code/resources/app/out/main.js', 'utf8');
function extract(prefix) {
  const start = source.indexOf(prefix);
  if (start < 0) throw new Error(`installed Code resolver method not found: ${prefix}`);
  let depth = 0;
  for (let i = start + prefix.length - 1; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`unbalanced installed Code method: ${prefix}`);
}
const rl = vm.runInNewContext(`(${extract('function rl(i){')})`, { ai: Number.isFinite });
const uri = (input) => {
  const url = new URL(input);
  const resource = {
    scheme: url.protocol.slice(0, -1), authority: url.host, path: decodeURIComponent(url.pathname),
    with(change) { return { ...this, ...change }; },
  };
  return resource;
};
const context = {
  P: { parse: uri }, L: { file: 'file' },
  St: (values) => values.filter(Boolean),
  To: (resource) => resource.authority,
  C1: (resource) => resource, $f: (resource) => resource,
  nS: (openable) => !!openable.fileUri,
  ju: (openable) => !!openable.workspaceUri,
  ug: (openable) => !!openable.folderUri,
  Ni: () => false,
  Po: (resource) => ({ configPath: resource }),
  xg: (resource) => ({ folderUri: resource }),
  rl,
};
const methods = [
  'async doExtractPathsFromCLI(e){', 'cliArgToUri(e){',
  'async resolveOpenable(e,t=Object.create(null)){',
  'doResolveRemoteOpenable(e,t){', 'resourceFromOpenable(e){',
].map(extract);
const resolver = vm.runInNewContext(`({${methods.join(',')}})`, context);
resolver.logService = { error(message) { throw new Error(message); } };
resolver.doResolveFilePath = () => { throw new Error('unexpected laptop file resolution'); };

function cli(argv) {
  const result = { _: [], goto: argv.includes('--goto'), remote: argv[argv.indexOf('--remote') + 1] };
  for (const flag of ['--file-uri', '--folder-uri']) {
    const at = argv.indexOf(flag);
    if (at >= 0) result[flag.slice(2)] = [argv[at + 1]];
  }
  return result;
}
(async () => {
  const argvs = JSON.parse(fs.readFileSync(0, 'utf8'));
  const resolved = [];
  for (const argv of argvs) {
    const resources = await resolver.doExtractPathsFromCLI(cli(argv));
    if (resources.length !== 1) throw new Error(`expected one resource, got ${resources.length}`);
    const resource = resources[0];
    resolved.push(resource.fileUri
      ? { kind: 'file', path: resource.fileUri.path,
          line: resource.options?.selection?.startLineNumber || null,
          column: resource.options?.selection?.startColumn || null,
          remoteAuthority: resource.remoteAuthority }
      : { kind: 'directory', path: resource.workspace?.folderUri?.path,
          remoteAuthority: resource.remoteAuthority });
  }
  process.stdout.write(JSON.stringify(resolved));
})().catch((error) => { console.error(error); process.exitCode = 1; });
