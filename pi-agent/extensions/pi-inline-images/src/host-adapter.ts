import { createHash } from "node:crypto";
import type { Component, TUI } from "@earendil-works/pi-tui";
import { assistantTextBlocks, type ImageSession } from "./session.ts";

export const READ_PREVIEW_ENTRIES_CHANGED = "pi-inline-images:read-preview-entries-changed";
export const READ_PREVIEW_COORDINATION = "pi-inline-images:read-preview-coordination";
export const READ_PREVIEW_COORDINATION_VERSION = 1;
const ENTRY_TYPE = "pi-tmux-images.preview";
const CLEAR_TYPE = "pi-tmux-images.clear";
const PLACEHOLDER = "\u{10EEEE}";
const MAX_REMEMBERED_PREFERENCES = 256;

export type HostSessionEntry = {
  type: string;
  customType?: string;
  data?: unknown;
  [key: string]: unknown;
};
export type HostAdapterApi = {
  version: string;
  sessionEntryToContextMessages(entry: HostSessionEntry): unknown[];
  /** Public native protocol capability; null means no native bitmap can compete. */
  nativeImageProtocol?(): "kitty" | "iterm2" | null;
  /** Identify Pi's public Markdown component; source is checked against the bound text block at transform time. */
  isAssistantTextMarkdown(component: Component): boolean;
};
type AssistantComponent = Component & {
  setHideThinkingBlock(hidden: boolean): void;
  setHiddenThinkingLabel(label: string): void;
};
type ToolComponent = Component & {
  setShowImages(show: boolean): void;
  setImageWidthCells(width: number): void;
};
type Expected =
  | { kind: "assistant"; message: object; fingerprint: string; live: boolean }
  | { kind: "tool"; toolCallId: string; toolName: string };
type Observed =
  | { kind: "assistant"; component: AssistantComponent }
  | { kind: "tool"; component: ToolComponent }
  | { kind: "ambiguous"; component: Component };
type AssistantBinding = {
  component: AssistantComponent;
  message: object;
  fingerprint: string;
  live: boolean;
  original: AssistantComponent["render"];
  wrapper: AssistantComponent["render"];
  /** Whether the wrapped method was an own property; prototype methods are restored by deleting the override. */
  hadOwnMethod: boolean;
  active: boolean;
};
type ToolBinding = {
  component: ToolComponent;
  toolCallId: string;
  original: ToolComponent["setShowImages"];
  wrapper: ToolComponent["setShowImages"];
  /** Whether the wrapped method was an own property; prototype methods are restored by deleting the override. */
  hadOwnMethod: boolean;
  active: boolean;
  suppressed: boolean;
  completedWithoutImages: boolean;
  /** Undefined until a result bitmap is observed or public setShowImages is called. */
  externalDesired: boolean | undefined;
};
export type ReadPreviewCoordination = {
  version: typeof READ_PREVIEW_COORDINATION_VERSION;
  ready: boolean;
  activeLogicalIds: string[];
  reason?: string;
};

