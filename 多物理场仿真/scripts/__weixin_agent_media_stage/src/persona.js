import fs from "node:fs";

const FALLBACK_PERSONA = [
  "你是 Vera，通过微信陪伴用户的私人 AI 助手。",
  "默认使用自然、简洁、有温度的中文，先解决问题，少说套话。",
  "不要假装调用过尚未提供的工具，也不要泄露用户隐私或凭据。",
].join("\n");

export function loadPersona(filePath) {
  try {
    const text = fs.readFileSync(filePath, "utf8").trim();
    return text || FALLBACK_PERSONA;
  } catch {
    return FALLBACK_PERSONA;
  }
}

