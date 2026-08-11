import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { discoverProfiles, normalizeProfileName, profilePaths } from "../src/profiles.js";

test("profile names and paths keep account state isolated", () => {
  assert.equal(normalizeProfileName("Second_2"), "second_2");
  assert.throws(() => normalizeProfileName("../other"));
  const root = path.join("C:\\", "test-project");
  const primary = profilePaths("default", root);
  const second = profilePaths("second", root);
  assert.equal(primary.accountFile, path.join(root, "state", "account.json"));
  assert.equal(second.accountFile, path.join(root, "state", "profiles", "second", "account.json"));
  assert.notEqual(primary.databaseFile, second.databaseFile);
  assert.notEqual(primary.logFile, second.logFile);
});

test("profile discovery includes default and logged-in named profiles only", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "vera-profiles-"));
  try {
    const primary = profilePaths("default", root);
    const second = profilePaths("second", root);
    const incomplete = profilePaths("incomplete", root);
    fs.mkdirSync(primary.stateDir, { recursive: true });
    fs.mkdirSync(second.stateDir, { recursive: true });
    fs.mkdirSync(incomplete.stateDir, { recursive: true });
    fs.writeFileSync(primary.accountFile, "{}", "utf8");
    fs.writeFileSync(second.accountFile, "{}", "utf8");
    assert.deepEqual(discoverProfiles(root), ["default", "second"]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
