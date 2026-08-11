import assert from "node:assert/strict";
import test from "node:test";

import { JobWorker } from "../src/job-worker.js";
import { AgentStorage } from "../src/storage.js";

test("tool result is persisted and tool is not rerun when outbound delivery retries", async () => {
  const storage = new AgentStorage(":memory:");
  let toolCalls = 0;
  let sendCalls = 0;
  const toolRegistry = {
    run: async () => {
      toolCalls += 1;
      return { reply: "工具结果", model: "tool:test", usage: {} };
    },
  };
  const weixin = {
    sendText: async () => {
      sendCalls += 1;
      if (sendCalls === 1) throw new Error("temporary delivery failure");
    },
  };
  const worker = new JobWorker({
    config: { maxJobAttempts: 3 },
    storage,
    agent: {},
    weixin,
    account: {},
    log: () => {},
    toolRegistry,
  });
  try {
    storage.enqueueJob({
      id: "tool-1",
      kind: "tool",
      userId: "user-1",
      payload: {
        tool: "test",
        originalText: "做个测试",
        contextToken: "ctx",
      },
    });
    await worker.process(storage.claimNextJob(["tool"]));
    const outbound = storage.claimNextJob(["outbound"]);
    await worker.process(outbound);
    storage.db.prepare("UPDATE jobs SET available_at=0 WHERE id=?").run(outbound.id);
    await worker.process(storage.claimNextJob(["outbound"]));

    assert.equal(toolCalls, 1);
    assert.equal(sendCalls, 2);
    assert.deepEqual(storage.getHistory("user-1"), [
      { role: "user", content: "做个测试" },
      { role: "assistant", content: "工具结果" },
    ]);
  } finally {
    storage.close();
  }
});
