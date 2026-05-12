from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT, REPORTS_DIR, ensure_project_dirs
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"


def _skill_dir(value: str | Path) -> Path:
    target = Path(value)
    if target.is_file():
        target = target.parent
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
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

    command_results: list[dict[str, Any]] = []
    overall_status = "passed"
    for command in commands:
        started = _now()
        try:
            if os.name == "nt":
                cmd: str | list[str] = ["powershell", "-NoProfile", "-Command", command]
                shell = False
            else:
                cmd = command
                shell = True
            completed = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=shell,
            )
            status = "passed" if completed.returncode == 0 else "failed"
            if status != "passed":
                overall_status = "failed"
            command_results.append(
                {
                    "command": command,
                    "status": status,
                    "returncode": completed.returncode,
                    "started_at": started,
                    "ended_at": _now(),
                    "stdout": _truncate(completed.stdout),
                    "stderr": _truncate(completed.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            overall_status = "failed"
            command_results.append(
                {
                    "command": command,
                    "status": "timeout",
                    "returncode": None,
                    "started_at": started,
                    "ended_at": _now(),
                    "stdout": _truncate(exc.stdout or "" if isinstance(exc.stdout, str) else ""),
                    "stderr": _truncate(exc.stderr or "" if isinstance(exc.stderr, str) else ""),
                }
            )
    result = {
        "status": overall_status,
        "skill_dir": str(skill_dir),
        "approval_required": True,
        "approved": True,
        "verification_passed": bool(verify_result.get("passed")),
        "timeout_sec": timeout,
        "workspace_only": workspace_only,
        "network": network,
        "commands": commands,
        "results": command_results,
        "updated_at": _now(),
    }
    validation.update(result)
    write_json(skill_dir / "validation.json", validation)
    write_json(REPORTS_DIR / f"validation_{skill_dir.name}.json", result)
    record_validation_feedback(result)
    return result
