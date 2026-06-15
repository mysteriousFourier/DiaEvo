from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .features import FeatureStore, cosine, top_terms
from .models import TraceRecord


def euclidean(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((a - b) * (a - b) for a, b in zip(left, right)))


def centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    size = len(vectors[0])
    center = [0.0] * size
    for vector in vectors:
        for index, value in enumerate(vector):
            center[index] += value
    return [value / len(vectors) for value in center]


def choose_k(sample_count: int, requested: int | None = None) -> int:
    if sample_count <= 0:
        return 0
    if requested:
        return max(1, min(requested, sample_count))
    return max(1, min(6, round(math.sqrt(sample_count / 2))))


def kmeans(
    vectors: list[list[float]],
    k: int | None = None,
    max_iter: int = 40,
    seed: int = 13,
) -> list[int]:
    if not vectors:
        return []
    cluster_count = choose_k(len(vectors), k)
    if cluster_count == 1:
        return [0] * len(vectors)
    rng = random.Random(seed)
    non_empty = [index for index, vector in enumerate(vectors) if any(vector)]
    if len(non_empty) < cluster_count:
        non_empty = list(range(len(vectors)))
    centers = [vectors[index][:] for index in rng.sample(non_empty, cluster_count)]
    assignments = [-1] * len(vectors)
    for _ in range(max_iter):
        changed = False
        for row, vector in enumerate(vectors):
            distances = [euclidean(vector, center) for center in centers]
            label = min(range(cluster_count), key=lambda index: distances[index])
            if assignments[row] != label:
                assignments[row] = label
                changed = True
        if not changed:
            break
        for label in range(cluster_count):
            members = [vectors[index] for index, assigned in enumerate(assignments) if assigned == label]
            if members:
                centers[label] = centroid(members)
            else:
                centers[label] = vectors[rng.randrange(len(vectors))][:]
    return assignments


def _dominant(values: Iterable[str], limit: int = 5) -> list[str]:
    return [value for value, _ in Counter(value for value in values if value).most_common(limit)]


@dataclass(slots=True)
class ClusterSummary:
    id: str
    trace_ids: list[str]
    size: int
    success_rate: float
    failure_rate: float
    avg_retries: float
    top_terms: list[str]
    top_tools: list[str]
    top_errors: list[str]
    top_failure_types: list[str]
    file_extensions: list[str]
    used_skills: list[str]
    source_counts: dict[str, int]
    event_count: int
    tool_success_rate: float
    tool_reuse_count: int
    coverage_gap: float
    explanations: list[dict[str, object]]
    representative_task: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "trace_ids": self.trace_ids,
            "size": self.size,
            "success_rate": round(self.success_rate, 4),
            "failure_rate": round(self.failure_rate, 4),
            "avg_retries": round(self.avg_retries, 4),
            "top_terms": self.top_terms,
            "top_tools": self.top_tools,
            "top_errors": self.top_errors,
            "top_failure_types": self.top_failure_types,
            "file_extensions": self.file_extensions,
            "used_skills": self.used_skills,
            "source_counts": self.source_counts,
            "event_count": self.event_count,
            "tool_success_rate": round(self.tool_success_rate, 4),
            "tool_reuse_count": self.tool_reuse_count,
            "coverage_gap": round(self.coverage_gap, 4),
            "explanations": self.explanations,
            "representative_task": self.representative_task,
        }


def _cluster_explanations(
    *,
    failure_rate: float,
    no_skill_rate: float,
    retry_pressure: float,
    coverage_gap: float,
    top_errors: list[str],
    top_failure_types: list[str],
    top_tools: list[str],
    tool_reuse_count: int,
    tool_success_rate: float,
    source_counts: dict[str, int],
) -> list[dict[str, object]]:
    explanations: list[dict[str, object]] = []
    if coverage_gap >= 0.35 or no_skill_rate >= 0.5:
        explanations.append(
            {
                "type": "coverage_gap",
                "score": round(coverage_gap, 4),
                "reason": "High failure, retry, or no-skill rate indicates work that is not well covered by existing skills.",
                "signals": {
                    "failure_rate": round(failure_rate, 4),
                    "no_skill_rate": round(no_skill_rate, 4),
                    "retry_pressure": round(retry_pressure, 4),
                },
            }
        )
    if failure_rate >= 0.25 or top_errors or top_failure_types:
        explanations.append(
            {
                "type": "failure_hotspot",
                "score": round(max(failure_rate, 1.0 - tool_success_rate if source_counts.get("tool_event") else 0.0), 4),
                "reason": "Repeated failures or tool errors suggest a workflow that needs explicit recovery guidance.",
                "signals": {
                    "top_errors": top_errors[:5],
                    "tool_failure_types": top_failure_types[:5],
                    "tool_success_rate": round(tool_success_rate, 4),
                },
            }
        )
    if tool_reuse_count > 0 or len(top_tools) >= 3:
        explanations.append(
            {
                "type": "high_reuse_path",
                "score": round(min(1.0, (tool_reuse_count / 5.0) + (len(top_tools) / 10.0)), 4),
                "reason": "A recurring tool sequence can be captured as a reusable operational path.",
                "signals": {
                    "top_tools": top_tools[:6],
                    "tool_reuse_count": tool_reuse_count,
                    "source_counts": source_counts,
                },
            }
        )
    if not explanations:
        explanations.append(
            {
                "type": "baseline_pattern",
                "score": round(max(coverage_gap, 0.1), 4),
                "reason": "The cluster groups semantically similar tasks but does not yet show a strong automation signal.",
                "signals": {"top_tools": top_tools[:5]},
            }
        )
    return explanations


