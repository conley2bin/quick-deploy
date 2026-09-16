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
  | { kind: "assistant"; message: object }
  | { kind: "tool"; toolCallId: string; toolName: string };
type Observed =
  | { kind: "assistant"; component: AssistantComponent }
  | { kind: "tool"; component: ToolComponent };
type AssistantBinding = {
  component: AssistantComponent;
  message: object;
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
  externalDesired: boolean;
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
function expectedComponents(entries: readonly HostSessionEntry[], host: HostAdapterApi): Expected[] {
  const expected: Expected[] = [];
  for (const value of contextMessages(entries, host)) {
    if (!value || typeof value !== "object" || (value as { role?: unknown }).role !== "assistant") continue;
    expected.push({ kind: "assistant", message: value as object });
    const content = (value as { content?: unknown }).content;
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      if (!part || typeof part !== "object" || (part as { type?: unknown }).type !== "toolCall") continue;
      const id = (part as { id?: unknown }).id;
      const name = (part as { name?: unknown }).name;
      if (typeof id !== "string" || !id || typeof name !== "string" || !name) continue;
      expected.push({ kind: "tool", toolCallId: id, toolName: name });
    }
  }
  return expected;
}
function resultImages(entries: readonly HostSessionEntry[], host: HostAdapterApi): Map<string, Set<number>> {
  const results = new Map<string, Set<number>>();
  for (const value of contextMessages(entries, host)) {
    if (!value || typeof value !== "object" || (value as { role?: unknown }).role !== "toolResult") continue;
    const toolCallId = (value as { toolCallId?: unknown }).toolCallId;
    const content = (value as { content?: unknown }).content;
    if (typeof toolCallId !== "string" || !toolCallId || !Array.isArray(content)) continue;
    const indexes = new Set<number>();
    content.forEach((part, index) => {
      if (part && typeof part === "object" && (part as { type?: unknown }).type === "image") indexes.add(index);
    });
    if (indexes.size) results.set(toolCallId, indexes);
  }
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
    const toolCallId = key.slice(5);
    const blocks = claims.get(toolCallId) ?? new Map<number, string>();
    blocks.set(Number(blockIndex), data.logicalId);
    claims.set(toolCallId, blocks);
  }
  return claims;
}
function containsNativeBitmap(lines: readonly string[]): boolean {
  return lines.some((line) => line.includes("\x1b_G") || line.includes("\x1b]1337;File=") || line.includes(PLACEHOLDER));
}

/** Host-0.85.1 adapter over public component identity, traversal, render, and image-toggle methods. */
export class HostImageOwnershipAdapter {
  private tui?: TUI;
  private readonly assistants = new Map<AssistantComponent, AssistantBinding>();
  private readonly tools = new Map<ToolComponent, ToolBinding>();
  private readonly componentIds = new WeakMap<object, number>();
  private nextComponentId = 1;

  constructor(
    private readonly session: ImageSession,
    private readonly publish: (coordination: ReadPreviewCoordination) => void,
    private readonly host: HostAdapterApi,
  ) {}

  setTui(tui: TUI): void { this.tui = tui; }

  publicTreeSignature(): string {
    if (!this.tui) return "unmounted";
    const observed = observedComponents(this.tui);
    return `${this.tui.children.length}|${observed.map(({ kind, component }) => `${kind}:${this.componentId(component)}`).join(",")}`;
  }

  reconcile(renderEntries: readonly HostSessionEntry[], branch: readonly HostSessionEntry[]): boolean {
    if (this.host.version !== SUPPORTED_HOST_VERSION || !this.tui) return this.fail(`unsupported Pi host ${this.host.version}`);
    const expected = expectedComponents(renderEntries, this.host);
    const observed = observedComponents(this.tui);
    if (expected.length !== observed.length || expected.some((item, index) => item.kind !== observed[index]?.kind)) {
      return this.fail(`host component sequence mismatch (${expected.length} expected, ${observed.length} observed)`);
    }
    for (let index = 0; index < expected.length; index++) {
      const wanted = expected[index]!;
      const found = observed[index]!;
      if (wanted.kind === "assistant" && found.kind === "assistant") {
        const prior = this.assistants.get(found.component);
        if (prior && prior.message !== wanted.message) return this.fail("assistant component identity changed branch binding");
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
        this.bindAssistant(found.component, wanted.message);
      } else if (wanted.kind === "tool" && found.kind === "tool") {
        currentTools.add(found.component);
        toolRows.set(wanted.toolCallId, found.component);
        this.bindTool(found.component, wanted.toolCallId);
      }
    }
    this.releaseDetached(currentAssistants, currentTools);

    const results = resultImages(renderEntries, this.host);
    const claims = activeClaims(branch);
    const accepted = new Set<string>();
    for (const [toolCallId, row] of toolRows) {
      const result = results.get(toolCallId);
      const claimed = claims.get(toolCallId);
      const complete = Boolean(result?.size && claimed && result.size === claimed.size
        && [...result].every((blockIndex) => claimed.has(blockIndex)));
      if (complete) {
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

  private bindAssistant(component: AssistantComponent, message: object): void {
    if (this.assistants.has(component)) return;
    const original = component.render;
    const binding: AssistantBinding = { component, message, original, wrapper: original, active: true };
    binding.wrapper = (width) => binding.active
      ? this.session.withRenderMessage(binding.message, () => binding.original.call(component, width))
      : binding.original.call(component, width);
    component.render = binding.wrapper;
    this.assistants.set(component, binding);
  }

  private bindTool(component: ToolComponent, toolCallId: string): void {
    if (this.tools.has(component)) return;
    const original = component.setShowImages;
    const width = Math.max(1, this.tui?.terminal.columns ?? 80);
    const binding: ToolBinding = {
      component,
      toolCallId,
      original,
      wrapper: original,
      active: true,
      suppressed: false,
      externalDesired: containsNativeBitmap(component.render(width)),
    };
    binding.wrapper = (show) => {
      if (!binding.active) return binding.original.call(component, show);
      binding.externalDesired = show;
      return binding.original.call(component, binding.suppressed ? false : show);
    };
    component.setShowImages = binding.wrapper;
    this.tools.set(component, binding);
  }

  private suppress(component: ToolComponent): void {
    const binding = this.tools.get(component);
    if (!binding || binding.suppressed) return;
    binding.suppressed = true;
    binding.original.call(component, false);
  }

  private release(component: ToolComponent): void {
    const binding = this.tools.get(component);
    if (!binding || !binding.suppressed) return;
    binding.suppressed = false;
    binding.original.call(component, binding.externalDesired);
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
    if (binding.suppressed) binding.original.call(binding.component, binding.externalDesired);
    binding.suppressed = false;
    binding.active = false;
    if (binding.component.setShowImages === binding.wrapper) binding.component.setShowImages = binding.original;
  }
}
