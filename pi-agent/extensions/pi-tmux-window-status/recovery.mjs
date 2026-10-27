// One recovery owner for root Pi sessions, independent of tmux and UI mode.
export const ERROR_STALL_MS = 600_000;
export const AUTO_CONTINUE_MIN_INTERVAL_MS = 600_000;
export const AUTO_CONTINUE_MAX = 5;
// Either terminal listener may consume the pane sentinel first. Broadcast the
// cancellation so both the recovery deadline and tmux's red lease are cleared.
export const RECOVERY_CANCEL_EVENT = "pi-tmux-window-status:cancel";
export const CANCEL_SENTINEL = "\x1b[997;1~";
const RETRY_EXHAUSTED_ABORT = /^aborted after \d+ retry attempts?$/i;

export function isManualEscapePress(data) {
  if (data === "\x1b") return true;
  const kitty = data.match(/^\x1b\[27(?::(\d*))?(?::(\d+))?(?:;(\d+))?(?::(\d+))?u$/);
  if (kitty) {
    const modifier = (Number.parseInt(kitty[3] || "1", 10) - 1) & ~(64 | 128);
    return modifier === 0 && Number.parseInt(kitty[4] || "1", 10) !== 3;
  }
  return data.match(/^\x1b\[27;(\d+);27~$/)?.[1] === "1";
}

export function recoveryTerminalInput(data, cancel) {
  if (data === CANCEL_SENTINEL) { cancel(); return { consume: true }; }
  if (isManualEscapePress(data)) cancel();
  return undefined;
}

export function isAssistantError(message) {
  return message?.role === "assistant" && message.stopReason === "error";
}

export function isRetryExhaustedAbort(message) {
  return message?.role === "assistant" && message.stopReason === "aborted"
    && typeof message.errorMessage === "string" && RETRY_EXHAUSTED_ABORT.test(message.errorMessage.trim());
}

export function semanticAssistantOutput(message) {
  const msg = message?.message || message;
  if (msg?.role !== "assistant") return false;
  const nonempty = (value) => typeof value === "string" && value.trim().length > 0;
  const semanticPart = (part) => part && (nonempty(part.text) || nonempty(part.thinking)
    || ["toolCall", "tool_call", "tool_use"].includes(part.type));
  return nonempty(msg.text) || nonempty(msg.thinking)
    || (Array.isArray(msg.content) && msg.content.some(semanticPart))
    || (Array.isArray(msg.delta?.content) && msg.delta.content.some(semanticPart));
}

export function installErrorRecovery(pi) {
  // Child failures belong to their supervisor, as in the previous recovery policy.
  if (Number(process.env.PI_SUBAGENT_DEPTH || 0) > 0) return;
  let timer, lastCtx, activeRunSignal, errorAt, terminalUnsubscribe, cancelUnsubscribe;
  let sessionActive = false, settled = false, sends = 0;
  let lastSentAt = -Infinity, inputNotBefore = -Infinity;
  let cancelledRunSignals = new WeakSet(), seenErrors = new WeakSet();

  const cancelTimer = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const disarm = () => { cancelTimer(); errorAt = undefined; };
  const signalFor = (ctx) => ctx.signal || activeRunSignal;
  const fromCancelledRun = (ctx) => {
    const signal = signalFor(ctx);
    return signal && cancelledRunSignals.has(signal);
  };
  const cancel = (signal = activeRunSignal) => {
    if (signal) cancelledRunSignals.add(signal);
    disarm();
  };
  const manualCancel = () => {
    cancel();
    pi.events.emit(RECOVERY_CANCEL_EVENT, { signal: activeRunSignal });
  };
  const eligible = () => sessionActive && settled && errorAt !== undefined && sends < AUTO_CONTINUE_MAX;
  const schedule = (notBefore = -Infinity) => {
    cancelTimer();
    if (!eligible()) return;
    const deadline = Math.max(errorAt + ERROR_STALL_MS, lastSentAt + AUTO_CONTINUE_MIN_INTERVAL_MS, inputNotBefore, notBefore);
    timer = setTimeout(tryContinue, Math.max(0, deadline - Date.now()));
  };
  const tryContinue = () => {
    timer = undefined;
    if (!eligible()) return;
    try {
      if (!lastCtx.isIdle() || lastCtx.hasPendingMessages()) {
        // A queued/starting prompt takes priority. A later settlement can
        // reschedule sooner; otherwise recheck without spinning at the deadline.
        schedule(Date.now() + ERROR_STALL_MS);
        return;
      }
    } catch (error) {
      disarm();
      console.error("pi-tmux-window-status: recovery context expired; cancelled pending continue", error);
      return;
    }
    lastSentAt = Date.now();
    sends += 1;
    // sendUserMessage is void and an input hook may consume it without starting
    // a run. Retain one bounded watchdog; agent_start/progress/cancel removes it.
    schedule();
    try { pi.sendUserMessage("continue"); }
    catch (error) { console.error("pi-tmux-window-status: could not send continue", error); }
  };
  const shutdown = () => {
    sessionActive = false;
    settled = false;
    disarm();
    terminalUnsubscribe?.();
    cancelUnsubscribe?.();
    terminalUnsubscribe = cancelUnsubscribe = lastCtx = activeRunSignal = undefined;
  };

  pi.on("session_start", (_event, ctx) => {
    shutdown();
    sessionActive = true;
    sends = 0;
    lastSentAt = inputNotBefore = -Infinity;
    cancelledRunSignals = new WeakSet();
    seenErrors = new WeakSet();
    lastCtx = ctx;
    cancelUnsubscribe = pi.events.on(RECOVERY_CANCEL_EVENT, (event) => cancel(event?.signal));
    if (ctx.mode === "tui") terminalUnsubscribe = ctx.ui.onTerminalInput((data) => recoveryTerminalInput(data, manualCancel));
  });
  pi.on("agent_start", (_event, ctx) => {
    if (!sessionActive) return;
    lastCtx = ctx;
    activeRunSignal = ctx.signal;
    settled = false;
    cancelTimer();
  });
  pi.on("agent_settled", (event, ctx) => {
    if (!sessionActive) return;
    lastCtx = ctx;
    settled = true;
    if (event.aborted) cancel(signalFor(ctx));
    else schedule();
  });
  pi.on("input", (_event, ctx) => {
    if (!sessionActive) return;
    lastCtx = ctx;
    inputNotBefore = Date.now() + ERROR_STALL_MS;
    // Give submitted input a full stall window even if another hook consumes it.
    // A real new run will cancel this timer at agent_start.
    schedule();
  });
  pi.on("message_update", (event, ctx) => {
    if (!sessionActive || fromCancelledRun(ctx)) return;
    if (!isAssistantError(event.message) && semanticAssistantOutput(event.message)) disarm();
  });
  pi.on("message_end", (event, ctx) => {
    const message = event.message;
    if (!sessionActive || message?.role !== "assistant" || fromCancelledRun(ctx)) return;
    lastCtx = ctx;
    if (isAssistantError(message)) {
      if (seenErrors.has(message)) return;
      seenErrors.add(message);
      errorAt = Date.now();
      sends = 0;
      schedule();
    } else if (!isRetryExhaustedAbort(message)) {
      if (message.stopReason === "aborted") cancel(signalFor(ctx));
      else { disarm(); sends = 0; }
    }
  });
  pi.on("model_select", () => {
    if (!sessionActive) return;
    disarm();
    sends = 0;
  });
  pi.on("session_shutdown", shutdown);
}
