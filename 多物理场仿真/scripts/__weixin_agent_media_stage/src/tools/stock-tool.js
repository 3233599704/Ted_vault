import path from "node:path";

import { runProcess } from "../process-runner.js";

const STOCK_WORDS = ["股票", "个股", "股价", "走势", "趋势", "模拟盘", "行情", "分析", "看看", "关注"];
const CODE_RE = /(?<!\d)((?:000|001|002|003|159|300|301|600|601|603|605|688|43\d|8\d{2}|920)\d{3})(?!\d)/g;

function stockCodes(text, requireContext = true) {
  if (requireContext && !STOCK_WORDS.some((word) => text.includes(word))) return [];
  return [...new Set([...text.matchAll(CODE_RE)].map((match) => match[1]))];
}

function positiveNumber(raw) {
  const value = Number(String(raw || "").replace(/,/g, ""));
  return Number.isFinite(value) && value > 0 ? value : null;
}

function naturalCapital(text) {
  const match = text.match(/(?:模拟盘)?本金\s*(?:是|为|设为|设置为|设置成)?\s*([0-9]+(?:\.[0-9]+)?)\s*(万|元)?/);
  if (!match) return null;
  const value = positiveNumber(match[1]);
  if (!value) return null;
  return match[2] === "万" ? value * 10_000 : value;
}

function wantsPortfolioCheck(text) {
  return (
    /(?:看看|看下|看一看|检查|分析).{0,8}(?:我的)?(?:模拟盘|股票)?持仓/.test(text) ||
    /(?:我的)?(?:模拟盘|股票)?持仓.{0,10}(?:怎么样|怎么办|要不要卖|该不该卖|怎么处理)/.test(text) ||
    /(?:我现在|目前).{0,4}(?:持仓|仓位).{0,10}(?:怎么样|怎么办)/.test(text)
  );
}

function wantsStockPicks(text) {
  return (
    /(?:看看|看下|看一看|查查|分析一下).{0,8}(?:今天|今日)(?:的)?(?:股票|A股)/.test(text) ||
    /(?:今天|今日)(?:的)?(?:股票|A股).{0,10}(?:看看|看下|看一看|推荐|选|挑|值得关注|可以买|能买)/.test(text) ||
    /(?:今天|今日).{0,4}(?:有没有|有什么).{0,10}(?:值得|可以买|能买|适合).{0,6}(?:股票|个股)/.test(text) ||
    /(?:推荐|选|挑).{0,8}(?:几只|一些)?(?:股票|个股|候选股)/.test(text) ||
    /(?:有什么|有没有).{0,8}(?:股票|个股).{0,10}(?:值得|可以买|能买|适合|关注)/.test(text)
  );
}

function wantsMarketReport(text) {
  return (
    /(?:目前|现在|今天|今日).{0,8}(?:股票市场|A股市场|大盘|市场行情).{0,10}(?:咋样|怎么样|如何|情况|走势|看看|看一下)/.test(text) ||
    /(?:检索|查一下|看一下|看看).{0,10}(?:今天|今日).{0,6}(?:股票市场|A股市场|大盘|市场行情)/.test(text)
  );
}

const RESEARCH_THEMES = [
  [/(?:AI\s*\+\s*AR|AR\s*医疗|医疗\s*AR|全息.*手术|手术.*全息)/i, "AI+AR医疗"],
  [/(?:物理\s*AI|Physical\s*AI)/i, "Physical AI"],
  [/世界模型/i, "世界模型"],
  [/(?:人形机器人|机器人)/i, "机器人"],
  [/(?:能源|电力|用电|供电|核电|水电|火电|储能|电网|特高压)/i, "AI能源与电力"],
  [/(?:AI|人工智能).{0,10}(?:上游|基础设施|算力|数据中心|服务器|液冷|光模块|芯片)|(?:上游|基础设施).{0,10}(?:AI|人工智能)/i, "AI上游基础设施"],
];

