import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { allocateImageId, getCellDimensions } from "@earendil-works/pi-tui";
import { transformMarkdown } from "./src/markdown.ts";
import { assistantTextBlocks, ImageSession } from "./src/session.ts";
import { TerminalImages } from "./src/terminal.ts";

export default function piInlineImages(pi: ExtensionAPI) {
  const terminal = new TerminalImages(allocateImageId, getCellDimensions);
  const session = new ImageSession(terminal);

  pi.registerMarkdownTransformer((markdown, context) => {
    if (context.messageType !== "assistant" || context.isStreaming) return markdown;
    const prepared = session.markdown.get(markdown);
    return prepared ? transformMarkdown(prepared, context.availableWidth, terminal) : markdown;
  });

  pi.on("message_end", async (event, context) => {
    if (context.mode !== "tui") return;
    for (const source of assistantTextBlocks(event.message as never)) await session.prepare(source, context.cwd);
  });

  const restore = async (_event: unknown, context: { mode: string; cwd: string; sessionManager: { getBranch(): readonly never[] } }) => {
    if (context.mode === "tui") await session.restore(context.sessionManager.getBranch(), context.cwd);
    else await session.reset();
  };
  pi.on("session_start", restore as never);
  pi.on("session_tree", (async (_event: unknown, context: { mode: string; cwd: string; sessionManager: { getBranch(): readonly never[] } }) => {
    if (context.mode === "tui") await session.restore(context.sessionManager.getBranch(), context.cwd, true);
    else await session.reset();
  }) as never);
  pi.on("session_shutdown", async () => session.reset(true));
}
