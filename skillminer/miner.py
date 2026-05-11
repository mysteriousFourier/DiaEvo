from __future__ import annotations

from pathlib import Path
from typing import Any

from .association_rules import mine_association_rules
from .clustering import cluster_traces
from .ingest import load_plugins, load_skill_registry, load_traces
from .paths import DATA_DIR, REPORTS_DIR, ensure_project_dirs
from .sequence_mining import mine_frequent_sequences
from .skill_graph import build_skill_graph
from .storage import write_json


def mine(
    traces_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    plugin_path: str | Path | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    ensure_project_dirs()
    trace_source = Path(traces_path) if traces_path else DATA_DIR / "processed_traces.jsonl"
    if not trace_source.exists():
        trace_source = DATA_DIR / "sample_traces.jsonl"
    traces = load_traces(trace_source)
    skills = load_skill_registry(registry_path)
    plugins = load_plugins(plugin_path)
    features, assignments, clusters = cluster_traces(traces, k=k)
    rules = mine_association_rules(traces)
    sequences = mine_frequent_sequences(traces)
    graph = build_skill_graph(traces, skills, plugins)
    result = {
        "trace_source": str(trace_source),
        "trace_count": len(traces),
        "feature_count": len(features.vocabulary),
        "assignments": {trace.id: f"C{assignments[index] + 1:02d}" for index, trace in enumerate(traces)},
        "clusters": [cluster.to_mapping() for cluster in clusters],
        "association_rules": rules[:100],
        "frequent_sequences": sequences[:100],
        "graph": {
            "node_count": len(graph),
            "edge_count": sum(len(edges) for edges in graph.values()) // 2,
        },
    }
    write_json(REPORTS_DIR / "mining_report.json", result)
    return result
