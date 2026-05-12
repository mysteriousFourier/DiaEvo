from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .features import FeatureStore, cosine, tokenize
from .generator import slugify
from .miner import mine
from .paths import CANDIDATE_SKILLS_DIR, DATA_DIR, REPORTS_DIR, ensure_project_dirs
from .quality import SkillText, collect_skill_texts, extract_skill_sections, nearest_duplicate
from .storage import read_json, write_json
from .verifier import verify_skill


SECTION_KEYS = (
    "when_to_use",
    "trigger_signals",
    "operating_steps",
    "failure_fallbacks",
    "verification_suggestions",
    "safety_constraints",
)

SECTION_TITLES = {
    "when_to_use": "When To Use",
    "trigger_signals": "Trigger Signals",
    "operating_steps": "Operating Steps",
    "failure_fallbacks": "Failure Fallbacks",
    "verification_suggestions": "Verification Suggestions",
    "safety_constraints": "Safety Constraints",
}

MEMORY_PATH = DATA_DIR / "evolution_memory.json"
EVOLUTION_REPORT_PATH = REPORTS_DIR / "evolution_report.json"

HARD_REJECT_CODES = {"dangerous_command", "credential_pattern", "missing_required_section"}
MAX_CANDIDATE_CHARS = 8_000


@dataclass(slots=True)
class CandidateEval:
    candidate_id: str
    score: float
    scores: dict[str, float]
    passed: bool
    rejected: bool
    rejection_reason: str
    warning_count: int
    error_count: int
    duplicate_similarity: float
    length: int
    findings: list[dict[str, Any]]
    side_info: dict[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "score": round(self.score, 4),
            "scores": {key: round(value, 4) for key, value in self.scores.items()},
            "passed": self.passed,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "duplicate_similarity": round(self.duplicate_similarity, 4),
            "length": self.length,
            "findings": self.findings,
            "side_info": self.side_info,
        }


def _cluster_lookup(report: dict[str, Any], cluster_id: str) -> dict[str, Any]:
    wanted = cluster_id.upper()
    for cluster in report.get("clusters", []):
        if str(cluster.get("id") or "").upper() == wanted:
            return cluster
    raise ValueError(f"Cluster not found in mining report: {cluster_id}")


def _load_mining_report() -> dict[str, Any]:
    report = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    if report.get("clusters"):
        return report
    return mine()


def _as_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if str(item)]
    text = str(value or "").strip()
    return [text] if text else []


def _bullets(values: list[str], empty: str) -> str:
    if not values:
        return f"- {empty}"
    return "\n".join(f"- `{value}`" for value in values)


def _numbered(values: list[str]) -> str:
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def _cluster_signal_text(cluster: dict[str, Any]) -> str:
    values = [
        str(cluster.get("representative_task") or ""),
        " ".join(_as_strings(cluster.get("top_terms"))),
        " ".join(_as_strings(cluster.get("top_tools"))),
        " ".join(_as_strings(cluster.get("top_errors"))),
        " ".join(_as_strings(cluster.get("top_failure_types"))),
    ]
    return " ".join(value for value in values if value)


def _task_family(cluster: dict[str, Any] | None = None, *, text: str = "") -> str:
    source = " ".join(_as_strings((cluster or {}).get("top_terms"))[:3]) if cluster else text
    tokens = tokenize(source)
    return "-".join(tokens[:3]) or "general"


def _tool_path_from_cluster(cluster: dict[str, Any]) -> list[str]:
    return _as_strings(cluster.get("top_tools"))[:8]


def _failure_types_from_cluster(cluster: dict[str, Any]) -> list[str]:
    return [*_as_strings(cluster.get("top_errors"))[:6], *_as_strings(cluster.get("top_failure_types"))[:6]]


def _load_memory(path: str | Path | None = None) -> dict[str, Any]:
    memory = read_json(Path(path) if path else MEMORY_PATH, default={})
    if not isinstance(memory, dict):
        memory = {}
    memory.setdefault("correct_templates", [])
    memory.setdefault("error_patterns", [])
    memory.setdefault("validation_patterns", [])
    memory.setdefault("duplicate_patterns", [])
    memory.setdefault("promotion_patterns", [])
    return memory


