import assert from "node:assert/strict";
import { test } from "node:test";
import {
  classifyProcessGroup,
  installSuspendListener,
  parseProcStat,
  suspendGuardInput,
} from "../pi-suspend-guard/guard.mjs";

const record = (pid, ppid, pgrp, session) => ({ pid, ppid, pgrp, session });
const lookup = (rows) => (pid) => {
  const value = rows.get(pid);
  if (!value) throw new Error(`missing ${pid}`);
  return value;
};

test("proc stat parser uses fields after the final command-name parenthesis", () => {
  const parsed = parseProcStat("41 (name ) with parens) S 3 5 7 0 0 0");
  assert.deepEqual(parsed, record(41, 3, 5, 7));
  assert.deepEqual(parseProcStat("2 (kernel) S 0 0 0 0 0 0"), record(2, 0, 0, 0));
});

test("classifier finds the formal same-session different-pgrp parent witness", () => {
  const rows = new Map([
    [20, record(20, 10, 20, 7)],
    [10, record(10, 1, 10, 7)],
  ]);
  assert.deepEqual(classifyProcessGroup(20, lookup(rows), () => [20]), {
    supported: true,
    orphaned: false,
  });
});

test("classifier recognizes a session-leader group whose parent is in another session", () => {
  const rows = new Map([
    [20, record(20, 10, 20, 20)],
    [10, record(10, 1, 10, 7)],
  ]);
  assert.deepEqual(classifyProcessGroup(20, lookup(rows), () => [20]), {
    supported: true,
    orphaned: true,
  });
});

test("classifier fails open when a group member or its parent cannot be read", () => {
  const rows = new Map([[20, record(20, 10, 20, 20)]]);
  assert.deepEqual(classifyProcessGroup(20, lookup(rows), () => [20]), {
    supported: false,
    orphaned: false,
  });
  assert.deepEqual(classifyProcessGroup(20, lookup(rows), () => [20, 21]), {
    supported: false,
    orphaned: false,
  });
});

test("guard lazily consumes only an orphan suspend attempt and delegates ordinary input", () => {
  const keybindings = { matches: (data, action) => action === "app.suspend" && data === "remapped-suspend" };
  const notices = [];
  const passed = [];
  let classifications = 0;
  const states = [
    { supported: true, orphaned: false },
    { supported: true, orphaned: true },
  ];
  const listener = (data) => suspendGuardInput(
    keybindings,
    data,
    (message, kind) => notices.push({ message, kind }),
    () => {
      classifications += 1;
      return states.shift();
    },
  );
  const dispatch = (data) => {
    const result = listener(data);
    if (!result?.consume) passed.push(data);
  };

  dispatch("text");
  dispatch("\x1b");
  assert.equal(classifications, 0, "ordinary input must not scan /proc");
  dispatch("remapped-suspend");
  dispatch("remapped-suspend");

  assert.equal(classifications, 2);
  assert.equal(notices.length, 1);
  assert.deepEqual(passed, ["text", "\x1b", "remapped-suspend"]);
});

test("listener installs once and releases across lifecycle boundaries", () => {
  let listener;
  let unsubscriptions = 0;
  const notices = [];
  const ui = {
    notify: (message, kind) => notices.push({ message, kind }),
    onTerminalInput: (next) => {
      listener = next;
      return () => { unsubscriptions += 1; };
    },
  };
  const keybindings = { matches: (data, action) => action === "app.suspend" && data === "configured-suspend" };

  const release = installSuspendListener(ui, keybindings, () => ({ supported: true, orphaned: true }));
  assert.equal(listener("configured-suspend").consume, true);
  assert.equal(listener("text"), undefined);
  assert.equal(notices.length, 1);
  release();
  assert.equal(unsubscriptions, 1);
});
