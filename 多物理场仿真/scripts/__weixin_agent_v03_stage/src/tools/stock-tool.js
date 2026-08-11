import path from "node:path";

import { runProcess } from "../process-runner.js";

const STOCK_WORDS = ["股票", "个股", "股价", "走势", "趋势", "模拟盘", "行情", "分析", "看看", "关注"];
const CODE_RE = /(?<!\d)((?:000|001|002|003|300|301|600|601|603|605|688|43\d|8\d{2}|920)\d{3})(?!\d)/g;

function stockCodes(text, requireContext = true) {
  if (requireContext && !STOCK_WORDS.some((word) => text.includes(word))) return [];
  return [...new Set([...text.matchAll(CODE_RE)].map((match) => match[1]))];
}

export class StockTool {
  constructor(config, options = {}) {
    this.name = "stock";
    this.config = config;
    this.runImpl = options.runImpl || runProcess;
  }

  route(text) {
    const normalized = text.trim();
    const watch = normalized.match(/^\/watch\s+(add|remove)\s+(.+)$/i);
    if (watch) {
      const codes = stockCodes(watch[2], false);
      if (!codes.length) return null;
      return {
        action: watch[1].toLowerCase() === "add" ? "watch_add" : "watch_remove",
        args: { codes },
        acknowledgement: "好，我去更新你的股票关注列表。",
      };
    }
    if (/^\/watch(?:\s+list)?$/i.test(normalized)) {
      return { action: "watch_list", args: {}, acknowledgement: "我查一下你的股票关注列表。" };
    }
    if (/^\/stock\s+watch$/i.test(normalized)) {
      return { action: "watch_report", args: {}, acknowledgement: "我去更新你关注股票的最新情况，稍后发给你。" };
    }
    if (/^\/stock\s+(report|today|market)$/i.test(normalized) || /^(今日|今天).*(股票|A股).*(报告|行情|关注)/i.test(normalized)) {
      return { action: "report", args: {}, acknowledgement: "我去抓取并分析最新 A 股数据，结果会单独发给你。" };
    }
    if (/^\/stock\b/i.test(normalized)) {
      const codes = stockCodes(normalized, false);
      if (codes.length) return {
        action: "code_report",
        args: { codes },
        acknowledgement: `我去查 ${codes.join("、")} 的行情和风险点，稍后发给你。`,
      };
    }
    const codes = stockCodes(normalized, true);
    if (codes.length) return {
      action: "code_report",
      args: { codes },
      acknowledgement: `我去分析 ${codes.join("、")}，结果会单独发给你。`,
    };
    return null;
  }

  async execute({ job }) {
    const payload = {
      action: job.payload.action,
      args: job.payload.args || {},
      user_id: job.user_id,
      watchlist_path: this.config.stockWatchlistFile,
    };
    let result;
    try {
      result = await this.runImpl(
        this.config.pythonCommand,
        [path.join(this.config.toolsDir, "stock_cli.py")],
        {
          cwd: this.config.projectDir,
          input: JSON.stringify(payload),
          timeoutMs: this.config.stockToolTimeoutMs,
          env: { ...process.env, PYTHONIOENCODING: "utf-8" },
        },
      );
    } catch (error) {
      const wrapped = new Error(`股票工具执行失败: ${String(error.message || error)}`);
      wrapped.userMessage = "股票数据这次没取成功，公开行情接口可能暂时不可用。稍后可以再发一次股票代码。";
      throw wrapped;
    }
    let parsed;
    try {
      parsed = JSON.parse(result.stdout);
    } catch {
      throw new Error(`股票工具返回了无法解析的数据: ${result.stdout.slice(0, 500)}`);
    }
    if (!parsed.ok) {
      const error = new Error(parsed.error || "股票工具执行失败");
      error.userMessage = `股票数据获取失败：${parsed.error || "未知错误"}`;
      throw error;
    }
    return {
      reply: parsed.text || "",
      skipped: Boolean(parsed.skipped),
      model: `stock:${parsed.provider || "public-data"}`,
      usage: {},
    };
  }
}
