import crypto from "node:crypto";
import fs from "node:fs";

import { VeraAgent } from "./agent.js";
import { CONFIG } from "./config.js";
import { JobWorker, newOutboundId } from "./job-worker.js";
import { createLogger } from "./logger.js";
import { DeepSeekProvider } from "./model-provider.js";
import { loadPersona } from "./persona.js";
import { StockScheduler } from "./scheduler.js";
import { AgentStorage } from "./storage.js";
import { ToolRegistry } from "./tool-registry.js";
import { DouyinTool } from "./tools/douyin-tool.js";
import { StockTool } from "./tools/stock-tool.js";
import {
  MESSAGE_TYPE_USER,
  STALE_TOKEN_ERRCODE,
  WeixinApi,
  extractMessageText,
  messageKey,
} from "./weixin-api.js";

const log = createLogger(CONFIG.logFile);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function loadJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function saveJson(filePath, value) {
  const temp = `${filePath}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2), "utf8");
  fs.renameSync(temp, filePath);
}

function loadAccount() {
  return loadJson(CONFIG.accountFile, null);
}

function saveAccount(account) {
  saveJson(CONFIG.accountFile, account);
  try {
    fs.chmodSync(CONFIG.accountFile, 0o600);
  } catch {
    // Best effort on Windows.
  }
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function acquireInstanceLock() {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const handle = fs.openSync(CONFIG.lockFile, "wx");
      fs.writeFileSync(handle, String(process.pid), "utf8");
      const release = () => {
        try { fs.closeSync(handle); } catch {}
        try { fs.rmSync(CONFIG.lockFile, { force: true }); } catch {}
      };
      process.once("exit", release);
      return release;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      const oldPid = Number.parseInt(fs.readFileSync(CONFIG.lockFile, "utf8"), 10);
      if (isProcessAlive(oldPid)) {
        throw new Error(`微信 Agent 已在运行，PID=${oldPid}`);
      }
      fs.rmSync(CONFIG.lockFile, { force: true });
    }
  }
  throw new Error("无法取得微信 Agent 单实例锁。");
}

function inboundJobId(key) {
  const digest = crypto.createHash("sha256").update(key).digest("hex").slice(0, 32);
  return `inbound:${digest}`;
}

async function login() {
  const weixin = new WeixinApi(CONFIG, log);
  const existing = loadAccount();
  const account = await weixin.login({
    localTokenList: existing?.token ? [existing.token] : [],
    existingAccount: existing,
  });
  saveAccount(account);
  saveJson(CONFIG.syncFile, { get_updates_buf: "" });
  log(`微信登录成功: profile=${CONFIG.profile} accountId=${account.accountId}`);
  console.log("\n登录完成。现在可以运行：npm run start");
}

async function start() {
  const account = loadAccount();
  if (!account?.token) throw new Error("还没有微信登录凭据，请先运行 npm run login。");
  const releaseLock = acquireInstanceLock();
  const storage = new AgentStorage(CONFIG.databaseFile);
  const provider = new DeepSeekProvider(CONFIG);
  const weixin = new WeixinApi(CONFIG, log);
  const toolRegistry = new ToolRegistry([
    new DouyinTool(CONFIG),
    new StockTool(CONFIG),
  ]);
  const agent = new VeraAgent({
    config: CONFIG,
    storage,
    provider,
    persona: loadPersona(CONFIG.personaFile),
    log,
  });
  const worker = new JobWorker({
    config: CONFIG,
    storage,
    agent,
    weixin,
    account,
    log,
    toolRegistry,
    kinds: ["inbound", "outbound"],
    name: "chat",
  });
  const toolWorker = new JobWorker({
    config: CONFIG,
    storage,
    agent,
    weixin,
    account,
    log,
    toolRegistry,
    kinds: ["tool"],
    name: "tools",
    recoverOnStart: false,
  });
  const scheduler = new StockScheduler({ storage, log });

  let running = true;
  const pollController = new AbortController();
  const stop = () => {
    running = false;
    pollController.abort();
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);

  log(`Vera 微信 Agent 启动: profile=${CONFIG.profile} accountId=${account.accountId}`);
  log(`模型后端: DeepSeek API / 默认 ${CONFIG.chatModel} / 复杂任务 ${CONFIG.complexModel}`);
  log(`工具层: 抖音总结 ${CONFIG.mimoVideoModel} / 股票公开数据 / 独立后台队列`);
  if (!provider.isConfigured()) log("警告: 尚未找到 DeepSeek API Key");
  if (!CONFIG.allowedUsers.size) log("安全提醒: WEIXIN_ALLOWED_USERS 未设置，当前允许所有联系人调用模型");

  try {
    await weixin.notifyStart(account).catch((error) => {
      log(`notifyStart 失败，已忽略: ${String(error).slice(0, 250)}`);
    });
    worker.start();
    toolWorker.start();
    scheduler.start();

    let sync = loadJson(CONFIG.syncFile, { get_updates_buf: "" }).get_updates_buf || "";
    let nextTimeout = 35_000;
    let failures = 0;
    let modelReadyAt = 0;

    while (running) {
      const pollStartedAt = Date.now();
      try {
        const response = await weixin.getUpdates(
          account,
          sync,
          nextTimeout + 5000,
          pollController.signal,
        );
        const pollElapsed = Date.now() - pollStartedAt;
        const resumedAfterGap = pollElapsed > nextTimeout + CONFIG.suspendGapMs;
        const recoveredFailures = failures;
        if (response.longpolling_timeout_ms > 0) nextTimeout = response.longpolling_timeout_ms;
        const ret = response.ret ?? 0;
        const errcode = response.errcode ?? 0;
        if (ret !== 0 || errcode !== 0) {
          if (ret === STALE_TOKEN_ERRCODE || errcode === STALE_TOKEN_ERRCODE) {
            throw new Error("微信 token 已失效，请重新运行 npm run login。");
          }
          throw new Error(`getUpdates ret=${ret} errcode=${errcode} errmsg=${response.errmsg || ""}`);
        }

        if (recoveredFailures || resumedAfterGap) {
          modelReadyAt = Math.max(modelReadyAt, Date.now() + CONFIG.resumeStabilizeMs);
          log(
            `微信连接已恢复，等待 ${CONFIG.resumeStabilizeMs}ms 后再调用模型` +
              ` (errors=${recoveredFailures}, poll=${pollElapsed}ms)`,
          );
        }
        failures = 0;
        for (const message of response.msgs || []) {
          if (message.message_type && message.message_type !== MESSAGE_TYPE_USER) continue;
          const from = message.from_user_id || "";
          const text = extractMessageText(message);
          const contextToken = message.context_token || "";
          const key = messageKey(message);
          if (!from || !text) {
            log(`跳过非文本消息: from=${from || "unknown"} key=${key}`);
            continue;
          }
          if (CONFIG.allowedUsers.size && !CONFIG.allowedUsers.has(from)) {
            log(`拒绝非白名单用户: ${from.slice(-12)}`);
            continue;
          }

          storage.saveContext(from, contextToken);
          log(`收到微信消息 ${from.slice(-12)}: ${text.slice(0, 120).replace(/\n/g, " ")}`);

          if (text.trim().toLowerCase() === "/cancel") {
            const cancelled = agent.cancel(from);
            await weixin.sendText(
              account,
              from,
              cancelled ? "已取消正在生成的回复。" : "当前没有正在生成的回复。",
              contextToken,
              `cancel:${key}`,
            );
            continue;
          }

          const inserted = storage.enqueueJob({
            id: inboundJobId(key),
            kind: "inbound",
            userId: from,
            sourceKey: key,
            payload: { text, contextToken, receivedAt: Date.now() },
            availableAt: Math.max(Date.now(), modelReadyAt),
          });
          if (inserted) log(`消息已进入任务队列: ${key.slice(-16)}`);
        }

        if (response.get_updates_buf != null && response.get_updates_buf !== "") {
          sync = response.get_updates_buf;
          saveJson(CONFIG.syncFile, { get_updates_buf: sync });
        }
      } catch (error) {
        if (!running || pollController.signal.aborted) break;
        failures += 1;
        modelReadyAt = Math.max(modelReadyAt, Date.now() + CONFIG.resumeStabilizeMs);
        log(`微信轮询异常 (${failures}): ${String(error).slice(0, 500)}`);
        if (/token 已失效/.test(String(error))) throw error;
        await sleep(failures >= 3 ? 30_000 : 2000);
        if (failures >= 3) failures = 0;
      }
    }
  } finally {
    await scheduler.stop();
    await worker.stop();
    await toolWorker.stop();
    await weixin.notifyStop(account).catch(() => {});
    storage.close();
    releaseLock();
    log("Vera 微信 Agent 已停止");
  }
}

function status() {
  const account = loadAccount();
  const storage = new AgentStorage(CONFIG.databaseFile);
  try {
    console.log(JSON.stringify({
      projectDir: CONFIG.projectDir,
      profile: CONFIG.profile,
      loggedIn: Boolean(account?.token),
      accountId: account?.accountId || "",
      baseUrl: account?.baseUrl || CONFIG.weixinBaseUrl,
      savedAt: account?.savedAt || "",
      provider: "deepseek",
      providerConfigured: Boolean(CONFIG.deepseekApiKey),
      defaultModel: CONFIG.chatModel,
      complexModel: CONFIG.complexModel,
      mimoConfigured: Boolean(CONFIG.mimoApiKey),
      mimoVideoModel: CONFIG.mimoVideoModel,
      queue: storage.getQueueStats(),
      logFile: CONFIG.logFile,
    }, null, 2));
  } finally {
    storage.close();
  }
}

function notify() {
  const text = process.argv.slice(3).join(" ").trim();
  if (!text) throw new Error("用法：npm run notify -- \"要发送的内容\"");
  const storage = new AgentStorage(CONFIG.databaseFile);
  try {
    const context = storage.getLatestContext();
    if (!context?.context_token) throw new Error("还没有可用的微信会话 context token。");
    const id = newOutboundId("manual");
    storage.enqueueJob({
      id,
      kind: "outbound",
      userId: context.user_id,
      sourceKey: id,
      payload: {
        to: context.user_id,
        contextToken: context.context_token,
        text,
      },
    });
    console.log(`主动消息已入队：${id}`);
  } finally {
    storage.close();
  }
}

async function main() {
  const command = process.argv[2] || "start";
  if (command === "login") return login();
  if (command === "start") return start();
  if (command === "status") return status();
  if (command === "notify") return notify();
  console.log("Usage: node src/weixin-agent.js <login|start|status|notify>");
  process.exitCode = 2;
}

main().catch((error) => {
  log(`致命错误: ${error?.stack || error}`);
  process.exitCode = 1;
});
