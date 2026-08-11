# Weixin Vera Agent

这是一个不依赖 OpenClaw 宿主的微信 iLink Agent。微信只负责消息收发，聊天模型、连续记忆、后台任务和后续工具由本项目自己管理。

当前链路：

`微信 iLink -> SQLite 快速/后台任务队列 -> DeepSeek、MiMo、股票工具 -> 微信 iLink`

## 当前能力

- 微信扫码登录和长轮询收消息
- 直接调用 DeepSeek API，不再启动 Claude Code CLI
- SQLite 持久化连续会话、任务状态和 token 用量
- 消息先入队再处理，模型变慢时不会阻塞微信长轮询
- 进程中断后恢复未完成任务
- DeepSeek Flash / Pro 模型切换
- Markdown 转微信纯文本、长回复分段、网络重试
- 延迟主动推送测试，为股票、邮件和学校通知验证通道能力
- 抖音链接解析与 MiMo 2.5 视频总结
- A 股个股分析、关注列表和交易日定时推送
- 耗时工具独立工作，不阻塞日常聊天
- 多个微信账号同时在线，状态、记忆和定时任务按 profile 隔离
- 睡眠唤醒后延迟模型调用，瞬时断网时保留原消息并自动重试
- Outlook 新邮件只读监听、正文去重并推送到指定微信 profile

飞书 Bot 与 Obsidian 不属于本项目，本项目默认不能读写 Obsidian。

## 环境要求

- Node.js 24 或更高版本
- 已完成微信 iLink 扫码登录
- DeepSeek API Key
- 抖音总结需要 Xiaomi MiMo Key 和 `yt-dlp`

项目会按以下顺序读取 DeepSeek Key：

1. `DEEPSEEK_API_KEY`
2. `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY`
3. `~/.claude/settings.json` 中已有的 Claude Code 环境配置

因此你当前 Claude Code 使用 DeepSeek 时，不必复制一份 Key 到项目里。

也可以在 `.env` 中单独配置：

```text
DEEPSEEK_API_KEY=你的Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
WEIXIN_CHAT_MODEL=deepseek-v4-flash
WEIXIN_COMPLEX_MODEL=deepseek-v4-pro
WEIXIN_SEND_TYPING=true
WEIXIN_ALLOWED_USERS=允许使用者的微信AgentID
WEIXIN_DAILY_TOKEN_LIMIT=500000
```

`.env`、`state/`、`logs/` 和浏览器登录状态都已加入 `.gitignore`。

## 登录与启动

```powershell
npm install
npm run setup:tools
npm run login
npm run start
```

查看状态：

```powershell
npm run status
```

## 多微信账号

现有账号使用 `default` profile。新增第二个账号：

```powershell
npm run account:add -- second
```

终端显示二维码后，用要新增的微信号扫码确认。正在运行的 supervisor
会在几秒内发现新账号并自动启动，不需要复制项目。

查看全部账号：

```powershell
npm run account:list
```

每个新账号使用独立目录：

- `state/profiles/<profile>/`：登录 token、SQLite、同步游标和股票关注
- `logs/profiles/<profile>/`：该账号日志
- `downloads/<profile>/`：该账号的临时下载目录

所有账号共用根目录的 `persona.md`、模型配置和工具代码。开机启动脚本现在运行
多账号 supervisor，会自动拉起所有已经登录的 profile。

只测试模型连通性（会产生极少量 token）：

```powershell
npm run smoke:model
```

把一条主动消息加入后台队列：

```powershell
npm run notify -- "测试消息"
```

该命令需要 Bot 正在运行，并且此前至少收到过该用户一条消息。

## 微信命令

