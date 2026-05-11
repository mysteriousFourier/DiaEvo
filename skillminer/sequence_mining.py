from __future__ import annotations

from collections import Counter
from itertools import combinations

from .models import TraceRecord


def _is_subsequence(pattern: tuple[str, ...], sequence: list[str]) -> bool:
    if not pattern:
        return True
    pos = 0
    for item in sequence:
        if item == pattern[pos]:
            pos += 1
            if pos == len(pattern):
                return True
    return False


def mine_frequent_sequences(
    traces: list[TraceRecord],
    min_support: int = 2,
    max_len: int = 4,
    successful_only: bool = True,
) -> list[dict[str, object]]:
    sequences = [trace.tools for trace in traces if trace.tools and (trace.success or not successful_only)]
    counts: Counter[tuple[str, ...]] = Counter()
    for sequence in sequences:
        unique_patterns: set[tuple[str, ...]] = set()
        indexes = range(len(sequence))
        for size in range(1, min(max_len, len(sequence)) + 1):
            for combo in combinations(indexes, size):
                pattern = tuple(sequence[index] for index in combo)
                unique_patterns.add(pattern)
        counts.update(unique_patterns)
    results = [
        {
            "sequence": list(pattern),
            "support": support,
            "support_rate": round(support / max(1, len(sequences)), 4),
        }
        for pattern, support in counts.items()
        if support >= min_support
    ]
    return sorted(results, key=lambda item: (-int(item["support"]), -len(item["sequence"]), item["sequence"]))


def matching_sequences(task_tools: list[str], patterns: list[dict[str, object]]) -> list[dict[str, object]]:
    matches = []
    for pattern in patterns:
        sequence = [str(item) for item in pattern.get("sequence", [])]
        if _is_subsequence(tuple(sequence), task_tools):
            matches.append(pattern)
    return matches
