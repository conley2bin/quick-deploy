import assert from "node:assert/strict";
import test from "node:test";
import { isPairedSshAlias, pairedSshAlias } from "../src/pairing.ts";

test("only the two actual OS hostnames select the opposite exact configured SSH alias", () => {
  for (const host of ["conley-company", "CONLEY-COMPANY", "Conley-Company"]) {
    assert.equal(pairedSshAlias(host), "conley-Y9000p");
  }
  for (const host of ["conley-Y9000P", "CONLEY-Y9000P", "conley-y9000p"]) {
    assert.equal(pairedSshAlias(host), "conley-company");
  }
  for (const host of ["", "unknown", "localhost", "conley-company.local", " conley-company", "__proto__"]) {
    assert.equal(pairedSshAlias(host), undefined);
  }
});

test("admitted aliases have a closed exact spelling and never default missing metadata", () => {
  assert.equal(isPairedSshAlias("conley-company"), true);
  assert.equal(isPairedSshAlias("conley-Y9000p"), true);
  for (const value of [undefined, null, "", "conley-Y9000P", "CONLEY-COMPANY", "other", "--proxy-command=bad", {}]) {
    assert.equal(isPairedSshAlias(value), false);
  }
});
