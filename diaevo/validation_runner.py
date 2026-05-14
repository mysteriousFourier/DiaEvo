from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import WORKSPACE_ROOT, REPORTS_DIR, ensure_project_dirs
from .storage import read_json, write_json
from .tool_layer import resolve_workspace_path
from .verifier import DANGEROUS_PATTERNS, verify_skill
from .evolution import record_validation_feedback


NETWORK_PATTERNS = [
    r"\bcurl\b",
    r"\bwget\b",
    r"\bInvoke-WebRequest\b",
    r"\bweb_fetch\b",
    r"\bweb_search\b",
    r"\bhttp://",
    r"\bhttps://",
]

INSTALL_PATTERNS = [
    r"\bpip\s+install\b",
    r"\buv\s+pip\s+install\b",
    r"\bnpm\s+install\b",
    r"\bpnpm\s+install\b",
    r"\byarn\s+add\b",
]

MAX_OUTPUT_CHARS = 20_000
MAX_DIFF_FILE_BYTES = 200_000
MAX_DIFF_CHARS = 200_000
VALIDATION_RUNS_DIR = WORKSPACE_ROOT / ".tmp" / "validation-runs"

EXCLUDED_COPY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
}

EXCLUDED_COPY_PATHS = {
    ("outputs", "reports"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return normalized or "skill"


def _run_id(skill_dir: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{os.getpid()}-{_slug(skill_dir.name)}"


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return ()


def _should_exclude_name(name: str) -> bool:
    return name in EXCLUDED_COPY_NAMES or name == ".env" or name.startswith(".env.")


def _is_excluded_path(parts: tuple[str, ...]) -> bool:
    if any(_should_exclude_name(part) for part in parts):
        return True
    for excluded in EXCLUDED_COPY_PATHS:
        if len(parts) >= len(excluded) and parts[: len(excluded)] == excluded:
            return True
    return False


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    current = Path(directory).resolve(strict=False)
    ignored: set[str] = set()
    for name in names:
        candidate = current / name
        parts = _relative_parts(candidate, WORKSPACE_ROOT.resolve())
        if _should_exclude_name(name) or _is_excluded_path(parts):
            ignored.add(name)
    return ignored


def _copy_skill_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if _should_exclude_name(name)}


def _create_sandbox(skill_dir: Path) -> dict[str, Path | str]:
    run_id = _run_id(skill_dir)
    run_dir = VALIDATION_RUNS_DIR / run_id
    workspace = run_dir / "workspace"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        WORKSPACE_ROOT,
        workspace,
        ignore=_copy_ignore,
        symlinks=True,
        ignore_dangling_symlinks=True,
    )
    sandbox_skill_dir = workspace / skill_dir.relative_to(WORKSPACE_ROOT.resolve())
    if not sandbox_skill_dir.exists() and skill_dir.exists():
        sandbox_skill_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_dir, sandbox_skill_dir, ignore=_copy_skill_ignore)
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "workspace": workspace,
        "artifacts": artifacts,
        "skill_dir": sandbox_skill_dir,
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_lines_for_diff(path: Path) -> list[str] | None:
    try:
        if path.stat().st_size > MAX_DIFF_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def _workspace_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = _relative_parts(path, root)
        if _is_excluded_path(rel_parts):
            continue
        rel_path = Path(*rel_parts).as_posix()
        try:
            stat = path.stat()
            snapshot[rel_path] = {
                "sha256": _hash_file(path),
                "size": stat.st_size,
                "text_lines": _text_lines_for_diff(path),
            }
        except OSError:
            continue
    return snapshot


