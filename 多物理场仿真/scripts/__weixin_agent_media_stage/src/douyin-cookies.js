import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const DOUYIN_COOKIE_DOMAINS = [
  "douyin.com",
  "iesdouyin.com",
];

function isDouyinCookie(cookie) {
  const domain = String(cookie?.domain || "").replace(/^\./, "").toLowerCase();
  return DOUYIN_COOKIE_DOMAINS.some(
    (allowed) => domain === allowed || domain.endsWith(`.${allowed}`),
  );
}

function cleanCookieField(value) {
  return String(value ?? "").replace(/[\t\r\n]/g, "");
}

export function cookiesToNetscape(cookies) {
  const lines = [
    "# Netscape HTTP Cookie File",
    "# Generated from the isolated Vera Douyin browser profile.",
    "# Do not edit or share this file.",
  ];
  for (const cookie of cookies.filter(isDouyinCookie)) {
    const domain = cleanCookieField(cookie.domain);
    const includeSubdomains = domain.startsWith(".") ? "TRUE" : "FALSE";
    const cookiePath = cleanCookieField(cookie.path || "/");
    const secure = cookie.secure ? "TRUE" : "FALSE";
    const expires = Number.isFinite(cookie.expires) && cookie.expires > 0
      ? Math.floor(cookie.expires)
      : 0;
    lines.push([
      domain,
      includeSubdomains,
      cookiePath,
      secure,
      expires,
      cleanCookieField(cookie.name),
      cleanCookieField(cookie.value),
    ].join("\t"));
  }
  return `${lines.join("\n")}\n`;
}

export async function cdpRequest(webSocketUrl, method, params = {}, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(webSocketUrl);
    const requestId = 1;
    const timer = setTimeout(() => {
      socket.close();
      reject(new Error(`Chrome 调试接口超时: ${method}`));
    }, timeoutMs);
    const finish = (callback) => {
      clearTimeout(timer);
      try { socket.close(); } catch {}
      callback();
    };
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({ id: requestId, method, params }));
    });
    socket.addEventListener("message", (event) => {
      let payload;
      try { payload = JSON.parse(String(event.data)); } catch { return; }
      if (payload.id !== requestId) return;
      if (payload.error) {
        finish(() => reject(new Error(`Chrome 调试接口错误: ${payload.error.message}`)));
        return;
      }
      finish(() => resolve(payload.result || {}));
    });
    socket.addEventListener("error", () => {
      finish(() => reject(new Error("无法连接隔离的抖音浏览器")));
    });
  });
}

export async function inspectDouyinPage({
  debugUrl = "http://127.0.0.1:9223",
  fetchImpl = globalThis.fetch,
} = {}) {
  const base = debugUrl.replace(/\/$/, "");
  const targetsResponse = await fetchImpl(`${base}/json/list`);
  if (!targetsResponse.ok) throw new Error(`无法读取抖音浏览器标签页: HTTP ${targetsResponse.status}`);
  const targets = await targetsResponse.json();
  const page = targets.find((target) => target.type === "page" && /(^|\.)douyin\.com$/i.test(new URL(target.url).hostname));
  if (!page?.webSocketDebuggerUrl) throw new Error("隔离浏览器里没有打开抖音页面");
  const expression = `(() => ({
    url: location.href,
    title: document.title,
    videos: Array.from(document.querySelectorAll("video")).map((video) => ({
      src: video.currentSrc || video.src || "",
      poster: video.poster || "",
      duration: Number.isFinite(video.duration) ? video.duration : 0
    })),
    resources: performance.getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((url) => /douyinvod|bytev|\\.mp4(?:$|\\?)/i.test(url))
  }))()`;
  const result = await cdpRequest(page.webSocketDebuggerUrl, "Runtime.evaluate", {
    expression,
    returnByValue: true,
  });
  return result.result?.value || {};
}

async function browserVersion(debugUrl, fetchImpl) {
  const response = await fetchImpl(`${debugUrl.replace(/\/$/, "")}/json/version`);
  if (!response.ok) throw new Error(`抖音浏览器未就绪: HTTP ${response.status}`);
  return response.json();
}

export async function ensureDouyinBrowser({
  debugUrl = "http://127.0.0.1:9223",
  chromePath,
  profileDir,
  fetchImpl = globalThis.fetch,
} = {}) {
  try {
    return await browserVersion(debugUrl, fetchImpl);
  } catch {}
  if (!chromePath || !fs.existsSync(chromePath)) throw new Error("没有找到独立抖音浏览器程序");
  if (!profileDir) throw new Error("没有配置独立抖音浏览器目录");
  fs.mkdirSync(profileDir, { recursive: true });
  const debug = new URL(debugUrl);
  const port = debug.port || "9223";
  const child = spawn(chromePath, [
    `--user-data-dir=${profileDir}`,
    `--remote-debugging-port=${port}`,
    "--remote-debugging-address=127.0.0.1",
    "--headless=new",
    "--disable-gpu",
    "--mute-audio",
    "--disable-application-cache",
    "--disk-cache-size=1",
    "--media-cache-size=1",
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
  ], { detached: true, stdio: "ignore", windowsHide: true });
  child.unref();
  let lastError;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 400));
    try { return await browserVersion(debugUrl, fetchImpl); } catch (error) { lastError = error; }
  }
  throw new Error(`独立抖音浏览器启动失败: ${String(lastError)}`);
}

