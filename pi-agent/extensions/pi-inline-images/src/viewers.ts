import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

export const VIEWER_POLL_MS = 1_500;
export const MAX_TMUX_OUTPUT_BYTES = 64 * 1024;
export const MAX_TMUX_CLIENTS = 32;
export const TMUX_SNAPSHOT_TIMEOUT_MS = 1_000;

export type ViewerState = {
  ready: boolean;
  /** Changes when authoritative attachment, visibility, or eligibility changes. */
  epoch: string;
  reason: string;
  /** Undefined means the snapshot failed and must not prune previously served identities. */
  attached?: readonly string[];
  /** Compatible terminal identities that can receive pane output in this snapshot. */
  receivers: readonly string[];
};

export interface ViewerProbe {
  snapshot(): ViewerState;
}

export interface ViewerScheduler {
  setTimeout(callback: () => void, delayMs: number): unknown;
  clearTimeout(handle: unknown): void;
}

const defaultViewerScheduler: ViewerScheduler = {
  setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimeout: (handle) => clearTimeout(handle as NodeJS.Timeout),
};

export type TmuxSnapshotResult = { status: number | null; stdout?: string | null; error?: Error };
export type TmuxSnapshotRun = (
  args: string[],
  options: { encoding: "utf8"; timeout: number; maxBuffer: number },
) => TmuxSnapshotResult;

function compatible(term: string): boolean {
  return /(?:kitty|ghostty|wezterm)/iu.test(term);
}

function fingerprint(parts: readonly string[]): string {
  return createHash("sha256").update(parts.join("\u0000")).digest("hex").slice(0, 24);
}

function state(
  ready: boolean,
  label: string,
  reason: string,
  attached: readonly string[] | undefined,
  receivers: readonly string[] = [],
): ViewerState {
  const identity = attached === undefined ? "unknown" : fingerprint([...attached, "|", ...receivers]);
  return { ready, epoch: `${label}:${identity}`, reason, attached, receivers };
}

function directViewer(env: NodeJS.ProcessEnv): ViewerState {
  const term = `${env.TERM_PROGRAM ?? ""} ${env.TERM ?? ""}`.trim();
  if (!compatible(term)) return state(false, "direct:none", "Kitty-compatible terminal unavailable", []);
  const identity = `direct:${term.slice(0, 256)}`;
  return state(true, "direct:ready", "", [identity], [identity]);
}

function boundedOutput(result: TmuxSnapshotResult): string | undefined {
  if (result.error || result.status !== 0 || typeof result.stdout !== "string") return undefined;
  return Buffer.byteLength(result.stdout, "utf8") <= MAX_TMUX_OUTPUT_BYTES ? result.stdout : undefined;
}

/**
 * Query the pane, zoom state, and every attached tmux client. Unzoomed panes
 * remain visible even when inactive; a zoomed window exposes only its active pane.
 */
