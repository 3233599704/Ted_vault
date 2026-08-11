import assert from "node:assert/strict";
import test from "node:test";

import { DeepSeekProvider } from "../src/model-provider.js";

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

