// Loader-level fault seam for lifecycle.test.mjs: substituted for "node:fs/promises" only inside the
// extension instance imported as index.ts?faulty-fs. Behaves exactly like the real module unless the test
// arms globalThis.__rcFsFaults; the real filesystem is never patched.
import * as real from "node:fs/promises";
export * from "node:fs/promises";

const plan = () => globalThis.__rcFsFaults;

export async function rm(path, options) {
  const faults = plan();
  faults?.rmCalls.push(path);
  if (faults?.rm > 0) {
    faults.rm -= 1;
    faults.onRmFail?.();
    throw Object.assign(new Error("synthetic rm failure"), { code: "EIO" });
  }
  return real.rm(path, options);
}

export async function unlink(path) {
  plan()?.unlinkCalls.push(path);
  return real.unlink(path);
}

export async function lstat(path, options) {
  const result = await real.lstat(path, options);
  plan()?.afterLstat?.();
  return result;
}