def summarize_clusters(
    traces: list[TraceRecord],
    features: FeatureStore,
    assignments: list[int],
) -> list[ClusterSummary]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(assignments):
        grouped[label].append(index)
    summaries: list[ClusterSummary] = []
    for display_index, label in enumerate(sorted(grouped), start=1):
        indexes = grouped[label]
        members = [traces[index] for index in indexes]
        vectors = [features.vectors[index] for index in indexes]
        center = centroid(vectors)
        representative_candidates = [
            index
            for index in indexes
            if (traces[index].source or "trace") != "tool_event"
        ] or indexes
        representative_index = max(representative_candidates, key=lambda index: cosine(features.vectors[index], center))
        success_count = sum(1 for trace in members if trace.success)
        size = len(members)
        used_skill_count = sum(1 for trace in members if trace.used_skills)
        failure_rate = 1.0 - (success_count / size if size else 0.0)
        no_skill_rate = 1.0 - (used_skill_count / size if size else 0.0)
        retry_pressure = min(1.0, sum(trace.retries for trace in members) / max(1, size * 3))
        event_count = sum(trace.event_count for trace in members)
        tool_reuse_count = sum(trace.tool_reuse_count for trace in members)
        event_traces = [trace for trace in members if trace.event_count]
        tool_success_rate = (
            sum(trace.tool_success_rate for trace in event_traces) / len(event_traces) if event_traces else 0.0
        )
        tool_failure_pressure = 1.0 - tool_success_rate if event_traces else 0.0
        coverage_gap = (
            (0.38 * failure_rate)
            + (0.28 * no_skill_rate)
            + (0.18 * retry_pressure)
            + (0.16 * tool_failure_pressure)
        )
        top_tools = _dominant(tool for trace in members for tool in trace.tools)
        top_errors = _dominant(trace.error_type for trace in members if trace.error_type)
        top_failure_types = _dominant(failure for trace in members for failure in trace.tool_failure_types)
        source_counts = dict(Counter(trace.source or "trace" for trace in members).most_common())
        summaries.append(
            ClusterSummary(
                id=f"C{display_index:02d}",
                trace_ids=[trace.id for trace in members],
                size=size,
                success_rate=success_count / size if size else 0.0,
                failure_rate=failure_rate,
                avg_retries=sum(trace.retries for trace in members) / size if size else 0.0,
                top_terms=top_terms(center, features.vocabulary, limit=8),
                top_tools=top_tools,
                top_errors=top_errors,
                top_failure_types=top_failure_types,
                file_extensions=_dominant(ext for trace in members for ext in trace.file_extensions),
                used_skills=_dominant(skill for trace in members for skill in trace.used_skills),
                source_counts=source_counts,
                event_count=event_count,
                tool_success_rate=tool_success_rate,
                tool_reuse_count=tool_reuse_count,
                coverage_gap=coverage_gap,
                explanations=_cluster_explanations(
                    failure_rate=failure_rate,
                    no_skill_rate=no_skill_rate,
                    retry_pressure=retry_pressure,
                    coverage_gap=coverage_gap,
                    top_errors=top_errors,
                    top_failure_types=top_failure_types,
                    top_tools=top_tools,
                    tool_reuse_count=tool_reuse_count,
                    tool_success_rate=tool_success_rate,
                    source_counts=source_counts,
                ),
                representative_task=traces[representative_index].task,
            )
        )
    return sorted(summaries, key=lambda item: (-item.coverage_gap, item.id))


def cluster_traces(traces: list[TraceRecord], k: int | None = None) -> tuple[FeatureStore, list[int], list[ClusterSummary]]:
    features = FeatureStore.from_traces(traces)
    assignments = kmeans(features.vectors, k=k)
    summaries = summarize_clusters(traces, features, assignments)
    return features, assignments, summaries