def _write_memory(memory: dict[str, Any], path: str | Path | None = None) -> None:
    write_json(Path(path) if path else MEMORY_PATH, memory)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_memory(memory: dict[str, Any]) -> dict[str, Any]:
    memory["correct_templates"] = memory.get("correct_templates", [])[-100:]
    memory["error_patterns"] = memory.get("error_patterns", [])[-200:]
    memory["validation_patterns"] = memory.get("validation_patterns", [])[-200:]
    memory["duplicate_patterns"] = memory.get("duplicate_patterns", [])[-200:]
    memory["promotion_patterns"] = memory.get("promotion_patterns", [])[-200:]
    return memory


def _validation_failure_category(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").lower()
    stderr = str(item.get("stderr") or "").lower()
    stdout = str(item.get("stdout") or "").lower()
    command = str(item.get("command") or "").lower()
    text = " ".join([status, stderr, stdout, command])
    if status == "timeout":
        return "timeout"
    if "modulenotfounderror" in text or "importerror" in text:
        return "missing-import"
    if "assert" in text or "failed" in text:
        return "test-failure"
    if "permission" in text or "approval" in text:
        return "approval-or-permission"
    if "network" in text or "http" in text or "curl" in text:
        return "network"
    if status and status != "passed":
        return status
    return "passed"


def record_validation_feedback(result: dict[str, Any], memory_path: str | Path | None = None) -> None:
    memory = _load_memory(memory_path)
    skill_dir = str(result.get("skill_dir") or "")
    command_results = result.get("results", [])
    if not isinstance(command_results, list):
        command_results = []
    if command_results:
        for item in command_results:
            if not isinstance(item, dict):
                continue
            memory["validation_patterns"].append(
                {
                    "schema": "validation_feedback.v2",
                    "skill_dir": skill_dir,
                    "status": item.get("status"),
                    "command": item.get("command"),
                    "returncode": item.get("returncode"),
                    "failure_category": _validation_failure_category(item),
                    "stdout_summary": str(item.get("stdout") or "")[:500],
                    "stderr_summary": str(item.get("stderr") or "")[:500],
                    "recorded_at": _now(),
                }
            )
    else:
        memory["validation_patterns"].append(
            {
                "schema": "validation_feedback.v2",
                "skill_dir": skill_dir,
                "status": result.get("status"),
                "command": "",
                "returncode": None,
                "failure_category": str(result.get("status") or "unknown"),
                "findings": result.get("findings", []),
                "recorded_at": _now(),
            }
        )
    _write_memory(_trim_memory(memory), memory_path)


def record_promotion_feedback(entry: dict[str, Any], memory_path: str | Path | None = None) -> None:
    memory = _load_memory(memory_path)
    duplicate = entry.get("duplicate") if isinstance(entry.get("duplicate"), dict) else {}
    labels = entry.get("review_labels") if isinstance(entry.get("review_labels"), dict) else {}
    active_labels = sorted(str(label) for label, enabled in labels.items() if enabled)
    validation = entry.get("validation") if isinstance(entry.get("validation"), dict) else {}
    verifier = entry.get("verifier") if isinstance(entry.get("verifier"), dict) else {}
    memory["promotion_patterns"].append(
        {
            "schema": "promotion_feedback.v2",
            "queue_id": entry.get("id"),
            "skill_dir": entry.get("skill_dir"),
            "source_cluster": entry.get("source_cluster"),
            "recommended_action": entry.get("recommended_action"),
            "validation_status": validation.get("status") or "",
            "promotion_outcome": str(entry.get("state") or ""),
            "labels": active_labels,
            "verifier_passed": verifier.get("passed") if isinstance(verifier, dict) else False,
            "duplicate_similarity": duplicate.get("similarity"),
            "duplicate_nearest": duplicate.get("nearest"),
            "duplicate_action": duplicate.get("recommended_action"),
            "section_review": duplicate.get("section_review", {}),
            "promotion_report": entry.get("promotion_report", {}),
            "recorded_at": _now(),
        }
    )
    if "accepted" in active_labels and verifier.get("passed"):
        section_review = duplicate.get("section_review", {}) if isinstance(duplicate.get("section_review"), dict) else {}
        evidence = section_review.get("evidence", {}) if isinstance(section_review.get("evidence"), dict) else {}
        memory["correct_templates"].append(
            {
                "schema": "correct_template.v2",
                "cluster_id": entry.get("source_cluster") or "",
                "task_family": _task_family(text=str(entry.get("description") or entry.get("name") or "")),
                "tool_path": _safe_list(evidence.get("candidate_tools")),
                "failure_types": [],
                "validation_status": validation.get("status") or "",
                "promotion_outcome": str(entry.get("state") or ""),
                "labels": active_labels,
                "query": " ".join(
                    str(value)
                    for value in [entry.get("name"), entry.get("description"), entry.get("source_cluster")]
                    if value
                ),
                "summary": f"promotion accepted for {entry.get('name') or entry.get('id')}",
                "candidate_digest": "",
                "scores": {},
            }
        )
    if duplicate and duplicate.get("recommended_action") != "keep":
        memory["duplicate_patterns"].append(
            {
                "schema": "duplicate_feedback.v2",
                "cluster_id": entry.get("source_cluster"),
                "candidate_id": entry.get("name"),
                "similarity": duplicate.get("similarity"),
                "nearest": duplicate.get("nearest"),
                "nearest_source": duplicate.get("nearest_source"),
                "recommended_action": duplicate.get("recommended_action"),
                "reason": duplicate.get("reason"),
                "section_review": duplicate.get("section_review", {}),
            }
        )
    _write_memory(_trim_memory(memory), memory_path)


def _memory_query_text(item: dict[str, Any]) -> str:
    fields = [
        item.get("query"),
        item.get("summary"),
        item.get("task_family"),
        " ".join(_safe_list(item.get("tool_path"))),
        " ".join(_safe_list(item.get("failure_types"))),
        item.get("validation_status"),
        item.get("promotion_outcome"),
        " ".join(_safe_list(item.get("labels"))),
        item.get("recommended_action"),
        item.get("failure_category"),
    ]
    return " ".join(str(value) for value in fields if value)


def _memory_matches(cluster: dict[str, Any], memory: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    templates = [item for item in memory.get("correct_templates", []) if isinstance(item, dict)]
    patterns = [
        item
        for key in ("validation_patterns", "duplicate_patterns", "promotion_patterns", "error_patterns")
        for item in memory.get(key, [])
        if isinstance(item, dict)
    ]
    entries = templates + patterns
    if not entries:
        return []
    query_text = " ".join(
        [
            _cluster_signal_text(cluster),
            _task_family(cluster),
            " ".join(_tool_path_from_cluster(cluster)),
            " ".join(_failure_types_from_cluster(cluster)),
        ]
    )
    documents = [query_text] + [_memory_query_text(item) for item in entries]
    store = FeatureStore.from_documents(documents)
    query = store.vectors[0]
    ranked = sorted(
        ((entries[index - 1], cosine(query, store.vectors[index])) for index in range(1, len(documents))),
        key=lambda item: item[1],
        reverse=True,
    )
    return [
        {**item, "similarity": round(score, 4)}
        for item, score in ranked[:limit]
        if score > 0.05
    ]


def memory_summary(memory_path: str | Path | None = None) -> dict[str, Any]:
    memory = _load_memory(memory_path)
    validation_status: dict[str, int] = {}
    validation_failures: dict[str, int] = {}
    duplicate_actions: dict[str, int] = {}
    promotion_outcomes: dict[str, int] = {}
    promotion_labels: dict[str, int] = {}
    for item in memory.get("validation_patterns", []):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        validation_status[status] = validation_status.get(status, 0) + 1
        category = str(item.get("failure_category") or "")
        if category:
            validation_failures[category] = validation_failures.get(category, 0) + 1
    for item in memory.get("duplicate_patterns", []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("recommended_action") or "unknown")
        duplicate_actions[action] = duplicate_actions.get(action, 0) + 1
    for item in memory.get("promotion_patterns", []):
        if not isinstance(item, dict):
            continue
        outcome = str(item.get("promotion_outcome") or item.get("state") or "unknown")
        promotion_outcomes[outcome] = promotion_outcomes.get(outcome, 0) + 1
        for label in _safe_list(item.get("labels")):
            promotion_labels[label] = promotion_labels.get(label, 0) + 1
    return {
        "counts": {
            "correct_templates": len([item for item in memory.get("correct_templates", []) if isinstance(item, dict)]),
            "error_patterns": len([item for item in memory.get("error_patterns", []) if isinstance(item, dict)]),
            "validation_patterns": len([item for item in memory.get("validation_patterns", []) if isinstance(item, dict)]),
            "duplicate_patterns": len([item for item in memory.get("duplicate_patterns", []) if isinstance(item, dict)]),
            "promotion_patterns": len([item for item in memory.get("promotion_patterns", []) if isinstance(item, dict)]),
        },
        "validation_status": dict(sorted(validation_status.items())),
        "validation_failure_categories": dict(sorted(validation_failures.items())),
        "duplicate_actions": dict(sorted(duplicate_actions.items())),
        "promotion_outcomes": dict(sorted(promotion_outcomes.items())),
        "promotion_labels": dict(sorted(promotion_labels.items())),
    }


def _seed_candidate(cluster: dict[str, Any], memory_matches: list[dict[str, Any]] | None = None) -> dict[str, str]:
    representative = str(cluster.get("representative_task") or "the mined task cluster")
    terms = _as_strings(cluster.get("top_terms"))
    tools = _as_strings(cluster.get("top_tools"))
    errors = _as_strings(cluster.get("top_errors"))
    failure_types = _as_strings(cluster.get("top_failure_types"))
    extensions = _as_strings(cluster.get("file_extensions"))
    failures = [*errors, *failure_types]
    memory_lines = [
        f"- Reuse prior successful template `{item.get('cluster_id', 'unknown')}`: {item.get('summary', '')}"
        for item in (memory_matches or [])[:2]
        if item.get("summary") and str(item.get("schema", "correct_template")).startswith("correct")
    ]
    warning_lines = [
        f"- Avoid repeated `{item.get('schema', 'memory')}` pattern: {item.get('failure_category') or item.get('recommended_action') or item.get('code') or item.get('promotion_outcome')}"
        for item in (memory_matches or [])[:3]
        if item.get("schema") and not str(item.get("schema", "")).startswith("correct")
    ]
    steps = [
        "Inspect only the files and commands that match the mined trigger signals.",
        "Reproduce the smallest failing or repeated workflow before making changes.",
    ]
    if tools:
        steps.append("Prefer the mined tool path before adding new tools: " + " -> ".join(f"`{tool}`" for tool in tools[:6]) + ".")
    if failures:
        steps.append("If the task shows " + ", ".join(f"`{item}`" for item in failures[:5]) + ", capture that failure before editing.")
    steps.extend(
        [
            "Make the narrowest workspace-scoped change or recommendation that addresses the evidence.",
            "Run the closest safe validation and record the result for the next feedback cycle.",
        ]
    )
    return {
        "when_to_use": "\n".join(
            [
                f"Use this skill when a task is similar to `{representative}`.",
                "The task should share at least two mined signals from the trigger list before this skill is applied.",
                "Keep the skill as a generated draft until verification, validation, and human promotion approval all pass.",
            ]
        ),
        "trigger_signals": "\n".join(
            [
                "Task terms:",
                _bullets(terms[:7], "No recurring task terms were mined."),
                "",
                "Files or extensions:",
                _bullets([f".{value}" for value in extensions[:5]], "No recurring file extension signal was mined."),
                "",
                "Tools and failures:",
                _bullets([*tools[:6], *failures[:6]], "No recurring tool or failure signal was mined."),
                *([] if not memory_lines else ["", "Prior successful templates:", *memory_lines]),
                *([] if not warning_lines else ["", "Memory cautions:", *warning_lines]),
            ]
        ),
        "operating_steps": _numbered(steps),
        "failure_fallbacks": "\n".join(
            [
                "- If validation fails, capture the command, exit code, and failure category before retrying.",
                "- If the same validation fails twice, stop broadening the change and summarize the unresolved failure.",
                "- If a tool or command requires approval, stop at preview and request explicit human approval.",
                "- If the task drifts outside the mined evidence, fall back to the closest installed skill or ask for review.",
            ]
        ),
        "verification_suggestions": "\n".join(
            [
                "- Run `skillminer verify --skill <candidate-dir>` before queueing promotion.",
                "- Run `skillminer validate --skill <candidate-dir> --approve` only after reviewing `validation.json`.",
                "- Compare the candidate against existing registry skills and merge or specialize if it is near-duplicate.",
                "- After use, run `skillminer feedback` so tool events become future mining evidence.",
            ]
        ),
        "safety_constraints": "\n".join(
            [
                "- Keep all file reads and writes inside the active workspace.",
                "- Do not install dependencies, use network access, or run shell commands without explicit approval.",
                "- Do not include real credentials, tokens, passwords, or private project secrets.",
                "- Do not auto-promote or auto-install this generated candidate.",
            ]
        ),
    }


def _candidate_variants(seed: dict[str, str], cluster: dict[str, Any], budget: int) -> list[tuple[str, dict[str, str]]]:
    variants: list[tuple[str, dict[str, str]]] = [("seed", dict(seed))]
    terms = _as_strings(cluster.get("top_terms"))
    tools = _as_strings(cluster.get("top_tools"))
    failures = [*_as_strings(cluster.get("top_errors")), *_as_strings(cluster.get("top_failure_types"))]
    if budget <= 1:
        return variants

    evidence_first = dict(seed)
    evidence_first["when_to_use"] += "\n\nUse the trace IDs and cluster metrics below as grounding; do not invent unsupported tools or files."
    evidence_first["operating_steps"] += "\n" + f"{len(evidence_first['operating_steps'].splitlines()) + 1}. Before changing anything, state which mined signal triggered the skill."
    variants.append(("evidence-first", evidence_first))

    safety_hardened = dict(seed)
    safety_hardened["failure_fallbacks"] += "\n- If a command mentions parent directories, absolute system paths, or remote scripts, reject it and propose a workspace-local alternative."
    safety_hardened["safety_constraints"] += "\n- Treat dependency installation and external network use as separate approval-gated work, never as an implicit skill step."
    variants.append(("safety-hardened", safety_hardened))

    specialized = dict(seed)
    if terms or tools or failures:
        specialized["trigger_signals"] += "\n\nMinimum activation rule:\n- Require one task term and one tool, file, or failure signal before applying this skill."
    if tools:
        specialized["operating_steps"] = _numbered(
            [
                "Confirm the task matches the activation rule.",
                "Inspect the smallest file set indicated by the trace evidence.",
                "Run or emulate the recurring tool path: " + " -> ".join(f"`{tool}`" for tool in tools[:6]) + ".",
                "Apply the narrowest workspace-scoped change.",
                "Record validation output and feed it back into SkillMiner.",
            ]
        )
    variants.append(("specialized", specialized))

    validation_focused = dict(seed)
    validation_focused["verification_suggestions"] += "\n- If `validation.json` has commands, execute them only through the validation runner so output becomes ASI."
    if "pytest" in {value.lower() for value in [*terms, *tools]}:
        validation_focused["verification_suggestions"] += "\n- Prefer focused pytest commands before running the full suite."
    variants.append(("validation-focused", validation_focused))

    compact = {key: _compact_section(value) for key, value in seed.items()}
    variants.append(("compact", compact))
    return variants[: max(1, budget)]


def _compact_section(text: str, max_lines: int = 12) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:max_lines])


