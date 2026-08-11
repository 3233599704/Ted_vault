import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { PROJECT_DIR, normalizeProfileName, profilePaths } from "./profiles.js";

export { PROJECT_DIR };
export const PROFILE = normalizeProfileName(process.env.WEIXIN_PROFILE || "default");
const PATHS = profilePaths(PROFILE, PROJECT_DIR);
export const STATE_DIR = PATHS.stateDir;
export const LOG_DIR = PATHS.logDir;
export const DOWNLOAD_DIR = PATHS.downloadsDir;

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const text = fs.readFileSync(filePath, "utf8");
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx <= 0) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

function loadClaudeSettings() {
  const settingsPath = path.join(os.homedir(), ".claude", "settings.json");
  try {
    return JSON.parse(fs.readFileSync(settingsPath, "utf8"));
  } catch {
    return {};
  }
}

function toPositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function toBool(value, fallback = false) {
  if (value == null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function toOpenAiBaseUrl(raw) {
  const fallback = "https://api.deepseek.com";
  if (!raw) return fallback;
  try {
    const url = new URL(raw);
    url.pathname = url.pathname.replace(/\/anthropic\/?$/i, "") || "/";
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return fallback;
  }
}

loadEnvFile(path.join(PROJECT_DIR, ".env"));
if (PROFILE !== "default") loadEnvFile(path.join(PROJECT_DIR, `.env.${PROFILE}`));
const claudeSettings = loadClaudeSettings();
const claudeEnv = claudeSettings?.env || {};
const OUTLOOK_DIR = path.join(PROJECT_DIR, "state", "outlook");

fs.mkdirSync(STATE_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });
fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
fs.mkdirSync(OUTLOOK_DIR, { recursive: true });

export const CONFIG = Object.freeze({
  projectDir: PROJECT_DIR,
  profile: PROFILE,
  accountFile: PATHS.accountFile,
  syncFile: PATHS.syncFile,
  databaseFile: PATHS.databaseFile,
  lockFile: PATHS.lockFile,
  logFile: PATHS.logFile,
  personaFile: path.join(PROJECT_DIR, "persona.md"),
  toolsDir: path.join(PROJECT_DIR, "tools"),
  downloadsDir: DOWNLOAD_DIR,
  stockWatchlistFile: PATHS.stockWatchlistFile,

  channelVersion: "2.4.6",
  ilinkAppId: "bot",
  weixinBaseUrl: process.env.WEIXIN_BASE_URL || "https://ilinkai.weixin.qq.com",
  botType: process.env.WEIXIN_BOT_TYPE || "3",
  botAgent: process.env.WEIXIN_BOT_AGENT || "VeraAgent/0.4.0",
  sendTyping: toBool(process.env.WEIXIN_SEND_TYPING, true),
  allowedUsers: new Set(
    String(process.env.WEIXIN_ALLOWED_USERS || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  ),

  deepseekApiKey:
    process.env.DEEPSEEK_API_KEY ||
    process.env.ANTHROPIC_AUTH_TOKEN ||
    process.env.ANTHROPIC_API_KEY ||
    claudeEnv.ANTHROPIC_AUTH_TOKEN ||
    claudeEnv.ANTHROPIC_API_KEY ||
    "",
  deepseekBaseUrl: toOpenAiBaseUrl(
    process.env.DEEPSEEK_BASE_URL ||
      process.env.ANTHROPIC_BASE_URL ||
      claudeEnv.ANTHROPIC_BASE_URL,
  ),
  chatModel: process.env.WEIXIN_CHAT_MODEL || "deepseek-v4-flash",
  complexModel: process.env.WEIXIN_COMPLEX_MODEL || "deepseek-v4-pro",
  modelTimeoutMs: toPositiveInt(process.env.MODEL_TIMEOUT_MS, 60_000),
  maxOutputTokens: toPositiveInt(process.env.MODEL_MAX_OUTPUT_TOKENS, 1800),
  maxInputChars: toPositiveInt(process.env.WEIXIN_MAX_INPUT_CHARS, 12_000),
  historyMessages: toPositiveInt(process.env.WEIXIN_HISTORY_MESSAGES, 24),
  historyChars: toPositiveInt(process.env.WEIXIN_HISTORY_CHARS, 24_000),
  dailyTokenLimit: toPositiveInt(process.env.WEIXIN_DAILY_TOKEN_LIMIT, 500_000),
  maxJobAttempts: toPositiveInt(process.env.WEIXIN_MAX_JOB_ATTEMPTS, 3),
  resumeStabilizeMs: toPositiveInt(process.env.WEIXIN_RESUME_STABILIZE_MS, 8000),
  suspendGapMs: toPositiveInt(process.env.WEIXIN_SUSPEND_GAP_MS, 90_000),

  pythonCommand: process.env.WEIXIN_PYTHON || "py",
  stockToolTimeoutMs: toPositiveInt(process.env.STOCK_TOOL_TIMEOUT_MS, 180_000),
  stockDailyDefaultTime: process.env.STOCK_DAILY_DEFAULT_TIME || "15:30",

  mimoApiKey: process.env.MIMO_API_KEY || process.env.VISION_API_KEY || "",
  mimoApiUrl:
    process.env.MIMO_API_URL ||
    process.env.VISION_API_URL ||
    "https://api.xiaomimimo.com/v1/chat/completions",
  mimoVideoModel: process.env.MIMO_VIDEO_MODEL || "mimo-v2.5",
  mimoTimeoutMs: toPositiveInt(process.env.MIMO_TIMEOUT_MS, 180_000),
  douyinMaxBytes: toPositiveInt(process.env.DOUYIN_MAX_BYTES, 35 * 1024 * 1024),

  outlookClientId: process.env.OUTLOOK_CLIENT_ID || "",
  outlookTenantId: process.env.OUTLOOK_TENANT_ID || "",
  outlookMailbox: process.env.OUTLOOK_MAILBOX || "",
  outlookWeixinProfile: process.env.OUTLOOK_WEIXIN_PROFILE || "second",
  outlookPollMs: toPositiveInt(process.env.OUTLOOK_POLL_MS, 120_000),
  outlookMaxBodyChars: toPositiveInt(process.env.OUTLOOK_MAX_BODY_CHARS, 30_000),
  outlookTokenFile: path.join(OUTLOOK_DIR, "token.dpapi"),
  outlookSyncFile: path.join(OUTLOOK_DIR, "sync.json"),
});
