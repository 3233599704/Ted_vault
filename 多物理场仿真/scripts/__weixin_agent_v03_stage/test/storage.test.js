import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { AgentStorage } from "../src/storage.js";

test("storage deduplicates jobs and persists conversation usage", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vera-storage-"));
  const storage = new AgentStorage(path.join(dir, "agent.sqlite"));
  try {
    assert.equal(storage.enqueueJob({
      id: "job-1",
      kind: "inbound",
      userId: "user-1",
      sourceKey: "message-1",
      payload: { text: "hi" },
    }), true);
    assert.equal(storage.enqueueJob({
      id: "job-2",
      kind: "inbound",
      userId: "user-1",
      sourceKey: "message-1",
      payload: { text: "duplicate" },
    }), false);

    const job = storage.claimNextJob();
    assert.equal(job.id, "job-1");
    assert.equal(job.payload.text, "hi");
    storage.saveJobResult(job.id, { reply: "hello" });
    storage.completeJob(job.id);

    storage.appendTurn(
      "user-1",
      "hi",
      "hello",
      "deepseek-v4-flash",
      { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 },
      120,
    );
    assert.deepEqual(storage.getHistory("user-1"), [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]);
    assert.equal(storage.getUsageSummary("user-1").total_tokens, 14);
    assert.equal(storage.getQueueStats().done, 1);
  } finally {
    storage.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("storage keeps context while changing model", () => {
  const storage = new AgentStorage(":memory:");
  try {
    storage.saveContext("user-1", "context-token");
    storage.setUserModel("user-1", "deepseek-v4-pro");
    assert.equal(storage.getLatestContext("user-1").context_token, "context-token");
    assert.equal(storage.getUserModel("user-1"), "deepseek-v4-pro");
  } finally {
    storage.close();
  }
});

test("storage claims only requested job kinds", () => {
  const storage = new AgentStorage(":memory:");
  try {
    storage.enqueueJob({ id: "inbound", kind: "inbound", userId: "u", payload: {} });
    storage.enqueueJob({ id: "tool", kind: "tool", userId: "u", payload: {} });
    assert.equal(storage.claimNextJob(["tool"]).id, "tool");
    assert.equal(storage.claimNextJob(["outbound"]), null);
    assert.equal(storage.claimNextJob(["inbound"]).id, "inbound");
  } finally {
    storage.close();
  }
});
