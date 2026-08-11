import assert from "node:assert/strict";
import test from "node:test";

import { JobWorker, splitChatMessages } from "../src/job-worker.js";
import { ModelProviderError } from "../src/model-provider.js";
import { AgentStorage } from "../src/storage.js";

test("normal chat paragraphs stay in one relaxed message bubble", () => {
  assert.deepEqual(splitChatMessages("第一段。\n\n第二段。\n\n第三段。"), [
    "第一段。\n第二段。\n第三段。",
  ]);
});

test("background stock and video reports keep their paragraph layout in one outbound job", async () => {
  const storage = new AgentStorage(":memory:");
  const sends = [];
  const worker = new JobWorker({
    config: { maxJobAttempts: 3 },
    storage,
    agent: {},
    weixin: {
      sendText: async (_account, _user, text) => { sends.push(text); },
    },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueJob({
      id: "stock-report-outbound",
      kind: "outbound",
      userId: "user",
      payload: {
        to: "user",
        contextToken: "ctx",
        text: "股票结论\n\n风险说明\n\n模拟计划",
      },
    });
    await worker.process(storage.claimNextJob(["outbound"]));
    assert.deepEqual(sends, ["股票结论\n\n风险说明\n\n模拟计划"]);
  } finally {
    storage.close();
  }
});

test("normal chat can append one automatically selected padded custom sticker", async () => {
  const storage = new AgentStorage(":memory:");
  const events = [];
  const worker = new JobWorker({
    config: { maxJobAttempts: 3, chatModel: "flash", complexModel: "pro" },
    storage,
    agent: {
      chat: async () => ({
        text: "你做得很好。",
        stickerIntent: "夸奖",
        model: "flash",
        usage: {},
        latencyMs: 5,
      }),
    },
    stickerLibrary: {
      prepare: async (tag) => ({ preparedPath: "stickers/cache/ready.png", file: `${tag}.png` }),
    },
    weixin: {
      sendTyping: async () => {},
      sendText: async (_account, _user, text) => events.push(["text", text]),
      sendImageFile: async (_account, _user, file) => events.push(["image", file]),
    },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueJob({
      id: "chat-with-sticker",
      kind: "inbound",
      userId: "user",
      payload: { text: "我完成了", contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob(["inbound"]));
    assert.deepEqual(events, [
      ["text", "你做得很好。"],
      ["image", "stickers/cache/ready.png"],
    ]);
  } finally {
    storage.close();
  }
});

test("chat retry reuses one saved relaxed message", async () => {
  const storage = new AgentStorage(":memory:");
  const sends = [];
  let failedOnce = false;
  let agentCalls = 0;
  const worker = new JobWorker({
    config: { maxJobAttempts: 3, chatModel: "flash", complexModel: "pro" },
    storage,
    agent: {
      chat: async () => {
        agentCalls += 1;
        return {
          text: "第一段。\n\n第二段。\n\n第三段。",
          model: "flash",
          usage: {},
          latencyMs: 5,
        };
      },
    },
    weixin: {
      sendTyping: async () => {},
      sendText: async (_account, _user, text) => {
        sends.push(text);
        if (!failedOnce) {
          failedOnce = true;
          throw new Error("temporary paragraph failure");
        }
      },
    },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueJob({
      id: "paragraph-retry",
      kind: "inbound",
      userId: "user",
      payload: { text: "hi", contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob(["inbound"]));
    storage.db.prepare("UPDATE jobs SET available_at=0 WHERE id='paragraph-retry'").run();
    await worker.process(storage.claimNextJob(["inbound"]));
    assert.equal(agentCalls, 1);
    assert.deepEqual(sends, [
      "第一段。\n第二段。\n第三段。",
      "第一段。\n第二段。\n第三段。",
    ]);
    assert.equal(storage.getQueueStats().done, 1);
  } finally {
    storage.close();
  }
});

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

test("two quick inbound messages call the model and reply only once", async () => {
  const storage = new AgentStorage(":memory:");
  const prompts = [];
  const sends = [];
  const now = Date.now();
  const common = {
    userId: "user",
    quietMs: 8_000,
    maxWaitMs: 30_000,
    maxMessages: 12,
  };
  const worker = new JobWorker({
    config: { maxJobAttempts: 3, chatModel: "flash", complexModel: "pro" },
    storage,
    agent: {
      chat: async (_user, prompt) => {
        prompts.push(prompt);
        return { text: "合并回复", model: "flash", usage: {}, latencyMs: 5 };
      },
    },
    weixin: {
      sendTyping: async () => {},
      sendText: async (_account, _user, text) => sends.push(text),
    },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueInboundBurst({
      ...common,
      id: "quick-burst",
      messageKey: "quick-1",
      payload: { text: "我先说第一句", media: [], contextToken: "ctx-1" },
      now,
    });
    storage.enqueueInboundBurst({
      ...common,
      id: "ignored-new-id",
      messageKey: "quick-2",
      payload: { text: "然后补充第二句", media: [], contextToken: "ctx-2" },
      now: now + 2_000,
    });
    storage.db.prepare("UPDATE jobs SET available_at=0 WHERE id='quick-burst'").run();
    await worker.process(storage.claimNextJob(["inbound"]));
    assert.deepEqual(prompts, ["我先说第一句\n然后补充第二句"]);
    assert.deepEqual(sends, ["合并回复"]);
  } finally {
    storage.close();
  }
});

test("media checkpoint avoids repeating recognition and usage after send retry", async () => {
  const storage = new AgentStorage(":memory:");
  let mediaCalls = 0;
  let agentCalls = 0;
  let sendCalls = 0;
  const worker = new JobWorker({
    config: { maxJobAttempts: 3, chatModel: "flash", complexModel: "pro" },
    storage,
    agent: {
      chat: async (_userId, text) => {
        agentCalls += 1;
        assert.match(text, /图片识别结果/);
        return { text: "看到了", model: "flash", usage: {}, latencyMs: 5 };
      },
    },
    mediaProcessor: {
      process: async () => {
        mediaCalls += 1;
        return {
          routeText: "请看图",
          modelText: "请看图\n[图片识别结果]\n一张课表",
          usageEvents: [{ model: "mimo-v2.5", usage: { total_tokens: 7 }, latencyMs: 10 }],
        };
      },
    },
    weixin: {
      sendTyping: async () => {},
      sendText: async () => {
        sendCalls += 1;
        if (sendCalls === 1) throw new Error("temporary send failure");
      },
    },
    account: {},
    log: () => {},
  });
  try {
    storage.enqueueJob({
      id: "media-inbound",
      kind: "inbound",
      userId: "user",
      payload: { text: "", media: [{ type: "image" }], contextToken: "ctx" },
    });
    await worker.process(storage.claimNextJob());
    storage.db.prepare("UPDATE jobs SET available_at=0 WHERE id='media-inbound'").run();
    await worker.process(storage.claimNextJob());

    assert.equal(mediaCalls, 1);
    assert.equal(agentCalls, 1);
    assert.equal(sendCalls, 2);
    assert.equal(storage.getUsageSummary("user", 0).total_tokens, 7);
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
