import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { getKeybindings } from "@earendil-works/pi-tui";
import { installSuspendListener } from "./guard.mjs";

export { classifyCurrentProcessGroup, classifyProcessGroup, installSuspendListener, parseProcStat, suspendGuardInput } from "./guard.mjs";

export default function piSuspendGuard(pi: ExtensionAPI): void {
  let unsubscribe: (() => void) | undefined;

  pi.on("session_start", (_event, ctx) => {
    unsubscribe?.();
    unsubscribe = undefined;
    if (ctx.mode !== "tui") return;
    unsubscribe = installSuspendListener(ctx.ui, getKeybindings());
  });

  pi.on("session_shutdown", () => {
    unsubscribe?.();
    unsubscribe = undefined;
  });
}
