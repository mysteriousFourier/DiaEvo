from __future__ import annotations

import os
import io
import time
from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable

from .deepseek_chat import DeepSeekConfig, chat_completion, config_from_env
from .evaluation import (
    _extended_candidate_rank,
    _nearest_cluster_for_trace,
    deterministic_trace_split,
    evaluate_recommendations,
    evaluate_safety_regressions,
    evaluate_stable_overlay_recommendations,
)
from .evolution import (
    SECTION_KEYS,
    MAX_CANDIDATE_CHARS,
    CandidateEval,
    _candidate_digest,
    _cluster_lookup,
    _known_skill_texts,
    _load_memory,
    _memory_matches,
    _score_candidate,
    _seed_candidate,
    _validation_suggestion,
    evolve_skill,
    memory_summary,
)
from .features import tokenize
from .ingest import ingest_traces, load_skill_registry, load_traces
from .miner import mine
from .paths import CANDIDATE_SKILLS_DIR, DATA_DIR, REPORTS_DIR, ensure_project_dirs
from .quality import extract_skill_sections
from .storage import read_json, write_json, write_jsonl
from .verifier import CREDENTIAL_PATTERNS, DANGEROUS_PATTERNS, parse_frontmatter


GEPA_REPORT_PATH = REPORTS_DIR / "gepa_skill_optimization.json"
GEPA_PHASE4_REPORT_PATH = REPORTS_DIR / "gepa_phase4_experiments.json"
SECRET_FIELD_NAMES = {"api_key", "authorization", "password", "secret", "token"}
MEMORY_POLICIES = {"current", "none", "ctm", "epm", "ctm_epm"}
RACING_POLICIES = {"off", "cheap_gates"}
JUDGE_POLICIES = {"none", "uncertainty_only"}
PHASE4_CONDITIONS = (
    "local_evolved",
    "gepa_seed_only",
    "gepa_ctm",
    "gepa_epm",
    "gepa_ctm_epm",
    "gepa_racing",
    "gepa_sparse_judge",
)


class GEPAUnavailableError(RuntimeError):
    """Raised when the optional GEPA stack is not installed."""


def _round(value: float) -> float:
    return round(value, 4)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _as_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _parse_tags(raw: str) -> list[str]:
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip('"').strip("'") for item in text.split(",") if item.strip()]


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SECRET_FIELD_NAMES or lowered.endswith("_api_key"):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _provider_summary(config: DeepSeekConfig) -> dict[str, Any]:
    return {
        "provider": "deepseek",
        "api": "openai-compatible",
        "api_key_source": ".env:DEEPSEEK_API_KEY",
        "api_key_configured": bool(config.api_key),
        "base_url": config.base_url,
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "timeout": config.timeout,
    }


