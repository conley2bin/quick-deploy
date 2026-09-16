import type { MarkdownTransformContext } from "@earendil-works/pi-coding-agent";
import {
  getOsc8LinkAtColumn, hyperlink, Markdown, TuiAltScreen, visibleWidth,
  type MarkdownTheme, type TUI,
} from "@earendil-works/pi-tui";
import { webUrl } from "./browser.ts";
import { codeBlocks, CopyStore, type CopyEntry } from "./source.ts";

// Pi has no public code-block renderer or pre-viewport mouse hook in 0.85.1.
// Keep the exact private surface here, reject unknown versions and restore on unload.
export const SUPPORTED_PI = "0.85.1";
interface Token { type: string; text?: string; lang?: string; href?: string; tokens?: Token[]; raw?: string }
interface InlineStyle { applyText: (text: string) => string; stylePrefix: string }
interface MarkdownRuntime {
  theme: MarkdownTheme;
  render(width: number): string[];
  renderToken(token: Token, width: number, next?: string, style?: InlineStyle): string[];
  renderInlineTokens(tokens: Token[], style?: InlineStyle): string;
}
interface FullscreenRuntime extends TUI {
  previousScreen: string[];
  previousScreenWidth: number;
  previousScreenHeight: number;
  openUrl?: (url: string) => void;
  handleViewportInput(data: string): { consume?: boolean; data?: string } | undefined;
}
interface Frame { owner: MarkdownRuntime; blocks?: ReturnType<typeof codeBlocks>; entries?: CopyEntry[]; used: Set<number>; streaming?: boolean }
interface Press { url: string; x: number; y: number; line: string; width: number; height: number; moved: boolean }
export interface AdapterOptions {
  version: string;
  active: () => boolean;
  copy: (text: string) => Promise<void>;
  open: (url: string) => Promise<void>;
  notify: (message: string, error?: boolean) => void;
  button: (text: string) => string;
}

