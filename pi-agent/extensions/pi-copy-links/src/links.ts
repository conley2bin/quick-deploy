import { stat } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launchApplication, openWebUrl, webUrl, type OpenLifetime, type SpawnRunner } from "./browser.ts";

const MAX_POSITION = 2_147_483_647;
const SCHEME = /^([A-Za-z][A-Za-z0-9+.-]*):/u;
const POSITION = "(?:[1-9]\\d{0,9})";
const FRAGMENT_POSITION = new RegExp(`^L(${POSITION})(?:C(${POSITION}))?$`, "u");
const PATH_POSITION_TWO = /^(.*):([+-]?\d+):([+-]?\d+)$/u;
const PATH_POSITION_ONE = /^(.*):([+-]?\d+)$/u;
const hasPathPositionCandidate = (value: string) => PATH_POSITION_TWO.test(value) || PATH_POSITION_ONE.test(value);
const ENCODED_SLASH = /%2f/iu;
const CONTROL = /[\x00-\x1f\x7f-\x9f]/u;

export type OpenTarget =
  | { kind: "web"; url: string }
  | { kind: "local"; path: string; line?: number; column?: number };

export interface OpenTargetDependencies {
  readonly platform?: NodeJS.Platform;
  readonly stat?: (path: string) => Promise<{ isDirectory(): boolean; isFile(): boolean }>;
  readonly spawn?: SpawnRunner;
  readonly browser?: Parameters<typeof openWebUrl>[2];
  readonly env?: NodeJS.ProcessEnv;
}

export function hasUriScheme(value: string): boolean {
  return SCHEME.test(value);
}

export function isLocalHref(value: string): boolean {
  const scheme = SCHEME.exec(value)?.[1]?.toLowerCase();
  return scheme === undefined || scheme === "file";
}

function parsePosition(value: string, label: string): number {
  if (!/^[1-9]\d*$/u.test(value)) throw new Error(`${label} 必须是正十进制整数`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > MAX_POSITION) {
    throw new Error(`${label} 超出范围（最大 ${MAX_POSITION}）`);
  }
  return parsed;
}

function decodeLocalPath(value: string): string {
  if (ENCODED_SLASH.test(value)) throw new Error("本地目标不允许编码的路径分隔符 %2F");
  // Reject malformed escapes before decodeURIComponent so the diagnostic is stable.
  if (/%(?![0-9A-Fa-f]{2})/u.test(value)) throw new Error("本地目标包含无效的百分号转义");
  let decoded: string;
  try { decoded = decodeURIComponent(value); }
  catch { throw new Error("本地目标包含无效的 UTF-8 百分号转义"); }
  if (CONTROL.test(decoded)) throw new Error("本地目标包含控制字符");
  return decoded;
}

function splitFragmentSelector(value: string): { path: string; line?: number; column?: number } {
  const first = value.indexOf("#");
  if (first < 0) return { path: value };
  if (value.indexOf("#", first + 1) >= 0) throw new Error("本地目标包含多个片段选择器");
  const path = value.slice(0, first);
  const fragment = value.slice(first + 1);
  const match = FRAGMENT_POSITION.exec(fragment);
  if (!match) throw new Error("仅支持 #L行 或 #L行C列；章节、空片段和行范围不受支持");
  return {
    path,
    line: parsePosition(match[1]!, "行号"),
    ...(match[2] === undefined ? {} : { column: parsePosition(match[2], "列号") }),
  };
}

function splitPathSelector(value: string): { path: string; line?: number; column?: number } {
  const match = PATH_POSITION_TWO.exec(value) ?? PATH_POSITION_ONE.exec(value);
  if (!match) return { path: value };
  const path = match[1]!;
  const explicitPathForm = path.startsWith("./") || path.startsWith("../") || path.startsWith("/") || path.includes("/");
  if (!explicitPathForm) {
    throw new Error(`位置语法 ${value} 与 URI scheme 有歧义；请使用 ./${value} 或 #L行号`);
  }
  return {
    path,
    line: parsePosition(match[2]!, "行号"),
    ...(match[3] === undefined ? {} : { column: parsePosition(match[3], "列号") }),
  };
}

