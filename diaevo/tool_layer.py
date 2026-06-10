from __future__ import annotations

import difflib
import fnmatch
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlencode, unquote, urlparse
from urllib.request import Request, urlopen

from .knowledge_graph import answer_kg
from .paths import DIAEVO_DIR, WORKSPACE_ROOT
from .skill_context import load_skill_context as load_skill_context_data
from .skill_context import recommend_skill_contexts

DEFAULT_EVENT_LOG = DIAEVO_DIR / "tool_events.jsonl"
MAX_TEXT_BYTES = 240_000
MAX_LOG_STRING = 2_000
MAX_WEB_EXCERPT_CHARS = 4_000
REPEAT_FAILURE_HINT = (
    "同一 run_shell 命令连续失败；请先查看 stderr/stdout，调整命令或验证思路，"
    "不要机械重复执行。"
)
_LAST_FAILED_SHELL_COMMAND = ""


class ToolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    approval_required: bool
    destructive: bool
    handler: Callable[[dict[str, Any], bool], dict[str, Any]]
    risk: str = "low"

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "read_only": self.read_only,
            "approval_required": self.approval_required,
            "destructive": self.destructive,
            "risk": self.risk,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_workspace_path(value: str | os.PathLike[str] | None, *, must_exist: bool = False) -> Path:
    raw_text = str(value or ".").strip() or "."
    raw = Path(raw_text)
    if not raw.is_absolute():
        raw = WORKSPACE_ROOT / raw
    resolved = raw.resolve(strict=False)
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ToolError(f"path is outside workspace: {raw_text}") from exc
    if must_exist and not resolved.exists():
        raise ToolError(f"path does not exist: {workspace_relative(resolved)}")
    return resolved