function isAssistantComponent(value: unknown): value is AssistantComponent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<AssistantComponent>;
  return typeof candidate.render === "function" && typeof candidate.setHideThinkingBlock === "function"
    && typeof candidate.setHiddenThinkingLabel === "function";
}
function isToolComponent(value: unknown): value is ToolComponent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ToolComponent>;
  return typeof candidate.render === "function" && typeof candidate.setShowImages === "function"
    && typeof candidate.setImageWidthCells === "function";
}
function assistantMarkdownComponents(root: AssistantComponent, isMarkdown: HostAdapterApi["isAssistantTextMarkdown"]): Component[] {
  const markdown: Component[] = [];
  const seen = new Set<object>();
  const visit = (value: unknown) => {
    if (!value || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    if (isMarkdown(value as Component)) { markdown.push(value as Component); return; }
    const children = (value as { children?: unknown }).children;
    if (Array.isArray(children)) for (const child of children) visit(child);
  };
  visit(root);
  return markdown;
}
function observedComponents(root: TUI): Observed[] {
  const observed: Observed[] = [];
  const seen = new Set<object>();
  const visit = (value: unknown) => {
    if (!value || typeof value !== "object" || seen.has(value as object)) return;
    seen.add(value as object);
    const assistant = isAssistantComponent(value);
    const tool = isToolComponent(value);
    // A component matching both contracts cannot be associated unambiguously.
    if (assistant && tool) observed.push({ kind: "ambiguous", component: value });
    else if (assistant) observed.push({ kind: "assistant", component: value });
    else if (tool) observed.push({ kind: "tool", component: value });
    const children = (value as { children?: unknown }).children;
    if (Array.isArray(children)) for (const child of children) visit(child);
  };
  visit(root);
  return observed;
}
function contextMessages(entries: readonly HostSessionEntry[], host: HostAdapterApi): unknown[] {
  return entries.flatMap((entry) => {
    if (entry.type === "custom") return [];
    const messages = host.sessionEntryToContextMessages(entry);
    if (!Array.isArray(messages)) {
      throw new Error(`sessionEntryToContextMessages returned a non-array for entry type "${entry.type}"`);
    }
    return messages;
  });
}
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
/** A method can be wrapped reversibly only as a writable data property on an extensible instance. */
function reversibleMethod(component: object, key: string): string | undefined {
  const own = Object.getOwnPropertyDescriptor(component, key);
  if (own) {
    if (typeof own.value !== "function") return `"${key}" is an accessor or non-function own property, not a reversible method`;
    if (own.writable !== true) return `own "${key}" is not writable`;
    return undefined;
  }
  let proto: object | null = Object.getPrototypeOf(component);
  while (proto) {
    const inherited = Object.getOwnPropertyDescriptor(proto, key);
    if (inherited) {
      if (typeof inherited.value !== "function") return `inherited "${key}" is an accessor or non-function property, not a reversible method`;
      if (inherited.writable !== true) return `inherited "${key}" is not writable, so an own override cannot be assigned`;
      break;
    }
    proto = Object.getPrototypeOf(proto);
  }
  if (!Object.isExtensible(component)) return `instance is not extensible, so an own "${key}" override cannot be installed`;
  return undefined;
}
function role(value: unknown): string | undefined {
  return value && typeof value === "object" && typeof (value as { role?: unknown }).role === "string"
    ? (value as { role: string }).role : undefined;
}
function toolCallId(value: unknown): string | undefined {
  const id = value && typeof value === "object" ? (value as { toolCallId?: unknown }).toolCallId : undefined;
  return typeof id === "string" && id ? id : undefined;
}
function assistantFingerprint(message: object): string {
  const hash = createHash("sha256");
  const content = (message as { content?: unknown }).content;
  if (Array.isArray(content)) for (const part of content) {
    if (!part || typeof part !== "object") { hash.update("unknown\0"); continue; }
    const value = part as { type?: unknown; id?: unknown; name?: unknown; text?: unknown; thinking?: unknown };
    hash.update(`${String(value.type)}\0`);
    if (value.type === "toolCall") hash.update(`${String(value.id)}\0${String(value.name)}\0`);
    else if (value.type === "text" && typeof value.text === "string") hash.update(value.text);
    else if (value.type === "thinking" && typeof value.thinking === "string") hash.update(value.thinking);
    hash.update("\0");
  }
  return hash.digest("hex");
}
function appendAssistant(expected: Expected[], message: object, live: boolean): void {
  expected.push({ kind: "assistant", message, fingerprint: assistantFingerprint(message), live });
  const content = (message as { content?: unknown }).content;
  if (!Array.isArray(content)) return;
  for (const part of content) {
    if (!part || typeof part !== "object" || (part as { type?: unknown }).type !== "toolCall") continue;
    const id = (part as { id?: unknown }).id;
    const name = (part as { name?: unknown }).name;
    if (typeof id === "string" && id && typeof name === "string" && name) expected.push({ kind: "tool", toolCallId: id, toolName: name });
  }
}
function expectedComponents(messages: readonly unknown[], liveAssistant?: object): Expected[] {
  const expected: Expected[] = [];
  for (const message of messages) if (role(message) === "assistant") appendAssistant(expected, message as object, false);
  if (liveAssistant) appendAssistant(expected, liveAssistant, true);
  return expected;
}
function resultImages(messages: readonly unknown[], liveResults: ReadonlyMap<string, object>): Map<string, Set<number>> {
  const results = new Map<string, Set<number>>();
  const inspect = (value: unknown) => {
    if (role(value) !== "toolResult") return;
    const id = toolCallId(value);
    const content = (value as { content?: unknown }).content;
    if (!id || !Array.isArray(content)) return;
    const indexes = new Set<number>();
    content.forEach((part, index) => {
      if (part && typeof part === "object" && (part as { type?: unknown }).type === "image") indexes.add(index);
    });
    if (indexes.size) results.set(id, indexes);
  };
  messages.forEach(inspect);
  for (const value of liveResults.values()) inspect(value);
  return results;
}
function activeClaims(branch: readonly HostSessionEntry[]): Map<string, Map<number, string>> {
  let latestClear = -1;
  branch.forEach((entry, index) => {
    if (entry.type === "custom" && entry.customType === CLEAR_TYPE) latestClear = index;
  });
  const claims = new Map<string, Map<number, string>>();
  const previews = branch.slice(latestClear + 1).filter((entry) =>
    entry.type === "custom" && entry.customType === ENTRY_TYPE && entry.data && typeof entry.data === "object").slice(-16);
  for (const entry of previews) {
    const data = entry.data as { logicalId?: unknown; origin?: { key?: unknown; blockIndex?: unknown } };
    const key = data.origin?.key;
    const blockIndex = data.origin?.blockIndex;
    if (typeof data.logicalId !== "string" || typeof key !== "string" || !key.startsWith("tool:")
      || !Number.isSafeInteger(blockIndex) || Number(blockIndex) < 0) continue;
    const id = key.slice(5);
    const blocks = claims.get(id) ?? new Map<number, string>();
    blocks.set(Number(blockIndex), data.logicalId);
    claims.set(id, blocks);
  }
  return claims;
}
function containsNativeBitmap(lines: readonly string[]): boolean {
  return lines.some((line) => line.includes("\x1b_G") || line.includes("\x1b]1337;File=") || line.includes(PLACEHOLDER));
}
function reconciliationDataSignature(entries: readonly HostSessionEntry[], liveAssistant: object | undefined, liveResults: ReadonlyMap<string, object>): string {
  const hash = createHash("sha256");
  for (const entry of entries) {
    hash.update(`${entry.type}\0${String(entry.customType ?? "")}\0`);
    if (entry.type === "message" && entry.message && typeof entry.message === "object") {
      hash.update(`${role(entry.message) ?? ""}\0${toolCallId(entry.message) ?? ""}\0`);
      if (role(entry.message) === "assistant") hash.update(assistantFingerprint(entry.message));
    } else if (entry.type === "custom" && entry.data && typeof entry.data === "object") {
      const data = entry.data as { logicalId?: unknown; origin?: { key?: unknown; blockIndex?: unknown } };
      hash.update(`${String(data.logicalId ?? "")}\0${String(data.origin?.key ?? "")}\0${String(data.origin?.blockIndex ?? "")}\0`);
    }
  }
  if (liveAssistant) hash.update(`live:${assistantFingerprint(liveAssistant)}\0`);
  for (const id of [...liveResults.keys()].sort()) hash.update(`result:${id}\0`);
  return hash.digest("hex");
}

/**
 * Host adapter over public component identity, traversal, render, and image-toggle methods.
 * Admission is capability-based, never version-based: any Pi release is attempted; missing
 * capabilities, ambiguous component contracts, irreversible method overrides, and failed
 * postconditions fail closed with the concrete reason. The host version appears in
 * diagnostics only. Structural checks (sequence, identity, coverage) re-verify the mapping
 * on every reconcile, so semantic drift on an untested release degrades to a notice rather
 * than a mis-assigned bitmap.
 */
export class HostImageOwnershipAdapter {
  private tui?: TUI;
  private readonly assistants = new Map<AssistantComponent, AssistantBinding>();
  private readonly tools = new Map<ToolComponent, ToolBinding>();
  private readonly componentIds = new WeakMap<object, number>();
  private nextComponentId = 1;
  private hostProbed = false;
  private hostRejection?: string;
  private liveAssistant?: { message: object; fingerprint: string; baseline: number };
  private readonly persistedAssistantCounts = new Map<string, number>();
  private readonly liveResults = new Map<string, object>();
  private preferenceRevision = 0;
  private preferenceScope?: string;
  private readonly rememberedPreferences = new Map<string, boolean>();
  /** At most 16 call IDs, from the same recent claim set used for display ownership. */
  private readonly retainedImageCalls = new Set<string>();
  private reconstructionHold?: { reason: string; priorTree: string };

  constructor(
    private readonly session: ImageSession,
    private readonly publish: (coordination: ReadPreviewCoordination) => void,
    private readonly host: HostAdapterApi,
    private readonly ownershipChanged: () => void = () => undefined,
  ) {}

  setTui(tui: TUI): void { this.tui = tui; }

  observeMessage(phase: "start" | "update" | "end", message: unknown): void {
    const messageRole = role(message);
    if (messageRole === "assistant" && message && typeof message === "object") {
      const fingerprint = assistantFingerprint(message);
      this.liveAssistant = { message, fingerprint, baseline: this.persistedAssistantCounts.get(fingerprint) ?? 0 };
    }
    if (phase === "end" && messageRole === "toolResult" && message && typeof message === "object") {
      const id = toolCallId(message);
      if (id) this.liveResults.set(id, message);
    }
  }

  reconciliationSignature(renderEntries: readonly HostSessionEntry[], branch: readonly HostSessionEntry[], sessionScope = "default"): string {
    const tree = this.publicTreeSignature();
    const data = reconciliationDataSignature(branch, this.liveAssistant?.message, this.liveResults);
    // Absent probe method ("unknown") and a present null capability ("null") are distinct states.
    const native = this.host.nativeImageProtocol ? String(this.host.nativeImageProtocol()) : "unknown";
    return `${tree}|${data}|scope:${sessionScope}|pref:${this.preferenceRevision}|native:${native}|render:${renderEntries.length}`;
  }

  publicTreeSignature(): string {
    if (!this.tui) return "unmounted";
    const observed = observedComponents(this.tui);
    return `${this.tui.children.length}|${observed.map(({ kind, component }) => `${kind}:${this.componentId(component)}`).join(",")}`;
  }

  reconcile(renderEntries: readonly HostSessionEntry[], branch: readonly HostSessionEntry[], sessionScope = "default"): boolean {
    if (!this.tui) return this.fail("host TUI is not mounted");
    const rejection = this.probeHost();
    if (rejection) return this.fail(rejection);
    this.setPreferenceScope(sessionScope);
    if (this.reconstructionHold) {
      if (this.publicTreeSignature() === this.reconstructionHold.priorTree) {
        this.publish({ version: READ_PREVIEW_COORDINATION_VERSION, ready: false, activeLogicalIds: [], reason: this.reconstructionHold.reason });
        return false;
      }
      this.reconstructionHold = undefined;
    }
    let messages: unknown[];
    try {
      messages = contextMessages(renderEntries, this.host);
    } catch (error) {
      return this.fail(`session entry projection failed: ${errorMessage(error)}`);
    }
    const assistantCounts = new Map<string, number>();
    for (const message of messages) if (role(message) === "assistant") {
      const fingerprint = assistantFingerprint(message as object);
      assistantCounts.set(fingerprint, (assistantCounts.get(fingerprint) ?? 0) + 1);
    }
    if (this.liveAssistant && (assistantCounts.get(this.liveAssistant.fingerprint) ?? 0) > this.liveAssistant.baseline) this.liveAssistant = undefined;
    this.persistedAssistantCounts.clear();
    for (const [fingerprint, count] of assistantCounts) this.persistedAssistantCounts.set(fingerprint, count);
    const persistedResults = new Set(messages.filter((message) => role(message) === "toolResult").map(toolCallId).filter((id): id is string => Boolean(id)));
    for (const id of persistedResults) this.liveResults.delete(id);

    const expected = expectedComponents(messages, this.liveAssistant?.message);
    const results = resultImages(messages, this.liveResults);
    const claims = activeClaims(branch);
    const completedTextCalls = new Set([...persistedResults, ...this.liveResults.keys()].filter((id) => !results.has(id)));
    this.preparePreferenceRetention(
      new Set(expected.flatMap((item) => item.kind === "tool" ? [item.toolCallId] : [])),
      completedTextCalls,
      claims,
    );
    const observed = observedComponents(this.tui);
    if (observed.some((item) => item.kind === "ambiguous")) {
      return this.fail("a host component matches both the assistant and tool contracts; association is ambiguous");
    }
    if (expected.length !== observed.length || expected.some((item, index) => item.kind !== observed[index]?.kind)) {
      return this.fail(`host component sequence mismatch (${expected.length} expected, ${observed.length} observed)`);
    }
    for (let index = 0; index < expected.length; index++) {
      const wanted = expected[index]!;
      const found = observed[index]!;
      if (wanted.kind === "assistant" && found.kind === "assistant") {
        const prior = this.assistants.get(found.component);
        if (prior && prior.fingerprint !== wanted.fingerprint && !prior.live) return this.fail("assistant component identity changed stable message binding");
      } else if (wanted.kind === "tool" && found.kind === "tool") {
        const prior = this.tools.get(found.component);
        if (prior && prior.toolCallId !== wanted.toolCallId) return this.fail("tool component identity changed call binding");
      }
    }

    const currentAssistants = new Set<AssistantComponent>();
    const currentTools = new Set<ToolComponent>();
    const toolRows = new Map<string, ToolComponent>();
    for (let index = 0; index < expected.length; index++) {
      const wanted = expected[index]!;
      const found = observed[index]!;
      if (wanted.kind === "assistant" && found.kind === "assistant") {
        currentAssistants.add(found.component);
        const failure = this.bindAssistant(found.component, wanted);
        if (failure) return this.fail(failure);
      } else if (wanted.kind === "tool" && found.kind === "tool") {
        currentTools.add(found.component);
        if (toolRows.has(wanted.toolCallId)) {
          return this.fail(`duplicate tool call id "${wanted.toolCallId}" in the host component sequence; association is ambiguous`);
        }
        toolRows.set(wanted.toolCallId, found.component);
        const failure = this.bindTool(found.component, wanted.toolCallId, completedTextCalls.has(wanted.toolCallId));
        if (failure) return this.fail(failure);
      }
    }
    this.releaseDetached(currentAssistants, currentTools);

    const accepted = new Set<string>();
    let nativeProtocol: "kitty" | "iterm2" | null | undefined;
    try {
      nativeProtocol = this.host.nativeImageProtocol?.();
    } catch (error) {
      return this.fail(`native image capability probe failed: ${errorMessage(error)}`);
    }
    const width = Math.max(1, this.tui.terminal.columns);
    for (const [id, row] of toolRows) {
      const result = results.get(id);
      const claimed = claims.get(id);
      const complete = Boolean(result?.size && claimed && result.size === claimed.size
        && [...result].every((blockIndex) => claimed.has(blockIndex)));
      const binding = this.tools.get(row)!;
      if (complete && binding.externalDesired !== false && nativeProtocol === null) {
        // Native protocol absence is capability, not user intent: keep the latent row flag untouched.
        const failure = this.release(row);
        if (failure) return this.fail(failure);
        for (const logicalId of claimed!.values()) accepted.add(logicalId);
        continue;
      }
      if (complete && binding.externalDesired === undefined) {
        let nativeVisible: boolean;
        try {
          nativeVisible = containsNativeBitmap(row.render(width));
        } catch (error) {
          return this.fail(`tool row render failed while observing the native image preference: ${errorMessage(error)}`);
        }
        if (nativeVisible) {
          binding.externalDesired = true;
          this.rememberBindingPreference(binding);
        }
      }
      if (complete && binding.externalDesired === true) {
        const failure = this.suppress(row, width);
        if (failure) return this.fail(failure);
        for (const logicalId of claimed!.values()) accepted.add(logicalId);
      } else {
        const failure = this.release(row);
        if (failure) return this.fail(failure);
      }
    }
    this.publish({ version: READ_PREVIEW_COORDINATION_VERSION, ready: true, activeLogicalIds: [...accepted].sort() });
    return true;
  }

  suspend(reason: string, waitForNewGeneration = true): void {
    let priorTree = "unavailable";
    try {
      priorTree = this.publicTreeSignature();
    } catch {
      // A broken tree must not block suspension; the hold simply clears on the next differing signature.
    }
    this.restoreAllBindings();
    this.tools.clear();
    this.assistants.clear();
    this.liveResults.clear();
    this.liveAssistant = undefined;
    this.persistedAssistantCounts.clear();
    this.reconstructionHold = waitForNewGeneration ? { reason, priorTree } : undefined;
    this.publish({ version: READ_PREVIEW_COORDINATION_VERSION, ready: false, activeLogicalIds: [], reason });
  }

  dispose(): void {
    this.restoreAllBindings();
    this.tools.clear();
    this.assistants.clear();
    this.liveResults.clear();
    this.liveAssistant = undefined;
    this.persistedAssistantCounts.clear();
    this.rememberedPreferences.clear();
    this.retainedImageCalls.clear();
    this.preferenceScope = undefined;
    this.reconstructionHold = undefined;
    this.publish({ version: READ_PREVIEW_COORDINATION_VERSION, ready: false, activeLogicalIds: [], reason: "adapter disposed" });
    this.tui = undefined;
  }

  private componentId(component: object): number {
    const existing = this.componentIds.get(component);
    if (existing) return existing;
    const id = this.nextComponentId++;
    this.componentIds.set(component, id);
    return id;
  }

  private setPreferenceScope(scope: string): void {
    if (this.preferenceScope === scope) return;
    this.preferenceScope = scope;
    this.rememberedPreferences.clear();
    this.retainedImageCalls.clear();
    this.preferenceRevision++;
  }

  private preparePreferenceRetention(
    activeCalls: ReadonlySet<string>,
    completedTextCalls: ReadonlySet<string>,
    claims: ReadonlyMap<string, ReadonlyMap<number, string>>,
  ): void {
    this.retainedImageCalls.clear();
    for (const callId of claims.keys()) if (activeCalls.has(callId)) this.retainedImageCalls.add(callId);
    let changed = false;
    for (const callId of this.rememberedPreferences.keys()) {
      // Image results can precede their custom entry. Keep those choices in the
      // unpinned FIFO; only a completed result with no images is irrelevant.
      if (!activeCalls.has(callId) || (completedTextCalls.has(callId) && !this.retainedImageCalls.has(callId))) {
        this.rememberedPreferences.delete(callId);
        changed = true;
      }
    }
    if (changed) this.preferenceRevision++;
  }

  private rememberPreference(callId: string, show: boolean): void {
    this.rememberedPreferences.delete(callId);
    this.rememberedPreferences.set(callId, show);
    // At most 16 entries can be pinned; the remainder is a FIFO for pending/unclaimed images.
    for (const id of this.rememberedPreferences.keys()) {
      if (this.rememberedPreferences.size <= MAX_REMEMBERED_PREFERENCES) break;
      if (!this.retainedImageCalls.has(id)) this.rememberedPreferences.delete(id);
    }
  }

  private rememberBindingPreference(binding: ToolBinding): void {
    if (binding.externalDesired === undefined) return;
    if (!binding.completedWithoutImages || this.retainedImageCalls.has(binding.toolCallId)) this.rememberPreference(binding.toolCallId, binding.externalDesired);
    else this.rememberedPreferences.delete(binding.toolCallId);
  }

  /**
   * Admission probe: the required host surface must exist and be callable. Unknown releases
   * are attempted, not rejected; per-reconcile structural checks remain the semantic gate.
   * Cached because the host identity cannot change within one adapter lifetime.
   */
  private probeHost(): string | undefined {
    if (this.hostProbed) return this.hostRejection;
    this.hostProbed = true;
    if (typeof this.host.sessionEntryToContextMessages !== "function") {
      this.hostRejection = "sessionEntryToContextMessages is not a function";
    } else if (this.host.nativeImageProtocol !== undefined && typeof this.host.nativeImageProtocol !== "function") {
      this.hostRejection = "nativeImageProtocol is present but not a function";
    } else if (typeof this.host.isAssistantTextMarkdown !== "function") {
      this.hostRejection = "isAssistantTextMarkdown is not a function";
    }
    return this.hostRejection;
  }

  private bindAssistant(component: AssistantComponent, expected: Extract<Expected, { kind: "assistant" }>): string | undefined {
    const prior = this.assistants.get(component);
    if (prior) {
      prior.message = expected.message;
      prior.fingerprint = expected.fingerprint;
      prior.live = expected.live;
      return undefined;
    }
    const irreversible = reversibleMethod(component, "render");
    if (irreversible) return `assistant component "render" cannot be wrapped reversibly: ${irreversible}`;
    const original = component.render;
    const binding: AssistantBinding = {
      component,
      message: expected.message,
      fingerprint: expected.fingerprint,
      live: expected.live,
      original,
      wrapper: original,
      hadOwnMethod: Object.prototype.hasOwnProperty.call(component, "render"),
      active: true,
    };
    binding.wrapper = (width) => binding.active
      ? this.renderAssistant(binding, width)
      : binding.original.call(component, width);
    component.render = binding.wrapper;
    this.assistants.set(component, binding);
    try {
      // The host may have rendered this new component before reconciliation. Its
      // Markdown then caches an unbound coordination notice and will not run the
      // transformer again merely because the assistant render is now decorated.
      // Clear only these child caches once, on first binding, not the whole TUI.
      for (const markdown of assistantMarkdownComponents(component, this.host.isAssistantTextMarkdown)) markdown.invalidate();
    } catch (error) {
      return `assistant Markdown cache invalidation failed: ${errorMessage(error)}`;
    }
    return undefined;
  }

  private renderAssistant(binding: AssistantBinding, width: number): string[] {
    type RenderOverride = { component: Component; original: Component["render"]; wrapper: Component["render"]; hadOwnMethod: boolean };
    const overrides: RenderOverride[] = [];
    const restore = () => {
      for (const { component, original, wrapper, hadOwnMethod } of overrides.reverse()) {
        if (component.render !== wrapper) continue; // another owner replaced it
        if (hadOwnMethod) component.render = original;
        else delete (component as Partial<Component>).render;
      }
      overrides.length = 0;
    };
    try {
      const blocks = assistantMarkdownComponents(binding.component, this.host.isAssistantTextMarkdown);
      if (blocks.length !== assistantTextBlocks(binding.message).length) throw new Error("assistant Markdown block sequence mismatch");
      for (const [blockIndex, component] of blocks.entries()) {
        const irreversible = reversibleMethod(component, "render");
        if (irreversible) throw new Error(`assistant Markdown render cannot be wrapped reversibly: ${irreversible}`);
        const original = component.render;
        const wrapper = (availableWidth: number) => this.session.withRenderBlock(binding.message, blockIndex,
          () => original.call(component, availableWidth));
        overrides.push({ component, original, wrapper, hadOwnMethod: Object.prototype.hasOwnProperty.call(component, "render") });
        component.render = wrapper;
        if (component.render !== wrapper) throw new Error("assistant Markdown render override did not commit");
      }
    } catch {
      restore();
      // A drifting host tree cannot assign identical sources by invocation order.
      return this.session.withRenderBlock(binding.message, -1, () => binding.original.call(binding.component, width));
    }
    try { return binding.original.call(binding.component, width); }
    finally { restore(); }
  }

  private bindTool(component: ToolComponent, id: string, completedWithoutImages: boolean): string | undefined {
    const existing = this.tools.get(component);
    if (existing) {
      existing.completedWithoutImages = completedWithoutImages;
      // Promote a newly claimed image's observed choice, or retire a text result.
      // Other pending choices keep their explicit-choice FIFO order across renders.
      if (completedWithoutImages || this.retainedImageCalls.has(id)) this.rememberBindingPreference(existing);
      return undefined;
    }
    const irreversible = reversibleMethod(component, "setShowImages");
    if (irreversible) return `tool component "setShowImages" cannot be wrapped reversibly: ${irreversible}`;
    const original = component.setShowImages;
    const binding: ToolBinding = {
      component,
      toolCallId: id,
      original,
      wrapper: original,
      hadOwnMethod: Object.prototype.hasOwnProperty.call(component, "setShowImages"),
      active: true,
      suppressed: false,
      completedWithoutImages,
      externalDesired: this.rememberedPreferences.get(id),
    };
    binding.wrapper = (show) => {
      if (!binding.active) return binding.original.call(component, show);
      const changed = binding.externalDesired !== show;
      binding.externalDesired = show;
      this.rememberBindingPreference(binding);
      binding.original.call(component, binding.suppressed ? false : show);
      if (changed) {
        this.preferenceRevision++;
        this.ownershipChanged();
      }
    };
    component.setShowImages = binding.wrapper;
    this.tools.set(component, binding);
    return undefined;
  }

  /**
   * Suppress the native bitmap and verify the postcondition before authorizing the custom
   * owner: a no-op or throwing setter must never yield a duplicated or silently stacked display.
   */
  private suppress(component: ToolComponent, width: number): string | undefined {
    const binding = this.tools.get(component);
    if (!binding || binding.suppressed || binding.externalDesired !== true) return undefined;
    binding.suppressed = true;
    try {
      binding.original.call(component, false);
    } catch (error) {
      return `setShowImages(false) failed: ${errorMessage(error)}`;
    }
    try {
      if (containsNativeBitmap(component.render(width))) {
        return "setShowImages(false) did not remove the native bitmap; refusing to authorize the custom owner on top of it";
      }
    } catch (error) {
      return `tool row render failed after setShowImages(false): ${errorMessage(error)}`;
    }
    return undefined;
  }

  private release(component: ToolComponent): string | undefined {
    const binding = this.tools.get(component);
    if (!binding || !binding.suppressed) return undefined;
    binding.suppressed = false;
    if (binding.externalDesired === undefined) return undefined;
    try {
      binding.original.call(component, binding.externalDesired);
    } catch (error) {
      return `setShowImages(${binding.externalDesired}) failed while restoring native display: ${errorMessage(error)}`;
    }
    return undefined;
  }

  /** Restore every binding independently: one broken row must not block the others. */
  private restoreAllBindings(): number {
    let unrestored = 0;
    for (const binding of this.tools.values()) {
      try { this.restoreTool(binding); } catch { unrestored++; }
    }
    for (const binding of this.assistants.values()) {
      try { this.restoreAssistant(binding); } catch { unrestored++; }
    }
    return unrestored;
  }

  private fail(reason: string): false {
    const unrestored = this.restoreAllBindings();
    this.tools.clear();
    this.assistants.clear();
    const detail = `${reason} [Pi ${this.host.version}]`;
    this.publish({
      version: READ_PREVIEW_COORDINATION_VERSION,
      ready: false,
      activeLogicalIds: [],
      reason: unrestored ? `${detail}; ${unrestored} bound component(s) could not be restored` : detail,
    });
    return false;
  }

  private releaseDetached(assistants: ReadonlySet<AssistantComponent>, tools: ReadonlySet<ToolComponent>): void {
    // Detached rows are no longer displayed; restoration here is best-effort per binding.
    for (const [component, binding] of this.assistants) if (!assistants.has(component)) {
      try { this.restoreAssistant(binding); } catch { /* keep releasing the rest */ }
      this.assistants.delete(component);
    }
    for (const [component, binding] of this.tools) if (!tools.has(component)) {
      try { this.restoreTool(binding); } catch { /* keep releasing the rest */ }
      this.tools.delete(component);
    }
  }

  private restoreAssistant(binding: AssistantBinding): void {
    binding.active = false;
    if (binding.component.render !== binding.wrapper) return; // another owner replaced it; do not stomp
    if (binding.hadOwnMethod) binding.component.render = binding.original;
    else delete (binding.component as Partial<AssistantComponent>).render; // reveal the inherited method again
  }

  private restoreTool(binding: ToolBinding): void {
    if (binding.suppressed && binding.externalDesired !== undefined) binding.original.call(binding.component, binding.externalDesired);
    binding.suppressed = false;
    binding.active = false;
    if (binding.component.setShowImages !== binding.wrapper) return; // another owner replaced it; do not stomp
    if (binding.hadOwnMethod) binding.component.setShowImages = binding.original;
    else delete (binding.component as Partial<ToolComponent>).setShowImages; // reveal the inherited method again
  }
}
