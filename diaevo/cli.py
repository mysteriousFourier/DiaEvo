from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .evaluation import baseline_report
from .code_evolution import run_code_evolution
from .evolution import evolve_skill
from .gepa_adapter import evaluate_gepa, evaluate_gepa_phase4
from .generator import generate_skill
from .ingest import ingest_traces
from .knowledge_graph import (
    KG_REVIEW_STATUSES,
    answer_kg,
    apply_kg_delta,
    build_kg_delta,
    export_kg_snapshot,
    kg_workbench,
    review_kg_delta,
    visualize_kg,
)
from .miner import mine
from .mining_snapshot import export_mining_snapshot
from .paths import DATA_DIR, bootstrap_workspace
from .promotion import PROMOTION_LABELS, label_promotion, promote, queue_promotion, rewrite_promotion
from .recommender import recommend
from .tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args, tool_schemas
from .validation_runner import run_validation
from .verifier import verify_skill
from .deepseek_chat import run_chat_test


PUBLIC_COMMANDS = (
    "ingest",
    "mine",
    "export-mining-snapshot",
    "recommend",
    "generate",
    "verify",
    "evolve",
    "validate",
    "queue-promotion",
    "promote",
    "label-promotion",
    "rewrite-promotion",
    "demo",
    "home",
    "tools",
    "feedback",
    "kg",
    "answer-kg",
    "evaluate",
    "evaluate-gepa",
    "evaluate-gepa-phase4",
    "evaluate-code-evolution",
    "tool",
    "chat-test",
)


class DiaEvoArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "invalid choice" in message and "choose from" in message:
            prefix = message.split("(choose from", 1)[0].rstrip()
            message = f"{prefix}（可用命令：{', '.join(PUBLIC_COMMANDS)}）"
        super().error(message)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = DiaEvoArgumentParser(
        prog="diaevo",
        description="从任务轨迹中挖掘、推荐、生成和验证 Agent 技能。",
    )
    subparsers = parser.add_subparsers(dest="command", required=False, metavar="command")

    def add_hidden_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        hidden = subparsers.add_parser(name, **kwargs)
        subparsers._choices_actions = [  # type: ignore[attr-defined]
            action for action in subparsers._choices_actions if action.dest != name  # type: ignore[attr-defined]
        ]
        return hidden

    ingest_parser = subparsers.add_parser("ingest", help="Validate and normalize JSONL trace data.")
    ingest_parser.add_argument("--input", required=True, help="Input JSONL trace file.")
    ingest_parser.add_argument("--output", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed JSONL output path.")
    ingest_parser.add_argument("--tool-events", default=None, help="Optional tool event JSONL path.")
    ingest_parser.add_argument("--no-tool-events", action="store_true", help="Do not merge .diaevo/tool_events.jsonl.")

    mine_parser = subparsers.add_parser("mine", help="Run clustering, association, sequence, and graph mining.")
    mine_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    mine_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="Skill registry JSON path.")
    mine_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="Plugin metadata JSON path.")
    mine_parser.add_argument("--clusters", type=int, default=None, help="Optional fixed K for K-Means.")

    snapshot_parser = subparsers.add_parser("export-mining-snapshot", help="Export human-readable mining findings to data/mining_snapshots/YYMMDD.")
    snapshot_parser.add_argument("--traces", default=None, help="Processed trace JSONL path; if omitted, input is ingested first.")
    snapshot_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="Base JSONL trace file used when --traces is omitted.")
    snapshot_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path used when --traces is omitted.")
    snapshot_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="Skill registry JSON path.")
    snapshot_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="Plugin metadata JSON path.")
    snapshot_parser.add_argument("--clusters", type=int, default=None, help="Optional fixed K for K-Means.")
    snapshot_parser.add_argument("--date", default=None, help="Snapshot date as YYMMDD or ISO date; defaults to today.")
    snapshot_parser.add_argument("--output-dir", default=None, help="Optional explicit snapshot output directory.")
    snapshot_parser.add_argument("--include-tool-events", action="store_true", help="Merge .diaevo/tool_events.jsonl when ingesting input.")

    recommend_parser = subparsers.add_parser("recommend", help="Recommend top-K skills for a task.")
    recommend_parser.add_argument("--task", required=True, help="Task description.")
    recommend_parser.add_argument("--top-k", type=int, default=5, help="Number of recommendations.")
    recommend_parser.add_argument("--language", default="", help="Optional project language signal.")
    recommend_parser.add_argument("--framework", action="append", default=[], help="Optional framework signal; can be repeated.")
    recommend_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    recommend_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="Skill registry JSON path.")
    recommend_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="Plugin metadata JSON path.")
    recommend_parser.add_argument("--weights", default=None, help="Optional recommender weight JSON path.")
    recommend_parser.add_argument("--rerank", choices=["weighted", "pareto"], default="weighted", help="Optional reranking strategy.")

    generate_parser = subparsers.add_parser("generate", help="Generate a candidate SKILL.md from a mined cluster.")
    generate_parser.add_argument("--cluster-id", required=True, help="Cluster id such as C03.")
    generate_parser.add_argument("--output-dir", default=None, help="Optional target directory.")
    generate_parser.add_argument("--with-code", action="store_true", help="Generate restricted helper code and validation.json for a code-backed skill.")

    verify_parser = subparsers.add_parser("verify", help="Verify a candidate skill directory or SKILL.md.")
    verify_parser.add_argument("--skill", required=True, help="Candidate skill directory or SKILL.md path.")

    evolve_parser = subparsers.add_parser("evolve", help="Evolve generated skill candidates with local metric/Pareto optimization.")
    evolve_target = evolve_parser.add_mutually_exclusive_group()
    evolve_target.add_argument("--cluster-id", default=None, help="Cluster id such as C03.")
    evolve_target.add_argument("--all-entrypoints", action="store_true", help="Evolve all mining report generation entrypoints.")
    evolve_parser.add_argument("--budget", type=int, default=50, help="Maximum local candidate variants per cluster.")
    evolve_parser.add_argument("--output-dir", default=None, help="Optional target directory for a single evolved candidate.")

    validate_parser = subparsers.add_parser("validate", help="Run approved validation.json commands for a candidate skill.")
    validate_parser.add_argument("--skill", required=True, help="Candidate skill directory or SKILL.md path.")
    validate_parser.add_argument("--approve", action="store_true", help="Execute validation commands after preview/safety checks.")

    queue_parser = subparsers.add_parser("queue-promotion", help="Queue a verified candidate for human promotion review.")
    queue_parser.add_argument("--skill", required=True, help="Candidate skill directory or SKILL.md path.")

    promote_parser = subparsers.add_parser("promote", help="Promote an approved queue item into the local registry.")
    promote_parser.add_argument("--queue-id", required=True, help="Promotion queue entry id.")
    promote_parser.add_argument("--approve", action="store_true", help="Update the registry after human approval.")
    promote_parser.add_argument("--registry", default=None, help="Optional registry JSON path.")

    label_parser = subparsers.add_parser("label-promotion", help="Attach human review labels to a promotion queue item.")
    label_parser.add_argument("--queue-id", required=True, help="Promotion queue entry id.")
    label_parser.add_argument(
        "--label",
        action="append",
        choices=sorted(PROMOTION_LABELS),
        required=True,
        help="Review label; can be repeated.",
    )
    label_parser.add_argument("--note", default="", help="Optional reviewer note.")
    label_parser.add_argument("--reviewer", default="", help="Optional reviewer id.")

    rewrite_parser = subparsers.add_parser(
        "rewrite-promotion",
        help="根据 promotion 人工标签生成 merge/specialize/reject_duplicate 重写草案。",
    )
    rewrite_parser.add_argument("--queue-id", required=True, help="Promotion queue entry id.")
    rewrite_parser.add_argument(
        "--action",
        choices=["auto", "merge", "specialize", "reject_duplicate"],
        default="auto",
        help="重写动作；auto 会根据标签和 duplicate 建议选择。",
    )
    rewrite_parser.add_argument("--output-dir", default=None, help="可选：重写草案输出目录。")

    demo_parser = subparsers.add_parser("demo", help="Run the full MVP pipeline on sample data.")
    demo_parser.add_argument("--task", default="给当前项目生成测试修复 skill", help="Task used for recommendation.")
    demo_parser.add_argument("--cluster-id", default="", help="Cluster to generate; defaults to highest coverage gap.")

    subparsers.add_parser("home", help="Open the dashboard and interactive shell.")

    subparsers.add_parser("tools", help="List local tool schemas and approval requirements.")

    feedback_parser = subparsers.add_parser("feedback", help="Fold tool event logs into processed traces.")
    feedback_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="Base JSONL trace file.")
    feedback_parser.add_argument("--output", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed JSONL output path.")
    feedback_parser.add_argument("--tool-events", default=None, help="Optional tool event JSONL path.")

    kg_delta_parser = add_hidden_parser(
        "build-kg-delta",
        description="底层自动化命令：生成待审核的增量知识图谱候选。日常请使用 kg。",
    )
    kg_delta_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    kg_delta_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="Skill registry JSON path.")
    kg_delta_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="Plugin metadata JSON path.")
    kg_delta_parser.add_argument("--tool-events", default=None, help="Optional tool event JSONL path.")
    kg_delta_parser.add_argument("--conversation", default=None, help="Optional conversation JSONL path.")
    kg_delta_parser.add_argument("--clusters", type=int, default=None, help="Optional fixed K for K-Means.")
    kg_delta_parser.add_argument("--no-mining", action="store_true", help="Skip mining-report-derived KG candidates.")
    kg_delta_parser.add_argument("--queue", default=None, help="Optional review queue JSONL path.")
    kg_delta_parser.add_argument("--current-dir", default=None, help="Optional active KG directory.")
    kg_delta_parser.add_argument("--delta-dir", default=None, help="Optional delta report directory.")

    kg_review_parser = add_hidden_parser(
        "review-kg-delta",
        description="底层自动化命令：查看或标注知识图谱审核队列。日常请使用 kg。",
    )
    kg_review_parser.add_argument("--review-id", default=None, help="Review id to label; omitted lists pending items.")
    kg_review_parser.add_argument("--status", choices=sorted(KG_REVIEW_STATUSES), default="accepted", help="Review status to apply.")
    kg_review_parser.add_argument("--note", default="", help="Optional reviewer note.")
    kg_review_parser.add_argument("--reviewer", default="", help="Optional reviewer id.")
    kg_review_parser.add_argument("--queue", default=None, help="Optional review queue JSONL path.")
    kg_review_parser.add_argument("--limit", type=int, default=20, help="Maximum pending items to list.")

    kg_apply_parser = add_hidden_parser(
        "apply-kg-delta",
        description="底层自动化命令：把已接受的 KG 候选写入 active KG。日常请使用 kg。",
    )
    kg_apply_parser.add_argument("--queue", default=None, help="Optional review queue JSONL path.")
    kg_apply_parser.add_argument("--current-dir", default=None, help="Optional active KG directory.")

    kg_parser = subparsers.add_parser("kg", help="打开可编辑知识图谱工作台，或应用工作台导出的编辑 JSON。")
    kg_parser.add_argument("--date", default=None, help="工作台显示日期，格式 YYMMDD 或 ISO 日期；默认今天。")
    kg_parser.add_argument("--output-dir", default=None, help="兼容选项：指定后才生成独立 HTML 导出目录。")
    kg_parser.add_argument("--current-dir", default=None, help="可选：指定 active KG 目录。")
    kg_parser.add_argument("--port", type=int, default=None, help="可选：绑定本地工作台端口；默认 8765，冲突时自动顺延。")
    kg_parser.add_argument("--no-open", action="store_true", help="只输出本地 URL，不自动打开浏览器。")
    kg_parser.add_argument("--apply-edit", default=None, help="应用知识图谱编辑器导出的 JSON 文件。")
    kg_parser.add_argument("--approve", action="store_true", help="确认把导出的 KG 编辑 JSON 写回 active KG。")

    kg_snapshot_parser = add_hidden_parser(
        "export-kg-snapshot",
        description="底层自动化命令：导出已审核知识图谱。日常请使用 kg。",
    )
    kg_snapshot_parser.add_argument("--date", default=None, help="Snapshot date as YYMMDD or ISO date; defaults to today.")
    kg_snapshot_parser.add_argument("--output-dir", default=None, help="Optional explicit snapshot output directory.")
    kg_snapshot_parser.add_argument("--current-dir", default=None, help="Optional active KG directory.")

    kg_visualize_parser = add_hidden_parser(
        "visualize-kg",
        description="底层兼容命令：生成 KG HTML。日常请使用 kg。",
    )
    kg_visualize_parser.add_argument("--date", default=None, help="Snapshot date as YYMMDD or ISO date; defaults to today.")
    kg_visualize_parser.add_argument("--output-dir", default=None, help="Optional explicit visualization output directory.")
    kg_visualize_parser.add_argument("--current-dir", default=None, help="Optional active KG directory.")

    kg_answer_parser = subparsers.add_parser("answer-kg", help="显式使用已审核知识图谱回答；严格模式需手动开启。")
    kg_answer_parser.add_argument("--query", required=True, help="要从 KG 中回答的问题。")
    kg_answer_parser.add_argument("--strict", action="store_true", help="只使用 accepted KG 事实和已审核证据。")
    kg_answer_parser.add_argument("--include-pending", action="store_true", help="同时搜索 pending 候选；严格模式会忽略。")
    kg_answer_parser.add_argument("--current-dir", default=None, help="可选：指定 active KG 目录。")
    kg_answer_parser.add_argument("--queue", default=None, help="可选：非严格模式使用的审核队列 JSONL 路径。")
    kg_answer_parser.add_argument("--max-paths", type=int, default=5, help="最多返回多少条图谱证据路径。")
    kg_answer_parser.add_argument("--vector-backend", choices=["auto", "dense", "tfidf"], default=None, help="KG 向量检索后端；dense 使用 sentence-transformers。")
    kg_answer_parser.add_argument("--embedding-model", default=None, help="dense 后端使用的 sentence-transformers/HF 模型名。")
    kg_answer_parser.add_argument("--hf-endpoint", default=None, help="HF 下载镜像；默认 https://hf-mirror.com。")

    eval_parser = subparsers.add_parser("evaluate", help="Run baseline metrics for the current engineering algorithms.")
    eval_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="Base JSONL trace file.")
    eval_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    eval_parser.add_argument("--tool-events", default=None, help="Optional tool event JSONL path.")
    eval_parser.add_argument("--no-tool-events", action="store_true", help="Do not merge .diaevo/tool_events.jsonl.")
    eval_parser.add_argument("--top-k", type=int, default=5, help="Top-K cutoff for recommendation metrics.")
    eval_parser.add_argument(
        "--duplicate-threshold",
        type=float,
        default=0.92,
        help="Cosine similarity threshold for candidate duplicate pairs.",
    )
    eval_parser.add_argument("--variant", choices=["baseline", "evolved"], default="baseline", help="Evaluation variant.")

    gepa_parser = subparsers.add_parser("evaluate-gepa", help="Run optional GEPA skill-section optimization for one cluster.")
    gepa_parser.add_argument("--cluster-id", required=True, help="Cluster id such as C03.")
    gepa_parser.add_argument("--budget", type=int, default=50, help="Maximum GEPA metric calls.")
    gepa_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="Base JSONL trace file.")
    gepa_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    gepa_parser.add_argument("--tool-events", default=None, help="Optional tool event JSONL path.")
    gepa_parser.add_argument("--no-tool-events", action="store_true", help="Do not merge .diaevo/tool_events.jsonl.")
    gepa_parser.add_argument("--top-k", type=int, default=3, help="Top-K cutoff for selected-cluster held-out metrics.")
    gepa_parser.add_argument("--env", default=None, help="Path to .env; defaults to project .env.")
    gepa_parser.add_argument("--model", default=None, help="Override DEEPSEEK_MODEL.")
    gepa_parser.add_argument("--base-url", default=None, help="Override DEEPSEEK_BASE_URL.")
    gepa_parser.add_argument("--max-tokens", type=int, default=None, help="Override DEEPSEEK_MAX_TOKENS.")
    gepa_parser.add_argument("--temperature", type=float, default=None, help="Override DEEPSEEK_TEMPERATURE.")
    gepa_parser.add_argument("--no-thinking", action="store_true", help="Disable DeepSeek thinking config for this run.")
    gepa_parser.add_argument("--dry-run", action="store_true", help="Exercise seed/local comparison and safety gates without importing or calling GEPA.")
    gepa_parser.add_argument("--output-dir", default=None, help="Optional target directory for the GEPA candidate.")
    gepa_parser.add_argument("--condition", default="single_run", help="Experiment condition label recorded in the report.")
    gepa_parser.add_argument(
        "--memory-policy",
        choices=["current", "none", "ctm", "epm", "ctm_epm"],
        default="current",
        help="Memory context policy for seed/background construction.",
    )
    gepa_parser.add_argument(
        "--racing-policy",
        choices=["off", "cheap_gates"],
        default="off",
        help="Cheap local gate policy for GEPA candidate evaluation.",
    )
    gepa_parser.add_argument(
        "--judge-policy",
        choices=["none", "uncertainty_only"],
        default="none",
        help="Sparse judge policy for uncertain candidates.",
    )

    phase4_parser = subparsers.add_parser("evaluate-gepa-phase4", help="Run Phase 4 GEPA/APO experiment matrix.")
    phase4_parser.add_argument("--cluster-id", required=True, help="Cluster id such as C03.")
    phase4_parser.add_argument("--budgets", default="5,10,25,50", help="Comma-separated GEPA budgets.")
    phase4_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="Base JSONL trace file.")
    phase4_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="Processed trace JSONL path.")
    phase4_parser.add_argument("--tool-events", default=None, help="Optional tool event JSONL path.")
    phase4_parser.add_argument("--no-tool-events", action="store_true", help="Do not merge .diaevo/tool_events.jsonl.")
    phase4_parser.add_argument("--top-k", type=int, default=3, help="Top-K cutoff for selected-cluster held-out metrics.")
    phase4_parser.add_argument("--env", default=None, help="Path to .env; defaults to project .env.")
    phase4_parser.add_argument("--model", default=None, help="Override DEEPSEEK_MODEL.")
    phase4_parser.add_argument("--base-url", default=None, help="Override DEEPSEEK_BASE_URL.")
    phase4_parser.add_argument("--max-tokens", type=int, default=None, help="Override DEEPSEEK_MAX_TOKENS.")
    phase4_parser.add_argument("--temperature", type=float, default=None, help="Override DEEPSEEK_TEMPERATURE.")
    phase4_parser.add_argument("--no-thinking", action="store_true", help="Disable DeepSeek thinking config for this run.")
    phase4_parser.add_argument("--dry-run", action="store_true", help="Run the full matrix without importing or calling GEPA.")
    phase4_parser.add_argument("--output-dir", default=None, help="Optional root directory for GEPA candidates.")
    phase4_parser.add_argument("--no-resume", action="store_true", help="Ignore any existing Phase 4 report and rerun all rows.")

    code_evolution_parser = subparsers.add_parser(
        "evaluate-code-evolution",
        help="在沙盒副本中评估候选代码 patch 或只输出 Phase 7 patch strategy。",
    )
    code_evolution_parser.add_argument("--task", required=True, help="要研究的代码演化任务描述。")
    code_evolution_parser.add_argument("--patch-file", default=None, help="可选：unified diff patch 文件路径。")
    code_evolution_parser.add_argument(
        "--test-command",
        action="append",
        default=[],
        help="验证命令，可重复；默认使用 `python -m pytest -q`。",
    )
    code_evolution_parser.add_argument(
        "--allowed-path",
        action="append",
        default=[],
        help="限制 patch 可修改的工作区路径前缀，可重复。",
    )
    code_evolution_parser.add_argument("--approve", action="store_true", help="确认后在 disposable sandbox 中应用 patch 并运行验证。")
    code_evolution_parser.add_argument("--timeout-sec", type=int, default=60, help="每条验证命令的超时秒数。")
    code_evolution_parser.add_argument("--network", action="store_true", help="允许验证命令使用网络；默认禁止。")
    code_evolution_parser.add_argument("--output-dir", default=None, help="可选：报告输出目录。")
    code_evolution_parser.add_argument(
        "--collect-baseline",
        action="store_true",
        help="未提供 patch 时，在 disposable sandbox 中运行验证命令并收集 baseline 证据。",
    )

    tool_parser = subparsers.add_parser("tool", help="Execute one local tool with JSON arguments.")
    tool_parser.add_argument("name", help="Tool name such as list_files or read_file.")
    tool_parser.add_argument("--args", default="{}", help="JSON object passed to the tool.")
    tool_parser.add_argument("--arg", action="append", default=[], help="Tool argument in key=value form; can be repeated.")
    tool_parser.add_argument("--approve", action="store_true", help="Execute tools that require approval.")

    chat_parser = subparsers.add_parser("chat-test", help="Run a simple DeepSeek chat completion test using .env.")
    chat_parser.add_argument("--prompt", default="用一句话说明 DiaEvo MVP 可以做什么。", help="User prompt.")
    chat_parser.add_argument("--system", default="你是用于测试 Agent 技能挖掘 MVP 的简洁中文助手。", help="System prompt.")
    chat_parser.add_argument("--env", default=None, help="Path to .env; defaults to project .env.")
    chat_parser.add_argument("--model", default=None, help="Override DEEPSEEK_MODEL.")
    chat_parser.add_argument("--base-url", default=None, help="Override DEEPSEEK_BASE_URL.")
    chat_parser.add_argument("--max-tokens", type=int, default=None, help="Override DEEPSEEK_MAX_TOKENS.")
    chat_parser.add_argument("--temperature", type=float, default=None, help="Override DEEPSEEK_TEMPERATURE.")
    chat_parser.add_argument("--no-thinking", action="store_true", help="Disable DeepSeek thinking field for this test.")
    chat_parser.add_argument("--image", action="append", default=[], help="Attach an image path or URL and use GLM vision config.")
    chat_parser.add_argument("--interactive", action="store_true", help="Keep conversation history locally and chat until /exit.")

    return parser