function fileUrlTarget(value: string): { path: string; line?: number; column?: number } {
  if (!/^file:\/\//iu.test(value)) throw new Error("file: 目标必须使用 file:///绝对/路径 形式");
  if (value.includes("\\")) throw new Error("file URL 不允许反斜杠");
  const hasRawQuery = value.indexOf("?") >= 0;
  if (hasRawQuery) throw new Error("本地目标不支持查询参数；文件名中的 ? 请写成 %3F");
  const fragmentIndex = value.indexOf("#");
  const rawBase = fragmentIndex < 0 ? value : value.slice(0, fragmentIndex);
  const fragment = fragmentIndex < 0 ? undefined : value.slice(fragmentIndex + 1);
  if (ENCODED_SLASH.test(rawBase)) throw new Error("file URL 不允许编码的路径分隔符 %2F");
  let parsed: URL;
  try { parsed = new URL(rawBase); }
  catch { throw new Error("无效的 file URL"); }
  if (parsed.protocol !== "file:") throw new Error("无效的 file URL");
  if (parsed.username || parsed.password || parsed.port) throw new Error("file URL 不允许用户信息或端口");
  if (parsed.hostname && parsed.hostname.toLowerCase() !== "localhost") {
    throw new Error(`不支持远程 file URL 主机：${parsed.hostname}`);
  }
  let path: string;
  try { path = fileURLToPath(parsed); }
  catch (error) { throw new Error(`无效的 file URL：${error instanceof Error ? error.message : String(error)}`); }
  if (CONTROL.test(path)) throw new Error("本地目标包含控制字符");
  if (fragment === undefined) return { path };
  const match = FRAGMENT_POSITION.exec(fragment);
  if (!match) throw new Error("file URL 仅支持 #L行 或 #L行C列");
  return {
    path,
    line: parsePosition(match[1]!, "行号"),
    ...(match[2] === undefined ? {} : { column: parsePosition(match[2], "列号") }),
  };
}

/** Parse without consulting the filesystem; selectors are recognized before one percent decode. */
export function parseOpenTarget(value: string, cwd: string): OpenTarget {
  const web = webUrl(value);
  if (web) return Object.freeze({ kind: "web", url: web });
  if (!isAbsolute(cwd)) throw new Error("Pi 会话工作目录必须是绝对路径");
  if (value.startsWith("//")) throw new Error("//host/path 是歧义 authority 形式，不作为本地路径处理");
  const scheme = SCHEME.exec(value)?.[1]?.toLowerCase();
  if (scheme && scheme !== "file") {
    if (hasPathPositionCandidate(value)) {
      throw new Error(`位置语法 ${value} 与 URI scheme 有歧义；请使用 ./${value} 或 #L行号`);
    }
    throw new Error(`不支持 URI scheme：${scheme}`);
  }

  let local: { path: string; line?: number; column?: number };
  if (scheme === "file") {
    local = fileUrlTarget(value);
  } else {
    if (value.includes("?")) throw new Error("本地目标不支持查询参数；文件名中的 ? 请写成 %3F");
    const fragment = splitFragmentSelector(value);
    if (fragment.line !== undefined && hasPathPositionCandidate(fragment.path)) {
      throw new Error("目标同时包含 :行号 和 #L行号；请只保留一种位置选择器");
    }
    local = fragment.line === undefined ? splitPathSelector(fragment.path) : fragment;
    local.path = decodeLocalPath(local.path);
    if (local.path.length === 0) throw new Error("本地目标路径不能为空");
    local.path = isAbsolute(local.path) ? local.path : resolve(cwd, local.path);
  }
  if (!isAbsolute(local.path)) throw new Error("本地目标必须解析为绝对路径");
  return Object.freeze({ kind: "local", ...local });
}

function codeArguments(target: Extract<OpenTarget, { kind: "local" }>, directory: boolean): string[] {
  if (directory && target.line !== undefined) throw new Error("目录不能带行号或列号");
  if (target.line === undefined) return ["--new-window", "--", target.path];
  if (target.path.includes(":")) throw new Error("VS Code --goto 不支持含冒号的 POSIX 路径；请移除位置选择器");
  const suffix = target.column === undefined ? `:${target.line}` : `:${target.line}:${target.column}`;
  return ["--new-window", "--goto", "--", `${target.path}${suffix}`];
}

function codeEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const env = { ...source };
  delete env.VSCODE_IPC_HOOK_CLI;
  return env;
}

function ensureCurrent(lifetime: OpenLifetime): void {
  if (lifetime.signal.aborted || !lifetime.isCurrent()) throw new Error("打开动作已失效");
}

/** Parse immediately, then validate the frozen request and launch without fallback. */
export async function openTarget(
  href: string,
  cwd: string,
  lifetime: OpenLifetime,
  dependencies: OpenTargetDependencies = {},
): Promise<void> {
  const target = parseOpenTarget(href, cwd);
  if (target.kind === "web") {
    await openWebUrl(target.url, lifetime, dependencies.browser);
    return;
  }
  if ((dependencies.platform ?? process.platform) !== "linux") {
    throw new Error("当前版本仅支持 Linux VS Code 显式新窗口启动");
  }
  ensureCurrent(lifetime);
  let metadata;
  try { metadata = await (dependencies.stat ?? stat)(target.path); }
  catch (error) {
    if (lifetime.signal.aborted || !lifetime.isCurrent()) throw new Error("打开动作已失效");
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") throw new Error(`目标不存在：${target.path}`);
    throw new Error(`无法读取目标：${target.path}（${error instanceof Error ? error.message : String(error)}）`);
  }
  const directory = metadata.isDirectory();
  if (!directory && !metadata.isFile()) throw new Error(`目标不是普通文件或目录：${target.path}`);
  const args = codeArguments(target, directory);
  ensureCurrent(lifetime);
  await launchApplication({
    command: "code",
    args,
    env: codeEnvironment(dependencies.env ?? process.env),
  }, lifetime, dependencies.spawn);
}
