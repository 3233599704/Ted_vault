import fs from "node:fs";
import path from "node:path";

import { protectText, unprotectText } from "./dpapi.js";

const GRAPH_MAIL_SCOPE = "https://graph.microsoft.com/Mail.Read";
const SCOPES = `${GRAPH_MAIL_SCOPE} offline_access openid profile`;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function decodeJwtPayload(token = "") {
  try {
    const part = token.split(".")[1];
    if (!part) return {};
    return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
  } catch {
    return {};
  }
}

export class OutlookAuthError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "OutlookAuthError";
    this.code = options.code || "";
    this.reauthRequired = Boolean(options.reauthRequired);
    this.userMessage = options.userMessage || "Outlook 授权暂时不可用。";
  }
}

export class OutlookAuthClient {
  constructor(config, options = {}) {
    this.clientId = config.outlookClientId;
    this.tenantId = config.outlookTenantId;
    this.mailbox = config.outlookMailbox;
    this.tokenFile = config.outlookTokenFile;
    this.fetchImpl = options.fetchImpl || globalThis.fetch;
    this.sleep = options.sleep || sleep;
    this.protect = options.protect || protectText;
    this.unprotect = options.unprotect || unprotectText;
    this.now = options.now || (() => Date.now());
    this.memoryToken = null;
  }

  isConfigured() {
    return Boolean(this.clientId && this.tenantId && this.mailbox);
  }

  deviceEndpoint() {
    return `https://login.microsoftonline.com/${encodeURIComponent(this.tenantId)}/oauth2/v2.0/devicecode`;
  }

  tokenEndpoint() {
    return `https://login.microsoftonline.com/${encodeURIComponent(this.tenantId)}/oauth2/v2.0/token`;
  }

  async postForm(url, values) {
    const response = await this.fetchImpl(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(values),
    });
    const raw = await response.text();
    let payload = {};
    try { payload = raw ? JSON.parse(raw) : {}; } catch {}
    return { response, payload, raw };
  }

  async loadRecord() {
    if (!fs.existsSync(this.tokenFile)) return null;
    const cipher = fs.readFileSync(this.tokenFile, "utf8");
    const plain = await this.unprotect(cipher);
    return JSON.parse(plain);
  }

  async saveRecord(record) {
    fs.mkdirSync(path.dirname(this.tokenFile), { recursive: true });
    const cipher = await this.protect(JSON.stringify(record));
    const temp = `${this.tokenFile}.tmp`;
    fs.writeFileSync(temp, cipher, "utf8");
    fs.renameSync(temp, this.tokenFile);
    try { fs.chmodSync(this.tokenFile, 0o600); } catch {}
  }

  tokenRecord(payload, previous = {}) {
    const claims = decodeJwtPayload(payload.id_token || "");
    return {
      refreshToken: payload.refresh_token || previous.refreshToken || "",
      username:
        claims.preferred_username ||
        claims.email ||
        previous.username ||
        this.mailbox,
      tenantId: claims.tid || previous.tenantId || this.tenantId,
      grantedScope: payload.scope || previous.grantedScope || SCOPES,
      updatedAt: new Date(this.now()).toISOString(),
    };
  }

  setMemoryToken(payload) {
    this.memoryToken = {
      accessToken: payload.access_token,
      expiresAt: this.now() + Number(payload.expires_in || 3600) * 1000,
    };
  }

  async deviceLogin(onCode = console.log) {
    if (!this.isConfigured()) {
      throw new OutlookAuthError("Outlook configuration is incomplete", {
        code: "not_configured",
        userMessage: "请先配置 Outlook Client ID、Tenant ID 和邮箱地址。",
      });
    }
    const { response, payload, raw } = await this.postForm(this.deviceEndpoint(), {
      client_id: this.clientId,
      scope: SCOPES,
    });
    if (!response.ok || !payload.device_code) {
      throw new OutlookAuthError(`Device code HTTP ${response.status}: ${raw.slice(0, 800)}`);
    }
    onCode({
      message: payload.message || "",
      userCode: payload.user_code,
      verificationUri: payload.verification_uri,
      expiresIn: Number(payload.expires_in || 900),
    });

    let intervalMs = Math.max(Number(payload.interval || 5), 1) * 1000;
    const deadline = this.now() + Number(payload.expires_in || 900) * 1000;
    while (this.now() < deadline) {
      await this.sleep(intervalMs);
      const token = await this.postForm(this.tokenEndpoint(), {
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
        client_id: this.clientId,
        device_code: payload.device_code,
      });
      if (token.response.ok && token.payload.access_token) {
        this.setMemoryToken(token.payload);
        const record = this.tokenRecord(token.payload);
        await this.saveRecord(record);
        return record;
      }
      const code = token.payload.error || "unknown_error";
      if (code === "authorization_pending") continue;
      if (code === "slow_down") {
        intervalMs += 5000;
        continue;
      }
      throw new OutlookAuthError(
        `Device token error ${code}: ${token.payload.error_description || token.raw}`,
        {
          code,
          reauthRequired: ["authorization_declined", "expired_token", "bad_verification_code"].includes(code),
          userMessage: "Outlook 登录未完成或已过期，请重新运行授权。",
        },
      );
    }
    throw new OutlookAuthError("Outlook device code expired", {
      code: "expired_token",
      reauthRequired: true,
      userMessage: "Outlook 登录二维码/设备码已过期，请重新运行授权。",
    });
  }

  async getAccessToken(forceRefresh = false) {
    if (!forceRefresh && this.memoryToken?.expiresAt > this.now() + 60_000) {
      return this.memoryToken.accessToken;
    }
    const record = await this.loadRecord();
    if (!record?.refreshToken) {
      throw new OutlookAuthError("Outlook refresh token is missing", {
        code: "login_required",
        reauthRequired: true,
        userMessage: "Outlook 尚未授权，请运行 npm run outlook:login。",
      });
    }
    const token = await this.postForm(this.tokenEndpoint(), {
      grant_type: "refresh_token",
      client_id: this.clientId,
      refresh_token: record.refreshToken,
      scope: SCOPES,
    });
    if (!token.response.ok || !token.payload.access_token) {
      const code = token.payload.error || "token_refresh_failed";
      throw new OutlookAuthError(
        `Outlook refresh error ${code}: ${token.payload.error_description || token.raw}`,
        {
          code,
          reauthRequired: ["invalid_grant", "interaction_required", "unauthorized_client"].includes(code),
          userMessage: "Outlook 授权已失效，需要重新登录。",
        },
      );
    }
    this.setMemoryToken(token.payload);
    await this.saveRecord(this.tokenRecord(token.payload, record));
    return token.payload.access_token;
  }
}

export const OUTLOOK_SCOPES = SCOPES;