def _frontmatter(cluster: dict[str, Any], variant: str) -> tuple[str, str]:
    cluster_id = str(cluster.get("id") or "C00")
    terms = _as_strings(cluster.get("top_terms"))
    tools = _as_strings(cluster.get("top_tools"))
    name = slugify(f"{cluster_id}-evolved-{'-'.join(terms[:3]) if terms else variant}")
    representative = str(cluster.get("representative_task") or "mined workflow")
    description = f"Evolved trace-grounded workflow for tasks similar to: {representative[:120]}".replace('"', "'")
    tags = [*terms[:5], *tools[:3], "evolved", "candidate"]
    tag_text = "[" + ", ".join(f'"{tag}"' for tag in tags[:10]) + "]"
    risk = min(
        1.0,
        0.18
        + float(cluster.get("failure_rate", 0.0) or 0.0) * 0.25
        + float(cluster.get("coverage_gap", 0.0) or 0.0) * 0.25,
    )
    return name, "\n".join(
        [
            "---",
            f'name: "{name}"',
            f'description: "{description}"',
            f"tags: {tag_text}",
            f'source_cluster: "{cluster_id}"',
            f"risk_score: {risk:.2f}",
            "status: candidate",
            "---",
            "",
        ]
    )


def render_candidate_skill(candidate: dict[str, str], cluster: dict[str, Any], variant: str = "evolved") -> str:
    name, frontmatter = _frontmatter(cluster, variant)
    explanations = cluster.get("explanations", [])
    if not isinstance(explanations, list):
        explanations = []
    evidence_lines = [
        f"- Source cluster: `{cluster.get('id', 'C00')}`",
        f"- Trace ids: `{', '.join(str(item) for item in cluster.get('trace_ids', []))}`",
        f"- Cluster size: `{cluster.get('size', 0)}`",
        f"- Failure rate: `{float(cluster.get('failure_rate', 0.0) or 0.0):.2f}`",
        f"- Coverage gap: `{float(cluster.get('coverage_gap', 0.0) or 0.0):.2f}`",
        f"- Tool success rate: `{float(cluster.get('tool_success_rate', 0.0) or 0.0):.2f}`",
    ]
    for item in explanations[:5]:
        if isinstance(item, dict):
            evidence_lines.append(f"- `{item.get('type', 'signal')}` score `{float(item.get('score') or 0.0):.2f}`: {item.get('reason', '')}")
    lines = [frontmatter, f"# {name}", ""]
    for key in SECTION_KEYS:
        lines.extend([f"## {SECTION_TITLES[key]}", "", candidate.get(key, "").strip(), ""])
        if key == "trigger_signals":
            lines.extend(["## Mined Evidence", "", *evidence_lines, ""])
    return "\n".join(lines).strip() + "\n"