export async function captureDouyinVideo({
  url,
  debugUrl = "http://127.0.0.1:9223",
  chromePath,
  profileDir,
  fetchImpl = globalThis.fetch,
  timeoutMs = 20_000,
} = {}) {
  const version = await ensureDouyinBrowser({ debugUrl, chromePath, profileDir, fetchImpl });
  const created = await cdpRequest(version.webSocketDebuggerUrl, "Target.createTarget", { url: "about:blank" });
  const targetId = created.targetId;
  if (!targetId) throw new Error("无法创建抖音浏览器标签页");
  const deadline = Date.now() + timeoutMs;
  let pageSocket = "";
  try {
    while (Date.now() < deadline && !pageSocket) {
      const response = await fetchImpl(`${debugUrl.replace(/\/$/, "")}/json/list`);
      const targets = response.ok ? await response.json() : [];
      pageSocket = targets.find((target) => target.id === targetId)?.webSocketDebuggerUrl || "";
      if (!pageSocket) await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!pageSocket) throw new Error("抖音浏览器标签页没有就绪");
    await cdpRequest(pageSocket, "Network.enable").catch(() => {});
    await cdpRequest(pageSocket, "Network.setCacheDisabled", { cacheDisabled: true }).catch(() => {});
    await cdpRequest(pageSocket, "Page.navigate", { url });

    const expression = `(() => {
      const video = Array.from(document.querySelectorAll("video"))
        .find((item) => /^https:/i.test(item.currentSrc || item.src || ""));
      return {
        pageUrl: location.href,
        title: document.title,
        videoUrl: video ? (video.currentSrc || video.src || "") : "",
        duration: video && Number.isFinite(video.duration) ? video.duration : 0
      };
    })()`;
    let last = {};
    while (Date.now() < deadline) {
      const evaluated = await cdpRequest(pageSocket, "Runtime.evaluate", {
        expression,
        returnByValue: true,
      });
      last = evaluated.result?.value || {};
      if (last.videoUrl) return last;
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    const blocked = /验证|captcha|安全/i.test(`${last.title || ""} ${last.pageUrl || ""}`);
    const error = new Error(blocked ? "抖音页面要求完成验证" : "抖音页面没有取得视频流");
    error.needsBrowserAuth = true;
    throw error;
  } finally {
    if (pageSocket) {
      await cdpRequest(pageSocket, "Network.clearBrowserCache", {}, 10_000).catch(() => {});
    }
    await cdpRequest(version.webSocketDebuggerUrl, "Target.closeTarget", { targetId }).catch(() => {});
  }
}

export async function clearDouyinBrowserCache({
  debugUrl = "http://127.0.0.1:9223",
  fetchImpl = globalThis.fetch,
} = {}) {
  const response = await fetchImpl(`${debugUrl.replace(/\/$/, "")}/json/list`);
  if (!response.ok) throw new Error(`无法读取抖音浏览器标签页: HTTP ${response.status}`);
  const target = (await response.json()).find((item) => item.type === "page");
  if (!target?.webSocketDebuggerUrl) throw new Error("抖音浏览器没有可用标签页");
  await cdpRequest(target.webSocketDebuggerUrl, "Network.clearBrowserCache", {}, 10_000);
}

export async function syncDouyinCookies({
  debugUrl = "http://127.0.0.1:9223",
  cookieFile,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (!cookieFile) throw new Error("缺少抖音 Cookie 文件路径");
  const versionResponse = await fetchImpl(`${debugUrl.replace(/\/$/, "")}/json/version`);
  if (!versionResponse.ok) {
    throw new Error(`隔离的抖音浏览器未就绪: HTTP ${versionResponse.status}`);
  }
  const version = await versionResponse.json();
  if (!version.webSocketDebuggerUrl) throw new Error("浏览器没有提供调试连接地址");
  const result = await cdpRequest(version.webSocketDebuggerUrl, "Storage.getCookies");
  const douyinCookies = (result.cookies || []).filter(isDouyinCookie);
  if (!douyinCookies.length) {
    throw new Error("隔离浏览器里还没有抖音 Cookie，请先打开一个抖音视频页面");
  }
  fs.mkdirSync(path.dirname(cookieFile), { recursive: true });
  const temp = `${cookieFile}.tmp`;
  fs.writeFileSync(temp, cookiesToNetscape(douyinCookies), { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temp, cookieFile);
  try { fs.chmodSync(cookieFile, 0o600); } catch {}
  return { count: douyinCookies.length, cookieFile };
}

async function main() {
  const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const cookieFile = process.env.DOUYIN_COOKIE_FILE || path.join(projectDir, "state", "douyin-cookies.txt");
  const debugUrl = process.env.DOUYIN_DEBUG_URL || "http://127.0.0.1:9223";
  const result = await syncDouyinCookies({ debugUrl, cookieFile });
  console.log(`已同步 ${result.count} 个抖音专用 Cookie。`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
