import fs from "node:fs";
import path from "node:path";

import { CONFIG } from "./config.js";
import { MimoMediaClient } from "./media/mimo-media.js";
import { detectImageMime } from "./media/weixin-media.js";

const [kind, fileArg, ...promptParts] = process.argv.slice(2);
if (!kind || !fileArg || !["image", "voice"].includes(kind)) {
  console.error("用法: npm run smoke:media -- image <图片路径> [问题]");
  console.error("   或: npm run smoke:media -- voice <wav/mp3路径>");
  process.exit(2);
}

const file = path.resolve(fileArg);
const data = fs.readFileSync(file);
const client = new MimoMediaClient(CONFIG);

try {
  let result;
  if (kind === "image") {
    const mime = detectImageMime(data);
    if (mime === "application/octet-stream") throw new Error("不支持的图片格式");
    result = await client.describeImages(
      [{ buffer: data, mime }],
      promptParts.join(" ") || "请描述这张图片，并识别其中的重要文字。",
    );
  } else {
    const ext = path.extname(file).toLowerCase();
    const mime = ext === ".mp3" ? "audio/mpeg" : "audio/wav";
    result = await client.transcribeAudio(data, mime);
  }
  console.log(result.text);
  const model = kind === "image" ? CONFIG.mimoVisionModel : CONFIG.mimoAsrModel;
  console.log(`\nmodel=${model} tokens=${result.usage.total_tokens} latency=${result.latencyMs}ms`);
} catch (error) {
  console.error(error.userMessage || error.message);
  console.error(error.message);
  process.exitCode = 1;
}
