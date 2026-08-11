import assert from "node:assert/strict";
import test from "node:test";

import { JobWorker } from "../src/job-worker.js";
import { ModelProviderError } from "../src/model-provider.js";
import { AgentStorage } from "../src/storage.js";

test("job retry reuses saved model result instead of billing twice", async () => {
  const storage = new AgentStorage(":memory:");
  let agentCalls = 0;
  let sendCalls = 0;
  const agent = {
    chat: async () => {
      agentCalls += 1;
      return {
        text: "reply",
        model: "test-model",
        usage: { total_tokens: 5 },
        latencyMs: 10,
      };
    },
    selectedModel: () => "test-model",
  };
  const weixin = {
    sendTyping: async () => {},
    sendText: async () => {
      sendCalls += 1;
      if (sendCalls === 1) throw new Error("temporary send failure");
    },
  };
  const worker = new JobWorker({
    config: {
      maxJobAttempts: 3,
      chatModel: "flash",
      complexModel: "pro",
      dailyTokenLimit: 500000,
    },
    storage,
    agent,
    weixin,
    account: {},
    log: () => {},
  });

  try {
    storage.enqueueJob({
      id: "inbound-1",
      kind: "inbound",
      userId: "user-1",
      payload: { text: "hi", contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob());
    storage.db.prepare("UPDATE jobs SET available_at=0 WHERE id='inbound-1'").run();
    await worker.process(storage.claimNextJob());

    assert.equal(agentCalls, 1);
    assert.equal(sendCalls, 2);
    assert.equal(storage.getQueueStats().done, 1);
  } finally {
    storage.close();
  }
});

test("retryable model failure keeps inbound message queued and later succeeds", async () => {
  const storage = new AgentStorage(":memory:");
  let agentCalls = 0;
  let sendCalls = 0;
  const agent = {
    chat: async () => {
      agentCalls += 1;
      if (agentCalls === 1) {
        throw new ModelProviderError("network unavailable", {
          retryable: true,
          userMessage: "暂时连接不上模型",
        });
      }
      return { text: "recovered", model: "flash", usage: {}, latencyMs: 10 };
    },
  };
  const worker = new JobWorker({
    config: { maxJobAttempts: 3, chatModel: "flash", complexModel: "pro" },
    storage,
    agent,
    weixin: {
      sendTyping: async () => {},
      sendText: async () => { sendCalls += 1; },
    },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueJob({
      id: "resume-message",
      kind: "inbound",
      userId: "user",
      payload: { text: "hello", contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob());
    assert.equal(storage.getQueueStats().pending, 1);
    assert.equal(sendCalls, 0);
    storage.db.prepare("UPDATE jobs SET available_at=0 WHERE id='resume-message'").run();
    await worker.process(storage.claimNextJob());
    assert.equal(agentCalls, 2);
    assert.equal(sendCalls, 1);
    assert.equal(storage.getQueueStats().done, 1);
  } finally {
    storage.close();
  }
});

test("final inbound failure queues one user-facing notification", async () => {
  const storage = new AgentStorage(":memory:");
  const worker = new JobWorker({
    config: { maxJobAttempts: 1, chatModel: "flash", complexModel: "pro" },
    storage,
    agent: {
      chat: async () => {
        throw new ModelProviderError("network unavailable", {
          retryable: true,
          userMessage: "模型网络仍未恢复",
        });
      },
    },
    weixin: { sendTyping: async () => {}, sendText: async () => {} },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueJob({
      id: "failed-message",
      kind: "inbound",
      userId: "user",
      payload: { text: "hello", contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob(["inbound"]));
    const notification = storage.claimNextJob(["outbound"]);
    assert.equal(notification.payload.text, "模型网络仍未恢复");
    assert.equal(storage.getQueueStats().failed, 1);
  } finally {
    storage.close();
  }
});
