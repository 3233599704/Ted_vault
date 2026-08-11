function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    this.fetchImpl = options.fetchImpl || globalThis.fetch;
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
        const response = await this.fetchImpl(endpointFor(this.baseUrl), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${this.apiKey}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
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
          lastError = new ModelProviderError(String(error), {
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