def _diff_touched_files(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    touched: list[dict[str, Any]] = []
    diff_chunks: list[str] = []
    for rel_path in sorted(set(before) | set(after)):
        before_item = before.get(rel_path)
        after_item = after.get(rel_path)
        if before_item is None:
            change = "added"
        elif after_item is None:
            change = "deleted"
        elif before_item.get("sha256") != after_item.get("sha256"):
            change = "modified"
        else:
            continue
        touched.append(
            {
                "path": rel_path,
                "change": change,
                "size_before": before_item.get("size") if before_item else None,
                "size_after": after_item.get("size") if after_item else None,
                "sha256_before": before_item.get("sha256") if before_item else None,
                "sha256_after": after_item.get("sha256") if after_item else None,
            }
        )
        before_lines = before_item.get("text_lines") if before_item else []
        after_lines = after_item.get("text_lines") if after_item else []
        if before_lines is None or after_lines is None:
            diff_chunks.append(f"Binary or large file changed: {rel_path} ({change})\n")
            continue
        diff_chunks.extend(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
    diff_text = "".join(diff_chunks) if diff_chunks else "No file changes detected.\n"
    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS] + f"\n... <truncated {len(diff_text) - MAX_DIFF_CHARS} chars>\n"
    return touched, diff_text


def _coerce_output(value: str | bytes | None) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return ""


def _validation_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "approval_required": result.get("approval_required"),
        "approved": result.get("approved"),
        "verification_passed": result.get("verification_passed"),
        "timeout_sec": result.get("timeout_sec"),
        "workspace_only": result.get("workspace_only"),
        "network": result.get("network"),
        "commands": result.get("commands", []),
        "updated_at": result.get("updated_at"),
        "sandbox_run_id": result.get("sandbox_run_id"),
        "sandbox_dir": result.get("sandbox_dir"),
        "sandbox_workspace": result.get("sandbox_workspace"),
        "artifacts_dir": result.get("artifacts_dir"),
        "sandbox_report_path": result.get("sandbox_report_path"),
        "diff_path": result.get("diff_path"),
        "touched_files_path": result.get("touched_files_path"),
        "touched_file_count": len(result.get("touched_files", [])) if isinstance(result.get("touched_files"), list) else 0,
        "results_summary": [
            {
                "command": item.get("command"),
                "status": item.get("status"),
                "returncode": item.get("returncode"),
                "duration_sec": item.get("duration_sec"),
            }
            for item in result.get("results", [])
            if isinstance(item, dict)
        ],
    }


def _subprocess_command(command: str) -> tuple[str | list[str], bool]:
    if os.name != "nt":
        return command, True
    script = (
        f"& {{ {command} }}; "
        "$DiaEvoSuccess = $?; "
        "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }; "
        "if ($DiaEvoSuccess) { exit 0 } else { exit 1 }"
    )
    return ["powershell", "-NoProfile", "-Command", script], False


def _skill_dir(value: str | Path) -> Path:
    target = Path(value)
    if target.is_file():
        target = target.parent
    if not target.is_absolute():
        target = WORKSPACE_ROOT / target
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(WORKSPACE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"skill path is outside workspace: {value}") from exc
    return resolved


def _load_validation(skill_dir: Path) -> dict[str, Any]:
    value = read_json(skill_dir / "validation.json", default={})
    if not isinstance(value, dict):
        raise ValueError(f"validation.json must be an object: {skill_dir / 'validation.json'}")
    return value


