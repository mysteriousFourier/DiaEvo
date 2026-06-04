from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .paths import REPORTS_DIR, ensure_project_dirs
from .script_artifacts import SCRIPT_REVIEW_STATUSES
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

ALLOWED_CODE_ARTIFACT_ENTRYPOINTS = {"scripts/skill_flow.py"}


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
    if "## Preserved Source Skill" in body:
        findings.append(
            {
                "severity": "error",
                "code": "external_skill_body_copied",
                "message": "external SKILL.md body must not be copied into DiaEvo candidate skills",
            }
        )
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
    _extend_migration_manifest_findings(skill_file.parent, findings)
    _extend_code_artifact_findings(skill_file.parent, findings)
    return _finalize(skill_file, findings, executable_validation, write_report=write_report)


def _load_executable_validation(skill_file: Path) -> dict[str, Any]:
    validation_path = skill_file.parent / "validation.json"
    value = read_json(validation_path, default={})
    if not isinstance(value, dict):
        return {"status": "invalid", "path": str(validation_path)}
    if value:
        value.setdefault("path", str(validation_path))
    return value


def _extend_migration_manifest_findings(skill_dir: Path, findings: list[dict[str, str]]) -> None:
    manifest_path = skill_dir / "migration_manifest.json"
    if not manifest_path.exists():
        return
    manifest = read_json(manifest_path, default={})
    if not isinstance(manifest, dict):
        findings.append({"severity": "error", "code": "invalid_migration_manifest", "message": "migration_manifest.json must be an object"})
        return
    schema = str(manifest.get("schema") or "")
    if schema not in {"diaevo.skill_package_migration.v1", "diaevo.skill_package_migration.v2"}:
        findings.append({"severity": "error", "code": "invalid_migration_manifest_schema", "message": "migration_manifest.json schema is invalid"})
    elif schema == "diaevo.skill_package_migration.v1":
        findings.append(
            {
                "severity": "warning",
                "code": "legacy_migration_manifest_schema",
                "message": "legacy skill package migrations should be regenerated with v2 provenance-only policy",
            }
        )
    mode = str(manifest.get("mode") or "")
    if mode != "skill_package":
        findings.append({"severity": "warning", "code": "unknown_migration_mode", "message": f"migration mode is `{mode}`"})
    if schema == "diaevo.skill_package_migration.v2":
        copy_policy = str(manifest.get("copy_policy") or "")
        if copy_policy != "provenance_only_no_external_skill_body_or_references":
            findings.append(
                {
                    "severity": "error",
                    "code": "invalid_migration_copy_policy",
                    "message": "v2 skill package migrations must use the provenance-only no-copy policy",
                }
            )
        if not isinstance(manifest.get("observed_headings", []), list):
            findings.append({"severity": "error", "code": "invalid_observed_headings", "message": "observed_headings must be a list"})
        if not isinstance(manifest.get("external_reference_candidates", []), list):
            findings.append(
                {
                    "severity": "error",
                    "code": "invalid_external_reference_candidates",
                    "message": "external_reference_candidates must be a list",
                }
            )
    references = manifest.get("copied_references", [])
    if not isinstance(references, list):
        findings.append({"severity": "error", "code": "invalid_copied_references", "message": "copied_references must be a list"})
        return
    if references:
        findings.append(
            {
                "severity": "error",
                "code": "external_skill_references_copied",
                "message": "external skill reference documents must not be copied into DiaEvo candidate skills",
            }
        )
    root = skill_dir.resolve(strict=False)
    for item in references:
        if not isinstance(item, dict):
            findings.append({"severity": "error", "code": "invalid_reference_entry", "message": "copied reference entry must be an object"})
            continue
        rel = str(item.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            findings.append({"severity": "error", "code": "empty_reference_path", "message": "copied reference path is empty"})
            continue
        target = (skill_dir / rel).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            findings.append({"severity": "error", "code": "reference_outside_skill_dir", "message": f"reference path escapes skill dir: {rel}"})
            continue
        if not target.exists() or not target.is_file():
            findings.append({"severity": "error", "code": "missing_copied_reference", "message": f"copied reference is missing: {rel}"})


def _extend_code_artifact_findings(skill_dir: Path, findings: list[dict[str, str]]) -> None:
    artifact_path = skill_dir / "code_artifacts.json"
    if not artifact_path.exists():
        return
    artifacts = read_json(artifact_path, default={})
    if not isinstance(artifacts, dict):
        findings.append({"severity": "error", "code": "invalid_code_artifacts", "message": "code_artifacts.json must be an object"})
        return
    if artifacts.get("schema") != "diaevo.code_backed_skill.v1":
        findings.append({"severity": "error", "code": "invalid_code_artifacts_schema", "message": "code_artifacts.json schema is invalid"})
    review_status = str(artifacts.get("review_status") or "pending").lower()
    if review_status not in SCRIPT_REVIEW_STATUSES:
        findings.append({"severity": "error", "code": "invalid_script_review_status", "message": f"unknown script review status: {review_status}"})
    elif review_status != "approved":
        findings.append({"severity": "warning", "code": "script_not_approved", "message": f"script review status is `{review_status}`; skill should use SKILL.md fallback"})
    fallback_mode = str(artifacts.get("fallback_mode") or "skill_md")
    if fallback_mode != "skill_md":
        findings.append({"severity": "warning", "code": "unsupported_script_fallback", "message": "fallback_mode should be `skill_md`"})
    entrypoint = str(artifacts.get("entrypoint") or "").replace("\\", "/").strip("/")
    if entrypoint not in ALLOWED_CODE_ARTIFACT_ENTRYPOINTS:
        findings.append({"severity": "error", "code": "unsupported_code_entrypoint", "message": f"unsupported helper entrypoint: {entrypoint}"})
        return
    helper_path = skill_dir / entrypoint
    if not helper_path.exists():
        findings.append({"severity": "error", "code": "missing_code_entrypoint", "message": f"helper code not found: {entrypoint}"})
        return
    helper_text = helper_path.read_text(encoding="utf-8", errors="replace")
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, helper_text, flags=re.IGNORECASE | re.DOTALL):
            findings.append({"severity": "error", "code": "dangerous_helper_code", "message": f"helper matched dangerous pattern: {pattern}"})
    for pattern in CREDENTIAL_PATTERNS:
        if re.search(pattern, helper_text, flags=re.IGNORECASE):
            findings.append({"severity": "error", "code": "credential_helper_code", "message": f"helper may contain credential material: {pattern}"})
    forbidden_imports = [r"\bsubprocess\b", r"\bos\.system\b", r"\bsocket\b", r"\burllib\b", r"\brequests\b"]
    for pattern in forbidden_imports:
        if re.search(pattern, helper_text, flags=re.IGNORECASE):
            findings.append({"severity": "error", "code": "forbidden_helper_capability", "message": f"helper uses forbidden capability: {pattern}"})


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
