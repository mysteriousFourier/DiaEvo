from pathlib import Path

from diaevo.cli import build_parser
from diaevo.mining_snapshot import export_mining_snapshot
from diaevo.storage import read_json


def test_export_mining_snapshot_writes_human_readable_files(tmp_path):
    output_dir = tmp_path / "snapshot"

    result = export_mining_snapshot(
        input_path="data/sample_traces.jsonl",
        processed_path=tmp_path / "processed.jsonl",
        output_dir=output_dir,
        include_tool_events=False,
        date="260513",
    )

    assert result["status"] == "ok"
    assert result["date"] == "260513"
    assert result["cluster_count"] > 0
    assert result["association_rule_count"] > 0
    assert result["frequent_sequence_count"] > 0
    for name in [
        "README.md",
        "clusters.csv",
        "clusters.md",
        "association_rules.csv",
        "association_rules.md",
        "frequent_sequences.csv",
        "frequent_sequences.md",
        "skill_coverage_gaps.csv",
        "skill_coverage_gaps.md",
        "graph_edges.csv",
        "summary.json",
    ]:
        assert (output_dir / name).exists()
    readme = (output_dir / "README.md").read_text(encoding="utf-8")
    assert "挖掘快照 260513" in readme
    assert "关键发现" in readme
    summary = read_json(output_dir / "summary.json")
    assert summary["snapshot_dir"] == str(output_dir)


def test_cli_accepts_export_mining_snapshot_args():
    args = build_parser().parse_args(
        [
            "export-mining-snapshot",
            "--date",
            "260513",
            "--clusters",
            "4",
            "--include-tool-events",
        ]
    )

    assert args.command == "export-mining-snapshot"
    assert args.date == "260513"
    assert args.clusters == 4
    assert args.include_tool_events is True