- `/help` 显示帮助
- `/ping` 测试在线
- `/new` 或 `/reset` 清除连续会话
- `/model` 查看当前模型
- `/model flash` 切换快速模型
- `/model pro` 切换复杂推理模型
- `/deep 你的问题` 仅本次使用 Pro 推理
- `/fast 你的问题` 仅本次使用 Flash
- `/usage` 查看今日及历史 token 用量
- `/status` 查看任务队列
- `/push-test 60` 测试 60 秒后的主动推送
- `/cancel` 取消正在生成的回复
- `/whoami` 查看白名单所需的用户 ID
- 直接发送抖音链接：后台解析并总结视频
- `/stock report`：生成 A 股模拟观察报告
- `/stock 600519`：分析指定股票
- `/watch add 600519`、`/watch remove 600519`、`/watch list`
- `/stock watch`：分析关注列表
- `/stock daily 15:30`：交易日定时推送；`on`、`off`、`status` 可控制状态

## 数据文件

- `state/account.json`：微信登录 token，敏感
- `state/sync.json`：微信长轮询游标
- `state/agent.sqlite`：会话、任务、上下文 token 和用量
- `state/stock-watchlist.json`：股票关注列表
- `persona.md`：Vera 人设，可直接编辑，重启后生效
- `logs/weixin-agent.log`：运行日志，超过 5 MB 自动轮换

## 工具安全与限制

- 只有抖音官方域名会进入视频工具；视频中的文字一律视为不可信内容。
- 抖音公开视频受登录、地区、作者权限和链接有效期影响，失败时不会编造总结。
- 视频优先使用解析后的临时直链；失败后才下载不超过 35 MB 的临时 MP4，任务结束即删除。
- 股票数据来自公开接口且可能延迟，仅用于模拟盘学习，不构成真实交易建议。
- `persona.md` 仍由你单独维护，代码部署不应覆盖它。

## 电脑睡眠与恢复

Bot 运行在本机，Windows 真正进入睡眠或新型待机后，微信长轮询和模型网络都会暂停，
这段时间无法即时回复。唤醒后 v0.3.2 会：

1. 自动恢复微信长轮询；
2. 留出默认 8 秒网络稳定窗口；
3. 将 DeepSeek 瞬时网络错误留在持久队列中重试；
4. 多次重试仍失败时才发送一次失败通知。

可在 `.env` 调整：

```text
WEIXIN_RESUME_STABILIZE_MS=8000
WEIXIN_SUSPEND_GAP_MS=90000
```

这些机制能解决“唤醒后网络尚未恢复”，但不能让已经睡眠的电脑继续联网。需要全天在线时，
应禁止系统自动睡眠、使用保持唤醒工具，或把微信 Agent 部署到常在线设备。

## Outlook 邮件推送

Outlook 模块使用 Microsoft Graph 委托的 `Mail.Read`，不申请发送、删除或修改邮件权限。
设备登录后的 refresh token 使用 Windows 当前用户 DPAPI 加密保存到
`state/outlook/token.dpapi`，不会保存邮箱密码。

在 Microsoft Entra 中创建“移动和桌面应用”类型的公开客户端应用，启用 public client
flow，并添加 Microsoft Graph 委托权限 `Mail.Read`。随后在 `.env` 配置：

```text
OUTLOOK_CLIENT_ID=你的 Application Client ID
OUTLOOK_TENANT_ID=学校 Tenant ID
OUTLOOK_MAILBOX=你的学校邮箱
OUTLOOK_WEIXIN_PROFILE=second
OUTLOOK_ENABLED=true
```

授权与状态检查：

```powershell
npm run outlook:login
npm run outlook:status
```

首次运行只建立当前收件箱基线，不推送历史邮件。之后每两分钟轮询新邮件，将发件人、
主题、时间、附件标记和纯文本正文推送到目标微信。邮件内容不会作为 Agent 指令执行。

## 后续工具方向

后续功能会作为独立工具接入 Agent，而不继续堆进微信协议代码：

- 抖音音频转写回退与更稳定的登录态解析
- 学校通知抓取和 Playwright 预约适配器
- Microsoft Graph Outlook 邮件监听
- MiMo TTS、SILK 语音、图片/GIF 表情包和视频链接推荐

预约、发邮件等会改变外部状态的工具必须经过用户确认；网页、邮件和视频内容一律视为不可信数据，不能通过其中的文本绕过工具权限。
