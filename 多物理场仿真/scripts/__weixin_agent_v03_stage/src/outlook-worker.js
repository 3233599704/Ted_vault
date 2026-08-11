import crypto from "node:crypto";
import path from "node:path";

import { CONFIG } from "./config.js";
import { createLogger } from "./logger.js";
import { OutlookAuthClient, OutlookAuthError } from "./outlook-auth-client.js";
import { OutlookMailboxPoller } from "./outlook-mail.js";
import { profilePaths } from "./profiles.js";
import { AgentStorage } from "./storage.js";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jobId(prefix, value) {
  const digest = crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 32);
  return `${prefix}:${digest}`;
}

const log = createLogger(path.join(CONFIG.projectDir, "logs", "outlook.log"));

async function main() {
  if (!CONFIG.outlookClientId || !CONFIG.outlookTenantId || !CONFIG.outlookMailbox) {
    throw new Error("Outlook 配置不完整，缺少 Client ID、Tenant ID 或邮箱地址");
  }
  const target = profilePaths(CONFIG.outlookWeixinProfile, CONFIG.projectDir);
  const storage = new AgentStorage(target.databaseFile);
  const auth = new OutlookAuthClient(CONFIG);
  const poller = new OutlookMailboxPoller(CONFIG, auth);
  let running = true;
  const stop = () => { running = false; };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  log(`Outlook 邮件监听启动: ${CONFIG.outlookMailbox} -> ${CONFIG.outlookWeixinProfile}`);

  try {
    while (running) {
      try {
        const result = await poller.poll();
        if (result.initial) log("Outlook 初始基线已建立，不推送历史邮件");
        for (const message of result.messages) {
          const context = storage.getLatestContext();
          if (!context?.context_token) {
            log(`跳过邮件 ${message.id}: 目标微信 profile 还没有可用会话`);
            continue;
          }
          const id = jobId("outlook", message.id);
          const inserted = storage.enqueueJob({
            id,
            kind: "outbound",
            userId: context.user_id,
            sourceKey: id,
            payload: {
              to: context.user_id,
              contextToken: context.context_token,
              text: message.text,
            },
          });
          if (inserted) log(`Outlook 新邮件已入队: ${message.id.slice(-12)}`);
        }
        await sleep(CONFIG.outlookPollMs);
      } catch (error) {
        log(`Outlook 轮询失败: ${String(error?.stack || error).slice(0, 1000)}`);
        if (error instanceof OutlookAuthError && error.reauthRequired) {
          const context = storage.getLatestContext();
          const day = new Date().toISOString().slice(0, 10);
          if (context?.context_token) {
            const id = jobId("outlook-auth", day);
            storage.enqueueJob({
              id,
              kind: "outbound",
              userId: context.user_id,
              sourceKey: id,
              payload: {
                to: context.user_id,
                contextToken: context.context_token,
                text: "Outlook 授权已失效，需要在电脑上重新运行 npm run outlook:login。",
              },
            });
          }
          await sleep(5 * 60_000);
        } else {
          await sleep(Math.min(CONFIG.outlookPollMs, 60_000));
        }
      }
    }
  } finally {
    storage.close();
    log("Outlook 邮件监听已停止");
  }
}

main().catch((error) => {
  log(`Outlook 致命错误: ${error?.stack || error}`);
  process.exitCode = 1;
});
