from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .association_rules import match_rules, trace_items
from .features import FeatureStore, cosine
from .ingest import load_plugins, load_skill_registry, load_traces
from .models import PluginRecord, SkillRecord, TraceRecord
from .paths import DATA_DIR, REPORTS_DIR, ensure_project_dirs
from .skill_graph import build_skill_graph, personalized_pagerank, seeds_for_task
from .storage import read_json, write_json


DEFAULT_WEIGHTS = {
    "similarity": 0.32,
    "rules": 0.17,
    "pagerank": 0.17,
    "usage_decay": 0.10,
    "success_rate": 0.14,
    "coverage_gap": 0.08,
    "recent_reuse": 0.05,
    "risk": 0.17,
    "cost": 0.07,
}


@dataclass(slots=True)
class Recommendation:
    skill: str
    score: float
    similarity: float
    rule_confidence: float
    pagerank: float
    usage_decay: float
    success_rate: float
    coverage_gap: float
    recent_reuse: float
    risk: float
    cost: float
    installed: bool
    source: str
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "score": round(self.score, 4),
            "similarity": round(self.similarity, 4),
            "rule_confidence": round(self.rule_confidence, 4),
            "pagerank": round(self.pagerank, 4),
            "usage_decay": round(self.usage_decay, 4),
            "success_rate": round(self.success_rate, 4),
            "coverage_gap": round(self.coverage_gap, 4),
            "recent_reuse": round(self.recent_reuse, 4),
            "risk": round(self.risk, 4),
            "cost": round(self.cost, 4),
            "installed": self.installed,
            "source": self.source,
            "reason": self.reason,
        }


def usage_decay(skill: SkillRecord, now: datetime | None = None, half_life_days: float = 7.0) -> float:
    if skill.usage_count <= 0:
        return 0.0
    timestamp = skill.last_used
    if not timestamp:
        return min(1.0, math.log1p(skill.usage_count) / 3.0)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return min(1.0, math.log1p(skill.usage_count) / 3.0)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_days = max(0.0, (current - parsed).total_seconds() / 86400)
    recency = 0.5 ** (age_days / half_life_days)
    frequency = min(1.0, math.log1p(skill.usage_count) / 3.0)
    return max(0.0, min(1.0, 0.65 * recency + 0.35 * frequency))


def _candidate_skills(registry: list[SkillRecord], plugins: list[PluginRecord]) -> list[SkillRecord]:
    merged: dict[str, SkillRecord] = {}
    for skill in registry:
        merged[skill.name] = skill
    for plugin in plugins:
        generated = plugin.as_skill()
        merged.setdefault(generated.name, generated)
    return list(merged.values())


def load_recommender_weights(path: str | Path | None = None) -> dict[str, float]:
    target = Path(path) if path else DATA_DIR / "recommender_weights.json"
    configured = read_json(target, default={})
    active = dict(DEFAULT_WEIGHTS)
    if isinstance(configured, dict):
        for key, value in configured.items():
            if key not in active:
                continue
            try:
                active[key] = float(value)
            except (TypeError, ValueError):
                continue
    return active


def pareto_rerank_recommendations(recommendations: list[Recommendation]) -> list[Recommendation]:
    objectives = ("score", "similarity", "rule_confidence", "pagerank", "success_rate", "coverage_gap", "recent_reuse")
    frontier: list[Recommendation] = []
    remaining = list(recommendations)
    while remaining:
        layer: list[Recommendation] = []
        for item in remaining:
            dominated = False
            for other in remaining:
                if other is item:
                    continue
                better_or_equal = (
                    all(getattr(other, key) >= getattr(item, key) for key in objectives)
                    and other.risk <= item.risk
                    and other.cost <= item.cost
                )
                strictly_better = (
                    any(getattr(other, key) > getattr(item, key) for key in objectives)
                    or other.risk < item.risk
                    or other.cost < item.cost
                )
                if better_or_equal and strictly_better:
                    dominated = True
                    break
            if not dominated:
                layer.append(item)
        layer.sort(key=lambda value: (-value.score, value.risk, value.cost, value.skill))
        frontier.extend(layer)
        layer_ids = {id(item) for item in layer}
        remaining = [item for item in remaining if id(item) not in layer_ids]
    return frontier


def _pseudo_trace_for_task(task: str, project_language: str = "", frameworks: list[str] | None = None) -> TraceRecord:
    lowered = task.lower()
    tags: list[str] = []
    tools: list[str] = []
    if any(term in lowered for term in ("pytest", "测试", "test", "单元")):
        tags.extend(["testing", "debug"])
        tools.append("pytest")
    if any(term in lowered for term in ("修复", "失败", "failure", "debug", "报错")):
        tags.append("debug")
    if any(term in lowered for term in ("skill", "技能")):
        tags.append("skill")
    if any(term in lowered for term in ("推荐", "recommend", "排序")):
        tags.append("recommendation")
    if any(term in lowered for term in ("前端", "react", "vite", "ui", "截图")):
        tags.extend(["frontend", "ui"])
    return TraceRecord(
        id="query",
        task=task,
        project_language=project_language,
        frameworks=frameworks or [],
        files=[],
        tools=tools,
        outcome="unknown",
        tags=sorted(set(tags)),
    )


