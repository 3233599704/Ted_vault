"""
飞书 Bot ←→ Claude Code 桥接服务
=================================
手机飞书发消息 → 长连接接收 → Claude Code 处理 → 回复到飞书

前置条件：
  1. pip install lark-oapi （已安装 ✅）
  2. Claude Code CLI 已安装（命令行能跑 `claude`）
  3. 飞书开发者后台创建应用 + 获取 APP_ID / APP_SECRET

启动：
  py 多物理场仿真/scripts/feishu-claude-bot.py
"""

import os
import sys
import re
import json
import subprocess
from datetime import datetime

# Windows 控制台 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    ReplyMessageRequest, ReplyMessageRequestBody
)

# ============================================================
# 配置区
# ============================================================

APP_ID = os.environ.get("FEISHU_APP_ID", "cli_aaa1df5bc2385bcb")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "1xC0wBdmrgU6wxTwRFPPre4DstFoqqVT")

# 白名单：只有这些 open_id 能触发 AI（留空 = 允许所有人）
ALLOWED_USERS = [
    u for u in os.environ.get("FEISHU_ALLOWED_USERS", "").split(",") if u
]

VAULT_PATH = os.environ.get("VAULT_PATH", r"D:\Staid\app\Obsidian\Ted_vault")
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", r"C:\Program Files\nodejs\node.exe")
CLAUDE_ENTRY = os.environ.get("CLAUDE_ENTRY",
    r"C:\Users\32335\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli-wrapper.cjs")
MAX_CLAUDE_SECONDS = int(os.environ.get("MAX_CLAUDE_SECONDS", "120"))
LOG_FILE = os.path.join(VAULT_PATH, "多物理场仿真", "scripts", "feishu-bot.log")

# ============================================================
# 日志
# ============================================================

def log(msg: str):
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# 消息去重（飞书可能对同一消息推送多次）
SEEN_MESSAGES = set()
MAX_SEEN = 200  # 防止无限增长
# ============================================================

def run_claude(prompt: str) -> str:
    try:
        safe_prompt = prompt.replace('"', "'")
        result = subprocess.run(
            [CLAUDE_CMD, CLAUDE_ENTRY, "--print", safe_prompt],
            cwd=VAULT_PATH,
            capture_output=True, text=True,
            timeout=MAX_CLAUDE_SECONDS,
            encoding="utf-8", errors="replace",
        )
        out = result.stdout.strip()
        if not out:
            out = "(Claude returned empty response)"
        return out
    except subprocess.TimeoutExpired:
        return f"⏰ Claude 超时 ({MAX_CLAUDE_SECONDS}s)，请简化问题或稍后重试。"
    except FileNotFoundError:
        return f"❌ 找不到 {CLAUDE_CMD}，请确认已安装 Claude Code CLI。"
    except Exception as e:
        return f"❌ Claude 异常: {type(e).__name__}: {e}"


def clean_reply(text: str, max_chars: int = 3000) -> str:
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)   # 去 ANSI 颜色
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n…（已截断）"
    return text

# ============================================================
# 消息处理（官方 EventDispatcherHandler 模式）
# ============================================================

def do_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """接收消息 v2.0 — 官方长连接回调，data 已自动解析为类型化对象"""
    try:
        msg = data.event.message
        sender_id = data.event.sender.sender_id.open_id
        msg_type = msg.message_type
        msg_id = msg.message_id

        # 去重
        if msg_id in SEEN_MESSAGES:
            return
        SEEN_MESSAGES.add(msg_id)
        if len(SEEN_MESSAGES) > MAX_SEEN:
            SEEN_MESSAGES.clear()

        content_str = msg.content

        # 只处理文本
        if msg_type != "text":
            log(f"跳过非文本消息: {msg_type}")
            return

        content = json.loads(content_str)
        user_text = content.get("text", "").strip()
        if not user_text:
            return

        log(f"📩 {sender_id[-8:]}: {user_text[:80]}")

        # 白名单
        if ALLOWED_USERS and sender_id not in ALLOWED_USERS:
            log(f"⛔ 非白名单用户: {sender_id}")
            return

        # 调 Claude
        reply = run_claude(user_text)
        reply = clean_reply(reply)
        log(f"🤖 → {reply[:80]}")

        # 回复
        body = ReplyMessageRequestBody.builder() \
            .content(json.dumps({"text": reply})) \
            .msg_type("text") \
            .build()
        req = ReplyMessageRequest.builder() \
            .message_id(msg_id) \
            .request_body(body) \
            .build()

        resp = lark.Client.builder() \
            .app_id(APP_ID) \
            .app_secret(APP_SECRET) \
            .build() \
            .im.v1.message.reply(req)

        if resp.success():
            log("✅ 回复成功")
        else:
            log(f"❌ 回复失败: code={resp.code}, msg={resp.msg}")

    except json.JSONDecodeError:
        log("消息 JSON 解析失败，跳过")
    except Exception as e:
        log(f"处理异常: {type(e).__name__}: {e}")

# ============================================================
# 主入口
# ============================================================

def main():
    if APP_ID.startswith("你的"):
        print("❌ 请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        print("   或在脚本顶部直接修改 APP_ID / APP_SECRET")
        return

    # 构建事件处理器
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .build()
    )

    # 长连接客户端
    cli = lark.ws.Client(
        APP_ID, APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    log("[OK] Feishu Bot started, waiting for messages...")
    print("[OK] Connected. Send message from Feishu app. Ctrl+C to stop")
    cli.start()


if __name__ == "__main__":
    main()
