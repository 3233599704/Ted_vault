---
title: 飞书 Claude Obsidian Bot 构建复盘与复现日志
aliases:
  - Feishu Claude Bot Build Log
tags:
  - 飞书
  - Claude-Code
  - Obsidian
  - 自动化
  - Python
  - MiMo
created: 2026-06-14
status: running
---

# 飞书 Claude Obsidian Bot 构建复盘与复现日志

> 构建时间：2026-06-12 至 2026-06-14  
> 当前状态：已投入使用，支持文字、连续会话、图片分析、语音回复、开机自启和断线自愈。  
> 安全说明：本文不保存 App Secret、API Key、用户 open_id 等敏感值。

## 1. 项目目标

目标是在手机飞书中直接调用本机 Claude Code，让 Claude 能够读取和管理 Obsidian Vault，同时避免部署公网服务。

最终链路：

```text
手机飞书
  -> 飞书开放平台
  -> WebSocket 长连接
  -> feishu-claude-bot.py
  -> Claude Code CLI
  -> 本机 Obsidian Vault
  -> 飞书文字或语音回复
```

这个方案的核心前提是：运行 Bot 的电脑必须开机、联网且没有进入睡眠。关闭屏幕不影响运行；睡眠、关机或断网会中断服务。

## 2. 当前运行环境

截至 2026-06-14：

| 项目 | 当前值 |
|---|---|
| 操作系统 | Windows |
| Python | 3.14.2 |
| Claude Code | 2.1.175 |
| 飞书 SDK | `lark-oapi 1.6.8` |
| 音频工具 | `imageio-ffmpeg 0.6.0` |
| 主程序 | `feishu-claude-bot.py` |
| 启动器 | `start-feishu-bot.ps1` |
| 计划任务 | `FeishuClaudeBot` |
| Claude 单次超时 | 300 秒 |
| WebSocket 异常重启阈值 | 240 秒 |
| 视觉模型 | `mimo-v2-omni` |
| TTS 模型 | `mimo-v2.5-tts-voiceclone` |
| 当前参考音色 | `voice-previews\kafka-reference-short.wav` |
| 当前音色名 | `Kafka` |
| 音频播放速度 | `1.10x` |

`ffmpeg` 不需要加入系统 PATH，代码通过 `imageio_ffmpeg.get_ffmpeg_exe()` 使用依赖包自带的可执行文件。

## 3. 项目文件

