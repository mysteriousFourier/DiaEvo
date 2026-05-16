from __future__ import annotations

import json
from typing import Any

from .cli_style import BLUE, DIM, GLYPHS, PURPLE, RESET, _fit, _term_width
from .output_policy import sanitize_no_emoji


def _preview_lines(value: Any, *, limit: int = 18) -> list[str]:
    if isinstance(value, str):
        lines = sanitize_no_emoji(value).splitlines() or [""]
    else:
        lines = sanitize_no_emoji(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)).splitlines()
    if len(lines) > limit:
        return lines[:limit] + [f"... {len(lines) - limit} more lines"]
    return lines


def render_tool_result(result: dict[str, Any]) -> str:
    width = min(max(72, _term_width() - 4), 144)
    status = sanitize_no_emoji(result.get("status", "ok"))
    tool = sanitize_no_emoji(result.get("tool", "tool"))
    content_width = width
    lines = [f"{PURPLE}{tool}{RESET} {DIM}{status}{RESET}"]

    if status == "requires_approval":
        message = sanitize_no_emoji(result.get("message") or "approval required")
        lines.append(_fit(message, content_width))
        preview = result.get("preview", {})
        for line in _preview_lines(preview):
            lines.append(_fit(line, content_width))
    elif status == "error":
        error = sanitize_no_emoji(result.get("error") or "unknown error")
        lines.append(_fit(error, content_width))
    else:
        shown: Any
        if "entries" in result:
            shown = [entry.get("path", "") for entry in result.get("entries", [])]
        elif "content" in result:
            shown = result.get("content", "")
        elif "diff" in result:
            shown = result.get("diff", "")
        elif "stdout" in result or "stderr" in result:
            shown = {"returncode": result.get("returncode"), "stdout": result.get("stdout"), "stderr": result.get("stderr")}
        else:
            shown = {key: value for key, value in result.items() if key not in {"event_log"}}
        for line in _preview_lines(shown):
            lines.append(_fit(line, content_width))

    if result.get("event_id"):
        footer = f"event {result['event_id']} -> {result.get('event_log', '')}"
        lines.append(DIM + _fit(footer, content_width) + RESET)
    lines.append(f"{BLUE}{GLYPHS['h'] * width}{RESET}")
    return "\n".join(lines)
