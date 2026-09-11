import { readdirSync, readFileSync } from "node:fs";

const SUSPEND_NOTICE = "Suspend unavailable: this process group has no controlling job owner.";

export function parseProcStat(raw) {
  const close = raw.lastIndexOf(")");
  if (close < 0) throw new Error("malformed proc stat");
  const pid = Number(raw.slice(0, raw.indexOf(" ")));
  const fields = raw.slice(close + 2).trim().split(/\s+/);
  const ppid = Number(fields[1]);
  const pgrp = Number(fields[2]);
  const session = Number(fields[3]);
  if (
    ![pid, ppid, pgrp, session].every(Number.isInteger)
    || pid <= 0
    || ppid < 0
    || pgrp < 0
    || session < 0
  ) {
    throw new Error("invalid proc identifiers");
  }
  return { pid, ppid, pgrp, session };
}

function readLinuxProc(pid) {
  return parseProcStat(readFileSync(`/proc/${pid}/stat`, "utf8"));
}

function listLinuxPids() {
  return readdirSync("/proc").filter((name) => /^\d+$/.test(name)).map(Number);
}

/**
 * Implements the POSIX orphan-process-group relation for one observed group.
 * Any inaccessible or inconsistent proc record fails open: callers must leave
 * native job control untouched unless the orphan conclusion is complete.
 */
export function classifyProcessGroup(pid, read, list) {
  try {
    const self = read(pid);
    if (self.pid !== pid || self.pgrp <= 0 || self.session <= 0) {
      return { supported: false, orphaned: false };
    }
    const members = [];
    for (const candidate of list()) {
      let record;
      try {
        record = read(candidate);
      } catch (error) {
        // A vanished proc entry is no longer a member by the time we decide.
        // Any other read or parse failure could hide a member, so fail open.
        if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") continue;
        return { supported: false, orphaned: false };
      }
      if (record.pgrp === self.pgrp) members.push(record);
    }
    if (!members.some((member) => member.pid === self.pid)) return { supported: false, orphaned: false };
    for (const member of members) {
      if (member.session !== self.session || member.ppid <= 0) return { supported: false, orphaned: false };
      const parent = read(member.ppid);
      if (parent.session === self.session && parent.pgrp !== self.pgrp) return { supported: true, orphaned: false };
    }
    return { supported: true, orphaned: true };
  } catch {
    return { supported: false, orphaned: false };
  }
}

export function classifyCurrentProcessGroup() {
  if (process.platform !== "linux") return { supported: false, orphaned: false };
  return classifyProcessGroup(process.pid, readLinuxProc, listLinuxPids);
}

export function suspendGuardInput(keybindings, data, notify, classify = classifyCurrentProcessGroup) {
  if (!keybindings.matches(data, "app.suspend")) return undefined;
  let classification;
  try {
    classification = classify();
  } catch {
    return undefined;
  }
  if (!classification?.supported || !classification.orphaned) return undefined;
  notify(SUSPEND_NOTICE, "warning");
  return { consume: true };
}

export function installSuspendListener(ui, keybindings, classify = classifyCurrentProcessGroup) {
  return ui.onTerminalInput((data) => suspendGuardInput(
    keybindings,
    data,
    (message, kind) => ui.notify(message, kind),
    classify,
  ));
}
