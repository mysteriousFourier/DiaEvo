from __future__ import annotations

import json
from pathlib import Path

from skillminer.paths import DATA_DIR, REPORTS_DIR
from skillminer.storage import read_json

from .theme import SUBTITLE, TITLE
from .widgets import ascii_logo, compact_stat, table


def _load_reports() -> tuple[dict, dict, dict]:
    ingest = read_json(REPORTS_DIR / "ingest_summary.json", default={}) or {}
    mining = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    recommendations = read_json(REPORTS_DIR / "recommendations.json", default={}) or {}
    return ingest, mining, recommendations


def render_plain() -> str:
    ingest, mining, recommendations = _load_reports()
    lines = [ascii_logo(), f"{TITLE} - {SUBTITLE}", ""]
    lines.append("Project")
    lines.append(compact_stat("Path", Path.cwd()))
    lines.append(compact_stat("Data", DATA_DIR / "processed_traces.jsonl"))
    lines.append(compact_stat("Mode", "TF-IDF + K-Means + Apriori + PrefixSpan + PageRank"))
    lines.append("")
    lines.append("Stats")
    lines.append(compact_stat("Traces", ingest.get("trace_count", mining.get("trace_count", 0))))
    lines.append(compact_stat("Success rate", ingest.get("success_rate", "n/a")))
    lines.append(compact_stat("Clusters", len(mining.get("clusters", []))))
    lines.append(compact_stat("Rules", len(mining.get("association_rules", []))))
    lines.append(compact_stat("Sequences", len(mining.get("frequent_sequences", []))))
    lines.append("")
    clusters = mining.get("clusters", [])[:5]
    if clusters:
        lines.append("Mining Panel")
        lines.append(
            table(
                ["Cluster", "Size", "Gap", "Representative task"],
                [
                    [
                        cluster.get("id", ""),
                        cluster.get("size", ""),
                        cluster.get("coverage_gap", ""),
                        cluster.get("representative_task", ""),
                    ]
                    for cluster in clusters
                ],
            )
        )
        lines.append("")
    recs = recommendations.get("recommendations", [])[:5]
    if recs:
        lines.append("Recommendation Panel")
        lines.append(
            table(
                ["Skill", "Score", "Risk", "Reason"],
                [[rec.get("skill", ""), rec.get("score", ""), rec.get("risk", ""), rec.get("reason", "")] for rec in recs],
            )
        )
        lines.append("")
    lines.append("Commands")
    lines.append("Type a normal prompt to chat with DeepSeek.")
    lines.append("/ingest  /mine  /recommend <task>  /generate C03  /verify C03  /demo  /exit")
    lines.append("Scriptable form: .\\skillminer.ps1 demo")
    return "\n".join(lines)


def render_rich() -> bool:
    try:
        from rich import box
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
    except ImportError:
        return False
    ingest, mining, recommendations = _load_reports()
    console = Console()
    console.print(Panel.fit(f"[bold cyan]{ascii_logo()}[/]\n[bold]{SUBTITLE}[/]", border_style="cyan"))
    stats = Table(box=box.SIMPLE_HEAVY)
    stats.add_column("Metric")
    stats.add_column("Value")
    stats.add_row("Path", str(Path.cwd()))
    stats.add_row("Mode", "TF-IDF + K-Means + Apriori + PrefixSpan + PageRank")
    stats.add_row("Traces", str(ingest.get("trace_count", mining.get("trace_count", 0))))
    stats.add_row("Success rate", str(ingest.get("success_rate", "n/a")))
    stats.add_row("Clusters", str(len(mining.get("clusters", []))))
    stats.add_row("Rules", str(len(mining.get("association_rules", []))))
    console.print(Panel(stats, title="Status", border_style="blue"))
    clusters = Table(box=box.SIMPLE)
    clusters.add_column("Cluster")
    clusters.add_column("Size")
    clusters.add_column("Gap")
    clusters.add_column("Representative")
    for cluster in mining.get("clusters", [])[:5]:
        clusters.add_row(
            str(cluster.get("id", "")),
            str(cluster.get("size", "")),
            str(cluster.get("coverage_gap", "")),
            str(cluster.get("representative_task", ""))[:80],
        )
    console.print(Panel(clusters, title="Mining Panel", border_style="magenta"))
    rec_table = Table(box=box.SIMPLE)
    rec_table.add_column("Skill")
    rec_table.add_column("Score")
    rec_table.add_column("Risk")
    rec_table.add_column("Reason")
    for rec in recommendations.get("recommendations", [])[:5]:
        rec_table.add_row(str(rec.get("skill", "")), str(rec.get("score", "")), str(rec.get("risk", "")), str(rec.get("reason", "")))
    console.print(Panel(rec_table, title="Recommendation Panel", border_style="green"))
    console.print(Panel(json.dumps({"reports": str(REPORTS_DIR)}, ensure_ascii=False, indent=2), title="Artifacts", border_style="yellow"))
    return True


def main() -> int:
    if not render_rich():
        print(render_plain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