| 文件 | 作用 |
|---|---|
| `feishu-claude-bot.py` | Bot 主程序 |
| `start-feishu-bot.ps1` | 隐藏启动、读取用户环境变量、异常退出后拉起 |
| `feishu-bot-setup.md` | 日常部署和配置说明 |
| `feishu-bot.log` | 业务日志 |
| `feishu-bot.stdout.log` | SDK、连接和标准输出日志 |
| `feishu-bot.stderr.log` | 未捕获错误日志 |
| `.feishu-claude-sessions.json` | 飞书用户与 Claude 会话 ID 的映射 |
| `.feishu-voice-settings.json` | 每个用户的语音模式开关 |
| `.feishu-bot-health.json` | WebSocket 健康状态和心跳 |
| `.feishu-claude-bot.lock` | 单实例文件锁 |
| `.feishu-distill-notified.json` | 已推送的每日蒸馏报告记录 |
| `voice-previews\` | Voice Design 和 Voice Clone 参考音频 |

运行状态文件、密钥和日志不应提交到公开 Git 仓库。

## 4. 构建过程时间线

### 2026-06-12：打通基础链路

1. 创建飞书企业自建应用并启用机器人能力。
2. 使用飞书 WebSocket 长连接接收 `im.message.receive_v1` 事件，因此不需要公网 IP、域名或回调服务器。
3. Python 收到文本后，通过 `subprocess` 调用 Claude Code CLI。
4. Claude Code 的工作目录固定为 Obsidian Vault 根目录。
5. 将 Claude 的结果回复到原飞书消息。
6. 把硬编码的 App ID、App Secret 改为 Windows 用户环境变量。
7. 增加 Windows 计划任务，实现用户登录后自动启动。
8. 增加连续会话：保存每个飞书用户对应的 Claude `session_id`。
9. 增加图片下载和视觉模型分析。
10. 接入小米 MiMo Vision API。

### 2026-06-13：完善语音系统和交互体验

1. 接入小米 MiMo TTS。
2. 将 WAV 转码成飞书语音消息要求的 Opus。
3. 增加 `/voice on`、`/voice off`、`/voice status` 和单次 `/voice 问题`。
4. 将语音模式改为“只发语音”，正常模式“只发文字”。
5. 语音失败时自动回退为完整文字，避免回答丢失。
6. 增加 Voice Design 音色实验，保留：
   - `vera-velvet.wav`
   - `vera-cool-idol.wav`
7. 使用用户提供的音频制作 Voice Clone 参考音色，最终使用 Kafka 短参考音频。
8. 删除效果不理想、背景杂音较多的参考音色实验。
9. 增加内容驱动的动态演绎风格。
10. 调整 romantic 预设，解决咬字过重、语速过慢和表演感过强的问题。
11. 增加 `1.10x` 不变调加速。
12. 增加 Markdown 到飞书纯文本的转换。
13. 增加 MiMo HTTP 429 自动退避重试。

### 2026-06-14：解决睡眠恢复和连接假死

1. 发现电脑睡眠后，一部分飞书消息没有回复，业务日志中也没有接收记录。
2. 从 SDK 日志确认曾出现 keepalive ping timeout、handshake timeout 和 DNS 解析失败。
3. 发现旧 Python 进程仍存在，但启动 PowerShell 父进程已经消失，属于“进程还在、连接已死”的状态。
4. 增加 `.feishu-bot-health.json`，每 20 秒记录连接状态和心跳。
5. 连续 240 秒处于 `starting`、`reconnecting`、`disconnected` 或 `unknown` 时：
   - 写入 `restarting` 状态；
   - 安排计划任务延迟重新启动；
   - 当前坏进程以退出码 3 退出。
6. `start-feishu-bot.ps1` 对异常退出等待 15 秒后重新拉起。
7. 2026-06-14 11:20:44 使用最终版本重启。
8. 2026-06-14 11:20:49 飞书 WebSocket 重新连接成功。
9. 健康文件确认状态为 `connected`，错误日志为空。

## 5. 最终功能

### 5.1 文字消息与 Obsidian 管理

- 飞书文字消息交给 Claude Code 处理。
- Claude 工作目录是 Vault 根目录。
- Claude 仅开放 `Read,Glob,Grep,Edit,Write` 工具。
- 默认权限模式为 `acceptEdits`。
- 不向 Claude 开放 Bash，从入口层降低执行任意系统命令的风险。
- 回复长度超过限制时会截断，避免飞书消息过长。

### 5.2 连续会话

- 每个飞书用户独立保存 Claude `session_id`。
- 下一条消息使用 `claude --resume <session_id>` 恢复上下文。
- Bot 或电脑重启后仍可继续。
- `/new`、`/reset` 或“新会话”会清空当前用户的上下文。
- 如果 Claude 本地历史失效，会自动清除旧 session 并重试一次新会话。

### 5.3 异步消息处理

飞书 WebSocket 回调只负责解析和投递任务，耗时操作进入单线程 `ThreadPoolExecutor`。

作用：

- 避免 Claude、视觉 API 或 TTS 阻塞 WebSocket 心跳。
- 避免同一个会话被多个 Claude 子进程并发修改。
- 保证消息大致按接收顺序处理。

代价：前一条任务很慢时，后续消息会排队。

### 5.4 图片识别

流程：

```text
飞书图片消息
  -> message resource API 下载原图
  -> 根据文件头判断扩展名
  -> 保存到 Vault 的 raw/图片
  -> MiMo Vision 分析
  -> 将视觉结果交给 Claude
  -> Claude 总结并纳入连续会话
```

视觉层和 Claude 层分离，原因是当时 Claude Code 背后的文本模型不能直接接受图片。

### 5.5 语音回复

命令：

| 命令 | 行为 |
|---|---|
| `/voice on` | 后续回答只发语音 |
| `/voice off` | 恢复只发文字 |
| `/voice status` | 查看模式和音色 |
| `/voice 你的问题` | 仅本次使用语音 |

TTS 流程：

```text
Claude 回答
  -> 清理 Markdown、代码块和链接
  -> 语音导演选择演绎预设
  -> MiMo Voice Clone 生成 WAV
  -> FFmpeg atempo=1.10
  -> 编码为 Opus
  -> 上传飞书文件资源
  -> 回复 audio 消息
