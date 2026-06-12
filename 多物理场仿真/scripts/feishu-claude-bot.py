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
import shutil
import subprocess
import threading
from datetime import datetime

# Windows 控制台 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody
)

# ============================================================
# 配置区
# ============================================================

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# 白名单：只有这些 open_id 能触发 AI（留空 = 允许所有人）
ALLOWED_USERS = [
    u for u in os.environ.get("FEISHU_ALLOWED_USERS", "").split(",") if u
]

VAULT_PATH = os.environ.get("VAULT_PATH", r"D:\Staid\app\Obsidian\Ted_vault")
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "")
CLAUDE_PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", "acceptEdits")
MAX_CLAUDE_SECONDS = int(os.environ.get("MAX_CLAUDE_SECONDS", "120"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "feishu-bot.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".feishu-claude-bot.lock")
SESSION_FILE = os.path.join(SCRIPT_DIR, ".feishu-claude-sessions.json")

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
    except OSError:
        pass


def acquire_single_instance():
    """Hold a non-blocking file lock for the lifetime of this process."""
    lock_handle = open(LOCK_FILE, "a+b")
    lock_handle.seek(0, os.SEEK_END)
    if lock_handle.tell() == 0:
        lock_handle.write(b"0")
        lock_handle.flush()
    lock_handle.seek(0)

    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_handle.close()
        return None

    return lock_handle


def find_claude_command() -> list[str]:
    """Resolve Claude Code without relying on the caller's PATH."""
    if CLAUDE_CMD:
        return [CLAUDE_CMD]

    appdata = os.environ.get("APPDATA", "")
    candidates = [
        os.path.join(
            appdata,
            "npm", "node_modules", "@anthropic-ai", "claude-code",
            "bin", "claude.exe",
        ),
        shutil.which("claude.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return [candidate]

    node = shutil.which("node.exe") or shutil.which("node")
    wrapper = os.path.join(
        appdata,
        "npm", "node_modules", "@anthropic-ai", "claude-code",
        "cli-wrapper.cjs",
    )
    if node and os.path.isfile(wrapper):
        return [node, wrapper]

    raise FileNotFoundError(
        "Claude Code CLI was not found. Run `claude --version` in PowerShell "
        "or set CLAUDE_CMD to the full path of claude.exe."
    )


CLAUDE_COMMAND: list[str] = []
CLAUDE_RUN_LOCK = threading.Lock()
SESSION_LOCK = threading.Lock()

# 消息去重（飞书可能对同一消息推送多次）
SEEN_MESSAGES = set()
MAX_SEEN = 200  # 防止无限增长
# ============================================================


def load_sessions() -> dict[str, str]:
    with SESSION_LOCK:
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
    return {
        str(user_id): str(session_id)
        for user_id, session_id in data.items()
        if user_id and session_id
    }


def save_sessions(sessions: dict[str, str]) -> None:
    temp_file = SESSION_FILE + ".tmp"
    with SESSION_LOCK:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, SESSION_FILE)


SESSIONS = load_sessions()


def reset_session(sender_id: str) -> None:
    SESSIONS.pop(sender_id, None)
    save_sessions(SESSIONS)


def invoke_claude(prompt: str, session_id: str | None) -> subprocess.CompletedProcess:
    system_prompt = (
        "你是通过飞书操作本机 Obsidian Vault 的助手。当前工作目录就是 Vault 根目录。"
        "优先用 Read、Glob、Grep、Edit、Write 工具完成用户请求。"
        "只操作当前 Vault 内的文件，不执行破坏性操作；修改后用中文简要说明结果。"
        "这是连续会话，请结合此前对话理解代词、追问和用户未重复说明的上下文。"
    )
    command = [
        *CLAUDE_COMMAND,
        "--print",
        "--output-format", "json",
        "--permission-mode", CLAUDE_PERMISSION_MODE,
        "--allowed-tools", "Read,Glob,Grep,Edit,Write",
        "--append-system-prompt", system_prompt,
    ]
    if session_id:
        command.extend(["--resume", session_id])
    command.append(prompt)

    return subprocess.run(
        command,
        cwd=VAULT_PATH,
        capture_output=True,
        text=True,
        timeout=MAX_CLAUDE_SECONDS,
        encoding="utf-8",
        errors="replace",
    )


def run_claude(sender_id: str, prompt: str) -> str:
    try:
        with CLAUDE_RUN_LOCK:
            session_id = SESSIONS.get(sender_id)
            result = invoke_claude(prompt, session_id)

            # A Claude update or deleted local history can invalidate a session.
            # Retry once as a fresh conversation instead of breaking the bot.
            if result.returncode != 0 and session_id:
                error = result.stderr.strip() or result.stdout.strip()
                log(f"会话恢复失败，自动新建会话: {error[:300]}")
                reset_session(sender_id)
                result = invoke_claude(prompt, None)

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            log(f"Claude 调用失败: exit={result.returncode}, {error[:500]}")
            return f"Claude 调用失败（退出码 {result.returncode}）：\n{error[:500]}"

        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            log(f"Claude JSON 响应解析失败: {result.stdout[:500]}")
            return result.stdout.strip() or "(Claude returned empty response)"

        new_session_id = response.get("session_id")
        if new_session_id and SESSIONS.get(sender_id) != new_session_id:
            SESSIONS[sender_id] = new_session_id
            save_sessions(SESSIONS)
            log(f"已保存连续会话: {sender_id[-8:]} / {new_session_id[:8]}")

        out = str(response.get("result", "")).strip()
        if not out:
            error = response.get("error") or result.stderr.strip()
            if error:
                log(f"Claude 无输出: {str(error)[:500]}")
                return f"Claude 无输出：{str(error)[:300]}"
            return "(Claude returned empty response)"
        return out
    except subprocess.TimeoutExpired:
        return f"⏰ Claude timed out ({MAX_CLAUDE_SECONDS}s)"
    except FileNotFoundError as e:
        log(f"Claude 可执行文件不存在: {e}")
        return f"找不到 Claude Code CLI：{e}"
    except Exception as e:
        log(f"Claude 调用异常: {type(e).__name__}: {e}")
        return f"Claude 调用异常：{type(e).__name__}: {e}"


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

        if user_text.lower() in {"/new", "/reset"} or user_text == "新会话":
            reset_session(sender_id)
            reply = "已开启新会话。下一条消息将不再使用之前的聊天上下文。"
        else:
            # 调 Claude
            reply = run_claude(sender_id, user_text)
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
    global CLAUDE_COMMAND

    instance_lock = acquire_single_instance()
    if instance_lock is None:
        log("已有一个 Feishu Bot 实例正在运行，本次启动已退出")
        return 2

    if not APP_ID or not APP_SECRET or APP_ID.startswith("你的"):
        log("请先设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
        return 2
    if not os.path.isdir(VAULT_PATH):
        log(f"Vault 路径不存在: {VAULT_PATH}")
        return 2

    try:
        CLAUDE_COMMAND = find_claude_command()
        version = subprocess.run(
            [*CLAUDE_COMMAND, "--version"],
            cwd=VAULT_PATH,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"Claude Code 启动自检失败: {e}")
        return 2
    if version.returncode != 0:
        log(f"Claude Code 启动自检失败: {version.stderr.strip()[:500]}")
        return 2

    log(f"Claude Code {version.stdout.strip()} | Vault: {VAULT_PATH}")

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
    try:
        cli.start()
    except KeyboardInterrupt:
        log("Feishu Bot stopped")
    finally:
        instance_lock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