function themesFromText(text) {
  return RESEARCH_THEMES
    .filter(([pattern]) => pattern.test(text))
    .map(([, label]) => label);
}

function contextualStockWatch(text, history = []) {
  const ongoing = /(?:盯紧|盯着|盯盘|持续关注|继续关注|长期关注|跟踪|加入关注|放进自选)/.test(text);
  const stockReference = /(?:这些|这几只|上述|刚才|股票|个股|方向|板块|赛道|\d{6})/.test(text);
  if (!ongoing || !stockReference) return null;

  const directCodes = stockCodes(text, false);
  if (directCodes.length) {
    return { codes: directCodes, themes: themesFromText(text) };
  }

  const directThemes = themesFromText(text);
  if (directThemes.length) {
    return { codes: [], themes: directThemes, thematic: true };
  }

  const recent = [...history].reverse().slice(0, 10);
  for (const message of recent) {
    if (message.role !== "assistant") continue;
    const codes = stockCodes(String(message.content || ""), false);
    if (!codes.length) continue;
    return {
      codes,
      themes: [...new Set([...themesFromText(message.content), ...themesFromText(text)])],
    };
  }
  return { codes: [], themes: themesFromText(text) };
}

function wantsPortfolioMonitoring(text) {
  const portfolioContext = /(?:股票|持仓|仓位|模拟盘|减仓|卖出|候选股|值得买)/.test(text);
  const ongoingIntent = /(?:每天|每日|以后|后续|帮我看着|盯着|盯盘|持续|推送|提醒)/.test(text);
  const analysisIntent = /(?:分析|减仓|卖出|操作|值得买|候选|推荐)/.test(text);
  const contextualSave = /(?:存|保存|记|记录).{0,12}(?:持仓|模拟盘|卖出|减仓)/.test(text)
    || /存到.{0,8}记录.{0,12}(?:卖出|减仓)/.test(text);
  return contextualSave || (portfolioContext && ongoingIntent && analysisIntent);
}

function tradePrice(text) {
  const patterns = [
    /(?:实际)?(?:成交价|卖出价|价格)\s*(?:是|为|[:：])?\s*([0-9]+(?:\.[0-9]+)?)/,
    /(?:按|以)\s*([0-9]+(?:\.[0-9]+)?)\s*元?/,
    /([0-9]+(?:\.[0-9]+)?)\s*元?\s*(?:卖完|全卖|全部卖|清仓)/,
  ];
  for (const pattern of patterns) {
    const value = positiveNumber(text.match(pattern)?.[1]);
    if (value) return value;
  }
  return null;
}

function naturalFullClose(text) {
  if (!/(?:卖完(?:了)?|全(?:部)?卖(?:掉|了|出)?|清仓(?:了)?)/.test(text)) return null;
  if (/(?:没(?:有)?卖完|还没(?:有)?.{0,6}(?:卖完|全卖|清仓)|没有.{0,6}(?:全卖|清仓))/.test(text)) return null;
  if (
    /(?:要不要|该不该|是否|是不是.{0,6}(?:该|要)|建议|可以|能不能|想|准备|打算|考虑).{0,16}(?:卖完|全(?:部)?卖|清仓)/.test(text)
    || /(?:卖完|全(?:部)?卖|清仓).{0,4}(?:吗|好不好|合适不合适|合适吗)[？?]?$/.test(text)
  ) return null;
  return { query: text, price: tradePrice(text) };
}

function naturalCloseSettlement(text) {
  if (!/(?:实际)?(?:成交价|卖出价)/.test(text)) return null;
  const price = tradePrice(text);
  return price ? { query: text, price } : null;
}

