from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evolution import record_promotion_feedback
from .paths import DATA_DIR, PROJECT_ROOT, REPORTS_DIR, ensure_project_dirs
from .quality import collect_skill_texts, nearest_duplicate
from .storage import read_json, write_json
from .verifier import parse_frontmatter, verify_skill


PROMOTION_QUEUE_PATH = REPORTS_DIR / "promotion_queue.json"
PROMOTION_LABELS = {"accepted", "rejected", "merge-needed", "too-broad", "duplicate", "unsafe"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _load_queue() -> dict[str, Any]:
    value = read_json(PROMOTION_QUEUE_PATH, default={})
    if isinstance(value, list):
        return {"items": value}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("items", [])
    return value


def _write_queue(queue: dict[str, Any]) -> None:
    queue["updated_at"] = _now()
    write_json(PROMOTION_QUEUE_PATH, queue)


def _entry_id(skill_path: Path) -> str:
    return hashlib.sha1(str(skill_path.resolve(strict=False)).encode("utf-8")).hexdigest()[:12]


def _validation_result(skill_dir: Path) -> dict[str, Any]:
    value = read_json(skill_dir / "validation.json", default={})
    return value if isinstance(value, dict) else {}


def _duplicate_against_known(text: str, skill_path: Path) -> dict[str, Any]:
    return nearest_duplicate(text, collect_skill_texts(exclude_paths=[skill_path]))


def _recommended_action(verify_result: dict[str, Any], validation: dict[str, Any], duplicate: dict[str, Any]) -> str:
    if not verify_result.get("passed"):
        return "needs_verification_fix"
    duplicate_action = str(duplicate.get("recommended_action") or "")
    if duplicate_action == "reject_duplicate":
        return "reject_duplicate"
    if duplicate_action in {"merge", "specialize"}:
        return duplicate_action
    status = str(validation.get("status") or "").lower()
    if status and status not in {"passed", "success", "ok"}:
        return "needs_validation"
    if not status:
        return "needs_validation"
    return "ready_for_manual_promotion"


def _promotion_report(
    *,
    name: str,
    source_cluster: str,
    verify_result: dict[str, Any],
    validation: dict[str, Any],
    duplicate: dict[str, Any],
    recommended_action: str,
) -> dict[str, Any]:
    section_review = duplicate.get("section_review", {}) if isinstance(duplicate.get("section_review"), dict) else {}
    next_steps = []
    if recommended_action == "ready_for_manual_promotion":
        next_steps.append("Reviewer may run `promote --approve` if the candidate scope is acceptable.")
    elif recommended_action == "needs_validation":
        next_steps.append("Run or update validation before promotion can proceed.")
    elif recommended_action == "needs_verification_fix":
        next_steps.append("Fix verifier errors before queue review.")
    elif recommended_action == "specialize":
        next_steps.append("Apply the section review specialization proposal before promotion.")
    elif recommended_action == "merge":
        next_steps.append("Review the section merge proposal and merge with the nearest skill instead of promoting separately.")
    elif recommended_action == "reject_duplicate":
        next_steps.append("Reject as duplicate unless a reviewer identifies a narrower non-overlapping scope.")
    return {
        "candidate": {
            "name": name,
            "source_cluster": source_cluster,
            "kind": "local-evolved-or-generated",
        },
        "comparison": {
            "seed": "available in `evaluate --variant evolved` baseline_vs_evolved_candidate_eval",
            "local_evolved": "this queued candidate",
            "gepa": "not implemented in Phase 2",
        },
        "gate_summary": {
            "verifier_passed": bool(verify_result.get("passed")),
            "verifier_errors": int(verify_result.get("error_count", 0) or 0),
            "verifier_warnings": int(verify_result.get("warning_count", 0) or 0),
            "validation_status": validation.get("status") or "",
            "duplicate_action": duplicate.get("recommended_action", "keep"),
            "duplicate_similarity": duplicate.get("similarity", 0.0),
        },
        "section_review": section_review,
        "recommended_action": recommended_action,
        "review_labels_allowed": sorted(PROMOTION_LABELS),
        "next_steps": next_steps,
    }


def _label_state(labels: list[str] | None = None) -> dict[str, bool]:
    selected = {str(label).strip().lower() for label in labels or [] if str(label).strip()}
    unknown = sorted(selected - PROMOTION_LABELS)
    if unknown:
        raise ValueError(f"unknown promotion label(s): {', '.join(unknown)}")
    return {label: label in selected for label in sorted(PROMOTION_LABELS)}


def _active_labels(entry: dict[str, Any]) -> list[str]:
    labels = entry.get("review_labels")
    if not isinstance(labels, dict):
        return []
    return sorted(label for label, enabled in labels.items() if enabled)


def _apply_labels(entry: dict[str, Any], labels: list[str], *, note: str = "", reviewer: str = "") -> dict[str, Any]:
    existing = set(_active_labels(entry))
    selected = existing | {str(label).strip().lower() for label in labels if str(label).strip()}
    entry["review_labels"] = _label_state(sorted(selected))
    entry["reviewed_at"] = _now()
    if reviewer:
        entry["reviewer"] = reviewer
    if note:
        entry.setdefault("review_notes", [])
        if isinstance(entry["review_notes"], list):
            entry["review_notes"].append({"note": note, "recorded_at": _now(), "reviewer": reviewer})
    if selected.intersection({"rejected", "unsafe", "duplicate", "too-broad"}):
        entry["state"] = "rejected"
    elif "accepted" in selected:
        entry["state"] = "approved"
    return entry


def queue_promotion(skill: str | Path) -> dict[str, Any]:
    ensure_project_dirs()
    skill_dir = _skill_dir(skill)
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        raise ValueError(f"SKILL.md not found: {skill_path}")
    text = skill_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    verify_result = verify_skill(skill_dir, write_report=False)
    validation = _validation_result(skill_dir)
    duplicate = _duplicate_against_known(text, skill_path)
    recommended_action = _recommended_action(verify_result, validation, duplicate)
    queue = _load_queue()
    items = [item for item in queue.get("items", []) if isinstance(item, dict)]
    entry_id = _entry_id(skill_path)
    entry = {
        "id": entry_id,
        "skill_dir": str(skill_dir),
        "skill_path": str(skill_path),
        "name": meta.get("name") or skill_dir.name,
        "description": meta.get("description") or "",
        "source_cluster": meta.get("source_cluster") or "",
        "state": "queued",
        "recommended_action": recommended_action,
        "queued_at": _now(),
        "review_labels": _label_state(),
        "review_notes": [],
        "verifier": {
            "passed": bool(verify_result.get("passed")),
            "risk_score": verify_result.get("risk_score"),
            "error_count": verify_result.get("error_count"),
            "warning_count": verify_result.get("warning_count"),
            "findings": verify_result.get("findings", []),
        },
        "validation": {
            "status": validation.get("status") or "",
            "approved": bool(validation.get("approved", False)),
            "command_count": len(validation.get("commands", [])) if isinstance(validation.get("commands"), list) else 0,
        },
        "duplicate": {
            "similarity": duplicate.get("similarity", 0.0),
            "nearest": duplicate.get("nearest", ""),
            "nearest_source": duplicate.get("nearest_source", ""),
            "nearest_path": duplicate.get("nearest_path", ""),
            "recommended_action": duplicate.get("recommended_action", "keep"),
            "reason": duplicate.get("reason", ""),
            "section_review": duplicate.get("section_review", {}),
        },
        "promotion_report": _promotion_report(
            name=meta.get("name") or skill_dir.name,
            source_cluster=meta.get("source_cluster") or "",
            verify_result=verify_result,
            validation=validation,
            duplicate=duplicate,
            recommended_action=recommended_action,
        ),
    }
    replaced = False
    for index, item in enumerate(items):
        if item.get("id") == entry_id:
            entry["queued_at"] = item.get("queued_at") or entry["queued_at"]
            entry["state"] = item.get("state") if item.get("state") in {"approved", "rejected"} else "queued"
            entry["review_labels"] = item.get("review_labels") if isinstance(item.get("review_labels"), dict) else entry["review_labels"]
            entry["review_notes"] = item.get("review_notes") if isinstance(item.get("review_notes"), list) else []
            if item.get("reviewed_at"):
                entry["reviewed_at"] = item.get("reviewed_at")
            if item.get("reviewer"):
                entry["reviewer"] = item.get("reviewer")
            items[index] = entry
            replaced = True
            break
    if not replaced:
        items.append(entry)
    queue["items"] = items
    _write_queue(queue)
    record_promotion_feedback(entry)
    return {"status": "ok", "queue_id": entry_id, "entry": entry, "queue_path": str(PROMOTION_QUEUE_PATH)}


def label_promotion(
    queue_id: str,
    *,
    labels: list[str],
    note: str = "",
    reviewer: str = "",
) -> dict[str, Any]:
    ensure_project_dirs()
    queue = _load_queue()
    items = [item for item in queue.get("items", []) if isinstance(item, dict)]
    for index, item in enumerate(items):
        if item.get("id") != queue_id:
            continue
        updated = _apply_labels(dict(item), labels, note=note, reviewer=reviewer)
        items[index] = updated
        queue["items"] = items
        _write_queue(queue)
        record_promotion_feedback(updated)
        return {
            "status": "labeled",
            "queue_id": queue_id,
            "labels": _active_labels(updated),
            "entry": updated,
            "queue_path": str(PROMOTION_QUEUE_PATH),
        }
    raise ValueError(f"promotion queue id not found: {queue_id}")


def _registry_values(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path else DATA_DIR / "skill_registry.json"
    values = read_json(target, default=[])
    if not isinstance(values, list):
        raise ValueError(f"Skill registry must be a JSON list: {target}")
    return [dict(item) for item in values if isinstance(item, dict)]


def promote(queue_id: str, *, approve: bool = False, registry_path: str | Path | None = None) -> dict[str, Any]:
    ensure_project_dirs()
    queue = _load_queue()
    items = [item for item in queue.get("items", []) if isinstance(item, dict)]
    entry = next((item for item in items if item.get("id") == queue_id), None)
    if entry is None:
        raise ValueError(f"promotion queue id not found: {queue_id}")
    if not approve:
        return {
            "status": "requires_approval",
            "queue_id": queue_id,
            "approval_required": True,
            "entry": entry,
        }
    blocking_labels = set(_active_labels(entry)).intersection({"rejected", "unsafe", "duplicate", "too-broad"})
    if blocking_labels:
        return {
            "status": "blocked",
            "queue_id": queue_id,
            "message": f"entry has blocking review labels: {', '.join(sorted(blocking_labels))}",
            "entry": entry,
        }
    if entry.get("recommended_action") != "ready_for_manual_promotion":
        return {
            "status": "blocked",
            "queue_id": queue_id,
            "message": f"entry is not ready for promotion: {entry.get('recommended_action')}",
            "entry": entry,
        }
    skill_path = Path(str(entry.get("skill_path")))
    text = skill_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    registry_target = Path(registry_path) if registry_path else DATA_DIR / "skill_registry.json"
    registry = _registry_values(registry_target)
    name = meta.get("name") or str(entry.get("name") or skill_path.parent.name)
    record = {
        "name": name,
        "description": meta.get("description") or str(entry.get("description") or ""),
        "tags": _parse_tags(meta.get("tags") or ""),
        "path": str(skill_path.parent),
        "permissions": ["workspace-read"],
        "usage_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "last_used": "",
        "risk": float(entry.get("verifier", {}).get("risk_score") or 0.2),
        "cost": 0.25,
        "source": "generated-candidate",
        "installed": False,
    }
    replaced = False
    for index, item in enumerate(registry):
        if item.get("name") == name:
            registry[index] = {**item, **record}
            replaced = True
            break
    if not replaced:
        registry.append(record)
    write_json(registry_target, registry)
    for item in items:
        if item.get("id") == queue_id:
            _apply_labels(item, ["accepted"])
            item["approved_at"] = _now()
            item["registry_path"] = str(registry_target)
    queue["items"] = items
    _write_queue(queue)
    updated_entry = next((item for item in items if item.get("id") == queue_id), entry)
    record_promotion_feedback(updated_entry)
    return {
        "status": "promoted",
        "queue_id": queue_id,
        "registry_path": str(registry_target),
        "record": record,
        "labels": _active_labels(updated_entry),
    }


def _parse_tags(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip('"').strip("'") for item in text.split(",") if item.strip()]
