from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cli import main as cli_main
from .deepseek_chat import chat_once, config_from_env
from .env import load_env
from .paths import DIAEVO_DIR, INSTALL_ROOT, WORKSPACE_ROOT
from .tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args


DEFAULT_APPROVAL_TTL_SECONDS = 300
DEFAULT_MAX_MESSAGE_CHARS = 1800
DEFAULT_NAPCAT_STARTUP_WAIT_SECONDS = 25.0
DEFAULT_NAPCAT_DOWNLOAD_URL = (
    "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.Windows.OneKey.zip"
)
FORBIDDEN_REMOTE_COMMANDS = {"key", "vision-key", "vision_key", "visionkey"}


@dataclass(frozen=True, slots=True)
class QQBridgeConfig:
    enabled: bool
    allowed_users: set[str]
    onebot_ws_url: str
    onebot_http_url: str
    access_token: str = ""
    approval_ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS
    event_log_path: Path = DIAEVO_DIR / "qq_remote_events.jsonl"
    napcat_autostart: bool = False
    napcat_command: str = ""
    napcat_startup_wait_seconds: float = DEFAULT_NAPCAT_STARTUP_WAIT_SECONDS
    napcat_auto_install: bool = False
    napcat_download_url: str = DEFAULT_NAPCAT_DOWNLOAD_URL
    napcat_install_dir: Path = INSTALL_ROOT / ".tmp" / "napcat"


@dataclass(frozen=True, slots=True)
class RemoteMessage:
    user_id: str
    text: str
    message_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingApproval:
    code: str
    user_id: str
    tool_name: str
    args: dict[str, Any]
    created_at: float
    expires_at: float
    preview_event_id: str = ""
    used: bool = False