export function parsePaperTrade(text) {
  const normalized = String(text || "").trim();
  const command = normalized.match(
    /^\/paper\s+(buy|sell)\s+([0-9]{6})\s+([0-9]+)\s+([0-9]+(?:\.[0-9]+)?)$/i,
  );
  if (command) {
    return {
      side: command[1].toLowerCase(),
      code: command[2],
      quantity: Number(command[3]),
      price: Number(command[4]),
    };
  }
  const side = /(?:卖了|卖出|减仓)/.test(normalized)
    ? "sell"
    : /(?:买了|买入|建仓|加仓)/.test(normalized)
      ? "buy"
      : "";
  if (!side) return null;
  const code = stockCodes(normalized, false)[0];
  const quantity = positiveNumber(normalized.match(/([0-9][0-9,]*)\s*股/)?.[1]);
  const pricePatterns = [
    /(?:买入价|卖出价|成交价|成本价|价格|每股)\s*(?:是|为|[:：])?\s*([0-9]+(?:\.[0-9]+)?)/,
    /以\s*([0-9]+(?:\.[0-9]+)?)\s*元?\s*(?:买入|买了|卖出|卖了)/,
    /([0-9]+(?:\.[0-9]+)?)\s*元(?:每股|\/股)?/,
  ];
  let price = null;
  for (const pattern of pricePatterns) {
    price = positiveNumber(normalized.match(pattern)?.[1]);
    if (price) break;
  }
  if (!code || !quantity || !price) return null;
  return { side, code, quantity, price };
}

export class StockTool {
  constructor(config, options = {}) {
    this.name = "stock";
    this.config = config;
    this.runImpl = options.runImpl || runProcess;
  }

