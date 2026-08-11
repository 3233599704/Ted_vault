import assert from "node:assert/strict";
import test from "node:test";

import { VeraAgent } from "../src/agent.js";
import { ModelProviderError } from "../src/model-provider.js";
import { AgentStorage } from "../src/storage.js";

function makeAgent(error) {
  const storage = new AgentStorage(":memory:");
  const agent = new VeraAgent({
    config: {
      chatModel: "flash",
      complexModel: "pro",
      maxInputChars: 1000,
      dailyTokenLimit: 10000,
      historyMessages: 10,
      historyChars: 1000,
    },
    storage,
    provider: { chat: async () => { throw error; } },
    persona: "test persona",
    log: () => {},
  });
  return { agent, storage };
}

test("agent rethrows retryable model errors for durable job retry", async () => {
  const error = new ModelProviderError("network unavailable", {
    retryable: true,
    userMessage: "稍后再试",
  });
  const { agent, storage } = makeAgent(error);
  try {
    await assert.rejects(agent.chat("user", "hello"), error);
  } finally {
    storage.close();
  }
});

test("agent returns non-retryable model errors immediately", async () => {
  const error = new ModelProviderError("invalid key", {
    retryable: false,
    userMessage: "鉴权失败",
  });
  const { agent, storage } = makeAgent(error);
  try {
    const result = await agent.chat("user", "hello");
    assert.equal(result.text, "鉴权失败");
  } finally {
    storage.close();
  }
});
