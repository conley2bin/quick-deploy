// Evaluate installed Code resource/window dispatch methods without starting its GUI.
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
const uri = (input) => {
  const url = new URL(input);
  return {
    scheme: url.protocol.slice(0, -1), authority: url.host, path: decodeURIComponent(url.pathname),
    with(change) { return { ...this, ...change }; },
  };
};
const key = (resource) => `${resource.scheme}://${resource.authority}${resource.path}`;
const context = {
  P: { parse: uri, isUri: (value) => typeof value?.scheme === 'string' }, L: { file: 'file' },
  St: (values) => values.filter(Boolean),
  Fn: (values, getKey = (value) => value) => [...new Map(values.map((value) => [getKey(value), value])).values()],
  Ge: { getComparisonKey: key, isEqual: (left, right) => key(left) === key(right) },
  Jn: (left, right) => left === right,
  To: (resource) => resource.authority,
  C1: (resource) => resource, $f: (resource) => resource,
  nS: (openable) => !!openable.fileUri,
  ju: (openable) => !!openable.workspaceUri,
  ug: (openable) => !!openable.folderUri,
  Ni: () => false,
  Po: (resource) => ({ configPath: resource }),
  xg: (resource) => ({ id: key(resource), uri: resource }),
  H: false, xe: { None: null },
};
for (const [name, prefix] of Object.entries({
  rl: 'function rl(i){', ht: 'function ht(i){', en: 'function en(i){',
  Vc: 'function Vc(i){', Op: 'function Op(i){', _o: 'function _o(i,n){',
})) {
  context[name] = vm.runInNewContext(`(${extract(prefix)})`, { ...context, ai: Number.isFinite });
}
const methods = [
  'async doExtractPathsFromCLI(e){', 'cliArgToUri(e){',
  'async resolveOpenable(e,t=Object.create(null)){',
  'doResolveRemoteOpenable(e,t){', 'resourceFromOpenable(e){',
  'async open(e){', 'async doOpen(e,t,r,o,s,a,l,c){',
  'doOpenFilesInExistingWindow(e,t,r){', 'doOpenFolderOrWorkspace(e,t,r,o,s){',
  'shouldOpenNewWindow(e){', 'resolveContextWindow(e,t){',
].map(extract);
const resolver = vm.runInNewContext(`({${methods.join(',')}})`, context);
resolver.logService = { trace() {}, error(message) { throw new Error(message); } };
resolver.doResolveFilePath = () => { throw new Error('unexpected desktop file resolution'); };

function cli(argv) {
  const result = { _: [], goto: argv.includes('--goto'), remote: argv[argv.indexOf('--remote') + 1],
    'new-window': argv.includes('--new-window') };
  for (const flag of ['--file-uri', '--folder-uri']) {
    const at = argv.indexOf(flag);
    if (at >= 0) result[flag.slice(2)] = [argv[at + 1]];
  }
  return result;
}
function file(resource) {
  return { kind: 'file', path: resource.fileUri.path,
    line: resource.options?.selection?.startLineNumber || null,
    column: resource.options?.selection?.startColumn || null,
    remoteAuthority: resource.remoteAuthority };
}
function describe(resource) {
  return resource.fileUri ? file(resource)
    : { kind: 'directory', path: resource.workspace.uri.path, remoteAuthority: resource.remoteAuthority };
}
async function dispatch(resources, args, alreadyOpen) {
  const folder = resources.find((resource) => resource.workspace?.uri);
  if (!folder) throw new Error('expected a typed workspace folder');
  const opened = [], messages = [];
  const existing = { id: 41, openedWorkspace: folder.workspace, remoteAuthority: folder.remoteAuthority,
    sendWhenReady(channel, _token, data) { messages.push({ channel, files: (data.filesToOpenOrCreate || []).map(file) }); } };
  const manager = Object.create(resolver);
  Object.assign(manager, {
    configurationService: { getValue() { return {}; } },
    getPathsToOpen: async () => resources,
    getWindows: () => alreadyOpen ? [existing] : [],
    focusMainOrChildWindow() {},
    workspacesHistoryMainService: { addRecentlyOpened() {} },
    handleWaitMarkerFile() {}, handleChatRequest() {},
    openInBrowserWindow: async (options) => {
      opened.push({ folder: options.workspace.uri.path, remoteAuthority: options.remoteAuthority,
        forceNewWindow: options.forceNewWindow, files: (options.filesToOpen?.filesToOpenOrCreate || []).map(file) });
      return { id: 42, openedWorkspace: options.workspace, remoteAuthority: options.remoteAuthority };
    },
    doOpenEmpty() { throw new Error('unexpected separate empty/file-only window'); },
  });
  // Installed CLI forwards new-window into this forceNewWindow option; native
  // open/doOpen/shouldOpenNewWindow below decide how it affects folder reuse.
  const windows = await manager.open({ context: 0, cli: args, forceNewWindow: args['new-window'],
    gotoLineMode: args.goto, remoteAuthority: args.remote });
  return { windowIds: windows.map((window) => window.id), created: opened, messages };
}
(async () => {
  const argvs = JSON.parse(fs.readFileSync(0, 'utf8'));
  const resolved = [];
  for (const argv of argvs) {
    const args = cli(argv);
    const resources = await resolver.doExtractPathsFromCLI(args);
    resolved.push({ resources: resources.map(describe),
      fresh: await dispatch(resources, args, false),
      existingParent: await dispatch(resources, args, true) });
  }
  process.stdout.write(JSON.stringify(resolved));
})().catch((error) => { console.error(error); process.exitCode = 1; });