def _known_skill_texts(extra_paths: list[Path] | None = None) -> list[SkillText]:
    return collect_skill_texts(exclude_paths=extra_paths)


def _evidence_alignment(markdown: str, cluster: dict[str, Any]) -> float:
    signals = [
        *_as_strings(cluster.get("top_terms")),
        *_as_strings(cluster.get("top_tools")),
        *_as_strings(cluster.get("top_errors")),
        *_as_strings(cluster.get("top_failure_types")),
    ]
    unique = sorted({signal.lower() for signal in signals if signal})
    if not unique:
        return 0.5
    text = markdown.lower()
    hits = sum(1 for signal in unique if signal in text)
    return hits / len(unique)


def _specificity_score(markdown: str, cluster: dict[str, Any]) -> float:
    tokens = tokenize(markdown)
    if not tokens:
        return 0.0
    evidence = len(set(tokens).intersection(set(tokenize(_cluster_signal_text(cluster)))))
    activation_bonus = 1 if "minimum activation rule" in markdown.lower() else 0
    return min(1.0, (evidence / 18.0) + activation_bonus * 0.15)


def _score_candidate(candidate_id: str, candidate: dict[str, str], cluster: dict[str, Any], known_texts: list[SkillText]) -> tuple[CandidateEval, str]:
    markdown = render_candidate_skill(candidate, cluster, candidate_id)
    duplicate = nearest_duplicate(markdown, known_texts)
    duplicate_similarity = float(duplicate["similarity"])
    duplicate_name = str(duplicate["nearest"])
    with tempfile.TemporaryDirectory(prefix="skillminer-evolution-") as tmp:
        candidate_dir = Path(tmp) / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "SKILL.md").write_text(markdown, encoding="utf-8")
        verify_result = verify_skill(candidate_dir, write_report=False)
    findings = [dict(item) for item in verify_result.get("findings", [])]
    codes = {str(item.get("code")) for item in findings}
    hard_reject = bool(codes.intersection(HARD_REJECT_CODES))
    length = len(markdown)
    length_score = max(0.0, min(1.0, 1.0 - max(0, length - 4_500) / 3_500))
    verifier_score = 1.0 if verify_result.get("passed") else 0.0
    warning_score = max(0.0, 1.0 - float(verify_result.get("warning_count", 0)) * 0.12)
    evidence_score = _evidence_alignment(markdown, cluster)
    duplicate_score = max(0.0, 1.0 - duplicate_similarity)
    specificity = _specificity_score(markdown, cluster)
    safety = 0.0 if hard_reject else max(0.0, 1.0 - float(verify_result.get("risk_score", 0.0) or 0.0))
    score = (
        0.30 * verifier_score
        + 0.16 * warning_score
        + 0.18 * evidence_score
        + 0.13 * duplicate_score
        + 0.12 * specificity
        + 0.11 * safety
        + 0.05 * length_score
    )
    rejected = hard_reject or length > MAX_CANDIDATE_CHARS or duplicate_similarity >= 0.985
    reasons = []
    if hard_reject:
        reasons.append("hard verifier finding")
    if length > MAX_CANDIDATE_CHARS:
        reasons.append("length limit")
    if duplicate_similarity >= 0.985:
        reasons.append(f"near duplicate of {duplicate_name}")
    side_info = {
        "cluster_id": str(cluster.get("id") or ""),
        "representative_task": str(cluster.get("representative_task") or ""),
        "verifier_findings": findings,
        "duplicate_nearest": duplicate_name,
        "duplicate": duplicate,
        "evidence_alignment": round(evidence_score, 4),
        "specificity": round(specificity, 4),
        "section_lengths": {key: len(value) for key, value in extract_skill_sections(markdown).items()},
    }
    return (
        CandidateEval(
            candidate_id=candidate_id,
            score=0.0 if rejected else score,
            scores={
                "verifier": verifier_score,
                "warning_cleanliness": warning_score,
                "evidence_alignment": evidence_score,
                "non_duplicate": duplicate_score,
                "specificity": specificity,
                "safety": safety,
                "length": length_score,
            },
            passed=bool(verify_result.get("passed")),
            rejected=rejected,
            rejection_reason=", ".join(reasons),
            warning_count=int(verify_result.get("warning_count", 0) or 0),
            error_count=int(verify_result.get("error_count", 0) or 0),
            duplicate_similarity=duplicate_similarity,
            length=length,
            findings=findings,
            side_info=side_info,
        ),
        markdown,
    )