def _cluster_signal_for_skill(skill: SkillRecord, clusters: list[dict[str, Any]]) -> tuple[float, float]:
    skill_terms = {token.lower() for token in [skill.name, *skill.tags]}
    best_gap = 0.0
    best_reuse = 0.0
    for cluster in clusters:
        cluster_terms = {
            str(value).lower()
            for value in [
                *cluster.get("top_terms", []),
                *cluster.get("top_tools", []),
                *cluster.get("top_errors", []),
                *cluster.get("top_failure_types", []),
            ]
        }
        if not skill_terms.intersection(cluster_terms):
            continue
        best_gap = max(best_gap, float(cluster.get("coverage_gap", 0.0) or 0.0))
        best_reuse = max(best_reuse, min(1.0, float(cluster.get("tool_reuse_count", 0) or 0) / 5.0))
    return best_gap, best_reuse


def recommend(
    task: str,
    traces_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    plugin_path: str | Path | None = None,
    top_k: int = 5,
    project_language: str = "",
    frameworks: list[str] | None = None,
    weights: dict[str, float] | None = None,
    weights_path: str | Path | None = None,
    rerank: str = "weighted",
) -> dict[str, Any]:
    ensure_project_dirs()
    trace_source = Path(traces_path) if traces_path else DATA_DIR / "processed_traces.jsonl"
    if not trace_source.exists():
        trace_source = DATA_DIR / "sample_traces.jsonl"
    traces = load_traces(trace_source)
    registry = load_skill_registry(registry_path)
    plugins = load_plugins(plugin_path)
    skills = _candidate_skills(registry, plugins)
    feature_store = FeatureStore.from_documents([trace.document for trace in traces] + [skill.document for skill in skills])
    query_vector = feature_store.vectorize(task)
    skill_offset = len(traces)
    mining_report = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    rules = mining_report.get("association_rules", [])
    if not isinstance(rules, list):
        rules = []
    clusters = mining_report.get("clusters", [])
    if not isinstance(clusters, list):
        clusters = []
    query_trace = _pseudo_trace_for_task(task, project_language=project_language, frameworks=frameworks)
    project_items = trace_items(query_trace)
    matched_rules = match_rules(project_items, rules)
    best_rule_by_skill: dict[str, float] = {}
    for rule in matched_rules:
        skill_name = str(rule.get("skill", ""))
        confidence = float(rule.get("confidence", 0.0) or 0.0)
        best_rule_by_skill[skill_name] = max(best_rule_by_skill.get(skill_name, 0.0), confidence)
    graph = build_skill_graph(traces, registry, plugins)
    ranks = personalized_pagerank(graph, seeds_for_task(task, project_items))
    max_rank = max((score for node, score in ranks.items() if node.startswith("skill:")), default=1.0)
    active_weights = load_recommender_weights(weights_path)
    if weights:
        active_weights.update(weights)
    recommendations: list[Recommendation] = []
    for index, skill in enumerate(skills):
        similarity = cosine(query_vector, feature_store.vectors[skill_offset + index])
        rule_score = best_rule_by_skill.get(skill.name, 0.0)
        raw_rank = ranks.get(f"skill:{skill.name}", 0.0)
        rank_score = raw_rank / max_rank if max_rank else 0.0
        decay = usage_decay(skill)
        success = skill.success_rate
        coverage_gap, recent_reuse = _cluster_signal_for_skill(skill, clusters)
        score = (
            active_weights["similarity"] * similarity
            + active_weights["rules"] * rule_score
            + active_weights["pagerank"] * rank_score
            + active_weights["usage_decay"] * decay
            + active_weights["success_rate"] * success
            + active_weights["coverage_gap"] * coverage_gap
            + active_weights["recent_reuse"] * recent_reuse
            - active_weights["risk"] * skill.risk
            - active_weights["cost"] * skill.cost
        )
        reason_parts = []
        if similarity > 0.2:
            reason_parts.append("semantic match")
        if rule_score > 0:
            reason_parts.append(f"rule confidence {rule_score:.2f}")
        if rank_score > 0.15:
            reason_parts.append("graph proximity")
        if skill.risk >= 0.7:
            reason_parts.append("high risk penalty")
        if coverage_gap > 0:
            reason_parts.append(f"coverage gap {coverage_gap:.2f}")
        if recent_reuse > 0:
            reason_parts.append(f"recent reuse {recent_reuse:.2f}")
        if not reason_parts:
            reason_parts.append("baseline prior")
        recommendations.append(
            Recommendation(
                skill=skill.name,
                score=score,
                similarity=similarity,
                rule_confidence=rule_score,
                pagerank=rank_score,
                usage_decay=decay,
                success_rate=success,
                coverage_gap=coverage_gap,
                recent_reuse=recent_reuse,
                risk=skill.risk,
                cost=skill.cost,
                installed=skill.installed,
                source=skill.source,
                reason=", ".join(reason_parts),
            )
        )
    recommendations.sort(key=lambda item: item.score, reverse=True)
    if rerank == "pareto":
        recommendations = pareto_rerank_recommendations(recommendations)
    result = {
        "task": task,
        "trace_source": str(trace_source),
        "top_k": top_k,
        "weights": active_weights,
        "rerank": rerank,
        "recommendations": [item.to_mapping() for item in recommendations[:top_k]],
    }
    write_json(REPORTS_DIR / "recommendations.json", result)
    return result
