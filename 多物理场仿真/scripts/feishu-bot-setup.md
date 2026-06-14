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
| `im:message:readonly` | 下载用户发给机器人的图片资源 |
| `im:resource` | 获取与上传图片或文件资源，用于上传 Bot 生成的 Opus 语音 |

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

Bot 内置长连接健康监控，每 20 秒记录一次状态。电脑从休眠恢复、网络切换
或 DNS 暂时失败后，如果连接连续异常超过 4 分钟，Bot 会主动退出坏进程，
并通过原有 `FeishuClaudeBot` 计划任务重新启动。健康状态保存在
`.feishu-bot-health.json`。

休眠期间电脑无法维持飞书 WebSocket，期间发送的事件不保证在恢复后补投。
Watchdog 可以缩短恢复窗口，但无法找回飞书没有再次投递的历史消息。

---

## 📱 第六步：手机上用

1. 打开飞书 App
2. 搜索你的应用名称 → 点进去
3. 发消息 → Bot 回复 Claude 的处理结果

群聊里 @机器人 也能触发。

同一位飞书用户的消息会自动恢复上一次 Claude 会话，Bot 或电脑重启后也能继续。
发送 `/new`、`/reset` 或 `新会话` 可以清空当前用户的聊天上下文。

### 图片识别

图片会先保存到 `多物理场仿真/raw/图片/`，再交给单独的视觉模型分析。
当前文字模型 DeepSeek 不支持图片输入，因此需要配置一个视觉 API。

使用 Anthropic Claude Vision：

```powershell
[Environment]::SetEnvironmentVariable("VISION_PROVIDER", "anthropic", "User")
[Environment]::SetEnvironmentVariable("VISION_API_KEY", "你的 Anthropic API Key", "User")
[Environment]::SetEnvironmentVariable("VISION_MODEL", "claude-sonnet-4-6", "User")
```

或使用 OpenAI Vision：

```powershell
[Environment]::SetEnvironmentVariable("VISION_PROVIDER", "openai", "User")
[Environment]::SetEnvironmentVariable("VISION_API_KEY", "你的 OpenAI API Key", "User")
[Environment]::SetEnvironmentVariable("VISION_MODEL", "gpt-5.4-mini", "User")
```

或使用小米 MiMo：

```powershell
[Environment]::SetEnvironmentVariable("VISION_PROVIDER", "xiaomi", "User")
[Environment]::SetEnvironmentVariable("VISION_API_KEY", "你的 MiMo API Key", "User")
[Environment]::SetEnvironmentVariable(
  "VISION_API_URL",
  "https://api.xiaomimimo.com/v1/chat/completions",
  "User"
)
[Environment]::SetEnvironmentVariable("VISION_MODEL", "mimo-v2-omni", "User")
```

配置后重启 `FeishuClaudeBot`。API Key 仅保存在 Windows 用户环境变量中。

### 语音回复

语音由小米 MiMo TTS 合成，再转成飞书语音消息所需的 Opus。当前使用
Voice Clone 固定复用 Kafka 参考音色。

```powershell
py -m pip install imageio-ffmpeg
[Environment]::SetEnvironmentVariable("TTS_API_KEY", "你的 MiMo API Key", "User")
[Environment]::SetEnvironmentVariable(
  "TTS_API_URL",
  "https://api.xiaomimimo.com/v1/chat/completions",
  "User"
)
[Environment]::SetEnvironmentVariable(
  "TTS_MODEL",
  "mimo-v2.5-tts-voiceclone",
  "User"
)
[Environment]::SetEnvironmentVariable("TTS_VOICE_NAME", "Kafka", "User")
[Environment]::SetEnvironmentVariable(
  "TTS_VOICE_REFERENCE",
  "voice-previews\kafka-reference-short.wav",
  "User"
)
[Environment]::SetEnvironmentVariable("TTS_PLAYBACK_SPEED", "1.10", "User")
[Environment]::SetEnvironmentVariable("TTS_DYNAMIC_STYLE", "true", "User")
[Environment]::SetEnvironmentVariable("TTS_DIRECTOR_MODEL", "mimo-v2-flash", "User")
```

飞书命令：

- `/voice on`：开启纯语音回复模式
- `/voice off`：关闭语音模式，恢复纯文字回复
- `/voice status`：查看状态和当前音色
- `/voice 你的问题`：仅本次用语音回答

普通模式只发送文字，语音模式只发送语音。若语音生成或上传失败，Bot 会把
完整答案回退为文字，避免丢失回复。

语音模式下，Claude 默认把答案压缩到 3 至 6 个简短句子；用户明确要求详细
解释或完整步骤时仍会展开。合成后的音频默认以 `1.10x` 不变调播放。

