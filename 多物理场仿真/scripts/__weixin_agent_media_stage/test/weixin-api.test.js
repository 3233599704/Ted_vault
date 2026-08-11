import assert from "node:assert/strict";
import test from "node:test";

import {
  extractInboundContent,
  extractMessageText,
  plainTextForWeixin,
  splitText,
} from "../src/weixin-api.js";

test("Weixin text formatter removes common Markdown", () => {
  const source = "# 标题\n\n**结论**：看[文档](https://example.com)。\n```js\nconst x = 1;\n```";
  assert.equal(
    plainTextForWeixin(source),
    "标题\n\n结论：看文档 (https://example.com)。\nconst x = 1;",
  );
});

test("Weixin extractor keeps image and untranslated voice media references", () => {
  const content = extractInboundContent({
    item_list: [
      {
        type: 2,
        image_item: {
          aeskey: "00112233445566778899aabbccddeeff",
          media: { encrypt_query_param: "image-param", aes_key: "image-key" },
        },
      },
      {
        type: 3,
        voice_item: {
          encode_type: 6,
          sample_rate: 24000,
          media: { encrypt_query_param: "voice-param", aes_key: "voice-key" },
        },
      },
    ],
  });
  assert.equal(content.text, "");
  assert.deepEqual(content.media.map((item) => item.type), ["image", "voice"]);
  assert.equal(content.media[0].imageAesKey, "00112233445566778899aabbccddeeff");
  assert.equal(content.media[1].encodeType, 6);
});

test("Weixin extractor uses built-in voice transcript without downloading media", () => {
  const content = extractInboundContent({
    item_list: [{
      type: 3,
      voice_item: {
        text: "帮我看看今天的安排",
        media: { encrypt_query_param: "unused", aes_key: "unused" },
      },
    }],
  });
  assert.equal(content.text, "帮我看看今天的安排");
  assert.equal(content.media.length, 0);
});

test("Weixin splitter prefers sentence boundaries", () => {
  const chunks = splitText(`${"a".repeat(70)}。${"b".repeat(70)}`, 100);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].endsWith("。"), true);
});

test("Weixin extractor handles quoted text", () => {
  const text = extractMessageText({
    item_list: [{
      type: 1,
      text_item: { text: "继续说" },
      ref_msg: { title: "旧消息", message_item: { text_item: { text: "原文" } } },
    }],
  });
  assert.equal(text, "[引用: 旧消息 | 原文]\n继续说");
});