def pareto_frontier(evaluations: list[CandidateEval]) -> list[CandidateEval]:
    active = [item for item in evaluations if not item.rejected]
    frontier: list[CandidateEval] = []
    objectives = ("verifier", "evidence_alignment", "non_duplicate", "specificity", "safety", "length")
    for candidate in active:
        dominated = False
        for other in active:
            if other is candidate:
                continue
            better_or_equal = all(other.scores.get(key, 0.0) >= candidate.scores.get(key, 0.0) for key in objectives)
            strictly_better = any(other.scores.get(key, 0.0) > candidate.scores.get(key, 0.0) for key in objectives)
            if better_or_equal and strictly_better and other.score >= candidate.score:
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    return sorted(frontier, key=lambda item: (-item.score, item.candidate_id))


def _validation_suggestion(cluster: dict[str, Any]) -> dict[str, Any]:
    tools = {value.lower() for value in _as_strings(cluster.get("top_tools"))}
    terms = {value.lower() for value in _as_strings(cluster.get("top_terms"))}
    commands: list[str] = []
    if "pytest" in tools or "pytest" in terms or "testing" in terms:
        commands.append("python -m pytest -q")
    return {
        "status": "suggested" if commands else "not_configured",
        "commands": commands,
        "timeout_sec": 60,
        "workspace_only": True,
        "network": False,
        "expected_status": "passed",
    }


