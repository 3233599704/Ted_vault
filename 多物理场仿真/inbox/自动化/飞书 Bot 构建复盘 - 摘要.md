---
title: "飞书 Bot 构建复盘 — 摘要"
tags:
  - inbox
  - 自动化
  - 飞书
  - Claude
  - Obsidian
source: "[[raw/飞书-Claude-Obsidian-Bot-构建复盘与复现日志.md]]"
created: 2026-06-14
status: reviewed
---

# 飞书 Bot 构建复盘 — 摘要

> **原始记录**: [[raw/飞书-Claude-Obsidian-Bot-构建复盘与复现日志.md]]（760 行完整复盘）
> **构建时间**：2026-06-12 至 2026-06-14
> **当前状态**：已投入使用，支持文字、连续会话、图片分析、语音回复、开机自启和断线自愈

---

## 🎯 项目目标

在手机飞书中直接调用本机 Claude Code，让 Claude 读取和管理 Obsidian Vault，同时避免部署公网服务。

**最终链路**：

```
手机飞书 → 飞书开放平台 → WebSocket 长连接 → feishu-claude-bot.py
  → Claude Code CLI → 本机 Obsidian Vault → 飞书文字或语音回复
```

核心前提：电脑必须开机、联网且没有进入睡眠。关闭屏幕不影响运行；睡眠、关机或断网会中断服务。

---

## 🖥️ 运行环境

| 项目 | 当前值 |
|:---|:---|
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
| 音频播放速度 | `1.10x` |

---

## 📂 项目文件清单

| 文件 | 作用 |
|:---|:---|
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

---

## 🗓️ 构建时间线

### 2026-06-12：打通基础链路

1. 创建飞书企业自建应用并启用机器人能力
2. 使用飞书 WebSocket 长连接接收 `im.message.receive_v1` 事件，不需要公网 IP、域名或回调服务器
3. Python 收到文本后，通过 `subprocess` 调用 Claude Code CLI（工作目录 = Vault 根目录）
4. Claude 结果回复到原飞书消息
5. 硬编码改 Windows 用户环境变量
6. 增加 Windows 计划任务，用户登录后自动启动
7. 增加连续会话：保存每个飞书用户对应的 Claude `session_id`
8. 增加图片下载和 MiMo Vision 视觉模型分析

### 2026-06-13：语音系统和交互体验

1. 接入 MiMo TTS
2. WAV 转 Opus（飞书语音消息格式）
3. 增加 `/voice on`、`/voice off`、`/voice status` 和单次 `/voice 问题`
4. 语音模式"只发语音"，正常模式"只发文字"；语音失败自动回退为完整文字
5. Voice Design 音色实验，保留 `vera-velvet.wav` 和 `vera-cool-idol.wav`
6. 使用用户提供的音频制作 Voice Clone 参考音色，最终使用 Kafka 短参考音频
7. 增加内容驱动的动态演绎风格（8 种预设）
8. 调整 romantic 预设：不加咬字过重、语速过慢和表演感过强的问题
9. 增加 `1.10x` 不变调加速
10. Markdown 到飞书纯文本的转换层
11. MiMo HTTP 429 自动退避重试

### 2026-06-14：睡眠恢复和连接假死

1. 发现电脑睡眠后部分飞书消息没有回复，业务日志中也没有接收记录
2. SDK 日志确认曾出现 keepalive ping timeout、handshake timeout 和 DNS 解析失败
3. 根因：旧 Python 进程还在，但 PowerShell 父进程已消失——"进程还在、连接已死"
4. 增加 `.feishu-bot-health.json` 每 20 秒心跳
5. 连续 240 秒异常 → 写入 restarting → 计划任务延迟重启 → 旧进程退出码 3
6. `start-feishu-bot.ps1` 对异常退出等待 15 秒后重新拉起
7. 2026-06-14 11:20:44 最终版本重启，11:20:49 飞书 WebSocket 重新连接成功

---

## 🔧 完整功能

### 文字消息与 Obsidian 管理

- Claude 工作目录 = Vault 根目录
- 只开放 `Read,Glob,Grep,Edit,Write` 工具，默认 `acceptEdits`
- **不开放 Bash**，从入口层降低执行任意系统命令的风险

### 连续会话

- 每个飞书用户独立保存 Claude `session_id`，重启不丢
- `/new`、`/reset` 或"新会话"清空上下文
- session 失效时自动删除旧映射并以新会话重试一次

### 异步消息处理

飞书 WebSocket 回调只负责解析和投递任务，耗时操作进入单线程 `ThreadPoolExecutor`：
- 避免 Claude、视觉 API 或 TTS 阻塞 WebSocket 心跳
- 避免同一个会话被多个 Claude 子进程并发修改
- 代价：前一条任务很慢时，后续消息会排队

### 图片识别

```
飞书图片消息
  → message resource API 下载原图
  → 根据文件头判断扩展名
  → 保存到 Vault 的 raw/图片
  → MiMo Vision 分析
  → 将视觉结果交给 Claude
  → Claude 总结并纳入连续会话
```

视觉层和 Claude 层分离——因为 Claude Code 当前不能直接接收图片。

### 语音回复

| 命令 | 行为 |
|:---|:---|
| `/voice on` | 后续回答只发语音 |
| `/voice off` | 恢复只发文字 |
| `/voice status` | 查看模式和音色 |
| `/voice 你的问题` | 仅本次使用语音 |

TTS 流程：

```
Claude 回答
  → 清理 Markdown、代码块和链接
  → 语音导演选择演绎预设
  → MiMo Voice Clone 生成 WAV
  → FFmpeg atempo=1.10 加速
  → 编码为 Opus
  → 上传飞书文件资源
  → 回复 audio 消息
```

