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
import tempfile
import threading
import time as _time_module
import urllib.error
import urllib.request
import wave
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Windows 控制台 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody,
    CreateMessageRequest, CreateMessageRequestBody,
    CreateFileRequest, CreateFileRequestBody,
    GetMessageResourceRequest,
)
from stock_research import (
    StockResearchService,
    WatchlistStore,
    extract_stock_codes,
    is_report_due,
    normalize_stock_code,
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
WS_RESTART_AFTER_SECONDS = int(
    os.environ.get("WS_RESTART_AFTER_SECONDS", "240")
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "feishu-bot.log")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".feishu-claude-bot.lock")
SESSION_FILE = os.path.join(SCRIPT_DIR, ".feishu-claude-sessions.json")
VOICE_SETTINGS_FILE = os.path.join(SCRIPT_DIR, ".feishu-voice-settings.json")
HEALTH_FILE = os.path.join(SCRIPT_DIR, ".feishu-bot-health.json")
STOCK_WATCHLIST_FILE = os.path.join(SCRIPT_DIR, ".feishu-stock-watchlists.json")
STOCK_REPORT_STATE_FILE = os.path.join(
    SCRIPT_DIR,
    ".feishu-stock-report-state.json",
)
IMAGE_SAVE_DIR = os.path.join(VAULT_PATH, "多物理场仿真", "raw", "图片")
STOCK_ENABLED = (
    os.environ.get("STOCK_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
)
STOCK_REPORT_TIME = os.environ.get("STOCK_REPORT_TIME", "15:30")
STOCK_TIMEZONE = os.environ.get("STOCK_TIMEZONE", "Asia/Shanghai")
try:
    STOCK_REPORT_HOUR, STOCK_REPORT_MINUTE = (
        int(part) for part in STOCK_REPORT_TIME.split(":", 1)
    )
    if not (0 <= STOCK_REPORT_HOUR <= 23 and 0 <= STOCK_REPORT_MINUTE <= 59):
        raise ValueError
except ValueError:
    STOCK_REPORT_HOUR, STOCK_REPORT_MINUTE = 15, 30
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "anthropic").lower()
VISION_API_KEY = os.environ.get("VISION_API_KEY", "")
VISION_API_URL = os.environ.get("VISION_API_URL", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "")
TTS_API_KEY = os.environ.get("TTS_API_KEY") or VISION_API_KEY
TTS_API_URL = (
    os.environ.get("TTS_API_URL")
    or "https://api.xiaomimimo.com/v1/chat/completions"
)
TTS_MODEL = os.environ.get("TTS_MODEL") or "mimo-v2.5-tts"
TTS_VOICE = os.environ.get("TTS_VOICE") or "mimo_default"
TTS_VOICE_NAME = os.environ.get("TTS_VOICE_NAME") or TTS_VOICE
TTS_VOICE_REFERENCE = os.environ.get("TTS_VOICE_REFERENCE", "").strip()
if TTS_VOICE_REFERENCE and not os.path.isabs(TTS_VOICE_REFERENCE):
    TTS_VOICE_REFERENCE = os.path.join(SCRIPT_DIR, TTS_VOICE_REFERENCE)
TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS") or "1200")
TTS_PLAYBACK_SPEED = min(
    max(float(os.environ.get("TTS_PLAYBACK_SPEED") or "1.10"), 0.5),
    2.0,
)
TTS_DYNAMIC_STYLE = (
    os.environ.get("TTS_DYNAMIC_STYLE") or "true"
).lower() in {"1", "true", "yes", "on"}
TTS_DIRECTOR_MODEL = (
    os.environ.get("TTS_DIRECTOR_MODEL") or "mimo-v2-flash"
)

