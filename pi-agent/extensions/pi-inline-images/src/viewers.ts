import { spawnSync } from "node:child_process";

export const VIEWER_POLL_MS = 1_500;
const MAX_TMUX_OUTPUT_BYTES = 64 * 1024;

export type ViewerState = {
  ready: boolean;
  /** Changes only when the attached compatible receiver set changes. */
  epoch: string;
  reason: string;
};

export interface ViewerProbe {
  snapshot(): ViewerState;
}

function compatible(term: string): boolean {
  return /(?:kitty|ghostty|wezterm)/iu.test(term);
}

function directViewer(env: NodeJS.ProcessEnv): ViewerState {
  const term = `${env.TERM_PROGRAM ?? ""} ${env.TERM ?? ""}`;
  if (!compatible(term)) return { ready: false, epoch: "direct:none", reason: "Kitty-compatible terminal unavailable" };
  return { ready: true, epoch: `direct:${term}`, reason: "" };
}

/**
 * Queries only the current tmux pane/window. Any visible incompatible client
 * makes the pane pending because Kitty graphics cannot be selectively omitted
 * from its shared pane output.
 */
export function currentViewerState(env: NodeJS.ProcessEnv = process.env): ViewerState {
  if (!(env.TMUX || env.TERM?.startsWith("tmux"))) return directViewer(env);
  const pane = env.TMUX_PANE?.trim();
  if (!pane || !/^%\d+$/u.test(pane)) return { ready: false, epoch: "tmux:unknown", reason: "tmux pane identity unavailable" };
  const run = (args: string[]) => spawnSync("tmux", args, { encoding: "utf8", timeout: 1_000, maxBuffer: MAX_TMUX_OUTPUT_BYTES });
  try {
    const policy = run(["show-options", "-Apv", "-t", pane, "allow-passthrough"]);
    if (policy.status !== 0 || !/^(on|all|yes|true|1)$/iu.test(policy.stdout?.trim() ?? "")) {
      return { ready: false, epoch: "tmux:policy", reason: "tmux passthrough is disabled" };
    }
    const paneInfo = run(["display-message", "-p", "-t", pane, "#{window_id}\t#{pane_active}\t#{window_zoomed_flag}"]);
    const [windowId, paneActive] = paneInfo.stdout?.trim().split("\t") ?? [];
    if (paneInfo.status !== 0 || !windowId || paneActive !== "1") {
      return { ready: false, epoch: "tmux:hidden", reason: "pane is not visible in its current window" };
    }
    const clients = run(["list-clients", "-F", "#{client_pid}\t#{client_created}\t#{client_tty}\t#{client_termname}\t#{client_window}"]);
    if (clients.status !== 0) return { ready: false, epoch: "tmux:unavailable", reason: "tmux client snapshot unavailable" };
    const visible = (clients.stdout ?? "").split("\n").flatMap((line) => {
      const [pid, created, tty, term, clientWindow] = line.split("\t");
      return pid && created && tty && term && clientWindow === windowId ? [{ pid, created, tty, term }] : [];
    });
    if (!visible.length) return { ready: false, epoch: `tmux:${windowId}:none`, reason: "no client is viewing this window" };
    if (visible.some((viewer) => !compatible(viewer.term))) {
      return { ready: false, epoch: `tmux:${windowId}:mixed`, reason: "an incompatible client is viewing this window" };
    }
    const identities = visible.map((viewer) => `${viewer.pid}:${viewer.created}:${viewer.tty}:${viewer.term}`).sort();
    return { ready: true, epoch: `tmux:${windowId}:${identities.join(",")}`, reason: "" };
  } catch {
    return { ready: false, epoch: "tmux:error", reason: "tmux client snapshot failed" };
  }
}

/** One nonoverlapping poller. It emits only state transitions, never ticks. */
export class ViewerMonitor {
  private timer: NodeJS.Timeout | undefined;
  private checking = false;
  private active = false;
  private previous?: ViewerState;

  constructor(private readonly probe: ViewerProbe, private readonly onTransition: (state: ViewerState) => void | Promise<void>) {}

  start(): void {
    if (this.active) return;
    this.active = true;
    void this.check();
  }

  stop(): void {
    this.active = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
  }

  private async check(): Promise<void> {
    if (!this.active || this.checking) return;
    this.checking = true;
    try {
      const next = this.probe.snapshot();
      if (!this.previous || next.ready !== this.previous.ready || next.epoch !== this.previous.epoch || next.reason !== this.previous.reason) {
        this.previous = next;
        await this.onTransition(next);
      }
    } finally {
      this.checking = false;
      if (this.active) {
        this.timer = setTimeout(() => void this.check(), VIEWER_POLL_MS);
        this.timer.unref();
      }
    }
  }
}
