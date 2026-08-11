import { CONFIG } from "./config.js";
import { DouyinTool } from "./tools/douyin-tool.js";

const url = process.argv[2] || "https://v.douyin.com/igB3Aq7PUy0/";
const tool = new DouyinTool(CONFIG, { log: console.log });

try {
  const result = await tool.execute({
    job: { payload: { args: { url } } },
  });
  console.log(result.reply);
  console.log(`\nmodel=${result.model} tokens=${result.usage?.total_tokens || 0} latency=${result.latencyMs}ms`);
} catch (error) {
  console.error(error.userMessage || error.message);
  console.error(error.message);
  process.exitCode = 1;
}