TTS_STYLE_PRESETS = {
    "natural": (
        "自然、松弛、亲切地说话，像真实聊天。语速中等，停顿自然，"
        "不要播音腔，不要刻意强调。"
    ),
    "romantic": (
        "像日常聊天一样自然、连贯地说情话，语气柔和亲昵，带一点轻松的"
        "暧昧和笑意。使用正常偏快的语速，短语自然连读，咬字放松，不要"
        "逐字强调。魅惑感只轻轻藏在语气里，不耳语、不气声、不拖尾、"
        "不刻意压低声音，也不要像舞台表演或强势挑逗。"
    ),
    "technical": (
        "清晰、耐心、可信地讲解技术内容。语速中等偏慢，术语和关键结论"
        "稍作强调，层次分明，但保持自然交流感。"
    ),
    "comforting": (
        "用温柔、安定、包容的语气回应。语速稍慢，音量感柔和，"
        "句间留出自然停顿，不说教，不制造额外焦虑。"
    ),
    "cheerful": (
        "用明亮、轻快、有感染力的语气表达好消息。节奏稍快，带自然笑意，"
        "但不要尖锐、亢奋或过度夸张。"
    ),
    "warning": (
        "用沉稳、严肃、明确的语气说明风险或提醒。语速适中，重点清楚，"
        "不恐吓，不冷漠，也不要使用戏剧化语气。"
    ),
    "narrative": (
        "用有画面感的叙述语气朗读。节奏有轻微起伏，转折处自然停顿，"
        "保持克制，不要舞台腔。"
    ),
    "summary": (
        "用简洁、沉稳、利落的语气朗读总结。语速中等偏快，"
        "突出结论和行动项，弱化格式符号感。"
    ),
}

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
VOICE_SETTINGS_LOCK = threading.Lock()
HEALTH_LOCK = threading.Lock()
STOCK_REPORT_STATE_LOCK = threading.Lock()
STOCK_SERVICE = StockResearchService()
STOCK_WATCHLISTS = WatchlistStore(STOCK_WATCHLIST_FILE)
WS_HEALTH = {
    "state": "starting",
    "state_since": _time_module.time(),
    "detail": "",
}
MESSAGE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="feishu-message",
)

# 消息去重（飞书可能对同一消息推送多次）
SEEN_MESSAGES = set()
MAX_SEEN = 200  # 防止无限增长
# ============================================================


def update_ws_health(state: str, detail: str = "") -> None:
    now = _time_module.time()
    with HEALTH_LOCK:
        if WS_HEALTH["state"] != state:
            WS_HEALTH["state"] = state
            WS_HEALTH["state_since"] = now
        WS_HEALTH["detail"] = detail[:300]
        payload = {
            "state": WS_HEALTH["state"],
            "state_since": WS_HEALTH["state_since"],
            "heartbeat_at": now,
            "pid": os.getpid(),
            "detail": WS_HEALTH["detail"],
        }
        temp_file = HEALTH_FILE + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(temp_file, HEALTH_FILE)
        except OSError as e:
            log(f"连接健康状态写入失败: {e}")


def watch_ws_health(client) -> None:
    """Publish connection health and recover a reconnect loop that gets stuck."""
    while True:
        try:
            connection = getattr(client, "_conn", None)
            is_closed = bool(getattr(connection, "closed", False))
            if connection is not None and not is_closed:
                update_ws_health("connected")
            elif WS_HEALTH["state"] not in {"reconnecting", "starting"}:
                update_ws_health("disconnected")
        except Exception as e:
            update_ws_health("unknown", f"{type(e).__name__}: {e}")

        with HEALTH_LOCK:
            state = str(WS_HEALTH["state"])
            state_age = _time_module.time() - float(WS_HEALTH["state_since"])
        if (
            state in {"starting", "reconnecting", "disconnected", "unknown"}
            and state_age > WS_RESTART_AFTER_SECONDS
        ):
            log(
                f"飞书连接状态 {state} 已持续 {state_age:.0f} 秒，"
                "准备自恢复重启"
            )
            update_ws_health("restarting", f"stale {state}: {state_age:.0f}s")
            if sys.platform == "win32":
                powershell = os.path.join(
                    os.environ.get("SystemRoot", r"C:\Windows"),
                    "System32",
                    "WindowsPowerShell",
                    "v1.0",
                    "powershell.exe",
                )
                restart_command = (
                    "Start-Sleep -Seconds 5; "
                    "Start-ScheduledTask -TaskName 'FeishuClaudeBot'"
                )
                try:
                    subprocess.Popen(
                        [
                            powershell,
                            "-NoProfile",
                            "-WindowStyle", "Hidden",
                            "-Command", restart_command,
                        ],
                        cwd=SCRIPT_DIR,
                        creationflags=(
                            subprocess.CREATE_NO_WINDOW
                            | subprocess.DETACHED_PROCESS
                        ),
                        close_fds=True,
                    )
                except OSError as e:
                    log(f"安排计划任务重启失败: {e}")
            os._exit(3)
        _time_module.sleep(20)


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


