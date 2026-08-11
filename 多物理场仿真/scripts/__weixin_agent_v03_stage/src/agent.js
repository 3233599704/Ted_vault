import { ModelProviderError } from "./model-provider.js";

export class VeraAgent {
  constructor({ config, storage, provider, persona, log }) {
    this.config = config;
    this.storage = storage;
    this.provider = provider;
    this.persona = persona;
    this.log = log;
    this.active = new Map();
  }

  selectedModel(userId) {
    return this.storage.getUserModel(userId) || this.config.chatModel;
  }

  setModel(userId, mode) {
    const model = mode === "pro" ? this.config.complexModel : this.config.chatModel;
    this.storage.setUserModel(userId, model);
    return model;
  }

  reset(userId) {
    this.storage.resetConversation(userId);
  }

  cancel(userId) {
    const controller = this.active.get(userId);
    if (!controller) return false;
    controller.abort(new Error("Cancelled by user"));
    return true;
  }

  async chat(userId, userText, options = {}) {
    if (userText.length > this.config.maxInputChars) {
      return {
        text: `这条消息太长了，请控制在 ${this.config.maxInputChars} 个字符以内，或分几次发给我。`,
        model: "local",
        usage: {},
        latencyMs: 0,
      };
    }
    if (this.active.has(userId)) {
      return {
        text: "上一条消息还在处理中。你可以稍等一下，或者发送 /cancel 取消它。",
        model: "local",
        usage: {},
        latencyMs: 0,
      };
    }

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dailyUsage = this.storage.getUsageSummary(userId, today.getTime());
    if (dailyUsage.total_tokens >= this.config.dailyTokenLimit) {
      return {
        text: `今天已经使用 ${dailyUsage.total_tokens} tokens，达到每日安全上限。明天会自动恢复，也可以调整 WEIXIN_DAILY_TOKEN_LIMIT 后重启。`,
        model: "local",
        usage: {},
        latencyMs: 0,
      };
    }

    const model = options.model || this.selectedModel(userId);
    const thinking = options.thinking ?? model === this.config.complexModel;
    const history = this.storage.getHistory(
      userId,
      this.config.historyMessages,
      this.config.historyChars,
    );
    const messages = [
      { role: "system", content: this.persona },
      ...history,
      { role: "user", content: userText },
    ];
    const controller = new AbortController();
    this.active.set(userId, controller);
    const started = Date.now();

    try {
      const response = await this.provider.chat({
        messages,
        model,
        thinking,
        signal: controller.signal,
      });
      const latencyMs = Date.now() - started;
      this.storage.appendTurn(
        userId,
        userText,
        response.text,
        response.model || model,
        response.usage,
        latencyMs,
      );
      return { ...response, latencyMs };
    } catch (error) {
      const latencyMs = Date.now() - started;
      if (controller.signal.aborted) {
        return { text: "", model, usage: {}, latencyMs, cancelled: true };
      }
      if (error instanceof ModelProviderError) {
        this.log(`模型调用失败: ${error.message.slice(0, 500)}`);
        if (error.retryable) throw error;
        return { text: error.userMessage, model, usage: {}, latencyMs };
      }
      this.log(`模型调用异常: ${String(error).slice(0, 500)}`);
      return {
        text: "模型调用出现异常，请稍后再试。",
        model,
        usage: {},
        latencyMs,
      };
    } finally {
      this.active.delete(userId);
    }
  }
}
