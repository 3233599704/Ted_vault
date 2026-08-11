import assert from "node:assert/strict";
import test from "node:test";

import { ToolRegistry } from "../src/tool-registry.js";
import { DouyinTool, extractDouyinUrl } from "../src/tools/douyin-tool.js";
import { StockTool } from "../src/tools/stock-tool.js";

const config = {
  pythonCommand: "py",
  projectDir: ".",
  toolsDir: "tools",
  stockWatchlistFile: "state/watch.json",
  stockToolTimeoutMs: 1000,
  mimoApiKey: "test-key",
  mimoApiUrl: "https://api.example.test/chat/completions",
  mimoVideoModel: "mimo-v2.5",
  mimoTimeoutMs: 1000,
  douyinMaxBytes: 1024,
  downloadsDir: ".",
};

test("tool registry routes only allowed Douyin hosts", () => {
  const registry = new ToolRegistry([new DouyinTool(config), new StockTool(config)]);
  const route = registry.route("帮我总结 https://v.douyin.com/abc123/ 谢谢");
  assert.equal(route.tool, "douyin");
  assert.equal(route.args.url, "https://v.douyin.com/abc123/");
  assert.equal(extractDouyinUrl("https://example.com/douyin.com/video"), "");
});

test("stock routes explicit commands and avoids unrelated six digit numbers", () => {
  const stock = new StockTool(config);
  assert.deepEqual(stock.route("/stock 600519").args.codes, ["600519"]);
  assert.deepEqual(stock.route("分析股票 000001 和 600519").args.codes, ["000001", "600519"]);
  assert.equal(stock.route("验证码是 600519"), null);
  assert.equal(stock.route("/stock daily 15:30"), null);
});

test("Douyin tool sends resolved video URL to MiMo", async () => {
  let requestBody;
  const tool = new DouyinTool(config, {
    runImpl: async () => ({
      code: 0,
      stdout: JSON.stringify({
        title: "测试视频",
        uploader: "测试作者",
        webpage_url: "https://www.douyin.com/video/1",
        url: "https://cdn.example.test/video.mp4",
      }),
      stderr: "",
    }),
    fetchImpl: async (_url, options) => {
      requestBody = JSON.parse(options.body);
      return new Response(JSON.stringify({
        choices: [{ message: { content: "一句话结论\n1. 要点" } }],
        usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      }), { status: 200 });
    },
  });
  const result = await tool.execute({
    job: { payload: { args: { url: "https://v.douyin.com/abc/" } } },
  });
  assert.equal(requestBody.model, "mimo-v2.5");
  assert.equal(requestBody.messages[0].content[1].video_url.url, "https://cdn.example.test/video.mp4");
  assert.match(result.reply, /测试视频/);
  assert.equal(result.usage.total_tokens, 15);
});
