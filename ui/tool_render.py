from __future__ import annotations

import json
from typing import Any

from .cli_style import BLUE, DIM, GLYPHS, PURPLE, RESET, _fit, _pad, _term_width
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
    body_width = width - 4
    status = sanitize_no_emoji(result.get("status", "ok"))
    tool = sanitize_no_emoji(result.get("tool", "tool"))
    title = f"{PURPLE}{tool}{RESET} {DIM}{status}{RESET}"
    lines = [f"{BLUE}{GLYPHS['tl']}{GLYPHS['h']} {title}{BLUE}{GLYPHS['h'] * max(0, body_width - len(tool) - len(status) - 2)}{GLYPHS['tr']}{RESET}"]

    if status == "requires_approval":
        message = sanitize_no_emoji(result.get("message") or "approval required")
        lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(message, body_width)} {BLUE}{GLYPHS['v']}{RESET}")
        preview = result.get("preview", {})
        for line in _preview_lines(preview):
            lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(_fit(line, body_width), body_width)} {BLUE}{GLYPHS['v']}{RESET}")
    elif status == "error":
        error = sanitize_no_emoji(result.get("error") or "unknown error")
        lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(_fit(error, body_width), body_width)} {BLUE}{GLYPHS['v']}{RESET}")
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
            lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(_fit(line, body_width), body_width)} {BLUE}{GLYPHS['v']}{RESET}")

    if result.get("event_id"):
        footer = f"event {result['event_id']} -> {result.get('event_log', '')}"
        lines.append(f"{BLUE}{GLYPHS['v']}{RESET} {_pad(DIM + _fit(footer, body_width) + RESET, body_width)} {BLUE}{GLYPHS['v']}{RESET}")
    lines.append(f"{BLUE}{GLYPHS['bl']}{GLYPHS['h'] * (width - 2)}{GLYPHS['br']}{RESET}")
    return "\n".join(lines)