  route(text, context = {}) {
    const normalized = text.trim();
    const capital = normalized.match(/^\/paper\s+capital\s+([0-9]+(?:\.[0-9]+)?)$/i);
    const capitalValue = capital ? Number(capital[1]) : naturalCapital(normalized);
    if (capitalValue) {
      return {
        action: "paper_set_capital",
        args: { capital: capitalValue },
        acknowledgement: "好，我记下你的模拟盘本金。",
      };
    }
    if (wantsPortfolioMonitoring(normalized)) {
      return {
        action: "monitor_portfolio",
        args: { includeHoldings: true, includePicks: true },
        silentAcknowledgement: true,
      };
    }
    const contextualWatch = contextualStockWatch(normalized, context.history || []);
    if (contextualWatch) {
      return {
        action: contextualWatch.thematic ? "theme_watch_add" : "watch_add",
        args: contextualWatch,
        silentAcknowledgement: true,
      };
    }
    const fullClose = naturalFullClose(normalized);
    if (fullClose) {
      return {
        action: "paper_close",
        args: fullClose,
        silentAcknowledgement: true,
      };
    }
    const settlement = naturalCloseSettlement(normalized);
    if (settlement) {
      return {
        action: "paper_settle_close",
        args: settlement,
        silentAcknowledgement: true,
      };
    }
    if (/^\/paper(?:\s+(?:portfolio|status))?$/i.test(normalized) || wantsPortfolioCheck(normalized)) {
      return {
        action: "paper_portfolio",
        args: {},
        acknowledgement: "我看看你现在的持仓，该拿着、减一点还是离场，我会把原因一起说清楚。",
      };
    }
    const trade = parsePaperTrade(normalized);
    if (trade) {
      return {
        action: trade.side === "buy" ? "paper_buy" : "paper_sell",
        args: trade,
        acknowledgement: "收到，我来记录这笔模拟交易并更新持仓。",
      };
    }
    if (wantsMarketReport(normalized)) {
      return {
        action: "report",
        args: {},
        acknowledgement: "我去读取今天的真实行情数据，结果单独发给你。",
      };
    }
    if (
      /^\/stock\s+(?:picks|pick|recommend)$/i.test(normalized) ||
      /^(?:今天|今日)?.*(?:选股|候选股|模拟盘推荐)/i.test(normalized) ||
      wantsStockPicks(normalized)
    ) {
      return {
        action: "picks",
        args: {},
        acknowledgement: "我看看今天有没有适合放进模拟盘的，等我一会儿，没达到标准的我不会硬塞给你。",
      };
    }
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
    if (/(?:我关注了哪些股票|看看我的(?:关注列表|自选股)|我的(?:关注列表|自选股)有哪些)/.test(normalized)) {
      return { action: "watch_list", args: {}, acknowledgement: "我看看你之前关注了哪些股票。" };
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

  async execute({ job, storage = null }) {
    if (job.payload.action === "monitor_portfolio") {
      if (!storage) throw new Error("开启持仓监控需要持久化存储");
      const current = storage.getSchedule(job.user_id, "stock_daily");
      const timeLocal = current?.time_local || this.config.stockDailyDefaultTime;
      storage.setSchedule(job.user_id, "stock_daily", {
        enabled: true,
        timeLocal,
        settings: {
          ...(current?.settings || {}),
          includeHoldings: true,
          includePicks: true,
        },
      });
      return {
        reply: [
          `设置好了。以后每个交易日 ${timeLocal} 的股票推送会固定包含：`,
          "1. 逐只检查你现有持仓的盈亏、趋势、仓位和风险线。",
          "2. 触发条件时明确告诉你继续持有、减仓多少或是否离场，并说明原因。",
          "3. 继续扫描新候选；当前仓位不适合再买时，也会直说，不会为了推荐而推荐。",
        ].join("\n"),
        model: "stock:local schedule",
        usage: {},
      };
    }
    if (job.payload.action === "watch_add" && !job.payload.args?.codes?.length) {
      return {
        reply: "我知道你想让我持续关注，但刚才的上下文里没有找到明确股票代码。把股票名称或代码再发我一次，我确认写进关注列表后再告诉你。",
        model: "stock:local clarification",
        usage: {},
      };
    }
    const payload = {
      action: job.payload.action,
      args: job.payload.args || {},
      user_id: job.user_id,
      watchlist_path: this.config.stockWatchlistFile,
      portfolio_path: this.config.stockPortfolioFile,
      journal_path: this.config.stockJournalFile,
      job_id: job.id,
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
    if (job.payload.action === "paper_buy" && storage) {
      const current = storage.getSchedule(job.user_id, "stock_daily");
      if (!current?.enabled) {
        storage.setSchedule(job.user_id, "stock_daily", {
          enabled: true,
          timeLocal: current?.time_local || this.config.stockDailyDefaultTime,
        });
        parsed.text += `\n已自动开启交易日 ${current?.time_local || this.config.stockDailyDefaultTime} 的持仓检查和候选推送。`;
      }
    }
    if (["watch_add", "theme_watch_add"].includes(job.payload.action) && storage) {
      const current = storage.getSchedule(job.user_id, "stock_daily");
      const themes = [...new Set([
        ...(current?.settings?.researchThemes || []),
        ...(job.payload.args?.themes || []),
      ])];
      const timeLocal = current?.time_local || this.config.stockDailyDefaultTime;
      storage.setSchedule(job.user_id, "stock_daily", {
        enabled: true,
        timeLocal,
        settings: {
          ...(current?.settings || {}),
          includeHoldings: current?.settings?.includeHoldings !== false,
          includePicks: current?.settings?.includePicks !== false,
          includeWatchlist: true,
          researchThemes: themes,
        },
      });
      const themeText = job.payload.action === "theme_watch_add"
        ? ""
        : themes.length ? `\n持续关注方向：${themes.join("、")}。` : "";
      parsed.text += `\n已并入每个交易日 ${timeLocal} 的推送；只有真实行情达到信号条件时才会提示你关注，不会把关注直接说成买入建议。${themeText}`;
    }
    return {
      reply: parsed.text || "",
      skipped: Boolean(parsed.skipped),
      model: `stock:${parsed.provider || "public-data"}`,
      usage: {},
    };
  }
}
