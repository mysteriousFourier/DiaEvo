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


def _clean_text(value: Any) -> str:
    return sanitize_no_emoji(value).replace("\r", "").strip()


def _truncate_text(value: Any, limit: int = 320) -> str:
    text = " ".join(_clean_text(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _result_url(item: dict[str, Any]) -> str:
    return _clean_text(item.get("url") or item.get("abs_url") or item.get("pdf_url") or "")


def _render_web_search(result: dict[str, Any], content_width: int) -> list[str]:
    lines = [_fit(f"query: {_clean_text(result.get('query', ''))}", content_width)]
    source = _clean_text(result.get("source") or "")
    if source:
        lines.append(_fit(f"source: {source}", content_width))
    items = result.get("results") or []
    if not isinstance(items, list) or not items:
        return lines + ["no results"]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title") or f"Result {index}")
        url = _result_url(item)
        snippet = _truncate_text(item.get("snippet") or item.get("summary") or item.get("content") or "", 220)
        lines.append(_fit(f"{index}. {title}", content_width))
        if url:
            lines.append(_fit(f"   {url}", content_width))
        if snippet:
            lines.append(_fit(f"   {snippet}", content_width))
    return lines


def _render_web_fetch(result: dict[str, Any], content_width: int) -> list[str]:
    lines = []
    url = _clean_text(result.get("url") or "")
    if url:
        lines.append(_fit(f"url: {url}", content_width))
    metadata = [
        f"status: {result.get('status_code')}",
        f"type: {_clean_text(result.get('content_type') or '')}",
        f"truncated: {bool(result.get('truncated'))}",
    ]
    lines.append(_fit("  ".join(item for item in metadata if not item.endswith(": ")), content_width))
    content = _truncate_text(result.get("content") or "", 1_200)
    if content:
        lines.append(_fit("content:", content_width))
        for line in _preview_lines(content, limit=8):
            lines.append(_fit(line, content_width))
    return lines


def _render_arxiv_search(result: dict[str, Any], content_width: int) -> list[str]:
    lines = [
        _fit(f"query: {_clean_text(result.get('query') or result.get('search_query') or '')}", content_width),
        _fit(f"source: {_clean_text(result.get('source') or 'arxiv_api')}", content_width),
    ]
    total = result.get("total_results")
    if total is not None:
        lines.append(_fit(f"total_results: {total}", content_width))
    items = result.get("results") or []
    if not isinstance(items, list) or not items:
        return lines + ["no papers"]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title") or f"Paper {index}")
        authors = item.get("authors") if isinstance(item.get("authors"), list) else []
        author_text = ", ".join(str(author) for author in authors[:4])
        if len(authors) > 4:
            author_text += ", et al."
        date = _clean_text(item.get("published") or item.get("updated") or "")
        category = _clean_text(item.get("primary_category") or "")
        lines.append(_fit(f"{index}. {title}", content_width))
        details = "  ".join(part for part in [author_text, date[:10], category] if part)
        if details:
            lines.append(_fit(f"   {details}", content_width))
        abs_url = _clean_text(item.get("abs_url") or "")
        pdf_url = _clean_text(item.get("pdf_url") or "")
        if abs_url:
            lines.append(_fit(f"   abs: {abs_url}", content_width))
        if pdf_url:
            lines.append(_fit(f"   pdf: {pdf_url}", content_width))
        summary = _truncate_text(item.get("summary") or "", 260)
        if summary:
            lines.append(_fit(f"   {summary}", content_width))
    return lines


def _render_tool_body(result: dict[str, Any], status: str, content_width: int) -> list[str]:
    tool = str(result.get("tool", ""))
    if status == "requires_approval":
        message = sanitize_no_emoji(result.get("message") or "approval required")
        lines = [_fit(message, content_width)]
        for line in _preview_lines(result.get("preview", {})):
            lines.append(_fit(line, content_width))
        return lines
    if status == "error":
        return [_fit(sanitize_no_emoji(result.get("error") or "unknown error"), content_width)]
    if tool == "web_search":
        return _render_web_search(result, content_width)
    if tool == "web_fetch":
        return _render_web_fetch(result, content_width)
    if tool == "arxiv_search":
        return _render_arxiv_search(result, content_width)
    return _render_generic_body(result, content_width)


def _render_generic_body(result: dict[str, Any], content_width: int) -> list[str]:
    shown: Any
    if "entries" in result:
        shown = [entry.get("path", "") for entry in result.get("entries", [])]
    elif "content" in result:
        shown = result.get("content", "")
    elif "diff" in result:
        shown = result.get("diff", "")
    elif "stdout" in result or "stderr" in result:
        shown = {
            "returncode": result.get("returncode"),
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
            "note": result.get("note"),
        }
    else:
        shown = {key: value for key, value in result.items() if key not in {"event_log"}}
    return [_fit(line, content_width) for line in _preview_lines(shown)]


def render_tool_result(result: dict[str, Any]) -> str:
    width = min(max(72, _term_width() - 4), 144)
    status = sanitize_no_emoji(result.get("status", "ok"))
    tool = sanitize_no_emoji(result.get("tool", "tool"))
    content_width = width
    lines = [f"{PURPLE}{tool}{RESET} {DIM}{status}{RESET}"]
    lines.extend(_render_tool_body(result, status, content_width))

    if result.get("event_id"):
        footer = f"event {result['event_id']} -> {result.get('event_log', '')}"
        lines.append(DIM + _fit(footer, content_width) + RESET)
    lines.append(f"{BLUE}{GLYPHS['h'] * width}{RESET}")
    return "\n".join(lines)
