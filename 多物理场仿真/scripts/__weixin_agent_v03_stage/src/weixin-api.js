import crypto from "node:crypto";
import readline from "node:readline/promises";

export const MESSAGE_TYPE_USER = 1;
export const MESSAGE_TYPE_BOT = 2;
export const MESSAGE_STATE_FINISH = 2;
export const ITEM_TEXT = 1;
export const ITEM_VOICE = 3;
export const STALE_TOKEN_ERRCODE = -14;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildClientVersion(version) {
  const [major = 0, minor = 0, patch = 0] = String(version)
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);
  return ((major & 0xff) << 16) | ((minor & 0xff) << 8) | (patch & 0xff);
}

function randomWechatUin() {
  const value = crypto.randomBytes(4).readUInt32BE(0);
  return Buffer.from(String(value), "utf8").toString("base64");
}

function ensureSlash(url) {
  return url.endsWith("/") ? url : `${url}/`;
}

export class WeixinApiError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "WeixinApiError";
    this.status = options.status || 0;
    this.retryable = Boolean(options.retryable);
  }
}

export function plainTextForWeixin(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/```[^\n]*\n?/g, "")
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function splitText(text, maxChars = 3500) {
  const chunks = [];
  let rest = String(text || "");
  while (rest.length > maxChars) {
    let cut = maxChars;
    const window = rest.slice(0, maxChars + 1);
    const candidates = [window.lastIndexOf("\n"), window.lastIndexOf("。"), window.lastIndexOf("！"), window.lastIndexOf("？")];
    const best = Math.max(...candidates);
    if (best >= Math.floor(maxChars * 0.6)) cut = best + 1;
    if (/^[\uDC00-\uDFFF]$/.test(rest[cut])) cut -= 1;
    chunks.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) chunks.push(rest);
  return chunks.length ? chunks : ["(空回复)"];
}

export function extractMessageText(message) {
  for (const item of message.item_list || []) {
    if (item.type === ITEM_TEXT && item.text_item?.text != null) {
      const text = String(item.text_item.text).trim();
      const ref = item.ref_msg;
      if (!ref) return text;
      const parts = [];
      if (ref.title) parts.push(ref.title);
      if (ref.message_item?.text_item?.text) {
        parts.push(ref.message_item.text_item.text);
      }
      return parts.length ? `[引用: ${parts.join(" | ")}]\n${text}`.trim() : text;
    }
    if (item.type === ITEM_VOICE && item.voice_item?.text) {
      return String(item.voice_item.text).trim();
    }
  }
  return "";
}

export function messageKey(message) {
  return String(
    message.message_id ||
      `${message.seq || ""}:${message.from_user_id || ""}:${message.create_time_ms || ""}`,
  );
}

export class WeixinApi {
  constructor(config, log, options = {}) {
    this.config = config;
    this.log = log;
    this.fetchImpl = options.fetchImpl || globalThis.fetch;
  }

  baseInfo() {
    return {
      channel_version: this.config.channelVersion,
      bot_agent: this.config.botAgent,
    };
  }

  commonHeaders() {
    return {
      "iLink-App-Id": this.config.ilinkAppId,
      "iLink-App-ClientVersion": String(buildClientVersion(this.config.channelVersion)),
    };
  }

  postHeaders(token) {
    const headers = {
      "Content-Type": "application/json",
      AuthorizationType: "ilink_bot_token",
      "X-WECHAT-UIN": randomWechatUin(),
      ...this.commonHeaders(),
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }

  async request({ method, baseUrl, endpoint, payload, token, timeoutMs, label, attempts = 1, signal }) {
    const url = new URL(endpoint, ensureSlash(baseUrl)).toString();
    let lastError;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const controller = new AbortController();
      const abortFromCaller = () => controller.abort(signal?.reason);
      if (signal?.aborted) controller.abort(signal.reason);
      else signal?.addEventListener("abort", abortFromCaller, { once: true });
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await this.fetchImpl(url, {
          method,
          headers: method === "GET" ? this.commonHeaders() : this.postHeaders(token),
          body: method === "GET" ? undefined : JSON.stringify(payload),
          signal: controller.signal,
        });
        const raw = await response.text();
        if (!response.ok) {
          throw new WeixinApiError(`${label} HTTP ${response.status}: ${raw.slice(0, 500)}`, {
            status: response.status,
            retryable: response.status === 429 || response.status >= 500,
          });
        }
        return raw ? JSON.parse(raw) : {};
      } catch (error) {
        if (signal?.aborted) throw error;
        lastError = error instanceof WeixinApiError
          ? error
          : new WeixinApiError(`${label}: ${String(error)}`, { retryable: true });
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abortFromCaller);
      }
      if (!lastError.retryable || attempt === attempts - 1) throw lastError;
      await sleep([1000, 3000, 8000][Math.min(attempt, 2)]);
    }
    throw lastError;
  }

  async fetchQRCode(localTokenList = []) {
    return this.request({
      method: "POST",
      baseUrl: this.config.weixinBaseUrl,
      endpoint: `ilink/bot/get_bot_qrcode?bot_type=${encodeURIComponent(this.config.botType)}`,
      payload: { local_token_list: localTokenList.slice(0, 10) },
      timeoutMs: 30_000,
      label: "fetchQRCode",
    });
  }

  async pollQRStatus(baseUrl, qrcode, verifyCode) {
    let endpoint = `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qrcode)}`;
    if (verifyCode) endpoint += `&verify_code=${encodeURIComponent(verifyCode)}`;
    try {
      return await this.request({
        method: "GET",
        baseUrl,
        endpoint,
        timeoutMs: 40_000,
        label: "pollQRStatus",
      });
    } catch (error) {
      this.log(`二维码状态轮询异常，继续等待: ${String(error).slice(0, 300)}`);
      return { status: "wait" };
    }
  }

  async displayQRCode(qrcodeUrl) {
    try {
      const qrterm = await import("qrcode-terminal");
      qrterm.default.generate(qrcodeUrl, { small: true });
    } catch {
      // qrcode-terminal is optional.
    }
    console.log("\n如果二维码没有显示，请打开下面的链接继续：");
    console.log(qrcodeUrl);
    console.log("");
  }

  async login({ localTokenList = [], existingAccount = null } = {}) {
    this.log("开始微信扫码登录");
    const qr = await this.fetchQRCode(localTokenList);
    if (!qr.qrcode || !qr.qrcode_img_content) {
      throw new Error(`二维码响应异常: ${JSON.stringify(qr).slice(0, 500)}`);
    }
    await this.displayQRCode(qr.qrcode_img_content);

    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const deadline = Date.now() + 8 * 60_000;
    let currentBaseUrl = this.config.weixinBaseUrl;
    let pendingVerifyCode = "";
    let scannedPrinted = false;

    try {
      while (Date.now() < deadline) {
        const status = await this.pollQRStatus(currentBaseUrl, qr.qrcode, pendingVerifyCode);
        switch (status.status) {
          case "wait":
            process.stdout.write(".");
            break;
          case "scaned":
            pendingVerifyCode = "";
            if (!scannedPrinted) {
              process.stdout.write("\n已扫码，正在等待手机确认\n");
              scannedPrinted = true;
            }
            break;
          case "need_verifycode":
            pendingVerifyCode = (await rl.question("\n输入手机微信显示的数字：")).trim();
            break;
          case "verify_code_blocked":
            throw new Error("多次输入错误，连接流程已停止。请稍后再试。");
          case "expired":
            throw new Error("二维码已过期，请重新运行 npm run login。");
          case "binded_redirect":
            if (existingAccount?.token) return existingAccount;
            throw new Error("服务端提示已绑定，但本地没有 token。请重新登录。");
          case "scaned_but_redirect":
            if (status.redirect_host) currentBaseUrl = `https://${status.redirect_host}`;
            break;
          case "confirmed":
            if (!status.bot_token || !status.ilink_bot_id) {
              throw new Error("登录响应缺少 token 或 bot id。");
            }
            return {
              accountId: status.ilink_bot_id,
              userId: status.ilink_user_id || "",
              token: status.bot_token,
              baseUrl: status.baseurl || currentBaseUrl,
              savedAt: new Date().toISOString(),
            };
          default:
            this.log(`未知二维码状态: ${JSON.stringify(status).slice(0, 300)}`);
        }
        await sleep(1000);
      }
    } finally {
      rl.close();
    }
    throw new Error("登录超时，请重新运行 npm run login。");
  }

  getUpdates(account, getUpdatesBuf, timeoutMs, signal) {
    return this.request({
      method: "POST",
      baseUrl: account.baseUrl || this.config.weixinBaseUrl,
      endpoint: "ilink/bot/getupdates",
      payload: { get_updates_buf: getUpdatesBuf || "", base_info: this.baseInfo() },
      token: account.token,
      timeoutMs,
      label: "getUpdates",
      signal,
    });
  }

  getConfig(account, userId, contextToken) {
    return this.request({
      method: "POST",
      baseUrl: account.baseUrl || this.config.weixinBaseUrl,
      endpoint: "ilink/bot/getconfig",
      payload: {
        ilink_user_id: userId,
        context_token: contextToken,
        base_info: this.baseInfo(),
      },
      token: account.token,
      timeoutMs: 10_000,
      label: "getConfig",
      attempts: 2,
    });
  }

  async sendTyping(account, userId, contextToken, status) {
    if (!this.config.sendTyping) return;
    try {
      const config = await this.getConfig(account, userId, contextToken);
      if (!config.typing_ticket) return;
      await this.request({
        method: "POST",
        baseUrl: account.baseUrl || this.config.weixinBaseUrl,
        endpoint: "ilink/bot/sendtyping",
        payload: {
          ilink_user_id: userId,
          typing_ticket: config.typing_ticket,
          status,
          base_info: this.baseInfo(),
        },
        token: account.token,
        timeoutMs: 10_000,
        label: "sendTyping",
        attempts: 2,
      });
    } catch (error) {
      this.log(`typing 状态发送失败: ${String(error).slice(0, 250)}`);
    }
  }

  async sendText(account, to, text, contextToken, deliveryId) {
    const clean = plainTextForWeixin(text) || "(空回复)";
    const chunks = splitText(clean);
    for (let index = 0; index < chunks.length; index += 1) {
      const stableId = crypto
        .createHash("sha256")
        .update(`${deliveryId}:${index}`)
        .digest("hex")
        .slice(0, 28);
      const response = await this.request({
        method: "POST",
        baseUrl: account.baseUrl || this.config.weixinBaseUrl,
        endpoint: "ilink/bot/sendmessage",
        payload: {
          msg: {
            from_user_id: "",
            to_user_id: to,
            client_id: `vera:${stableId}`,
            message_type: MESSAGE_TYPE_BOT,
            message_state: MESSAGE_STATE_FINISH,
            item_list: [{ type: ITEM_TEXT, text_item: { text: chunks[index] } }],
            context_token: contextToken || undefined,
            run_id: deliveryId,
          },
          base_info: this.baseInfo(),
        },
        token: account.token,
        timeoutMs: 15_000,
        label: "sendMessage",
        attempts: 3,
      });
      if (response.ret && response.ret !== 0) {
        throw new WeixinApiError(
          `sendMessage ret=${response.ret} errmsg=${response.errmsg || ""}`,
          { retryable: false },
        );
      }
    }
  }

  notifyStart(account) {
    return this.request({
      method: "POST",
      baseUrl: account.baseUrl || this.config.weixinBaseUrl,
      endpoint: "ilink/bot/msg/notifystart",
      payload: { base_info: this.baseInfo() },
      token: account.token,
      timeoutMs: 10_000,
      label: "notifyStart",
    });
  }

  notifyStop(account) {
    return this.request({
      method: "POST",
      baseUrl: account.baseUrl || this.config.weixinBaseUrl,
      endpoint: "ilink/bot/msg/notifystop",
      payload: { base_info: this.baseInfo() },
      token: account.token,
      timeoutMs: 10_000,
      label: "notifyStop",
    });
  }
}

