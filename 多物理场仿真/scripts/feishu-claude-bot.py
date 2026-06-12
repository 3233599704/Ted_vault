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
import base64
import hashlib
import shutil
import subprocess
import threading
import time as _time_module
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Windows 控制台 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody,
    CreateMessageRequest, CreateMessageRequestBody,
    GetMessageResourceRequest,
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
MAX_CLAUDE_SECONDS = int(os.environ.get("MAX_CLAUDE_SECONDS", "300"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "feishu-bot.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".feishu-claude-bot.lock")
SESSION_FILE = os.path.join(SCRIPT_DIR, ".feishu-claude-sessions.json")
IMAGE_SAVE_DIR = os.path.join(VAULT_PATH, "多物理场仿真", "raw", "图片")
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "anthropic").lower()
VISION_API_KEY = os.environ.get("VISION_API_KEY", "")
VISION_API_URL = os.environ.get("VISION_API_URL", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "")

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
MESSAGE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="feishu-message",
)

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
        log(f"Claude 调用超时: {MAX_CLAUDE_SECONDS}s")
        return (
            f"Claude 处理超过 {MAX_CLAUDE_SECONDS} 秒，已停止本次任务。"
            "这通常是因为任务较复杂或网络暂时较慢，可以缩小任务范围后重试。"
        )
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


def reply_text_message(msg_id: str, text: str) -> bool:
    body = ReplyMessageRequestBody.builder() \
        .content(json.dumps({"text": text})) \
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
    if not resp.success():
        log(f"❌ 回复失败: code={resp.code}, msg={resp.msg}")
        return False
    log("✅ 回复成功")
    return True


def detect_image_extension(raw_bytes: bytes, fallback_name: str = "") -> str:
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw_bytes.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if raw_bytes.startswith(b"BM"):
        return ".bmp"
    if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP":
        return ".webp"

    extension = os.path.splitext(fallback_name)[1].lower()
    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return extension
    return ".png"


def image_media_type(path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(extension, "image/png")


def extract_openai_output_text(response: dict) -> str:
    if response.get("output_text"):
        return str(response["output_text"]).strip()
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(str(content["text"]))
    return "\n".join(parts).strip()


def extract_chat_completion_text(response: dict) -> str:
    choices = response.get("choices", [])
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("text")
        ).strip()
    return str(content).strip()


def analyze_image_with_vision_api(path: str) -> str:
    """Analyze an image with a separate vision-capable API."""
    if not VISION_API_KEY:
        raise RuntimeError(
            "尚未配置视觉模型。当前 DeepSeek 接口不支持图片输入；"
            "请设置 VISION_API_KEY 后重启 Bot。"
        )

    with open(path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")
    media_type = image_media_type(path)
    prompt = (
        "请用中文分析这张图片。说明：1. 主要内容；"
        "2. 所有可识别文字、数字、图表轴和关键数据；"
        "3. 对图片含义的解释；4. 适合记录进 Obsidian 的要点。"
        "不确定的内容必须明确标注，不要猜测。"
    )

    if VISION_PROVIDER == "anthropic":
        url = VISION_API_URL or "https://api.anthropic.com/v1/messages"
        model = VISION_MODEL or "claude-sonnet-4-6"
        payload = {
            "model": model,
            "max_tokens": 1600,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        headers = {
            "x-api-key": VISION_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    elif VISION_PROVIDER == "openai":
        url = VISION_API_URL or "https://api.openai.com/v1/responses"
        model = VISION_MODEL or "gpt-5.4-mini"
        payload = {
            "model": model,
            "max_output_tokens": 1600,
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{media_type};base64,{image_b64}",
                    },
                ],
            }],
        }
        headers = {
            "Authorization": f"Bearer {VISION_API_KEY}",
            "content-type": "application/json",
        }
    elif VISION_PROVIDER == "xiaomi":
        url = VISION_API_URL or \
            "https://api.xiaomimimo.com/v1/chat/completions"
        model = VISION_MODEL or "mimo-v2-omni"
        payload = {
            "model": model,
            "max_tokens": 1600,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}",
                        },
                    },
                ],
            }],
        }
        headers = {
            "Authorization": f"Bearer {VISION_API_KEY}",
            "content-type": "application/json",
        }
    else:
        raise RuntimeError(
            f"不支持的 VISION_PROVIDER: {VISION_PROVIDER}，"
            "请使用 anthropic、openai 或 xiaomi。"
        )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=MAX_CLAUDE_SECONDS,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"视觉 API 请求失败（HTTP {e.code}）：{error_body[:400]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"视觉 API 连接失败：{e.reason}") from e

    if VISION_PROVIDER == "anthropic":
        text = "\n".join(
            str(block.get("text", ""))
            for block in result.get("content", [])
            if block.get("type") == "text"
        ).strip()
    elif VISION_PROVIDER == "xiaomi":
        text = extract_chat_completion_text(result)
    else:
        text = extract_openai_output_text(result)

    if not text:
        raise RuntimeError("视觉模型返回了空结果。")
    return text

