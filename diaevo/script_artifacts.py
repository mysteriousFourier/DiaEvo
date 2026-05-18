from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import WORKSPACE_ROOT
from .storage import read_json, write_json


SCRIPT_REVIEW_STATUSES = {"pending", "approved", "rejected"}
SCRIPT_ENTRYPOINT = "scripts/skill_flow.py"
SCRIPT_FALLBACK_MODE = "skill_md"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_skill_dir(value: str | Path) -> Path:
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


def load_code_artifacts(skill_dir: str | Path) -> dict[str, Any]:
    target = Path(skill_dir)
    if target.is_file():
        target = target.parent
    value = read_json(target / "code_artifacts.json", default={})
    return value if isinstance(value, dict) else {"status": "invalid"}


def write_code_artifacts(skill_dir: str | Path, artifacts: dict[str, Any]) -> None:
    target = Path(skill_dir)
    if target.is_file():
        target = target.parent
    write_json(target / "code_artifacts.json", artifacts)


def script_status_from_artifacts(artifacts: dict[str, Any]) -> dict[str, Any]:
    if not artifacts:
        return {
            "available": False,
            "review_status": "missing",
            "entrypoint": "",
            "execution_mode": "skill_md_fallback",
            "fallback_reason": "skill has no reviewed helper script",
            "fallback_mode": SCRIPT_FALLBACK_MODE,
            "last_validation_status": "",
        }
    if artifacts.get("status") == "invalid":
        return {
            "available": False,
            "review_status": "invalid",
            "entrypoint": "",
            "execution_mode": "skill_md_fallback",
            "fallback_reason": "code_artifacts.json is invalid",
            "fallback_mode": SCRIPT_FALLBACK_MODE,
            "last_validation_status": "",
        }
    entrypoint = str(artifacts.get("entrypoint") or "").replace("\\", "/").strip("/")
    review_status = str(artifacts.get("review_status") or artifacts.get("status") or "pending").lower()
    validation_status = str(artifacts.get("last_validation_status") or "").lower()
    if review_status not in SCRIPT_REVIEW_STATUSES:
        review_status = "invalid"
    available = review_status == "approved" and validation_status in {"passed", "success", "ok"}
    if available:
        reason = ""
        execution_mode = "script"
    elif review_status != "approved":
        reason = f"script review status is {review_status}"
        execution_mode = "skill_md_fallback"
    elif validation_status not in {"passed", "success", "ok"}:
        reason = f"script validation status is {validation_status or 'unknown'}"
        execution_mode = "skill_md_fallback"
    else:
        reason = "script is not available"
        execution_mode = "skill_md_fallback"
    return {
        "available": available,
        "review_status": review_status,
        "entrypoint": entrypoint,
        "execution_mode": execution_mode,
        "fallback_reason": reason,
        "fallback_mode": str(artifacts.get("fallback_mode") or SCRIPT_FALLBACK_MODE),
        "last_validation_status": validation_status,
    }


def script_status_for_skill_dir(skill_dir: str | Path) -> dict[str, Any]:
    artifacts = load_code_artifacts(skill_dir)
    summary = script_status_from_artifacts(artifacts)
    entrypoint = summary.get("entrypoint")
    if entrypoint:
        target = Path(skill_dir) / str(entrypoint)
        if not target.exists():
            summary = {**summary, "available": False, "execution_mode": "skill_md_fallback"}
            summary["fallback_reason"] = f"script entrypoint is missing: {entrypoint}"
            if summary.get("review_status") == "approved":
                summary["review_status"] = "invalid"
    return summary


def update_validation_summary(skill_dir: str | Path, result: dict[str, Any]) -> None:
    artifacts = load_code_artifacts(skill_dir)
    if not artifacts or artifacts.get("status") == "invalid":
        return
    artifacts["last_validation_status"] = str(result.get("status") or "")
    if result.get("sandbox_report_path"):
        artifacts["last_sandbox_report_path"] = str(result["sandbox_report_path"])
    artifacts["last_validated_at"] = str(result.get("updated_at") or now_iso())
    write_code_artifacts(skill_dir, artifacts)


def review_script(
    skill: str | Path,
    *,
    status: str,
    note: str = "",
    reviewer: str = "",
    approve: bool = False,
) -> dict[str, Any]:
    skill_dir = resolve_skill_dir(skill)
    artifacts_path = skill_dir / "code_artifacts.json"
    artifacts = load_code_artifacts(skill_dir)
    normalized = status.strip().lower()
    if normalized not in SCRIPT_REVIEW_STATUSES:
        raise ValueError(f"unknown script review status: {status}")
    if not artifacts:
        raise ValueError(f"code_artifacts.json not found: {artifacts_path}")
    preview = {
        "review_status": normalized,
        "reviewer": reviewer,
        "note": note,
        "artifacts_path": str(artifacts_path),
    }
    if not approve:
        return {
            "status": "requires_approval",
            "approval_required": True,
            "approved": False,
            "skill_dir": str(skill_dir),
            "preview": preview,
        }
    artifacts["review_status"] = normalized
    artifacts["reviewed_at"] = now_iso()
    if reviewer:
        artifacts["reviewer"] = reviewer
    if note:
        notes = artifacts.get("review_notes")
        if not isinstance(notes, list):
            notes = []
        notes.append({"note": note, "reviewer": reviewer, "recorded_at": now_iso()})
        artifacts["review_notes"] = notes
    artifacts.setdefault("fallback_mode", SCRIPT_FALLBACK_MODE)
    write_code_artifacts(skill_dir, artifacts)
    return {
        "status": "reviewed",
        "approval_required": True,
        "approved": True,
        "skill_dir": str(skill_dir),
        "artifacts_path": str(artifacts_path),
        "script": script_status_for_skill_dir(skill_dir),
    }