```

语音模式下，系统提示 Claude 默认将回答压缩为 3 至 6 个短句。用户明确要求完整步骤或详细解释时仍可展开。

### 5.6 动态语音风格

音色和演绎风格分离：

- 音色由固定参考音频决定。
- 演绎风格由一个轻量“语音导演”根据本次回答内容选择。
- 导演只输出预设名和少量微调，不改写实际回答。
- 导演失败时回退到 `natural`，不影响主回答。

当前预设：

| 预设 | 用途 |
|---|---|
| `natural` | 日常聊天 |
| `romantic` | 爱意、暧昧和亲昵内容 |
| `technical` | 技术解释和操作步骤 |
| `comforting` | 安慰和陪伴 |
| `cheerful` | 好消息和庆祝 |
| `warning` | 风险提醒 |
| `narrative` | 故事叙述 |
| `summary` | 总结和行动项 |

romantic 预设的关键经验：不要通过“极慢、耳语、气声、拖尾、逐字强调、刻意压低声线”制造魅惑感。更自然的方案是正常偏快语速、放松咬字、连贯表达和轻微笑意。

### 5.7 飞书格式适配

飞书普通 `text` 消息不会渲染 Markdown，因此增加转换层：

- 标题去除 `#`。
- 无序列表转换为纯文本项目符号。
- Markdown 链接转换为“标题（URL）”。
- 代码块保留内容并增加文字标签。
- 表格转换为可读的分隔文本。
- 粗体、斜体等标记被清理。

### 5.8 启动、自愈与单实例

采用三层保护：

1. Windows 计划任务在用户登录后启动 `start-feishu-bot.ps1`。
2. PowerShell 启动器在 Python 异常退出后等待 15 秒再拉起。
3. Python 内部监控 WebSocket，异常超过 240 秒主动退出并触发计划任务。

文件锁保证同一时间只有一个 Bot 实例，避免重复回复和多个 WebSocket 连接。

### 5.9 每日蒸馏通知

Bot 还包含一个后台 Watchdog：

- 每 60 秒扫描 `多物理场仿真/每日蒸馏`。
- 发现新的 Markdown 报告后生成摘要。
- 可通过 `FEISHU_NOTIFY_USERS` 或白名单用户主动推送。
- `.feishu-distill-notified.json` 防止重复通知。

当前日志显示通知目标为 0，说明功能存在，但尚未配置接收用户。

## 6. 关键报错、原因与解决办法

### 6.1 Claude timed out

日志：

```text
Claude timed out (120s)
```

原因：

- 任务复杂、文件较多或网络较慢。
- 原来的 120 秒上限太短。

解决：

- 将默认超时提高到 300 秒。
- 捕获 `subprocess.TimeoutExpired` 并返回可理解的提示。
- 语音模式压缩回答长度，减少 Claude 和 TTS 总耗时。

### 6.2 图片下载 Invalid request param

日志：

```text
下载图片失败: code=234001, msg=Invalid request param.
```

原因：

- 使用了不适合“消息内图片”的资源接口或请求参数。

解决：

- 改用 `GetMessageResourceRequest`。
- 同时传入 `message_id`、`file_key` 和 `type("image")`。

### 6.3 图片下载 Access denied

日志：

```text
Access denied. One of the following scopes is required:
[im:message.history:readonly, im:message:readonly, im:message]
```

原因：

- 应用身份没有读取消息资源的权限。

解决：

- 在飞书开放平台开通 `im:message:readonly` 等提示中的任一有效权限。
- 上传 Bot 生成的语音还需要消息资源上传相关权限，例如 `im:resource`。
- 权限变更后创建并发布新版本，再重启 Bot。

### 6.4 UnicodeEncodeError 或中文乱码

原因：

- Windows 控制台、重定向日志和 Python 输出编码不一致。

解决：

- Python 启动时将 `stdout` 重配置为 UTF-8，并设置 `errors="replace"`。
- 所有 JSON、日志和 subprocess 文本显式使用 UTF-8。

注意：如果 PowerShell 自身用错误编码读取 UTF-8 文件，终端仍可能显示乱码，但文件内容本身不一定损坏。

### 6.5 计划任务读不到最新环境变量

原因：

- Windows 计划任务可能继承创建时的旧环境块。
- 修改用户环境变量后，旧进程不会自动刷新。

解决：

- `start-feishu-bot.ps1` 每次启动都通过
  `[Environment]::GetEnvironmentVariable(..., "User")`
  重新读取配置并写入当前进程环境。
- 修改配置后重启计划任务。

### 6.6 找不到 Claude Code CLI

原因：

- 计划任务的 PATH 与交互式 PowerShell 不一致。

解决：

- 依次查找 `CLAUDE_CMD`、npm 安装目录、`claude.exe` 和 Node wrapper。
- 启动时先运行 `claude --version` 自检。

