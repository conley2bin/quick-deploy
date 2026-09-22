import { isAbsolute } from "node:path";
import type { MarkdownTransformContext } from "@earendil-works/pi-coding-agent";
import {
  getOsc8LinkAtColumn, hyperlink, Markdown, TuiAltScreen, visibleWidth,
  type MarkdownTheme, type TUI,
} from "@earendil-works/pi-tui";
import { webUrl, type OpenLifetime } from "./browser.ts";
import { hasUriScheme, isLocalHref } from "./links.ts";
import {
  ActionStore, alignCodeBlocks, codeSyntax, sourceAutolinkDestination, sourceCodeBlocks, sourceLinkEvidence,
  type Action, type ActionEntry, type ActionSlot, type CleanupIssue, type CodeContainer,
  type RenderedCodeBlock, type SourceCodeBlock, type SourceLinkEvidence,
} from "./source.ts";

// Pi has no public code-block renderer or pre-viewport mouse hook in these releases.
// Keep the exact private surface here, reject unknown versions and restore on unload.
export const SUPPORTED_PI = ["0.85.1", "0.86.1"] as const;
interface Token { type: string; text?: string; lang?: string; href?: string; tokens?: Token[]; raw?: string }
interface InlineStyle { applyText: (text: string) => string; stylePrefix: string }
interface MarkdownRuntime {
  theme: MarkdownTheme;
  invalidate(): void;
  render(width: number): string[];
  renderToken(token: Token, width: number, next?: string, style?: InlineStyle): string[];
  renderInlineTokens(tokens: Token[], style?: InlineStyle): string;
}
interface OverlayRuntimeEntry { component: object }
interface OverlayRuntimeLayout {
  entry: OverlayRuntimeEntry;
  row: number;
  col: number;
  width: number;
  height: number;
}
interface MouseDispatchTargetRuntime { component: object }
type NativeInput = (this: FullscreenRuntime, data: string) => { consume?: boolean; data?: string } | undefined;
type NativeOpener = (url: string) => void;
interface FullscreenRuntime extends TUI {
  previousScreen: string[];
  previousScreenWidth: number;
  previousScreenHeight: number;
  openUrl?: NativeOpener;
  renderedOverlayLayouts: OverlayRuntimeLayout[];
  overlayStack: OverlayRuntimeEntry[];
  mouseCapture?: MouseDispatchTargetRuntime;
  mousePressTarget?: MouseDispatchTargetRuntime;
  containsComponent(root: object, target: object): boolean;
  handleViewportInput: NativeInput;
}
type FramePhase = "collect" | "decorate" | "native";
interface LinkCollection {
  readonly raw: string;
  readonly href: string;
  count: number;
  decorated: number;
  action?: Readonly<Action>;
  entry?: ActionEntry;
}
interface Frame {
  owner: MarkdownRuntime;
  tracking: boolean;
  phase: FramePhase;
  assistant: boolean;
  streaming: boolean;
  failed: boolean;
  decorationInvalid: boolean;
  containers: CodeContainer[];
  sources: SourceCodeBlock[];
  sourceLinks: SourceLinkEvidence;
  native: RenderedCodeBlock[];
  codeEntries: ActionSlot[];
  links: Map<string, LinkCollection>;
  linkKeys: string[];
  codeCursor: number;
}
interface OverlaySnapshot {
  entry: object;
  row: number;
  col: number;
  width: number;
  height: number;
}
interface Press {
  url: string;
  actionKind: Action["kind"] | "expired";
  modifiers: number;
  x: number;
  y: number;
  line: string;
  width: number;
  height: number;
  overlays: OverlaySnapshot[];
  moved: boolean;
}
interface Release {
  bits: number;
  at: number;
  screen: string[];
  width: number;
  height: number;
  x: number;
  y: number;
  url?: string;
  privateAction: boolean;
  actionKind?: Action["kind"] | "expired";
  overlayOwned: boolean;
  overlays: OverlaySnapshot[];
}
interface HookCapture {
  name: string;
  owner: object;
  key: string;
  descriptor: PropertyDescriptor;
  method: (...args: never[]) => unknown;
}
interface HookSurface extends HookCapture { wrapper: (...args: never[]) => unknown }
const INPUT_GUARD = Symbol.for("pi-copy-links:input-guard");
const OPEN_URL_FILTER = Symbol.for("pi-copy-links:open-url-filter");
const CLIPBOARD_QUEUE = Symbol.for("pi-copy-links:clipboard-queue");
interface InputGuardMetadata {
  baseMethod: NativeInput;
  baseDescriptor: PropertyDescriptor;
  passive: boolean;
  minimal?: true;
  /** A prior composed frame may still contain a private URI even if this adapter never rendered. */
  requiresQuiescence: boolean;
}
interface OpenUrlFilterMetadata { base?: NativeOpener }
interface OpenUrlTransaction {
  priorOwn?: PropertyDescriptor;
  installedOwn: PropertyDescriptor;
  filter: FilteredOpener;
}
interface ClipboardQueueState { tail: Promise<void> }
type GuardedInput = NativeInput & { [INPUT_GUARD]?: InputGuardMetadata };
type FilteredOpener = NativeOpener & { [OPEN_URL_FILTER]?: OpenUrlFilterMetadata };
interface BookkeepingGeneration {
  store: ActionStore;
  controller: AbortController;
  tracked: Set<WeakRef<MarkdownRuntime>>;
  registered: WeakMap<MarkdownRuntime, WeakRef<MarkdownRuntime>>;
  cleanup: FinalizationRegistry<WeakRef<MarkdownRuntime>>;
  poison?: unknown;
}
export interface AdapterOptions {
  version: string;
  active: () => boolean;
  copy: (text: string) => Promise<void>;
  cwd: () => string;
  open: (href: string, cwd: string, lifetime: OpenLifetime) => Promise<void>;
  notify: (message: string, error?: boolean) => void;
  button: (text: string) => string;
  recoverTmuxRelease?: () => boolean;
  requestRender?: () => void;
  /** Synchronously render and verify that no composed fullscreen row retains a private URI. */
  quiesce?: () => boolean;
  /** Milliseconds the copied flash stays visible after a successful write. */
  flashMs?: number;
}

function baseOpener(opener: NativeOpener | undefined): NativeOpener | undefined {
  let current = opener as FilteredOpener | undefined;
  const seen = new Set<NativeOpener>();
  while (current) {
    if (seen.has(current)) return undefined;
    seen.add(current);
    const owned = current[OPEN_URL_FILTER];
    if (!owned) return current;
    current = owned.base as FilteredOpener | undefined;
  }
  return undefined;
}