# ============================================================
# 消息处理（官方 EventDispatcherHandler 模式）
# ============================================================


def process_text_message(sender_id: str, msg_id: str, user_text: str) -> None:
    """Run Claude outside the WebSocket callback so heartbeats stay responsive."""
    try:
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
        reply_text_message(msg_id, reply)

    except json.JSONDecodeError:
        log("消息 JSON 解析失败，跳过")
    except Exception as e:
        log(f"处理异常: {type(e).__name__}: {e}")


def handle_image_message(sender_id: str, msg_id: str, image_key: str) -> None:
    """Download a Feishu message image, then ask Claude to analyze it."""
    try:
        log(f"🖼️ {sender_id[-8:]}: 收到图片 {image_key[-16:]}")

        if ALLOWED_USERS and sender_id not in ALLOWED_USERS:
            log(f"⛔ 非白名单用户: {sender_id}")
            return

        # Images received in messages use the message-resource endpoint.
        req = GetMessageResourceRequest.builder() \
            .message_id(msg_id) \
            .file_key(image_key) \
            .type("image") \
            .build()
        resp = lark.Client.builder() \
            .app_id(APP_ID) \
            .app_secret(APP_SECRET) \
            .build() \
            .im.v1.message_resource.get(req)

        if not resp.success():
            log(f"下载图片失败: code={resp.code}, msg={resp.msg}")
            reply_text_message(
                msg_id,
                f"图片下载失败：{resp.msg or resp.code}。请检查飞书应用的消息资源权限。",
            )
            return

        # Get raw bytes from response
        raw_bytes: bytes | None = None
        if hasattr(resp, "file") and resp.file is not None:
            if hasattr(resp.file, "read"):
                raw_bytes = resp.file.read()
            elif isinstance(resp.file, (bytes, bytearray)):
                raw_bytes = bytes(resp.file)

        if not raw_bytes:
            log("图片响应中无文件数据")
            reply_text_message(msg_id, "图片下载成功，但响应中没有可读取的图片数据。")
            return

        response_name = getattr(resp, "file_name", None) or ""
        extension = detect_image_extension(raw_bytes, response_name)
        image_hash = hashlib.sha256(image_key.encode("utf-8")).hexdigest()[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"feishu_{timestamp}_{image_hash}{extension}"

        os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
        save_path = os.path.join(IMAGE_SAVE_DIR, fname)

        with open(save_path, "wb") as f:
            f.write(raw_bytes)

        log(f"💾 图片已保存: {save_path} ({len(raw_bytes)} bytes)")

        relative_path = os.path.relpath(save_path, VAULT_PATH).replace("\\", "/")
        try:
            vision_result = analyze_image_with_vision_api(save_path)
        except RuntimeError as e:
            log(f"视觉分析不可用: {e}")
            reply_text_message(
                msg_id,
                f"图片已保存到 `{relative_path}`，但暂时无法识别：{e}",
            )
            return

        prompt = (
            f"用户刚刚发送了一张图片，保存在 `{relative_path}`。"
            "独立视觉模型已经得到以下分析：\n\n"
            f"{vision_result}\n\n"
            "请基于这份视觉结果，用中文给用户一个清晰、简洁的总结，"
            "并把图片内容作为当前连续会话的上下文记住。"
        )
        analysis = clean_reply(run_claude(sender_id, prompt))
        size_kb = len(raw_bytes) / 1024
        reply = (
            f"图片已保存到 `{relative_path}`（{size_kb:.1f} KB）\n\n"
            f"{analysis}"
        )
        log(f"🧠 图片分析 → {analysis[:80]}")
        reply_text_message(msg_id, clean_reply(reply))

    except Exception as e:
        log(f"图片处理异常: {type(e).__name__}: {e}")
        reply_text_message(msg_id, f"图片处理失败：{type(e).__name__}: {e}")


def do_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """Receive an event and hand slow work to a background thread."""
    try:
        msg = data.event.message
        sender_id = data.event.sender.sender_id.open_id
        msg_type = msg.message_type
        msg_id = msg.message_id

        if msg_id in SEEN_MESSAGES:
            return
        SEEN_MESSAGES.add(msg_id)
        if len(SEEN_MESSAGES) > MAX_SEEN:
            SEEN_MESSAGES.clear()

        if msg_type == "image":
            content = json.loads(msg.content)
            image_key = content.get("image_key", "")
            if image_key:
                MESSAGE_EXECUTOR.submit(
                    handle_image_message,
                    sender_id,
                    msg_id,
                    image_key,
                )
            else:
                log("图片消息中无 image_key，跳过")
            return

        if msg_type != "text":
            log(f"跳过非文本消息: {msg_type}")
            return

        content = json.loads(msg.content)
        user_text = content.get("text", "").strip()
        if not user_text:
            return

        MESSAGE_EXECUTOR.submit(
            process_text_message,
            sender_id,
            msg_id,
            user_text,
        )
    except json.JSONDecodeError:
        log("消息 JSON 解析失败，跳过")
    except Exception as e:
        log(f"接收消息异常: {type(e).__name__}: {e}")

# ============================================================
# 每日蒸馏 Watchdog — 检测 Codex 自动化完成并主动通知
# ============================================================

WATCH_DISTILL_DIR = os.path.join(VAULT_PATH, "多物理场仿真", "每日蒸馏")
DISTILL_NOTIFIED_FILE = os.path.join(SCRIPT_DIR, ".feishu-distill-notified.json")


def _load_distill_notified() -> set[str]:
    try:
        with open(DISTILL_NOTIFIED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def _save_distill_notified(notified: set[str]) -> None:
    temp = DISTILL_NOTIFIED_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(sorted(notified), f)
    os.replace(temp, DISTILL_NOTIFIED_FILE)


def _send_proactive_msg(open_id: str, text: str) -> None:
    """Send a direct message (not a reply) to a user via Feishu API."""
    try:
        body = CreateMessageRequestBody.builder() \
            .receive_id(open_id) \
            .msg_type("text") \
            .content(json.dumps({"text": text})) \
            .build()
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(body) \
            .build()
        resp = lark.Client.builder() \
            .app_id(APP_ID) \
            .app_secret(APP_SECRET) \
            .build() \
            .im.v1.message.create(req)
        if not resp.success():
            log(f"[Watchdog] 通知失败 {open_id[-8:]}: code={resp.code}")
    except Exception as e:
        log(f"[Watchdog] 通知异常: {e}")


def _summarize_daily_report(report_path: str, fname: str) -> str:
    """Extract key info from daily distill report for a push notification."""
    date_str = fname.replace(".md", "")
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            text = f.read()

        new_count = text.count("✅")
        pending_high = text.count("🔴")
        pending_warn = text.count("⚠️")
        pending_total = pending_high + pending_warn

        lines = [
            f"🥃 每日蒸馏完成 — {date_str}",
            "",
        ]
        if new_count:
            lines.append(f"📥 今日处理: {new_count} 个新文件")
        else:
            lines.append("📥 今日无新文件")
        if pending_total:
            lines.append(f"📋 待你审阅: {pending_total} 篇 inbox 摘要")
            if pending_high:
                lines.append(f"   🔴 {pending_high} 篇超过 7 天")
            if pending_warn:
                lines.append(f"   ⚠️ {pending_warn} 篇超过 3 天")

        lines.extend(["", f"📄 报告: 多物理场仿真/每日蒸馏/{fname}"])
        return "\n".join(lines)
    except Exception as e:
        log(f"[Watchdog] 读报告失败: {e}")
        return f"🥃 每日蒸馏完成 — {date_str}\n\n📄 报告: 多物理场仿真/每日蒸馏/{fname}"


def _watch_daily_distill() -> None:
    """Background daemon: poll for new daily distill reports and push notify."""
    notified = _load_distill_notified()

    # Seed with already-existing reports so we don't re-notify on restart
    if os.path.isdir(WATCH_DISTILL_DIR):
        for fname in os.listdir(WATCH_DISTILL_DIR):
            if fname.endswith(".md") and fname not in (".state.md", "README.md"):
                notified.add(fname)
        _save_distill_notified(notified)

    notify_env = os.environ.get("FEISHU_NOTIFY_USERS", "")
    notify_users = [u for u in notify_env.split(",") if u] if notify_env else ALLOWED_USERS

    if not os.path.isdir(WATCH_DISTILL_DIR):
        log(f"[Watchdog] 蒸馏目录不存在，跳过: {WATCH_DISTILL_DIR}")
        return

    log(f"[Watchdog] 监控: {WATCH_DISTILL_DIR} | 目标用户: {len(notify_users)} | "
        f"已追踪 {len(notified)} 份旧报告")

    while True:
        try:
            for fname in os.listdir(WATCH_DISTILL_DIR):
                if not fname.endswith(".md"):
                    continue
                if fname in (".state.md", "README.md"):
                    continue
                if fname in notified:
                    continue

                log(f"[Watchdog] 🥃 新报告: {fname}")
                notified.add(fname)
                _save_distill_notified(notified)

                report_path = os.path.join(WATCH_DISTILL_DIR, fname)
                summary = _summarize_daily_report(report_path, fname)

                if not notify_users:
                    log("[Watchdog] 无通知目标用户，跳过推送")
                for uid in notify_users:
                    _send_proactive_msg(uid, summary)
                    _time_module.sleep(0.5)
        except Exception as e:
            log(f"[Watchdog] 扫描异常: {e}")

        _time_module.sleep(60)

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

    # 启动每日蒸馏 Watchdog（后台轮询新报告并主动通知）
    threading.Thread(
        target=_watch_daily_distill,
        daemon=True,
        name="distill-watchdog",
    ).start()

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
