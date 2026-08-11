import crypto from "node:crypto";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function startOfToday() {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

export class JobWorker {
  constructor({ config, storage, agent, weixin, account, log, toolRegistry = null, kinds = [], name = "main", recoverOnStart = true }) {
    this.config = config;
    this.storage = storage;
    this.agent = agent;
    this.weixin = weixin;
    this.account = account;
    this.log = log;
    this.toolRegistry = toolRegistry;
    this.kinds = kinds;
    this.name = name;
    this.recoverOnStart = recoverOnStart;
    this.running = false;
    this.loopPromise = null;
  }

  start() {
    if (this.running) return;
    this.running = true;
    const recovered = this.recoverOnStart ? this.storage.recoverStaleJobs(0) : 0;
    if (recovered) this.log(`恢复了 ${recovered} 个中断任务`);
    this.loopPromise = this.loop();
  }

  async stop() {
    this.running = false;
    await this.loopPromise;
  }

  async loop() {
    while (this.running) {
      const job = this.storage.claimNextJob(this.kinds);
      if (!job) {
        await sleep(250);
        continue;
      }
      await this.process(job);
    }
  }

  async process(job) {
    try {
      if (job.kind === "outbound") await this.processOutbound(job);
      else if (job.kind === "inbound") await this.processInbound(job);
      else if (job.kind === "tool") await this.processTool(job);
      else throw new Error(`Unknown job kind: ${job.kind}`);
      this.storage.completeJob(job.id);
    } catch (error) {
      const summary = String(error?.stack || error).slice(0, 1000);
      if (error?.retryable !== false && job.attempts < this.config.maxJobAttempts) {
        const delay = [2000, 10_000, 30_000][Math.min(job.attempts - 1, 2)];
        this.storage.retryJob(job.id, summary, delay);
        this.log(`任务重试 ${job.id.slice(-12)} (${job.attempts}/${this.config.maxJobAttempts}): ${summary.slice(0, 300)}`);
      } else {
        this.storage.failJob(job.id, summary);
        this.log(`任务失败 ${job.id.slice(-12)}: ${summary.slice(0, 500)}`);
        if (job.kind === "tool") this.enqueueToolFailure(job, error);
        if (job.kind === "inbound") this.enqueueInboundFailure(job, error);
      }
    }
  }

  enqueueInboundFailure(job, error) {
    const id = `inbound-failed:${job.id}`;
    this.storage.enqueueJob({
      id,
      kind: "outbound",
      userId: job.user_id,
      sourceKey: id,
      payload: {
        to: job.user_id,
        contextToken: job.payload.contextToken,
        text: error?.userMessage || "这条消息连续尝试了几次仍未处理成功，已经停止重试，避免重复消耗额度。",
      },
    });
  }

  enqueueToolFailure(job, error) {
    const id = `tool-failed:${job.id}`;
    this.storage.enqueueJob({
      id,
      kind: "outbound",
      userId: job.user_id,
      sourceKey: id,
      payload: {
        to: job.user_id,
        contextToken: job.payload.contextToken,
        text: error?.userMessage || "这个后台任务连续尝试了几次仍没成功，我已经停下了，避免继续消耗额度。",
      },
    });
  }

  async processOutbound(job) {
    const { to, text, contextToken } = job.payload;
    await this.weixin.sendText(this.account, to, text, contextToken, job.id);
    this.log(`主动消息发送成功: ${to.slice(-12)} / ${text.slice(0, 80).replace(/\n/g, " ")}`);
  }

  commandReply(job, text) {
    const normalized = text.trim();
    const lower = normalized.toLowerCase();
    const userId = job.user_id;

    if (lower === "/ping") return "pong";
    if (lower === "/whoami") return `你的微信 Agent ID：${userId}`;
    if (lower === "/help") {
      return [
        "Vera Agent 可用命令：",
        "/ping 测试在线",
        "/new 或 /reset 清除连续会话",
        "/model 查看模型",
        "/model flash 切换日常快速模型",
        "/model pro 切换复杂推理模型",
        "/usage 查看今日 token 用量",
        "/status 查看队列状态",
        "/push-test 60 测试 60 秒后的主动推送",
        "/stock report 生成 A 股观察报告",
        "/stock 600519 分析指定股票",
        "/watch add 600519 加入关注",
        "/watch list 查看关注列表",
        "/stock daily 15:30 设置每日推送",
        "/cancel 取消正在生成的回复",
      ].join("\n");
    }
    if (["/new", "/reset"].includes(lower)) {
      this.agent.reset(userId);
      return "已清除当前连续会话。长期设置和用量记录没有删除。";
    }
    if (lower === "/model") {
      return `当前模型：${this.agent.selectedModel(userId)}\n可用命令：/model flash、/model pro`;
    }
    if (lower === "/model flash" || lower === "/model pro") {
      const mode = lower.endsWith("pro") ? "pro" : "flash";
      const model = this.agent.setModel(userId, mode);
      return `已切换到 ${model}。`;
    }
    if (lower === "/usage") {
      const today = this.storage.getUsageSummary(userId, startOfToday());
      const all = this.storage.getUsageSummary(userId, 0);
      return [
        `今日：${today.requests} 次请求，${today.total_tokens} tokens`,
        `其中输入 ${today.prompt_tokens}，输出 ${today.completion_tokens}`,
        `每日安全上限：${this.config.dailyTokenLimit} tokens`,
        `历史累计：${all.requests} 次请求，${all.total_tokens} tokens`,
      ].join("\n");
    }
    if (lower === "/status") {
      const stats = this.storage.getQueueStats();
      return [
        "Vera Agent 正在运行。",
        `模型：${this.agent.selectedModel(userId)}`,
        `队列：待处理 ${stats.pending || 0}，处理中 ${stats.running || 0}，失败 ${stats.failed || 0}`,
      ].join("\n");
    }
    if (lower.startsWith("/stock daily")) {
      return this.stockScheduleReply(job, normalized);
    }
    if (lower.startsWith("/push-test")) {
      const rawSeconds = Number.parseInt(normalized.split(/\s+/)[1] || "60", 10);
      const seconds = Math.min(Math.max(Number.isFinite(rawSeconds) ? rawSeconds : 60, 5), 3600);
      const outboundId = `push-test:${job.id}`;
      this.storage.enqueueJob({
        id: outboundId,
        kind: "outbound",
        userId,
        sourceKey: outboundId,
        availableAt: Date.now() + seconds * 1000,
        payload: {
          to: userId,
          contextToken: job.payload.contextToken,
          text: `主动推送测试成功：这条消息是在 ${seconds} 秒后由后台任务发出的。`,
        },
      });
      return `已安排 ${seconds} 秒后的主动推送测试。`;
    }
    return "";
  }

  stockScheduleReply(job, text) {
    const userId = job.user_id;
    const arg = text.trim().split(/\s+/)[2]?.toLowerCase() || "status";
    const current = this.storage.getSchedule(userId, "stock_daily");
    const fallbackTime = current?.time_local || this.config.stockDailyDefaultTime;
    if (arg === "off") {
      this.storage.setSchedule(userId, "stock_daily", { enabled: false, timeLocal: fallbackTime });
      return "已关闭每日股票推送。";
    }
    if (arg === "on") {
      this.storage.setSchedule(userId, "stock_daily", { enabled: true, timeLocal: fallbackTime });
      return `每日股票推送已开启，时间是 ${fallbackTime}。`;
    }
    if (/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(arg)) {
      this.storage.setSchedule(userId, "stock_daily", { enabled: true, timeLocal: arg });
      return `每日股票推送已开启，时间改为 ${arg}。交易日才会发送。`;
    }
    if (arg === "status") {
      if (!current?.enabled) return `每日股票推送目前关闭。设置示例：/stock daily ${fallbackTime}`;
      return `每日股票推送已开启：${current.time_local}，交易日发送。`;
    }
    return "时间格式不对。示例：/stock daily 15:30，或 /stock daily off。";
  }

  async processInbound(job) {
    const { text, contextToken } = job.payload;
    let result = job.result;

    await this.weixin.sendTyping(this.account, job.user_id, contextToken, 1);
    try {
      if (!result?.reply) {
        let reply = this.commandReply(job, text);
        let model = "local";
        let usage = {};
        let latencyMs = 0;
        if (!reply) {
          const route = this.toolRegistry?.route(text);
          if (route) {
            const toolId = `tool:${job.id}`;
            this.storage.enqueueJob({
              id: toolId,
              kind: "tool",
              userId: job.user_id,
              sourceKey: toolId,
              payload: {
                tool: route.tool,
                action: route.action,
                args: route.args || {},
                originalText: text,
                contextToken,
              },
            });
            reply = route.acknowledgement || "任务已经进入后台队列，完成后我会单独发给你。";
          }
        }
        if (!reply) {
          let prompt = text;
          let forcedModel;
          let thinking;
          if (/^\/deep\s+/i.test(prompt)) {
            prompt = prompt.replace(/^\/deep\s+/i, "").trim();
            forcedModel = this.config.complexModel;
            thinking = true;
          } else if (/^\/fast\s+/i.test(prompt)) {
            prompt = prompt.replace(/^\/fast\s+/i, "").trim();
            forcedModel = this.config.chatModel;
            thinking = false;
          }
          const response = await this.agent.chat(job.user_id, prompt, {
            model: forcedModel,
            thinking,
          });
          if (response.cancelled) {
            this.storage.saveJobResult(job.id, { cancelled: true });
            return;
          }
          reply = response.text;
          model = response.model;
          usage = response.usage;
          latencyMs = response.latencyMs;
        }
        result = { reply, model, usage, latencyMs };
        this.storage.saveJobResult(job.id, result);
      }

      await this.weixin.sendText(
        this.account,
        job.user_id,
        result.reply,
        contextToken,
        job.id,
      );
      const tokenCount = Number(result.usage?.total_tokens || 0);
      this.log(
        `回复成功: ${job.user_id.slice(-12)} | ${result.model} | ${tokenCount} tokens | ` +
          `${result.reply.slice(0, 100).replace(/\n/g, " ")}`,
      );
    } finally {
      await this.weixin.sendTyping(this.account, job.user_id, contextToken, 2);
    }
  }

  async processTool(job) {
    if (!this.toolRegistry) throw new Error("工具工作器没有配置 ToolRegistry");
    let result = job.result;
    if (!result) {
      const started = Date.now();
      result = await this.toolRegistry.run(job.payload.tool, { job });
      result.latencyMs = Number(result.latencyMs || Date.now() - started);
      if (result.skipped || !result.reply) {
        this.storage.saveJobResult(job.id, result);
      } else {
        this.storage.saveToolResultAndTurn(job.id, result, {
          userId: job.user_id,
          userText: job.payload.originalText || `[后台任务: ${job.payload.tool}]`,
          assistantText: result.reply,
          model: result.model || `tool:${job.payload.tool}`,
          usage: result.usage || {},
          latencyMs: result.latencyMs,
        });
      }
    }
    if (result.skipped || !result.reply) {
      this.log(`后台任务跳过: ${job.payload.tool} / ${job.user_id.slice(-12)}`);
      return;
    }
    const outboundId = `tool-result:${job.id}`;
    this.storage.enqueueJob({
      id: outboundId,
      kind: "outbound",
      userId: job.user_id,
      sourceKey: outboundId,
      payload: {
        to: job.user_id,
        contextToken: job.payload.contextToken,
        text: result.reply,
      },
    });
    this.log(`后台任务完成: ${job.payload.tool} / ${job.user_id.slice(-12)} / ${result.latencyMs}ms`);
  }
}

export function newOutboundId(prefix = "outbound") {
  return `${prefix}:${crypto.randomUUID()}`;
}
