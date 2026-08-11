import assert from "node:assert/strict";
import test from "node:test";

import { DeepSeekProvider, requestJsonWithAddressFailover } from "../src/model-provider.js";

test("DeepSeek transport falls through a broken DNS address to a healthy one", async () => {
  const attempted = [];
  const response = await requestJsonWithAddressFailover(
    "https://api.deepseek.com/chat/completions",
    { headers: {}, body: "{}", timeoutMs: 1000 },
    {
      resolve4: async () => ["183.131.191.171", "58.49.197.113"],
      requestAddress: async (_url, options) => {
        attempted.push(options.address);
        if (options.address === "183.131.191.171") {
          const error = new Error("connection reset");
          error.code = "ECONNRESET";
          throw error;
        }
        return { ok: true, status: 200, text: async () => "{}" };
      },
    },
  );
  assert.equal(response.status, 200);
  assert.deepEqual(attempted, ["183.131.191.171", "58.49.197.113"]);
});

test("DeepSeek transport ignores an empty resolver result and retains a fallback request", async () => {
  const attempted = [];
  const response = await requestJsonWithAddressFailover(
    "https://api.deepseek.com/chat/completions",
    { headers: {}, body: "{}", timeoutMs: 1000 },
    {
      resolve4: async () => [],
      requestAddress: async (_url, options) => {
        attempted.push(options.address);
        return { ok: true, status: 200, text: async () => "{}" };
      },
    },
  );
  assert.equal(response.status, 200);
  assert.deepEqual(attempted, [""]);
});

test("DeepSeek provider sends non-thinking chat request and returns usage", async () => {
  let request;
  const provider = new DeepSeekProvider({
    deepseekApiKey: "test-key",
    deepseekBaseUrl: "https://api.deepseek.com",
    modelTimeoutMs: 5000,
    maxOutputTokens: 900,
  }, {
    fetchImpl: async (url, options) => {
      request = { url, options, body: JSON.parse(options.body) };
      return new Response(JSON.stringify({
        model: "deepseek-v4-flash",
        choices: [{ message: { content: "hello" } }],
        usage: { prompt_tokens: 8, completion_tokens: 2, total_tokens: 10 },
      }), { status: 200 });
    },
  });

  const result = await provider.chat({
    model: "deepseek-v4-flash",
    messages: [{ role: "user", content: "hi" }],
    thinking: false,
  });

  assert.equal(request.url, "https://api.deepseek.com/chat/completions");
  assert.equal(request.body.thinking.type, "disabled");
  assert.equal(request.body.max_tokens, 900);
  assert.equal(request.options.headers.Authorization, "Bearer test-key");
  assert.equal(result.text, "hello");
  assert.equal(result.usage.total_tokens, 10);
});