def _commands(validation: dict[str, Any]) -> list[str]:
    values = validation.get("commands", [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        raise ValueError("validation commands must be a list")
    return [str(item).strip() for item in values if str(item).strip()]


def _check_command(command: str, *, network: bool) -> list[str]:
    findings: list[str] = []
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE | re.DOTALL):
            findings.append(f"dangerous command pattern matched: {pattern}")
    for pattern in INSTALL_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            findings.append(f"dependency installation is not allowed in validation: {pattern}")
    if not network:
        for pattern in NETWORK_PATTERNS:
            if re.search(pattern, command, flags=re.IGNORECASE):
                findings.append(f"network command is not allowed in validation: {pattern}")
    return findings


def _preview(skill_dir: Path, validation: dict[str, Any], commands: list[str], findings: list[str]) -> dict[str, Any]:
    return {
        "status": "requires_approval",
        "skill_dir": str(skill_dir),
        "approval_required": True,
        "approved": False,
        "commands": commands,
        "timeout_sec": int(validation.get("timeout_sec") or 60),
        "workspace_only": bool(validation.get("workspace_only", True)),
        "network": bool(validation.get("network", False)),
        "findings": findings,
    }


def run_validation(skill: str | Path, *, approve: bool = False) -> dict[str, Any]:
    ensure_project_dirs()
    skill_dir = _skill_dir(skill)
    verify_result = verify_skill(skill_dir, write_report=False)
    validation = _load_validation(skill_dir)
    commands = _commands(validation)
    if not commands:
        result = {
            "status": "not_configured",
            "skill_dir": str(skill_dir),
            "message": "validation.json does not declare commands",
            "verification_passed": bool(verify_result.get("passed")),
            "commands": [],
        }
        write_json(REPORTS_DIR / f"validation_{skill_dir.name}.json", result)
        record_validation_feedback(result)
        return result
    timeout = max(1, min(int(validation.get("timeout_sec") or 60), 300))
    workspace_only = bool(validation.get("workspace_only", True))
    network = bool(validation.get("network", False))
    findings: list[str] = []
    if not verify_result.get("passed"):
        findings.append("skill verification must pass before validation replay")
    for command in commands:
        findings.extend(_check_command(command, network=network))
    if workspace_only:
        resolve_workspace_path(".")
    if findings or not approve:
        result = _preview(skill_dir, validation, commands, findings)
        if findings:
            result["status"] = "blocked"
        write_json(REPORTS_DIR / f"validation_{skill_dir.name}.json", result)
        record_validation_feedback(result)
        return result

    sandbox = _create_sandbox(skill_dir)
    sandbox_run_id = str(sandbox["run_id"])
    sandbox_dir = Path(sandbox["run_dir"])
    sandbox_workspace = Path(sandbox["workspace"])
    sandbox_artifacts = Path(sandbox["artifacts"])
    sandbox_skill_dir = Path(sandbox["skill_dir"])
    before_snapshot = _workspace_snapshot(sandbox_workspace)

    command_results: list[dict[str, Any]] = []
    overall_status = "passed"
    for command in commands:
        started = _now()
        monotonic_started = time.monotonic()
        try:
            cmd, shell = _subprocess_command(command)
            completed = subprocess.run(
                cmd,
                cwd=sandbox_workspace,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=shell,
            )
            ended = _now()
            duration_sec = round(time.monotonic() - monotonic_started, 4)
            status = "passed" if completed.returncode == 0 else "failed"
            if status != "passed":
                overall_status = "failed"
            command_results.append(
                {
                    "command": command,
                    "status": status,
                    "returncode": completed.returncode,
                    "started_at": started,
                    "ended_at": ended,
                    "duration_sec": duration_sec,
                    "stdout": _truncate(completed.stdout),
                    "stderr": _truncate(completed.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            overall_status = "failed"
            ended = _now()
            duration_sec = round(time.monotonic() - monotonic_started, 4)
            command_results.append(
                {
                    "command": command,
                    "status": "timeout",
                    "returncode": None,
                    "started_at": started,
                    "ended_at": ended,
                    "duration_sec": duration_sec,
                    "stdout": _truncate(_coerce_output(exc.stdout)),
                    "stderr": _truncate(_coerce_output(exc.stderr)),
                }
            )
    after_snapshot = _workspace_snapshot(sandbox_workspace)
    touched_files, diff_text = _diff_touched_files(before_snapshot, after_snapshot)
    diff_path = sandbox_artifacts / "diff.patch"
    touched_files_path = sandbox_artifacts / "touched_files.json"
    diff_path.write_text(diff_text, encoding="utf-8")
    write_json(touched_files_path, touched_files)
    result = {
        "status": overall_status,
        "skill_dir": str(skill_dir),
        "sandbox_skill_dir": str(sandbox_skill_dir),
        "approval_required": True,
        "approved": True,
        "verification_passed": bool(verify_result.get("passed")),
        "timeout_sec": timeout,
        "workspace_only": workspace_only,
        "network": network,
        "commands": commands,
        "results": command_results,
        "sandbox_run_id": sandbox_run_id,
        "sandbox_dir": str(sandbox_dir),
        "sandbox_workspace": str(sandbox_workspace),
        "artifacts_dir": str(sandbox_artifacts),
        "diff_path": str(diff_path),
        "touched_files_path": str(touched_files_path),
        "touched_files": touched_files,
        "updated_at": _now(),
    }
    sandbox_report_path = sandbox_artifacts / "report.json"
    result["sandbox_report_path"] = str(sandbox_report_path)
    write_json(sandbox_report_path, result)
    validation.update(_validation_summary(result))
    write_json(skill_dir / "validation.json", validation)
    write_json(REPORTS_DIR / f"validation_{skill_dir.name}.json", result)
    record_validation_feedback(result)
    return result
