from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .generator import generate_skill
from .ingest import ingest_traces
from .miner import mine
from .paths import DATA_DIR, ensure_project_dirs
from .recommender import recommend
from .verifier import verify_skill


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillminer",
        description="Mine, recommend, generate, and verify Agent skills from task traces.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Validate and normalize JSONL trace data.")
    ingest_parser.add_argument("--input", required=True, help="Input JSONL trace file.")
    ingest_parser.add_argument("--output", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed JSONL output path.")

    mine_parser = subparsers.add_parser("mine", help="Run clustering, association, sequence, and graph mining.")
    mine_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    mine_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="Skill registry JSON path.")
    mine_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="Plugin metadata JSON path.")
    mine_parser.add_argument("--clusters", type=int, default=None, help="Optional fixed K for K-Means.")

    recommend_parser = subparsers.add_parser("recommend", help="Recommend top-K skills for a task.")
    recommend_parser.add_argument("--task", required=True, help="Task description.")
    recommend_parser.add_argument("--top-k", type=int, default=5, help="Number of recommendations.")
    recommend_parser.add_argument("--language", default="", help="Optional project language signal.")
    recommend_parser.add_argument("--framework", action="append", default=[], help="Optional framework signal; can be repeated.")
    recommend_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    recommend_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="Skill registry JSON path.")
    recommend_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="Plugin metadata JSON path.")

    generate_parser = subparsers.add_parser("generate", help="Generate a candidate SKILL.md from a mined cluster.")
    generate_parser.add_argument("--cluster-id", required=True, help="Cluster id such as C03.")
    generate_parser.add_argument("--output-dir", default=None, help="Optional target directory.")

    verify_parser = subparsers.add_parser("verify", help="Verify a candidate skill directory or SKILL.md.")
    verify_parser.add_argument("--skill", required=True, help="Candidate skill directory or SKILL.md path.")

    demo_parser = subparsers.add_parser("demo", help="Run the full MVP pipeline on sample data.")
    demo_parser.add_argument("--task", default="给当前项目生成测试修复 skill", help="Task used for recommendation.")
    demo_parser.add_argument("--cluster-id", default="", help="Cluster to generate; defaults to highest coverage gap.")

    return parser


def run_demo(task: str, cluster_id: str = "") -> dict[str, Any]:
    ensure_project_dirs()
    ingest_result = ingest_traces(DATA_DIR / "sample_traces.jsonl", DATA_DIR / "processed_traces.jsonl")
    mine_result = mine(DATA_DIR / "processed_traces.jsonl")
    selected_cluster = cluster_id
    if not selected_cluster:
        clusters = mine_result.get("clusters", [])
        selected_cluster = str(clusters[0]["id"]) if clusters else "C01"
    rec_result = recommend(task=task, top_k=5)
    gen_result = generate_skill(selected_cluster)
    verify_result = verify_skill(gen_result["skill_dir"])
    return {
        "ingest": ingest_result,
        "mine": {
            "trace_count": mine_result["trace_count"],
            "cluster_count": len(mine_result.get("clusters", [])),
            "rule_count": len(mine_result.get("association_rules", [])),
            "sequence_count": len(mine_result.get("frequent_sequences", [])),
            "graph": mine_result.get("graph", {}),
        },
        "recommend": rec_result,
        "generate": gen_result,
        "verify": verify_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            result = ingest_traces(args.input, args.output)
        elif args.command == "mine":
            result = mine(args.traces, args.registry, args.plugins, args.clusters)
        elif args.command == "recommend":
            result = recommend(
                task=args.task,
                traces_path=args.traces,
                registry_path=args.registry,
                plugin_path=args.plugins,
                top_k=args.top_k,
                project_language=args.language,
                frameworks=args.framework,
            )
        elif args.command == "generate":
            result = generate_skill(args.cluster_id, args.output_dir)
        elif args.command == "verify":
            result = verify_skill(Path(args.skill))
        elif args.command == "demo":
            result = run_demo(args.task, args.cluster_id)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
