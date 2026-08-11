import { CONFIG } from "./config.js";
import { DouyinTool, extractDouyinUrl } from "./tools/douyin-tool.js";

const input = process.argv.slice(2).join(" ").trim();
const url = extractDouyinUrl(input);
if (!url) {
  console.error("用法：npm run smoke:douyin -- https://v.douyin.com/xxxx/");
  process.exitCode = 2;
} else {
  const tool = new DouyinTool(CONFIG);
  try {
    const result = await tool.execute({
      job: { payload: { args: { url } } },
    });
    console.log(JSON.stringify({
      ok: true,
      model: result.model,
      usage: result.usage,
      latencyMs: result.latencyMs,
      reply: result.reply,
    }, null, 2));
  } catch (error) {
    console.error(JSON.stringify({
      ok: false,
      error: String(error?.message || error),
      userMessage: error?.userMessage || "",
    }, null, 2));
    process.exitCode = 1;
  }
}
