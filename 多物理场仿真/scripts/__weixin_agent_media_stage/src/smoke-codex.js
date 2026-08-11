import path from "node:path";

import { CONFIG } from "./config.js";
import { CodexDiagnosticTool } from "./tools/codex-diagnostic-tool.js";

const adminUser = "codex-smoke-admin";
const config = {
  ...CONFIG,
  codexEnabled: true,
  codexAdminUsers: new Set([adminUser]),
  codexWorkspaceDir: path.join(CONFIG.projectDir, "codex-workspace", "smoke"),
};

const tool = new CodexDiagnosticTool(config);
const result = await tool.execute({
  job: {
    id: `codex-smoke-${Date.now()}`,
    user_id: adminUser,
    payload: {
      args: {
        request: "检查当前项目中可能导致微信 Bot 收到消息后不回复的代码风险，只做只读诊断。",
      },
    },
  },
});

console.log(JSON.stringify({
  ok: true,
  model: result.model,
  reply: result.reply,
  usage: result.usage,
}, null, 2));
