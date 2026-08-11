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

test("storage durably debounces a burst and deduplicates message receipts", () => {
  const storage = new AgentStorage(":memory:");
  const options = {
    userId: "user-1",
    quietMs: 8_000,
    maxWaitMs: 30_000,
    maxMessages: 12,
  };
  try {
    const first = storage.enqueueInboundBurst({
      ...options,
      id: "burst-1",
      messageKey: "message-1",
      payload: { text: "第一条", media: [], contextToken: "ctx-1" },
      now: 1_000,
    });
    assert.equal(first.merged, false);
    assert.equal(first.availableAt, 9_000);

    const second = storage.enqueueInboundBurst({
      ...options,
      id: "burst-2",
      messageKey: "message-2",
      payload: { text: "第二条", media: [{ type: "image" }], contextToken: "ctx-2" },
      now: 6_000,
    });
    assert.equal(second.merged, true);
    assert.equal(second.jobId, "burst-1");
    assert.equal(second.messageCount, 2);
    assert.equal(second.availableAt, 14_000);

    const late = storage.enqueueInboundBurst({
      ...options,
      id: "burst-3",
      messageKey: "message-3",
      payload: { text: "第三条", media: [], contextToken: "ctx-3" },
      now: 26_000,
    });
    assert.equal(late.availableAt, 31_000);

    const duplicate = storage.enqueueInboundBurst({
      ...options,
      id: "duplicate",
      messageKey: "message-2",
      payload: { text: "不应重复", media: [], contextToken: "ctx-x" },
      now: 27_000,
    });
    assert.equal(duplicate.duplicate, true);

    const row = storage.db.prepare("SELECT payload_json FROM jobs WHERE id='burst-1'").get();
    const payload = JSON.parse(row.payload_json);
    assert.equal(payload.text, "第一条\n第二条\n第三条");
    assert.equal(payload.messageCount, 3);
    assert.equal(payload.contextToken, "ctx-3");
    assert.equal(payload.media.length, 1);
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
