import { CONFIG } from "./config.js";
import { DeepSeekProvider } from "./model-provider.js";

const provider = new DeepSeekProvider({
  ...CONFIG,
  modelTimeoutMs: Math.min(CONFIG.modelTimeoutMs, 30_000),
  maxOutputTokens: 32,
});

if (!provider.isConfigured()) {
  throw new Error("DeepSeek API Key is not configured.");
}

const result = await provider.chat({
  model: CONFIG.chatModel,
  thinking: false,
  messages: [
    { role: "system", content: "Return only the requested short answer." },
    { role: "user", content: "只回复 OK" },
  ],
});

console.log(JSON.stringify({
  ok: result.text.trim().toUpperCase() === "OK",
  model: result.model,
  text: result.text,
  totalTokens: Number(result.usage?.total_tokens || 0),
}, null, 2));

