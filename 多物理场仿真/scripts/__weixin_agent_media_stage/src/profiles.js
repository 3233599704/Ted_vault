import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
export const PROJECT_DIR = path.resolve(SCRIPT_DIR, "..");

export function normalizeProfileName(raw = "default") {
  const value = String(raw || "default").trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(value)) {
    throw new Error("账号名称只能包含字母、数字、下划线和短横线，最长 32 位。");
  }
  return value;
}

export function profilePaths(profileName = "default", projectDir = PROJECT_DIR) {
  const profile = normalizeProfileName(profileName);
  const stateRoot = path.join(projectDir, "state");
  const logRoot = path.join(projectDir, "logs");
  const downloadRoot = path.join(projectDir, "downloads");
  const isDefault = profile === "default";
  const stateDir = isDefault ? stateRoot : path.join(stateRoot, "profiles", profile);
  const logDir = isDefault ? logRoot : path.join(logRoot, "profiles", profile);
  const downloadsDir = isDefault ? downloadRoot : path.join(downloadRoot, profile);
  return {
    profile,
    stateRoot,
    logRoot,
    downloadRoot,
    stateDir,
    logDir,
    downloadsDir,
    accountFile: path.join(stateDir, "account.json"),
    syncFile: path.join(stateDir, "sync.json"),
    databaseFile: path.join(stateDir, "agent.sqlite"),
    lockFile: path.join(stateDir, "weixin-agent.lock"),
    logFile: path.join(logDir, "weixin-agent.log"),
    stockWatchlistFile: path.join(stateDir, "stock-watchlist.json"),
    stockPortfolioFile: path.join(stateDir, "paper-portfolio.json"),
  };
}

export function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function readJson(filePath, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

export function discoverProfiles(projectDir = PROJECT_DIR) {
  const names = [];
  const defaultPaths = profilePaths("default", projectDir);
  if (fs.existsSync(defaultPaths.accountFile)) names.push("default");

  const profilesDir = path.join(defaultPaths.stateRoot, "profiles");
  if (fs.existsSync(profilesDir)) {
    for (const entry of fs.readdirSync(profilesDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      let profile;
      try { profile = normalizeProfileName(entry.name); } catch { continue; }
      if (fs.existsSync(profilePaths(profile, projectDir).accountFile)) names.push(profile);
    }
  }
  return [...new Set(names)].sort((a, b) => {
    if (a === "default") return -1;
    if (b === "default") return 1;
    return a.localeCompare(b);
  });
}

export function profileSummary(profileName, projectDir = PROJECT_DIR) {
  const paths = profilePaths(profileName, projectDir);
  const account = readJson(paths.accountFile, {});
  const lockText = fs.existsSync(paths.lockFile)
    ? fs.readFileSync(paths.lockFile, "utf8")
    : "";
  const lockPid = Number.parseInt(lockText, 10);
  return {
    profile: paths.profile,
    accountId: account.accountId || "",
    userId: account.userId || "",
    savedAt: account.savedAt || "",
    pid: Number.isInteger(lockPid) ? lockPid : 0,
    running: isProcessAlive(lockPid),
  };
}
