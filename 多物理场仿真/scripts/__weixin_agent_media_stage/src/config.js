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

function toRatio(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0.25 && parsed <= 0.95 ? parsed : fallback;
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

fs.mkdirSync(STATE_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });
fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

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
  stockPortfolioFile: PATHS.stockPortfolioFile,
  stockJournalFile: path.join(PROJECT_DIR, "records", "stocks", PROFILE, "模拟盘复盘.md"),
  douyinCookieFile:
    process.env.DOUYIN_COOKIE_FILE || path.join(PROJECT_DIR, "state", "douyin-cookies.txt"),
  douyinArchiveFile:
    process.env.DOUYIN_ARCHIVE_FILE || path.join(PROJECT_DIR, "records", "douyin", "抖音视频总结.md"),

  channelVersion: "2.4.6",
  ilinkAppId: "bot",
  weixinBaseUrl: process.env.WEIXIN_BASE_URL || "https://ilinkai.weixin.qq.com",
  botType: process.env.WEIXIN_BOT_TYPE || "3",
  botAgent: process.env.WEIXIN_BOT_AGENT || "VeraAgent/0.9.0",
  sendTyping: toBool(process.env.WEIXIN_SEND_TYPING, true),
  allowedUsers: new Set(
    String(process.env.WEIXIN_ALLOWED_USERS || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  ),
  codexEnabled: toBool(process.env.WEIXIN_CODEX_ENABLED, true),
  codexAdminUsers: new Set(
    String(process.env.WEIXIN_CODEX_ADMIN_USERS || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  ),
  codexModel: process.env.WEIXIN_CODEX_MODEL || "gpt-5.6-sol",
  codexReasoningEffort: process.env.WEIXIN_CODEX_REASONING || "high",
  codexWorkspaceDir:
    process.env.WEIXIN_CODEX_WORKSPACE || path.join(PROJECT_DIR, "codex-workspace", PROFILE),
  codexDiagnosticTimeoutMs: toPositiveInt(
    process.env.WEIXIN_CODEX_TIMEOUT_MS,
    5 * 60_000,
  ),
  codexMaxLogLines: toPositiveInt(process.env.WEIXIN_CODEX_MAX_LOG_LINES, 300),
  codexMaxReplyChars: toPositiveInt(process.env.WEIXIN_CODEX_MAX_REPLY_CHARS, 6000),

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
  messageBurstQuietMs: toPositiveInt(process.env.WEIXIN_BURST_QUIET_MS, 4_000),
  messageBurstMaxWaitMs: toPositiveInt(process.env.WEIXIN_BURST_MAX_WAIT_MS, 30_000),
  messageBurstMaxMessages: toPositiveInt(process.env.WEIXIN_BURST_MAX_MESSAGES, 12),
  resumeStabilizeMs: toPositiveInt(process.env.WEIXIN_RESUME_STABILIZE_MS, 8000),
  suspendGapMs: toPositiveInt(process.env.WEIXIN_SUSPEND_GAP_MS, 90_000),
  weixinCdnBaseUrl:
    process.env.WEIXIN_CDN_BASE_URL || "https://novac2c.cdn.weixin.qq.com/c2c",
  weixinMediaTimeoutMs: toPositiveInt(process.env.WEIXIN_MEDIA_TIMEOUT_MS, 30_000),
  weixinImageMaxBytes: toPositiveInt(process.env.WEIXIN_IMAGE_MAX_BYTES, 15 * 1024 * 1024),
  weixinVoiceMaxBytes: toPositiveInt(process.env.WEIXIN_VOICE_MAX_BYTES, 8 * 1024 * 1024),
  weixinMaxImages: toPositiveInt(process.env.WEIXIN_MAX_IMAGES, 4),
  weixinMaxVoices: toPositiveInt(process.env.WEIXIN_MAX_VOICES, 2),
  weixinOutboundImageMaxBytes: toPositiveInt(
    process.env.WEIXIN_OUTBOUND_IMAGE_MAX_BYTES,
    8 * 1024 * 1024,
  ),
  stickersDir: path.join(PROJECT_DIR, "stickers"),
  customStickersDir: path.join(PROJECT_DIR, "stickers", "custom"),
  customStickerCacheDir: path.join(PROJECT_DIR, "stickers", "cache"),
  customStickersEnabled: toBool(process.env.CUSTOM_STICKERS_ENABLED, true),
  customStickerContentScale: toRatio(process.env.CUSTOM_STICKER_CONTENT_SCALE, 0.9),
  customStickerVisualFill: toRatio(process.env.CUSTOM_STICKER_VISUAL_FILL, 0.82),
  customStickerCanvasSize: toPositiveInt(process.env.CUSTOM_STICKER_CANVAS_SIZE, 512),

  pythonCommand: process.env.WEIXIN_PYTHON || "py",
  stockToolTimeoutMs: toPositiveInt(process.env.STOCK_TOOL_TIMEOUT_MS, 180_000),
  stockDailyDefaultTime: process.env.STOCK_DAILY_DEFAULT_TIME || "15:30",

  mimoApiKey: process.env.MIMO_API_KEY || process.env.VISION_API_KEY || "",
  mimoApiUrl:
    process.env.MIMO_API_URL ||
    process.env.VISION_API_URL ||
    "https://api.xiaomimimo.com/v1/chat/completions",
  mimoVideoModel: process.env.MIMO_VIDEO_MODEL || "mimo-v2.5",
  mimoVisionModel: process.env.MIMO_VISION_MODEL || "mimo-v2.5",
  mimoAsrModel: process.env.MIMO_ASR_MODEL || "mimo-v2.5-asr",
  mimoVisionMaxTokens: toPositiveInt(process.env.MIMO_VISION_MAX_TOKENS, 1200),
  mimoAsrMaxBase64Bytes: toPositiveInt(
    process.env.MIMO_ASR_MAX_BASE64_BYTES,
    10 * 1024 * 1024,
  ),
  mimoTimeoutMs: toPositiveInt(process.env.MIMO_TIMEOUT_MS, 180_000),
  douyinMaxBytes: toPositiveInt(process.env.DOUYIN_MAX_BYTES, 35 * 1024 * 1024),
  douyinDebugUrl: process.env.DOUYIN_DEBUG_URL || "http://127.0.0.1:9223",
  douyinAutoSyncCookies: toBool(process.env.DOUYIN_AUTO_SYNC_COOKIES, true),
  douyinChromePath:
    process.env.DOUYIN_CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  douyinBrowserProfileDir:
    process.env.DOUYIN_BROWSER_PROFILE || path.join(PROJECT_DIR, "state", "douyin-browser"),
});
