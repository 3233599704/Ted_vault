"""
飞书 Bot ←→ Claude Code 桥接服务
=================================
手机飞书发消息 → 长连接接收 → Claude Code 处理 → 回复到飞书

前置条件：
  1. pip install lark-oapi
  2. Claude Code CLI 已安装（命令行能跑 `claude`）
  3. 飞书开发者后台创建应用 + 获取 APP_ID / APP_SECRET

启动：
  python feishu-claude-bot.py

安全：
  - 只响应白名单用户（ALLOWED_USERS）
  - Claude Code 在 vault 目录下运行，可访问 Obsidian vault
  - 超时保护（MAX_CLAUDE_SECONDS）
"""

import os
import sys
import re
import json
import subprocess
import threading
from datetime import datetime

from lark_oapi.ws import Client, LogLevel
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    ReplyMessageRequest, ReplyMessageRequestBody
)
from lark_oapi import Config, Context

# ============================================================
# 配置区 —— 按你的实际情况修改
# ============================================================

APP_ID = os.environ.get("FEISHU_APP_ID", "你的App ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "你的App Secret")

# 白名单：只有这些用户的 open_id 能触发 AI 处理
ALLOWED_USERS = os.environ.get("FEISHU_ALLOWED_USERS", "").split(",")
# 留空表示允许所有人 → 慎用！

# Claude Code 配置
VAULT_PATH = os.environ.get("VAULT_PATH",
    r"D:\Staid\app\Obsidian\Ted_vault")

MAX_CLAUDE_SECONDS = int(os.environ.get("MAX_CLAUDE_SECONDS", "120"))
# Claude Code CLI 命令（Codex 用户同理，改成 codex）
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")

# 日志
LOG_FILE = os.path.join(VAULT_PATH, "多物理场仿真", "scripts", "feishu-bot.log")

# ============================================================
# 日志
# ============================================================

def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ============================================================
# Claude Code 调用
# ============================================================

def run_claude(prompt: str) -> str:
    """调用 Claude Code CLI，返回输出文本"""
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "--print", prompt],
            cwd=VAULT_PATH,
            capture_output=True,
            text=True,
            timeout=MAX_CLAUDE_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
        output = result.stdout.strip()
        if result.stderr:
            output += "\n\n--- stderr ---\n" + result.stderr.strip()
        return output if output else "(Claude returned empty response)"
    except subprocess.TimeoutExpired:
        return f"⏰ Claude 超时 ({MAX_CLAUDE_SECONDS}s)，请简化问题或稍后重试。"
    except FileNotFoundError:
        return f"❌ 找不到 {CLAUDE_CMD} 命令，请确认已安装 Claude Code CLI。"
    except Exception as e:
        return f"❌ Claude 执行异常：{type(e).__name__}: {e}"


def clean_reply(text: str, max_chars: int = 3000) -> str:
    """清理输出，截断过长的回复"""
    # 去掉 ANSI 颜色码
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    # 去掉可能的文件路径前缀噪音
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n...(已截断)"
    return text

# ============================================================
# 飞书消息处理
# ============================================================

def is_allowed(sender_open_id: str) -> bool:
    if not ALLOWED_USERS or ALLOWED_USERS == [""]:
        return True  # 没设白名单 → 允许所有人
    return sender_open_id in ALLOWED_USERS


def on_message(ctx: Context, conf: Config, event: dict):
    """接收到飞书消息的回调（长连接模式）"""
    try:
        msg_event = event.get("event", {})
        message = msg_event.get("message", {})
        msg_type = message.get("message_type", "")
        content_str = message.get("content", "{}")
        msg_id = message.get("message_id", "")
        chat_id = message.get("chat_id", "")
        sender_id = event.get("event", {}).get("sender", {}).get("sender_id", {}).get("open_id", "")

        # 只处理文本消息
        if msg_type != "text":
            log(f"跳过非文本消息: {msg_type}")
            return

        content = json.loads(content_str)
        user_text = content.get("text", "").strip()
        if not user_text:
            return

        log(f"收到消息 [sender={sender_id}]: {user_text[:80]}...")

        # 白名单检查
        if not is_allowed(sender_id):
            log(f"拒绝非白名单用户: {sender_id}")
            return

        # 调用 Claude
        reply = run_claude(user_text)
        reply = clean_reply(reply)
        log(f"Claude 回复 [{sender_id}]: {reply[:80]}...")

        # 回复到飞书
        request = ReplyMessageRequest.builder() \
            .message_id(msg_id) \
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": reply}))
                .msg_type("text")
                .build()
            ).build()

        response = ctx.client.im.v1.message.reply(request)
        if not response.success():
            log(f"回复失败: {response.code} {response.msg}")
        else:
            log(f"回复成功 [sender={sender_id}]")

    except json.JSONDecodeError:
        log("消息内容 JSON 解析失败")
    except Exception as e:
        log(f"处理消息异常: {type(e).__name__}: {e}")


# ============================================================
# 主入口
# ============================================================

def main():
    print("""
╔══════════════════════════════════════════╗
║   🦜 飞书 ↔ Claude Code 桥接服务        ║
║   基于飞书长连接 + Claude Code CLI      ║
║   Vault: {vault}
║   命令: {cmd}
╚══════════════════════════════════════════╝
    """.format(vault=VAULT_PATH, cmd=CLAUDE_CMD))

    if APP_ID.startswith("你的"):
        print("❌ 请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 环境变量")
        print("   或直接修改脚本顶部的 APP_ID / APP_SECRET")
        sys.exit(1)

    # 初始化飞书客户端（长连接模式，无需公网 IP）
    client = Client(
        APP_ID, APP_SECRET,
        log_level=LogLevel.INFO,
    )

    # 注册消息事件
    client.on("im.message.receive_v1", on_message)

    log("飞书 Bot 启动，等待消息...")
    print("✅ 已连接飞书长连接，手机发消息即可对话")
    print("   按 Ctrl+C 停止\n")

    try:
        client.start()
    except KeyboardInterrupt:
        log("收到停止信号，关闭连接...")
        print("\n👋 已停止")
    except Exception as e:
        log(f"连接异常: {type(e).__name__}: {e}")
        print(f"\n❌ 连接失败: {e}")


if __name__ == "__main__":
    main()
