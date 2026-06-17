from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import DIAEVO_DIR
from .storage import read_json, write_json


SESSIONS_DIR = DIAEVO_DIR / "sessions"
SESSION_FILE_SUFFIX = ".json"


@dataclass(frozen=True)
class DiaEvoSession:
    id: str
    title: str
    path: Path
    created_at: str
    updated_at: str
    message_count: int
    preview: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_session_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def session_path(session_id: str) -> Path:
    clean = session_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", clean):
        raise ValueError(f"invalid session id: {session_id!r}")
    return SESSIONS_DIR / f"{clean}{SESSION_FILE_SUFFIX}"


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return " ".join(content.split())
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(" ".join(parts).split())
    if content is None:
        return ""
    return " ".join(str(content).split())


def _derive_title(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        text = _message_text(message)
        if text:
            return text[:80]
    return "Untitled DiaEvo session"


def _derive_preview(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") not in {"user", "assistant"}:
            continue
        text = _message_text(message)
        if text:
            return text[:120]
    return ""


def save_session(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    title: str | None = None,
    path: str | Path | None = None,
) -> DiaEvoSession:
    now = utc_now_iso()
    target_id = session_id or create_session_id()
    target = Path(path) if path is not None else session_path(target_id)
    existing = read_json(target, {}) if target.exists() else {}
    created_at = str(existing.get("created_at") or now) if isinstance(existing, dict) else now
    session_title = title or (str(existing.get("title") or "") if isinstance(existing, dict) else "") or _derive_title(messages)
    payload = {
        "id": target_id,
        "title": session_title,
        "created_at": created_at,
        "updated_at": now,
        "messages": messages,
    }
    write_json(target, payload)
    return _summary_from_payload(payload, target)


def load_session(session_id: str | None = None, *, path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else session_path(session_id or "")
    payload = read_json(target, None)
    if not isinstance(payload, dict):
        raise FileNotFoundError(f"session not found: {target}")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError(f"session file has no messages list: {target}")
    payload = dict(payload)
    payload["messages"] = [item for item in messages if isinstance(item, dict)]
    payload.setdefault("id", target.stem)
    payload.setdefault("title", _derive_title(payload["messages"]))
    payload["_path"] = str(target)
    return payload


def list_sessions(*, limit: int = 20, sessions_dir: str | Path | None = None) -> list[DiaEvoSession]:
    root = Path(sessions_dir) if sessions_dir is not None else SESSIONS_DIR
    if not root.exists():
        return []
    summaries: list[DiaEvoSession] = []
    for path in root.glob(f"*{SESSION_FILE_SUFFIX}"):
        try:
            payload = read_json(path, {})
            if isinstance(payload, dict):
                summaries.append(_summary_from_payload(payload, path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return summaries[: max(0, limit)]


def _summary_from_payload(payload: dict[str, Any], path: Path) -> DiaEvoSession:
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    typed_messages = [item for item in messages if isinstance(item, dict)]
    return DiaEvoSession(
        id=str(payload.get("id") or path.stem),
        title=str(payload.get("title") or _derive_title(typed_messages)),
        path=path,
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        message_count=len(typed_messages),
        preview=_derive_preview(typed_messages),
    )


def render_session_list(sessions: list[DiaEvoSession]) -> str:
    if not sessions:
        return "没有可恢复的 DiaEvo 会话。"
    lines = ["最近 DiaEvo 会话："]
    for index, session in enumerate(sessions, start=1):
        title = session.title or "Untitled DiaEvo session"
        lines.append(f"{index}. {session.id}  {session.updated_at}  {session.message_count} messages  {title}")
        if session.preview and session.preview != title:
            lines.append(f"   {session.preview}")
    lines.append("")
    lines.append("继续会话：diaevo resume <session-id>")
    return "\n".join(lines)


def render_session_transcript(messages: list[dict[str, Any]], *, limit: int = 12) -> str:
    visible = [
        item
        for item in messages
        if isinstance(item, dict) and item.get("role") in {"user", "assistant", "tool", "function"}
    ]
    if not visible:
        return "这个会话还没有可显示的历史对话。"
    shown = visible[-max(1, limit) :]
    omitted = len(visible) - len(shown)
    lines: list[str] = ["历史对话："]
    if omitted > 0:
        lines.append(f"... 已省略更早的 {omitted} 条消息")
    for message in shown:
        role = str(message.get("role") or "message")
        text = _message_text(message)
        if not text:
            text = "[tool call or empty content]"
        if len(text) > 1200:
            text = text[:1200].rstrip() + "... <truncated>"
        lines.append(f"\n{role}>")
        lines.append(text)
    return "\n".join(lines)
