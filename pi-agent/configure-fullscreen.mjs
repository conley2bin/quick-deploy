// Main-installer policy only; never load Pi or change a running session.
import { execFile } from "node:child_process";
import nativeFs, { constants } from "node:fs";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const settingsPath = join(resolve(process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi/agent")), "settings.json");

async function statOrMissing(path) {
  try { return await fs.lstat(path, { bigint: true }); }
  catch (error) { if (error.code === "ENOENT") return undefined; throw error; }
}

function revision(stat) {
  return stat && [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(":");
}

async function targetOf(entry) {
  // A dangling settings symlink is an error, not a fresh configuration.
  return entry ? fs.realpath(settingsPath) : join(await fs.realpath(dirname(settingsPath)), basename(settingsPath));
}

async function configure() {
  // Resolve from the installed executable, not an npm prefix, Pi version, or
  // checkout dependency. This imports only the same locking library Pi uses.
  let lockfile;
  try { lockfile = createRequire(await fs.realpath(process.argv[2]))("proper-lockfile"); }
  catch (error) { throw new Error(`cannot load Pi's proper-lockfile dependency from its executable: ${error.message}`); }

  await fs.mkdir(dirname(settingsPath), { recursive: true, mode: 0o700 });
  const locks = [];
  let compromised, work, failure, changed = false;
  function assertLockOwner(path) {
    const lock = locks.find(item => item.path === path);
    if (!lock) return; // proper-lockfile's stale-lock acquisition, not cleanup
    const current = nativeFs.lstatSync(path, { bigint: true });
    if (!lock.stat || !current.isDirectory() || current.dev !== lock.stat.dev || current.ino !== lock.stat.ino) {
      throw new Error("settings lock was replaced; refusing to write or remove a successor's lock");
    }
  }
  // proper-lockfile's release/exit cleanup otherwise unconditionally rmdirs
  // its pathname. Guard cleanup too if the lock was lost during disk I/O.
  const lockFs = Object.create(nativeFs);
  lockFs.rmdir = (path, callback) => {
    try { assertLockOwner(path); } catch (error) { callback(error); return; }
    nativeFs.rmdir(path, callback);
  };
  lockFs.rmdirSync = path => { assertLockOwner(path); nativeFs.rmdirSync(path); };
  async function acquire(path) {
    const release = await lockfile.lock(path, {
      realpath: false,
      fs: lockFs,
      // Match FileSettingsStorage's bounded contention retry and default
      // stale/heartbeat policy. Never use a separate advisory flock.
      retries: { retries: 9, factor: 1, minTimeout: 20, maxTimeout: 20 },
      onCompromised: error => { compromised = error; },
    });
    const lock = { path: `${path}.lock`, release };
    locks.push(lock);
    // Hold the directory inode open: deletion/recreation must not reuse the
    // inode number and impersonate the lock we acquired.
    lock.directory = await fs.open(lock.path, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    lock.stat = await lock.directory.stat({ bigint: true });
  }

  try {
    await acquire(settingsPath);
    const entry = await statOrMissing(settingsPath);
    const target = await targetOf(entry);
    // Pi locks the lexical path (realpath:false). A file symlink needs a
    // second target lock; a directory alias already shares the physical lock.
    if (`${target}.lock` !== await fs.realpath(`${settingsPath}.lock`)) await acquire(target);
    if (await targetOf(await statOrMissing(settingsPath)) !== target) throw new Error("settings target changed while acquiring its lock");

    const before = await statOrMissing(target);
    if (before && !before.isFile()) throw new Error("settings target is not a regular file");
    let text = "", settings = {};
    if (before) {
      // Refuse malformed UTF-8 as well as malformed JSON; do not replace bytes
      // with U+FFFD and silently rewrite unrelated values.
      text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await fs.readFile(target, {
        flag: constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      }));
      settings = JSON.parse(text.replace(/^\uFEFF/, ""));
      if (!settings || typeof settings !== "object" || Array.isArray(settings)) throw new Error("settings JSON must be an object");
    }

    if (settings.tuiMode !== "fullscreen") {
      if (before) {
        if (before.nlink !== 1n) throw new Error("cannot atomically update multiply-linked settings; use a single file or symlink");
        // Rename permission alone must not bypass a read-only settings file.
        await fs.access(target, constants.W_OK);
      }
      settings.tuiMode = "fullscreen";
      work = await fs.mkdtemp(join(dirname(target), ".pi-settings-"));
      const temporary = join(work, "settings.json");
      const file = await fs.open(temporary, "wx", 0o600);
      try {
        await file.writeFile(`${text.startsWith("\uFEFF") ? "\uFEFF" : ""}${JSON.stringify(settings, null, 2)}\n`, "utf8");
        if (before) {
          // Explicit attributes make ownership/ACL/xattr failures fatal (unlike
          // cp --preserve=all's best-effort xattr behavior). No content copy.
          await run("cp", ["--attributes-only", "--preserve=mode,ownership,xattr", "--", target, temporary]);
          const copied = await file.stat({ bigint: true });
          if (copied.uid !== before.uid || copied.gid !== before.gid || copied.mode !== before.mode) throw new Error("settings metadata could not be preserved");
        }
        await file.sync();
      } finally { await file.close(); }

      if (compromised) throw compromised;
      if (revision(await statOrMissing(settingsPath)) !== revision(entry) ||
          await targetOf(await statOrMissing(settingsPath)) !== target ||
          revision(await statOrMissing(target)) !== revision(before)) throw new Error("settings changed outside the lock; refusing to overwrite");
      for (const lock of locks) assertLockOwner(lock.path);
      if (compromised) throw compromised;
      await fs.rename(temporary, target);
      changed = true;
      const directory = await fs.open(dirname(target), "r");
      try { await directory.sync(); } finally { await directory.close(); }
    }
    if (compromised) throw compromised;
  } catch (error) { failure = error; }
  finally {
    // Attempt every cleanup and preserve a primary write/parse error. A failed
    // unlock is still a real failure; do not print configured after it.
    const errors = failure ? [failure] : [];
    if (work) { try { await fs.rm(work, { recursive: true, force: true }); } catch (error) { errors.push(error); } }
    for (const lock of locks.reverse()) {
      try { await lock.release(); } catch (error) { errors.push(error); }
      if (lock.directory) { try { await lock.directory.close(); } catch (error) { errors.push(error); } }
    }
    if (errors.length === 1 && !changed) throw errors[0];
    if (errors.length) throw new AggregateError(errors, `${changed ? "fullscreen committed, but completion failed" : "settings update failed"}: ${errors.map(error => error.message).join("; ")}`);
  }
  console.log(`Global TUI default ${changed ? "set to" : "already"} fullscreen: ${settingsPath}${changed ? "" : " (unchanged)"}`);
}

try { await configure(); }
catch (error) {
  console.error(`pi-agent install: failed to configure global fullscreen at ${settingsPath}: ${error.message}`);
  process.exitCode = 1;
}
