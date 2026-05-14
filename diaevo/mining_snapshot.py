from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from .ingest import ingest_traces, load_traces
from .miner import mine
from .paths import DATA_DIR, ensure_project_dirs
from .storage import write_json


SNAPSHOT_ROOT = DATA_DIR / "mining_snapshots"


def _date_stamp(value: str | None = None) -> str:
    if value:
        text = value.strip()
        if len(text) == 6 and text.isdigit():
            return text
        parsed = datetime.fromisoformat(text)
        return parsed.strftime("%y%m%d")
    return datetime.now().strftime("%y%m%d")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _join(values: Any, limit: int | None = None) -> str:
    items = [str(item) for item in _as_list(values)]
    if limit is not None:
        items = items[:limit]
    return ", ".join(items)


def _format_float(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "0.0000"


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _cluster_rows(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cluster in clusters:
        explanations = _as_list(cluster.get("explanations"))
        rows.append(
            {
                "cluster_id": cluster.get("id", ""),
                "size": cluster.get("size", 0),
                "representative_task": cluster.get("representative_task", ""),
                "trace_ids": _join(cluster.get("trace_ids")),
                "top_terms": _join(cluster.get("top_terms"), 8),
                "top_tools": _join(cluster.get("top_tools"), 8),
                "used_skills": _join(cluster.get("used_skills"), 8),
                "coverage_gap": _format_float(cluster.get("coverage_gap")),
                "failure_rate": _format_float(cluster.get("failure_rate")),
                "success_rate": _format_float(cluster.get("success_rate")),
                "tool_reuse_count": cluster.get("tool_reuse_count", 0),
                "explanations": "; ".join(
                    str(item.get("type", "")) + ": " + str(item.get("reason", ""))
                    for item in explanations
                    if isinstance(item, dict)
                ),
            }
        )
    return rows


def _rule_rows(rules: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule in rules[:limit]:
        rows.append(
            {
                "antecedent": " + ".join(str(item) for item in _as_list(rule.get("antecedent"))),
                "consequent": rule.get("consequent", ""),
                "skill": rule.get("skill", ""),
                "support": rule.get("support", 0),
                "confidence": _format_float(rule.get("confidence")),
                "lift": _format_float(rule.get("lift")),
            }
        )
    return rows


def _sequence_rows(sequences: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sequences[:limit]:
        rows.append(
            {
                "sequence": " -> ".join(str(value) for value in _as_list(item.get("sequence"))),
                "support": item.get("support", 0),
                "support_rate": _format_float(item.get("support_rate")),
            }
        )
    return rows


def _coverage_rows(entrypoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in entrypoints:
        rows.append(
            {
                "cluster_id": item.get("cluster_id", ""),
                "primary_reason": item.get("primary_reason", ""),
                "coverage_gap": _format_float(item.get("coverage_gap")),
                "failure_rate": _format_float(item.get("failure_rate")),
                "tool_reuse_count": item.get("tool_reuse_count", 0),
                "recommended_action": item.get("recommended_action", ""),
            }
        )
    return rows


def _graph_edge_rows(traces: list[Any]) -> list[dict[str, Any]]:
    weights: dict[tuple[str, str, str], int] = {}
    for trace in traces:
        task_node = f"trace:{trace.id}"
        for tool in trace.tools:
            key = (task_node, f"tool:{tool}", "uses_tool")
            weights[key] = weights.get(key, 0) + 1
        for skill in trace.used_skills:
            key = (task_node, f"skill:{skill}", "uses_skill")
            weights[key] = weights.get(key, 0) + 1
        for skill in trace.used_skills:
            for tool in trace.tools:
                key = (f"skill:{skill}", f"tool:{tool}", "skill_tool_cooccurrence")
                weights[key] = weights.get(key, 0) + 1
    return [
        {"source": source, "target": target, "relation": relation, "weight": weight}
        for (source, target, relation), weight in sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    ]


def _write_markdown_files(snapshot_dir: Path, rows: dict[str, list[dict[str, Any]]]) -> None:
    cluster_table = _markdown_table(
        ["cluster_id", "size", "coverage_gap", "failure_rate", "top_tools", "representative_task"],
        [
            [
                row["cluster_id"],
                row["size"],
                row["coverage_gap"],
                row["failure_rate"],
                row["top_tools"],
                row["representative_task"],
            ]
            for row in rows["clusters"]
        ],
    )
    (snapshot_dir / "clusters.md").write_text("# 任务聚类发现\n\n" + cluster_table + "\n", encoding="utf-8")

    rules_table = _markdown_table(
        ["antecedent", "consequent", "support", "confidence", "lift"],
        [[row["antecedent"], row["consequent"], row["support"], row["confidence"], row["lift"]] for row in rows["rules"]],
    )
    (snapshot_dir / "association_rules.md").write_text("# 关联规则\n\n" + rules_table + "\n", encoding="utf-8")

    sequence_table = _markdown_table(
        ["sequence", "support", "support_rate"],
        [[row["sequence"], row["support"], row["support_rate"]] for row in rows["sequences"]],
    )
    (snapshot_dir / "frequent_sequences.md").write_text("# 频繁工具序列\n\n" + sequence_table + "\n", encoding="utf-8")

    coverage_table = _markdown_table(
        ["cluster_id", "primary_reason", "coverage_gap", "failure_rate", "tool_reuse_count", "recommended_action"],
        [
            [
                row["cluster_id"],
                row["primary_reason"],
                row["coverage_gap"],
                row["failure_rate"],
                row["tool_reuse_count"],
                row["recommended_action"],
            ]
            for row in rows["coverage"]
        ],
    )
    (snapshot_dir / "skill_coverage_gaps.md").write_text("# 技能覆盖缺口\n\n" + coverage_table + "\n", encoding="utf-8")


def _readme_text(*, stamp: str, report: dict[str, Any], rows: dict[str, list[dict[str, Any]]]) -> str:
    clusters = rows["clusters"]
    rules = rows["rules"]
    sequences = rows["sequences"]
    coverage = rows["coverage"]
    top_cluster = max(clusters, key=lambda item: float(item["coverage_gap"])) if clusters else {}
    top_sequence = sequences[0] if sequences else {}
    top_rule = rules[0] if rules else {}
    lines = [
        f"# 挖掘快照 {stamp}",
        "",
        "本文件夹是 DiaEvo 从任务轨迹中导出的可读挖掘证据包，用于直观看到聚类、关联规则、频繁序列、覆盖缺口和图边。",
        "",
        "## 摘要",
        "",
        f"- 轨迹来源：`{report.get('trace_source', '')}`",
        f"- 轨迹数量：`{report.get('trace_count', 0)}`",
        f"- 特征数量：`{report.get('feature_count', 0)}`",
        f"- 聚类数量：`{len(clusters)}`",
        f"- 导出的关联规则：`{len(rules)}`",
        f"- 导出的频繁序列：`{len(sequences)}`",
        f"- 覆盖缺口入口：`{len(coverage)}`",
        f"- 图边数量：`{len(rows['graph_edges'])}`",
        "",
        "## 关键发现",
        "",
    ]
    if top_cluster:
        lines.append(
            f"1. 覆盖缺口最高的簇是 `{top_cluster.get('cluster_id')}`，"
            f"缺口为 `{top_cluster.get('coverage_gap')}`，代表任务：{top_cluster.get('representative_task')}"
        )
    if top_sequence:
        lines.append(
            f"2. 最常见工具序列是 `{top_sequence.get('sequence')}`，支持度为 `{top_sequence.get('support')}`。"
        )
    if top_rule:
        lines.append(
            f"3. 最强导出关联规则：`{top_rule.get('antecedent')}` -> "
            f"`{top_rule.get('consequent')}`，置信度 `{top_rule.get('confidence')}`，提升度 `{top_rule.get('lift')}`。"
        )
    if not any([top_cluster, top_sequence, top_rule]):
        lines.append("当前报告没有可用的挖掘发现。")
    lines.extend(
        [
            "",
            "## 文件说明",
            "",
            "- `clusters.md` / `clusters.csv`：任务簇、代表任务、关键词、工具、覆盖缺口和失败率。",
            "- `association_rules.md` / `association_rules.csv`：从轨迹挖掘出的 trace-to-skill 规则，包含支持度、置信度和提升度。",
            "- `frequent_sequences.md` / `frequent_sequences.csv`：反复出现的工具调用子序列。",
            "- `skill_coverage_gaps.md` / `skill_coverage_gaps.csv`：适合生成或演化候选技能的簇。",
            "- `graph_edges.csv`：trace-skill-tool 共现图边，可用于图可视化。",
            "- `summary.json`：机器可读快照元数据。",
            "",
            "## 报告使用方式",
            "",
            "可将本文件夹作为“系统确实执行了数据挖掘流程”的可见证据，而不是只展示机器可读 JSON。",
            "Markdown 文件适合直接阅读，CSV 文件可导入 Excel 或绘图工具。",
        ]
    )
    return "\n".join(lines) + "\n"


def export_mining_snapshot(
    *,
    traces_path: str | Path | None = None,
    input_path: str | Path | None = None,
    processed_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    plugin_path: str | Path | None = None,
    k: int | None = None,
    date: str | None = None,
    output_dir: str | Path | None = None,
    include_tool_events: bool = False,
) -> dict[str, Any]:
    ensure_project_dirs()
    stamp = _date_stamp(date)
    snapshot_dir = Path(output_dir) if output_dir else SNAPSHOT_ROOT / stamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if traces_path:
        trace_source = Path(traces_path)
    else:
        source_input = Path(input_path) if input_path else DATA_DIR / "sample_traces.jsonl"
        target_processed = Path(processed_path) if processed_path else DATA_DIR / "processed_traces.jsonl"
        ingest_traces(source_input, target_processed, include_tool_events=include_tool_events)
        trace_source = target_processed
    report = mine(trace_source, registry_path=registry_path, plugin_path=plugin_path, k=k)
    traces = load_traces(trace_source)
    rows = {
        "clusters": _cluster_rows(_as_list(report.get("clusters"))),
        "rules": _rule_rows(_as_list(report.get("association_rules"))),
        "sequences": _sequence_rows(_as_list(report.get("frequent_sequences"))),
        "coverage": _coverage_rows(_as_list(report.get("generation_entrypoints"))),
        "graph_edges": _graph_edge_rows(traces),
    }
    _write_csv(
        snapshot_dir / "clusters.csv",
        [
            "cluster_id",
            "size",
            "representative_task",
            "trace_ids",
            "top_terms",
            "top_tools",
            "used_skills",
            "coverage_gap",
            "failure_rate",
            "success_rate",
            "tool_reuse_count",
            "explanations",
        ],
        rows["clusters"],
    )
    _write_csv(
        snapshot_dir / "association_rules.csv",
        ["antecedent", "consequent", "skill", "support", "confidence", "lift"],
        rows["rules"],
    )
    _write_csv(snapshot_dir / "frequent_sequences.csv", ["sequence", "support", "support_rate"], rows["sequences"])
    _write_csv(
        snapshot_dir / "skill_coverage_gaps.csv",
        ["cluster_id", "primary_reason", "coverage_gap", "failure_rate", "tool_reuse_count", "recommended_action"],
        rows["coverage"],
    )
    _write_csv(snapshot_dir / "graph_edges.csv", ["source", "target", "relation", "weight"], rows["graph_edges"])
    _write_markdown_files(snapshot_dir, rows)
    readme = _readme_text(stamp=stamp, report=report, rows=rows)
    (snapshot_dir / "README.md").write_text(readme, encoding="utf-8")
    files = sorted(path.name for path in snapshot_dir.iterdir() if path.is_file())
    if "summary.json" not in files:
        files.append("summary.json")
        files = sorted(files)
    summary = {
        "status": "ok",
        "snapshot_dir": str(snapshot_dir),
        "date": stamp,
        "trace_source": str(trace_source),
        "trace_count": report.get("trace_count", 0),
        "feature_count": report.get("feature_count", 0),
        "cluster_count": len(rows["clusters"]),
        "association_rule_count": len(rows["rules"]),
        "frequent_sequence_count": len(rows["sequences"]),
        "coverage_gap_count": len(rows["coverage"]),
        "graph_edge_count": len(rows["graph_edges"]),
        "files": files,
    }
    write_json(snapshot_dir / "summary.json", summary)
    return summary
