import assert from "node:assert/strict";
import test from "node:test";

import { JobWorker } from "../src/job-worker.js";
import { AgentStorage } from "../src/storage.js";

test("silent tool routes do not send an awkward acknowledgement", async () => {
  const storage = new AgentStorage(":memory:");
  let textSends = 0;
  const worker = new JobWorker({
    config: { maxJobAttempts: 3 },
    storage,
    agent: {},
    weixin: {
      sendTyping: async () => {},
      sendText: async () => { textSends += 1; },
    },
    account: {},
    log: () => {},
    toolRegistry: {
      route: () => ({
        tool: "sticker",
        action: "send",
        args: { id: "smirk" },
        silentAcknowledgement: true,
      }),
    },
  });
  try {
    storage.enqueueJob({
      id: "inbound-sticker",
      kind: "inbound",
      userId: "user",
      payload: { text: "/sticker smirk", contextToken: "ctx", media: [] },
    });
    await worker.process(storage.claimNextJob(["inbound"]));
    assert.equal(textSends, 0);
    const toolJob = storage.claimNextJob(["tool"]);
    assert.equal(toolJob.payload.tool, "sticker");
    assert.equal(toolJob.payload.args.id, "smirk");
  } finally {
    storage.close();
  }
});

test("Codex routes enter an independent codex job queue", async () => {
  const storage = new AgentStorage(":memory:");
  const worker = new JobWorker({
    config: { maxJobAttempts: 3 },
    storage,
    agent: {},
    weixin: { sendTyping: async () => {}, sendText: async () => {} },
    account: {},
    log: () => {},
    toolRegistry: {
      route: () => ({
        tool: "codex_diagnostic",
        action: "diagnose",
        args: { request: "查日志" },
        jobKind: "codex",
        acknowledgement: "我去检查。",
      }),
    },
  });
  try {
    storage.enqueueJob({
      id: "inbound-codex",
      kind: "inbound",
      userId: "admin",
      payload: { text: "查一下日志", contextToken: "ctx", media: [] },
    });
    await worker.process(storage.claimNextJob(["inbound"]));
    const codexJob = storage.claimNextJob(["codex"]);
    assert.equal(codexJob.kind, "codex");
    assert.equal(codexJob.payload.tool, "codex_diagnostic");
    assert.equal(storage.claimNextJob(["tool"]), null);
  } finally {
    storage.close();
  }
});

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

test("image tool result becomes an outbound image job", async () => {
  const storage = new AgentStorage(":memory:");
  const worker = new JobWorker({
    config: { maxJobAttempts: 3 },
    storage,
    agent: {},
    weixin: {},
    account: {},
    log: () => {},
    toolRegistry: {
      run: async () => ({
        reply: "",
        imagePath: "stickers/smirk.png",
        stickerId: "smirk",
        model: "sticker:local",
        usage: {},
      }),
    },
  });
  try {
    storage.enqueueJob({
      id: "sticker-job",
      kind: "tool",
      userId: "user",
      payload: { tool: "sticker", originalText: "/sticker smirk", contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob(["tool"]));
    const outbound = storage.claimNextJob(["outbound"]);
    assert.equal(outbound.payload.imagePath, "stickers/smirk.png");
    assert.equal(outbound.payload.text, "");
  } finally {
    storage.close();
  }
});