export function currentViewerState(
  env: NodeJS.ProcessEnv = process.env,
  execute: TmuxSnapshotRun = (args, options) => spawnSync("tmux", args, options),
): ViewerState {
  if (!(env.TMUX || env.TERM?.startsWith("tmux"))) return directViewer(env);
  const pane = env.TMUX_PANE?.trim();
  if (!pane || !/^%\d+$/u.test(pane)) return state(false, "tmux:pane", "tmux pane identity unavailable", undefined);
  const run = (args: string[]) => execute(args, {
    encoding: "utf8",
    timeout: TMUX_SNAPSHOT_TIMEOUT_MS,
    maxBuffer: MAX_TMUX_OUTPUT_BYTES,
  });

  try {
    const policy = boundedOutput(run(["show-options", "-Apv", "-t", pane, "allow-passthrough"]));
    if (policy === undefined) return state(false, "tmux:policy-error", "tmux passthrough snapshot failed", undefined);
    if (!/^(on|all|yes|true|1)$/iu.test(policy.trim())) {
      return state(false, "tmux:policy", "tmux passthrough is disabled", undefined);
    }

    const paneOutput = boundedOutput(run(["display-message", "-p", "-t", pane, "#{window_id}\t#{pane_active}\t#{window_zoomed_flag}"]));
    const paneFields = paneOutput?.trim().split("\t");
    if (!paneFields || paneFields.length !== 3 || !/^@\d+$/u.test(paneFields[0] ?? "")
      || !/^[01]$/u.test(paneFields[1] ?? "") || !/^[01]$/u.test(paneFields[2] ?? "")) {
      return state(false, "tmux:pane-snapshot", "tmux pane visibility snapshot failed", undefined);
    }
    const [windowId, paneActive, zoomed] = paneFields as [string, "0" | "1", "0" | "1"];

    const clientOutput = boundedOutput(run(["list-clients", "-F", "#{client_pid}\t#{client_created}\t#{client_tty}\t#{client_termname}\t#{client_session}\t#{window_id}\t#{client_control_mode}\t#{client_flags}"]));
    if (clientOutput === undefined) return state(false, "tmux:clients", "tmux client snapshot failed", undefined);
    const normalized = clientOutput.endsWith("\n") ? clientOutput.slice(0, -1) : clientOutput;
    const lines = normalized ? normalized.split("\n") : [];
    if (lines.length > MAX_TMUX_CLIENTS) {
      return state(false, `tmux:${windowId}:clients-limit`, `tmux client snapshot exceeds ${MAX_TMUX_CLIENTS} clients`, undefined);
    }

    const clients: Array<{ identity: string; term: string; window: string; suspended: boolean; control: boolean }> = [];
    for (const line of lines) {
      const fields = line.split("\t");
      if (fields.length !== 8) return state(false, `tmux:${windowId}:client-row`, "tmux client snapshot is malformed", undefined);
      const [pid, created, tty, term, session, clientWindow, control, flags] = fields;
      if (!/^\d+$/u.test(pid!) || !/^\d+$/u.test(created!) || !session || session.length > 512
        || !/^@\d+$/u.test(clientWindow!) || !/^[01]$/u.test(control!)) {
        return state(false, `tmux:${windowId}:client-row`, "tmux client snapshot is malformed", undefined);
      }
      if (control === "1") continue;
      if (!tty || tty.length > 512 || !term || term.length > 512) {
        return state(false, `tmux:${windowId}:client-row`, "tmux client snapshot is malformed", undefined);
      }
      const identity = `${pid}:${created}:${tty}:${term}`;
      clients.push({
        identity,
        term,
        window: clientWindow!,
        suspended: /(?:^|,)suspended(?:,|$)/iu.test(flags ?? ""),
        control: false,
      });
    }

    const terminalClients = clients.filter((client) => !client.control);
    const attached = terminalClients.map((client) => client.identity).sort();
    const paneVisible = zoomed === "0" || paneActive === "1";
    if (!paneVisible) return state(false, `tmux:${windowId}:pane-hidden`, "pane is hidden by the zoomed window", attached);

    const visible = terminalClients.filter((client) => client.window === windowId);
    if (!visible.length) return state(false, `tmux:${windowId}:no-viewer`, "no client is viewing this window", attached);
    if (visible.some((client) => client.suspended)) {
      return state(false, `tmux:${windowId}:suspended`, "a client viewing this window is suspended", attached);
    }
    if (visible.some((client) => !compatible(client.term))) {
      return state(false, `tmux:${windowId}:mixed`, "an incompatible client is viewing this window", attached);
    }
    const receivers = visible.map((client) => client.identity).sort();
    return state(true, `tmux:${windowId}:ready`, "", attached, receivers);
  } catch {
    return state(false, "tmux:error", "tmux client snapshot failed", undefined);
  }
}

/** One nonoverlapping snapshot poller. Transition work never delays the next snapshot. */
export class ViewerMonitor {
  private timer: unknown;
  private checking = false;
  private active = false;
  private previous?: ViewerState;

  constructor(
    private readonly probe: ViewerProbe,
    private readonly onTransition: (state: ViewerState) => void | Promise<void>,
    private readonly scheduler: ViewerScheduler = defaultViewerScheduler,
  ) {}

  start(): void {
    if (this.active) return;
    this.active = true;
    this.check();
  }

  stop(): void {
    this.active = false;
    if (this.timer !== undefined) this.scheduler.clearTimeout(this.timer);
    this.timer = undefined;
    this.previous = undefined;
  }

  private check(): void {
    if (!this.active || this.checking) return;
    this.checking = true;
    try {
      let next: ViewerState;
      try {
        next = this.probe.snapshot();
      } catch {
        next = state(false, "probe:error", "viewer snapshot failed", undefined);
      }
      if (!this.previous || next.ready !== this.previous.ready || next.epoch !== this.previous.epoch || next.reason !== this.previous.reason) {
        this.previous = next;
        try {
          void Promise.resolve(this.onTransition(next)).catch(() => undefined);
        } catch { /* Keep polling after a synchronous transition failure. */ }
      }
    } finally {
      this.checking = false;
      if (this.active) {
        this.timer = this.scheduler.setTimeout(() => this.check(), VIEWER_POLL_MS);
        if (this.timer && typeof this.timer === "object" && "unref" in this.timer) {
          (this.timer as { unref(): void }).unref();
        }
      }
    }
  }
}
