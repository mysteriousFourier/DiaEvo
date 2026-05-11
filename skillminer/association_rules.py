from __future__ import annotations

from collections import Counter
from itertools import combinations

from .models import TraceRecord


def trace_items(trace: TraceRecord) -> set[str]:
    items: set[str] = set()
    if trace.project_language:
        items.add(f"lang:{trace.project_language.lower()}")
    items.update(f"framework:{value.lower()}" for value in trace.frameworks)
    items.update(f"ext:{value}" for value in trace.file_extensions)
    items.update(f"tool:{value.lower()}" for value in trace.tools)
    items.update(f"tag:{value.lower()}" for value in trace.tags)
    if trace.error_type:
        items.add(f"error:{trace.error_type}")
    if trace.success:
        items.add("outcome:success")
    else:
        items.add("outcome:failure")
    return items


def mine_association_rules(
    traces: list[TraceRecord],
    min_support: int = 2,
    min_confidence: float = 0.5,
    max_antecedent: int = 3,
) -> list[dict[str, object]]:
    antecedent_counts: Counter[tuple[str, ...]] = Counter()
    rule_counts: Counter[tuple[tuple[str, ...], str]] = Counter()
    consequent_counts: Counter[str] = Counter()
    transaction_count = 0
    for trace in traces:
        if not trace.used_skills:
            continue
        items = sorted(trace_items(trace))
        transaction_count += 1
        consequents = [f"skill:{skill}" for skill in trace.used_skills]
        for size in range(1, min(max_antecedent, len(items)) + 1):
            for combo in combinations(items, size):
                antecedent_counts[combo] += 1
                for consequent in consequents:
                    rule_counts[(combo, consequent)] += 1
        consequent_counts.update(consequents)
    rules: list[dict[str, object]] = []
    for (antecedent, consequent), support in rule_counts.items():
        if support < min_support:
            continue
        confidence = support / antecedent_counts[antecedent]
        if confidence < min_confidence:
            continue
        consequent_support = consequent_counts[consequent] / max(1, transaction_count)
        lift = confidence / consequent_support if consequent_support else 0.0
        rules.append(
            {
                "antecedent": list(antecedent),
                "consequent": consequent,
                "skill": consequent.removeprefix("skill:"),
                "support": support,
                "confidence": round(confidence, 4),
                "lift": round(lift, 4),
            }
        )
    return sorted(
        rules,
        key=lambda item: (-float(item["confidence"]), -float(item["lift"]), -int(item["support"]), item["skill"]),
    )


def match_rules(items: set[str], rules: list[dict[str, object]]) -> list[dict[str, object]]:
    matched = []
    for rule in rules:
        antecedent = set(str(item) for item in rule.get("antecedent", []))
        if antecedent and antecedent.issubset(items):
            matched.append(rule)
    return matched
