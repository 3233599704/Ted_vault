import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { StickerTool } from "../src/tools/sticker-tool.js";
import { WeixinApi } from "../src/weixin-api.js";

const pngHeader = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);

test("sticker debug command sends inline emoji while natural chat stays with the model", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sticker-tool-"));
  try {
    fs.writeFileSync(path.join(directory, "manifest.json"), JSON.stringify({
      stickers: [{ id: "smirk", label: "坏笑", text: "😏", moods: ["smirk"] }],
    }));
    const tool = new StickerTool({ stickersDir: directory });
    const route = tool.route("/sticker smirk");
    assert.equal(route.args.id, "smirk");
    assert.equal(route.silentAcknowledgement, true);
    assert.equal(tool.route("发个坏笑表情"), null);
    assert.equal(tool.route("来个开心的表情"), null);
    const result = await tool.execute({ job: { payload: { action: "send", args: { id: "smirk" } } } });
    assert.equal(result.stickerId, "smirk");
    assert.equal(result.reply, "😏");
    assert.equal(result.imagePath, undefined);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("custom sticker artwork can still use the image transport", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sticker-image-"));
  try {
    fs.writeFileSync(path.join(directory, "vera.png"), pngHeader);
    fs.writeFileSync(path.join(directory, "manifest.json"), JSON.stringify({
      stickers: [{ id: "vera", label: "Vera", file: "vera.png", moods: ["vera"] }],
    }));
    const tool = new StickerTool({ stickersDir: directory });
    const result = await tool.execute({ job: { payload: { action: "send", args: { id: "vera" } } } });
    assert.equal(result.imagePath, path.join(directory, "vera.png"));
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("Weixin image upload encrypts CDN bytes and sends an IMAGE item", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "weixin-upload-"));
  const imageFile = path.join(directory, "test.png");
  fs.writeFileSync(imageFile, pngHeader);
  const requests = [];
  const api = new WeixinApi({
    channelVersion: "2.4.6",
    botAgent: "VeraAgent/0.7.3",
    ilinkAppId: "bot",
    weixinBaseUrl: "https://ilinkai.weixin.qq.com",
    weixinCdnBaseUrl: "https://novac2c.cdn.weixin.qq.com/c2c",
    weixinMediaTimeoutMs: 1000,
    weixinOutboundImageMaxBytes: 1024,
  }, () => {}, {
    fetchImpl: async (url, options) => {
      requests.push({ url: String(url), options });
      if (String(url).includes("getuploadurl")) {
        return new Response(JSON.stringify({
          upload_full_url: "https://novac2c.cdn.weixin.qq.com/c2c/upload?token=test",
        }), { status: 200 });
      }
      if (String(url).includes("/c2c/upload")) {
        return new Response("", {
          status: 200,
          headers: { "x-encrypted-param": "download-param" },
        });
      }
      return new Response(JSON.stringify({ ret: 0 }), { status: 200 });
    },
  });
  try {
    await api.sendImageFile(
      { token: "token", baseUrl: "https://ilinkai.weixin.qq.com" },
      "user@im.wechat",
      imageFile,
      "context",
      "delivery-1",
    );
    const uploadRequest = requests.find((item) => item.url.includes("/c2c/upload"));
    assert.ok(uploadRequest.options.body.byteLength > pngHeader.length);
    const sendRequest = requests.find((item) => item.url.includes("sendmessage"));
    const payload = JSON.parse(sendRequest.options.body);
    assert.equal(payload.msg.item_list[0].type, 2);
    assert.equal(payload.msg.item_list[0].image_item.media.encrypt_query_param, "download-param");
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("Weixin send rejects a non-zero errcode even when ret is zero", async () => {
  const api = new WeixinApi({
    channelVersion: "2.4.6",
    botAgent: "VeraAgent/0.8.0",
    ilinkAppId: "bot",
    weixinBaseUrl: "https://ilinkai.weixin.qq.com",
  }, () => {}, {
    fetchImpl: async () => new Response(JSON.stringify({
      ret: 0,
      errcode: -14,
      errmsg: "context token expired",
    }), { status: 200 }),
  });

  await assert.rejects(
    api.sendText(
      { token: "token", baseUrl: "https://ilinkai.weixin.qq.com" },
      "user@im.wechat",
      "hello",
      "expired-context",
      "delivery-error",
    ),
    /errcode=-14.*context token expired/,
  );
});
