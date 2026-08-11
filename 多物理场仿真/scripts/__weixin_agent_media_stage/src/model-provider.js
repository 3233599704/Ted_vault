import dns from "node:dns/promises";
import https from "node:https";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function transportErrorText(error) {
  const parts = [];
  let current = error;
  for (let depth = 0; current && depth < 3; depth += 1) {
    const message = String(current.message || current);
    const code = current.code ? ` [${current.code}]` : "";
    if (message && !parts.includes(`${message}${code}`)) parts.push(`${message}${code}`);
    current = current.cause;
  }
  return parts.join(" <- ") || "unknown transport error";
}

function isProxyFakeAddress(address) {
  return /^198\.(?:18|19)\./.test(String(address || ""));
}

async function resolveRealIpv4(hostname) {
  const configured = String(
    process.env.DEEPSEEK_DNS_SERVERS || "119.29.29.29,223.5.5.5",
  ).split(",").map((item) => item.trim()).filter(Boolean);
  for (const server of configured) {
    const resolver = new dns.Resolver();
    resolver.setServers([server]);
    try {
      const addresses = (await resolver.resolve4(hostname)).filter(
        (address) => !isProxyFakeAddress(address),
      );
      if (addresses.length) return addresses;
    } catch {
      // Try the next independent resolver before falling back to system DNS.
    }
  }
  try {
    return (await dns.resolve4(hostname)).filter((address) => !isProxyFakeAddress(address));
  } catch {
    return [];
  }
}

function requestJsonAtAddress(url, { headers, body, signal, timeoutMs, address = "" }) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      const error = new Error("Request aborted");
      error.name = "AbortError";
      reject(error);
      return;
    }
    const lookup = address
      ? (_hostname, options, callback) => {
          if (options?.all) callback(null, [{ address, family: 4 }]);
          else callback(null, address, 4);
        }
      : undefined;
    const request = https.request({
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || 443,
      path: `${url.pathname}${url.search}`,
      method: "POST",
      headers: {
        ...headers,
        "Content-Length": Buffer.byteLength(body),
        Connection: "close",
      },
      agent: false,
      lookup,
    });
    const abort = () => {
      const error = new Error("Request aborted");
      error.name = "AbortError";
      request.destroy(error);
    };
    const cleanup = () => signal?.removeEventListener("abort", abort);
    signal?.addEventListener("abort", abort, { once: true });
    request.setTimeout(timeoutMs, () => {
      const error = new Error(`Connection to ${address || url.hostname} timed out`);
      error.code = "ETIMEDOUT";
      request.destroy(error);
    });
    request.on("response", (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        cleanup();
        const raw = Buffer.concat(chunks).toString("utf8");
        const status = Number(response.statusCode || 0);
        resolve({
          ok: status >= 200 && status < 300,
          status,
          text: async () => raw,
        });
      });
      response.on("error", (error) => {
        cleanup();
        reject(error);
      });
    });
    request.on("error", (error) => {
      cleanup();
      reject(error);
    });
    request.end(body);
  });
}

export async function requestJsonWithAddressFailover(endpoint, options, dependencies = {}) {
  const url = new URL(endpoint);
  const resolve4 = dependencies.resolve4 || resolveRealIpv4;
  const requestAddress = dependencies.requestAddress || requestJsonAtAddress;
  let addresses = [];
  try {
    addresses = [...new Set(await resolve4(url.hostname))];
  } catch {
    addresses = [];
  }
  const candidates = addresses.length ? addresses : [""];
  const failures = [];
  let lastError;
  for (const address of candidates) {
    try {
      return await requestAddress(url, { ...options, address });
    } catch (error) {
      if (error?.name === "AbortError" || options.signal?.aborted) throw error;
      lastError = error;
      failures.push(`${address || url.hostname}: ${transportErrorText(error)}`);
    }
  }
  const error = new Error(`All DeepSeek addresses failed: ${failures.join("; ")}`);
  error.name = "DeepSeekTransportError";
  error.cause = lastError;
  throw error;
}

