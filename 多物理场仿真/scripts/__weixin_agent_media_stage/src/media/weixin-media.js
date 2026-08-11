import crypto from "node:crypto";

const DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c";

export class MediaProcessingError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "MediaProcessingError";
    this.retryable = options.retryable ?? true;
    this.userMessage = options.userMessage || "这条媒体消息暂时处理不了，请稍后重发一次。";
  }
}

export function parseAesKey(aesKeyBase64) {
  const decoded = Buffer.from(String(aesKeyBase64 || ""), "base64");
  if (decoded.length === 16) return decoded;
  if (decoded.length === 32 && /^[0-9a-fA-F]{32}$/.test(decoded.toString("ascii"))) {
    return Buffer.from(decoded.toString("ascii"), "hex");
  }
  throw new MediaProcessingError("微信媒体 AES Key 格式无效", {
    retryable: false,
    userMessage: "这条媒体消息的解密信息不完整，请重新发送一次。",
  });
}

export function decryptAesEcb(encrypted, key) {
  const decipher = crypto.createDecipheriv("aes-128-ecb", key, null);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]);
}

export function detectImageMime(buffer) {
  if (buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return "image/png";
  }
  if (buffer.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))) return "image/jpeg";
  if (buffer.subarray(0, 6).toString("ascii") === "GIF87a" || buffer.subarray(0, 6).toString("ascii") === "GIF89a") {
    return "image/gif";
  }
  if (buffer.subarray(0, 4).toString("ascii") === "RIFF" && buffer.subarray(8, 12).toString("ascii") === "WEBP") {
    return "image/webp";
  }
  return "application/octet-stream";
}

function isAllowedCdnUrl(rawUrl, cdnBaseUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "https:") return false;
    const configured = new URL(cdnBaseUrl).hostname.toLowerCase();
    const host = url.hostname.toLowerCase();
    return host === configured || host === "novac2c.cdn.weixin.qq.com" || host.endsWith(".cdn.weixin.qq.com");
  } catch {
    return false;
  }
}

function mediaDownloadUrl(media, cdnBaseUrl) {
  if (media?.full_url) {
    if (!isAllowedCdnUrl(media.full_url, cdnBaseUrl)) {
      throw new MediaProcessingError("拒绝未知微信媒体下载地址", { retryable: false });
    }
    return media.full_url;
  }
  if (!media?.encrypt_query_param) {
    throw new MediaProcessingError("微信媒体缺少下载参数", { retryable: false });
  }
  return `${cdnBaseUrl.replace(/\/$/, "")}/download?encrypted_query_param=${encodeURIComponent(media.encrypt_query_param)}`;
}

async function readLimited(response, maxBytes) {
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > maxBytes) throw new MediaProcessingError(`微信媒体过大: ${declared} bytes`, { retryable: false });
  if (!response.body?.getReader) {
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length > maxBytes) throw new MediaProcessingError(`微信媒体过大: ${buffer.length} bytes`, { retryable: false });
    return buffer;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      throw new MediaProcessingError(`微信媒体超过 ${maxBytes} bytes`, { retryable: false });
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks, size);
}

export async function downloadAndDecryptMedia(descriptor, config, options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const cdnBaseUrl = config.weixinCdnBaseUrl || DEFAULT_CDN_BASE_URL;
  const maxBytes = descriptor.type === "image" ? config.weixinImageMaxBytes : config.weixinVoiceMaxBytes;
  let aesKeyBase64 = descriptor.media?.aes_key || "";
  if (descriptor.type === "image" && descriptor.imageAesKey) {
    if (!/^[0-9a-fA-F]{32}$/.test(descriptor.imageAesKey)) {
      throw new MediaProcessingError("微信图片 aeskey 格式无效", { retryable: false });
    }
    aesKeyBase64 = Buffer.from(descriptor.imageAesKey, "hex").toString("base64");
  }
  if (!aesKeyBase64) {
    throw new MediaProcessingError("微信媒体缺少 AES Key", { retryable: false });
  }
  const url = mediaDownloadUrl(descriptor.media, cdnBaseUrl);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.weixinMediaTimeoutMs);
  try {
    const response = await fetchImpl(url, { signal: controller.signal });
    if (!response.ok) {
      throw new MediaProcessingError(`微信媒体 CDN HTTP ${response.status}`, {
        retryable: response.status === 429 || response.status >= 500,
      });
    }
    const encrypted = await readLimited(response, maxBytes + 16);
    const decrypted = decryptAesEcb(encrypted, parseAesKey(aesKeyBase64));
    if (decrypted.length > maxBytes) {
      throw new MediaProcessingError(`解密后的微信媒体超过 ${maxBytes} bytes`, { retryable: false });
    }
    return decrypted;
  } catch (error) {
    if (error instanceof MediaProcessingError) throw error;
    const wrapped = new MediaProcessingError(`微信媒体下载或解密失败: ${String(error)}`);
    if (error?.name === "AbortError") wrapped.userMessage = "微信媒体下载超时，请稍后重发一次。";
    throw wrapped;
  } finally {
    clearTimeout(timer);
  }
}
