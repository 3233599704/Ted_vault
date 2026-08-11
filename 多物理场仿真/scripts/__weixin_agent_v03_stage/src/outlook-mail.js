import fs from "node:fs";
import path from "node:path";

function loadJson(filePath, fallback) {
  try { return JSON.parse(fs.readFileSync(filePath, "utf8")); } catch { return fallback; }
}

function saveJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2), "utf8");
  fs.renameSync(temp, filePath);
}

function cleanText(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{4,}/g, "\n\n\n")
    .trim();
}

export function formatOutlookMessage(message, maxBodyChars = 30_000) {
  const sender = message.from?.emailAddress || {};
  const fullBody = message.body?.contentType?.toLowerCase() === "text"
    ? message.body.content
    : message.bodyPreview;
  let body = cleanText(fullBody) || "（邮件正文为空）";
  if (body.length > maxBodyChars) {
    body = `${body.slice(0, maxBodyChars)}\n\n（正文超过 ${maxBodyChars} 字，已截断）`;
  }
  let received = message.receivedDateTime || "";
  try {
    received = new Date(received).toLocaleString("zh-CN", { hour12: false });
  } catch {}
  return [
    "Outlook 新邮件",
    `发件人：${sender.name || "未知"}${sender.address ? ` <${sender.address}>` : ""}`,
    `主题：${cleanText(message.subject) || "（无主题）"}`,
    `时间：${received}`,
    `附件：${message.hasAttachments ? "有" : "无"}`,
    "",
    body,
  ].join("\n");
}

export class OutlookMailboxPoller {
  constructor(config, auth, options = {}) {
    this.auth = auth;
    this.stateFile = config.outlookSyncFile;
    this.maxBodyChars = config.outlookMaxBodyChars;
    this.fetchImpl = options.fetchImpl || globalThis.fetch;
    this.now = options.now || (() => Date.now());
    this.graphBase = options.graphBase || "https://graph.microsoft.com/v1.0";
  }

  initialUrl() {
    const params = new URLSearchParams({
      "$select": "id,subject,from,receivedDateTime,body,bodyPreview,hasAttachments",
      "$orderby": "receivedDateTime desc",
      "$top": "50",
    });
    return `${this.graphBase}/me/mailFolders/inbox/messages?${params}`;
  }

  incrementalUrl(lastPollAt) {
    const since = new Date(new Date(lastPollAt).getTime() - 5 * 60_000).toISOString();
    const params = new URLSearchParams({
      "$select": "id,subject,from,receivedDateTime,body,bodyPreview,hasAttachments",
      "$filter": `receivedDateTime ge ${since}`,
      "$orderby": "receivedDateTime asc",
      "$top": "50",
    });
    return `${this.graphBase}/me/mailFolders/inbox/messages?${params}`;
  }

  async request(url, forceRefresh = false) {
    const token = await this.auth.getAccessToken(forceRefresh);
    const response = await this.fetchImpl(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        Prefer: 'outlook.body-content-type="text"',
      },
    });
    if (response.status === 401 && !forceRefresh) return this.request(url, true);
    const raw = await response.text();
    if (!response.ok) throw new Error(`Microsoft Graph HTTP ${response.status}: ${raw.slice(0, 800)}`);
    return raw ? JSON.parse(raw) : {};
  }

  async poll() {
    const state = loadJson(this.stateFile, { seenIds: [], lastPollAt: "" });
    const initial = !state.lastPollAt;
    let url = initial ? this.initialUrl() : this.incrementalUrl(state.lastPollAt);
    const messages = [];
    for (let page = 0; url && page < 20; page += 1) {
      const payload = await this.request(url);
      messages.push(...(payload.value || []));
      url = initial ? "" : payload["@odata.nextLink"] || "";
    }

    const seen = new Set(state.seenIds || []);
    const fresh = initial ? [] : messages.filter((message) => message.id && !seen.has(message.id));
    const ordered = fresh.sort((a, b) => String(a.receivedDateTime).localeCompare(String(b.receivedDateTime)));
    const newestMessageIds = [...messages]
      .sort((a, b) => String(b.receivedDateTime).localeCompare(String(a.receivedDateTime)))
      .map((message) => message.id)
      .filter(Boolean);
    const combinedIds = [
      ...newestMessageIds,
      ...(state.seenIds || []),
    ];
    saveJson(this.stateFile, {
      seenIds: [...new Set(combinedIds)].slice(0, 1000),
      lastPollAt: new Date(this.now()).toISOString(),
      updatedAt: new Date(this.now()).toISOString(),
    });
    return {
      initial,
      messages: ordered.map((message) => ({
        id: message.id,
        text: formatOutlookMessage(message, this.maxBodyChars),
      })),
    };
  }
}