### 6.7 连续会话恢复失败

原因：

- Claude Code 升级、本地历史被删除或 session ID 已失效。

解决：

- 使用旧 session 调用失败时，自动删除映射。
- 同一条消息以新会话重试一次。

### 6.8 MiMo TTS 返回中没有音频数据

日志：

```text
MiMo TTS 返回中没有音频数据
```

原因可能包括：

- 文本触发模型内容限制。
- 模型只返回文字说明，没有返回 `message.audio.data`。
- 服务临时异常。
- 请求结构与当前 TTS 模型不匹配。

解决：

- 记录 `finish_reason` 和模型返回的文字说明。
- 给用户返回更明确的“可能受到内容限制或服务繁忙”提示。
- 语音失败时自动发送完整文字。
- Voice Clone、Voice Design 和普通 TTS 使用各自对应的 payload 结构。

### 6.9 MiMo HTTP 429 Too many requests

日志：

```text
HTTP 429
Too many requests
```

原因：

- MiMo TTS 调用频率或并发超过服务限制。

解决：

- 自动按 5、12、25 秒退避重试三次。
- 优先读取服务端 `Retry-After`。
- 重试后仍失败则回退文字。
- 单线程队列也降低了并发触发限流的概率。

### 6.10 语音导演 JSONDecodeError

日志：

```text
语音导演失败，回退自然风格:
JSONDecodeError: Expecting value
```

原因：

- 导演模型返回空内容、额外文本或非标准 JSON。

解决：

- 从响应中提取第一个 JSON 对象。
- 校验 preset 必须在白名单内。
- 任意异常直接使用 `natural`。

### 6.11 语音太慢、不连贯、咬字过重

原因：

- romantic 指令过度强调低沉、慢速、耳语和逐字表达。
- 长文本本身不适合作为即时语音消息。

解决：

- 重写 romantic 预设，要求正常偏快、自然连读、放松咬字。
- 语音模式要求 Claude 默认输出 3 至 6 个短句。
- 合成后通过 `atempo=1.10` 不变调加速。

### 6.12 语音未完全忠于文本

判断：

- Voice Clone/TTS 模型可能出现漏字、替换、断句错误或语气影响发音。
- 如果送入模型的 `spoken_text` 与原回答一致，通常属于模型生成质量问题。

缓解：

- 控制单次文本长度。
- 减少 Markdown、链接、代码和特殊符号。
- 使用更干净、短而清晰的参考音频。
- 重要信息保留文字模式，语音更适合短对话。

### 6.13 电脑睡眠后消息无回复且日志无记录

关键日志：

```text
keepalive ping timeout
handshake timeout
name resolution failure: open.feishu.cn
```

根因：

- 电脑睡眠时 Python、网络和 WebSocket 都被暂停。
- 唤醒后 SDK 可能进入长期重连或假死。
- 某次故障中只剩旧 Python 子进程，PowerShell 启动器已经不存在。
- 业务日志没有消息记录，说明飞书事件根本没有抵达回调，而不是 Claude 没有回答。

解决：

- 增加 WebSocket 健康心跳。
- 连接异常超过 4 分钟时自杀并重新拉起。
- 保留计划任务和 PowerShell 循环作为外部恢复层。
- 接通电源时将 Windows“进入睡眠”设置为“从不”；可以正常关闭屏幕。

无法解决的限制：

- 电脑实际处于睡眠期间，Bot 一定不可用。
- 飞书 WebSocket 事件不保证在电脑唤醒后补投，因此睡眠期间发出的旧消息可能永久遗漏。
- 真正 24 小时在线需要常开设备或云服务器。

## 7. 创新点和可复用设计

### 7.1 无公网入口的本地 AI 网关

使用飞书 WebSocket 主动出站连接，不需要开放本机端口，也不需要公网 IP。这让本地 Obsidian 可以被手机远程操作，同时减少外网暴露面。

### 7.2 视觉模型与 Agent 分层

视觉模型负责“看图”，Claude Code 负责理解上下文、管理文件和继续对话。这样即使 Agent 本身不能直接接收图片，也能获得结构化视觉信息。

### 7.3 音色与演绎解耦

固定 Voice Clone 参考音频保证角色声音稳定，动态导演只改变节奏、情绪和表达方式。相比每次重新设计音色，更稳定也更便宜。

### 7.4 语音导演的受控决策

导演不自由生成长 prompt，而是从有限预设中选择并给出短微调。这个设计降低了风格漂移、输出格式错误和不可预测性。