def run_demo(task: str, cluster_id: str = "") -> dict[str, Any]:
    bootstrap_workspace()
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
    bootstrap_workspace()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None or args.command == "home":
            from ui.interactive_shell import main as shell_main

            return shell_main()
        if args.command == "ingest":
            result = ingest_traces(
                args.input,
                args.output,
                tool_events_path=args.tool_events,
                include_tool_events=not args.no_tool_events,
            )
        elif args.command == "mine":
            result = mine(args.traces, args.registry, args.plugins, args.clusters)
        elif args.command == "export-mining-snapshot":
            result = export_mining_snapshot(
                traces_path=args.traces,
                input_path=args.input,
                processed_path=args.processed,
                registry_path=args.registry,
                plugin_path=args.plugins,
                k=args.clusters,
                date=args.date,
                output_dir=args.output_dir,
                include_tool_events=args.include_tool_events,
            )
        elif args.command == "recommend":
            result = recommend(
                task=args.task,
                traces_path=args.traces,
                registry_path=args.registry,
                plugin_path=args.plugins,
                top_k=args.top_k,
                project_language=args.language,
                frameworks=args.framework,
                weights_path=args.weights,
                rerank=args.rerank,
            )
        elif args.command == "generate":
            result = generate_skill(args.cluster_id, args.output_dir, with_code=args.with_code)
        elif args.command == "verify":
            result = verify_skill(Path(args.skill))
        elif args.command == "evolve":
            result = evolve_skill(
                args.cluster_id,
                all_entrypoints=args.all_entrypoints,
                budget=args.budget,
                output_dir=args.output_dir,
            )
        elif args.command == "validate":
            result = run_validation(args.skill, approve=args.approve)
        elif args.command == "queue-promotion":
            result = queue_promotion(args.skill)
        elif args.command == "promote":
            result = promote(args.queue_id, approve=args.approve, registry_path=args.registry)
        elif args.command == "label-promotion":
            result = label_promotion(args.queue_id, labels=args.label, note=args.note, reviewer=args.reviewer)
        elif args.command == "rewrite-promotion":
            result = rewrite_promotion(args.queue_id, action=args.action, output_dir=args.output_dir)
        elif args.command == "demo":
            result = run_demo(args.task, args.cluster_id)
        elif args.command == "tools":
            result = {"tools": tool_schemas()}
        elif args.command == "feedback":
            result = ingest_traces(args.input, args.output, tool_events_path=args.tool_events, include_tool_events=True)
        elif args.command == "build-kg-delta":
            result = build_kg_delta(
                traces_path=args.traces,
                registry_path=args.registry,
                plugin_path=args.plugins,
                tool_events_path=args.tool_events,
                conversation_path=args.conversation,
                k=args.clusters,
                include_mining=not args.no_mining,
                queue_path=args.queue,
                current_dir=args.current_dir,
                delta_dir=args.delta_dir,
            )
        elif args.command == "review-kg-delta":
            result = review_kg_delta(
                args.review_id,
                status=args.status,
                note=args.note,
                reviewer=args.reviewer,
                queue_path=args.queue,
                limit=args.limit,
            )
        elif args.command == "apply-kg-delta":
            result = apply_kg_delta(queue_path=args.queue, current_dir=args.current_dir)
        elif args.command == "kg":
            result = kg_workbench(
                date=args.date,
                output_dir=args.output_dir,
                current_dir=args.current_dir,
                edit_path=args.apply_edit,
                approve=args.approve,
                port=args.port,
                open_browser=not args.no_open,
            )
        elif args.command == "export-kg-snapshot":
            result = export_kg_snapshot(date=args.date, output_dir=args.output_dir, current_dir=args.current_dir)
        elif args.command == "visualize-kg":
            result = visualize_kg(date=args.date, output_dir=args.output_dir, current_dir=args.current_dir)
        elif args.command == "answer-kg":
            result = answer_kg(
                args.query,
                strict=args.strict,
                include_pending=args.include_pending,
                current_dir=args.current_dir,
                queue_path=args.queue,
                max_paths=args.max_paths,
                vector_backend=args.vector_backend,
                embedding_model=args.embedding_model,
                hf_endpoint=args.hf_endpoint,
            )
        elif args.command == "evaluate":
            result = baseline_report(
                input_path=args.input,
                processed_path=args.processed,
                tool_events_path=args.tool_events,
                include_tool_events=not args.no_tool_events,
                top_k=args.top_k,
                duplicate_threshold=args.duplicate_threshold,
                variant=args.variant,
            )
        elif args.command == "evaluate-gepa":
            result = evaluate_gepa(
                args.cluster_id,
                budget=args.budget,
                input_path=args.input,
                processed_path=args.processed,
                tool_events_path=args.tool_events,
                include_tool_events=not args.no_tool_events,
                top_k=args.top_k,
                env_path=args.env,
                model=args.model,
                base_url=args.base_url,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                no_thinking=args.no_thinking,
                dry_run=args.dry_run,
                output_dir=args.output_dir,
                condition=args.condition,
                memory_policy=args.memory_policy,
                racing_policy=args.racing_policy,
                judge_policy=args.judge_policy,
            )
        elif args.command == "evaluate-gepa-phase4":
            result = evaluate_gepa_phase4(
                args.cluster_id,
                budgets=args.budgets,
                input_path=args.input,
                processed_path=args.processed,
                tool_events_path=args.tool_events,
                include_tool_events=not args.no_tool_events,
                top_k=args.top_k,
                env_path=args.env,
                model=args.model,
                base_url=args.base_url,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                no_thinking=args.no_thinking,
                dry_run=args.dry_run,
                output_dir=args.output_dir,
                resume=not args.no_resume,
            )
        elif args.command == "evaluate-code-evolution":
            result = run_code_evolution(
                task=args.task,
                patch_file=args.patch_file,
                test_commands=args.test_command,
                allowed_paths=args.allowed_path,
                approve=args.approve,
                timeout_sec=args.timeout_sec,
                network=args.network,
                output_dir=args.output_dir,
                collect_baseline=args.collect_baseline,
            )
        elif args.command == "tool":
            tool_args = parse_tool_args(args.args)
            tool_args.update(parse_tool_arg_pairs(args.arg))
            result = execute_tool(args.name, tool_args, approve=args.approve)
        elif args.command == "chat-test":
            return run_chat_test(
                prompt=args.prompt,
                system=args.system,
                env_path=args.env,
                model=args.model,
                base_url=args.base_url,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                no_thinking=args.no_thinking,
                interactive=args.interactive,
                image_paths=args.image,
            )
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
