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
- 抖音总结自动追加到 `records/douyin/抖音视频总结.md`
- 微信图片 CDN 解密、MiMo 视觉识别与连续对话
- 微信语音优先使用自带转写，缺失时解码 SILK 并调用 MiMo ASR
- A 股个股分析、关注列表和交易日定时推送
- 同花顺模拟盘多因子候选、交易账本、持仓检查和分批买卖提醒
- 微信图片上传与本地表情包库
- 自然语言一次性提醒，重启不丢，睡眠唤醒后补发
- 文件名标签驱动的自定义表情图库，模型按语境自动选择并控制视觉尺寸
- 耗时工具独立工作，不阻塞日常聊天
- 多个微信账号同时在线，状态、记忆和定时任务按 profile 隔离
- 睡眠唤醒后延迟模型调用，瞬时断网时保留原消息并自动重试

飞书 Bot 与 Obsidian 不属于本项目，本项目默认不能读写 Obsidian。

## 环境要求

- Node.js 24 或更高版本
- 已完成微信 iLink 扫码登录
- DeepSeek API Key
- 抖音总结、图片识别和语音识别需要 Xiaomi MiMo Key
- 抖音使用隔离 Chrome 取流，`yt-dlp` 仅作为备用解析器
- AKShare 用于刷新沪深300成分股快照，运行时通过腾讯公开接口获取实时报价和复权日线
- 自定义表情归一化需要 Pillow（`npm run setup:tools` 会安装）

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
npm run setup:stocks
npm run login
npm run start
```

`setup:stocks` 会安装 AKShare 与 BaoStock，并生成带点时行业分类的 `data/csi300-universe.json`。建议每月运行一次 `npm run stock:refresh-universe` 更新成分股；日常筛选不会再等待容易卡住的全市场接口。

研究策略时可运行 `npm run stock:backtest`。它会缓存历史沪深300成分、行业和日线，使用次日开盘成交及真实成本假设生成 `records/stocks/选股策略点时回测.md`；缓存数据库不会提交到版本库。

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

## 自然语言使用

平时不需要记斜杠命令，直接像聊天一样说：

- “看看今天的股票”或“今天有没有值得关注的股票”
- “看看我的模拟盘持仓”或“我的持仓该不该卖”
- “我的模拟盘本金设为 10 万”
- “我买了 200 股 002415，成交价 34.5”
- “分析一下股票 600519”
- “今晚八点提醒我写作业”或“半小时后提醒我拿快递”
- “看看我的提醒”“取消最近的提醒”或“取消所有提醒”

Vera 会根据自己回复的情感，自行决定是否在文字中自然使用脸部 emoji。可选范围覆盖 Unicode `Smileys & Emotion` 中的黄色脸部表情，不固定为六个，也不要求用户先说“发个表情”。通常使用 0–2 个，严肃说明和工具报告会克制使用。iLink 当前没有开放微信收藏表情的原生消息类型，自定义贴纸只能作为图片消息发送。

日常聊天中，模型回复里的空行会被转换成多条微信消息，最多拆成六条，让连续对话更接近真人聊天。股票推荐、持仓报告、抖音总结和其他后台工具结果保持一条完整消息，不按段落拆分。

把静态图片放进 `stickers/custom/` 后会立即成为可选表情，不用重启。文件名用下划线写场景标签，例如 `开心_夸奖_得意.png`。Vera 会按回复语境自行决定是否调用；发送前会移除与四周连通的近白色底色，再按原图长宽比添加一圈窄透明边，主体默认占约 82%，不会使用大面积正方形留白。处理缓存不会改动原图。详细规则见 `stickers/custom/README.md`。

提醒属于本地持久任务。电脑正常运行时会到点发送；电脑睡眠时无法联网，唤醒后会补发。当前支持一次性提醒，不包含每天、每周等循环提醒。

## 调试命令

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
- 直接发送图片：识别画面和文字，并继续对话
- 直接发送语音：像文字一样连续聊天
- `/stock report`：生成 A 股模拟观察报告
- `/stock picks`：按模拟本金生成最多三只候选、首仓股数和风控线
- `/paper capital 100000`：设置同花顺模拟盘本金
- `/paper buy 600519 100 1500`：记录 100 股、成交价 1500 的模拟买入
- `/paper sell 600519 100 1600`：记录模拟卖出
- `/paper portfolio`：更新持仓盈亏、减仓/清仓数量与原因
- `/stock 600519`：分析指定股票
- `/watch add 600519`、`/watch remove 600519`、`/watch list`
- `/stock watch`：分析关注列表
- `/stock daily 15:30`：交易日定时推送；`on`、`off`、`status` 可控制状态
- `/sticker list`、`/sticker smirk`：查看或发送内联 emoji
- `/reminder list`、`/reminder cancel`、`/reminder cancel all`：提醒调试命令
- 模拟交易也可以直接说“我买了 100 股 600519，成交价 1500”

## 数据文件

- `state/account.json`：微信登录 token，敏感
- `state/sync.json`：微信长轮询游标
- `state/agent.sqlite`：会话、任务、上下文 token 和用量
- `state/stock-watchlist.json`：股票关注列表
- `state/paper-portfolio.json`：模拟本金、现金、持仓和交易流水
- `state/douyin-browser/`：只用于抖音的隔离浏览器状态
- `state/douyin-cookies.txt`：只包含抖音域名的 Cookie，敏感
- `records/douyin/抖音视频总结.md`：可长期复盘的视频摘要
- `records/stocks/<profile>/模拟盘复盘.md`：候选、持仓检查和模拟交易记录
- `data/csi300-universe.json`：AKShare/CSI 成分与 BaoStock 行业组成的沪深300候选池快照
- `stickers/`：本地表情包与 `manifest.json`
- `stickers/custom/`：你自行添加的静态表情原图，文件名即场景标签
- `stickers/cache/`：自动留白和缩放后的发送缓存，可安全删除
- `persona.md`：Vera 人设，可直接编辑，重启后生效
- `logs/weixin-agent.log`：运行日志，超过 5 MB 自动轮换

## 工具安全与限制

- 只有抖音官方域名会进入视频工具；视频中的文字一律视为不可信内容。
- 抖音公开视频受验证码、地区、作者权限和链接有效期影响，失败时不会编造总结。
- Bot 会自动启动无界面的隔离 Chrome 取流；若提示刷新访问状态，运行 `open-douyin-browser.ps1` 完成一次验证。
- 隔离浏览器与日常 Chrome 完全分开，Cookie 导出只保留 `douyin.com` 和 `iesdouyin.com`。
- 视频下载上限默认 35 MB，只在内存或任务临时目录中处理；取流标签页禁用缓存并在结束后清理浏览器缓存。
- 微信图片和语音经官方 CDN 下载并用 AES-128-ECB 解密；明文只在当前任务内存中处理。
- 图片中的文字一律视为不可信资料，不会触发工具或外部操作。
- 股票数据来自公开接口且可能延迟，仅用于模拟盘学习，不构成真实交易建议。
- 选股在沪深300成分股中使用趋势、跳过最近一月的中期动量、基本面质量、估值、低波动、流动性和风险七类横截面因子；质量数据缺失时保持中性，不用猜测值补齐。
- 最多三只候选时，同一细分行业最多出现一只，避免表面持有多只、实际集中押注银行等单一行业。
- 候选门槛为 72 分，最多同时持有三只，单只上限 20%，首仓约 10%，默认硬止损 8%。达不到门槛时不会推荐。
- 记录第一笔模拟买入后会自动开启交易日 `15:30` 的持仓检查；任何建议都不会操作真实账户。
- `persona.md` 仍由你单独维护，代码部署不应覆盖它。

## 电脑睡眠与恢复

Bot 运行在本机，Windows 真正进入睡眠或新型待机后，微信长轮询和模型网络都会暂停，
这段时间无法即时回复。唤醒后 v0.7.3 会：

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

## 后续工具方向

后续功能会作为独立工具接入 Agent，而不继续堆进微信协议代码：

- 学校通知抓取和 Playwright 预约适配器
- MiMo TTS、微信原生语音回复、GIF 表情包和视频链接推荐

预约、发邮件等会改变外部状态的工具必须经过用户确认；网页、邮件和视频内容一律视为不可信数据，不能通过其中的文本绕过工具权限。
