from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .paths import REPORTS_DIR, ensure_project_dirs
from .storage import read_json, write_json


DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b",
    r"\bdel\s+/s\b",
    r"\bformat\s+[a-z]:",
    r"\bcurl\b.*\|\s*(sh|bash|powershell|pwsh)",
    r"\bwget\b.*\|\s*(sh|bash|powershell|pwsh)",
    r"\bInvoke-WebRequest\b.*\|\s*iex\b",
    r"\bSet-ExecutionPolicy\b",
    r"\bssh\b.+@",
    r"\bscp\b.+:",
    r"\b[A-Z]:\\Windows\\System32\b",
]

CREDENTIAL_PATTERNS = [
    r"api[_-]?key\s*[:=]",
    r"secret\s*[:=]",
    r"token\s*[:=]",
    r"password\s*[:=]",
]

REQUIRED_FRONTMATTER = ("name", "description", "tags", "source_cluster", "status")
REQUIRED_SECTIONS = (
    "## When To Use",
    "## Trigger Signals",
    "## Operating Steps",
    "## Failure Fallbacks",
    "## Verification Suggestions",
)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip()
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def verify_skill(skill_dir: str | Path, write_report: bool = True) -> dict[str, Any]:
    ensure_project_dirs()
    root = Path(skill_dir)
    skill_file = root / "SKILL.md" if root.is_dir() else root
    findings: list[dict[str, str]] = []
    if not skill_file.exists():
        findings.append({"severity": "error", "code": "missing_skill_md", "message": "SKILL.md not found"})
        return _finalize(skill_file, findings, write_report=write_report)
    text = skill_file.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if not meta:
        findings.append({"severity": "error", "code": "missing_frontmatter", "message": "YAML-like frontmatter is required"})
    for field in REQUIRED_FRONTMATTER:
        if not meta.get(field):
            findings.append({"severity": "error", "code": f"missing_{field}", "message": f"frontmatter field `{field}` is required"})
    if meta.get("status") and meta.get("status") not in {"candidate", "draft", "verified"}:
        findings.append({"severity": "warning", "code": "unknown_status", "message": "status should be candidate, draft, or verified"})
    description = meta.get("description", "")
    if len(description) < 30:
        findings.append({"severity": "warning", "code": "short_description", "message": "description should explain when to use the skill"})
    if len(body.strip()) < 400:
        findings.append({"severity": "warning", "code": "short_body", "message": "skill body is probably too short for reuse"})
    for section in REQUIRED_SECTIONS:
        if section not in body:
            findings.append(
                {"severity": "error", "code": "missing_required_section", "message": f"required section `{section}` is missing"}
            )
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            findings.append({"severity": "error", "code": "dangerous_command", "message": f"dangerous command pattern matched: {pattern}"})
    for pattern in CREDENTIAL_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            findings.append({"severity": "error", "code": "credential_pattern", "message": f"possible credential material matched: {pattern}"})
    if "..\\" in text or "../" in text:
        findings.append({"severity": "warning", "code": "parent_path", "message": "parent-directory paths require manual review"})
    if re.search(r"\binstall\b|\bpip\b|\bnpm\b|\buv\b", text, flags=re.IGNORECASE):
        findings.append({"severity": "info", "code": "dependency_hint", "message": "dependency-related instructions require user confirmation"})
    executable_validation = _load_executable_validation(skill_file)
    if executable_validation:
        status = str(executable_validation.get("status") or "unknown").lower()
        if status not in {"passed", "success", "ok"}:
            findings.append(
                {
                    "severity": "warning",
                    "code": "executable_validation_not_passed",
                    "message": f"executable validation status is `{status}`",
                }
            )
    return _finalize(skill_file, findings, executable_validation, write_report=write_report)


def _load_executable_validation(skill_file: Path) -> dict[str, Any]:
    validation_path = skill_file.parent / "validation.json"
    value = read_json(validation_path, default={})
    if not isinstance(value, dict):
        return {"status": "invalid", "path": str(validation_path)}
    if value:
        value.setdefault("path", str(validation_path))
    return value


def _finalize(
    skill_file: Path,
    findings: list[dict[str, str]],
    executable_validation: dict[str, Any] | None = None,
    write_report: bool = True,
) -> dict[str, Any]:
    error_count = sum(1 for finding in findings if finding["severity"] == "error")
    warning_count = sum(1 for finding in findings if finding["severity"] == "warning")
    info_count = sum(1 for finding in findings if finding["severity"] == "info")
    risk_score = min(1.0, error_count * 0.45 + warning_count * 0.15 + info_count * 0.05)
    result = {
        "skill_path": str(skill_file),
        "passed": error_count == 0,
        "risk_score": round(risk_score, 4),
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "findings": findings,
        "executable_validation": executable_validation or {},
    }
    if write_report:
        report_path = REPORTS_DIR / f"verify_{skill_file.parent.name if skill_file.parent.name else 'skill'}.json"
        write_json(report_path, result)
        result["report_path"] = str(report_path)
    return result
