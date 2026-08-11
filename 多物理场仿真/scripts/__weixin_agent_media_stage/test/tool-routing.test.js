import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { ToolRegistry } from "../src/tool-registry.js";
import { DouyinTool, extractDouyinUrl } from "../src/tools/douyin-tool.js";
import { ReminderTool } from "../src/tools/reminder-tool.js";
import { StockTool, parsePaperTrade } from "../src/tools/stock-tool.js";

const config = {
  pythonCommand: "py",
  projectDir: ".",
  toolsDir: "tools",
  stockWatchlistFile: "state/watch.json",
  stockToolTimeoutMs: 1000,
  stockPortfolioFile: "state/paper.json",
  stockJournalFile: "records/stocks.md",
  stockDailyDefaultTime: "15:30",
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

test("ongoing portfolio instructions bypass one-time reminders and reach stocks", () => {
  const registry = new ToolRegistry([new ReminderTool(config), new StockTool(config)]);
  const route = registry.route(
    "以后每天推送时记得带上我持有股票的分析，觉得合适的时候提醒我对持仓做出操作",
  );
  assert.equal(route.tool, "stock");
  assert.equal(route.action, "monitor_portfolio");
});

test("stock routes explicit commands and avoids unrelated six digit numbers", () => {
  const stock = new StockTool(config);
  assert.deepEqual(stock.route("/stock 600519").args.codes, ["600519"]);
  assert.deepEqual(stock.route("分析股票 000001 和 600519").args.codes, ["000001", "600519"]);
  assert.deepEqual(stock.route("分析股票 159018").args.codes, ["159018"]);
  assert.equal(stock.route("验证码是 600519"), null);
  assert.equal(stock.route("/stock daily 15:30"), null);
  assert.equal(
    stock.route("以后每天推送时带上我的持仓分析，该减仓时提醒我操作，也看看有没有值得买的股票").action,
    "monitor_portfolio",
  );
});

test("stock routes current market questions to real data", () => {
  const stock = new StockTool(config);
  assert.equal(stock.route("目前的股票市场咋样").action, "report");
  assert.equal(stock.route("你先去检索一下今天的A股市场行情").action, "report");
});

test("stock resolves contextual watch requests and refuses fake completion", async () => {
  const stock = new StockTool(config, {
    runImpl: async (_command, _args, options) => {
      const request = JSON.parse(options.input);
      assert.equal(request.action, "watch_add");
      assert.deepEqual(request.args.codes, ["300678", "002273", "002241"]);
      return {
        stdout: JSON.stringify({
          ok: true,
          text: "已加入关注：300678、002273、002241",
          provider: "local",
        }),
        stderr: "",
        code: 0,
      };
    },
  });
  const route = stock.route("反正你帮我盯紧这些股票吧，有异动告诉我", {
    history: [{
      role: "assistant",
      content: "AI+AR医疗方向：中科信息（300678）、水晶光电（002273）、歌尔股份（002241）",
    }],
  });
  assert.equal(route.action, "watch_add");
  assert.deepEqual(route.args.codes, ["300678", "002273", "002241"]);
  assert.deepEqual(route.args.themes, ["AI+AR医疗"]);

  let schedule;
  const result = await stock.execute({
    job: { id: "watch-context", user_id: "user", payload: route },
    storage: {
      getSchedule: () => null,
      setSchedule: (_user, _kind, value) => { schedule = value; },
    },
  });
  assert.equal(schedule.enabled, true);
  assert.equal(schedule.settings.includeWatchlist, true);
  assert.deepEqual(schedule.settings.researchThemes, ["AI+AR医疗"]);
  assert.match(result.reply, /已并入每个交易日/);

  const unresolved = stock.route("这些方向以后帮我持续关注", { history: [] });
  const clarification = await stock.execute({
    job: { id: "watch-empty", user_id: "user", payload: unresolved },
    storage: {},
  });
  assert.match(clarification.reply, /没有找到明确股票代码/);
});

test("stock treats a research direction as a theme instead of asking for codes", async () => {
  const stock = new StockTool(config, {
    runImpl: async (_command, _args, options) => {
      const request = JSON.parse(options.input);
      assert.equal(request.action, "theme_watch_add");
      assert.deepEqual(request.args.codes, []);
      assert.deepEqual(request.args.themes, ["AI能源与电力", "AI上游基础设施"]);
      return {
        stdout: JSON.stringify({
          ok: true,
          text: "已开始持续关注：AI能源与电力、AI上游基础设施。",
          provider: "test-market",
        }),
        stderr: "",
        code: 0,
      };
    },
  });
  const route = stock.route(
    "关于能源方面的股票也关注一下，因为现在的AI发展，还有什么类似AI上游的也持续关注",
    { history: [{ role: "assistant", content: "之前聊过别的股票 300678、002273" }] },
  );
  assert.equal(route.action, "theme_watch_add");
  assert.deepEqual(route.args.codes, []);

  let schedule;
  const result = await stock.execute({
    job: { id: "theme-watch", user_id: "user", payload: route },
    storage: {
      getSchedule: () => null,
      setSchedule: (_user, _kind, value) => { schedule = value; },
    },
  });
  assert.deepEqual(schedule.settings.researchThemes, ["AI能源与电力", "AI上游基础设施"]);
  assert.match(result.reply, /已开始持续关注/);
  assert.doesNotMatch(result.reply, /股票代码再发我/);
});

test("paper trades support explicit commands and common Chinese confirmations", () => {
  assert.deepEqual(parsePaperTrade("/paper buy 600519 100 1500.5"), {
    side: "buy", code: "600519", quantity: 100, price: 1500.5,
  });
  assert.deepEqual(parsePaperTrade("我买了 200 股 000001，成交价 12.34"), {
    side: "buy", code: "000001", quantity: 200, price: 12.34,
  });
  assert.deepEqual(parsePaperTrade("600519 我卖出100股，每股1600元"), {
    side: "sell", code: "600519", quantity: 100, price: 1600,
  });
  assert.equal(parsePaperTrade("我想买股票600519"), null);

  const stock = new StockTool(config);
  assert.equal(stock.route("/stock picks").action, "picks");
  assert.equal(stock.route("看看今天的股票").action, "picks");
  assert.equal(stock.route("今天有没有值得关注的股票").action, "picks");
  assert.equal(stock.route("/paper portfolio").action, "paper_portfolio");
  assert.equal(stock.route("看看我的模拟盘持仓").action, "paper_portfolio");
  assert.equal(stock.route("我的模拟盘本金设为10万").args.capital, 100000);
  assert.equal(stock.route("我买了100股600519，价格1500").action, "paper_buy");
  const closeWithPrice = stock.route("我把数字认证 20.90 元全部卖出了");
  assert.equal(closeWithPrice.action, "paper_close");
  assert.equal(closeWithPrice.args.price, 20.9);
  assert.equal(stock.route("格尔软件已经清仓了").action, "paper_close");
  const settlement = stock.route("格尔软件实际成交价 14.88");
  assert.equal(settlement.action, "paper_settle_close");
  assert.equal(settlement.args.price, 14.88);
  assert.equal(stock.route("数字认证要不要全部卖出"), null);
  assert.equal(stock.route("格尔软件我还没有清仓"), null);
});

test("recording the first paper buy enables daily holding checks", async () => {
  let schedule;
  const stock = new StockTool(config, {
    runImpl: async () => ({
      stdout: JSON.stringify({ ok: true, text: "已记录", provider: "local" }),
      stderr: "",
      code: 0,
    }),
  });
  const result = await stock.execute({
    job: {
      id: "buy-job",
      user_id: "user",
      payload: { action: "paper_buy", args: { code: "600519", quantity: 100, price: 1500 } },
    },
    storage: {
      getSchedule: () => null,
      setSchedule: (_user, _kind, value) => { schedule = value; },
    },
  });
  assert.deepEqual(schedule, { enabled: true, timeLocal: "15:30" });
  assert.match(result.reply, /自动开启交易日 15:30/);
});

test("natural portfolio monitoring enables holdings and picks in the daily schedule", async () => {
  let schedule;
  const stock = new StockTool(config);
  const result = await stock.execute({
    job: {
      id: "monitor-job",
      user_id: "user",
      payload: { action: "monitor_portfolio", args: {} },
    },
    storage: {
      getSchedule: () => ({
        enabled: true,
        time_local: "15:30",
        settings: { existing: true },
      }),
      setSchedule: (_user, _kind, value) => { schedule = value; },
    },
  });
  assert.deepEqual(schedule, {
    enabled: true,
    timeLocal: "15:30",
    settings: { existing: true, includeHoldings: true, includePicks: true },
  });
  assert.match(result.reply, /逐只检查你现有持仓/);
  assert.match(result.reply, /继续扫描新候选/);
});

test("Douyin tool sends resolved video URL to MiMo", async () => {
  let requestBody;
  const tool = new DouyinTool(config, {
    captureVideoImpl: async () => {
      const error = new Error("browser unavailable in unit test");
      error.needsBrowserAuth = true;
      throw error;
    },
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

test("Douyin summaries append to one Markdown archive without duplicate job entries", () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "douyin-archive-"));
  const archiveFile = path.join(tempDir, "抖音视频总结.md");
  const tool = new DouyinTool({
    ...config,
    douyinArchiveFile: archiveFile,
  });
  try {
    const result = { text: "一句话结论\n1. 测试要点", usage: { total_tokens: 42 } };
    const metadata = {
      title: "测试视频",
      uploader: "测试作者",
      webpage_url: "https://www.douyin.com/video/123",
    };
    tool.formatResult(result, metadata, metadata.webpage_url, Date.now(), "job-1");
    tool.formatResult(result, metadata, metadata.webpage_url, Date.now(), "job-1");
    const text = fs.readFileSync(archiveFile, "utf8");
    assert.match(text, /# 抖音视频总结/);
    assert.match(text, /测试作者/);
    assert.match(text, /42 tokens/);
    assert.equal(text.match(/vera-douyin-job:job-1/g)?.length, 1);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});
