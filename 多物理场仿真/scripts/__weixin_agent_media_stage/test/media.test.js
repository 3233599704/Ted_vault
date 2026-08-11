import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { encode as encodeSilk, isSilk } from "silk-wasm";

import { MediaProcessor } from "../src/media/media-processor.js";
import { MimoMediaClient } from "../src/media/mimo-media.js";
import { pcmToWav, silkToWav } from "../src/media/silk-transcode.js";
import {
  detectImageMime,
  downloadAndDecryptMedia,
  parseAesKey,
} from "../src/media/weixin-media.js";

const baseConfig = {
  weixinCdnBaseUrl: "https://novac2c.cdn.weixin.qq.com/c2c",
  weixinMediaTimeoutMs: 1000,
  weixinImageMaxBytes: 1024 * 1024,
  weixinVoiceMaxBytes: 1024 * 1024,
  weixinMaxImages: 4,
  weixinMaxVoices: 2,
  mimoApiKey: "test-key",
  mimoApiUrl: "https://api.example.test/v1/chat/completions",
  mimoTimeoutMs: 1000,
  mimoVisionModel: "mimo-v2.5",
  mimoAsrModel: "mimo-v2.5-asr",
  mimoVisionMaxTokens: 1200,
  mimoAsrMaxBase64Bytes: 10 * 1024 * 1024,
};

test("Weixin media decryption accepts both AES key encodings", async () => {
  const key = Buffer.from("00112233445566778899aabbccddeeff", "hex");
  const plain = Buffer.from("image bytes for aes test");
  const cipher = crypto.createCipheriv("aes-128-ecb", key, null);
  const encrypted = Buffer.concat([cipher.update(plain), cipher.final()]);
  assert.deepEqual(parseAesKey(key.toString("base64")), key);
  assert.deepEqual(parseAesKey(Buffer.from(key.toString("hex"), "ascii").toString("base64")), key);

  const result = await downloadAndDecryptMedia({
    type: "image",
    media: {
      full_url: "https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=x",
      aes_key: key.toString("base64"),
    },
  }, baseConfig, {
    fetchImpl: async () => new Response(encrypted, { status: 200 }),
  });
  assert.deepEqual(result, plain);
});

test("image MIME detection and PCM WAV wrapping are deterministic", () => {
  const png = Buffer.from("89504e470d0a1a0a00000000", "hex");
  assert.equal(detectImageMime(png), "image/png");
  const wav = pcmToWav(new Uint8Array([0, 0, 1, 0]), 24000);
  assert.equal(wav.subarray(0, 4).toString("ascii"), "RIFF");
  assert.equal(wav.readUInt32LE(24), 24000);
  assert.equal(wav.length, 48);
});

test("silk-wasm encodes and decodes a Weixin-compatible voice buffer", async () => {
  const pcm = new Uint8Array(2400);
  const wav = pcmToWav(pcm, 24000);
  const silk = await encodeSilk(wav, 0);
  assert.equal(isSilk(silk.data), true);
  const decodedWav = await silkToWav(Buffer.from(silk.data), 24000);
  assert.equal(decodedWav.subarray(0, 4).toString("ascii"), "RIFF");
  assert.equal(decodedWav.readUInt32LE(24), 24000);
});

test("MiMo media client builds image and ASR requests", async () => {
  const bodies = [];
  const client = new MimoMediaClient(baseConfig, {
    fetchImpl: async (_url, options) => {
      bodies.push(JSON.parse(options.body));
      return new Response(JSON.stringify({
        choices: [{ message: { content: bodies.length === 1 ? "图片里是一张课表" : "明天下午去游泳" } }],
        usage: { total_tokens: 12 },
      }), { status: 200 });
    },
  });
  await client.describeImages([{ buffer: Buffer.from("png"), mime: "image/png" }], "这是什么");
  await client.transcribeAudio(Buffer.from("wav"), "audio/wav");
  assert.equal(bodies[0].messages[0].content[0].type, "image_url");
  assert.match(bodies[0].messages[0].content[0].image_url.url, /^data:image\/png;base64,/);
  assert.equal(bodies[1].model, "mimo-v2.5-asr");
  assert.equal(bodies[1].messages[0].content[0].type, "input_audio");
});

test("media processor uses ASR for untranslated voice and vision for images", async () => {
  const png = Buffer.from("89504e470d0a1a0a00000000", "hex");
  const processor = new MediaProcessor(baseConfig, {
    downloadImpl: async (item) => item.type === "image" ? png : Buffer.from("silk"),
    silkToWavImpl: async () => pcmToWav(new Uint8Array([0, 0]), 24000),
    mimo: {
      transcribeAudio: async () => ({ text: "帮我看图", usage: { total_tokens: 3 }, latencyMs: 10 }),
      describeImages: async (_images, text) => ({ text: `识别结果:${text}`, usage: { total_tokens: 5 }, latencyMs: 20 }),
    },
  });
  const result = await processor.process({
    media: [
      { type: "voice", encodeType: 6, media: {} },
      { type: "image", media: {} },
    ],
  });
  assert.equal(result.routeText, "帮我看图");
  assert.match(result.modelText, /识别结果:帮我看图/);
  assert.equal(result.usageEvents.length, 2);
});