function endpointFor(baseUrl) {
  const base = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return new URL("chat/completions", base).toString();
}

export class ModelProviderError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "ModelProviderError";
    this.status = options.status || 0;
    this.retryable = Boolean(options.retryable);
    this.userMessage = options.userMessage || "模型服务暂时不可用，请稍后再试。";
  }
}

export class DeepSeekProvider {
  constructor(config, options = {}) {
    this.apiKey = config.deepseekApiKey;
    this.baseUrl = config.deepseekBaseUrl;
    this.timeoutMs = config.modelTimeoutMs;
    this.maxOutputTokens = config.maxOutputTokens;
    this.fetchImpl = options.fetchImpl || null;
    this.sleep = options.sleep || sleep;
  }

  isConfigured() {
    return Boolean(this.apiKey && this.baseUrl);
  }

  async chat({ messages, model, thinking = false, signal }) {
    if (!this.isConfigured()) {
      throw new ModelProviderError("DeepSeek API key is missing", {
        userMessage: "尚未配置 DeepSeek API Key，暂时无法生成回复。",
      });
    }

    const body = {
      model,
      messages,
      thinking: { type: thinking ? "enabled" : "disabled" },
      max_tokens: this.maxOutputTokens,
      stream: false,
    };
    if (thinking) body.reasoning_effort = "high";

    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const controller = new AbortController();
      const abortFromCaller = () => controller.abort(signal?.reason);
      if (signal?.aborted) controller.abort(signal.reason);
      else signal?.addEventListener("abort", abortFromCaller, { once: true });
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);

      try {
        const requestOptions = {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${this.apiKey}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
          timeoutMs: Math.min(this.timeoutMs, 15_000),
        };
        const response = this.fetchImpl
          ? await this.fetchImpl(endpointFor(this.baseUrl), requestOptions)
          : await requestJsonWithAddressFailover(endpointFor(this.baseUrl), requestOptions);
        const raw = await response.text();
        if (!response.ok) {
          const retryable = response.status === 429 || response.status >= 500;
          const userMessage =
            response.status === 401 || response.status === 403
              ? "DeepSeek API 鉴权失败，请检查 API Key。"
              : response.status === 402
                ? "DeepSeek 账户余额或额度不足。"
                : response.status === 429
                  ? "DeepSeek 当前请求较多，稍后再试一下。"
                  : "DeepSeek 模型服务暂时不可用，请稍后再试。";
          throw new ModelProviderError(
            `DeepSeek HTTP ${response.status}: ${raw.slice(0, 500)}`,
            { status: response.status, retryable, userMessage },
          );
        }

        const payload = JSON.parse(raw);
        const choice = payload.choices?.[0]?.message;
        const text = String(choice?.content || "").trim();
        if (!text) {
          throw new ModelProviderError("DeepSeek returned an empty response", {
            userMessage: "模型这次没有生成有效内容，请重新发一次。",
          });
        }
        return {
          text,
          model: String(payload.model || model),
          usage: payload.usage || {},
        };
      } catch (error) {
        if (signal?.aborted) {
          throw new ModelProviderError("Request cancelled by user", {
            userMessage: "已取消这次请求。",
          });
        }
        if (error?.name === "AbortError") {
          lastError = new ModelProviderError("DeepSeek request timed out", {
            retryable: true,
            userMessage: `模型处理超过 ${Math.round(this.timeoutMs / 1000)} 秒，已停止本次请求。`,
          });
        } else if (error instanceof ModelProviderError) {
          lastError = error;
        } else {
          lastError = new ModelProviderError(transportErrorText(error), {
            retryable: true,
            userMessage: "暂时连接不上 DeepSeek，请稍后再试。",
          });
        }
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abortFromCaller);
      }

      if (!lastError.retryable || attempt === 2) throw lastError;
      await this.sleep([1200, 3500][attempt]);
    }
    throw lastError;
  }
}