def _candidate_digest(markdown: str) -> str:
    return hashlib.sha1(markdown.encode("utf-8")).hexdigest()[:12]


def _write_evolved_candidate(cluster: dict[str, Any], markdown: str, eval_result: CandidateEval, output_dir: str | Path | None = None) -> dict[str, Any]:
    cluster_id = str(cluster.get("id") or "C00").upper()
    target = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / cluster_id / "evolved"
    target.mkdir(parents=True, exist_ok=True)
    skill_path = target / "SKILL.md"
    skill_path.write_text(markdown, encoding="utf-8")
    validation_path = target / "validation.json"
    if not validation_path.exists():
        write_json(validation_path, _validation_suggestion(cluster))
    metadata = {
        "cluster_id": cluster_id,
        "skill_dir": str(target),
        "skill_path": str(skill_path),
        "status": "evolved-candidate",
        "candidate_id": eval_result.candidate_id,
        "candidate_digest": _candidate_digest(markdown),
        "score": round(eval_result.score, 4),
        "scores": {key: round(value, 4) for key, value in eval_result.scores.items()},
        "duplicate_similarity": round(eval_result.duplicate_similarity, 4),
        "duplicate": eval_result.side_info.get("duplicate", {}),
    }
    write_json(target / "metadata.json", metadata)
    return metadata


