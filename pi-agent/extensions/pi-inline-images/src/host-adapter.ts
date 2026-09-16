import { createHash } from "node:crypto";
import type { Component, TUI } from "@earendil-works/pi-tui";
import type { ImageSession } from "./session.ts";

export const READ_PREVIEW_ENTRIES_CHANGED = "pi-inline-images:read-preview-entries-changed";
export const READ_PREVIEW_COORDINATION = "pi-inline-images:read-preview-coordination";
export const READ_PREVIEW_COORDINATION_VERSION = 1;
const SUPPORTED_HOST_VERSION = "0.85.1";
const ENTRY_TYPE = "pi-tmux-images.preview";
const CLEAR_TYPE = "pi-tmux-images.clear";
const PLACEHOLDER = "\u{10EEEE}";

export type HostSessionEntry = {
  type: string;
  customType?: string;
  data?: unknown;
  [key: string]: unknown;
};
export type HostAdapterApi = {
  version: string;
  sessionEntryToContextMessages(entry: HostSessionEntry): unknown[];
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
  | { kind: "tool"; component: ToolComponent };
type AssistantBinding = {
  component: AssistantComponent;
  message: object;
  fingerprint: string;
  live: boolean;
  original: AssistantComponent["render"];
  wrapper: AssistantComponent["render"];
  active: boolean;
};
type ToolBinding = {
  component: ToolComponent;
  toolCallId: string;
  original: ToolComponent["setShowImages"];
  wrapper: ToolComponent["setShowImages"];
  active: boolean;
  suppressed: boolean;
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
function observedComponents(root: TUI): Observed[] {
  const observed: Observed[] = [];
  const seen = new Set<object>();
  const visit = (value: unknown) => {
    if (!value || typeof value !== "object" || seen.has(value as object)) return;
    seen.add(value as object);
    if (isAssistantComponent(value)) observed.push({ kind: "assistant", component: value });
    else if (isToolComponent(value)) observed.push({ kind: "tool", component: value });
    const children = (value as { children?: unknown }).children;
    if (Array.isArray(children)) for (const child of children) visit(child);
  };
  visit(root);
  return observed;
}
function contextMessages(entries: readonly HostSessionEntry[], host: HostAdapterApi): unknown[] {
  return entries.flatMap((entry) => entry.type === "custom" ? [] : host.sessionEntryToContextMessages(entry));
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

/** Host-0.85.1 adapter over public component identity, traversal, render, and image-toggle methods. */
export class HostImageOwnershipAdapter {
  private tui?: TUI;
  private readonly assistants = new Map<AssistantComponent, AssistantBinding>();
  private readonly tools = new Map<ToolComponent, ToolBinding>();
  private readonly componentIds = new WeakMap<object, number>();
  private nextComponentId = 1;
  private liveAssistant?: { message: object; fingerprint: string; baseline: number };
  private readonly persistedAssistantCounts = new Map<string, number>();
  private readonly liveResults = new Map<string, object>();
  private preferenceRevision = 0;

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

  reconciliationSignature(renderEntries: readonly HostSessionEntry[], branch: readonly HostSessionEntry[]): string {
    const tree = this.publicTreeSignature();
    const data = reconciliationDataSignature(branch, this.liveAssistant?.message, this.liveResults);
    return `${tree}|${data}|pref:${this.preferenceRevision}|render:${renderEntries.length}`;
  }

  publicTreeSignature(): string {
    if (!this.tui) return "unmounted";
    const observed = observedComponents(this.tui);
    return `${this.tui.children.length}|${observed.map(({ kind, component }) => `${kind}:${this.componentId(component)}`).join(",")}`;
  }

  reconcile(renderEntries: readonly HostSessionEntry[], branch: readonly HostSessionEntry[]): boolean {
    if (this.host.version !== SUPPORTED_HOST_VERSION || !this.tui) return this.fail(`unsupported Pi host ${this.host.version}`);
    const messages = contextMessages(renderEntries, this.host);
    const assistantCounts = new Map<string, number>();
    for (const message of messages) if (role(message) === "assistant") {
      const fingerprint = assistantFingerprint(message as object);
      assistantCounts.set(fingerprint, (assistantCounts.get(fingerprint) ?? 0) + 1);
    }
    if (this.liveAssistant && (assistantCounts.get(this.liveAssistant.fingerprint) ?? 0) > this.liveAssistant.baseline) this.liveAssistant = undefined;
    this.persistedAssistantCounts.clear();
    for (const [fingerprint, count] of assistantCounts) this.persistedAssistantCounts.set(fingerprint, count);
    const persistedResults = new Set(messages.map(toolCallId).filter((id): id is string => Boolean(id)));
    for (const id of persistedResults) this.liveResults.delete(id);

    const expected = expectedComponents(messages, this.liveAssistant?.message);
    const observed = observedComponents(this.tui);
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
        this.bindAssistant(found.component, wanted);
      } else if (wanted.kind === "tool" && found.kind === "tool") {
        currentTools.add(found.component);
        toolRows.set(wanted.toolCallId, found.component);
        this.bindTool(found.component, wanted.toolCallId);
      }
    }
    this.releaseDetached(currentAssistants, currentTools);

    const results = resultImages(messages, this.liveResults);
    const claims = activeClaims(branch);
    const accepted = new Set<string>();
    for (const [id, row] of toolRows) {
      const result = results.get(id);
      const claimed = claims.get(id);
      const complete = Boolean(result?.size && claimed && result.size === claimed.size
        && [...result].every((blockIndex) => claimed.has(blockIndex)));
      const binding = this.tools.get(row)!;
      if (complete && binding.externalDesired === undefined && containsNativeBitmap(row.render(Math.max(1, this.tui.terminal.columns)))) {
        binding.externalDesired = true;
      }
      if (complete && binding.externalDesired === true) {
        this.suppress(row);
        for (const logicalId of claimed!.values()) accepted.add(logicalId);
      } else this.release(row);
    }
    this.publish({ version: READ_PREVIEW_COORDINATION_VERSION, ready: true, activeLogicalIds: [...accepted].sort() });
    return true;
  }

  dispose(): void {
    for (const binding of this.tools.values()) this.restoreTool(binding);
    for (const binding of this.assistants.values()) this.restoreAssistant(binding);
    this.tools.clear();
    this.assistants.clear();
    this.liveResults.clear();
    this.liveAssistant = undefined;
    this.persistedAssistantCounts.clear();
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

  private bindAssistant(component: AssistantComponent, expected: Extract<Expected, { kind: "assistant" }>): void {
    const prior = this.assistants.get(component);
    if (prior) {
      prior.message = expected.message;
      prior.fingerprint = expected.fingerprint;
      prior.live = expected.live;
      return;
    }
    const original = component.render;
    const binding: AssistantBinding = {
      component,
      message: expected.message,
      fingerprint: expected.fingerprint,
      live: expected.live,
      original,
      wrapper: original,
      active: true,
    };
    binding.wrapper = (width) => binding.active
      ? this.session.withRenderMessage(binding.message, () => binding.original.call(component, width))
      : binding.original.call(component, width);
    component.render = binding.wrapper;
    this.assistants.set(component, binding);
  }

  private bindTool(component: ToolComponent, id: string): void {
    if (this.tools.has(component)) return;
    const original = component.setShowImages;
    const binding: ToolBinding = {
      component,
      toolCallId: id,
      original,
      wrapper: original,
      active: true,
      suppressed: false,
      externalDesired: undefined,
    };
    binding.wrapper = (show) => {
      if (!binding.active) return binding.original.call(component, show);
      const changed = binding.externalDesired !== show;
      binding.externalDesired = show;
      binding.original.call(component, binding.suppressed ? false : show);
      if (changed) {
        this.preferenceRevision++;
        this.ownershipChanged();
      }
    };
    component.setShowImages = binding.wrapper;
    this.tools.set(component, binding);
  }

  private suppress(component: ToolComponent): void {
    const binding = this.tools.get(component);
    if (!binding || binding.suppressed || binding.externalDesired !== true) return;
    binding.suppressed = true;
    binding.original.call(component, false);
  }

  private release(component: ToolComponent): void {
    const binding = this.tools.get(component);
    if (!binding || !binding.suppressed) return;
    binding.suppressed = false;
    if (binding.externalDesired !== undefined) binding.original.call(component, binding.externalDesired);
  }

  private fail(reason: string): false {
    for (const binding of this.tools.values()) this.restoreTool(binding);
    for (const binding of this.assistants.values()) this.restoreAssistant(binding);
    this.tools.clear();
    this.assistants.clear();
    this.publish({ version: READ_PREVIEW_COORDINATION_VERSION, ready: false, activeLogicalIds: [], reason });
    return false;
  }

  private releaseDetached(assistants: ReadonlySet<AssistantComponent>, tools: ReadonlySet<ToolComponent>): void {
    for (const [component, binding] of this.assistants) if (!assistants.has(component)) {
      this.restoreAssistant(binding);
      this.assistants.delete(component);
    }
    for (const [component, binding] of this.tools) if (!tools.has(component)) {
      this.restoreTool(binding);
      this.tools.delete(component);
    }
  }

  private restoreAssistant(binding: AssistantBinding): void {
    binding.active = false;
    if (binding.component.render === binding.wrapper) binding.component.render = binding.original;
  }

  private restoreTool(binding: ToolBinding): void {
    if (binding.suppressed && binding.externalDesired !== undefined) binding.original.call(binding.component, binding.externalDesired);
    binding.suppressed = false;
    binding.active = false;
    if (binding.component.setShowImages === binding.wrapper) binding.component.setShowImages = binding.original;
  }
}