def workspace_relative(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(WORKSPACE_ROOT).as_posix()
    except ValueError:
        return str(path)


def parse_tool_args(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError(f"tool args must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolError("tool args must be a JSON object")
    return parsed


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_tool_arg_pairs(values: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise ToolError(f"tool arg must use key=value form: {value}")
        key, raw = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ToolError("tool arg key cannot be empty")
        result[key] = _parse_scalar(raw)
    return result


def _truncate(value: str, limit: int = MAX_LOG_STRING) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"... <truncated {len(value) - limit} chars>"


def _safe_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith("__"):
                continue
            lowered = key_text.lower()
            if any(token in lowered for token in ("key", "token", "secret", "password")):
                result[key_text] = "***"
            else:
                result[key_text] = _safe_for_log(item)
        return result
    if isinstance(value, list):
        return [_safe_for_log(item) for item in value[:50]]
    if isinstance(value, str):
        return _truncate(value)
    return value


def _append_event(event: dict[str, Any], event_log_path: str | Path | None = None) -> None:
    target = Path(event_log_path) if event_log_path else DEFAULT_EVENT_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _approval_result(spec: ToolSpec, args: dict[str, Any], preview: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "status": "requires_approval",
        "tool": spec.name,
        "message": message,
        "approval_required": True,
        "approved": False,
        "read_only": spec.read_only,
        "destructive": spec.destructive,
        "preview": preview,
    }


def _read_text(path: Path, *, max_bytes: int = MAX_TEXT_BYTES) -> tuple[str, bool]:
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace"), truncated


def _diff_text(path: Path, before: str, after: str) -> str:
    fromfile = workspace_relative(path)
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{fromfile}",
            tofile=f"b/{fromfile}",
            lineterm="",
        )
    )
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _is_hidden(rel_path: str) -> bool:
    return any(part.startswith(".") for part in PurePosixPath(rel_path).parts)


def _list_files(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    root = resolve_workspace_path(args.get("path", "."), must_exist=True)
    if not root.is_dir():
        raise ToolError(f"path is not a directory: {workspace_relative(root)}")
    recursive = bool(args.get("recursive", False))
    include_hidden = bool(args.get("include_hidden", False))
    pattern = str(args.get("pattern") or "*")
    max_results = max(1, min(int(args.get("max_results") or 200), 2_000))
    iterator = root.rglob("*") if recursive else root.iterdir()
    entries: list[dict[str, Any]] = []
    for child in iterator:
        rel = workspace_relative(child)
        if not include_hidden and _is_hidden(rel):
            continue
        if pattern and pattern != "*":
            posix_rel = rel.replace("\\", "/")
            if not fnmatch.fnmatch(child.name, pattern) and not PurePosixPath(posix_rel).match(pattern):
                continue
        stat = child.stat()
        entries.append(
            {
                "path": rel,
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
            }
        )
        if len(entries) >= max_results:
            break
    return {
        "status": "ok",
        "tool": "list_files",
        "path": workspace_relative(root),
        "recursive": recursive,
        "count": len(entries),
        "truncated": len(entries) >= max_results,
        "entries": entries,
    }


def _read_file(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    target = resolve_workspace_path(_required_text(args, "path", "read_file"), must_exist=True)
    if not target.is_file():
        raise ToolError(f"path is not a file: {workspace_relative(target)}")
    offset = max(0, int(args.get("offset") or 0))
    limit = args.get("limit", 400)
    limit = None if limit is None else max(1, min(int(limit), 5_000))
    text, byte_truncated = _read_text(target)
    lines = text.splitlines()
    selected = lines[offset:] if limit is None else lines[offset : offset + limit]
    return {
        "status": "ok",
        "tool": "read_file",
        "path": workspace_relative(target),
        "offset": offset,
        "line_count": len(lines),
        "truncated": byte_truncated or (limit is not None and offset + limit < len(lines)),
        "content": "\n".join(selected),
    }


def _write_file(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    _required_text(args, "path", "write_file")
    if "content" not in args:
        raise ToolError("write_file requires content")
    if args.get("content") is None or not str(args.get("content")).strip():
        raise ToolError("write_file requires content")
    target = resolve_workspace_path(args.get("path"))
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    after = str(args.get("content"))
    diff = _diff_text(target, before, after)
    preview = {
        "path": workspace_relative(target),
        "operation": "update" if target.exists() else "create",
        "diff": diff,
    }
    if not approved:
        return _approval_result(TOOLS["write_file"], args, preview, "write_file needs approval before changing the workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(after, encoding="utf-8", newline="\n")
    return {
        "status": "ok",
        "tool": "write_file",
        "path": workspace_relative(target),
        "operation": preview["operation"],
        "diff": diff,
    }


def _edit_file(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    target = resolve_workspace_path(_required_text(args, "path", "edit_file"), must_exist=True)
    if not target.is_file():
        raise ToolError(f"path is not a file: {workspace_relative(target)}")
    old_string = str(args.get("old_string", ""))
    new_string = str(args.get("new_string", ""))
    replace_all = bool(args.get("replace_all", False))
    if old_string == new_string:
        raise ToolError("old_string and new_string are identical")
    before = target.read_text(encoding="utf-8")
    matches = before.count(old_string)
    if matches == 0:
        raise ToolError("old_string was not found")
    if matches > 1 and not replace_all:
        raise ToolError(f"old_string matched {matches} times; set replace_all=true or provide more context")
    after = before.replace(old_string, new_string) if replace_all else before.replace(old_string, new_string, 1)
    diff = _diff_text(target, before, after)
    preview = {"path": workspace_relative(target), "matches": matches, "replace_all": replace_all, "diff": diff}
    if not approved:
        return _approval_result(TOOLS["edit_file"], args, preview, "edit_file needs approval before changing the workspace")
    target.write_text(after, encoding="utf-8", newline="\n")
    return {
        "status": "ok",
        "tool": "edit_file",
        "path": workspace_relative(target),
        "matches": matches,
        "replace_all": replace_all,
        "diff": diff,
    }


def _delete_file(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    target = resolve_workspace_path(_required_text(args, "path", "delete_file"), must_exist=True)
    recursive = bool(args.get("recursive", False))
    if target.is_dir() and not recursive:
        raise ToolError("delete_file requires recursive=true for directories")
    preview = {
        "path": workspace_relative(target),
        "type": "directory" if target.is_dir() else "file",
        "recursive": recursive,
    }
    if not approved:
        return _approval_result(TOOLS["delete_file"], args, preview, "delete_file is destructive and needs approval")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"status": "ok", "tool": "delete_file", **preview}


def _patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith("a/") or raw.startswith("b/"):
                raw = raw[2:]
            paths.append(raw)
    return sorted(set(paths))


def _apply_patch(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    patch_text = str(args.get("patch") or "")
    if not patch_text.strip():
        raise ToolError("apply_patch requires a non-empty patch")
    touched = _patch_paths(patch_text)
    if not touched:
        raise ToolError("patch does not contain file headers")
    for rel_path in touched:
        resolve_workspace_path(rel_path)
    preview = {"paths": touched, "patch": patch_text}
    if not approved:
        return _approval_result(TOOLS["apply_patch"], args, preview, "apply_patch needs approval before changing files")
    check = subprocess.run(
        ["git", "apply", "--check", "-"],
        input=patch_text,
        text=True,
        capture_output=True,
        cwd=WORKSPACE_ROOT,
        timeout=30,
    )
    if check.returncode != 0:
        raise ToolError(f"git apply --check failed: {check.stderr.strip() or check.stdout.strip()}")
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        input=patch_text,
        text=True,
        capture_output=True,
        cwd=WORKSPACE_ROOT,
        timeout=30,
    )
    if applied.returncode != 0:
        raise ToolError(f"git apply failed: {applied.stderr.strip() or applied.stdout.strip()}")
    return {"status": "ok", "tool": "apply_patch", "paths": touched, "patch": patch_text}


def _run_shell(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ToolError("run_shell requires command")
    timeout = max(1, min(int(args.get("timeout") or 30), 300))
    preview = {"command": command, "timeout": timeout, "cwd": str(WORKSPACE_ROOT)}
    if not approved:
        return _approval_result(TOOLS["run_shell"], args, preview, "run_shell needs approval before executing a local command")
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-Command", command]
        shell = False
    else:
        cmd = command
        shell = True
    cancel_event = args.get("__cancel_event__")
    if not isinstance(cancel_event, threading.Event):
        cancel_event = None
    started = time.monotonic()
    process = subprocess.Popen(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=WORKSPACE_ROOT,
        shell=shell,
    )
    while process.poll() is None:
        if cancel_event and cancel_event.is_set():
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            result = {
                "status": "interrupted",
                "tool": "run_shell",
                "command": command,
                "returncode": process.returncode,
                "stdout": _truncate(stdout, 20_000),
                "stderr": _truncate(stderr, 20_000),
            }
            return _annotate_shell_repeat_failure(result)
        if time.monotonic() - started >= timeout:
            process.kill()
            stdout, stderr = process.communicate()
            result = {
                "status": "timeout",
                "tool": "run_shell",
                "command": command,
                "returncode": process.returncode,
                "stdout": _truncate(stdout, 20_000),
                "stderr": _truncate(stderr, 20_000),
            }
            return _annotate_shell_repeat_failure(result)
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    result = {
        "status": "ok" if process.returncode == 0 else "error",
        "tool": "run_shell",
        "command": command,
        "returncode": process.returncode,
        "stdout": _truncate(stdout, 20_000),
        "stderr": _truncate(stderr, 20_000),
    }
    return _annotate_shell_repeat_failure(result)


def _annotate_shell_repeat_failure(result: dict[str, Any]) -> dict[str, Any]:
    global _LAST_FAILED_SHELL_COMMAND
    command = str(result.get("command") or "").strip()
    if result.get("status") in {"error", "timeout", "interrupted"}:
        if command and command == _LAST_FAILED_SHELL_COMMAND:
            result["note"] = REPEAT_FAILURE_HINT
        _LAST_FAILED_SHELL_COMMAND = command
    elif result.get("status") == "ok":
        _LAST_FAILED_SHELL_COMMAND = ""
    return result


def _fetch_url(url: str, max_bytes: int) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError("web tools only support http and https URLs")
    request = Request(url, headers={"User-Agent": "diaevo/0.1"})
    try:
        with urlopen(request, timeout=20) as response:
            data = response.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            if truncated:
                data = data[:max_bytes]
            content_type = response.headers.get("content-type", "")
            text = data.decode("utf-8", errors="replace")
            return {
                "url": response.geturl(),
                "status_code": response.status,
                "content_type": content_type,
                "truncated": truncated,
                "content": text,
            }
    except HTTPError as exc:
        raise ToolError(f"HTTP {exc.code} fetching {url}") from exc
    except URLError as exc:
        raise ToolError(f"network error fetching {url}: {exc.reason}") from exc


def _http_error_message(exc: HTTPError, url: str, *, body_limit: int = 600) -> str:
    retry_after = exc.headers.get("Retry-After", "") if exc.headers is not None else ""
    try:
        body = exc.read(body_limit + 1).decode("utf-8", errors="replace")
    except Exception:
        body = ""
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > body_limit:
        body = body[:body_limit].rstrip() + "..."
    pieces = [f"HTTP {exc.code} fetching {url}"]
    if retry_after:
        pieces.append(f"Retry-After={retry_after}")
    if body:
        pieces.append(f"body={body}")
    return "; ".join(pieces)


def _fetch_arxiv_api_url(url: str, max_bytes: int) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError("arxiv_search only supports http and https URLs")
    request = Request(url, headers={"User-Agent": "diaevo/0.1 (+https://github.com/local/diaevo; arxiv_search)"})
    try:
        with urlopen(request, timeout=20) as response:
            data = response.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            if truncated:
                data = data[:max_bytes]
            return {
                "url": response.geturl(),
                "status_code": response.status,
                "content_type": response.headers.get("content-type", ""),
                "truncated": truncated,
                "content": data.decode("utf-8", errors="replace"),
            }
    except HTTPError as exc:
        raise ToolError(_http_error_message(exc, url)) from exc
    except URLError as exc:
        raise ToolError(f"network error fetching {url}: {exc.reason}") from exc


def _extract_web_text(content: str, *, limit: int = MAX_WEB_EXCERPT_CHARS) -> tuple[str, bool]:
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", content)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip()
    return text, truncated


def _best_web_excerpt(text: str, *, limit: int = 1_200) -> tuple[str, bool]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    noise_patterns = (
        "skip to main content",
        "we gratefully acknowledge",
        "search | arxiv",
        "cookie",
        "privacy policy",
    )
    kept = [
        line
        for line in lines
        if not any(pattern in line.lower() for pattern in noise_patterns)
    ]
    excerpt = " ".join(kept or lines)
    excerpt = re.sub(r"\s+", " ", excerpt).strip()
    truncated = len(excerpt) > limit
    if truncated:
        excerpt = excerpt[:limit].rstrip()
    return excerpt, truncated


def _coerce_domains(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[\s,]+", value)
    elif isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    domains: list[str] = []
    for item in raw_items:
        domain = item.strip().removeprefix("http://").removeprefix("https://").strip("/")
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _query_with_domains(query: str, domains: list[str]) -> str:
    if not domains:
        return query
    domain_filter = " OR ".join(f"site:{domain}" for domain in domains)
    return f"({query}) ({domain_filter})"


def _web_fetch(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("web_fetch requires url")
    max_bytes = max(1_000, min(int(args.get("max_bytes") or 80_000), 500_000))
    preview = {"url": url, "max_bytes": max_bytes}
    if not approved:
        return _approval_result(TOOLS["web_fetch"], args, preview, "web_fetch needs approval because it uses the network")
    fetched = _fetch_url(url, max_bytes)
    content, content_truncated = _extract_web_text(fetched.get("content", ""))
    excerpt, excerpt_truncated = _best_web_excerpt(content)
    return {
        "status": "ok",
        "tool": "web_fetch",
        "url": url,
        "final_url": fetched.get("url", url),
        "status_code": fetched.get("status_code"),
        "content_type": fetched.get("content_type", ""),
        "truncated": bool(fetched.get("truncated")) or content_truncated or excerpt_truncated,
        "content": excerpt,
    }


RESULT_LINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    flags=re.IGNORECASE | re.DOTALL,
)
RESULT_BLOCK_RE = re.compile(
    r'<div[^>]+class="[^"]*\bresult\b[^"]*"[^>]*>(?P<body>.*?)</div>\s*</div>',
    flags=re.IGNORECASE | re.DOTALL,
)
RESULT_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>|'
    r'<div[^>]+class="result__snippet"[^>]*>(?P<snippet_div>.*?)</div>',
    flags=re.IGNORECASE | re.DOTALL,
)
BING_RESULT_RE = re.compile(
    r'<li[^>]+class="[^"]*\bb_algo\b[^"]*"[^>]*>(?P<body>.*?)</li>',
    flags=re.IGNORECASE | re.DOTALL,
)
BING_LINK_RE = re.compile(
    r'<h2[^>]*>\s*<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    flags=re.IGNORECASE | re.DOTALL,
)
BING_SNIPPET_RE = re.compile(
    r'<div[^>]+class="[^"]*\bb_caption\b[^"]*"[^>]*>.*?<p[^>]*>(?P<snippet>.*?)</p>',
    flags=re.IGNORECASE | re.DOTALL,
)


def _clean_html_fragment(value: str) -> str:
    text = re.sub(r"<.*?>", " ", value, flags=re.DOTALL)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _normalize_duckduckgo_url(value: str) -> str:
    url = html.unescape(value)
    parsed = urlparse(url)
    if parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return url


def _parse_duckduckgo_results(page_content: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = [match.group("body") for match in RESULT_BLOCK_RE.finditer(page_content)]
    if not blocks:
        blocks = [match.group(0) for match in RESULT_LINK_RE.finditer(page_content)]
    for block in blocks:
        link = RESULT_LINK_RE.search(block)
        if not link:
            continue
        title = _clean_html_fragment(link.group("title"))
        url = _normalize_duckduckgo_url(link.group("url"))
        snippet = ""
        snippet_match = RESULT_SNIPPET_RE.search(block)
        if snippet_match:
            snippet = _clean_html_fragment(snippet_match.group("snippet") or snippet_match.group("snippet_div") or "")
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": "duckduckgo_html",
                "fetch_status": "not_fetched",
                "content_excerpt": "",
                "content_truncated": False,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _searxng_time_range(recency_days: int | None) -> str:
    if not recency_days or recency_days <= 0:
        return ""
    if recency_days <= 1:
        return "day"
    if recency_days <= 31:
        return "month"
    return "year"


def _searxng_search(query: str, *, max_results: int, domains: list[str], recency_days: int | None) -> list[dict[str, Any]]:
    base_url = os.environ.get("DIAEVO_SEARXNG_URL", "").strip().rstrip("/")
    if not base_url:
        raise ToolError("DIAEVO_SEARXNG_URL is not configured")
    params = {
        "q": _query_with_domains(query, domains),
        "format": "json",
        "language": "auto",
        "safesearch": "0",
    }
    time_range = _searxng_time_range(recency_days)
    if time_range:
        params["time_range"] = time_range
    fetched = _fetch_url(f"{base_url}/search?{urlencode(params)}", 300_000)
    try:
        payload = json.loads(fetched.get("content", "{}"))
    except json.JSONDecodeError as exc:
        raise ToolError("SearXNG returned invalid JSON") from exc
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or url or "Untitled").strip()
        if not url:
            continue
        engine = str(item.get("engine") or item.get("engines") or "searxng").strip()
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": str(item.get("content") or item.get("snippet") or "").strip(),
                "source": engine or "searxng",
                "fetch_status": "not_fetched",
                "content_excerpt": "",
                "content_truncated": False,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _duckduckgo_search(query: str, *, max_results: int, domains: list[str]) -> list[dict[str, Any]]:
    page = _fetch_url(f"https://duckduckgo.com/html/?q={quote_plus(_query_with_domains(query, domains))}", 200_000)
    return _parse_duckduckgo_results(page["content"], max_results)


def _parse_bing_results(page_content: str, max_results: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for match in BING_RESULT_RE.finditer(page_content):
        block = match.group("body")
        link = BING_LINK_RE.search(block)
        if not link:
            continue
        title = _clean_html_fragment(link.group("title"))
        url = html.unescape(link.group("url"))
        if not title or not url:
            continue
        snippet = ""
        snippet_match = BING_SNIPPET_RE.search(block)
        if snippet_match:
            snippet = _clean_html_fragment(snippet_match.group("snippet") or "")
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": "bing_html",
                "fetch_status": "not_fetched",
                "content_excerpt": "",
                "content_truncated": False,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _bing_search(query: str, *, max_results: int, domains: list[str]) -> list[dict[str, Any]]:
    url = f"https://www.bing.com/search?q={quote_plus(_query_with_domains(query, domains))}"
    page = _fetch_url(url, 240_000)
    return _parse_bing_results(page["content"], max_results)


def _fetch_search_result_excerpts(results: list[dict[str, Any]], fetch_top: int) -> None:
    for item in results[:fetch_top]:
        url = str(item.get("url") or "").strip()
        if not url:
            item["fetch_status"] = "skipped"
            continue
        try:
            fetched = _fetch_url(url, 80_000)
            content, content_truncated = _extract_web_text(fetched.get("content", ""), limit=2_400)
            excerpt, excerpt_truncated = _best_web_excerpt(content, limit=900)
        except Exception as exc:
            item["fetch_status"] = f"error: {exc}"
            continue
        item["fetch_status"] = "ok"
        item["final_url"] = fetched.get("url", url)
        item["content_type"] = fetched.get("content_type", "")
        item["status_code"] = fetched.get("status_code")
        item["content_excerpt"] = excerpt
        item["content_truncated"] = bool(fetched.get("truncated")) or content_truncated or excerpt_truncated


def _web_search(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("web_search requires query")
    max_results = max(1, min(int(args.get("max_results") or 5), 20))
    fetch_top = max(0, min(int(args.get("fetch_top", 3)), max_results))
    backend = str(args.get("backend") or os.environ.get("DIAEVO_WEB_SEARCH_BACKEND") or "auto").strip().lower()
    domains = _coerce_domains(args.get("domains"))
    recency_days = args.get("recency_days")
    recency_value = int(recency_days) if recency_days not in {None, ""} else None
    preview = {
        "query": query,
        "max_results": max_results,
        "fetch_top": fetch_top,
        "backend": backend,
        "domains": domains,
        "recency_days": recency_value,
    }
    if not approved:
        return _approval_result(TOOLS["web_search"], args, preview, "web_search needs approval because it uses the network")

    attempted_backends: list[str] = []
    fallback_reason = ""
    if backend in {"auto", "searxng"}:
        attempted_backends.append("searxng")
        try:
            results = _searxng_search(query, max_results=max_results, domains=domains, recency_days=recency_value)
            used_backend = "searxng"
        except Exception as exc:
            if backend == "searxng":
                raise
            fallback_reason = str(exc)
            results = []
            used_backend = ""
        if results:
            _fetch_search_result_excerpts(results, fetch_top)
            return {
                "status": "ok",
                "tool": "web_search",
                "backend": used_backend,
                "query": query,
                "results": results,
                "attempted_backends": attempted_backends,
                "fallback_reason": fallback_reason,
            }
        if backend == "searxng":
            return {
                "status": "ok",
                "tool": "web_search",
                "backend": "searxng",
                "query": query,
                "results": [],
                "attempted_backends": attempted_backends,
                "fallback_reason": fallback_reason,
            }

    if backend in {"bing", "bing_html"}:
        attempted_backends.append("bing_html")
        results = _bing_search(query, max_results=max_results, domains=domains)
        _fetch_search_result_excerpts(results, fetch_top)
        used_backend = "bing_html"
    elif backend in {"auto", "duckduckgo", "duckduckgo_html"}:
        attempted_backends.append("duckduckgo_html")
        results = _duckduckgo_search(query, max_results=max_results, domains=domains)
        _fetch_search_result_excerpts(results, fetch_top)
        used_backend = "duckduckgo_html"
    else:
        raise ToolError(f"unsupported web_search backend: {backend}")
    return {
        "status": "ok",
        "tool": "web_search",
        "backend": used_backend,
        "query": query,
        "results": results,
        "attempted_backends": attempted_backends,
        "fallback_reason": fallback_reason,
    }


ARXIV_ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
OPENSEARCH_NS = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV_REQUEST_LOCK = threading.Lock()
ARXIV_LAST_REQUEST_AT = 0.0
ARXIV_MIN_REQUEST_INTERVAL = 3.0
ARXIV_CACHE_TTL_SECONDS = 6 * 60 * 60
ARXIV_LOCK_TIMEOUT_SECONDS = 90.0
ARXIV_LOCK_STALE_SECONDS = 10 * 60
ARXIV_STATE_PATH = DIAEVO_DIR / "arxiv_api_state.json"
ARXIV_CACHE_DIR = DIAEVO_DIR / "arxiv_cache"
ARXIV_LOCK_DIR = DIAEVO_DIR / "arxiv_api.lock"
ARXIV_FIELD_PREFIXES = {
    "all": "all",
    "title": "ti",
    "author": "au",
    "abstract": "abs",
    "category": "cat",
}


@contextmanager
def _arxiv_api_file_lock() -> Any:
    ARXIV_LOCK_DIR.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    while True:
        try:
            ARXIV_LOCK_DIR.mkdir()
            (ARXIV_LOCK_DIR / "owner.json").write_text(
                json.dumps({"pid": os.getpid(), "created_at": time.time()}, sort_keys=True),
                encoding="utf-8",
            )
            break
        except FileExistsError:
            try:
                age = time.time() - ARXIV_LOCK_DIR.stat().st_mtime
                if age > ARXIV_LOCK_STALE_SECONDS:
                    shutil.rmtree(ARXIV_LOCK_DIR, ignore_errors=True)
                    continue
            except OSError:
                pass
            if time.monotonic() - started >= ARXIV_LOCK_TIMEOUT_SECONDS:
                raise ToolError("arxiv_search timed out waiting for shared API rate-limit lock")
            time.sleep(0.1)
    try:
        yield
    finally:
        shutil.rmtree(ARXIV_LOCK_DIR, ignore_errors=True)


def _read_arxiv_state_last_request_at() -> float:
    try:
        state = json.loads(ARXIV_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    try:
        return float(state.get("last_request_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _write_arxiv_state_last_request_at(value: float) -> None:
    ARXIV_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARXIV_STATE_PATH.with_name(f"{ARXIV_STATE_PATH.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({"last_request_at": value}, sort_keys=True), encoding="utf-8")
    tmp.replace(ARXIV_STATE_PATH)


def _arxiv_cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return ARXIV_CACHE_DIR / f"{digest}.json"


def _read_arxiv_cache(url: str) -> dict[str, Any] | None:
    path = _arxiv_cache_path(url)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        fetched_at = float(payload.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if time.time() - fetched_at > ARXIV_CACHE_TTL_SECONDS:
        return None
    content = payload.get("content")
    if not isinstance(content, str):
        return None
    return {
        "url": str(payload.get("url") or url),
        "status_code": int(payload.get("status_code") or 200),
        "content_type": str(payload.get("content_type") or "application/atom+xml"),
        "truncated": bool(payload.get("truncated", False)),
        "content": content,
        "cache_hit": True,
    }


def _write_arxiv_cache(url: str, fetched: dict[str, Any]) -> None:
    ARXIV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _arxiv_cache_path(url)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    payload = {
        "url": fetched.get("url") or url,
        "status_code": fetched.get("status_code"),
        "content_type": fetched.get("content_type"),
        "truncated": fetched.get("truncated", False),
        "content": fetched.get("content") or "",
        "fetched_at": time.time(),
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fetch_arxiv_url(url: str, max_bytes: int) -> dict[str, Any]:
    cached = _read_arxiv_cache(url)
    if cached is not None:
        return cached

    global ARXIV_LAST_REQUEST_AT
    with ARXIV_REQUEST_LOCK:
        with _arxiv_api_file_lock():
            cached = _read_arxiv_cache(url)
            if cached is not None:
                return cached
            now = time.time()
            last_request_at = max(ARXIV_LAST_REQUEST_AT, _read_arxiv_state_last_request_at())
            if last_request_at > now:
                time.sleep(last_request_at - now)
            elif last_request_at > 0:
                elapsed = now - last_request_at
                if elapsed < ARXIV_MIN_REQUEST_INTERVAL:
                    time.sleep(ARXIV_MIN_REQUEST_INTERVAL - elapsed)
            request_at = time.time()
            ARXIV_LAST_REQUEST_AT = request_at
            _write_arxiv_state_last_request_at(request_at)
            try:
                fetched = _fetch_arxiv_api_url(url, max_bytes)
            except ToolError as exc:
                if "HTTP 429" in str(exc):
                    retry_after = re.search(r"Retry-After=(\d+)", str(exc))
                    cool_down = float(retry_after.group(1)) if retry_after else 30.0
                    ARXIV_LAST_REQUEST_AT = time.time() + max(cool_down, ARXIV_MIN_REQUEST_INTERVAL)
                    _write_arxiv_state_last_request_at(ARXIV_LAST_REQUEST_AT)
                raise
            _write_arxiv_cache(url, fetched)
            return fetched


def _clean_xml_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _arxiv_atom_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return _clean_xml_text(child.text if child is not None else "")


def _arxiv_term(prefix: str, value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if prefix == "cat":
        return f"cat:{text}"
    escaped = text.replace('"', '\\"')
    return f'{prefix}:"{escaped}"'


def _looks_like_arxiv_query(value: str) -> bool:
    if re.search(r"\b(all|ti|au|abs|cat|id|doi|jr|co):", value, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"\b(AND|OR|ANDNOT)\b", value))


def _build_arxiv_query(args: dict[str, Any]) -> str:
    query = _required_text(args, "query", "arxiv_search")
    search_field = str(args.get("search_field") or "all").strip().lower()
    if search_field not in {*ARXIV_FIELD_PREFIXES, "advanced"}:
        raise ToolError("arxiv_search search_field must be one of all, title, author, abstract, category, advanced")
    if search_field == "advanced" or _looks_like_arxiv_query(query):
        pieces = [query]
    else:
        pieces = [_arxiv_term(ARXIV_FIELD_PREFIXES[search_field], query)]
    category = str(args.get("category") or "").strip()
    if category:
        pieces.append(_arxiv_term("cat", category))
    author = str(args.get("author") or "").strip()
    if author:
        pieces.append(_arxiv_term("au", author))
    return " AND ".join(piece for piece in pieces if piece)


def _parse_arxiv_feed(text: str) -> tuple[int | None, list[dict[str, Any]]]:
    root = ET.fromstring(text)
    total_text = root.findtext(f"{OPENSEARCH_NS}totalResults")
    try:
        total_results = int(str(total_text).strip()) if total_text else None
    except ValueError:
        total_results = None
    papers: list[dict[str, Any]] = []
    for entry in root.findall(f"{ARXIV_ATOM}entry"):
        entry_id = _arxiv_atom_text(entry, f"{ARXIV_ATOM}id")
        links: dict[str, str] = {}
        for link in entry.findall(f"{ARXIV_ATOM}link"):
            href = str(link.attrib.get("href") or "")
            title = str(link.attrib.get("title") or "")
            rel = str(link.attrib.get("rel") or "")
            link_type = str(link.attrib.get("type") or "")
            if title == "pdf" or link_type == "application/pdf":
                links["pdf_url"] = href
            elif rel == "alternate":
                links["abs_url"] = href
        authors = [
            _clean_xml_text(author.findtext(f"{ARXIV_ATOM}name"))
            for author in entry.findall(f"{ARXIV_ATOM}author")
        ]
        categories = [str(item.attrib.get("term") or "") for item in entry.findall(f"{ARXIV_ATOM}category")]
        primary = entry.find(f"{ARXIV_NS}primary_category")
        arxiv_id = entry_id.rsplit("/abs/", 1)[-1] if "/abs/" in entry_id else entry_id
        papers.append(
            {
                "arxiv_id": arxiv_id,
                "title": _arxiv_atom_text(entry, f"{ARXIV_ATOM}title"),
                "authors": [author for author in authors if author],
                "published": _arxiv_atom_text(entry, f"{ARXIV_ATOM}published"),
                "updated": _arxiv_atom_text(entry, f"{ARXIV_ATOM}updated"),
                "summary": _arxiv_atom_text(entry, f"{ARXIV_ATOM}summary"),
                "primary_category": str(primary.attrib.get("term") or "") if primary is not None else "",
                "categories": [category for category in categories if category],
                "doi": _arxiv_atom_text(entry, f"{ARXIV_NS}doi"),
                "journal_ref": _arxiv_atom_text(entry, f"{ARXIV_NS}journal_ref"),
                "comment": _arxiv_atom_text(entry, f"{ARXIV_NS}comment"),
                "abs_url": links.get("abs_url") or entry_id,
                "pdf_url": links.get("pdf_url") or (entry_id.replace("/abs/", "/pdf/") if "/abs/" in entry_id else ""),
            }
        )
    return total_results, papers


def _arxiv_search(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    search_query = _build_arxiv_query(args)
    max_results = max(1, min(int(args.get("max_results") or 5), 25))
    start = max(0, int(args.get("start") or 0))
    sort_by = str(args.get("sort_by") or "relevance").strip()
    sort_order = str(args.get("sort_order") or "descending").strip()
    if sort_by not in {"relevance", "lastUpdatedDate", "submittedDate"}:
        raise ToolError("arxiv_search sort_by must be relevance, lastUpdatedDate, or submittedDate")
    if sort_order not in {"ascending", "descending"}:
        raise ToolError("arxiv_search sort_order must be ascending or descending")
    params = urlencode(
        {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"
    fetched = _fetch_arxiv_url(url, 500_000)
    total_results, papers = _parse_arxiv_feed(fetched["content"])
    return {
        "status": "ok",
        "tool": "arxiv_search",
        "query": args.get("query"),
        "search_query": search_query,
        "source": "arxiv_api",
        "url": url,
        "total_results": total_results,
        "start": start,
        "max_results": max_results,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "cache_hit": bool(fetched.get("cache_hit", False)),
        "rate_limit_interval_seconds": ARXIV_MIN_REQUEST_INTERVAL,
        "results": papers,
    }


def _kg_answer(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("kg_answer requires query")
    strict = bool(args.get("strict", True))
    include_pending = bool(args.get("include_pending", False))
    max_paths = max(1, min(int(args.get("max_paths") or 5), 20))
    current_dir = args.get("current_dir") or None
    domain = args.get("domain") or None
    queue_path = args.get("queue_path") or None
    vector_backend = args.get("vector_backend") or None
    embedding_model = args.get("embedding_model") or None
    hf_endpoint = args.get("hf_endpoint") or None
    result = answer_kg(
        query,
        strict=strict,
        include_pending=include_pending,
        current_dir=current_dir,
        domain=domain,
        queue_path=queue_path,
        max_paths=max_paths,
        vector_backend=vector_backend,
        embedding_model=embedding_model,
        hf_endpoint=hf_endpoint,
    )
    return {"status": "ok", "tool": "kg_answer", **result}


def _recommend_skills(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    task = _required_text(args, "task", "recommend_skills")
    top_k = max(1, min(int(args.get("top_k") or 5), 10))
    return {"status": "ok", "tool": "recommend_skills", "recommendations": recommend_skill_contexts(task, top_k=top_k)}


def _load_skill_context(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    name = _required_text(args, "name", "load_skill_context")
    task = str(args.get("task") or "")
    result = load_skill_context_data(name, task=task)
    return {"tool": "load_skill_context", **result}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _required_text(args: dict[str, Any], key: str, tool_name: str) -> str:
    value = args.get(key)
    if value is None:
        raise ToolError(f"{tool_name} requires {key}")
    text = str(value)
    if not text.strip():
        raise ToolError(f"{tool_name} requires non-empty {key}")
    return text


def _validate_required_args(spec: ToolSpec, args: dict[str, Any]) -> None:
    required = spec.input_schema.get("required") or []
    properties = spec.input_schema.get("properties") or {}
    for key in required:
        if key not in args or args.get(key) is None:
            raise ToolError(f"{spec.name} requires {key}")
        schema = properties.get(key) if isinstance(properties, dict) else {}
        if isinstance(schema, dict) and schema.get("type") == "string":
            text = str(args.get(key))
            if not text.strip():
                if key == "content":
                    raise ToolError(f"{spec.name} requires content")
                raise ToolError(f"{spec.name} requires non-empty {key}")


TOOLS: dict[str, ToolSpec] = {}


def _register(spec: ToolSpec) -> None:
    TOOLS[spec.name] = spec


_register(
    ToolSpec(
        name="list_files",
        description="List files under a workspace directory.",
        input_schema=_schema(
            {
                "path": {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": False},
                "pattern": {"type": "string", "default": "*"},
                "max_results": {"type": "integer", "default": 200},
                "include_hidden": {"type": "boolean", "default": False},
            }
        ),
        read_only=True,
        approval_required=False,
        destructive=False,
        handler=_list_files,
    )
)
_register(
    ToolSpec(
        name="read_file",
        description="Read a bounded UTF-8 view of a workspace file.",
        input_schema=_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": ["integer", "null"], "default": 400},
            },
            ["path"],
        ),
        read_only=True,
        approval_required=False,
        destructive=False,
        handler=_read_file,
    )
)
_register(
    ToolSpec(
        name="write_file",
        description="Create or overwrite a workspace file after showing a diff preview.",
        input_schema=_schema(
            {"path": {"type": "string", "minLength": 1}, "content": {"type": "string"}},
            ["path", "content"],
        ),
        read_only=False,
        approval_required=True,
        destructive=False,
        handler=_write_file,
        risk="medium",
    )
)
_register(
    ToolSpec(
        name="edit_file",
        description="Replace an exact string in a workspace file after showing a diff preview.",
        input_schema=_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "old_string": {"type": "string", "minLength": 1},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            ["path", "old_string", "new_string"],
        ),
        read_only=False,
        approval_required=True,
        destructive=False,
        handler=_edit_file,
        risk="medium",
    )
)
_register(
    ToolSpec(
        name="delete_file",
        description="Delete a workspace file or directory after approval.",
        input_schema=_schema(
            {"path": {"type": "string", "minLength": 1}, "recursive": {"type": "boolean", "default": False}},
            ["path"],
        ),
        read_only=False,
        approval_required=True,
        destructive=True,
        handler=_delete_file,
        risk="high",
    )
)
_register(
    ToolSpec(
        name="apply_patch",
        description="Apply a unified diff to workspace files after approval.",
        input_schema=_schema({"patch": {"type": "string", "minLength": 1}}, ["patch"]),
        read_only=False,
        approval_required=True,
        destructive=False,
        handler=_apply_patch,
        risk="medium",
    )
)
_register(
    ToolSpec(
        name="run_shell",
        description="Run a local shell command in the workspace after approval.",
        input_schema=_schema(
            {"command": {"type": "string", "minLength": 1}, "timeout": {"type": "integer", "default": 30}},
            ["command"],
        ),
        read_only=False,
        approval_required=True,
        destructive=False,
        handler=_run_shell,
        risk="high",
    )
)
_register(
    ToolSpec(
        name="web_fetch",
        description="Fetch a URL after approval and return bounded page content with metadata.",
        input_schema=_schema(
            {"url": {"type": "string", "minLength": 1}, "max_bytes": {"type": "integer", "default": 80000}},
            ["url"],
        ),
        read_only=True,
        approval_required=True,
        destructive=False,
        handler=_web_fetch,
        risk="network",
    )
)
_register(
    ToolSpec(
        name="web_search",
        description=(
            "Search the web after approval using SearXNG when configured, with DuckDuckGo HTML fallback "
            "or an explicit Bing HTML backend, and optionally fetch excerpts from top results. "
            "Set DIAEVO_WEB_SEARCH_BACKEND to choose the default backend when the tool call does not pass backend."
        ),
        input_schema=_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "max_results": {"type": "integer", "default": 5},
                "fetch_top": {"type": "integer", "default": 3},
                "backend": {
                    "type": "string",
                    "enum": ["auto", "searxng", "duckduckgo", "duckduckgo_html", "bing", "bing_html"],
                    "default": "auto",
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "recency_days": {"type": "integer"},
            },
            ["query"],
        ),
        read_only=True,
        approval_required=True,
        destructive=False,
        handler=_web_search,
        risk="network",
    )
)
_register(
    ToolSpec(
        name="arxiv_search",
        description=(
            "Search arXiv papers via the official Atom API and return structured academic metadata, "
            "including title, authors, abstract, categories, dates, abs URL, and PDF URL."
        ),
        input_schema=_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "search_field": {
                    "type": "string",
                    "enum": ["all", "title", "author", "abstract", "category", "advanced"],
                    "default": "all",
                },
                "category": {"type": "string", "default": ""},
                "author": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 5},
                "start": {"type": "integer", "default": 0},
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "lastUpdatedDate", "submittedDate"],
                    "default": "relevance",
                },
                "sort_order": {"type": "string", "enum": ["ascending", "descending"], "default": "descending"},
            },
            ["query"],
        ),
        read_only=True,
        approval_required=False,
        destructive=False,
        handler=_arxiv_search,
        risk="network",
    )
)
_register(
    ToolSpec(
        name="kg_answer",
        description="Answer from the reviewed graph-vector KG. strict=true uses only accepted graph-vector evidence subgraphs.",
        input_schema=_schema(
            {
                "query": {"type": "string"},
                "strict": {"type": "boolean", "default": True},
                "include_pending": {"type": "boolean", "default": False},
                "max_paths": {"type": "integer", "default": 5},
                "current_dir": {"type": "string", "default": ""},
                "domain": {"type": "string", "default": ""},
                "queue_path": {"type": "string", "default": ""},
                "vector_backend": {"type": "string", "enum": ["auto", "dense", "tfidf"], "default": "auto"},
                "embedding_model": {"type": "string", "default": ""},
                "hf_endpoint": {"type": "string", "default": "https://hf-mirror.com"},
            },
            ["query"],
        ),
        read_only=True,
        approval_required=False,
        destructive=False,
        handler=_kg_answer,
        risk="low",
    )
)
_register(
    ToolSpec(
        name="recommend_skills",
        description="Recommend installed or generated skills for the current user task.",
        input_schema=_schema(
            {
                "task": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "default": 5},
            },
            ["task"],
        ),
        read_only=True,
        approval_required=False,
        destructive=False,
        handler=_recommend_skills,
    )
)
_register(
    ToolSpec(
        name="load_skill_context",
        description="Load an installed or generated skill's SKILL.md plus task-relevant reference routing.",
        input_schema=_schema(
            {
                "name": {"type": "string", "minLength": 1},
                "task": {"type": "string", "default": ""},
            },
            ["name"],
        ),
        read_only=True,
        approval_required=False,
        destructive=False,
        handler=_load_skill_context,
    )
)


def tool_schemas() -> list[dict[str, Any]]:
    return [spec.to_schema() for spec in TOOLS.values()]


def execute_tool(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    approve: bool = False,
    turn_id: str | None = None,
    event_log_path: str | Path | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    args = dict(args or {})
    spec = TOOLS.get(name)
    if spec is None:
        result = {"status": "error", "tool": name, "error": f"unknown tool: {name}"}
        return result
    started_at = now_iso()
    event_id = uuid.uuid4().hex
    try:
        _validate_required_args(spec, args)
        if cancel_event is not None:
            args["__cancel_event__"] = cancel_event
        result = spec.handler(args, approve)
    except Exception as exc:
        result = {"status": "error", "tool": spec.name, "error": str(exc)}
    ended_at = now_iso()
    preview_only = result.get("status") == "requires_approval" and not approve
    event = {
        "id": event_id,
        "turn_id": turn_id or event_id,
        "tool": spec.name,
        "args": _safe_for_log(args),
        "status": result.get("status", "ok"),
        "preview_only": preview_only,
        "approval_required": spec.approval_required,
        "approved": bool(approve),
        "read_only": spec.read_only,
        "destructive": spec.destructive,
        "risk": spec.risk,
        "started_at": started_at,
        "ended_at": ended_at,
        "result": _safe_for_log(result),
    }
    _append_event(event, event_log_path)
    if preview_only:
        result["preview_only"] = True
    result["event_id"] = event_id
    result["event_log"] = str(Path(event_log_path) if event_log_path else DEFAULT_EVENT_LOG)
    return result
