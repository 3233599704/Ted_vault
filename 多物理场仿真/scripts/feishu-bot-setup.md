---
title: 飞书 Bot 接入指南
tags:
  - 自动化
  - 飞书
  - Claude
created: 2026-06-12
---

# 飞书 Bot 接入 Claude Code — 手机控制 Obsidian

> 手机飞书发消息 → Claude Code 处理 → 回复到飞书。不用公网 IP，不用服务器，长连接搞定。

---

## 🎯 架构

```
📱 手机飞书
    ↕
☁️ 飞书服务器
    ↕ WebSocket 长连接（本地电脑）
💻 feishu-claude-bot.py
    ↕ subprocess
🤖 Claude Code CLI
    ↕
📂 Obsidian Vault
```

---

## 📋 第一步：创建飞书应用

1. 打开 [飞书开发者后台](https://open.feishu.cn/app)
2. 创建**企业自建应用**
3. 添加应用能力 → 勾选 **机器人**
4. 权限管理 → 搜索并开通：

| 权限 | 用途 |
|:---|:---|
| `im:message.p2p_msg:readonly` | 读取用户发给机器人的单聊消息 |
| `im:message:send_as_bot` | 以机器人身份发送消息 |
| `im:message.group_at_msg:readonly` | 接收群聊中 @机器人的消息 |

5. 事件订阅 → 添加事件 → **接收消息 v2.0** → `im.message.receive_v1`
6. 订阅方式选 **使用长连接**（无需配置请求网址！）
7. 发布应用 → 创建版本 → 申请发布（自己用不需要审核）

---

## 📋 第二步：获取凭证

在飞书开发者后台 → 应用详情页：

- **App ID**：左侧 `凭证与基础信息` → `App ID`
- **App Secret**：左侧 `凭证与基础信息` → `App Secret`

---

## 📋 第三步：安装依赖

```bash
pip install lark-oapi
```

确认 Claude Code CLI 可用：

```bash
claude --version
```

---

## 📋 第四步：启动 Bot

凭证保存在 Windows 用户级环境变量中，脚本不会保存 App Secret。
首次设置或重置凭证后，需要完全重启 VS Code 或终端，再运行：

```bash
cd "D:\Staid\app\Obsidian\Ted_vault\多物理场仿真\scripts"
py feishu-claude-bot.py
```

看到 `[OK] Connected to wss://msg-frontier.feishu.cn/...` 就成功了。

> 如果遇到 `UnicodeEncodeError`，说明 Windows 控制台编码问题，脚本已内置 UTF-8 修复。
> 
> 如果想加白名单限制使用者，设置环境变量 `FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy`。

---

## 📋 第五步：开机自启

Windows 计划任务 `FeishuClaudeBot` 会在用户登录后隐藏运行：

```powershell
Start-ScheduledTask -TaskName "FeishuClaudeBot"
Stop-ScheduledTask -TaskName "FeishuClaudeBot"
```

Bot 有单实例保护，重复启动不会产生多个连接。计划任务只保存脚本路径，
飞书凭证仍从 Windows 用户环境变量读取。

---

## 📱 第六步：手机上用

1. 打开飞书 App
2. 搜索你的应用名称 → 点进去
3. 发消息 → Bot 回复 Claude 的处理结果

群聊里 @机器人 也能触发。

---

## 🔒 安全建议

| 配置 | 说明 |
|:---|:---|
| `FEISHU_ALLOWED_USERS` | 只允许指定用户使用（强烈建议） |
| `MAX_CLAUDE_SECONDS=120` | 单次调用最多 120 秒 |
| 脚本里可加命令黑名单 | 防止 `rm -rf` 等危险操作 |

---

## 🔄 替代方案：微信（等 Android 开放）

等 Android 微信 ClawBot 插件上线后，把脚本里的飞书 SDK 换成 iLink API 即可。核心逻辑完全一样——长轮询收消息 → Claude Code 处理 → 回复。参考 `claude-code-wechat-channel`。