def _target_skill_path(cluster: dict[str, Any], output_dir: str | Path | None = None) -> Path:
    cluster_id = str(cluster.get("id") or "C00").upper()
    target = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / cluster_id / "evolved"
    return target / "SKILL.md"


def _update_memory(cluster: dict[str, Any], best_eval: CandidateEval, markdown: str, memory_path: str | Path | None = None) -> None:
    memory = _load_memory(memory_path)
    cluster_id = str(cluster.get("id") or "C00")
    if best_eval.passed and not best_eval.rejected:
        memory["correct_templates"].append(
            {
                "schema": "correct_template.v2",
                "cluster_id": cluster_id,
                "task_family": _task_family(cluster),
                "tool_path": _tool_path_from_cluster(cluster),
                "failure_types": _failure_types_from_cluster(cluster),
                "validation_status": "",
                "promotion_outcome": "",
                "query": _cluster_signal_text(cluster),
                "summary": f"{best_eval.candidate_id} passed with score {best_eval.score:.3f}",
                "candidate_digest": _candidate_digest(markdown),
                "scores": {key: round(value, 4) for key, value in best_eval.scores.items()},
                "sections": {key: best_eval.side_info.get("section_lengths", {}).get(key, 0) for key in SECTION_KEYS},
            }
        )
    for finding in best_eval.findings:
        if finding.get("severity") in {"error", "warning"}:
            memory["error_patterns"].append(
                {
                    "schema": "verifier_feedback.v2",
                    "cluster_id": cluster_id,
                    "task_family": _task_family(cluster),
                    "tool_path": _tool_path_from_cluster(cluster),
                    "failure_types": _failure_types_from_cluster(cluster),
                    "code": finding.get("code"),
                    "message": finding.get("message"),
                    "candidate_id": best_eval.candidate_id,
                }
            )
    duplicate = best_eval.side_info.get("duplicate")
    if isinstance(duplicate, dict) and duplicate.get("recommended_action") != "keep":
        memory["duplicate_patterns"].append(
            {
                "schema": "duplicate_feedback.v2",
                "cluster_id": cluster_id,
                "task_family": _task_family(cluster),
                "tool_path": _tool_path_from_cluster(cluster),
                "failure_types": _failure_types_from_cluster(cluster),
                "candidate_id": best_eval.candidate_id,
                "candidate_digest": _candidate_digest(markdown),
                "similarity": duplicate.get("similarity"),
                "nearest": duplicate.get("nearest"),
                "nearest_source": duplicate.get("nearest_source"),
                "recommended_action": duplicate.get("recommended_action"),
                "reason": duplicate.get("reason"),
                "section_review": duplicate.get("section_review", {}),
            }
        )
    _write_memory(_trim_memory(memory), memory_path)