function descriptorStillOwnsFilter(
  current: PropertyDescriptor | undefined,
  installed: PropertyDescriptor,
  filter: FilteredOpener,
): boolean {
  if (current === undefined) return false;
  if ("value" in installed) {
    return "value" in current && current.value === filter && current.writable === installed.writable &&
      current.enumerable === installed.enumerable && current.configurable === installed.configurable;
  }
  return !("value" in current) && current.get === installed.get && current.set === installed.set &&
    current.enumerable === installed.enumerable && current.configurable === installed.configurable;
}

function ownsOpenUrlFilter(tui: FullscreenRuntime, transaction: OpenUrlTransaction): boolean {
  try {
    const current = Object.getOwnPropertyDescriptor(tui, "openUrl");
    return descriptorStillOwnsFilter(current, transaction.installedOwn, transaction.filter) &&
      Reflect.get(tui, "openUrl", tui) === transaction.filter;
  } catch { return false; }
}

function restoreOpenUrl(tui: FullscreenRuntime, transaction: OpenUrlTransaction): void {
  if (!ownsOpenUrlFilter(tui, transaction)) return;
  try {
    if (transaction.priorOwn === undefined) Reflect.deleteProperty(tui, "openUrl");
    else Object.defineProperty(tui, "openUrl", transaction.priorOwn);
  } catch { /* A failed restoration must not replace native input's result/error. */ }
}

function temporaryOpenUrlDescriptor(
  priorOwn: PropertyDescriptor | undefined,
  filter: FilteredOpener,
): PropertyDescriptor | undefined {
  if (priorOwn === undefined) {
    return { value: filter, writable: true, enumerable: true, configurable: true };
  }
  if (!("value" in priorOwn)) {
    if (priorOwn.configurable !== true) return undefined;
    return { value: filter, writable: true, enumerable: priorOwn.enumerable, configurable: true };
  }
  if (priorOwn.configurable !== true && priorOwn.writable !== true) return undefined;
  return {
    value: filter,
    writable: true,
    enumerable: priorOwn.enumerable,
    configurable: priorOwn.configurable,
  };
}

function acquireOpenUrlFilter(
  tui: FullscreenRuntime,
  allowNonPrivate: boolean,
): OpenUrlTransaction | undefined {
  let priorOwn: PropertyDescriptor | undefined;
  let predecessor: unknown;
  try {
    priorOwn = Object.getOwnPropertyDescriptor(tui, "openUrl");
    predecessor = Reflect.get(tui, "openUrl", tui);
  } catch { return undefined; }

  const stableBase = baseOpener(typeof predecessor === "function" ? predecessor as NativeOpener : undefined);
  const filteredOpen: FilteredOpener = (target: string) => {
    if (target.startsWith("pi-copy://")) return;
    if (allowNonPrivate) stableBase?.call(tui, target);
  };
  Object.defineProperty(filteredOpen, OPEN_URL_FILTER, {
    value: Object.freeze<OpenUrlFilterMetadata>({ base: stableBase }),
  });

  const temporary = temporaryOpenUrlDescriptor(priorOwn, filteredOpen);
  if (!temporary) return undefined;
  try {
    // Never call an arbitrary accessor setter: shadow/replace it by descriptor,
    // or change only the value of an own writable data property.
    Object.defineProperty(tui, "openUrl", temporary);
    const installedOwn = Object.getOwnPropertyDescriptor(tui, "openUrl");
    if (!installedOwn || Reflect.get(tui, "openUrl", tui) !== filteredOpen) return undefined;
    return { priorOwn, installedOwn, filter: filteredOpen };
  } catch { return undefined; }
}

/** Run Pi's native state machine only after transactionally owning its URL side effect. */
function callNativeFiltered(
  tui: FullscreenRuntime,
  data: string,
  nativeInput: NativeInput,
  allowNonPrivate: boolean,
) {
  const transaction = acquireOpenUrlFilter(tui, allowNonPrivate);
  if (!transaction) return { consume: true };
  try { return nativeInput.call(tui, data); }
  finally { restoreOpenUrl(tui, transaction); }
}

