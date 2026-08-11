import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  PROJECT_DIR,
  discoverProfiles,
  normalizeProfileName,
  profilePaths,
  profileSummary,
} from "./profiles.js";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const AGENT_FILE = path.join(SCRIPT_DIR, "weixin-agent.js");

function mask(value) {
  const text = String(value || "");
  if (text.length <= 18) return text || "-";
  return `${text.slice(0, 6)}...${text.slice(-10)}`;
}

function list() {
  const profiles = discoverProfiles(PROJECT_DIR);
  if (!profiles.length) {
    console.log("还没有已登录的微信账号。");
    return;
  }
  console.table(profiles.map((profile) => {
    const item = profileSummary(profile, PROJECT_DIR);
    return {
      profile: item.profile,
      status: item.running ? `运行中 PID ${item.pid}` : "未运行",
      bot: mask(item.accountId),
      owner: mask(item.userId),
      savedAt: item.savedAt,
    };
  }));
}

function add(rawName) {
  const profile = normalizeProfileName(rawName);
  if (profile === "default") throw new Error("default 是现有账号，请为新账号换一个名称，例如 second。");
  const paths = profilePaths(profile, PROJECT_DIR);
  if (fs.existsSync(paths.accountFile)) throw new Error(`账号 profile ${profile} 已存在。`);
  fs.mkdirSync(paths.stateDir, { recursive: true });
  fs.mkdirSync(paths.logDir, { recursive: true });
  console.log(`正在添加微信账号 profile：${profile}`);
  console.log("请用要新增的微信号扫描下面二维码并在手机上确认。\n");
  const result = spawnSync(
    process.execPath,
    ["--disable-warning=ExperimentalWarning", AGENT_FILE, "login"],
    {
      cwd: PROJECT_DIR,
      env: { ...process.env, WEIXIN_PROFILE: profile },
      stdio: "inherit",
      windowsHide: false,
    },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`账号登录未完成，退出码 ${result.status}`);
  console.log(`\n账号 ${profile} 已保存。正在运行的 supervisor 会自动将它上线。`);
}

function remove(rawName, confirmation) {
  const profile = normalizeProfileName(rawName);
  if (profile === "default") throw new Error("为保护现有账号，default 不能通过该命令移除。");
  if (confirmation !== "--confirm") {
    throw new Error(`这是会移走账号数据的操作。确认后运行：npm run account:remove -- ${profile} --confirm`);
  }
  const summary = profileSummary(profile, PROJECT_DIR);
  if (!summary.accountId) throw new Error(`没有找到账号 profile：${profile}`);
  if (summary.running) throw new Error(`账号 ${profile} 仍在运行，请先重启 supervisor 后再移除。`);
  const paths = profilePaths(profile, PROJECT_DIR);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const backupDir = path.join(PROJECT_DIR, "backup", "accounts", `${profile}-${stamp}`);
  fs.mkdirSync(backupDir, { recursive: true });
  fs.renameSync(paths.stateDir, path.join(backupDir, "state"));
  if (fs.existsSync(paths.logDir)) fs.renameSync(paths.logDir, path.join(backupDir, "logs"));
  console.log(`账号 ${profile} 已移出，数据备份在：${backupDir}`);
}

function main() {
  const command = (process.argv[2] || "list").toLowerCase();
  if (command === "list") return list();
  if (command === "add") return add(process.argv[3] || "");
  if (command === "remove") return remove(process.argv[3] || "", process.argv[4] || "");
  throw new Error("用法：account-manager.js <list|add|remove> [profile]");
}

try {
  main();
} catch (error) {
  console.error(`账号管理失败：${error.message || error}`);
  process.exitCode = 1;
}
