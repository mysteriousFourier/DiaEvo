from __future__ import annotations

import difflib
import fnmatch
import html
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from .knowledge_graph import answer_kg
from .paths import PROJECT_ROOT

WORKSPACE_ROOT = PROJECT_ROOT.resolve()
DEFAULT_EVENT_LOG = PROJECT_ROOT / ".skillminer" / "tool_events.jsonl"
MAX_TEXT_BYTES = 240_000
MAX_LOG_STRING = 2_000


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
    target = resolve_workspace_path(args.get("path"), must_exist=True)
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
    if "content" not in args:
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
    target = resolve_workspace_path(args.get("path"), must_exist=True)
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
    target = resolve_workspace_path(args.get("path"), must_exist=True)
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
    completed = subprocess.run(cmd, text=True, capture_output=True, cwd=WORKSPACE_ROOT, timeout=timeout, shell=shell)
    return {
        "status": "ok" if completed.returncode == 0 else "error",
        "tool": "run_shell",
        "command": command,
        "returncode": completed.returncode,
        "stdout": _truncate(completed.stdout, 20_000),
        "stderr": _truncate(completed.stderr, 20_000),
    }


def _fetch_url(url: str, max_bytes: int) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolError("web tools only support http and https URLs")
    request = Request(url, headers={"User-Agent": "SkillMiner/0.1"})
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


def _web_fetch(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("web_fetch requires url")
    max_bytes = max(1_000, min(int(args.get("max_bytes") or 80_000), 500_000))
    preview = {"url": url, "max_bytes": max_bytes}
    if not approved:
        return _approval_result(TOOLS["web_fetch"], args, preview, "web_fetch needs approval because it uses the network")
    fetched = _fetch_url(url, max_bytes)
    return {"status": "ok", "tool": "web_fetch", **fetched}


RESULT_LINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    flags=re.IGNORECASE | re.DOTALL,
)


def _web_search(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("web_search requires query")
    max_results = max(1, min(int(args.get("max_results") or 5), 10))
    preview = {"query": query, "max_results": max_results}
    if not approved:
        return _approval_result(TOOLS["web_search"], args, preview, "web_search needs approval because it uses the network")
    page = _fetch_url(f"https://duckduckgo.com/html/?q={quote_plus(query)}", 200_000)
    results = []
    for match in RESULT_LINK_RE.finditer(page["content"]):
        title = re.sub(r"<.*?>", "", match.group("title"))
        results.append({"title": html.unescape(title), "url": html.unescape(match.group("url"))})
        if len(results) >= max_results:
            break
    return {
        "status": "ok",
        "tool": "web_search",
        "query": query,
        "source": "duckduckgo_html",
        "results": results,
    }


def _kg_answer(args: dict[str, Any], approved: bool) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("kg_answer requires query")
    strict = bool(args.get("strict", True))
    include_pending = bool(args.get("include_pending", False))
    max_paths = max(1, min(int(args.get("max_paths") or 5), 20))
    current_dir = args.get("current_dir") or None
    queue_path = args.get("queue_path") or None
    result = answer_kg(
        query,
        strict=strict,
        include_pending=include_pending,
        current_dir=current_dir,
        queue_path=queue_path,
        max_paths=max_paths,
    )
    return {"status": "ok", "tool": "kg_answer", **result}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


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
                "path": {"type": "string"},
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
        input_schema=_schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
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
                "path": {"type": "string"},
                "old_string": {"type": "string"},
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
        input_schema=_schema({"path": {"type": "string"}, "recursive": {"type": "boolean", "default": False}}, ["path"]),
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
        input_schema=_schema({"patch": {"type": "string"}}, ["patch"]),
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
        input_schema=_schema({"command": {"type": "string"}, "timeout": {"type": "integer", "default": 30}}, ["command"]),
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
        input_schema=_schema({"url": {"type": "string"}, "max_bytes": {"type": "integer", "default": 80000}}, ["url"]),
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
        description="Run a basic web search after approval and return source URLs.",
        input_schema=_schema({"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, ["query"]),
        read_only=True,
        approval_required=True,
        destructive=False,
        handler=_web_search,
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
                "queue_path": {"type": "string", "default": ""},
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


def tool_schemas() -> list[dict[str, Any]]:
    return [spec.to_schema() for spec in TOOLS.values()]


def execute_tool(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    approve: bool = False,
    turn_id: str | None = None,
    event_log_path: str | Path | None = None,
) -> dict[str, Any]:
    args = dict(args or {})
    spec = TOOLS.get(name)
    if spec is None:
        result = {"status": "error", "tool": name, "error": f"unknown tool: {name}"}
        return result
    started_at = now_iso()
    event_id = uuid.uuid4().hex
    try:
        result = spec.handler(args, approve)
    except Exception as exc:
        result = {"status": "error", "tool": spec.name, "error": str(exc)}
    ended_at = now_iso()
    event = {
        "id": event_id,
        "turn_id": turn_id or event_id,
        "tool": spec.name,
        "args": _safe_for_log(args),
        "status": result.get("status", "ok"),
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
    result["event_id"] = event_id
    result["event_log"] = str(Path(event_log_path) if event_log_path else DEFAULT_EVENT_LOG)
    return result