def evolve_skill(
    cluster_id: str | None = None,
    *,
    all_entrypoints: bool = False,
    budget: int = 50,
    output_dir: str | Path | None = None,
    memory_path: str | Path | None = None,
) -> dict[str, Any]:
    ensure_project_dirs()
    report = _load_mining_report()
    if all_entrypoints:
        wanted_ids = [
            str(item.get("cluster_id"))
            for item in report.get("generation_entrypoints", [])
            if isinstance(item, dict) and item.get("cluster_id")
        ]
        if not wanted_ids:
            wanted_ids = [str(cluster.get("id")) for cluster in report.get("clusters", []) if cluster.get("id")]
    else:
        wanted_ids = [cluster_id or str((report.get("clusters") or [{"id": "C01"}])[0].get("id"))]
    runs: list[dict[str, Any]] = []
    for wanted in wanted_ids:
        cluster = _cluster_lookup(report, wanted)
        memory = _load_memory(memory_path)
        matches = _memory_matches(cluster, memory)
        seed = _seed_candidate(cluster, matches)
        target_path = _target_skill_path(cluster, output_dir if not all_entrypoints else None)
        known_texts = _known_skill_texts(extra_paths=[target_path])
        evaluations: list[CandidateEval] = []
        rendered: dict[str, str] = {}
        for candidate_id, candidate in _candidate_variants(seed, cluster, max(1, budget)):
            eval_result, markdown = _score_candidate(candidate_id, candidate, cluster, known_texts)
            evaluations.append(eval_result)
            rendered[candidate_id] = markdown
            if eval_result.rejected and len(evaluations) >= max(3, min(budget, 5)):
                continue
        frontier = pareto_frontier(evaluations)
        best = frontier[0] if frontier else max(evaluations, key=lambda item: item.score)
        metadata = _write_evolved_candidate(cluster, rendered[best.candidate_id], best, output_dir if not all_entrypoints else None)
        _update_memory(cluster, best, rendered[best.candidate_id], memory_path)
        runs.append(
            {
                "cluster_id": str(cluster.get("id")),
                "budget": budget,
                "memory_matches": matches,
                "best_candidate": best.to_mapping(),
                "frontier": [item.candidate_id for item in frontier],
                "evaluations": [item.to_mapping() for item in evaluations],
                "output": metadata,
            }
        )
    result = {
        "status": "ok",
        "optimizer": "local_metric_pareto",
        "racing": {
            "hard_reject_codes": sorted(HARD_REJECT_CODES),
            "max_candidate_chars": MAX_CANDIDATE_CHARS,
        },
        "run_count": len(runs),
        "runs": runs,
    }
    write_json(EVOLUTION_REPORT_PATH, result)
    result["report_path"] = str(EVOLUTION_REPORT_PATH)
    return result