class QQBridgeError(RuntimeError):
    pass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _split_csv(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").replace(";", ",").split(",") if item.strip()}


def _napcat_install_dir_from_env(value: str | None) -> Path:
    raw = (value or "").strip()
    if not raw:
        return INSTALL_ROOT / ".tmp" / "napcat"
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return INSTALL_ROOT / path


def config_from_env_vars(env_path: str | Path | None = None) -> QQBridgeConfig:
    load_env(env_path)
    autostart_value = os.environ.get("DIAEVO_QQ_NAPCAT_AUTOSTART")
    auto_install_value = os.environ.get("DIAEVO_QQ_NAPCAT_AUTO_INSTALL")
    return QQBridgeConfig(
        enabled=_truthy(os.environ.get("DIAEVO_QQ_ENABLED")),
        allowed_users=_split_csv(os.environ.get("DIAEVO_QQ_ALLOWED_USERS")),
        onebot_ws_url=os.environ.get("DIAEVO_QQ_ONEBOT_WS_URL", "").strip(),
        onebot_http_url=os.environ.get("DIAEVO_QQ_ONEBOT_HTTP_URL", "").strip().rstrip("/"),
        access_token=os.environ.get("DIAEVO_QQ_ACCESS_TOKEN", "").strip(),
        approval_ttl_seconds=int(os.environ.get("DIAEVO_QQ_APPROVAL_TTL_SECONDS", DEFAULT_APPROVAL_TTL_SECONDS)),
        max_message_chars=int(os.environ.get("DIAEVO_QQ_MAX_MESSAGE_CHARS", DEFAULT_MAX_MESSAGE_CHARS)),
        napcat_autostart=True if autostart_value is None else _truthy(autostart_value),
        napcat_command=(
            os.environ.get("DIAEVO_QQ_NAPCAT_COMMAND", "").strip()
            or os.environ.get("DIAEVO_QQ_AUTOSTART_COMMAND", "").strip()
        ),
        napcat_startup_wait_seconds=float(
            os.environ.get("DIAEVO_QQ_NAPCAT_STARTUP_WAIT_SECONDS", DEFAULT_NAPCAT_STARTUP_WAIT_SECONDS)
        ),
        napcat_auto_install=True if auto_install_value is None else _truthy(auto_install_value),
        napcat_download_url=os.environ.get("DIAEVO_QQ_NAPCAT_DOWNLOAD_URL", DEFAULT_NAPCAT_DOWNLOAD_URL).strip()
        or DEFAULT_NAPCAT_DOWNLOAD_URL,
        napcat_install_dir=_napcat_install_dir_from_env(os.environ.get("DIAEVO_QQ_NAPCAT_INSTALL_DIR")),
    )


def validate_config(config: QQBridgeConfig) -> None:
    if not config.enabled:
        raise QQBridgeError("DIAEVO_QQ_ENABLED is not enabled")
    if not config.allowed_users:
        raise QQBridgeError("DIAEVO_QQ_ALLOWED_USERS must include at least one QQ number")
    if not config.onebot_ws_url:
        raise QQBridgeError("DIAEVO_QQ_ONEBOT_WS_URL is required")
    if not config.onebot_http_url:
        raise QQBridgeError("DIAEVO_QQ_ONEBOT_HTTP_URL is required")
    if config.approval_ttl_seconds <= 0:
        raise QQBridgeError("DIAEVO_QQ_APPROVAL_TTL_SECONDS must be positive")
    if config.napcat_startup_wait_seconds < 0:
        raise QQBridgeError("DIAEVO_QQ_NAPCAT_STARTUP_WAIT_SECONDS must be non-negative")


def onebot_service_available(config: QQBridgeConfig, *, timeout: float = 0.5) -> bool:
    return _endpoint_available(config.onebot_ws_url, timeout=timeout) and _endpoint_available(
        config.onebot_http_url,
        timeout=timeout,
    )


def prepare_onebot_service(config: QQBridgeConfig) -> dict[str, Any]:
    if not config.enabled:
        return {"status": "disabled"}
    validate_config(config)
    if onebot_service_available(config):
        return {"status": "already_running"}
    if not config.napcat_autostart:
        return {
            "status": "not_running",
            "message": "OneBot 服务未监听；如需自动拉起 NapCat，请设置 DIAEVO_QQ_NAPCAT_AUTOSTART=true。",
        }
    napcat_command = config.napcat_command or discover_napcat_command()
    if not napcat_command:
        if config.napcat_auto_install:
            try:
                napcat_command = install_managed_napcat(config)
            except QQBridgeError as exc:
                return {
                    "status": "missing_command",
                    "message": (
                        "已启用 NapCat 自动启动，但自动安装失败。"
                        f"{exc} 可手动安装 NapCat，或设置 DIAEVO_QQ_NAPCAT_COMMAND。"
                    ),
                }
        if not napcat_command:
            return {
                "status": "missing_command",
                "message": (
                    "已启用 NapCat 自动启动，但没有在 PATH、npm 全局目录、DiaEvo 安装目录 .tmp\\napcat、workspace .tmp\\napcat "
                    "或常见安装目录找到 NapCat。可设置 DIAEVO_QQ_NAPCAT_AUTO_INSTALL=true 自动安装，"
                    "或设置 DIAEVO_QQ_NAPCAT_COMMAND / DIAEVO_QQ_NAPCAT_INSTALL_DIR。"
                ),
            }

    process = _start_napcat_process(napcat_command)
    deadline = time.monotonic() + config.napcat_startup_wait_seconds
    while time.monotonic() <= deadline:
        if onebot_service_available(config):
            return {
                "status": "started",
                "pid": process.pid,
                "command": napcat_command,
                "message": "NapCat 已启动，OneBot 服务已可连接。",
            }
        if process.poll() is not None:
            return {
                "status": "exited",
                "pid": process.pid,
                "returncode": process.returncode,
                "command": napcat_command,
                "message": "NapCat 启动命令已退出，但 OneBot 服务仍不可连接。",
            }
        time.sleep(0.5)
    return {
        "status": "timeout",
        "pid": process.pid,
        "command": napcat_command,
        "message": "已启动 NapCat，但等待 OneBot 服务可连接超时；可能仍在等待扫码登录。",
    }


def install_managed_napcat(config: QQBridgeConfig) -> str:
    if not sys.platform.startswith("win"):
        raise QQBridgeError("NapCat 自动安装当前只支持 Windows。")
    if not config.napcat_download_url:
        raise QQBridgeError("DIAEVO_QQ_NAPCAT_DOWNLOAD_URL 为空。")

    install_dir = config.napcat_install_dir
    existing = _discover_napcat_command_from_roots([install_dir])
    if existing:
        return existing

    download_dir = install_dir.parent / f".napcat-download-{uuid.uuid4().hex}"
    archive_path = download_dir / "NapCat.Shell.Windows.OneKey.zip"
    try:
        download_dir.mkdir(parents=True, exist_ok=False)
        _download_url(config.napcat_download_url, archive_path)
        install_dir.mkdir(parents=True, exist_ok=True)
        _extract_zip_safely(archive_path, install_dir)
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise QQBridgeError(f"下载或解压 NapCat 失败：{exc}") from exc
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)

    installed = _discover_napcat_command_from_roots([install_dir])
    if not installed:
        raise QQBridgeError(f"NapCat 已下载到 {install_dir}，但未找到可启动文件。")
    return installed