def _validate_choice(value: str, allowed: set[str], label: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _filter_memory(memory: dict[str, Any], policy: str) -> dict[str, Any]:
    normalized = _validate_choice(policy, MEMORY_POLICIES, "memory_policy")
    empty = {
        "correct_templates": [],
        "error_patterns": [],
        "validation_patterns": [],
        "duplicate_patterns": [],
        "promotion_patterns": [],
    }
    if normalized == "current" or normalized == "ctm_epm":
        return {
            "correct_templates": [item for item in memory.get("correct_templates", []) if isinstance(item, dict)],
            "error_patterns": [item for item in memory.get("error_patterns", []) if isinstance(item, dict)],
            "validation_patterns": [item for item in memory.get("validation_patterns", []) if isinstance(item, dict)],
            "duplicate_patterns": [item for item in memory.get("duplicate_patterns", []) if isinstance(item, dict)],
            "promotion_patterns": [item for item in memory.get("promotion_patterns", []) if isinstance(item, dict)],
        }
    if normalized == "none":
        return empty
    if normalized == "ctm":
        return {
            **empty,
            "correct_templates": [item for item in memory.get("correct_templates", []) if isinstance(item, dict)],
        }
    return {
        **empty,
        "error_patterns": [item for item in memory.get("error_patterns", []) if isinstance(item, dict)],
        "validation_patterns": [item for item in memory.get("validation_patterns", []) if isinstance(item, dict)],
        "duplicate_patterns": [item for item in memory.get("duplicate_patterns", []) if isinstance(item, dict)],
        "promotion_patterns": [item for item in memory.get("promotion_patterns", []) if isinstance(item, dict)],
    }


def _memory_policy_summary(memory: dict[str, Any], policy: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
    feedback_directions: dict[str, int] = {}
    validation_artifact_count = 0
    for item in memory.get("promotion_patterns", []):
        if not isinstance(item, dict):
            continue
        policy_item = item.get("feedback_policy") if isinstance(item.get("feedback_policy"), dict) else {}
        direction = str(policy_item.get("direction") or "neutral")
        feedback_directions[direction] = feedback_directions.get(direction, 0) + 1
    for item in memory.get("validation_patterns", []):
        if not isinstance(item, dict):
            continue
        artifacts = item.get("artifacts") if isinstance(item.get("artifacts"), dict) else {}
        if artifacts.get("sandbox_run_id") or artifacts.get("touched_file_count"):
            validation_artifact_count += 1
    return {
        "policy": policy,
        "source_counts": {
            "correct_templates": len([item for item in memory.get("correct_templates", []) if isinstance(item, dict)]),
            "error_patterns": len([item for item in memory.get("error_patterns", []) if isinstance(item, dict)]),
            "validation_patterns": len([item for item in memory.get("validation_patterns", []) if isinstance(item, dict)]),
            "duplicate_patterns": len([item for item in memory.get("duplicate_patterns", []) if isinstance(item, dict)]),
            "promotion_patterns": len([item for item in memory.get("promotion_patterns", []) if isinstance(item, dict)]),
        },
        "match_count": len(matches),
        "match_schemas": sorted({str(item.get("schema") or "") for item in matches if item.get("schema")}),
        "human_feedback_policy": dict(sorted(feedback_directions.items())),
        "validation_artifact_pattern_count": validation_artifact_count,
    }


def _deepseek_config(
    *,
    env_path: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    no_thinking: bool = False,
) -> DeepSeekConfig:
    try:
        return config_from_env(
            env_path=env_path,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            no_thinking=no_thinking,
        )
    except ValueError as exc:
        message = str(exc).replace("chat-test", "evaluate-gepa")
        raise RuntimeError(message) from exc


def _import_gepa_stack() -> dict[str, Any]:
    try:
        from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig, make_litellm_lm, optimize_anything
    except ImportError as exc:
        raise GEPAUnavailableError(
            "GEPA dependency is not installed. Install the optional GEPA/LiteLLM stack before running "
            "`DiaEvo evaluate-gepa` without `--dry-run`."
        ) from exc
    return {
        "EngineConfig": EngineConfig,
        "GEPAConfig": GEPAConfig,
        "ReflectionConfig": ReflectionConfig,
        "make_litellm_lm": make_litellm_lm,
        "optimize_anything": optimize_anything,
    }


def _trace_example(trace: Any, cluster_id: str) -> dict[str, Any]:
    return {
        "id": trace.id,
        "cluster_id": cluster_id,
        "task": trace.task,
        "project_language": trace.project_language,
        "frameworks": trace.frameworks,
        "tools": trace.tools,
        "commands": trace.commands,
        "outcome": trace.outcome,
        "used_skills": trace.used_skills,
        "tags": trace.tags,
        "document": trace.document,
    }


def _trace_examples(traces: list[Any], mine_report: dict[str, Any]) -> list[dict[str, Any]]:
    return [_trace_example(trace, _nearest_cluster_for_trace(trace, mine_report)) for trace in traces]


def _target_cluster_traces(traces: list[Any], mine_report: dict[str, Any], cluster_id: str) -> list[Any]:
    wanted = cluster_id.upper()
    return [trace for trace in traces if _nearest_cluster_for_trace(trace, mine_report).upper() == wanted]


def _learning_context_summary(
    *,
    train_traces: list[Any],
    holdout_traces: list[Any],
    memory_matches: list[dict[str, Any]],
    fallback_used: bool,
) -> dict[str, Any]:
    match_sources: dict[str, int] = {}
    for item in memory_matches:
        schema = str(item.get("schema") or "unknown")
        match_sources[schema] = match_sources.get(schema, 0) + 1
    return {
        "style": "hermes_inspired_retrieval_boundary",
        "notes": [
            "Optimize with target-cluster traces and relevant memory matches instead of the full trace corpus.",
            "Keep generated skills as draft procedural memory until verifier, validation, and promotion gates pass.",
            "Use memory matches as compact cues and cautions, not as text to copy.",
        ],
        "target_train_trace_count": len(train_traces),
        "target_holdout_trace_count": len(holdout_traces),
        "fallback_used": fallback_used,
        "memory_match_schemas": dict(sorted(match_sources.items())),
    }


def _coerce_candidate_sections(candidate: Any) -> dict[str, str]:
    if isinstance(candidate, dict):
        return {key: str(candidate.get(key) or "").strip() for key in SECTION_KEYS}
    if isinstance(candidate, str):
        sections = extract_skill_sections(candidate)
        return {key: str(sections.get(key) or "").strip() for key in SECTION_KEYS}
    raw = getattr(candidate, "candidate", None)
    if isinstance(raw, dict):
        return {key: str(raw.get(key) or "").strip() for key in SECTION_KEYS}
    return {key: "" for key in SECTION_KEYS}


def _section_completeness(candidate: dict[str, str]) -> tuple[float, list[str]]:
    missing = [key for key in SECTION_KEYS if len(candidate.get(key, "").strip()) < 20]
    return (len(SECTION_KEYS) - len(missing)) / len(SECTION_KEYS), missing


def _example_alignment(markdown: str, example: dict[str, Any] | None) -> float:
    if not example:
        return 0.5
    signals: list[str] = []
    signals.extend(tokenize(str(example.get("task") or "")))
    for key in ("tools", "commands", "frameworks", "tags", "used_skills"):
        value = example.get(key)
        if isinstance(value, list):
            for item in value:
                signals.extend(tokenize(str(item)))
    unique = sorted(set(signals))
    if not unique:
        return 0.5
    text_tokens = set(tokenize(markdown))
    hits = sum(1 for signal in unique if signal in text_tokens)
    return hits / len(unique)


def _candidate_score_eval(
    candidate_id: str,
    candidate: dict[str, str],
    cluster: dict[str, Any],
    known_texts: list[Any],
    *,
    example: dict[str, Any] | None = None,
) -> tuple[CandidateEval, str, dict[str, Any]]:
    eval_result, markdown = _score_candidate(candidate_id, candidate, cluster, known_texts)
    completeness, missing_content = _section_completeness(candidate)
    example_alignment = _example_alignment(markdown, example)
    score = 0.0 if eval_result.rejected else (0.80 * eval_result.score + 0.12 * example_alignment + 0.08 * completeness)
    side_info = {
        "input": {
            "cluster_id": str(cluster.get("id") or ""),
            "representative_task": str(cluster.get("representative_task") or ""),
            "trace_ids": _as_strings(cluster.get("trace_ids")),
            "top_terms": _as_strings(cluster.get("top_terms"))[:8],
            "top_tools": _as_strings(cluster.get("top_tools"))[:8],
            "top_failures": [*_as_strings(cluster.get("top_errors"))[:5], *_as_strings(cluster.get("top_failure_types"))[:5]],
            "example_trace_id": str((example or {}).get("id") or ""),
        },
        "candidate": {
            "section_lengths": {key: len(candidate.get(key, "")) for key in SECTION_KEYS},
            "missing_or_thin_sections": missing_content,
            "rendered_length": len(markdown),
        },
        "feedback": {
            "verifier": {
                "passed": eval_result.passed,
                "warning_count": eval_result.warning_count,
                "error_count": eval_result.error_count,
                "findings": eval_result.findings,
            },
            "duplicate": eval_result.side_info.get("duplicate", {}),
            "example_alignment": _round(example_alignment),
            "section_completeness": _round(completeness),
        },
        "scores": {
            **{key: _round(value) for key, value in eval_result.scores.items()},
            "example_alignment": _round(example_alignment),
            "section_completeness": _round(completeness),
            "aggregate": _round(score),
        },
        "edit_direction": _edit_direction(eval_result, missing_content, example_alignment),
    }
    return eval_result, markdown, {"score": score, "side_info": side_info}


def _edit_direction(eval_result: CandidateEval, missing_content: list[str], example_alignment: float) -> str:
    if eval_result.rejected:
        return "Remove hard verifier failures before optimizing usefulness."
    duplicate = eval_result.side_info.get("duplicate", {})
    if isinstance(duplicate, dict) and duplicate.get("recommended_action") in {"merge", "specialize", "reject_duplicate"}:
        return f"Address duplicate risk by following the `{duplicate.get('recommended_action')}` review action."
    if missing_content:
        return "Add concrete mined guidance to thin sections: " + ", ".join(missing_content)
    if example_alignment < 0.25:
        return "Add trace-grounded task terms, tools, and failure signals from the example."
    return "Preserve safety constraints and improve trigger specificity only with mined evidence."


def _pattern_matches(patterns: list[str], text: str) -> list[str]:
    import re

    matches: list[str] = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            matches.append(pattern)
    return matches


def _cheap_gate_result(
    *,
    candidate: dict[str, str],
    markdown: str,
    eval_result: CandidateEval,
    scored: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    missing = scored.get("side_info", {}).get("candidate", {}).get("missing_or_thin_sections", [])
    if isinstance(missing, list) and missing:
        reasons.append("missing_or_thin_sections:" + ",".join(str(item) for item in missing))
    dangerous = _pattern_matches(DANGEROUS_PATTERNS, markdown)
    if dangerous:
        reasons.append("dangerous_command_pattern")
    credentials = _pattern_matches(CREDENTIAL_PATTERNS, markdown)
    if credentials:
        reasons.append("credential_pattern")
    actionable_text = _strip_negated_policy_phrases(markdown).lower()
    if "auto-promote" in actionable_text or "automatically promote" in actionable_text or "auto promote" in actionable_text:
        reasons.append("auto_promotion_instruction")
    if "auto-install" in actionable_text or "automatically install" in actionable_text or "auto install" in actionable_text:
        reasons.append("auto_install_instruction")
    if len(markdown) > MAX_CANDIDATE_CHARS:
        reasons.append("length_limit")
    if eval_result.duplicate_similarity >= 0.985:
        reasons.append("near_duplicate")
    scores = scored.get("side_info", {}).get("scores", {})
    try:
        alignment = float(scores.get("example_alignment", 0.0))
    except (TypeError, ValueError):
        alignment = 0.0
    if alignment < 0.05:
        reasons.append("poor_example_alignment")
    if eval_result.rejected:
        reasons.append("static_eval_rejected")
    return {
        "passed": not reasons,
        "reasons": sorted(set(reasons)),
        "section_lengths": {key: len(candidate.get(key, "")) for key in SECTION_KEYS},
        "rendered_length": len(markdown),
        "duplicate_similarity": _round(eval_result.duplicate_similarity),
    }


def _strip_negated_policy_phrases(text: str) -> str:
    import re

    patterns = [
        r"\bdo\s+not\s+auto[-\s]promote\s+or\s+auto[-\s]install\b",
        r"\bdo\s+not\s+automatically\s+promote\s+or\s+automatically\s+install\b",
        r"\bnever\s+auto[-\s]promote\s+or\s+auto[-\s]install\b",
        r"\bnever\s+automatically\s+promote\s+or\s+automatically\s+install\b",
        r"\bdo\s+not\s+auto[-\s]promote\b",
        r"\bdo\s+not\s+automatically\s+promote\b",
        r"\bdo\s+not\s+auto[-\s]install\b",
        r"\bdo\s+not\s+automatically\s+install\b",
        r"\bnever\s+auto[-\s]promote\b",
        r"\bnever\s+automatically\s+promote\b",
        r"\bnever\s+auto[-\s]install\b",
        r"\bnever\s+automatically\s+install\b",
    ]
    value = text
    for pattern in patterns:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    return value


def _judge_usage_tokens(usage: dict[str, Any]) -> int | None:
    for key in ("total_tokens", "total_token_count"):
        value = usage.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _judge_uncertainty_reasons(eval_result: CandidateEval, scored: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    scores = eval_result.scores
    side_scores = scored.get("side_info", {}).get("scores", {})
    aggregate = float(side_scores.get("aggregate", 0.0) or 0.0)
    evidence = float(scores.get("evidence_alignment", 0.0) or 0.0)
    if scores.get("verifier", 0.0) >= 1.0 and evidence < 0.25:
        reasons.append("verifier_evidence_disagreement")
    if eval_result.passed and aggregate < 0.45:
        reasons.append("low_aggregate_after_pass")
    return sorted(set(reasons))


def _run_sparse_judge(
    *,
    candidate: dict[str, str],
    scored: dict[str, Any],
    reasons: list[str],
    deepseek: DeepSeekConfig | None,
) -> dict[str, Any]:
    if not reasons:
        return {"called": False, "reason": "not_uncertain", "verdict": "not_needed"}
    if deepseek is None:
        return {
            "called": False,
            "reason": "deepseek_config_unavailable",
            "trigger_reasons": reasons,
            "verdict": "skipped",
        }
    prompt = (
        "Judge whether this draft DiaEvo skill candidate should continue GEPA optimization. "
        "Return compact JSON with keys verdict, reason. Use verdict continue or reject.\n\n"
        f"Uncertainty reasons: {', '.join(reasons)}\n"
        f"Scores: {scored.get('side_info', {}).get('scores', {})}\n"
        f"Candidate sections: {candidate}"
    )
    response = chat_completion(
        [
            {"role": "system", "content": "You are a conservative reviewer for draft agent skills. Do not reveal secrets."},
            {"role": "user", "content": prompt},
        ],
        deepseek,
    )
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    return {
        "called": True,
        "trigger_reasons": reasons,
        "verdict": "reviewed",
        "usage": usage if isinstance(usage, dict) else {},
    }


def _make_gepa_evaluator(
    *,
    cluster: dict[str, Any],
    known_texts: list[Any],
    racing_policy: str = "off",
    judge_policy: str = "none",
    deepseek: DeepSeekConfig | None = None,
    counters: dict[str, Any] | None = None,
) -> Callable[..., tuple[float, dict[str, Any]]]:
    normalized_racing = _validate_choice(racing_policy, RACING_POLICIES, "racing_policy")
    normalized_judge = _validate_choice(judge_policy, JUDGE_POLICIES, "judge_policy")
    stats = counters if counters is not None else {}
    stats.setdefault("metric_calls", 0)
    stats.setdefault("racing_rejected_count", 0)
    stats.setdefault("racing_rejection_reasons", {})
    stats.setdefault("judge_calls", 0)
    stats.setdefault("judge_skipped_count", 0)
    stats.setdefault("judge_trigger_reasons", {})

    def evaluator(candidate: Any, example: object | None = None, **_: Any) -> tuple[float, dict[str, Any]]:
        stats["metric_calls"] = int(stats.get("metric_calls", 0) or 0) + 1
        mapped_example = example if isinstance(example, dict) else None
        sections = _coerce_candidate_sections(candidate)
        eval_result, markdown, scored = _candidate_score_eval("gepa-proposal", sections, cluster, known_texts, example=mapped_example)
        side_info = dict(scored["side_info"])
        if normalized_racing == "cheap_gates":
            gate = _cheap_gate_result(candidate=sections, markdown=markdown, eval_result=eval_result, scored=scored)
            side_info["racing"] = gate
            if not gate["passed"]:
                stats["racing_rejected_count"] = int(stats.get("racing_rejected_count", 0) or 0) + 1
                reason_counts = stats.setdefault("racing_rejection_reasons", {})
                for reason in gate["reasons"]:
                    reason_counts[reason] = int(reason_counts.get(reason, 0) or 0) + 1
                side_info["edit_direction"] = "Resolve cheap-gate rejection before spending more GEPA calls: " + ", ".join(gate["reasons"])
                return 0.0, side_info
        if normalized_judge == "uncertainty_only":
            reasons = _judge_uncertainty_reasons(eval_result, scored)
            trigger_counts = stats.setdefault("judge_trigger_reasons", {})
            for reason in reasons:
                trigger_counts[reason] = int(trigger_counts.get(reason, 0) or 0) + 1
            if reasons:
                judge = _run_sparse_judge(candidate=sections, scored=scored, reasons=reasons, deepseek=deepseek)
                side_info["judge"] = judge
                if judge.get("called"):
                    stats["judge_calls"] = int(stats.get("judge_calls", 0) or 0) + 1
                    usage = judge.get("usage") if isinstance(judge.get("usage"), dict) else {}
                    tokens = _judge_usage_tokens(usage)
                    if tokens is not None:
                        stats["total_tokens"] = int(stats.get("total_tokens", 0) or 0) + tokens
                else:
                    stats["judge_skipped_count"] = int(stats.get("judge_skipped_count", 0) or 0) + 1
            else:
                side_info["judge"] = {"called": False, "reason": "not_uncertain", "verdict": "not_needed"}
        return float(scored["score"]), side_info

    return evaluator


def _candidate_registry_record(
    *,
    skill_path: Path,
    source: str,
    cluster_id: str,
    scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = skill_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(text)
    score_data = scores if isinstance(scores, dict) else {}
    try:
        risk = float(meta.get("risk_score") or 0.25)
    except (TypeError, ValueError):
        risk = 0.25
    if "safety" in score_data:
        try:
            risk = 1.0 - float(score_data.get("safety") or 0.0)
        except (TypeError, ValueError):
            pass
    return {
        "name": meta.get("name") or skill_path.parent.name,
        "description": meta.get("description") or f"{source} from {cluster_id}",
        "tags": [*_parse_tags(meta.get("tags") or ""), cluster_id.lower(), source],
        "path": str(skill_path.parent),
        "permissions": ["workspace-read"],
        "usage_count": 0,
        "success_count": 1,
        "failure_count": 0,
        "last_used": "",
        "risk": max(0.0, min(1.0, risk)),
        "cost": 0.25,
        "source": source,
        "installed": False,
        "source_cluster": cluster_id,
    }


def _write_augmented_registry(path: Path, records: list[dict[str, Any]]) -> Path:
    registry = [skill.to_mapping() for skill in load_skill_registry()]
    names = {item.get("name") for item in registry}
    for record in records:
        if record.get("name") and record.get("name") not in names:
            registry.append(record)
            names.add(record.get("name"))
    write_json(path, registry)
    return path


def _write_skill_candidate(
    *,
    cluster: dict[str, Any],
    candidate: dict[str, str],
    eval_result: CandidateEval,
    markdown: str,
    output_dir: str | Path | None,
) -> dict[str, Any]:
    cluster_id = str(cluster.get("id") or "C00").upper()
    target = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / cluster_id / "gepa"
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
        "status": "gepa-candidate",
        "candidate_id": eval_result.candidate_id,
        "candidate_digest": _candidate_digest(markdown),
        "score": _round(eval_result.score),
        "scores": {key: _round(value) for key, value in eval_result.scores.items()},
        "duplicate_similarity": _round(eval_result.duplicate_similarity),
        "duplicate": eval_result.side_info.get("duplicate", {}),
        "section_lengths": {key: len(candidate.get(key, "")) for key in SECTION_KEYS},
    }
    write_json(target / "metadata.json", metadata)
    return metadata


def _candidate_name_from_markdown(markdown: str, fallback: str) -> str:
    meta, _ = parse_frontmatter(markdown)
    return meta.get("name") or fallback


def _optimizer_call_counts(counters: dict[str, Any] | None = None) -> dict[str, Any]:
    stats = counters or {}
    return {
        "metric_calls": int(stats.get("metric_calls", 0) or 0),
        "reflection_calls": int(stats.get("reflection_calls", 0) or 0),
        "judge_calls": int(stats.get("judge_calls", 0) or 0),
        "judge_skipped_count": int(stats.get("judge_skipped_count", 0) or 0),
        "racing_rejected_count": int(stats.get("racing_rejected_count", 0) or 0),
        "racing_rejection_reasons": dict(stats.get("racing_rejection_reasons", {}) or {}),
        "judge_trigger_reasons": dict(stats.get("judge_trigger_reasons", {}) or {}),
        "total_tokens": stats.get("total_tokens"),
    }


def _first_present_attr(value: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        item = getattr(value, name, None)
        if item is not None:
            return item
    return None


def _candidate_holdout_eval(
    *,
    label: str,
    candidate_name: str,
    registry_path: Path,
    train_path: Path,
    target_holdout: list[Any],
    baseline_eval: dict[str, Any],
    top_k: int,
) -> dict[str, Any]:
    labeled = [trace for trace in target_holdout if trace.used_skills]
    if not labeled:
        return {
            "label": label,
            "candidate": candidate_name,
            "target_heldout_trace_count": len(target_holdout),
            "target_heldout_query_count": 0,
            "metrics": {},
            "diagnostics": [],
            "overlay_recommendation_eval": {"metrics": {}, "per_query": []},
            "raw_recommendation_eval": {"metrics": {}, "per_query": []},
        }
    overlay = evaluate_stable_overlay_recommendations(
        labeled,
        train_path=train_path,
        overlay_registry_path=registry_path,
        top_k=top_k,
    )
    raw = evaluate_recommendations(labeled, traces_path=train_path, registry_path=registry_path, top_k=top_k)
    diagnostics = [
        _extended_candidate_rank(
            trace,
            candidate_name,
            train_path=train_path,
            registry_path=registry_path,
            top_k=top_k,
        )
        for trace in labeled
    ]
    hit_rate = _round(_mean([1.0 if item.get("top_k_hit") else 0.0 for item in diagnostics]))
    baseline_metrics = baseline_eval.get("metrics", {})
    overlay_metrics = overlay.get("metrics", {})
    precision_key = f"precision_at_{top_k}"
    metrics = {
        "target_heldout_trace_count": len(target_holdout),
        "target_heldout_query_count": len(labeled),
        f"candidate_top_{top_k}_hit_rate": hit_rate,
        f"precision_at_{top_k}": overlay_metrics.get(precision_key, 0.0),
        "mrr": overlay_metrics.get("mrr", 0.0),
        "recommendation_lift": overlay_metrics.get("recommendation_lift", 0.0),
        f"precision_at_{top_k}_delta": _round(
            float(overlay_metrics.get(precision_key, 0.0)) - float(baseline_metrics.get(precision_key, 0.0))
        ),
        "mrr_delta": _round(float(overlay_metrics.get("mrr", 0.0)) - float(baseline_metrics.get("mrr", 0.0))),
        "recommendation_lift_delta": _round(
            float(overlay_metrics.get("recommendation_lift", 0.0)) - float(baseline_metrics.get("recommendation_lift", 0.0))
        ),
        "failed_candidate_trace_count": sum(1 for item in diagnostics if not item.get("top_k_hit")),
    }
    return {
        "label": label,
        "candidate": candidate_name,
        "target_heldout_trace_count": len(target_holdout),
        "target_heldout_query_count": len(labeled),
        "metrics": metrics,
        "diagnostics": diagnostics,
        "overlay_recommendation_eval": overlay,
        "raw_recommendation_eval": raw,
    }


def _heldout_metrics_for(comparison: dict[str, Any], label: str) -> dict[str, Any]:
    item = comparison.get(label, {}) if isinstance(comparison.get(label), dict) else {}
    heldout = item.get("heldout", {}) if isinstance(item.get("heldout"), dict) else {}
    metrics = heldout.get("metrics", {}) if isinstance(heldout.get("metrics"), dict) else {}
    return dict(metrics)


def _experiment_row_from_report_parts(
    *,
    condition: str,
    cluster_id: str,
    budget: int,
    memory_policy: str,
    racing_policy: str,
    judge_policy: str,
    elapsed_sec: float,
    call_counts: dict[str, Any],
    comparison: dict[str, Any],
    safety: dict[str, Any],
    adoption: dict[str, Any],
    top_k: int,
) -> dict[str, Any]:
    safety_metrics = safety.get("metrics", {}) if isinstance(safety.get("metrics"), dict) else {}
    gepa = comparison.get("gepa", {}) if isinstance(comparison.get("gepa"), dict) else {}
    optimizer = gepa.get("optimizer", {}) if isinstance(gepa.get("optimizer"), dict) else {}
    optimizer_counts = optimizer.get("call_counts", {}) if isinstance(optimizer.get("call_counts"), dict) else {}
    counts = {**call_counts, **optimizer_counts}
    status = str(adoption.get("status") or "")
    reason = str(adoption.get("reason") or "")
    return {
        "condition": condition,
        "cluster_id": cluster_id,
        "budget": budget,
        "memory_policy": memory_policy,
        "racing_policy": racing_policy,
        "judge_policy": judge_policy,
        "metric_calls": counts.get("metric_calls"),
        "reflection_calls": counts.get("reflection_calls"),
        "judge_calls": counts.get("judge_calls"),
        "judge_skipped_count": counts.get("judge_skipped_count"),
        "racing_rejected_count": counts.get("racing_rejected_count"),
        "racing_rejection_reasons": counts.get("racing_rejection_reasons", {}),
        "judge_trigger_reasons": counts.get("judge_trigger_reasons", {}),
        "total_tokens": counts.get("total_tokens"),
        "elapsed_sec": elapsed_sec,
        "heldout": {
            "seed": _heldout_metrics_for(comparison, "seed"),
            "local_evolved": _heldout_metrics_for(comparison, "local_evolved"),
            "gepa": _heldout_metrics_for(comparison, "gepa"),
        },
        "safety_false_negative_rate": safety_metrics.get("safety_false_negative_rate"),
        "adoption_status": status,
        "not_adopted_reason": reason if status in {"not_adopted", "blocked", "not_applicable"} else "",
        "top_k": top_k,
    }


def _build_objective(cluster: dict[str, Any]) -> str:
    return (
        "Optimize structured SKILL.md sections for a DiaEvo candidate. "
        "Improve held-out discoverability and reuse while preserving verifier safety, workspace boundaries, "
        "manual validation, and manual promotion. Do not invent unsupported tools or credentials."
    )


def _build_background(cluster: dict[str, Any], memory_matches: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            f"Cluster: {cluster.get('id')}",
            f"Representative task: {cluster.get('representative_task')}",
            "Top terms: " + ", ".join(_as_strings(cluster.get("top_terms"))[:8]),
            "Top tools: " + ", ".join(_as_strings(cluster.get("top_tools"))[:8]),
            "Failures: " + ", ".join([*_as_strings(cluster.get("top_errors"))[:5], *_as_strings(cluster.get("top_failure_types"))[:5]]),
            "Required sections: " + ", ".join(SECTION_KEYS),
            "Safety is a hard constraint; generated skills remain drafts until human promotion.",
            "Learning boundary: use relevant trace and memory evidence as procedural cues; do not copy unrelated skills or optimize for unrelated clusters.",
            "Memory matches: "
            + "; ".join(str(item.get("summary") or item.get("recommended_action") or item.get("failure_category") or item.get("schema")) for item in memory_matches[:5]),
            "Human feedback labels: "
            + "; ".join(
                ",".join(_as_strings(item.get("labels"))) + f" => {item.get('feedback_policy', {}).get('direction', 'neutral')}"
                for item in memory_matches[:5]
                if isinstance(item.get("feedback_policy"), dict) and _as_strings(item.get("labels"))
            ),
            "Validation artifact ASI: "
            + "; ".join(
                f"{item.get('failure_category', item.get('status'))} touched {item.get('artifacts', {}).get('touched_file_count', 0)} files"
                for item in memory_matches[:5]
                if isinstance(item.get("artifacts"), dict)
            ),
        ]
    )


@contextmanager
def _temporary_litellm_env(deepseek: DeepSeekConfig):
    updates = {
        "OPENAI_API_KEY": deepseek.api_key,
        "OPENAI_BASE_URL": deepseek.base_url.rstrip("/"),
        "LITELLM_TEMPERATURE": str(deepseek.temperature),
    }
    if deepseek.timeout is not None:
        updates["LITELLM_TIMEOUT"] = str(deepseek.timeout)
    original = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run_gepa_optimizer(
    *,
    seed: dict[str, str],
    evaluator: Callable[..., tuple[float, dict[str, Any]]],
    train_examples: list[dict[str, Any]],
    heldout_examples: list[dict[str, Any]],
    cluster: dict[str, Any],
    memory_matches: list[dict[str, Any]],
    deepseek: DeepSeekConfig,
    budget: int,
    counters: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    stats = counters if counters is not None else {}
    stack = _import_gepa_stack()
    model_name = os.environ.get("DEEPSEEK_GEPA_MODEL", f"openai/{deepseek.model}").strip() or f"openai/{deepseek.model}"
    with _temporary_litellm_env(deepseek):
        lm = stack["make_litellm_lm"](model_name)
        gepa_config = stack["GEPAConfig"](
            engine=stack["EngineConfig"](
                max_metric_calls=max(1, budget),
                display_progress_bar=False,
                parallel=False,
                capture_stdio=False,
            ),
            reflection=stack["ReflectionConfig"](reflection_lm=lm),
        )
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            result = stack["optimize_anything"](
                seed_candidate=seed,
                evaluator=evaluator,
                dataset=train_examples,
                valset=heldout_examples or train_examples,
                objective=_build_objective(cluster),
                background=_build_background(cluster, memory_matches),
                config=gepa_config,
            )
    best_candidate = _coerce_candidate_sections(getattr(result, "best_candidate", seed))
    if hasattr(result, "to_dict"):
        raw_result = result.to_dict()
    else:
        raw_result = {
            "best_candidate": best_candidate,
            "total_metric_calls": getattr(result, "total_metric_calls", None),
            "num_full_val_evals": getattr(result, "num_full_val_evals", None),
        }
    total_metric_calls = getattr(result, "total_metric_calls", None)
    if total_metric_calls is not None:
        stats["metric_calls"] = total_metric_calls
    reflection_calls = _first_present_attr(result, ("total_reflection_calls", "reflection_calls", "num_reflection_calls"))
    if reflection_calls is not None:
        stats["reflection_calls"] = reflection_calls
    total_tokens = _first_present_attr(result, ("total_tokens", "token_count", "total_token_count"))
    if total_tokens is not None:
        stats["total_tokens"] = total_tokens
    return best_candidate, {
        "model_name": model_name,
        "total_metric_calls": total_metric_calls,
        "total_reflection_calls": reflection_calls,
        "total_tokens": total_tokens,
        "num_full_val_evals": getattr(result, "num_full_val_evals", None),
        "num_candidates": getattr(result, "num_candidates", None),
        "best_idx": getattr(result, "best_idx", None),
        "call_counts": _optimizer_call_counts(stats),
        "captured_stdout": stdout_capture.getvalue()[-2000:],
        "captured_stderr": stderr_capture.getvalue()[-2000:],
        "result": _redact_secrets(raw_result),
    }


def evaluate_gepa(
    cluster_id: str,
    *,
    budget: int = 50,
    input_path: str | Path = DATA_DIR / "sample_traces.jsonl",
    processed_path: str | Path = DATA_DIR / "processed_traces.jsonl",
    tool_events_path: str | Path | None = None,
    include_tool_events: bool = True,
    top_k: int = 3,
    env_path: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    no_thinking: bool = False,
    dry_run: bool = False,
    output_dir: str | Path | None = None,
    condition: str = "single_run",
    memory_policy: str = "current",
    racing_policy: str = "off",
    judge_policy: str = "none",
    write_report: bool = True,
) -> dict[str, Any]:
    ensure_project_dirs()
    normalized_memory_policy = _validate_choice(memory_policy, MEMORY_POLICIES, "memory_policy")
    normalized_racing_policy = _validate_choice(racing_policy, RACING_POLICIES, "racing_policy")
    normalized_judge_policy = _validate_choice(judge_policy, JUDGE_POLICIES, "judge_policy")
    started = time.perf_counter()
    deepseek = _deepseek_config(
        env_path=env_path,
        model=model,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        no_thinking=no_thinking,
    )
    ingest_summary = ingest_traces(
        input_path,
        processed_path,
        tool_events_path=tool_events_path,
        include_tool_events=include_tool_events,
    )
    traces = load_traces(processed_path)
    train, holdout = deterministic_trace_split(traces)
    split_root = REPORTS_DIR / "gepa"
    split_root.mkdir(parents=True, exist_ok=True)
    train_path = split_root / "train_traces.jsonl"
    holdout_path = split_root / "heldout_traces.jsonl"
    write_jsonl(train_path, [trace.to_mapping() for trace in train])
    write_jsonl(holdout_path, [trace.to_mapping() for trace in holdout])
    mine_report = mine(train_path)
    cluster = _cluster_lookup(mine_report, cluster_id)
    normalized_cluster_id = str(cluster.get("id") or cluster_id).upper()
    target_train = _target_cluster_traces(train, mine_report, normalized_cluster_id)
    target_holdout = _target_cluster_traces(holdout, mine_report, normalized_cluster_id)
    used_trace_fallback = False
    if not target_train:
        target_train = train
        used_trace_fallback = True
    if not target_holdout:
        target_holdout = holdout
        used_trace_fallback = True
    raw_memory = _load_memory()
    memory = _filter_memory(raw_memory, normalized_memory_policy)
    matches = _memory_matches(cluster, memory)
    learning_context = _learning_context_summary(
        train_traces=target_train,
        holdout_traces=target_holdout,
        memory_matches=matches,
        fallback_used=used_trace_fallback,
    )
    seed = _seed_candidate(cluster, matches)
    output_root = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / normalized_cluster_id / "gepa"
    known_texts = _known_skill_texts(extra_paths=[output_root / "SKILL.md"])

    registry_root = REPORTS_DIR / "gepa"
    seed_eval, seed_markdown, seed_scored = _candidate_score_eval("seed", seed, cluster, known_texts)
    local_evolution = evolve_skill(
        normalized_cluster_id,
        budget=min(max(1, budget), 20),
        output_dir=registry_root / "local_evolved_candidate" / normalized_cluster_id,
        memory_path=registry_root / "local_evolution_memory.json",
    )
    local_run = local_evolution["runs"][0] if local_evolution.get("runs") else {}
    local_output = local_run.get("output") if isinstance(local_run.get("output"), dict) else {}
    local_skill_path = Path(str(local_output.get("skill_path") or ""))
    local_markdown = local_skill_path.read_text(encoding="utf-8") if local_skill_path.exists() else ""

    seed_dir = registry_root / "seed_candidate" / normalized_cluster_id
    seed_dir.mkdir(parents=True, exist_ok=True)
    seed_skill_path = seed_dir / "SKILL.md"
    seed_skill_path.write_text(seed_markdown, encoding="utf-8")
    seed_record = _candidate_registry_record(
        skill_path=seed_skill_path,
        source="seed-candidate",
        cluster_id=normalized_cluster_id,
        scores=seed_eval.scores,
    )
    local_record = (
        _candidate_registry_record(
            skill_path=local_skill_path,
            source="evolved-candidate",
            cluster_id=normalized_cluster_id,
            scores=local_output.get("scores") if isinstance(local_output.get("scores"), dict) else {},
        )
        if local_skill_path.exists()
        else {}
    )
    seed_registry_path = _write_augmented_registry(registry_root / "seed_registry.json", [seed_record])
    local_registry_path = _write_augmented_registry(registry_root / "local_evolved_registry.json", [local_record] if local_record else [])

    labeled_target = [trace for trace in target_holdout if trace.used_skills]
    baseline_eval = evaluate_recommendations(labeled_target, traces_path=train_path, top_k=top_k)
    seed_name = _candidate_name_from_markdown(seed_markdown, normalized_cluster_id)
    local_name = _candidate_name_from_markdown(local_markdown, normalized_cluster_id) if local_markdown else ""
    comparison: dict[str, Any] = {
        "seed": {
            "static_eval": seed_eval.to_mapping(),
            "asi": seed_scored["side_info"],
            "heldout": _candidate_holdout_eval(
                label="seed",
                candidate_name=seed_name,
                registry_path=seed_registry_path,
                train_path=train_path,
                target_holdout=target_holdout,
                baseline_eval=baseline_eval,
                top_k=top_k,
            ),
        },
        "local_evolved": {
            "evolution": local_run,
            "heldout": _candidate_holdout_eval(
                label="local_evolved",
                candidate_name=local_name,
                registry_path=local_registry_path,
                train_path=train_path,
                target_holdout=target_holdout,
                baseline_eval=baseline_eval,
                top_k=top_k,
            )
            if local_name
            else {},
        },
    }

    gepa_optimizer: dict[str, Any] = {}
    gepa_output: dict[str, Any] = {}
    call_counters = _optimizer_call_counts()
    if not dry_run:
        mutable_counters: dict[str, Any] = {}
        evaluator = _make_gepa_evaluator(
            cluster=cluster,
            known_texts=known_texts,
            racing_policy=normalized_racing_policy,
            judge_policy=normalized_judge_policy,
            deepseek=deepseek if normalized_judge_policy == "uncertainty_only" else None,
            counters=mutable_counters,
        )
        train_examples = _trace_examples(target_train, mine_report)
        heldout_examples = _trace_examples(target_holdout, mine_report)
        gepa_candidate, gepa_optimizer = _run_gepa_optimizer(
            seed=seed,
            evaluator=evaluator,
            train_examples=train_examples,
            heldout_examples=heldout_examples,
            cluster=cluster,
            memory_matches=matches,
            deepseek=deepseek,
            budget=budget,
            counters=mutable_counters,
        )
        call_counters = _optimizer_call_counts(mutable_counters)
        gepa_eval, gepa_markdown, gepa_scored = _candidate_score_eval("gepa", gepa_candidate, cluster, known_texts)
        gepa_output = _write_skill_candidate(
            cluster=cluster,
            candidate=gepa_candidate,
            eval_result=gepa_eval,
            markdown=gepa_markdown,
            output_dir=output_dir,
        )
        gepa_record = _candidate_registry_record(
            skill_path=Path(gepa_output["skill_path"]),
            source="gepa-candidate",
            cluster_id=normalized_cluster_id,
            scores=gepa_output.get("scores") if isinstance(gepa_output.get("scores"), dict) else {},
        )
        gepa_registry_path = _write_augmented_registry(registry_root / "gepa_registry.json", [gepa_record])
        gepa_name = _candidate_name_from_markdown(gepa_markdown, normalized_cluster_id)
        comparison["gepa"] = {
            "status": "completed",
            "static_eval": gepa_eval.to_mapping(),
            "asi": gepa_scored["side_info"],
            "output": gepa_output,
            "optimizer": gepa_optimizer,
            "heldout": _candidate_holdout_eval(
                label="gepa",
                candidate_name=gepa_name,
                registry_path=gepa_registry_path,
                train_path=train_path,
                target_holdout=target_holdout,
                baseline_eval=baseline_eval,
                top_k=top_k,
            ),
        }
    else:
        comparison["gepa"] = {
            "status": "skipped_dry_run",
            "reason": "GEPA optimizer was not called; seed/local comparison and safety gates were exercised.",
        }

    safety = evaluate_safety_regressions()
    adoption = _adoption_recommendation(comparison, safety, top_k)
    elapsed_sec = _round(time.perf_counter() - started)
    experiment = _experiment_row_from_report_parts(
        condition=condition,
        cluster_id=normalized_cluster_id,
        budget=budget,
        memory_policy=normalized_memory_policy,
        racing_policy=normalized_racing_policy,
        judge_policy=normalized_judge_policy,
        elapsed_sec=elapsed_sec,
        call_counts=call_counters,
        comparison=comparison,
        safety=safety,
        adoption=adoption,
        top_k=top_k,
    )
    report = {
        "status": "dry_run" if dry_run else "ok",
        "optimizer": "gepa",
        "condition": condition,
        "cluster_id": normalized_cluster_id,
        "budget": budget,
        "phase4_controls": {
            "memory_policy": normalized_memory_policy,
            "racing_policy": normalized_racing_policy,
            "judge_policy": normalized_judge_policy,
        },
        "provider": _provider_summary(deepseek),
        "ingest_summary": ingest_summary,
        "split": {
            "policy": "deterministic_trace_id_hash",
            "train_path": str(train_path),
            "holdout_path": str(holdout_path),
            "train_ids": [trace.id for trace in train],
            "heldout_ids": [trace.id for trace in holdout],
            "target_train_ids": [trace.id for trace in target_train],
            "target_heldout_ids": [trace.id for trace in target_holdout],
            "target_trace_fallback_used": used_trace_fallback,
        },
        "cluster": {
            "id": normalized_cluster_id,
            "representative_task": cluster.get("representative_task"),
            "trace_ids": cluster.get("trace_ids", []),
            "top_terms": cluster.get("top_terms", []),
            "top_tools": cluster.get("top_tools", []),
        },
        "learning_context": learning_context,
        "memory_policy_summary": _memory_policy_summary(memory, normalized_memory_policy, matches),
        "memory_matches": matches,
        "comparison": comparison,
        "safety_eval": safety,
        "adoption_recommendation": adoption,
        "cost": {
            **call_counters,
            "elapsed_sec": elapsed_sec,
        },
        "experiment": experiment,
        "memory_summary": memory_summary(),
        "report_path": str(GEPA_REPORT_PATH),
        "notes": [
            "GEPA uses DeepSeek through the project .env and OpenAI-compatible API settings.",
            "The report never stores the raw DEEPSEEK_API_KEY value.",
            "GEPA candidates remain drafts; this command does not validate, promote, or install them.",
        ],
    }
    redacted = _redact_secrets(report)
    if write_report:
        write_json(GEPA_REPORT_PATH, redacted)
    return redacted


def _adoption_recommendation(comparison: dict[str, Any], safety: dict[str, Any], top_k: int) -> dict[str, Any]:
    gepa = comparison.get("gepa", {}) if isinstance(comparison.get("gepa"), dict) else {}
    if gepa.get("status") != "completed":
        return {
            "status": "not_applicable",
            "reason": "GEPA candidate was not produced.",
        }
    raw_safety_rate = safety.get("metrics", {}).get("safety_false_negative_rate", 1.0)
    safety_rate = float(raw_safety_rate if raw_safety_rate is not None else 1.0)
    if safety_rate != 0.0:
        return {
            "status": "blocked",
            "reason": "Safety false-negative rate regressed.",
        }
    hit_key = f"candidate_top_{top_k}_hit_rate"
    local_metrics = comparison.get("local_evolved", {}).get("heldout", {}).get("metrics", {})
    gepa_metrics = gepa.get("heldout", {}).get("metrics", {})
    local_hit = float(local_metrics.get(hit_key, 0.0) or 0.0)
    gepa_hit = float(gepa_metrics.get(hit_key, 0.0) or 0.0)
    local_mrr = float(local_metrics.get("mrr", 0.0) or 0.0)
    gepa_mrr = float(gepa_metrics.get("mrr", 0.0) or 0.0)
    gepa_static = gepa.get("static_eval", {})
    if bool(gepa_static.get("rejected")) or not bool(gepa_static.get("passed")):
        return {
            "status": "blocked",
            "reason": "GEPA candidate did not pass static verifier/evaluator gates.",
        }
    if gepa_hit > local_hit or gepa_mrr > local_mrr:
        return {
            "status": "candidate_for_review",
            "reason": "GEPA improved selected-cluster held-out discoverability or recommendation MRR without safety regression.",
        }
    return {
        "status": "not_adopted",
        "reason": "GEPA did not improve held-out usefulness over the local evolved candidate.",
    }


def _parse_budgets(value: str | list[int] | tuple[int, ...]) -> list[int]:
    if isinstance(value, list | tuple):
        budgets = [int(item) for item in value]
    else:
        budgets = [int(item.strip()) for item in str(value).split(",") if item.strip()]
    if not budgets:
        raise ValueError("At least one budget is required.")
    invalid = [item for item in budgets if item < 1]
    if invalid:
        raise ValueError("Budgets must be positive integers.")
    return budgets


def _phase4_condition_specs(budgets: list[int]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "condition": "local_evolved",
            "budget": 0,
            "memory_policy": "current",
            "racing_policy": "off",
            "judge_policy": "none",
            "dry_run": True,
        }
    ]
    matrix = [
        ("gepa_seed_only", "none", "off", "none"),
        ("gepa_ctm", "ctm", "off", "none"),
        ("gepa_epm", "epm", "off", "none"),
        ("gepa_ctm_epm", "ctm_epm", "off", "none"),
        ("gepa_racing", "ctm_epm", "cheap_gates", "none"),
        ("gepa_sparse_judge", "ctm_epm", "cheap_gates", "uncertainty_only"),
    ]
    for condition, memory_policy, racing_policy, judge_policy in matrix:
        for budget in budgets:
            specs.append(
                {
                    "condition": condition,
                    "budget": budget,
                    "memory_policy": memory_policy,
                    "racing_policy": racing_policy,
                    "judge_policy": judge_policy,
                    "dry_run": False,
                }
            )
    return specs


def _phase4_row_key(row: dict[str, Any]) -> tuple[str, int, str, str, str]:
    return (
        str(row.get("condition") or ""),
        int(row.get("budget", 0) or 0),
        str(row.get("memory_policy") or ""),
        str(row.get("racing_policy") or ""),
        str(row.get("judge_policy") or ""),
    )


def _phase4_spec_key(spec: dict[str, Any]) -> tuple[str, int, str, str, str]:
    return (
        str(spec.get("condition") or ""),
        int(spec.get("budget", 0) or 0),
        str(spec.get("memory_policy") or ""),
        str(spec.get("racing_policy") or ""),
        str(spec.get("judge_policy") or ""),
    )


def _load_phase4_resume_rows(*, cluster_id: str, budgets: list[int], top_k: int, dry_run: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = read_json(GEPA_PHASE4_REPORT_PATH, default={})
    if not isinstance(existing, dict):
        return [], []
    if str(existing.get("phase") or "") != "phase4_low_cost_gepa_apo":
        return [], []
    if str(existing.get("cluster_id") or "").upper() != cluster_id.upper():
        return [], []
    if existing.get("budgets") != budgets or int(existing.get("top_k") or 0) != int(top_k):
        return [], []
    if bool(existing.get("dry_run")) != bool(dry_run):
        return [], []
    rows = [item for item in existing.get("rows", []) if isinstance(item, dict)]
    runs = [item for item in existing.get("runs", []) if isinstance(item, dict)]
    completed_rows = [row for row in rows if str(row.get("adoption_status") or "") != "error"]
    return completed_rows, runs


def _phase4_report(
    *,
    status: str,
    cluster_id: str,
    budgets: list[int],
    top_k: int,
    include_tool_events: bool,
    dry_run: bool,
    rows: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    started: float,
) -> dict[str, Any]:
    return {
        "status": status,
        "phase": "phase4_low_cost_gepa_apo",
        "cluster_id": cluster_id.upper(),
        "budgets": budgets,
        "top_k": top_k,
        "include_tool_events": include_tool_events,
        "dry_run": dry_run,
        "split_policy": "deterministic_trace_id_hash",
        "conditions": PHASE4_CONDITIONS,
        "rows": rows,
        "runs": runs,
        "failures": failures,
        "elapsed_sec": _round(time.perf_counter() - started),
        "report_path": str(GEPA_PHASE4_REPORT_PATH),
        "notes": [
            "Phase 4 compares low-cost GEPA/APO controls under a fixed split.",
            "GEPA candidates remain drafts and are not promoted by this command.",
            "not_adopted rows are expected evidence for future EPM memory.",
            "The report is written after every row so long GEPA sweeps can resume.",
        ],
    }


def _write_phase4_report(**kwargs: Any) -> dict[str, Any]:
    report = _phase4_report(**kwargs)
    redacted = _redact_secrets(report)
    write_json(GEPA_PHASE4_REPORT_PATH, redacted)
    return redacted


def evaluate_gepa_phase4(
    cluster_id: str,
    *,
    budgets: str | list[int] | tuple[int, ...] = "5,10,25,50",
    input_path: str | Path = DATA_DIR / "sample_traces.jsonl",
    processed_path: str | Path = DATA_DIR / "processed_traces.jsonl",
    tool_events_path: str | Path | None = None,
    include_tool_events: bool = True,
    top_k: int = 3,
    env_path: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    no_thinking: bool = False,
    dry_run: bool = False,
    output_dir: str | Path | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    parsed_budgets = _parse_budgets(budgets)
    started = time.perf_counter()
    specs = _phase4_condition_specs(parsed_budgets)
    rows, runs = _load_phase4_resume_rows(
        cluster_id=cluster_id,
        budgets=parsed_budgets,
        top_k=top_k,
        dry_run=dry_run,
    ) if resume else ([], [])
    completed = {_phase4_row_key(row) for row in rows}
    failures: list[dict[str, Any]] = []
    _write_phase4_report(
        status="running",
        cluster_id=cluster_id,
        budgets=parsed_budgets,
        top_k=top_k,
        include_tool_events=include_tool_events,
        dry_run=dry_run,
        rows=rows,
        runs=runs,
        failures=failures,
        started=started,
    )
    for spec in specs:
        if _phase4_spec_key(spec) in completed:
            continue
        run_budget = max(1, int(spec["budget"] or min(parsed_budgets)))
        run_dry = bool(dry_run or spec["dry_run"])
        try:
            report = evaluate_gepa(
                cluster_id,
                budget=run_budget,
                input_path=input_path,
                processed_path=processed_path,
                tool_events_path=tool_events_path,
                include_tool_events=include_tool_events,
                top_k=top_k,
                env_path=env_path,
                model=model,
                base_url=base_url,
                max_tokens=max_tokens,
                temperature=temperature,
                no_thinking=no_thinking,
                dry_run=run_dry,
                output_dir=(Path(output_dir) / spec["condition"] / str(spec["budget"])) if output_dir else None,
                condition=str(spec["condition"]),
                memory_policy=str(spec["memory_policy"]),
                racing_policy=str(spec["racing_policy"]),
                judge_policy=str(spec["judge_policy"]),
                write_report=False,
            )
            row = dict(report.get("experiment", {}))
            if spec["condition"] == "local_evolved":
                row["budget"] = 0
            rows.append(row)
            completed.add(_phase4_row_key(row))
            runs.append(
                {
                    "condition": spec["condition"],
                    "budget": spec["budget"],
                    "status": report.get("status"),
                    "adoption_recommendation": report.get("adoption_recommendation", {}),
                    "cost": report.get("cost", {}),
                }
            )
            _write_phase4_report(
                status="running",
                cluster_id=cluster_id,
                budgets=parsed_budgets,
                top_k=top_k,
                include_tool_events=include_tool_events,
                dry_run=dry_run,
                rows=rows,
                runs=runs,
                failures=failures,
                started=started,
            )
        except Exception as exc:
            failure = {
                "condition": spec["condition"],
                "budget": spec["budget"],
                "memory_policy": spec["memory_policy"],
                "racing_policy": spec["racing_policy"],
                "judge_policy": spec["judge_policy"],
                "error": str(exc),
            }
            failures.append(failure)
            rows.append(
                {
                    "condition": spec["condition"],
                    "cluster_id": cluster_id.upper(),
                    "budget": spec["budget"],
                    "memory_policy": spec["memory_policy"],
                    "racing_policy": spec["racing_policy"],
                    "judge_policy": spec["judge_policy"],
                    "metric_calls": 0,
                    "reflection_calls": 0,
                    "judge_calls": 0,
                    "elapsed_sec": 0.0,
                    "heldout": {},
                    "safety_false_negative_rate": None,
                    "adoption_status": "error",
                    "not_adopted_reason": str(exc),
                    "top_k": top_k,
                }
            )
            _write_phase4_report(
                status="partial",
                cluster_id=cluster_id,
                budgets=parsed_budgets,
                top_k=top_k,
                include_tool_events=include_tool_events,
                dry_run=dry_run,
                rows=rows,
                runs=runs,
                failures=failures,
                started=started,
            )
            continue
    return _write_phase4_report(
        status="ok" if not failures else "partial",
        cluster_id=cluster_id,
        budgets=parsed_budgets,
        top_k=top_k,
        include_tool_events=include_tool_events,
        dry_run=dry_run,
        rows=rows,
        runs=runs,
        failures=failures,
        started=started,
    )
