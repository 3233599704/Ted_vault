import assert from "node:assert/strict";
import test from "node:test";

import {
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

