import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { PROJECT_DIR, discoverProfiles, isProcessAlive } from "./profiles.js";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const AGENT_FILE = path.join(SCRIPT_DIR, "weixin-agent.js");
const LOG_DIR = path.join(PROJECT_DIR, "logs");
const STATE_DIR = path.join(PROJECT_DIR, "state");
const LOG_FILE = path.join(LOG_DIR, "supervisor.log");
const LOCK_FILE = path.join(STATE_DIR, "weixin-supervisor.lock");

fs.mkdirSync(LOG_DIR, { recursive: true });
fs.mkdirSync(STATE_DIR, { recursive: true });

function log(message) {
  const now = new Date().toLocaleString("zh-CN", { hour12: false });
  const line = `[${now}] ${message}`;
  console.log(line);
  fs.appendFileSync(LOG_FILE, `${line}\n`, "utf8");
}

function acquireLock() {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const handle = fs.openSync(LOCK_FILE, "wx");
      fs.writeFileSync(handle, String(process.pid), "utf8");
      return () => {
        try { fs.closeSync(handle); } catch {}
        try { fs.rmSync(LOCK_FILE, { force: true }); } catch {}
      };
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      const oldPid = Number.parseInt(fs.readFileSync(LOCK_FILE, "utf8"), 10);
      if (isProcessAlive(oldPid)) throw new Error(`多账号 supervisor 已在运行，PID=${oldPid}`);
      fs.rmSync(LOCK_FILE, { force: true });
    }
  }
  throw new Error("无法取得多账号 supervisor 锁。");
}

const releaseLock = acquireLock();
const children = new Map();
const restartAfter = new Map();
let stopping = false;

function startProfile(profile) {
  const child = spawn(
    process.execPath,
    ["--disable-warning=ExperimentalWarning", AGENT_FILE, "start"],
    {
      cwd: PROJECT_DIR,
      env: { ...process.env, WEIXIN_PROFILE: profile },
      windowsHide: true,
      stdio: "ignore",
    },
  );
  children.set(profile, child);
  log(`账号 ${profile} 已启动，PID=${child.pid}`);
  child.once("error", (error) => log(`账号 ${profile} 启动异常：${String(error).slice(0, 500)}`));
  child.once("exit", (code, signal) => {
    children.delete(profile);
    if (!stopping) {
      restartAfter.set(profile, Date.now() + 15_000);
      log(`账号 ${profile} 已退出 code=${code} signal=${signal || "-"}，15 秒后重试`);
    }
  });
}

function reconcile() {
  const available = new Set(discoverProfiles(PROJECT_DIR));
  for (const [profile, child] of children) {
    if (!available.has(profile)) {
      log(`账号 ${profile} 已不在配置中，正在停止`);
      child.kill();
    }
  }
  for (const profile of available) {
    if (children.has(profile)) continue;
    if ((restartAfter.get(profile) || 0) > Date.now()) continue;
    restartAfter.delete(profile);
    startProfile(profile);
  }
}

async function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  clearInterval(timer);
  log(`收到 ${signal}，正在停止 ${children.size} 个微信账号`);
  const exits = [];
  for (const child of children.values()) {
    exits.push(new Promise((resolve) => child.once("exit", resolve)));
    child.kill();
  }
  await Promise.race([
    Promise.allSettled(exits),
    new Promise((resolve) => setTimeout(resolve, 5000)),
  ]);
  releaseLock();
  process.exit(0);
}

process.once("SIGINT", () => shutdown("SIGINT"));
process.once("SIGTERM", () => shutdown("SIGTERM"));
process.once("exit", releaseLock);

log("Vera 多账号 supervisor 启动");
reconcile();
const timer = setInterval(reconcile, 5000);
