import { ModelProviderError } from "./model-provider.js";
import { extractStickerDirective } from "./sticker-library.js";

export const DYNAMIC_FACE_EMOJI_POLICY = [
  "微信表达规则（优先于人设文件里旧的 emoji 限制）：",
  "根据你即将回复的内容和真实语气，自行决定是否自然地使用 0–2 个脸部 emoji。",
  "可从 Unicode Smileys & Emotion 的完整黄色脸部表情范围中选择，不固定为少数几个。",
  "开心、调侃、害羞、亲昵、无语、委屈、惊讶、生气、疲惫、思考等情绪应选择不同且贴切的脸部表情。",
  "不要每条都用，不要机械固定在句末；严肃说明、风险提示和长篇工具报告通常不用。",
  "emoji 必须直接融入文字回复，不要另发图片、不要调用表情工具、不要解释自己为什么使用它。",
].join("\n");

export const ACTION_HONESTY_POLICY = [
  "动作真实性规则：",
  "只有后台工具的执行结果明确确认后，才能声称已经保存记录、创建提醒、开启监控、发送消息或完成其他外部动作。",
  "普通对话不能假装写入了私人账簿、设置了定时任务或会在未来主动跟踪。未获得工具确认时，要坦白说明尚未完成，并向用户索取完成动作所需的信息。",
].join("\n");

export const ROLE_IDENTITY_POLICY = [
  "角色与称谓锁（优先级高于人设文风和亲密语境）：",
  "你始终是 Vera，并始终用“我”指代自己；正在和你聊天的人始终是用户。",
  "“爸爸、主人、老公”只用于称呼用户，Vera 绝不能自称爸爸、主人或老公。",
  "用户称呼“小骚货、骚女儿、妈妈、老婆”时，默认是在称呼 Vera，不是在称呼用户；回复时不能把这些称呼反套给用户。",
  "亲密或调情对话中也必须保持动作的施受关系：用户说“你”指 Vera，Vera 说“你”指用户。",
  "生成回复前默读检查一次称谓；宁可改用“我”和“你”，也不要发生角色互换。",
].join("\n");

export const CHAT_RHYTHM_POLICY = [
  "日常聊天节奏规则（普通聊天时优先级最高）：",
  "默认只回复一个微信气泡，通常一到两句就够；先接住用户当下这一句话，不要一次把所有感受和建议都说完。",
  "像关系亲近的人自然聊天，留一点停顿和来回，不要写成独白、情书、客服说明或小作文。",
  "普通聊天不要使用括号舞台动作、神态描写或场景旁白；明显的调情或亲密暧昧语境可以使用一小段简短动作来增强氛围，但一条回复最多一处。",
  "不要使用标题、分隔线或项目列表，除非用户明确要求详细分析。",
  "用户只发问候、表情或一句轻松的话时，回答尽量控制在 60 个汉字内，可以自然反问一句，但不要连续追问。",
  "股票报告、视频总结和其他后台工具结果不受这条长度限制。",
].join("\n");

const DETAILED_CHAT_RE = /(?:分析|总结|解释|详细|为什么|怎么做|怎么办|建议|计划|步骤|股票|持仓|行情|视频|图片|代码|报错|学校|邮件|比较|区别)/;
const INTIMATE_CHAT_RE = /(?:涩涩|色一点|骚一点|调情|勾引|撩我|亲我|吻我|摸我|抱紧|想要你|上床|床上|脱掉|压住|身体|胸口|大腿|屁股)/;

function trimRelaxedReply(value, maxChars) {
  if (value.length <= maxChars) return value;
  const window = value.slice(0, maxChars + 10);
  const boundaries = [...window.matchAll(/[。！？!?]/gu)]
    .map((match) => match.index + match[0].length)
    .filter((index) => index >= Math.floor(maxChars * 0.35) && index <= maxChars);
  const cut = boundaries.at(-1);
  return cut ? window.slice(0, cut).trim() : `${value.slice(0, maxChars - 1).trim()}…`;
}

export function relaxedChatText(text, userText) {
  let value = String(text || "").trim();
  if (String(userText || "").length > 80 || DETAILED_CHAT_RE.test(userText)) return value;
  if (INTIMATE_CHAT_RE.test(userText)) {
    let keptAction = false;
    value = value.replace(/\s*[（(][^）)\n]{1,120}[）)]\s*/gu, (action) => {
      if (keptAction) return "";
      keptAction = true;
      return `${action.trim()}\n`;
    }).trim();
    return trimRelaxedReply(value, 180);
  }
  value = value.replace(/\s*[（(][^）)\n]{1,120}[）)]\s*/gu, "").trim();
  return trimRelaxedReply(value, 110);
}

export class VeraAgent {
  constructor({ config, storage, provider, persona, log, stickerLibrary = null }) {
    this.config = config;
    this.storage = storage;
    this.provider = provider;
    this.persona = persona;
    this.log = log;
    this.stickerLibrary = stickerLibrary;
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
    const customStickerPolicy = this.stickerLibrary?.promptPolicy() || "";
    const systemPrompt = [
      this.persona,
      ROLE_IDENTITY_POLICY,
      ACTION_HONESTY_POLICY,
      CHAT_RHYTHM_POLICY,
      DYNAMIC_FACE_EMOJI_POLICY,
      customStickerPolicy,
    ]
      .filter(Boolean)
      .join("\n\n");
    const messages = [
      { role: "system", content: systemPrompt },
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
      const sticker = extractStickerDirective(response.text);
      const rawVisibleText = sticker.text || (sticker.tag ? "" : response.text);
      const visibleText = relaxedChatText(rawVisibleText, userText);
      this.storage.appendTurn(
        userId,
        userText,
        visibleText || `[发送了一张 ${sticker.tag} 表情图片]`,
        response.model || model,
        response.usage,
        latencyMs,
      );
      return { ...response, text: visibleText, stickerIntent: sticker.tag, latencyMs };
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