def load_voice_settings() -> dict[str, bool]:
    with VOICE_SETTINGS_LOCK:
        try:
            with open(VOICE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
    return {
        str(user_id): bool(enabled)
        for user_id, enabled in data.items()
        if user_id
    }


def save_voice_settings() -> None:
    temp_file = VOICE_SETTINGS_FILE + ".tmp"
    with VOICE_SETTINGS_LOCK:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(VOICE_SETTINGS, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, VOICE_SETTINGS_FILE)


VOICE_SETTINGS = load_voice_settings()


def reset_session(sender_id: str) -> None:
    SESSIONS.pop(sender_id, None)
    save_sessions(SESSIONS)


def invoke_claude(
    prompt: str,
    session_id: str | None,
    response_instruction: str = "",
) -> subprocess.CompletedProcess:
    system_prompt = (
        "你是通过飞书操作本机 Obsidian Vault 的助手。当前工作目录就是 Vault 根目录。"
        "优先用 Read、Glob、Grep、Edit、Write 工具完成用户请求。"
        "只操作当前 Vault 内的文件，不执行破坏性操作；修改后用中文简要说明结果。"
        "这是连续会话，请结合此前对话理解代词、追问和用户未重复说明的上下文。"
    )
    if response_instruction:
        system_prompt += response_instruction
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


def run_claude(
    sender_id: str,
    prompt: str,
    response_instruction: str = "",
) -> str:
    try:
        with CLAUDE_RUN_LOCK:
            session_id = SESSIONS.get(sender_id)
            result = invoke_claude(
                prompt,
                session_id,
                response_instruction,
            )

            # A Claude update or deleted local history can invalidate a session.
            # Retry once as a fresh conversation instead of breaking the bot.
            if result.returncode != 0 and session_id:
                error = result.stderr.strip() or result.stdout.strip()
                log(f"会话恢复失败，自动新建会话: {error[:300]}")
                reset_session(sender_id)
                result = invoke_claude(
                    prompt,
                    None,
                    response_instruction,
                )

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


def markdown_to_feishu_text(text: str) -> str:
    """Convert common Markdown into readable Feishu plain text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    code_blocks: list[str] = []

    def preserve_code(match: re.Match) -> str:
        language = match.group(1).strip()
        code = match.group(2).strip("\n")
        label = f"代码（{language}）：" if language else "代码："
        token = f"@@FEISHU_CODE_BLOCK_{len(code_blocks)}@@"
        code_blocks.append(f"{label}\n{code}")
        return f"\n{token}\n"

    text = re.sub(
        r"```([^\n`]*)\n?(.*?)```",
        preserve_code,
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: f"图片：{m.group(1) or m.group(2)}（{m.group(2)}）",
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f"{m.group(1)}（{m.group(2)}）",
        text,
    )
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-*_](?:\s*[-*_]){2,}\s*$", "────────", text)
    text = re.sub(r"(?m)^\s*>\s?", "│ ", text)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+\[x\]\s+", r"\1☑ ", text, flags=re.I)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+\[ \]\s+", r"\1☐ ", text)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+", r"\1• ", text)

    lines = text.split("\n")
    formatted_lines = []
    for line in lines:
        stripped = line.strip()
        if "|" in line and re.fullmatch(
            r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?",
            stripped,
        ):
            continue
        if "|" in line and not stripped.startswith("@@FEISHU_CODE_BLOCK_"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) > 1:
                indent = line[:len(line) - len(line.lstrip())]
                line = indent + "  ·  ".join(cells)
        formatted_lines.append(line)
    text = "\n".join(formatted_lines)

    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"<[^>\n]+>", "", text)

    for index, code in enumerate(code_blocks):
        text = text.replace(f"@@FEISHU_CODE_BLOCK_{index}@@", code)

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def reply_text_message(msg_id: str, text: str) -> bool:
    text = markdown_to_feishu_text(text)
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


def prepare_tts_text(text: str) -> str:
    text = re.sub(r"```.*?```", "代码内容已省略。", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"https?://\S+", "链接已省略", text)
    text = re.sub(r"[*#>|_~-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= TTS_MAX_CHARS:
        return text

    shortened = text[:TTS_MAX_CHARS]
    sentence_end = max(
        shortened.rfind("。"),
        shortened.rfind("！"),
        shortened.rfind("？"),
    )
    if sentence_end > TTS_MAX_CHARS // 2:
        shortened = shortened[:sentence_end + 1]
    return shortened + " 后续内容请查看文字消息。"


def choose_tts_style(text: str) -> tuple[str, str]:
    """Choose a stable preset and a small content-specific adjustment."""
    if not TTS_DYNAMIC_STYLE:
        return "natural", TTS_STYLE_PRESETS["natural"]

    preset_list = "\n".join(
        f"- {name}: {instruction}"
        for name, instruction in TTS_STYLE_PRESETS.items()
    )
    director_prompt = (
        "你是语音导演。请根据待朗读文本的主要意图，从预设中选择一个。"
        "只有文本的核心是在表达爱意、暧昧、亲昵或撩拨时才选 romantic；"
        "不能仅因为出现“爸爸”“宝贝”等称呼就选 romantic。"
        "romantic 的微调不得要求慢速、耳语、贴耳、气声、拖长尾音、"
        "逐字强调或刻意压低声音；应优先自然连读和正常聊天节奏。"
        "只要核心是在解释代码、配置、故障排查、操作步骤、工程或科学知识，"
        "即使语气亲切，也优先选择 technical。"
        "不要改写待朗读文本。\n\n"
        f"预设：\n{preset_list}\n\n"
        "仅输出一行严格 JSON，不要 Markdown："
        '{"preset":"预设名","adjustment":"不超过40字的具体演绎微调"}'
        f"\n\n待朗读文本：\n{text[:1800]}"
    )
    payload = {
        "model": TTS_DIRECTOR_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "输出必须是有效 JSON，preset 必须来自给定预设。",
            },
            {"role": "user", "content": director_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 120,
    }
    request = urllib.request.Request(
        TTS_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TTS_API_KEY}",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw = extract_chat_completion_text(result)
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        decision = json.loads(match.group(0) if match else raw)
        preset = str(decision.get("preset", "natural")).lower()
        if preset not in TTS_STYLE_PRESETS:
            preset = "natural"
        adjustment = str(decision.get("adjustment", "")).strip()[:80]
    except Exception as e:
        log(f"语音导演失败，回退自然风格: {type(e).__name__}: {e}")
        return "natural", TTS_STYLE_PRESETS["natural"]

    instruction = TTS_STYLE_PRESETS[preset]
    if adjustment:
        instruction += f" 本次微调：{adjustment}"
    return preset, instruction


def synthesize_mimo_speech(text: str) -> tuple[bytes, int]:
    if not TTS_API_KEY:
        raise RuntimeError("尚未配置 TTS_API_KEY。")

    spoken_text = prepare_tts_text(text)
    if not spoken_text:
        raise RuntimeError("没有可朗读的文本。")

    style_name, style_instruction = choose_tts_style(spoken_text)
    log(f"🎙️ 语音风格: {style_name} | {style_instruction[-80:]}")

    messages = [
        {"role": "user", "content": style_instruction},
        {"role": "assistant", "content": spoken_text},
    ]
    if TTS_MODEL.endswith("-voiceclone"):
        if not TTS_VOICE_REFERENCE:
            raise RuntimeError("Voice Clone 模式尚未配置 TTS_VOICE_REFERENCE。")
        try:
            with open(TTS_VOICE_REFERENCE, "rb") as reference_file:
                reference_audio = base64.b64encode(
                    reference_file.read()
                ).decode("ascii")
        except OSError as e:
            raise RuntimeError(
                f"无法读取参考音色：{TTS_VOICE_REFERENCE}"
            ) from e

        extension = os.path.splitext(TTS_VOICE_REFERENCE)[1].lower()
        mime_type = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".pcm": "audio/pcm",
        }.get(extension, "audio/wav")
        audio_options = {
            "format": "wav",
            "voice": f"data:{mime_type};base64,{reference_audio}",
        }
    else:
        audio_options = {"voice": TTS_VOICE, "format": "wav"}

    payload = {
        "model": TTS_MODEL,
        "messages": messages,
        "audio": audio_options,
    }
    if not TTS_MODEL.endswith(("-voiceclone", "-voicedesign")):
        payload["modalities"] = ["text", "audio"]
    request_body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")
    retry_delays = (5, 12, 25)
    for attempt in range(len(retry_delays) + 1):
        request = urllib.request.Request(
            TTS_API_URL,
            data=request_body,
            headers={
                "Authorization": f"Bearer {TTS_API_KEY}",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=MAX_CLAUDE_SECONDS,
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 and attempt < len(retry_delays):
                retry_after = e.headers.get("Retry-After")
                try:
                    delay = max(float(retry_after), 1.0)
                except (TypeError, ValueError):
                    delay = retry_delays[attempt]
                log(
                    f"MiMo TTS 触发限流，{delay:g} 秒后重试 "
                    f"({attempt + 1}/{len(retry_delays)})"
                )
                _time_module.sleep(delay)
                continue
            if e.code == 429:
                raise RuntimeError(
                    "MiMo TTS 当前触发限流，自动重试后仍未恢复，"
                    "请稍后再试。文字回复不受影响。"
                ) from e
            raise RuntimeError(
                f"MiMo TTS 请求失败（HTTP {e.code}）：{error_body[:400]}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"MiMo TTS 连接失败：{e.reason}") from e

    choices = result.get("choices", [])
    choice = choices[0] if choices else {}
    message = choice.get("message", {})
    audio_data = message.get("audio", {}).get("data")
    if not audio_data:
        explanation = extract_chat_completion_text(result)
        finish_reason = str(choice.get("finish_reason", "")).strip()
        detail = re.sub(r"\s+", " ", explanation).strip()[:160]
        log(
            "MiMo TTS 未返回音频"
            f" | finish_reason={finish_reason or 'unknown'}"
            f" | content={detail or '<empty>'}"
        )
        if detail:
            raise RuntimeError(
                "MiMo 没有为这段文本生成语音，可能受到内容限制。"
                f"模型说明：{detail}"
            )
        raise RuntimeError(
            "MiMo 没有为这段文本生成语音，可能受到内容限制或服务繁忙。"
        )

    wav_bytes = base64.b64decode(audio_data)
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        duration_ms = round(
            wav_file.getnframes() / wav_file.getframerate() * 1000
        )

    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise RuntimeError(
            "缺少 imageio-ffmpeg，请运行 `py -m pip install imageio-ffmpeg`。"
        ) from e

    with tempfile.TemporaryDirectory(prefix="feishu-tts-") as temp_dir:
        wav_path = os.path.join(temp_dir, "speech.wav")
        opus_path = os.path.join(temp_dir, "speech.opus")
        with open(wav_path, "wb") as f:
            f.write(wav_bytes)

        conversion = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-loglevel", "error",
                "-i", wav_path,
                "-filter:a", f"atempo={TTS_PLAYBACK_SPEED:g}",
                "-c:a", "libopus",
                "-b:a", "32k",
                "-vbr", "on",
                "-application", "voip",
                opus_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        if conversion.returncode != 0:
            raise RuntimeError(
                f"Opus 转码失败：{conversion.stderr.strip()[:400]}"
            )
        with open(opus_path, "rb") as f:
            opus_bytes = f.read()

    adjusted_duration_ms = round(duration_ms / TTS_PLAYBACK_SPEED)
    return opus_bytes, adjusted_duration_ms


def reply_audio_message(msg_id: str, text: str) -> bool:
    opus_bytes, duration_ms = synthesize_mimo_speech(text)
    audio_stream = BytesIO(opus_bytes)
    audio_stream.name = "reply.opus"

    upload_body = CreateFileRequestBody.builder() \
        .file_type("opus") \
        .file_name("reply.opus") \
        .duration(duration_ms) \
        .file(audio_stream) \
        .build()
    upload_req = CreateFileRequest.builder() \
        .request_body(upload_body) \
        .build()
    upload_resp = lark.Client.builder() \
        .app_id(APP_ID) \
        .app_secret(APP_SECRET) \
        .build() \
        .im.v1.file.create(upload_req)
    if not upload_resp.success():
        raise RuntimeError(
            f"飞书音频上传失败: code={upload_resp.code}, "
            f"msg={upload_resp.msg}"
        )

    file_key = upload_resp.data.file_key
    body = ReplyMessageRequestBody.builder() \
        .content(json.dumps({"file_key": file_key})) \
        .msg_type("audio") \
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
        raise RuntimeError(
            f"飞书语音发送失败: code={resp.code}, msg={resp.msg}"
        )

    log(f"🔊 语音回复成功: {duration_ms}ms / {len(opus_bytes)} bytes")
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


def _stock_help_text() -> str:
    return (
        "A 股模拟研究命令：\n"
        "/stock report：查看今天的全市场模拟关注名单\n"
        "/stock 600519：查看指定股票，单次最多 5 只\n"
        "/watch add 600519：加入个人观察列表\n"
        "/watch remove 600519：移出观察列表\n"
        "/watch list：查看观察列表\n\n"
        "也可以直接问：今天有哪些股票值得关注？或：看看 600519。\n"
        "股票报告固定使用文字，仅供模拟盘学习，不构成真实交易建议。"
    )


def _is_market_stock_request(text: str) -> bool:
    lowered = text.lower()
    phrases = (
        "哪些股票", "什么股票值得", "股票值得入手", "股票值得关注",
        "模拟盘选股", "今日选股", "今天选股", "收盘股票报告",
        "股票收盘报告", "股票报告",
    )
    return any(phrase in lowered for phrase in phrases)


def handle_stock_request(sender_id: str, text: str) -> tuple[bool, str]:
    """Return whether the text is a stock request and its plain-text reply."""
    normalized = text.strip()
    lowered = normalized.lower()
    is_stock_command = lowered == "/stock" or lowered.startswith("/stock ")
    is_watch_command = lowered == "/watch" or lowered.startswith("/watch ")
    natural_codes = extract_stock_codes(normalized, require_context=True)
    market_request = _is_market_stock_request(normalized)
    if not (is_stock_command or is_watch_command or natural_codes or market_request):
        return False, ""
    if not STOCK_ENABLED:
        return True, "股票研究功能当前已关闭。设置 STOCK_ENABLED=true 后重启 Bot。"

    try:
        if is_watch_command:
            parts = normalized.split()
            if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "list"):
                values = STOCK_WATCHLISTS.list(sender_id)
                if not values:
                    return True, "你的个人观察列表还是空的。使用 /watch add 600519 添加。"
                return True, "你的个人观察列表：\n" + "\n".join(
                    f"{index}. {code}" for index, code in enumerate(values, 1)
                )
            if len(parts) != 3 or parts[1].lower() not in {"add", "remove"}:
                return True, _stock_help_text()
            code = normalize_stock_code(parts[2])
            if not code:
                return True, "没有识别到有效的 A 股六位代码。"
            if parts[1].lower() == "add":
                identity = STOCK_SERVICE.stock_identity(code)
                if not identity:
                    return True, f"没有在当前 A 股代码表中找到 {code}。"
                if len(STOCK_WATCHLISTS.list(sender_id)) >= 30:
                    return True, "个人观察列表最多保存 30 只股票。"
                added = STOCK_WATCHLISTS.add(sender_id, code)
                state = "已加入" if added else "已经在"
                return True, f"{identity[1]}（{code}）{state}你的个人观察列表。"
            removed = STOCK_WATCHLISTS.remove(sender_id, code)
            return True, (
                f"{code} 已移出个人观察列表。"
                if removed else f"{code} 不在你的个人观察列表中。"
            )

        if lowered in {"/stock", "/stock help"}:
            return True, _stock_help_text()
        if lowered in {"/stock report", "/stock market"} or market_request:
            return True, STOCK_SERVICE.market_report()

        if is_stock_command:
            codes = extract_stock_codes(normalized[6:], require_context=False)
        else:
            codes = natural_codes
        if not codes:
            return True, "没有识别到有效的 A 股六位代码。\n\n" + _stock_help_text()
        if len(codes) > 5:
            return True, "单次最多分析 5 只股票，请缩小范围后再试。"
        return True, STOCK_SERVICE.code_report(codes)
    except Exception as e:
        log(f"[Stock] 请求失败: {type(e).__name__}: {e}")
        return True, (
            f"股票数据暂时没有取到：{e}\n"
            "本次不会用猜测值补全，请稍后再试。"
        )


def process_text_message(sender_id: str, msg_id: str, user_text: str) -> None:
    """Run Claude outside the WebSocket callback so heartbeats stay responsive."""
    try:
        log(f"📩 {sender_id[-8:]}: {user_text[:80]}")

        # 白名单
        if ALLOWED_USERS and sender_id not in ALLOWED_USERS:
            log(f"⛔ 非白名单用户: {sender_id}")
            return

        normalized = user_text.strip()
        lower_text = normalized.lower()
        force_voice = False

        stock_handled, stock_reply = handle_stock_request(sender_id, normalized)
        if stock_handled:
            reply_text_message(msg_id, clean_reply(stock_reply))
            return

        if lower_text == "/voice on":
            VOICE_SETTINGS[sender_id] = True
            save_voice_settings()
            reply_text_message(
                msg_id,
                f"语音回复已开启。当前音色：{TTS_VOICE_NAME}。"
            )
            return
        if lower_text == "/voice off":
            VOICE_SETTINGS[sender_id] = False
            save_voice_settings()
            reply_text_message(msg_id, "语音回复已关闭。")
            return
        if lower_text in {"/voice", "/voice status"}:
            status = "开启" if VOICE_SETTINGS.get(sender_id, False) else "关闭"
            reply_text_message(
                msg_id,
                f"语音回复当前为：{status}。音色：{TTS_VOICE_NAME}。\n"
                "命令：/voice on、/voice off、/voice 你的问题"
            )
            return
        if lower_text.startswith("/voice "):
            normalized = normalized[7:].strip()
            if not normalized:
                reply_text_message(msg_id, "请在 /voice 后填写问题。")
                return
            force_voice = True

        voice_mode = force_voice or VOICE_SETTINGS.get(sender_id, False)
        if lower_text in {"/new", "/reset"} or normalized == "新会话":
            reset_session(sender_id)
            reply = "已开启新会话。下一条消息将不再使用之前的聊天上下文。"
        else:
            # 调 Claude
            response_instruction = ""
            if voice_mode:
                response_instruction = (
                    " 当前回复将被直接合成为语音。默认先给结论，并控制在3到6个"
                    "简短句子内；避免 Markdown 标题、表格和冗长列表，使用自然口语。"
                    "如果用户明确要求详细解释、完整步骤或长篇内容，则按用户要求展开。"
                )
            reply = run_claude(
                sender_id,
                normalized,
                response_instruction=response_instruction,
            )
        reply = clean_reply(reply)
        log(f"🤖 → {reply[:80]}")

        if voice_mode:
            try:
                reply_audio_message(msg_id, reply)
            except Exception as e:
                log(f"语音回复失败: {type(e).__name__}: {e}")
                reply_text_message(
                    msg_id,
                    f"{reply}\n\n（语音生成失败，已回退为文字：{e}）",
                )
        else:
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


def _send_proactive_msg(open_id: str, text: str) -> bool:
    """Send a direct message (not a reply) to a user via Feishu API."""
    try:
        text = markdown_to_feishu_text(clean_reply(text))
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
            return False
        return True
    except Exception as e:
        log(f"[Watchdog] 通知异常: {e}")
        return False


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


def _load_stock_report_state() -> dict:
    with STOCK_REPORT_STATE_LOCK:
        try:
            with open(STOCK_REPORT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}


def _save_stock_report_state(state: dict) -> None:
    temp = STOCK_REPORT_STATE_FILE + ".tmp"
    with STOCK_REPORT_STATE_LOCK:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(temp, STOCK_REPORT_STATE_FILE)


def _watch_stock_reports() -> None:
    """Push one A-share market report after the configured close time."""
    if not STOCK_ENABLED:
        log("[Stock] 股票研究功能已关闭")
        return
    notify_env = (
        os.environ.get("FEISHU_STOCK_NOTIFY_USERS", "")
        or os.environ.get("FEISHU_NOTIFY_USERS", "")
    )
    configured_users = (
        [value.strip() for value in notify_env.split(",") if value.strip()]
        if notify_env else ALLOWED_USERS
    )
    notify_users = list(dict.fromkeys(configured_users or SESSIONS.keys()))
    if not configured_users and notify_users:
        log(f"[Stock] 使用 {len(notify_users)} 位已有会话用户作为日报接收人")
    elif not notify_users:
        log("[Stock] 暂无通知用户；建立飞书会话后会自动成为日报接收人")
    try:
        timezone = ZoneInfo(STOCK_TIMEZONE)
    except ZoneInfoNotFoundError:
        log(f"[Stock] 未找到时区 {STOCK_TIMEZONE}，改用 Asia/Shanghai")
        timezone = ZoneInfo("Asia/Shanghai")

    state = _load_stock_report_state()
    last_attempt_at = 0.0
    log(
        f"[Stock] 收盘报告时间 {STOCK_REPORT_HOUR:02d}:{STOCK_REPORT_MINUTE:02d} "
        f"{STOCK_TIMEZONE} | 目标用户: {len(notify_users)}"
    )
    while True:
        now = datetime.now(timezone)
        try:
            trading_day = STOCK_SERVICE.is_trading_day(now.date())
            due = is_report_due(
                now,
                STOCK_REPORT_HOUR,
                STOCK_REPORT_MINUTE,
                str(state.get("last_sent_date") or ""),
                trading_day,
            )
            if due and _time_module.time() - last_attempt_at >= 15 * 60:
                last_attempt_at = _time_module.time()
                if not notify_users:
                    notify_users = list(SESSIONS.keys())
                    if not notify_users:
                        continue
                log(f"[Stock] 开始生成 {now.date()} 全市场收盘报告")
                report = STOCK_SERVICE.market_report(
                    today=now.date(),
                    force_refresh=True,
                )
                sent = False
                for user_id in notify_users:
                    sent = _send_proactive_msg(user_id, report) or sent
                    _time_module.sleep(0.5)
                if sent:
                    state = {
                        "last_sent_date": now.date().isoformat(),
                        "sent_at": now.isoformat(),
                    }
                    _save_stock_report_state(state)
                    log(f"[Stock] {now.date()} 收盘报告已发送")
                else:
                    log("[Stock] 收盘报告发送失败，15 分钟后重试")
        except Exception as e:
            log(f"[Stock] 收盘报告异常: {type(e).__name__}: {e}")
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
    cli.on_reconnecting = lambda: update_ws_health("reconnecting")
    cli.on_reconnected = lambda: update_ws_health("connected")
    update_ws_health("starting")
    threading.Thread(
        target=watch_ws_health,
        args=(cli,),
        daemon=True,
        name="feishu-ws-health",
    ).start()

    # 启动每日蒸馏 Watchdog（后台轮询新报告并主动通知）
    threading.Thread(
        target=_watch_daily_distill,
        daemon=True,
        name="distill-watchdog",
    ).start()

    threading.Thread(
        target=_watch_stock_reports,
        daemon=True,
        name="stock-report-watchdog",
    ).start()

    log("[OK] Feishu Bot started, waiting for messages...")
    print("[OK] Connected. Send message from Feishu app. Ctrl+C to stop")
    try:
        cli.start()
    except KeyboardInterrupt:
        log("Feishu Bot stopped")
        update_ws_health("stopped", "KeyboardInterrupt")
    except Exception as e:
        log(f"飞书长连接异常退出: {type(e).__name__}: {e}")
        update_ws_health("failed", f"{type(e).__name__}: {e}")
        return 3
    finally:
        instance_lock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
