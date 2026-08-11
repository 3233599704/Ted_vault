import assert from "node:assert/strict";
import test from "node:test";

import { ACTION_HONESTY_POLICY, CHAT_RHYTHM_POLICY, DYNAMIC_FACE_EMOJI_POLICY, relaxedChatText, ROLE_IDENTITY_POLICY, VeraAgent } from "../src/agent.js";
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

test("casual replies drop stage directions and stop after a relaxed beat", () => {
  const verbose = "（看到消息后放下手里的东西，轻轻笑了一下）\n\n宝宝上午好呀。这个开场白我喜欢。今天忙不忙？先跟我说说状态。后面还有一大串不需要一次说完的话。";
  const result = relaxedChatText(verbose.repeat(2), "宝宝上午好");
  assert.doesNotMatch(result, /看到消息/);
  assert.ok(result.length <= 110);
  assert.match(result, /[。！？!?…]$/);

  const detailed = "第一部分很长。".repeat(30);
  assert.equal(relaxedChatText(detailed, "详细分析一下我的股票持仓"), detailed);
});

test("intimate chat keeps at most one short action beat", () => {
  const response = "（靠近一点，压低声音）想听我说点涩涩的话？（轻轻笑了一下）那你先乖一点，别急。";
  const result = relaxedChatText(response, "今晚想跟你聊点涩涩的");
  assert.match(result, /靠近一点/);
  assert.doesNotMatch(result, /轻轻笑了一下/);
  assert.match(result, /想听我说点涩涩的话/);
  assert.ok(result.length <= 180);
});

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

test("agent gives the model a dynamic full face-emoji policy", async () => {
  const storage = new AgentStorage(":memory:");
  let request;
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
    provider: {
      chat: async (value) => {
        request = value;
        return { text: "知道啦 😏", model: "flash", usage: {} };
      },
    },
    persona: "test persona with old emoji rule",
    log: () => {},
  });
  try {
    await agent.chat("user", "hi");
    const system = request.messages[0].content;
    assert.match(system, /test persona with old emoji rule/);
    assert.match(system, /完整黄色脸部表情范围/);
    assert.match(system, /自行决定是否自然地使用 0–2 个脸部 emoji/);
    assert.match(system, /只有后台工具的执行结果明确确认后/);
    assert.match(system, /不能假装写入了私人账簿/);
    assert.match(system, /默认只回复一个微信气泡/);
    assert.match(system, /不要使用括号舞台动作/);
    assert.match(system, /Vera 绝不能自称爸爸/);
    assert.match(system, /不能把这些称呼反套给用户/);
    assert.ok(system.includes(ACTION_HONESTY_POLICY));
    assert.ok(system.includes(CHAT_RHYTHM_POLICY));
    assert.ok(system.includes(ROLE_IDENTITY_POLICY));
    assert.ok(system.endsWith(DYNAMIC_FACE_EMOJI_POLICY));
  } finally {
    storage.close();
  }
});

test("agent hides a model-selected custom sticker directive from chat history", async () => {
  const storage = new AgentStorage(":memory:");
  let systemPrompt = "";
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
    provider: {
      chat: async ({ messages }) => {
        systemPrompt = messages[0].content;
        return {
          text: "你这次做得不错。\n\n[[VERA_STICKER:夸奖]]",
          model: "flash",
          usage: {},
        };
      },
    },
    persona: "test persona",
    stickerLibrary: { promptPolicy: () => "可用自定义表情标签：夸奖" },
    log: () => {},
  });
  try {
    const result = await agent.chat("user", "我完成了");
    assert.match(systemPrompt, /可用自定义表情标签：夸奖/);
    assert.equal(result.text, "你这次做得不错。");
    assert.equal(result.stickerIntent, "夸奖");
    assert.deepEqual(storage.getHistory("user"), [
      { role: "user", content: "我完成了" },
      { role: "assistant", content: "你这次做得不错。" },
    ]);
  } finally {
    storage.close();
  }
});