def discover_napcat_command() -> str:
    for command_name in ("NapCatQQ", "NapCatQQ.exe", "napcat", "napcat.cmd", "napcat.bat"):
        found = shutil.which(command_name)
        if found:
            return _shell_command_for_path(Path(found))

    npm_bin = _npm_global_bin()
    for path in _candidate_napcat_paths(npm_bin=npm_bin):
        if path.exists() and path.is_file():
            return _shell_command_for_path(path)
    for path in _recursive_napcat_candidates():
        if path.exists() and path.is_file():
            return _shell_command_for_path(path)
    return ""


def _npm_global_bin() -> Path | None:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    target = Path(appdata) / "npm"
    return target if target.exists() else None


def _candidate_napcat_paths(*, npm_bin: Path | None = None) -> list[Path]:
    roots = [WORKSPACE_ROOT, INSTALL_ROOT]
    for env_name in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(env_name, "").strip()
        if value:
            roots.append(Path(value))
    if npm_bin is not None:
        roots.insert(0, npm_bin)

    direct_names = (
        "NapCatQQ.exe",
        "NapCat.exe",
        "napcat.exe",
        "napcat.cmd",
        "napcat.bat",
    )
    napcat_dir_names = (*direct_names, "start.bat", "start.cmd")
    subdirs = (
        "",
        "NapCat",
        "NapCatQQ",
        "NapCat.Shell",
        "LiteLoaderQQNT/NapCat",
        "node_modules/napcat",
        "node_modules/@napcat/napcat",
    )
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for subdir in subdirs:
            base = root / subdir if subdir else root
            names = napcat_dir_names if subdir else direct_names
            for name in names:
                path = base / name
                key = str(path).lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append(path)
    return candidates


def _recursive_napcat_candidates() -> list[Path]:
    roots = [
        INSTALL_ROOT / ".tmp" / "napcat",
        WORKSPACE_ROOT / ".tmp" / "napcat",
        WORKSPACE_ROOT / "NapCat",
        WORKSPACE_ROOT / "NapCatQQ",
        INSTALL_ROOT / "NapCat",
        INSTALL_ROOT / "NapCatQQ",
    ]
    for env_name in ("LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(env_name, "").strip()
        if value:
            roots.extend([Path(value) / "NapCat", Path(value) / "NapCatQQ"])

    return _napcat_candidates_under(roots)


def _discover_napcat_command_from_roots(roots: list[Path]) -> str:
    for path in _napcat_candidates_under(roots):
        if path.exists() and path.is_file():
            return _shell_command_for_path(path)
    return ""


def _napcat_candidates_under(roots: list[Path]) -> list[Path]:
    preferred_names = (
        "napcat.bat",
        "NapCatWinBootMain.exe",
        "launcher-user.bat",
        "launcher-win10-user.bat",
        "launcher.bat",
        "napcat.cmd",
        "NapCatQQ.exe",
        "NapCat.exe",
    )
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for name in preferred_names:
            for path in root.rglob(name):
                key = str(path).lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append(path)
    return sorted(candidates, key=_napcat_candidate_rank)


def _download_url(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "diaevo-napcat-bootstrap"})
    with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _extract_zip_safely(archive_path: Path, target_dir: Path) -> None:
    target_root = target_dir.resolve(strict=False)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve(strict=False)
            if target_root != destination and target_root not in destination.parents:
                raise QQBridgeError(f"NapCat 压缩包包含非法路径：{member.filename}")
        archive.extractall(target_dir)


