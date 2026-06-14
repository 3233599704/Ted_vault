---
title: 飞书 Bot 构建经验
tags:
  - 自动化
  - 飞书
  - Claude
  - Obsidian
  - 核心概念
aliases:
  - Feishu Claude Bot
  - 飞书机器人
created: 2026-06-14
updated: 2026-06-14
status: growing
---

# 飞书 Bot 构建经验

> 三天把飞书 Bot 从基础链路做到日常可用。文字 + 图片 + 语音 + 自愈，完整的本地 AI 入口。

---

## 🎯 目标与架构

手机飞书直接调用本机 Claude Code 读写 Obsidian Vault，不部署公网服务。

```
手机飞书 → 飞书开放平台 → WebSocket 长连接 → feishu-claude-bot.py
  → Claude Code CLI → Obsidian Vault → 飞书文字或语音回复
```

**核心前提**：电脑必须开机联网且不睡眠。关屏幕不影响。睡眠、关机或断网会中断。

---

## 🖥️ 运行环境

| 项目 | 值 |
|:---|:---|
| OS | Windows |
| Python | 3.14.2 |
| Claude Code | 2.1.175 |
| 飞书 SDK | `lark-oapi 1.6.8` |
| 视觉模型 | `mimo-v2-omni` |
| TTS 模型 | `mimo-v2.5-tts-voiceclone` |
| Claude 超时 | 300s |
| WS 重启阈值 | 240s |
| 音频加速 | 1.10x |

---

## 🗓️ 构建时间线

### Day 1（06-12）：基础链路

飞书长连接 → Python subprocess 调 Claude Code CLI → 回复文字消息。环境变量管理凭证。连续会话（session_id 持久化）。图片识别（MiMo Vision）。计划任务开机自启。

### Day 2（06-13）：语音系统

MiMo TTS + Voice Clone。`/voice on/off` 模式切换。8 种动态演绎风格。Markdown 转飞书纯文本。429 自动退避。

### Day 3（06-14）：稳定性

修复电脑睡眠后连接假死——根因是进程还在但 WebSocket 已断。增加了三层保护：健康心跳监控、240 秒自杀重启、PowerShell 循环拉起。

---

## 🔧 完整功能矩阵

### 文字与 Vault 管理

- Claude 只开放 `Read,Glob,Grep,Edit,Write`，**不开放 Bash**
- 默认 `acceptEdits`，工作目录 = Vault 根

### 连续会话

每个飞书用户独立保存 `session_id`，重启不丢。`/new` 清空上下文。session 失效自动重建。

### 异步队列

WebSocket 回调只投递任务，耗时操作进单线程 `ThreadPoolExecutor`。避免阻塞心跳、避免并发修改。

### 图片识别

```
飞书图片 → 下载原图 → 判断扩展名 → 保存 raw/图片
  → MiMo Vision 分析 → 结果交给 Claude → 纳入会话
```

### 语音系统

| 命令 | 行为 |
|:---|:---|
| `/voice on` | 后续只发语音 |
| `/voice off` | 只发文字 |
| `/voice status` | 查看模式和音色 |
| `/voice 问题` | 仅本次语音 |

TTS 管线：Claude 回答 → 清理 Markdown → 语音导演选风格 → Voice Clone 生成 WAV → FFmpeg 1.10x → 编码 Opus → 上传飞书 → 回复语音。

**动态演绎风格**（8 种预设，音色与演绎解耦）：

| 预设 | 用途 |
|:---|:---|
| `natural` | 日常 |
| `romantic` | 爱意/暧昧 |
| `technical` | 技术解释 |
| `comforting` | 安慰 |
| `cheerful` | 好消息 |
| `warning` | 风险 |
| `narrative` | 叙述 |
| `summary` | 总结 |

> romantic 经验：不要极慢/耳语/气声/拖尾。正常偏快、放松咬字、轻微笑意更自然。

### 飞书格式适配

Markdown → 纯文本：去 `#`、列表转符号、链接转 `标题（URL）`、代码块保留加标签、表格转分隔文本。

### 自愈与单实例

三层保护：计划任务 → PowerShell 循环 → Python 心跳监控。文件锁防重复启动。

### 每日蒸馏推送

后台 Watchdog 每 60 秒扫描 `每日蒸馏/`，发现新报告推送通知。

---

## 🔧 13 项报错与解决

| # | 问题 | 根因 | 解决 |
|:---|:---|:---|:---|
| 1 | Claude timed out (120s) | 上限太短 | 300s + 语音压缩 |
| 2 | 图片 Invalid request param | 接口用错 | `GetMessageResourceRequest` |
| 3 | 图片 Access denied | 缺权限 | 开通 `im:message:readonly` |
| 4 | UnicodeEncodeError | Windows 编码 | stdout UTF-8 |
| 5 | 计划任务读不到环境变量 | 旧环境块 | PS 启动器每次重读 |
| 6 | 找不到 Claude CLI | PATH 不一致 | 多路径查找 + 自检 |
| 7 | 会话恢复失败 | Claude 升级/历史删除 | 自动重建 |
| 8 | TTS 无音频 | 内容限制/模型不匹配 | 回退文字 |
| 9 | MiMo 429 | 频率超限 | 5/12/25s 退避 ×3 |
| 10 | 语音导演 JSON 解析失败 | 非标准返回 | 提取第一个 JSON + 回退 natural |
| 11 | 语音咬字过重 | romantic 指令问题 | 正常偏快 + 1.10x |
| 12 | 语音不忠于文本 | 模型漏字/替换 | 短文本 + 干净参考 |
| 13 | 睡眠后无回复 | 进程在连接死 | 心跳 + 240s 自杀 + 三层恢复 |

**硬限制**：睡眠期间不可用，唤醒后不补投消息。真正 24 小时需要常开设备。

---

## 💡 可复用的设计模式

1. **无公网入口**：WebSocket 主动出站，不需要公网 IP 或 ngrok
2. **视觉与 Agent 分层**：视觉看图、Agent 推理，互不耦合
3. **音色与演绎解耦**：固定 Voice Clone + 动态导演，稳定且灵活
4. **面向失败设计**：每层都有 fallback，不因单个环节崩溃而丢回答
5. **状态持久化**：会话、语音偏好、健康状态存 JSON，重启不丢
6. **渠道适配**：不原样透传，专门做飞书格式转换

---

## 🔗 相关页面

- [[../scripts/feishu-bot-setup|飞书 Bot 配置指南]]
- [[../../raw/飞书-Claude-Obsidian-Bot-构建复盘与复现日志|完整复盘日志（760 行）]]

## 📚 原始资料

- [[raw/飞书-Claude-Obsidian-Bot-构建复盘与复现日志.md|完整构建日志]]
- [[多物理场仿真/scripts/feishu-claude-bot.py|Bot 主程序]]
- [[多物理场仿真/scripts/start-feishu-bot.ps1|PowerShell 启动器]]