### 7.5 面向失败设计

每一层都有降级路径：

- session 失效 -> 新会话重试；
- 导演失败 -> natural；
- TTS 失败 -> 完整文字；
- 429 -> 退避重试；
- WebSocket 假死 -> 自动重启；
- 重复启动 -> 文件锁退出。

### 7.6 状态持久化

连续会话、语音偏好、蒸馏通知记录和连接健康状态都写入独立 JSON。程序重启不会丢失用户模式，也方便人工诊断。

### 7.7 渠道适配而不是直接透传

Claude 输出不能原样投递到所有渠道。项目针对飞书做了 Markdown 转纯文本、WAV 转 Opus、消息长度控制和语音文本清洗，是从“能调用”走向“能用”的关键。

## 8. 从零复现

### 8.1 创建飞书应用

1. 在飞书开放平台创建企业自建应用。
2. 添加机器人能力。
3. 开通至少以下权限：

```text
im:message.p2p_msg:readonly
im:message.group_at_msg:readonly
im:message:send_as_bot
im:message:readonly
im:resource
```

4. 添加事件 `im.message.receive_v1`。
5. 订阅方式选择长连接。
6. 创建并发布应用版本。

飞书后台的权限名称可能调整，应以 API 报错中给出的 scope 为准。

### 8.2 安装依赖

```powershell
py -m pip install lark-oapi imageio-ffmpeg
claude --version
```

### 8.3 配置用户环境变量

示例只放占位符：

```powershell
[Environment]::SetEnvironmentVariable(
  "FEISHU_APP_ID", "cli_xxx", "User"
)
[Environment]::SetEnvironmentVariable(
  "FEISHU_APP_SECRET", "重新生成的Secret", "User"
)
[Environment]::SetEnvironmentVariable(
  "VAULT_PATH", "D:\path\to\ObsidianVault", "User"
)
[Environment]::SetEnvironmentVariable(
  "FEISHU_ALLOWED_USERS", "ou_xxx", "User"
)
```

视觉：

```powershell
[Environment]::SetEnvironmentVariable("VISION_PROVIDER", "xiaomi", "User")
[Environment]::SetEnvironmentVariable("VISION_API_KEY", "你的Key", "User")
[Environment]::SetEnvironmentVariable(
  "VISION_API_URL",
  "https://api.xiaomimimo.com/v1/chat/completions",
  "User"
)
[Environment]::SetEnvironmentVariable(
  "VISION_MODEL", "mimo-v2-omni", "User"
)
```

语音：

```powershell
[Environment]::SetEnvironmentVariable("TTS_API_KEY", "你的Key", "User")
[Environment]::SetEnvironmentVariable(
  "TTS_API_URL",
  "https://api.xiaomimimo.com/v1/chat/completions",
  "User"
)
[Environment]::SetEnvironmentVariable(
  "TTS_MODEL", "mimo-v2.5-tts-voiceclone", "User"
)
[Environment]::SetEnvironmentVariable("TTS_VOICE_NAME", "Kafka", "User")
[Environment]::SetEnvironmentVariable(
  "TTS_VOICE_REFERENCE",
  "voice-previews\kafka-reference-short.wav",
  "User"
)
[Environment]::SetEnvironmentVariable(
  "TTS_PLAYBACK_SPEED", "1.10", "User"
)
```

未显式设置时，代码当前默认：

```text
MAX_CLAUDE_SECONDS=300
WS_RESTART_AFTER_SECONDS=240
TTS_DYNAMIC_STYLE=true
TTS_DIRECTOR_MODEL=mimo-v2-flash
```

### 8.4 手动启动验证

```powershell
cd "D:\path\to\ObsidianVault\多物理场仿真\scripts"
py .\feishu-claude-bot.py
```

看到以下信息说明基础连接成功：

```text
[OK] Feishu Bot started, waiting for messages...
connected to wss://msg-frontier.feishu.cn/...
```

### 8.5 创建登录自启任务

以当前用户身份运行 PowerShell，路径按实际位置修改：

```powershell
$script = "D:\path\to\scripts\start-feishu-bot.ps1"
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName "FeishuClaudeBot" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Feishu to Claude Code bridge"
```

常用操作：

```powershell
Start-ScheduledTask -TaskName "FeishuClaudeBot"
Stop-ScheduledTask -TaskName "FeishuClaudeBot"
```

### 8.6 验收清单