def _napcat_candidate_rank(path: Path) -> tuple[int, int, str]:
    name = path.name.lower()
    text = str(path).lower()
    if name == "napcat.bat" and "bootmain" in text:
        priority = 0
    elif name == "napcat.bat":
        priority = 1
    elif name == "napcatwinbootmain.exe":
        priority = 2
    elif name.startswith("launcher") and name.endswith(".bat"):
        priority = 3
    else:
        priority = 4
    return (priority, len(path.parts), text)


def _shell_command_for_path(path: Path) -> str:
    text = str(path)
    if sys.platform.startswith("win"):
        return f'start "" /D "{path.parent}" "{text}"'
    return f'"{text}"'


def _start_napcat_process(command: str) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "shell": True,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _endpoint_available(url: str, *, timeout: float = 0.5) -> bool:
    host_port = _endpoint_host_port(url)
    if host_port is None:
        return False
    host, port = host_port
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _endpoint_host_port(url: str) -> tuple[str, int] | None:
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme in {"https", "wss"} else 80
    return parsed.hostname, port


def parse_onebot_private_message(event: dict[str, Any]) -> RemoteMessage | None:
    if event.get("post_type") != "message":
        return None
    if event.get("message_type") != "private":
        return None
    user_id = str(event.get("user_id") or "").strip()
    if not user_id:
        return None
    text = _message_to_text(event.get("message"))
    if not text:
        text = str(event.get("raw_message") or "").strip()
    if not text:
        return None
    return RemoteMessage(user_id=user_id, text=text, message_id=str(event.get("message_id") or ""), raw=event)


def _message_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                data = item.get("data")
                if isinstance(data, dict):
                    parts.append(str(data.get("text") or ""))
        return "".join(parts).strip()
    return ""


def is_allowed_message(message: RemoteMessage, config: QQBridgeConfig) -> bool:
    return message.user_id in config.allowed_users


def redact_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("key", "token", "secret", "password")):
                output[str(key)] = "***"
            else:
                output[str(key)] = redact_for_log(item)
        return output
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    return value