文字发送前会自动转换常见 Markdown：标题、列表、链接、代码块和表格会整理
成飞书 `text` 消息可读的纯文本格式，不再显示原始 Markdown 标记。

保留的自定义参考音色：

- `voice-previews\kafka-reference-short.wav`：当前使用，Kafka 短参考
- `voice-previews\kafka-reference.wav`：Kafka 完整参考备份
- `voice-previews\vera-velvet.wav`：成熟、温润、御姐感
- `voice-previews\vera-cool-idol.wav`：冷艳、清透、利落

切换参考音色时修改 `TTS_VOICE_REFERENCE` 和 `TTS_VOICE_NAME`，然后重启
`FeishuClaudeBot`。

音色保持固定，演绎风格会根据回复内容自动选择：

- `natural`：自然聊天
- `romantic`：情话专用，自然连贯、柔和亲昵，略带暧昧和笑意，感动
- `technical`：技术讲解
- `comforting`：安慰陪伴
- `cheerful`：好消息与庆祝
- `warning`：风险提醒
- `narrative`：故事叙述
- `summary`：总结和行动项

语音导演只选择预设并做少量微调，不会修改实际回复文字。

MiMo 返回 `HTTP 429` 时，Bot 会自动退避重试三次。若某段文本受到模型内容
限制而没有生成音频，Bot 会回退发送文字，日志会记录 MiMo 的结束原因。

---

## 📈 A 股模拟研究助手

股票功能会在 A 股收盘后从整个市场筛选 3 至 5 只模拟关注候选，并用普通话
说明“最近整体向上还是向下、为什么值得观察、哪里需要小心”。它不会输出
目标价、仓位、保证盈利等内容，只适合在同花顺模拟盘中学习和记录。

推荐安装 AKShare，以取得更完整的财务和公告数据：

```powershell
py -m pip install akshare
```

如果暂时没有安装，Bot 会自动使用免安装的东方财富公开行情备用通道，仍可完成
全市场价格趋势筛选；备用通道的财务和公告信息较少，报告会如实省略缺失部分。

推荐配置：

```powershell
[Environment]::SetEnvironmentVariable("STOCK_ENABLED", "true", "User")
[Environment]::SetEnvironmentVariable("STOCK_REPORT_TIME", "15:30", "User")
[Environment]::SetEnvironmentVariable("STOCK_TIMEZONE", "Asia/Shanghai", "User")
[Environment]::SetEnvironmentVariable(
  "FEISHU_STOCK_NOTIFY_USERS",
  "你的飞书 open_id",
  "User"
)
```

如果已经配置 `FEISHU_ALLOWED_USERS` 或 `FEISHU_NOTIFY_USERS`，股票日报会在
没有单独设置 `FEISHU_STOCK_NOTIFY_USERS` 时复用它们。配置后重启
`FeishuClaudeBot`。

飞书命令：

- `/stock report`：立即生成今天的全市场模拟关注名单
- `/stock 600519`：查看指定股票，单次最多 5 只
- `/watch add 600519`：加入个人观察列表
- `/watch remove 600519`：移出个人观察列表
- `/watch list`：查看个人观察列表

也可以直接发送“今天有哪些股票值得关注”或“看看 600519 的走势”。普通聊天
里的六位数字不会自动当成股票代码，必须同时出现“股票、走势、看看、模拟盘”
等语境。

筛选会先排除 ST、退市风险、成交过少、短期暴涨和明显数据异常的股票，再
比较价格方向、成交活跃程度、公司最近一期收入和利润、估值及近期公告。某项
数据获取失败时会明确说明或跳过该股票，不会让 Claude 猜测数字。

日报使用北京时间交易日 `15:30` 推送，周末和休市日不推送；同一天重启 Bot
也不会重复发送。AKShare 来自公开数据接口，可能存在延迟或上游接口变化，
因此所有报告都仅供同花顺模拟盘学习，不构成真实交易建议。

---

## 🔒 安全建议

| 配置 | 说明 |
|:---|:---|
| `FEISHU_ALLOWED_USERS` | 只允许指定用户使用（强烈建议） |
| `MAX_CLAUDE_SECONDS=300` | 单次调用默认最多 300 秒 |
| 脚本里可加命令黑名单 | 防止 `rm -rf` 等危险操作 |

---

## 🔄 替代方案：微信（等 Android 开放）

等 Android 微信 ClawBot 插件上线后，把脚本里的飞书 SDK 换成 iLink API 即可。核心逻辑完全一样——长轮询收消息 → Claude Code 处理 → 回复。参考 `claude-code-wechat-channel`。
