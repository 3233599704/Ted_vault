import crypto from "node:crypto";

export function aesEcbPaddedSize(size) {
  return Math.ceil((Number(size) + 1) / 16) * 16;
}

export function prepareEncryptedUpload(buffer) {
  const plaintext = Buffer.from(buffer);
  const aesKey = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv("aes-128-ecb", aesKey, null);
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return {
    plaintext,
    ciphertext,
    aesKey,
    aesKeyHex: aesKey.toString("hex"),
    filekey: crypto.randomBytes(16).toString("hex"),
    rawsize: plaintext.length,
    rawfilemd5: crypto.createHash("md5").update(plaintext).digest("hex"),
    filesize: ciphertext.length,
  };
}

export function buildCdnUploadUrl({ uploadFullUrl, uploadParam, filekey, cdnBaseUrl }) {
  if (uploadFullUrl) return uploadFullUrl;
  if (!uploadParam) throw new Error("微信 CDN 没有返回上传参数");
  return `${cdnBaseUrl.replace(/\/$/, "")}/upload?encrypted_query_param=${encodeURIComponent(uploadParam)}&filekey=${encodeURIComponent(filekey)}`;
}

export function assertAllowedUploadUrl(rawUrl, cdnBaseUrl) {
  const url = new URL(rawUrl);
  if (url.protocol !== "https:") throw new Error("拒绝非 HTTPS 的微信 CDN 上传地址");
  const configuredHost = new URL(cdnBaseUrl).hostname.toLowerCase();
  const host = url.hostname.toLowerCase();
  if (host !== configuredHost && !host.endsWith(".weixin.qq.com")) {
    throw new Error("拒绝未知的微信 CDN 上传地址");
  }
  return url.toString();
}

export async function uploadEncryptedBuffer({
  url,
  ciphertext,
  fetchImpl = globalThis.fetch,
  timeoutMs = 30_000,
}) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: new Uint8Array(ciphertext),
        signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.text().catch(() => "");
        const error = new Error(`微信 CDN 上传 HTTP ${response.status}: ${body.slice(0, 300)}`);
        error.retryable = response.status === 429 || response.status >= 500;
        throw error;
      }
      const downloadParam = response.headers.get("x-encrypted-param");
      if (!downloadParam) throw new Error("微信 CDN 响应缺少 x-encrypted-param");
      return downloadParam;
    } catch (error) {
      lastError = error;
      if (error?.retryable === false || attempt === 2) throw error;
      await new Promise((resolve) => setTimeout(resolve, [500, 1500][attempt]));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}