- [ ] 飞书发送普通文本后收到纯文本回复
- [ ] Claude 可以读取 Vault 中的文件
- [ ] Claude 可以按要求编辑测试笔记
- [ ] 第二条追问能继承上一条上下文
- [ ] `/new` 后上下文被重置
- [ ] 图片能保存到 `raw/图片` 并返回分析
- [ ] `/voice 问题` 能收到 Opus 语音
- [ ] `/voice on` 后只发语音
- [ ] `/voice off` 后只发文字
- [ ] TTS 故意失败时能回退完整文字
- [ ] 重复启动不会出现两个 Bot
- [ ] 注销并重新登录后计划任务自动启动
- [ ] `.feishu-bot-health.json` 持续更新且为 `connected`

## 9. 日常运维

### 重启

```powershell
Stop-ScheduledTask -TaskName "FeishuClaudeBot"
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "FeishuClaudeBot"
```

### 看业务日志

```powershell
Get-Content .\feishu-bot.log -Tail 100
```

### 看连接日志

```powershell
Get-Content .\feishu-bot.stdout.log -Tail 100
```

### 看未捕获异常

```powershell
Get-Content .\feishu-bot.stderr.log -Tail 100
```

### 看健康状态

```powershell
Get-Content .\.feishu-bot-health.json
```

### 判断消息丢在哪一层

1. 业务日志完全没有该消息：飞书事件没有到达本机，先查睡眠、网络和 WebSocket。
2. 有收到消息、没有 Claude 结果：查 Claude 超时、session 或 CLI。
3. 有 Claude 结果、没有回复成功：查飞书发送权限和 API 错误。
4. 文字正常、语音失败：查 MiMo 返回、429、参考音频和飞书资源上传权限。

## 10. 安全与隐私

### 必须执行

- App Secret 和 MiMo API Key 曾经在聊天中直接出现过，应在对应后台重置。
- 新密钥只保存在 Windows 用户环境变量，不写入代码、Markdown 或 Git。
- 建议设置 `FEISHU_ALLOWED_USERS`，当前为空代表任何能联系该 Bot 的人都可能触发 Claude。
- 不公开提交日志，因为日志可能包含用户消息、文件路径和模型回答。
- 参考音频只能使用本人声音、明确授权素材或拥有合法使用权的素材。

### 当前权限边界

Claude Code 只开放 Vault 内的文件类工具，但它仍然能够修改笔记。重要 Vault 应启用 Git、Obsidian Sync 或其他版本备份。

## 11. 已知限制

1. 电脑睡眠和关机期间不可用。
2. 睡眠期间的飞书消息不保证补发。
3. 单线程队列保证稳定，但长任务会阻塞后续消息。
4. 飞书 `text` 消息只能近似呈现 Markdown。
5. TTS 可能漏字、改字或被内容策略拒绝。
6. Voice Clone 的质量高度依赖参考音频的干净程度。
7. MiMo API 限流时，语音回复会明显变慢或回退文字。
8. 当前部署依赖本机 Claude Code 登录状态和本地会话历史。
9. 计划任务需要用户登录；如果电脑停在登录界面，是否启动取决于任务配置。

## 12. 后续优化方向

按优先级建议：

1. 设置 `FEISHU_ALLOWED_USERS`，完成最基本的访问控制。
2. 重置所有曾经暴露的 App Secret 和 API Key。
3. 给消息队列增加“正在处理”和排队提示。
4. 将日志按日期轮转，避免单文件无限增长。
5. 增加 `/health` 命令，直接返回连接、Claude、Vision 和 TTS 状态。
6. 保存未处理消息 ID，在短暂断线后尝试通过消息历史 API补偿。
7. 给高风险文件操作增加二次确认或只读模式。
8. 将配置集中到不入库的 `.env` 或 Windows Credential Manager。
9. 如果确实需要 24 小时服务，再迁移到云服务器或家用常开设备，并同步 Vault。

## 13. 最终结论

这次构建完成了一个可长期使用的本地个人 Agent 入口：

- 飞书负责跨设备交互；
- Claude Code 负责推理和 Vault 操作；
- MiMo 负责图片理解、语音设计和语音合成；
- Windows 计划任务与内部健康检查负责持续运行；
- JSON 状态文件负责跨重启保存会话和偏好；
- 格式转换与失败回退保证移动端体验。

真正影响稳定性的不是单个 API 调用，而是整条链路中每一层的状态、超时、权限、编码和降级行为。最终版本的价值，也正是在这些边缘问题被逐一处理后，Bot 从“偶尔能跑”变成了“日常可用”。