def sanitize_remote_text_for_log(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return stripped
    command = stripped.split(maxsplit=1)[0].removeprefix("/").lower()
    if command in FORBIDDEN_REMOTE_COMMANDS or any(token in command for token in ("key", "token", "secret")):
        return f"/{command} ***"
    return stripped


def append_remote_event(action: str, payload: dict[str, Any], *, path: Path | None = None) -> None:
    target = path or (DIAEVO_DIR / "qq_remote_events.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "id": uuid.uuid4().hex,
        "action": action,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **redact_for_log(payload),
    }
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


class QQRemoteSession:
    def __init__(
        self,
        config: QQBridgeConfig,
        *,
        send_message: Callable[[str, str], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.send_message = send_message or OneBotClient(config).send_private_msg
        self.now = now or time.time
        self.pending: dict[str, PendingApproval] = {}
        self.busy = False

    def handle_message(self, message: RemoteMessage) -> None:
        if not is_allowed_message(message, self.config):
            append_remote_event(
                "ignored_unauthorized",
                {"user_id": message.user_id, "message_id": message.message_id},
                path=self.config.event_log_path,
            )
            return
        text = message.text.strip()
        append_remote_event(
            "received",
            {"user_id": message.user_id, "message_id": message.message_id, "text": sanitize_remote_text_for_log(text)},
            path=self.config.event_log_path,
        )
        if not text:
            return
        if text.startswith("/approve"):
            self._handle_approve(message.user_id, text)
            return
        if text.startswith("/deny"):
            self._handle_deny(message.user_id, text)
            return
        if text in {"/status", "status"}:
            self._send_status(message.user_id)
            return
        if text in {"/cancel", "cancel"}:
            self.pending.clear()
            self.busy = False
            self._send(message.user_id, "已取消当前远程等待项。")
            return
        if self.busy:
            self._send(message.user_id, "当前已有远程任务运行中，请稍后再试或发送 /status 查看。")
            return
        self.busy = True
        try:
            self._dispatch_text(message.user_id, text)
        finally:
            self.busy = False

    def _dispatch_text(self, user_id: str, text: str) -> None:
        if text.startswith("/tool "):
            self._handle_tool_command(user_id, text)
            return
        if text.startswith("/"):
            self._handle_cli_command(user_id, text)
            return
        self._handle_chat(user_id, text)

    def _handle_tool_command(self, user_id: str, text: str) -> None:
        try:
            parts = _split_shell_like(text)
            if len(parts) < 2:
                raise ValueError("usage: /tool <name> <json|key=value...>")
            tool_name = parts[1]
            raw_args = parts[2:]
            if raw_args and all("=" in item for item in raw_args):
                args = parse_tool_arg_pairs(raw_args)
            else:
                args = parse_tool_args(" ".join(raw_args) if raw_args else "{}")
        except Exception as exc:
            self._send(user_id, f"工具参数解析失败：{exc}")
            return
        result = execute_tool(tool_name, args, event_log_path=DIAEVO_DIR / "tool_events.jsonl")
        if result.get("status") == "requires_approval":
            self._create_pending_approval(user_id, tool_name, args, result)
            return
        self._send(user_id, _format_tool_result(result, self.config.max_message_chars))

    def _handle_cli_command(self, user_id: str, text: str) -> None:
        try:
            parts = _split_shell_like(text)
        except ValueError as exc:
            self._send(user_id, f"命令解析失败：{exc}")
            return
        if not parts:
            return
        command_name = parts[0].removeprefix("/").lower()
        if command_name in FORBIDDEN_REMOTE_COMMANDS:
            self._send(user_id, f"远程 QQ 入口禁用 /{command_name}。请在本机终端设置密钥。")
            append_remote_event(
                "forbidden_command",
                {"user_id": user_id, "command": command_name},
                path=self.config.event_log_path,
            )
            return
        argv = _remote_command_to_cli_argv(command_name, parts[1:])
        if argv is None:
            self._send(user_id, f"未知远程命令：/{command_name}")
            return
        output = _capture_cli(argv)
        self._send(user_id, _truncate(output.strip() or "命令已完成。", self.config.max_message_chars))

    def _handle_chat(self, user_id: str, text: str) -> None:
        try:
            config = config_from_env(max_tokens=2048, no_thinking=True)
            answer, _response = chat_once(
                text,
                "你是 DiaEvo 的 QQ 远程助手。用中文简洁回答；需要本地修改时提示用户使用 /tool 并按确认码审批。",
                config,
            )
        except Exception as exc:
            self._send(user_id, f"模型请求失败：{exc}")
            return
        self._send(user_id, _truncate(answer, self.config.max_message_chars))

    def _create_pending_approval(
        self,
        user_id: str,
        tool_name: str,
        args: dict[str, Any],
        preview: dict[str, Any],
    ) -> None:
        code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
        created_at = self.now()
        pending = PendingApproval(
            code=code,
            user_id=user_id,
            tool_name=tool_name,
            args=dict(args),
            created_at=created_at,
            expires_at=created_at + self.config.approval_ttl_seconds,
            preview_event_id=str(preview.get("event_id") or ""),
        )
        self.pending[code] = pending
        append_remote_event(
            "approval_requested",
            {
                "user_id": user_id,
                "tool": tool_name,
                "preview_event_id": pending.preview_event_id,
                "approval_code_hash": _hash_code(code),
            },
            path=self.config.event_log_path,
        )
        body = _format_tool_result(preview, self.config.max_message_chars - 220)
        self._send(
            user_id,
            (
                f"{body}\n\n"
                f"需要远程确认。{self.config.approval_ttl_seconds} 秒内回复：\n"
                f"/approve {code}\n"
                f"拒绝回复：/deny {code} 原因"
            ),
        )

    def _handle_approve(self, user_id: str, text: str) -> None:
        code = _command_arg(text)
        pending = self.pending.get(code)
        if pending is None or pending.user_id != user_id:
            self._send(user_id, "确认码无效。")
            return
        if pending.used:
            self._send(user_id, "确认码已使用。")
            return
        if self.now() > pending.expires_at:
            self.pending.pop(code, None)
            self._send(user_id, "确认码已过期。")
            return
        pending.used = True
        result = execute_tool(
            pending.tool_name,
            dict(pending.args),
            approve=True,
            event_log_path=DIAEVO_DIR / "tool_events.jsonl",
        )
        self.pending.pop(code, None)
        append_remote_event(
            "approval_accepted",
            {
                "user_id": user_id,
                "tool": pending.tool_name,
                "approval_code_hash": _hash_code(code),
                "result_event_id": result.get("event_id", ""),
            },
            path=self.config.event_log_path,
        )
        self._send(user_id, _format_tool_result(result, self.config.max_message_chars))

    def _handle_deny(self, user_id: str, text: str) -> None:
        parts = text.split(maxsplit=2)
        code = parts[1] if len(parts) >= 2 else ""
        reason = parts[2] if len(parts) >= 3 else ""
        pending = self.pending.pop(code, None)
        if pending is None or pending.user_id != user_id:
            self._send(user_id, "确认码无效。")
            return
        append_remote_event(
            "approval_denied",
            {
                "user_id": user_id,
                "tool": pending.tool_name,
                "approval_code_hash": _hash_code(code),
                "reason": reason,
            },
            path=self.config.event_log_path,
        )
        self._send(user_id, "已拒绝这次工具调用。")

    def _send_status(self, user_id: str) -> None:
        active = [item for item in self.pending.values() if not item.used and self.now() <= item.expires_at]
        if not active:
            self._send(user_id, "状态：空闲；无待确认事项。")
            return
        lines = ["状态：等待远程确认。"]
        for item in active:
            remaining = max(0, int(item.expires_at - self.now()))
            lines.append(f"- {item.tool_name}，剩余 {remaining} 秒，确认码 {item.code}")
        self._send(user_id, "\n".join(lines))

    def _send(self, user_id: str, text: str) -> None:
        message = _truncate(text, self.config.max_message_chars)
        self.send_message(user_id, message)
        append_remote_event(
            "sent",
            {"user_id": user_id, "text": message},
            path=self.config.event_log_path,
        )


class QQInteractiveBridge:
    def __init__(
        self,
        config: QQBridgeConfig,
        *,
        enqueue_text: Callable[[str], None],
        send_message: Callable[[str, str], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.enqueue_text = enqueue_text
        self.send_message = send_message or OneBotClient(config).send_private_msg
        self.now = now or time.time
        self.last_user_id = ""

    def handle_message(self, message: RemoteMessage) -> None:
        if not is_allowed_message(message, self.config):
            append_remote_event(
                "ignored_unauthorized",
                {"user_id": message.user_id, "message_id": message.message_id},
                path=self.config.event_log_path,
            )
            return
        text = message.text.strip()
        append_remote_event(
            "received",
            {"user_id": message.user_id, "message_id": message.message_id, "text": sanitize_remote_text_for_log(text)},
            path=self.config.event_log_path,
        )
        if not text:
            return
        if _is_forbidden_remote_text(text):
            command = text.split(maxsplit=1)[0].removeprefix("/")
            self.send_to_user(message.user_id, f"远程 QQ 入口禁用 /{command}。请在本机终端设置密钥。")
            append_remote_event(
                "forbidden_command",
                {"user_id": message.user_id, "command": command},
                path=self.config.event_log_path,
            )
            return
        self.last_user_id = message.user_id
        self.enqueue_text(text)
        self.send_to_user(message.user_id, "已接收，DiaEvo 会在当前会话中继续处理。")

    def send_to_last_user(self, text: str) -> None:
        if not self.last_user_id:
            return
        self.send_to_user(self.last_user_id, text)

    def send_to_user(self, user_id: str, text: str) -> None:
        message = _truncate(text, self.config.max_message_chars)
        self.send_message(user_id, message)
        append_remote_event(
            "sent",
            {"user_id": user_id, "text": message},
            path=self.config.event_log_path,
        )


class OneBotClient:
    def __init__(self, config: QQBridgeConfig) -> None:
        self.config = config

    def send_private_msg(self, user_id: str, text: str) -> None:
        if not self.config.onebot_http_url:
            raise QQBridgeError("DIAEVO_QQ_ONEBOT_HTTP_URL is required to send messages")
        url = f"{self.config.onebot_http_url}/send_private_msg"
        payload = json.dumps({"user_id": int(user_id), "message": text}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read()
        except urllib.error.URLError as exc:
            raise QQBridgeError(f"OneBot send_private_msg failed: {exc}") from exc


async def run_bridge(config: QQBridgeConfig) -> None:
    validate_config(config)
    session = QQRemoteSession(config)
    await run_onebot_event_loop(config, session.handle_message)


async def run_interactive_bridge(config: QQBridgeConfig, bridge: QQInteractiveBridge) -> None:
    validate_config(config)
    await run_onebot_event_loop(config, bridge.handle_message)


async def run_onebot_event_loop(config: QQBridgeConfig, handler: Callable[[RemoteMessage], None]) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise QQBridgeError("qq-bridge requires optional dependency `websockets`; install diaevo[qq].") from exc

    headers = {}
    if config.access_token:
        headers["Authorization"] = f"Bearer {config.access_token}"

    while True:
        try:
            connect_kwargs = _websocket_header_kwargs(websockets.connect, headers)
            async with websockets.connect(config.onebot_ws_url, **connect_kwargs) as websocket:
                async for raw in websocket:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    message = parse_onebot_private_message(event)
                    if message is not None:
                        handler(message)
        except Exception as exc:
            append_remote_event("bridge_reconnect", {"error": str(exc)}, path=config.event_log_path)
            await asyncio.sleep(3)


def run_bridge_from_env(env_path: str | Path | None = None) -> int:
    config = config_from_env_vars(env_path)
    prepare_onebot_service(config)
    try:
        asyncio.run(run_bridge(config))
    except KeyboardInterrupt:
        return 0
    return 0


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _is_forbidden_remote_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False
    command = stripped.split(maxsplit=1)[0].removeprefix("/").lower()
    return command in FORBIDDEN_REMOTE_COMMANDS


def _websocket_header_kwargs(connect: Callable[..., Any], headers: dict[str, str]) -> dict[str, Any]:
    if not headers:
        return {}
    parameters = inspect.signature(connect).parameters
    if "additional_headers" in parameters:
        return {"additional_headers": headers}
    return {"extra_headers": headers}


def _command_arg(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def _split_shell_like(text: str) -> list[str]:
    import shlex

    return shlex.split(text, posix=False)


def _remote_command_to_cli_argv(command_name: str, rest: list[str]) -> list[str] | None:
    shortcuts: dict[str, Callable[[list[str]], list[str]]] = {
        "learn": lambda args: ["learn", *args],
        "status": lambda args: ["status", *args],
        "ingest": lambda args: ["ingest", "--input", "data/sample_traces.jsonl", *args],
        "mine": lambda args: ["mine", *args],
        "kg": lambda args: ["kg", "--no-open", *args],
        "recommend": lambda args: ["recommend", "--task", " ".join(args) if args else "给当前项目生成测试修复 skill"],
        "generate": lambda args: ["generate", "--cluster-id", args[0], *args[1:]] if args else ["learn"],
        "verify": lambda args: ["verify", "--skill", args[0], *args[1:]] if args else ["status"],
        "feedback": lambda args: ["feedback", *args],
        "tools": lambda args: ["tools", *args],
        "answer-kg": lambda args: ["answer-kg", *args],
        "answer_kg": lambda args: ["answer-kg", *args],
    }
    handler = shortcuts.get(command_name)
    return handler(rest) if handler else None


def _capture_cli(argv: list[str]) -> str:
    import contextlib
    import io

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(argv)
    output = stdout.getvalue().strip()
    errors = stderr.getvalue().strip()
    parts: list[str] = []
    if output:
        parts.append(output)
    if errors:
        parts.append(errors)
    if code:
        parts.append(f"命令退出，状态码：{code}")
    return "\n".join(parts)


def _format_tool_result(result: dict[str, Any], limit: int) -> str:
    text = json.dumps(redact_for_log(result), ensure_ascii=False, indent=2, sort_keys=True)
    return _truncate(text, limit)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 28)].rstrip() + "\n... <已截断>"