语音模式下系统提示 Claude 默认将回答压缩为 3 至 6 个短句。

### 动态语音风格

音色和演绎风格分离：音色由固定参考音频决定，演绎风格由"语音导演"根据内容选择。导演只输出预设名和少量微调，不改写实际回答。失败时回退 `natural`。

| 预设 | 用途 |
|:---|:---|
| `natural` | 日常聊天 |
| `romantic` | 爱意、暧昧和亲昵内容 |
| `technical` | 技术解释和操作步骤 |
| `comforting` | 安慰和陪伴 |
| `cheerful` | 好消息和庆祝 |
| `warning` | 风险提醒 |
| `narrative` | 故事叙述 |
| `summary` | 总结和行动项 |

> romantic 预设的关键经验：不要通过"极慢、耳语、气声、拖尾、逐字强调、刻意压低声线"制造魅惑感。更自然的方案是正常偏快语速、放松咬字、连贯表达和轻微笑意。

### 飞书格式适配

飞书普通 `text` 消息不会渲染 Markdown，增加转换层：标题去 `#`、列表转纯文本、链接转"标题（URL）"、代码块保留内容加标签、表格转分隔文本、粗体斜体清理。

### 启动、自愈与单实例

三层保护：

1. Windows 计划任务在用户登录后启动 `start-feishu-bot.ps1`
2. PowerShell 启动器在 Python 异常退出后等待 15 秒再拉起
3. Python 内部监控 WebSocket，异常超过 240 秒主动退出并触发计划任务

文件锁保证同一时间只有一个 Bot 实例。

### 每日蒸馏通知

Bot 包含后台 Watchdog：
- 每 60 秒扫描 `多物理场仿真/每日蒸馏`
- 发现新 Markdown 报告后生成摘要
- `.feishu-distill-notified.json` 防止重复通知

---

## 🔧 关键报错与解决（13 项）

| # | 问题 | 原因 | 解决 |
|:---|:---|:---|:---|
| 1 | Claude timed out (120s) | 任务复杂，上限太短 | 提高到 300s，语音模式压缩回答 |
| 2 | 图片下载 Invalid request param | 用错了资源接口 | 改用 `GetMessageResourceRequest` + `message_id + file_key + type` |
| 3 | 图片下载 Access denied | 缺少消息读取权限 | 开通 `im:message:readonly` 等权限 |
| 4 | UnicodeEncodeError / 中文乱码 | Windows 编码不一致 | stdout 重配置 UTF-8，所有 IO 显式 UTF-8 |
| 5 | 计划任务读不到最新环境变量 | 继承旧的进程环境块 | `start-feishu-bot.ps1` 每次重新读取用户环境变量 |
| 6 | 找不到 Claude Code CLI | 计划任务 PATH ≠ 交互式 PATH | 依次查找多种路径，启动时自检 `claude --version` |
| 7 | 连续会话恢复失败 | Claude Code 升级 / 本地历史删除 | 自动删除旧映射，新会话重试一次 |
| 8 | MiMo TTS 无音频数据 | 文本触发限制 / 模型不匹配 | 记录 finish_reason，回退文字，使用各自对应的 payload 结构 |
| 9 | MiMo HTTP 429 | 调用频率超限 | 5/12/25 秒退避重试三次，读取 Retry-After，失败回退文字 |
| 10 | 语音导演 JSONDecodeError | 模型返回空内容或非标准 JSON | 提取第一个 JSON 对象，校验 preset 白名单，失败用 natural |
| 11 | 语音太慢/咬字过重 | romantic 指令过度强调低沉慢速 | 重写预设为正常偏快、自然连读；1.10x 加速 |
| 12 | 语音未完全忠于文本 | 模型漏字/替换/断句错误 | 控制文本长度，减少特殊符号，短参考音频 |
| 13 | 电脑睡眠后消息无回复 | 进程还在但 WebSocket 已死 | 健康心跳 + 240s 自杀重启 + 三层恢复机制 |

**无法解决的硬限制**：电脑睡眠期间 Bot 一定不可用。飞书 WebSocket 不保证唤醒后补投消息。

---

## 💡 创新设计

1. **无公网入口的本地 AI 网关**：飞书 WebSocket 主动出站连接，不需要开放本机端口
2. **视觉模型与 Agent 分层**：视觉负责看图，Claude 负责理解上下文和管理文件
3. **音色与演绎解耦**：固定 Voice Clone 保声音稳定，动态导演控制情绪风格
4. **语音导演的受控决策**：只从有限预设选择，不自由生成——防风格漂移和格式错误
5. **面向失败设计**：每一层都有降级路径——session 失效、导演失败、TTS 失败、429、WebSocket 假死、重复启动——都有明确的 fallback
6. **状态持久化**：会话、语音偏好、蒸馏通知、连接健康都写入 JSON，重启不丢
7. **渠道适配而不是直接透传**：Markdown 转纯文本、WAV 转 Opus、长度控制、语音清洗

---

## 📝 后续优化（按优先级）

1. 设置 `FEISHU_ALLOWED_USERS` 完成基本访问控制
2. 重置所有曾经暴露的 App Secret 和 API Key
3. 给消息队列增加"正在处理"和排队提示
4. 日志按日期轮转，避免无限增长
5. 增加 `/health` 命令返回连接、Claude、Vision、TTS 状态
6. 保存未处理消息 ID，短暂断线后尝试通过消息历史 API 补偿
7. 高风险文件操作增加二次确认或只读模式
8. 配置集中到不入库的 `.env` 或 Windows Credential Manager

## 📁 后续动作

- [ ] 精炼为 wiki 页面：[[wiki/自动化/飞书 Bot 构建经验]]
- [ ] 语音系统部分独立：[[wiki/自动化/TTS 语音合成实践]]