export function installAdapter(options: AdapterOptions) {
  const md = Markdown.prototype as unknown as MarkdownRuntime;
  const alt = TuiAltScreen.prototype as unknown as FullscreenRuntime;
  if (options.version !== SUPPORTED_PI || typeof md.renderToken !== "function" ||
      typeof md.renderInlineTokens !== "function" || typeof alt.handleViewportInput !== "function") {
    throw new Error(`pi-copy-links 仅适配 Pi ${SUPPORTED_PI}，当前 ${options.version}；未修改渲染器`);
  }
  const original = { render: md.render, token: md.renderToken, inline: md.renderInlineTokens, input: alt.handleViewportInput };
  const store = new CopyStore();
  const frames: Frame[] = [];
  const presses = new WeakMap<object, Press>();
  let enabled = true;
  let warned = false;
  let copyQueue = Promise.resolve();
  const isActive = () => enabled && options.active();
  const currentFrame = (owner: MarkdownRuntime) => {
    const frame = frames.at(-1);
    return frame?.owner === owner && frame.blocks ? frame : undefined;
  };

  function render(this: MarkdownRuntime, width: number): string[] {
    if (!isActive()) return original.render.call(this, width);
    frames.push({ owner: this, used: new Set() });
    try { return original.render.call(this, width); }
    finally { frames.pop(); }
  }

  function transform(source: string, context: MarkdownTransformContext): string {
    const frame = frames.at(-1);
    if (isActive() && frame && context.messageType === "assistant") {
      frame.blocks = codeBlocks(source);
      frame.streaming = context.isStreaming;
      frame.entries = store.set(frame.owner, frame.blocks.map((block) => block.value));
    }
    return source; // Display metadata only; never rewrite a session/model message.
  }

  function token(this: MarkdownRuntime, value: Token, width: number, next?: string, style?: InlineStyle): string[] {
    const lines = original.token.call(this, value, width, next, style);
    const frame = currentFrame(this);
    if (!enabled || value.type !== "code" || !frame) return lines;
    // The native renderer receives tab-expanded values. Match only exact logical
    // values and language; never strip/dedent a rendered command to guess its source.
    const index = frame.blocks!.findIndex((block, i) => !frame.used.has(i) &&
      block.value.replace(/\t/gu, "   ") === value.text &&
      [block.lang, block.meta].filter(Boolean).join(" ") === (value.lang ?? ""));
    if (index < 0) {
      if (!warned && !frame.streaming) {
        warned = true;
        queueMicrotask(() => { if (enabled) options.notify("有代码块的源文与 Pi 显示解析不一致，未给该块启用复制；请保留原文并报告此例", true); });
      }
      return lines;
    }
    frame.used.add(index);
    const entry = frame.entries![index]!;
    const label = width >= 6 ? "[复制]" : width >= 4 ? "Copy" : "C";
    const button = hyperlink(options.button(label), entry.url);
    const footer = next && next !== "space" ? lines.length - 2 : lines.length - 1;
    if (footer < 0) return lines;
    const border = width >= visibleWidth(label) + 4 ? this.theme.codeBlockBorder("```") : "";
    lines[footer] = border + " ".repeat(Math.max(0, width - visibleWidth(border) - visibleWidth(label))) + button;
    return lines;
  }

  function inline(this: MarkdownRuntime, tokens: Token[], style?: InlineStyle): string {
    if (!enabled || !currentFrame(this)) return original.inline.call(this, tokens, style);
    // Fullscreen's hit-testing needs OSC 8 even inside tmux, where capability
    // detection may turn terminal-owned hyperlinks off. Do not change global caps.
    const linked = tokens.map((value) => {
      const href = value.type === "link" && value.href ? webUrl(value.href) : undefined;
      if (!href) return value;
      const text = this.renderInlineTokens(value.tokens ?? [], style);
      return { type: "html", raw: hyperlink(this.theme.link(this.theme.underline(text)), href) };
    });
    return original.inline.call(this, linked, style);
  }

  function invoke(url: string): void {
    if (store.owns(url)) {
      const entry = store.get(url);
      if (!entry) { options.notify("代码块已失效，请刷新显示后重试", true); return; }
      // Serialize writes: two deliberately clicked blocks must leave the second
      // block in the clipboard even if a platform clipboard process is slow.
      copyQueue = copyQueue.then(async () => {
        if (!enabled) return;
        await options.copy(entry.text);
        if (enabled) options.notify("代码已复制");
      }).catch((error: unknown) => {
        if (enabled) options.notify(`复制失败：${String(error)}`, true);
      });
    } else {
      const target = webUrl(url);
      if (!target) { options.notify("只打开 HTTP(S) 网页链接", true); return; }
      void options.open(target).catch((error: unknown) => {
        if (enabled) options.notify(`打开链接失败：${String(error)}`, true);
      });
    }
  }

  function input(this: FullscreenRuntime, data: string) {
    if (!isActive()) return original.input.call(this, data);
    const event = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/u.exec(data);
    const pending = presses.get(this);
    if (!event) {
      presses.delete(this);
      return original.input.call(this, data);
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
    const overlay = this.hasOverlay();
    const valid = !overlay && this.previousScreenWidth === width && this.previousScreenHeight === height &&
      x >= 0 && x < width && y >= 0 && y < height && Array.isArray(this.previousScreen);
    const line = valid ? this.previousScreen[y] ?? "" : "";
    const url = getOsc8LinkAtColumn(line, x);
    if (pending && !wheel) {
      if (motion || x !== pending.x || y !== pending.y) pending.moved = true;
      if (release) {
        presses.delete(this);
        if (primary && !pending.moved && valid && pending.url === url && pending.line === line &&
            pending.width === width && pending.height === height) invoke(pending.url);
      }
      return { consume: true };
    }
    if (wheel) presses.delete(this);
    if (!wheel && !motion && !release && primary && valid && url && (bits & 12) === 0 &&
        (store.owns(url) || ((bits & 16) !== 0 && webUrl(url)))) {
      presses.set(this, { url, x, y, line, width, height, moved: false });
      return { consume: true };
    }
    // Keep native selection, scrolling and component dispatch. Native fullscreen
    // otherwise opens links on an unmodified click; our contract is Ctrl+click.
    // Also ensure a stale internal copy URI can never reach the OS URI handler.
    const opener = this.openUrl;
    if (!overlay) this.openUrl = undefined;
    try { return original.input.call(this, data); }
    finally { if (!overlay) this.openUrl = opener; }
  }

  md.render = render;
  md.renderToken = token;
  md.renderInlineTokens = inline;
  alt.handleViewportInput = input;
  return {
    transform,
    dispose() {
      enabled = false;
      store.clear();
      // Another extension may have wrapped us subsequently. Never overwrite it;
      // a retained wrapper is now inert and delegates to its captured predecessor.
      if (md.render === render) md.render = original.render;
      if (md.renderToken === token) md.renderToken = original.token;
      if (md.renderInlineTokens === inline) md.renderInlineTokens = original.inline;
      if (alt.handleViewportInput === input) alt.handleViewportInput = original.input;
    },
  };
}
