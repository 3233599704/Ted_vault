import fs from "node:fs";

import { CONFIG } from "./config.js";
import { OutlookAuthClient } from "./outlook-auth-client.js";

async function main() {
  const command = (process.argv[2] || "status").toLowerCase();
  const auth = new OutlookAuthClient(CONFIG);
  if (command === "login") {
    const record = await auth.deviceLogin((code) => {
      console.log("\n请在浏览器打开：", code.verificationUri);
      console.log("输入设备代码：", code.userCode);
      if (code.message) console.log("\n微软提示：", code.message);
      console.log("\n请使用学校邮箱登录并同意只读邮件权限。\n");
    });
    console.log(`Outlook 授权完成：${record.username}`);
    return;
  }
  if (command === "status") {
    const record = await auth.loadRecord();
    console.log(JSON.stringify({
      configured: auth.isConfigured(),
      tokenStored: fs.existsSync(CONFIG.outlookTokenFile),
      mailbox: CONFIG.outlookMailbox,
      tenantId: CONFIG.outlookTenantId,
      targetProfile: CONFIG.outlookWeixinProfile,
      authorizedUser: record?.username || "",
      updatedAt: record?.updatedAt || "",
    }, null, 2));
    return;
  }
  throw new Error("用法：npm run outlook:login 或 npm run outlook:status");
}

main().catch((error) => {
  console.error(`Outlook 授权失败：${error?.userMessage || error?.message || error}`);
  process.exitCode = 1;
});
