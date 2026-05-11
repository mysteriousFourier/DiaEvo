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
    file_extensions: list[str]
    used_skills: list[str]
    coverage_gap: float
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
            "file_extensions": self.file_extensions,
            "used_skills": self.used_skills,
            "coverage_gap": round(self.coverage_gap, 4),
            "representative_task": self.representative_task,
        }


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
        representative_index = max(indexes, key=lambda index: cosine(features.vectors[index], center))
        success_count = sum(1 for trace in members if trace.success)
        size = len(members)
        used_skill_count = sum(1 for trace in members if trace.used_skills)
        failure_rate = 1.0 - (success_count / size if size else 0.0)
        no_skill_rate = 1.0 - (used_skill_count / size if size else 0.0)
        retry_pressure = min(1.0, sum(trace.retries for trace in members) / max(1, size * 3))
        coverage_gap = (0.45 * failure_rate) + (0.35 * no_skill_rate) + (0.20 * retry_pressure)
        summaries.append(
            ClusterSummary(
                id=f"C{display_index:02d}",
                trace_ids=[trace.id for trace in members],
                size=size,
                success_rate=success_count / size if size else 0.0,
                failure_rate=failure_rate,
                avg_retries=sum(trace.retries for trace in members) / size if size else 0.0,
                top_terms=top_terms(center, features.vocabulary, limit=8),
                top_tools=_dominant(tool for trace in members for tool in trace.tools),
                top_errors=_dominant(trace.error_type for trace in members if trace.error_type),
                file_extensions=_dominant(ext for trace in members for ext in trace.file_extensions),
                used_skills=_dominant(skill for trace in members for skill in trace.used_skills),
                coverage_gap=coverage_gap,
                representative_task=traces[representative_index].task,
            )
        )
    return sorted(summaries, key=lambda item: (-item.coverage_gap, item.id))


def cluster_traces(traces: list[TraceRecord], k: int | None = None) -> tuple[FeatureStore, list[int], list[ClusterSummary]]:
    features = FeatureStore.from_traces(traces)
    assignments = kmeans(features.vectors, k=k)
    summaries = summarize_clusters(traces, features, assignments)
    return features, assignments, summaries