function mousePoint(data: string): { x: number; y: number } | undefined {
  const sgr = /^\x1b\[<(\d+);(\d+);(\d+)[Mm]$/u.exec(data);
  if (sgr) return { x: Number(sgr[2]) - 1, y: Number(sgr[3]) - 1 };
  if (data.length === 6 && data.startsWith("\x1b[M")) {
    return { x: data.charCodeAt(4) - 33, y: data.charCodeAt(5) - 33 };
  }
  return undefined;
}

/** Match Pi's overlay-first dispatch ownership for this exact mouse event. */
function overlayOwnsInput(tui: FullscreenRuntime, data: string): boolean {
  const point = mousePoint(data);
  if (!point) return false;
  try {
    if (Array.isArray(tui.renderedOverlayLayouts) && tui.renderedOverlayLayouts.some((layout) =>
      point.x >= layout.col && point.x < layout.col + layout.width &&
      point.y >= layout.row && point.y < layout.row + layout.height)) return true;

    const capture = tui.mouseCapture ?? tui.mousePressTarget;
    return capture !== undefined && Array.isArray(tui.overlayStack) &&
      typeof tui.containsComponent === "function" && tui.overlayStack.some((entry) =>
        tui.containsComponent(entry.component, capture.component));
  } catch {
    // Missing/drifting private geometry can only suppress native ordinary URLs.
    return false;
  }
}

function overlaySnapshot(tui: FullscreenRuntime): OverlaySnapshot[] {
  try {
    return Array.isArray(tui.renderedOverlayLayouts) ? tui.renderedOverlayLayouts.map((layout) => ({
      entry: layout.entry,
      row: layout.row,
      col: layout.col,
      width: layout.width,
      height: layout.height,
    })) : [];
  } catch { return []; }
}

function sameOverlaySnapshot(tui: FullscreenRuntime, expected: readonly OverlaySnapshot[]): boolean {
  const current = overlaySnapshot(tui);
  return current.length === expected.length && current.every((layout, index) => {
    const prior = expected[index];
    return prior !== undefined && layout.entry === prior.entry && layout.row === prior.row &&
      layout.col === prior.col && layout.width === prior.width && layout.height === prior.height;
  });
}

function clipboardQueue(): ClipboardQueueState {
  const globals = globalThis as typeof globalThis & Record<symbol, unknown>;
  const existing = globals[CLIPBOARD_QUEUE] as ClipboardQueueState | undefined;
  if (existing) {
    if (!existing.tail || typeof existing.tail.then !== "function") {
      throw new Error("pi-copy-links process clipboard queue is invalid");
    }
    return existing;
  }
  const created: ClipboardQueueState = { tail: Promise.resolve() };
  Object.defineProperty(globals, CLIPBOARD_QUEUE, { value: created });
  return created;
}

function enqueueClipboard(operation: () => Promise<void>): Promise<void> {
  const state = clipboardQueue();
  const result = state.tail.then(operation);
  state.tail = result.then(() => undefined, () => undefined);
  return result;
}

function createPassiveInput(
  baseMethod: NativeInput,
  baseDescriptor: PropertyDescriptor,
  requiresQuiescence: boolean,
): GuardedInput {
  function passiveInput(this: FullscreenRuntime, data: string) {
    const metadata = (passiveInput as GuardedInput)[INPUT_GUARD]!;
    return callNativeFiltered(this, data, metadata.baseMethod, true);
  }
  const metadata = Object.freeze<InputGuardMetadata>({
    baseMethod,
    baseDescriptor: Object.freeze({ ...baseDescriptor }),
    passive: true,
    minimal: true,
    requiresQuiescence,
  });
  Object.defineProperty(passiveInput, INPUT_GUARD, { value: metadata });
  return passiveInput;
}

const methodAt = (owner: object, key: string) => Reflect.get(owner, key, owner) as unknown;

function captureHook(name: string, owner: object, key: string): HookCapture {
  const descriptor = Object.getOwnPropertyDescriptor(owner, key);
  let method: unknown;
  try { method = methodAt(owner, key); }
  catch (error) { throw new Error(`${name} 读取失败：${String(error)}`); }
  if (!descriptor || typeof method !== "function") throw new Error(`缺少运行时方法 ${name}`);
  if ("value" in descriptor) {
    if (descriptor.writable !== true) throw new Error(`运行时方法不可写 ${name}`);
  } else if (typeof descriptor.set !== "function" || descriptor.configurable !== true) {
    throw new Error(`运行时访问器不可安全替换 ${name}`);
  }
  return { name, owner, key, descriptor, method: method as HookCapture["method"] };
}

function restoreHook(surface: HookCapture): void {
  if (!("value" in surface.descriptor) && typeof surface.descriptor.set === "function") {
    try { Reflect.set(surface.owner, surface.key, surface.method, surface.owner); }
    catch { /* defineProperty below restores the observable descriptor. */ }
  }
  Object.defineProperty(surface.owner, surface.key, surface.descriptor);
  if (methodAt(surface.owner, surface.key) !== surface.method) {
    throw new Error(`${surface.name} 回滚后方法身份不一致`);
  }
}

function installHooks(surfaces: readonly HookSurface[]): void {
  try {
    for (const surface of surfaces) {
      if (!Reflect.set(surface.owner, surface.key, surface.wrapper, surface.owner) ||
          methodAt(surface.owner, surface.key) !== surface.wrapper) {
        throw new Error(`${surface.name} 安装后方法身份不一致`);
      }
    }
    for (const surface of surfaces) {
      if (methodAt(surface.owner, surface.key) !== surface.wrapper) {
        throw new Error(`${surface.name} 安装验证失败`);
      }
    }
  } catch (error) {
    const rollbackErrors: string[] = [];
    for (const surface of [...surfaces].reverse()) {
      try { restoreHook(surface); }
      catch (rollbackError) { rollbackErrors.push(`${surface.name}: ${String(rollbackError)}`); }
    }
    const suffix = rollbackErrors.length ? `；回滚失败：${rollbackErrors.join("；")}` : "";
    throw new Error(`pi-copy-links 原子安装失败：${String(error)}${suffix}`);
  }
}

export function installAdapter(options: AdapterOptions) {
  const md = Markdown.prototype as unknown as MarkdownRuntime;
  const alt = TuiAltScreen.prototype as unknown as FullscreenRuntime;
  const supported = SUPPORTED_PI.join("、");
  if (!SUPPORTED_PI.some((version) => version === options.version)) {
    throw new Error(`pi-copy-links 不支持 Pi ${options.version}；已验证版本：${supported}；未修改渲染器`);
  }

  let captures: HookCapture[];
  try {
    captures = [
      captureHook("Markdown.render", md, "render"),
      captureHook("Markdown.renderToken", md, "renderToken"),
      captureHook("Markdown.renderInlineTokens", md, "renderInlineTokens"),
      captureHook("TuiAltScreen.handleViewportInput", alt, "handleViewportInput"),
    ];
  } catch (error) {
    throw new Error(
      `pi-copy-links 无法适配 Pi ${options.version}：${error instanceof Error ? error.message : String(error)}；` +
      `已验证版本：${supported}；未修改渲染器`,
    );
  }
  const predecessorInput = captures[3]!.method as unknown as GuardedInput;
  const predecessorGuard = predecessorInput[INPUT_GUARD];
  const absorbedGuard = predecessorGuard?.passive === true ? predecessorGuard : undefined;
  const original = {
    render: captures[0]!.method as unknown as MarkdownRuntime["render"],
    token: captures[1]!.method as unknown as MarkdownRuntime["renderToken"],
    inline: captures[2]!.method as unknown as MarkdownRuntime["renderInlineTokens"],
    input: absorbedGuard?.baseMethod ?? predecessorInput,
  };
  const inputBaseCapture: HookCapture = absorbedGuard
    ? {
        name: captures[3]!.name,
        owner: TuiAltScreen.prototype as unknown as Record<string, unknown>,
        key: "handleViewportInput",
        descriptor: absorbedGuard.baseDescriptor,
        method: absorbedGuard.baseMethod as unknown as (...args: never[]) => unknown,
      }
    : captures[3]!;
  const frames: Frame[] = [];
  const presses = new WeakMap<object, Press>();
  const releases = new WeakMap<object, Release>();
  let generation: BookkeepingGeneration | undefined;
  const MAX_TRACKED_INSTANCES = 100_000;
  const RECOVERY_ATTEMPTS = 8;
  const RECOVERY_DELAY_MS = 1000;
  let enabled = true;
  let warned = false;
  let trackingDisabled = false;
  let recoveryAttempt = 0;
  let recoveryNotified = false;
  let recoveryReason = "unknown allocation failure";
  let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
  let pressedUrl: string | undefined;
  let flashUrl: string | undefined;
  let flashTimer: ReturnType<typeof setTimeout> | undefined;
  let privateUriIssued = absorbedGuard?.requiresQuiescence === true;
  let lastTui: FullscreenRuntime | undefined;
  const emphasize = (text: string) => `\x1b[7m${text}\x1b[27m`;
  const unpress = (line: string) => line.replaceAll("\x1b[7m", "").replaceAll("\x1b[27m", "");
  const reasonOf = (error: unknown) => {
    try { return error instanceof Error ? `${error.name}: ${error.message}` : String(error); }
    catch { return "unprintable bookkeeping failure"; }
  };
  const failureWithCleanup = (primary: unknown, issues: readonly CleanupIssue[]) => {
    if (issues.length === 0) return primary instanceof Error ? primary : new Error(reasonOf(primary));
    const detail = issues.map((issue) => `${issue.operation}: ${reasonOf(issue.error)}`).join("; ");
    return new AggregateError(
      [primary, ...issues.map((issue) => issue.error)],
      `${reasonOf(primary)}; cleanup failures: ${detail}`,
      { cause: primary },
    );
  };
  const attempt = (issues: CleanupIssue[], operation: string, action: () => void) => {
    try { action(); }
    catch (error) { issues.push({ operation, error }); }
  };
  const requestRender = () => {
    try {
      if (options.requestRender) options.requestRender();
      else lastTui?.requestRender();
    } catch { /* Rendering will resume on the next native UI event. */ }
  };

  function warnMismatch(streaming: boolean): void {
    if (warned || streaming) return;
    warned = true;
    const notify = () => {
      if (!enabled) return;
      try { options.notify("代码块源文与 Pi 显示解析无法唯一对应，已省略不安全的复制按钮；请保留原文并报告此例", true); }
      catch { /* Notifications are never part of render correctness. */ }
    };
    try { queueMicrotask(notify); }
    catch {
      try { notify(); } catch { /* Notifications are never part of render correctness. */ }
    }
  }

  function createGeneration(): BookkeepingGeneration {
    let store: ActionStore | undefined;
    try {
      store = new ActionStore();
      const candidate: BookkeepingGeneration = {
        store,
        controller: new AbortController(),
        tracked: new Set<WeakRef<MarkdownRuntime>>(),
        registered: new WeakMap<MarkdownRuntime, WeakRef<MarkdownRuntime>>(),
        cleanup: undefined as unknown as FinalizationRegistry<WeakRef<MarkdownRuntime>>,
      };
      candidate.cleanup = new FinalizationRegistry<WeakRef<MarkdownRuntime>>((ref) => {
        try { candidate.tracked.delete(ref); }
        catch (error) { candidate.poison = error; }
      });
      return candidate;
    } catch (error) {
      try { store?.retire(); } catch { /* ActionStore.retire is specified noexcept. */ }
      throw error;
    }
  }

  function invalidateGeneration(current: BookkeepingGeneration): CleanupIssue[] {
    const issues: CleanupIssue[] = [];
    try {
      for (const ref of current.tracked) {
        let owner: MarkdownRuntime | undefined;
        attempt(issues, "tracked.deref", () => { owner = ref.deref(); });
        if (owner) attempt(issues, "owner.invalidate", () => { owner!.invalidate(); });
        else {
          attempt(issues, "tracked.delete", () => {
            if (!current.tracked.delete(ref)) throw new Error("Set.delete returned false");
          });
          attempt(issues, "tracked.finalizer.unregister", () => {
            if (!current.cleanup.unregister(ref)) throw new Error("FinalizationRegistry.unregister returned false");
          });
        }
      }
    } catch (error) { issues.push({ operation: "tracked.iterate", error }); }
    return issues;
  }

  function retireGeneration(current: BookkeepingGeneration): CleanupIssue[] {
    const issues: CleanupIssue[] = [];
    attempt(issues, "generation.abort", () => { current.controller.abort(); });
    attempt(issues, "store.retire", () => { issues.push(...current.store.retire()); });
    try {
      for (const ref of current.tracked) {
        attempt(issues, "tracked.finalizer.unregister", () => {
          if (!current.cleanup.unregister(ref)) throw new Error("FinalizationRegistry.unregister returned false");
        });
      }
    } catch (error) { issues.push({ operation: "tracked.iterate", error }); }
    attempt(issues, "tracked.clear", () => { current.tracked.clear(); });
    return issues;
  }

  function deleteOwnerActions(current: BookkeepingGeneration, owner: object): CleanupIssue[] {
    const issues: CleanupIssue[] = [];
    attempt(issues, "store.delete", () => { issues.push(...current.store.delete(owner)); });
    return issues;
  }

  function pruneGeneration(current: BookkeepingGeneration): void {
    const issues: CleanupIssue[] = [];
    if (current.poison !== undefined) throw current.poison;
    if (!current.store.isTrusted()) throw new Error("ActionStore generation is untrusted");
    try {
      for (const ref of current.tracked) {
        let owner: MarkdownRuntime | undefined;
        attempt(issues, "tracked.deref", () => { owner = ref.deref(); });
        if (owner) continue;
        attempt(issues, "tracked.delete", () => {
          if (!current.tracked.delete(ref)) throw new Error("Set.delete returned false");
        });
        attempt(issues, "tracked.finalizer.unregister", () => {
          if (!current.cleanup.unregister(ref)) throw new Error("FinalizationRegistry.unregister returned false");
        });
      }
    } catch (error) { issues.push({ operation: "tracked.iterate", error }); }
    if (issues.length) throw failureWithCleanup(new Error("tracked prune failed"), issues);
  }

  function probeGeneration(candidate: BookkeepingGeneration): void {
    const target = {} as MarkdownRuntime;
    let ref: WeakRef<MarkdownRuntime> | undefined;
    let primary: unknown;
    const cleanupIssues: CleanupIssue[] = [];
    try {
      pruneGeneration(candidate);
      if (candidate.tracked.size >= MAX_TRACKED_INSTANCES) {
        throw new RangeError("too many live Markdown instances");
      }
      ref = new WeakRef(target);
      candidate.registered.set(target, ref);
      if (candidate.registered.get(target) !== ref) throw new Error("registered WeakMap.set did not commit");
      candidate.tracked.add(ref);
      if (!candidate.tracked.has(ref)) throw new Error("tracked Set.add did not commit");
      candidate.cleanup.register(target, ref, ref);
      candidate.store.probe();
      if (!candidate.store.isTrusted()) throw new Error("ActionStore probe poisoned its generation");
    } catch (error) { primary = error; }
    if (ref) {
      attempt(cleanupIssues, "probe.finalizer.unregister", () => {
        if (!candidate.cleanup.unregister(ref!)) throw new Error("FinalizationRegistry.unregister returned false");
      });
      attempt(cleanupIssues, "probe.tracked.delete", () => {
        if (!candidate.tracked.delete(ref!)) throw new Error("Set.delete returned false");
      });
      attempt(cleanupIssues, "probe.registered.delete", () => {
        if (!candidate.registered.delete(target)) throw new Error("WeakMap.delete returned false");
      });
    }
    if (primary !== undefined || cleanupIssues.length) {
      const failure = failureWithCleanup(primary ?? new Error("generation probe cleanup failed"), cleanupIssues);
      candidate.poison = failure;
      const retirementIssues = retireGeneration(candidate);
      throw failureWithCleanup(failure, retirementIssues);
    }
  }

  function notifyTerminalRecoveryFailure(): void {
    if (recoveryNotified || !enabled) return;
    recoveryNotified = true;
    try {
      options.notify(
        `pi-copy-links 已停用：连续 ${RECOVERY_ATTEMPTS} 次无法恢复动作登记（${recoveryReason}）；请重启 Pi`,
        true,
      );
    } catch { /* A UI notification failure must not escape a recovery turn. */ }
  }

  function scheduleRecovery(): void {
    if (!enabled || recoveryTimer || recoveryNotified) return;
    try {
      recoveryTimer = setTimeout(() => {
        recoveryTimer = undefined;
        if (!enabled) return;
        recoveryAttempt += 1;
        let candidate: BookkeepingGeneration | undefined;
        try {
          candidate = createGeneration();
          probeGeneration(candidate);
        } catch (error) {
          if (candidate) {
            const issues = retireGeneration(candidate);
            error = failureWithCleanup(error, issues);
          }
          recoveryReason = reasonOf(error);
          if (recoveryAttempt >= RECOVERY_ATTEMPTS) notifyTerminalRecoveryFailure();
          else scheduleRecovery();
          return;
        }
        generation = candidate;
        trackingDisabled = false;
        recoveryAttempt = 0;
        recoveryNotified = false;
        requestRender();
      }, RECOVERY_DELAY_MS);
    } catch (error) {
      recoveryReason = reasonOf(error);
      recoveryAttempt = RECOVERY_ATTEMPTS;
      notifyTerminalRecoveryFailure();
    }
  }

  /** Detach trust first; every subsequent cleanup and scheduling operation is noexcept. */
  function suspend(primary: unknown): void {
    try {
      recoveryReason = reasonOf(primary);
      if (!enabled) return;
      if (!trackingDisabled) {
        trackingDisabled = true;
        recoveryAttempt = 0;
        recoveryNotified = false;
        pressedUrl = undefined;
        flashUrl = undefined;
        const retired = generation;
        generation = undefined;
        const issues: CleanupIssue[] = [];
        if (retired) {
          attempt(issues, "generation.invalidate", () => { issues.push(...invalidateGeneration(retired)); });
          attempt(issues, "generation.retire", () => { issues.push(...retireGeneration(retired)); });
        }
        if (issues.length) recoveryReason = reasonOf(failureWithCleanup(primary, issues));
        requestRender();
      }
      scheduleRecovery();
    } catch (secondary) {
      // Nothing in suspension may escape the renderer. Generation was detached
      // before cleanup, so even this path leaves every old prefix non-actionable.
      generation = undefined;
      trackingDisabled = true;
      recoveryReason = `${reasonOf(primary)}; suspension failure: ${reasonOf(secondary)}`;
      try { scheduleRecovery(); } catch { /* scheduleRecovery is specified noexcept. */ }
    }
  }

  function register(owner: MarkdownRuntime): boolean {
    if (trackingDisabled || !generation) return false;
    const current = generation;
    let ref: WeakRef<MarkdownRuntime> | undefined;
    let primary: unknown;
    const cleanupIssues: CleanupIssue[] = [];
    try {
      pruneGeneration(current);
      if (current.registered.has(owner)) return true;
      if (current.tracked.size >= MAX_TRACKED_INSTANCES) throw new RangeError("too many live Markdown instances");
      ref = new WeakRef(owner);
      current.registered.set(owner, ref);
      if (current.registered.get(owner) !== ref) throw new Error("registered WeakMap.set did not commit");
      current.tracked.add(ref);
      if (!current.tracked.has(ref)) throw new Error("tracked Set.add did not commit");
      current.cleanup.register(owner, ref, ref);
      return true;
    } catch (error) { primary = error; }
    if (ref) {
      attempt(cleanupIssues, "register.finalizer.unregister", () => {
        if (!current.cleanup.unregister(ref!)) throw new Error("FinalizationRegistry.unregister returned false");
      });
      attempt(cleanupIssues, "register.tracked.delete", () => {
        if (!current.tracked.delete(ref!)) throw new Error("Set.delete returned false");
      });
      attempt(cleanupIssues, "register.registered.delete", () => {
        if (!current.registered.delete(owner)) throw new Error("WeakMap.delete returned false");
      });
    }
    const failure = failureWithCleanup(primary, cleanupIssues);
    current.poison = failure;
    suspend(failure);
    return false;
  }

  function invalidateActiveGeneration(): void {
    const current = generation;
    if (!current) return;
    const issues = invalidateGeneration(current);
    if (current.poison !== undefined || !current.store.isTrusted() || issues.length) {
      suspend(failureWithCleanup(current.poison ?? new Error("bookkeeping generation invalidation failed"), issues));
    }
  }

  function setPressed(url: string | undefined): void {
    if (pressedUrl === url) return;
    pressedUrl = url;
    invalidateActiveGeneration();
    try { lastTui?.requestRender(); } catch { /* input feedback is best effort */ }
  }

  function flash(url: string): void {
    flashUrl = url;
    invalidateActiveGeneration();
    try { lastTui?.requestRender(); } catch { /* input feedback is best effort */ }
    try {
      if (flashTimer) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        flashTimer = undefined;
        if (flashUrl === url) flashUrl = undefined;
        if (!enabled) return;
        invalidateActiveGeneration();
        try { lastTui?.requestRender(); } catch { /* best effort */ }
      }, options.flashMs ?? 450);
    } catch (error) { suspend(error); }
  }

  const isActive = () => enabled && options.active();
  const currentFrame = (owner: MarkdownRuntime) => {
    const frame = frames.at(-1);
    return frame?.owner === owner && frame.tracking ? frame : undefined;
  };
  const resetPass = (frame: Frame, phase: FramePhase) => {
    frame.phase = phase;
    frame.containers = [];
    frame.codeCursor = 0;
    if (phase === "decorate") {
      for (const link of frame.links.values()) link.decorated = 0;
    }
  };
  const sameRendered = (left: RenderedCodeBlock, right: RenderedCodeBlock) =>
    left.text === right.text && left.lang === right.lang && left.raw === right.raw &&
    left.path.length === right.path.length && left.path.every((part, index) => part === right.path[index]);

  function nativePass(this: MarkdownRuntime, frame: Frame, width: number): string[] {
    resetPass(frame, "native");
    this.invalidate();
    return original.render.call(this, width);
  }

  function render(this: MarkdownRuntime, width: number): string[] {
    if (!isActive()) return original.render.call(this, width);
    const frame: Frame = {
      owner: this,
      tracking: false,
      phase: "collect",
      assistant: false,
      streaming: false,
      failed: false,
      decorationInvalid: false,
      containers: [],
      sources: [],
      sourceLinks: { destinations: new Map(), explicitAutolinks: new Set(), plainTextSpans: [] },
      native: [],
      codeEntries: [],
      links: new Map(),
      linkKeys: [],
      codeCursor: 0,
    };
    frames.push(frame);
    try {
      frame.tracking = register(this);
      const plain = original.render.call(this, width);
      if (!frame.tracking || !frame.assistant || frame.failed || trackingDisabled) return plain;
      try {
        const current = generation;
        if (!current || current.poison !== undefined || !current.store.isTrusted()) {
          throw current?.poison ?? new Error("no trusted bookkeeping generation");
        }
        const alignment = alignCodeBlocks(frame.sources, frame.native);
        if (alignment.ambiguous) warnMismatch(frame.streaming);
        const codeActions: Array<Readonly<Action> | undefined> = alignment.sourceIndexes.map((sourceIndex) =>
          sourceIndex === undefined ? undefined : { kind: "copy", text: frame.sources[sourceIndex]!.value });
        const linkActions = frame.linkKeys.map((key) => frame.links.get(key)?.action);
        const actions = [...codeActions, ...linkActions];
        if (actions.some((action) => action !== undefined)) {
          const entries = current.store.set(frame.owner, actions);
          frame.codeEntries = entries.slice(0, codeActions.length);
          for (const [index, key] of frame.linkKeys.entries()) {
            const link = frame.links.get(key);
            if (link) link.entry = entries[codeActions.length + index];
          }
          if (entries.some((entry) => entry !== undefined)) privateUriIssued = true;
        } else {
          const issues = deleteOwnerActions(current, frame.owner);
          if (issues.length) throw failureWithCleanup(new Error("owner action cleanup failed"), issues);
          frame.codeEntries = codeActions.map(() => undefined);
        }
        resetPass(frame, "decorate");
        this.invalidate();
        const decorated = original.render.call(this, width);
        const linksComplete = [...frame.links.values()].every((link) => link.decorated === link.count);
        if (frame.failed || frame.decorationInvalid || trackingDisabled ||
            frame.codeCursor !== frame.native.length || !linksComplete) {
          const issues = deleteOwnerActions(current, frame.owner);
          if (issues.length) suspend(failureWithCleanup(new Error("drift action cleanup failed"), issues));
          warnMismatch(frame.streaming);
          return nativePass.call(this, frame, width);
        }
        return decorated;
      } catch (error) {
        frame.failed = true;
        suspend(error);
        if (frame.phase === "collect") return plain;
        return nativePass.call(this, frame, width);
      }
    } finally { frames.pop(); }
  }

  function transform(source: string, context: MarkdownTransformContext): string {
    const frame = frames.at(-1);
    if (frame?.tracking && frame.phase === "collect" && context.messageType === "assistant") {
      frame.assistant = true;
      frame.streaming = context.isStreaming;
      try {
        frame.sources = sourceCodeBlocks(source);
        frame.sourceLinks = sourceLinkEvidence(source);
      } catch (error) {
        frame.sources = [];
        frame.sourceLinks = { destinations: new Map(), explicitAutolinks: new Set(), plainTextSpans: [] };
        frame.failed = true;
        suspend(error);
      }
    }
    return source;
  }

  function token(this: MarkdownRuntime, value: Token, width: number, next?: string, style?: InlineStyle): string[] {
    const frame = currentFrame(this);
    const container = value.type === "blockquote" || value.type === "list" ? value.type : undefined;
    if (frame && container) frame.containers.push(container);
    let lines: string[];
    try { lines = original.token.call(this, value, width, next, style); }
    finally { if (frame && container) frame.containers.pop(); }
    if (!enabled || value.type !== "code" || !frame || frame.phase === "native" || frame.failed) return lines;

    let descriptor: RenderedCodeBlock;
    try {
      descriptor = {
        text: value.text ?? "",
        lang: value.lang ?? "",
        raw: value.raw ?? "",
        path: [...frame.containers],
        syntax: codeSyntax(value.raw ?? ""),
      };
      if (frame.phase === "collect") {
        frame.native.push(descriptor);
        return lines;
      }
    } catch (error) {
      frame.failed = true;
      suspend(error);
      return lines;
    }

    const index = frame.codeCursor++;
    const expected = frame.native[index];
    if (!expected || !sameRendered(expected, descriptor)) {
      frame.decorationInvalid = true;
      return lines;
    }
    const entry = frame.codeEntries[index];
    if (!entry) return lines;
    try {
      const label = width >= 6 ? "[复制]" : width >= 4 ? "Copy" : "C";
      const styled = pressedUrl === entry.url || flashUrl === entry.url ?
        emphasize(options.button(label)) : options.button(label);
      const button = hyperlink(styled, entry.url);
      const footer = next && next !== "space" ? lines.length - 2 : lines.length - 1;
      if (footer < 0) return lines;
      const border = width >= visibleWidth(label) + 4 ? this.theme.codeBlockBorder("```") : "";
      lines[footer] = border + " ".repeat(Math.max(0, width - visibleWidth(border) - visibleWidth(label))) + button;
    } catch (error) {
      frame.failed = true;
      suspend(error);
    }
    return lines;
  }

  function linkKey(raw: string, href: string): string {
    return JSON.stringify([raw, href]);
  }

  function actionForLink(frame: Frame, raw: string, href: string): Readonly<Action> {
    const destinations = frame.sourceLinks.destinations.get(raw);
    if (destinations) {
      if (destinations.size !== 1) {
        return { kind: "diagnostic", message: "链接源文对应多个语义目标，已拒绝猜测" };
      }
      const semantic = destinations.values().next().value as string;
      if (!semantic) return { kind: "diagnostic", message: "链接目标为空，未执行任何打开操作" };

      const sourceWeb = webUrl(semantic);
      if (sourceWeb) {
        if (frame.sourceLinks.explicitAutolinks.has(raw)) {
          const nativeWeb = webUrl(href);
          if (!nativeWeb || nativeWeb !== sourceWeb) {
            return {
              kind: "diagnostic",
              message: `网页自动链接源文与 Pi 解析不一致，已拒绝打开（显示：${href}；源文：${semantic}）`,
            };
          }
        }
        return { kind: "open", href: sourceWeb };
      }

      if (isLocalHref(semantic)) {
        if (semantic === href) return { kind: "open", href: semantic };
        return {
          kind: "diagnostic",
          message: `本地链接源文与 Pi 解析不一致，已拒绝打开（显示：${href || "空目标"}；源文：${semantic}）`,
        };
      }
      const scheme = hasUriScheme(semantic) ? semantic.slice(0, semantic.indexOf(":")) : "未知";
      return { kind: "diagnostic", message: `不支持 URI scheme：${scheme}` };
    }

    const sourceAutolink = sourceAutolinkDestination(frame.sourceLinks, raw);
    if (sourceAutolink !== undefined) {
      const sourceWeb = webUrl(sourceAutolink);
      const nativeWeb = webUrl(href);
      if (sourceWeb && nativeWeb && sourceWeb === nativeWeb) return { kind: "open", href: sourceWeb };
      return {
        kind: "diagnostic",
        message: `网页自动链接源文与 Pi 解析不一致，已拒绝打开（显示：${href || "空目标"}；源文：${sourceAutolink}）`,
      };
    }

    return { kind: "diagnostic", message: "链接缺少唯一可验证的源文语义，已拒绝打开" };
  }

  function inline(this: MarkdownRuntime, tokens: Token[], style?: InlineStyle): string {
    const frame = currentFrame(this);
    if (!enabled || !frame || frame.phase === "native" || !frame.assistant || frame.failed) {
      return original.inline.call(this, tokens, style);
    }
    try {
      if (frame.phase === "collect") {
        for (const value of tokens) {
          if (value.type !== "link") continue;
          const raw = value.raw ?? "";
          const href = value.href ?? "";
          const key = linkKey(raw, href);
          const collected = frame.links.get(key);
          if (collected) collected.count += 1;
          else {
            frame.links.set(key, {
              raw, href, count: 1, decorated: 0,
              action: actionForLink(frame, raw, href),
            });
            frame.linkKeys.push(key);
          }
        }
        return original.inline.call(this, tokens, style);
      }

      const linked = tokens.map((value) => {
        if (value.type !== "link") return value;
        const key = linkKey(value.raw ?? "", value.href ?? "");
        const collected = frame.links.get(key);
        if (!collected || collected.decorated >= collected.count || !collected.entry) {
          frame.decorationInvalid = true;
          return value;
        }
        collected.decorated += 1;
        const text = this.renderInlineTokens(value.tokens ?? [], style);
        const styled = this.theme.link(this.theme.underline(text));
        const shown = pressedUrl === collected.entry.url ? emphasize(styled) : styled;
        return { type: "html", raw: hyperlink(shown, collected.entry.url) };
      });
      return original.inline.call(this, linked, style);
    } catch (error) {
      frame.failed = true;
      suspend(error);
      return original.inline.call(this, tokens, style);
    }
  }

  function generationIsLive(current: BookkeepingGeneration): boolean {
    return enabled && generation === current && current.poison === undefined &&
      !current.controller.signal.aborted && current.store.isTrusted();
  }

  function requireGenerationLive(current: BookkeepingGeneration): boolean {
    if (generationIsLive(current)) return true;
    if (enabled && generation === current) {
      const reason = current.poison ?? (current.controller.signal.aborted ?
        current.controller.signal.reason ?? new Error("bookkeeping generation aborted") :
        new Error("ActionStore generation is untrusted"));
      suspend(reason);
    }
    return false;
  }

  function currentPrivateAction(url: string): { current?: BookkeepingGeneration; entry?: ActionEntry; kind: Action["kind"] | "expired" } {
    const current = generation;
    if (!current || !requireGenerationLive(current) || !current.store.owns(url)) {
      return { current, kind: "expired" };
    }
    const lookup = current.store.lookup(url);
    if (lookup.error !== undefined) {
      suspend(lookup.error);
      return { current, kind: "expired" };
    }
    if (!requireGenerationLive(current)) return { current, kind: "expired" };
    return { current, entry: lookup.entry, kind: lookup.entry?.action.kind ?? "expired" };
  }

  function lifetimeFor(current: BookkeepingGeneration): OpenLifetime {
    return Object.freeze({
      signal: current.controller.signal,
      isCurrent: () => generationIsLive(current),
    });
  }

  function invoke(url: string): void {
    if (url.startsWith("pi-copy://")) {
      const resolved = currentPrivateAction(url);
      const { current, entry } = resolved;
      if (!current || !entry) {
        if (current && !generationIsLive(current)) return;
        try { options.notify("动作已失效，请刷新显示后重试", true); } catch { /* best effort */ }
        return;
      }
      if (!requireGenerationLive(current)) return;
      if (entry.action.kind === "diagnostic") {
        if (!requireGenerationLive(current)) return;
        try { options.notify(entry.action.message, true); } catch { /* best effort */ }
        return;
      }
      if (entry.action.kind === "copy") {
        const text = entry.action.text;
        void enqueueClipboard(async () => {
          if (!requireGenerationLive(current)) return;
          await options.copy(text);
          if (!requireGenerationLive(current)) return;
          try { options.notify("代码已复制"); } catch { /* best effort */ }
          flash(entry.url);
        }).catch((error: unknown) => {
          if (!requireGenerationLive(current)) return;
          try { options.notify(`复制失败：${String(error)}`, true); } catch { /* best effort */ }
        });
        return;
      }
      let cwd: string;
      try {
        cwd = options.cwd();
        if (!isAbsolute(cwd)) throw new Error("Pi 会话工作目录不是绝对路径");
      } catch (error) {
        if (!requireGenerationLive(current)) return;
        try { options.notify(`打开链接失败：${String(error)}`, true); } catch { /* best effort */ }
        return;
      }
      if (!requireGenerationLive(current)) return;
      const lifetime = lifetimeFor(current);
      void options.open(entry.action.href, cwd, lifetime).catch((error: unknown) => {
        if (!requireGenerationLive(current)) return;
        try { options.notify(`打开链接失败：${String(error)}`, true); } catch { /* best effort */ }
      });
      return;
    }
  }

  function input(this: FullscreenRuntime, data: string) {
    const active = enabled && isActive();
    if (!active) return callNativeFiltered(this, data, original.input, true);
    lastTui = this;
    const event = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/u.exec(data);
    const pending = presses.get(this);
    if (!event) {
      presses.delete(this);
      releases.delete(this);
      setPressed(undefined);
      return callNativeFiltered(this, data, original.input, false);
    }
    const bits = Number(event[1]);
    const x = Number(event[2]) - 1;
    const y = Number(event[3]) - 1;
    const release = event[4] === "m";
    const motion = (bits & 32) !== 0;
    const wheel = (bits & 64) !== 0;
    const primary = (bits & 3) === 0;
    const width = this.terminal.columns;
    const height = this.terminal.rows;
    const overlayOwned = overlayOwnsInput(this, data);
    const screenValid = this.previousScreenWidth === width && this.previousScreenHeight === height &&
      x >= 0 && x < width && y >= 0 && y < height && Array.isArray(this.previousScreen);
    const valid = !overlayOwned && screenValid;
    const line = screenValid ? this.previousScreen[y] ?? "" : "";
    const url = getOsc8LinkAtColumn(line, x);
    const privateAction = url?.startsWith("pi-copy://") === true;
    const actionKind = !url || !privateAction ? undefined : currentPrivateAction(url).kind;
    const modifiers = bits & 28;
    const authorized = actionKind === "copy" ? (bits & 12) === 0 :
      actionKind !== undefined && bits === 16;
    const actionable = Boolean(url && primary && authorized);
    const rememberRelease = () => {
      if (valid && primary && release && !motion) releases.set(this, {
        bits,
        at: performance.now(),
        screen: [...this.previousScreen],
        width,
        height,
        x,
        y,
        ...(url === undefined ? {} : { url }),
        privateAction,
        ...(actionKind === undefined ? {} : { actionKind }),
        overlayOwned,
        overlays: overlaySnapshot(this),
      });
      else releases.delete(this);
    };
    if (pending && !wheel) {
      if (motion || x !== pending.x || y !== pending.y) {
        pending.moved = true;
        setPressed(undefined);
      }
      if (release) {
        presses.delete(this);
        const releaseAuthorized = pending.actionKind === "copy" ? (bits & 12) === 0 : bits === 16;
        if (primary && releaseAuthorized && modifiers === pending.modifiers && !pending.moved && valid &&
            pending.url === url && sameOverlaySnapshot(this, pending.overlays) &&
            unpress(pending.line) === unpress(line) && pending.width === width && pending.height === height) {
          invoke(pending.url);
        }
        if (pending.moved) releases.delete(this);
        else rememberRelease();
        setPressed(undefined);
      }
      return { consume: true };
    }
    if (wheel) { presses.delete(this); setPressed(undefined); }
    if (motion || wheel || !valid) releases.delete(this);
    const previousRelease = releases.get(this);
    if (release && bits === 16 && !motion && actionable && valid && options.recoverTmuxRelease?.() &&
        previousRelease && performance.now() - previousRelease.at <= 500 &&
        (previousRelease.bits & 28) === 0 && previousRelease.x === x && previousRelease.y === y &&
        previousRelease.url === url && previousRelease.privateAction === privateAction &&
        previousRelease.actionKind === actionKind && previousRelease.overlayOwned === overlayOwned &&
        sameOverlaySnapshot(this, previousRelease.overlays) && previousRelease.width === width &&
        previousRelease.height === height && previousRelease.screen.length === this.previousScreen.length &&
        previousRelease.screen.every((row, index) => unpress(row) === unpress(this.previousScreen[index]!))) {
      invoke(url!);
      rememberRelease();
      return { consume: true };
    }
    if (release) rememberRelease();
    else if (primary && !motion && !wheel) releases.delete(this);
    if (!wheel && !motion && !release && primary && valid && actionable) {
      releases.delete(this);
      presses.set(this, {
        url: url!, actionKind: actionKind!, modifiers, x, y, line, width, height,
        overlays: overlaySnapshot(this), moved: false,
      });
      setPressed(url);
      return { consume: true };
    }
    return callNativeFiltered(this, data, original.input, overlayOwned);
  }

  const inputGuard: InputGuardMetadata = {
    baseMethod: original.input,
    baseDescriptor: inputBaseCapture.descriptor,
    passive: false,
    requiresQuiescence: privateUriIssued,
  };
  Object.defineProperty(input, INPUT_GUARD, { value: inputGuard });
  let passiveReplacement: GuardedInput | undefined;

  try { generation = createGeneration(); }
  catch (error) {
    enabled = false;
    throw new Error(`pi-copy-links 无法创建动作登记代际：${reasonOf(error)}`);
  }

  const surfaces: HookSurface[] = captures.map((capture, index) => ({
    ...capture,
    wrapper: [render, token, inline, input][index] as HookSurface["wrapper"],
  }));
  try { installHooks(surfaces); }
  catch (error) {
    enabled = false;
    const retired = generation;
    generation = undefined;
    if (retired) retireGeneration(retired);
    throw error;
  }

  return {
    transform,
    dispose() {
      enabled = false;
      const issues: CleanupIssue[] = [];
      attempt(issues, "flashTimer.clear", () => { if (flashTimer) clearTimeout(flashTimer); });
      flashTimer = undefined;
      attempt(issues, "recoveryTimer.clear", () => { if (recoveryTimer) clearTimeout(recoveryTimer); });
      recoveryTimer = undefined;
      pressedUrl = undefined;
      flashUrl = undefined;

      const retired = generation;
      generation = undefined;
      if (retired) {
        issues.push(...invalidateGeneration(retired));
        issues.push(...retireGeneration(retired));
      }

      let quiesced = !privateUriIssued;
      if (!quiesced) {
        if (!options.quiesce) {
          issues.push({ operation: "fullscreen.quiesce", error: new Error("no synchronous screen verifier") });
        } else {
          attempt(issues, "fullscreen.quiesce", () => {
            quiesced = options.quiesce!() === true;
            if (!quiesced) throw new Error("composed fullscreen screen still contains a private URI");
          });
        }
      }
      lastTui = undefined;

      for (const surface of [...surfaces.slice(0, 3)].reverse()) {
        let current: unknown;
        attempt(issues, `${surface.name}.read`, () => { current = methodAt(surface.owner, surface.key); });
        if (current !== surface.wrapper) continue;
        attempt(issues, `${surface.name}.restore`, () => { restoreHook(surface); });
      }

      const inputSurface = surfaces[3]!;
      let currentInput: unknown;
      attempt(issues, `${inputSurface.name}.read`, () => {
        currentInput = methodAt(inputSurface.owner, inputSurface.key);
      });
      let passive = currentInput !== inputBaseCapture.method;
      const ownedInput = currentInput === inputSurface.wrapper || currentInput === passiveReplacement;
      if (quiesced && ownedInput) {
        try {
          restoreHook(inputBaseCapture);
          passiveReplacement = undefined;
          passive = false;
        } catch (error) {
          issues.push({ operation: `${inputSurface.name}.restore`, error });
          inputGuard.passive = currentInput === inputSurface.wrapper;
          inputGuard.requiresQuiescence = false;
          passive = true;
        }
      } else if (!quiesced && currentInput === inputSurface.wrapper) {
        const minimal = createPassiveInput(
          inputBaseCapture.method as unknown as NativeInput,
          inputBaseCapture.descriptor,
          true,
        );
        try {
          if (!Reflect.set(inputSurface.owner, inputSurface.key, minimal, inputSurface.owner) ||
              methodAt(inputSurface.owner, inputSurface.key) !== minimal) {
            throw new Error(`${inputSurface.name} 被动过滤器安装后身份不一致`);
          }
          passiveReplacement = minimal;
        } catch (error) {
          issues.push({ operation: `${inputSurface.name}.passive`, error });
          let afterFailure: unknown;
          attempt(issues, `${inputSurface.name}.passive.read`, () => {
            afterFailure = methodAt(inputSurface.owner, inputSurface.key);
          });
          if (afterFailure === minimal) passiveReplacement = minimal;
          else {
            inputGuard.passive = true;
            inputGuard.requiresQuiescence = true;
          }
        }
        passive = true;
      } else if (currentInput !== inputBaseCapture.method) {
        // A later wrapper may still delegate to our active input; make that delegate inert.
        inputGuard.passive = true;
        inputGuard.requiresQuiescence = !quiesced;
        passive = true;
      }

      if (issues.length) {
        try {
          options.notify(
            `pi-copy-links 卸载清理遇到错误：${reasonOf(failureWithCleanup(new Error("dispose cleanup failed"), issues))}`,
            true,
          );
        } catch { /* Disposal is noexcept. */ }
      }
      return { quiesced, passive };
    },
  };
}
