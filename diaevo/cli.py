from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .evaluation import baseline_report
from .code_evolution import run_code_evolution
from .evolution import evolve_skill
from .gepa_adapter import evaluate_gepa, evaluate_gepa_phase4
from .generator import generate_skill, generation_diagnostic, semantic_skill_name
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
from .paths import CANDIDATE_SKILLS_DIR, DATA_DIR, REPORTS_DIR, WORKSPACE_ROOT, bootstrap_workspace
from .promotion import PROMOTION_LABELS, label_promotion, promote, queue_promotion, rewrite_promotion
from .recommender import recommend
from .script_artifacts import SCRIPT_REVIEW_STATUSES, review_script
from .skill_adapter import adapt_external_skill
from .skill_context import discover_skill_sources, load_skill_context
from .storage import read_json, write_json
from .tool_layer import execute_tool, parse_tool_arg_pairs, parse_tool_args, tool_schemas
from .validation_runner import run_validation
from .verifier import verify_skill
from .deepseek_chat import run_chat_test


PUBLIC_COMMANDS = (
    "learn",
    "status",
    "ingest",
    "mine",
    "export-mining-snapshot",
    "recommend",
    "skills",
    "list-skills",
    "generate",
    "verify",
    "evolve",
    "self-evolve",
    "validate",
    "queue-promotion",
    "promote",
    "label-promotion",
    "rewrite-promotion",
    "review-script",
    "adapt-skill",
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
    "qq-bridge",
)

USER_COMMANDS = (
    "learn",
    "status",
    "skills",
    "list-skills",
    "kg",
    "answer-kg",
    "home",
    "tools",
    "tool",
    "chat-test",
    "qq-bridge",
)

DEBUG_COMMANDS = tuple(command for command in PUBLIC_COMMANDS if command not in USER_COMMANDS)


class DiaEvoArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "invalid choice" in message and "choose from" in message:
            prefix = message.split("(choose from", 1)[0].rstrip()
            message = f"{prefix}（可用命令：{', '.join(PUBLIC_COMMANDS)}）"
        elif message.startswith("the following arguments are required:"):
            message = "缺少必需参数：" + message.split(":", 1)[1]
        elif message.startswith("unrecognized arguments:"):
            message = "无法识别的参数：" + message.split(":", 1)[1]
        super().error(message)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _cli_output_mode(args: argparse.Namespace) -> str:
    if getattr(args, "json", False):
        return "json"
    if getattr(args, "plain", False):
        return "plain"
    env_value = os.environ.get("DIAEVO_OUTPUT", "").strip().lower()
    if env_value == "json":
        return "json"
    if env_value in {"plain", "terminal"}:
        return "plain"
    return "plain"


_TOOL_LABELS = {
    "list_files": "列出工作区文件",
    "read_file": "读取工作区文件片段",
    "write_file": "创建或覆盖工作区文件",
    "edit_file": "替换文件中的精确字符串",
    "delete_file": "删除工作区文件或目录",
    "apply_patch": "应用 unified diff 补丁",
    "run_shell": "运行本地 shell 命令",
    "web_fetch": "抓取网页内容",
    "web_search": "执行网页搜索",
    "arxiv_search": "检索 arXiv 论文",
    "kg_answer": "从已审核知识图谱回答",
    "recommend_skills": "按任务推荐技能",
    "load_skill_context": "载入技能上下文",
}


def _format_score(value: object) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _render_tools_result(result: dict[str, Any]) -> str:
    specs = result.get("tools") if isinstance(result.get("tools"), list) else []
    lines = ["本地工具", ""]
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "")
        label = _TOOL_LABELS.get(name, str(spec.get("description") or ""))
        mode = "只读" if spec.get("read_only") else "可写"
        gate = "需审批" if spec.get("approval_required") else "直接执行"
        risk = str(spec.get("risk") or "low")
        lines.append(f"- {name}：{label}（{mode}，{gate}，风险：{risk}）")
    return "\n".join(lines).rstrip()


def _render_recommend_result(result: dict[str, Any]) -> str:
    recommendations = result.get("recommendations") if isinstance(result.get("recommendations"), list) else []
    lines = [
        f"任务：{result.get('task', '')}",
        f"推荐结果：Top {result.get('top_k', len(recommendations))}",
        "",
    ]
    if not recommendations:
        lines.append("没有找到可推荐的技能。")
        return "\n".join(lines)
    for index, item in enumerate(recommendations, start=1):
        if not isinstance(item, dict):
            continue
        skill = item.get("skill", "")
        score = _format_score(item.get("score", ""))
        source = item.get("source", "")
        reason = item.get("reason", "")
        lines.append(f"{index}. {skill}（分数 {score}，来源 {source}）")
        if reason:
            lines.append(f"   原因：{reason}")
        execution_mode = item.get("execution_mode", "")
        fallback = item.get("fallback_reason", "")
        if execution_mode:
            lines.append(f"   执行方式：{execution_mode}")
        if fallback:
            lines.append(f"   说明：{fallback}")
    return "\n".join(lines)


def _render_answer_kg_result(result: dict[str, Any]) -> str:
    lines = [str(result.get("answer") or "").strip()]
    facts = result.get("facts") if isinstance(result.get("facts"), list) else []
    evidence = result.get("evidence_paths") if isinstance(result.get("evidence_paths"), list) else []
    if facts:
        lines.extend(["", "证据事实："])
        for item in facts:
            if not isinstance(item, dict):
                continue
            confidence = item.get("confidence", "")
            lines.append(
                f"- {item.get('subject', '')} {item.get('predicate', '')} "
                f"{item.get('object', '')}（置信度 {confidence}）"
            )
    missing = result.get("missing") if isinstance(result.get("missing"), list) else []
    if missing:
        lines.extend(["", "缺少证据："])
        lines.extend(f"- {item}" for item in missing)
    if evidence:
        lines.extend(["", "证据路径："])
        for item in evidence:
            if not isinstance(item, dict):
                continue
            path = item.get("path", "")
            summary = item.get("summary", "")
            lines.append(f"- {path}  {summary}".rstrip())
    return "\n".join(line for line in lines if line is not None).strip()


def _render_skills_result(result: dict[str, Any]) -> str:
    if result.get("mode") == "detail":
        if result.get("status") != "ok":
            return str(result.get("error") or "skill not found")
        lines = [
            f"Skill：{result.get('name', '')}",
            f"路径：{result.get('skill_file', '')}",
        ]
        description = str(result.get("description") or "").strip()
        if description:
            lines.extend(["", description])
        skill_text = str(result.get("skill_text") or "").strip()
        if skill_text:
            lines.extend(["", skill_text[:4000]])
            if len(skill_text) > 4000:
                lines.append("... <truncated>")
        return "\n".join(lines).rstrip()

    skills = result.get("skills") if isinstance(result.get("skills"), list) else []
    if result.get("names_only"):
        return "\n".join(
            str(item.get("name") or "")
            for item in skills
            if isinstance(item, dict) and item.get("name")
        )
    lines = [f"现有 skills：{result.get('skill_count', len(skills))}"]
    if result.get("query"):
        lines[0] += f"（过滤：{result.get('query')}）"
    if not skills:
        lines.append("没有找到 skill。")
        return "\n".join(lines)
    for index, item in enumerate(skills, start=1):
        if not isinstance(item, dict):
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        tag_text = f"  tags={', '.join(str(tag) for tag in tags)}" if tags else ""
        lines.append(f"{index}. {item.get('name', '')}  [{item.get('source', '')}]{tag_text}")
        description = str(item.get("description") or "").strip()
        if description:
            lines.append(f"   {description}")
        lines.append(f"   {item.get('path', '')}")
    return "\n".join(lines).rstrip()


def _render_tool_result_plain(result: dict[str, Any]) -> str:
    tool = result.get("tool", "tool")
    status = result.get("status", "")
    lines = [f"{tool}：{status}"]
    message = result.get("message") or result.get("error")
    if message:
        lines.append(str(message))
    if status == "requires_approval":
        lines.append("这是审批前预览，尚未修改工作区。确认执行时加 --approve。")
    if "content" in result:
        lines.extend(["", str(result.get("content") or "")])
    elif "entries" in result and isinstance(result.get("entries"), list):
        entries = result["entries"]
        for item in entries[:30]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('path', '')}")
        if len(entries) > 30:
            lines.append(f"... 还有 {len(entries) - 30} 项")
    elif "preview" in result:
        preview = result.get("preview")
        if isinstance(preview, dict):
            diff = preview.get("diff")
            operation = preview.get("operation")
            path = preview.get("path")
            if operation or path:
                lines.append(f"预览：{operation or ''} {path or ''}".strip())
            if diff:
                lines.extend(["", str(diff).rstrip()])
    return "\n".join(lines).rstrip()


def _render_generate_result(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    cluster_id = str(result.get("cluster_id") or "")
    if status == "skipped":
        lines = [
            f"生成候选 skill：跳过 {cluster_id}".rstrip(),
            str(result.get("message") or result.get("reason") or "该簇暂不适合生成 skill。"),
        ]
        diagnostic = result.get("diagnostic") if isinstance(result.get("diagnostic"), dict) else {}
        representative = str(diagnostic.get("representative_task") or "").strip()
        top_tools = diagnostic.get("top_tools") if isinstance(diagnostic.get("top_tools"), list) else []
        if representative:
            lines.append(f"代表任务：{representative}")
        if top_tools:
            lines.append("工具信号：" + ", ".join(str(item) for item in top_tools[:6]))
        if result.get("workspace"):
            lines.append(f"工作区：{result.get('workspace')}")
        return "\n".join(lines).rstrip()
    lines = [
        f"生成候选 skill：{status or 'ok'}",
        f"簇：{cluster_id}",
        f"名称：{result.get('name_hint', '')}",
        f"目录：{result.get('skill_dir', '')}",
        f"文件：{result.get('skill_path', '')}",
    ]
    if result.get("workspace"):
        lines.append(f"工作区：{result.get('workspace')}")
    if result.get("code_backed"):
        lines.append("包含 helper code：是")
    return "\n".join(line for line in lines if line).rstrip()


def _render_learn_result(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    lines = ["学习结果"]
    if status in {"no_candidate", "skipped"}:
        lines.append(str(result.get("message") or "暂时没有适合沉淀成 skill 的任务。"))
        next_step = str(result.get("next_step") or "")
        if next_step:
            lines.extend(["", f"下一步建议：{next_step}"])
        return "\n".join(lines).rstrip()

    selected = result.get("selected_task") if isinstance(result.get("selected_task"), dict) else {}
    generated = result.get("generated") if isinstance(result.get("generated"), dict) else {}
    verified = result.get("verify") if isinstance(result.get("verify"), dict) else {}
    if status == "preview":
        lines.extend(
            [
                "做了什么：找到了一个适合沉淀的任务候选；本次没有写入文件。",
                f"任务名：{selected.get('title', '')}",
                f"它解决什么：{selected.get('solves', '')}",
                f"为什么：{selected.get('reason', '')}",
            ]
        )
        report_path = str(result.get("report_path") or "")
        if report_path:
            lines.append(f"详细报告：{report_path}")
        lines.extend(["", "下一步建议：运行 /learn 生成候选 skill，或继续完成更多真实任务再学习。"])
        return "\n".join(line for line in lines if line).rstrip()
    lines.extend(
        [
            f"做了什么：从最近任务轨迹中沉淀了一个候选 skill。",
            f"任务名：{selected.get('title', '')}",
            f"它解决什么：{selected.get('solves', '')}",
            f"为什么：{selected.get('reason', '')}",
            f"产物在哪：{generated.get('skill_path', '')}",
        ]
    )
    if generated.get("name_hint"):
        lines.append(f"Skill 名称：{generated.get('name_hint')}")
    if verified:
        passed = "通过" if verified.get("passed") else "未通过"
        lines.append(f"验证：{passed}")
    report_path = str(result.get("report_path") or "")
    if report_path:
        lines.append(f"详细报告：{report_path}")
    lines.extend(["", "下一步建议：打开 SKILL.md 快速检查；满意后再走验证和晋升。"])
    return "\n".join(line for line in lines if line).rstrip()


def _render_status_result(result: dict[str, Any]) -> str:
    lines = [
        "当前状态",
        f"工作区：{result.get('workspace', '')}",
        f"模型：{result.get('model', '')}",
        f"最近学习：{result.get('last_learn_status', '无')}",
    ]
    if result.get("last_skill"):
        lines.append(f"最近生成：{result.get('last_skill')}")
    lines.append(f"待处理候选：{result.get('pending_candidates', 0)}")
    if result.get("report_path"):
        lines.append(f"详细报告：{result.get('report_path')}")
    return "\n".join(lines).rstrip()


def render_cli_result(command: str | None, result: dict[str, Any]) -> str:
    if command == "tools":
        return _render_tools_result(result)
    if command == "recommend":
        return _render_recommend_result(result)
    if command in {"skills", "list-skills"}:
        return _render_skills_result(result)
    if command == "answer-kg":
        return _render_answer_kg_result(result)
    if command == "tool":
        return _render_tool_result_plain(result)
    if command == "generate":
        return _render_generate_result(result)
    if command == "learn":
        return _render_learn_result(result)
    if command == "status":
        return _render_status_result(result)
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def print_result(command: str | None, result: dict[str, Any], args: argparse.Namespace) -> None:
    if _cli_output_mode(args) == "json":
        print_json(result)
        return
    print(render_cli_result(command, result))


def build_parser() -> argparse.ArgumentParser:
    parser = DiaEvoArgumentParser(
        prog="diaevo",
        description="从任务轨迹中挖掘、推荐、生成和验证 Agent 技能。",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出。")
    parser._positionals.title = "命令"  # type: ignore[attr-defined]
    parser._optionals.title = "选项"  # type: ignore[attr-defined]
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="以 JSON 输出结果，适合脚本消费。")
    output_group.add_argument("--plain", action="store_true", help="以人类可读文本输出结果。")
    subparsers = parser.add_subparsers(dest="command", required=False, metavar="command")

    def add_hidden_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        hidden = subparsers.add_parser(name, **kwargs)
        subparsers._choices_actions = [  # type: ignore[attr-defined]
            action for action in subparsers._choices_actions if action.dest != name  # type: ignore[attr-defined]
        ]
        return hidden

    learn_parser = subparsers.add_parser("learn", help="从最近任务轨迹中自动沉淀一个候选 skill。")
    learn_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="基础 JSONL 轨迹文件。")
    learn_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后 JSONL 输出路径。")
    learn_parser.add_argument("--tool-events", default=None, help="可选：工具事件 JSONL 路径。")
    learn_parser.add_argument("--no-tool-events", action="store_true", help="不合并 .diaevo/tool_events.jsonl。")
    learn_parser.add_argument("--clusters", type=int, default=None, help="可选：固定聚类数量。")
    learn_parser.add_argument("--output-dir", default=None, help="可选：候选 skill 输出目录。")
    learn_parser.add_argument("--with-code", action="store_true", help="为候选 skill 生成只读 helper code。")
    learn_parser.add_argument("--dry-run", action="store_true", help="只显示会学习哪个任务，不写入候选 skill。")

    subparsers.add_parser("status", help="显示当前工作区、模型和最近学习结果。")

    ingest_parser = subparsers.add_parser("ingest", help="校验并规范化 JSONL 任务轨迹。")
    ingest_parser.add_argument("--input", required=True, help="输入 JSONL 轨迹文件。")
    ingest_parser.add_argument("--output", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后 JSONL 输出路径。")
    ingest_parser.add_argument("--tool-events", default=None, help="可选：工具事件 JSONL 路径。")
    ingest_parser.add_argument("--no-tool-events", action="store_true", help="不合并 .diaevo/tool_events.jsonl。")

    mine_parser = subparsers.add_parser("mine", help="运行聚类、关联规则、序列和图挖掘。")
    mine_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后的轨迹 JSONL 路径。")
    mine_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="技能注册表 JSON 路径。")
    mine_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="插件元数据 JSON 路径。")
    mine_parser.add_argument("--clusters", type=int, default=None, help="可选：固定 K-Means 聚类数。")

    snapshot_parser = subparsers.add_parser("export-mining-snapshot", help="导出人类可读的挖掘快照到 data/mining_snapshots/YYMMDD。")
    snapshot_parser.add_argument("--traces", default=None, help="处理后的轨迹 JSONL 路径；省略时会先导入输入数据。")
    snapshot_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="省略 --traces 时使用的基础 JSONL 轨迹文件。")
    snapshot_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="省略 --traces 时写入的处理后轨迹路径。")
    snapshot_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="技能注册表 JSON 路径。")
    snapshot_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="插件元数据 JSON 路径。")
    snapshot_parser.add_argument("--clusters", type=int, default=None, help="可选：固定 K-Means 聚类数。")
    snapshot_parser.add_argument("--date", default=None, help="快照日期，格式 YYMMDD 或 ISO 日期；默认今天。")
    snapshot_parser.add_argument("--output-dir", default=None, help="可选：显式指定快照输出目录。")
    snapshot_parser.add_argument("--include-tool-events", action="store_true", help="导入输入数据时合并 .diaevo/tool_events.jsonl。")

    recommend_parser = subparsers.add_parser("recommend", help="按任务推荐 Top-K 技能。")
    recommend_parser.add_argument("--task", required=True, help="任务描述。")
    recommend_parser.add_argument("--top-k", type=int, default=5, help="推荐数量。")
    recommend_parser.add_argument("--language", default="", help="可选：项目语言信号。")
    recommend_parser.add_argument("--framework", action="append", default=[], help="可选：项目框架信号，可重复。")
    recommend_parser.add_argument("--traces", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后的轨迹 JSONL 路径。")
    recommend_parser.add_argument("--registry", default=str(DATA_DIR / "skill_registry.json"), help="技能注册表 JSON 路径。")
    recommend_parser.add_argument("--plugins", default=str(DATA_DIR / "plugin_metadata.json"), help="插件元数据 JSON 路径。")
    recommend_parser.add_argument("--weights", default=None, help="可选：推荐器权重 JSON 路径。")
    recommend_parser.add_argument("--rerank", choices=["weighted", "pareto"], default="weighted", help="可选：重排策略。")

    skills_parser = subparsers.add_parser("skills", help="列出现有 skill，或查看某个 skill 的上下文。")
    skills_parser.add_argument("--name", default="", help="可选：查看指定 skill 名称或路径。")
    skills_parser.add_argument("--names", action="store_true", help="只输出 skill name，每行一个。")
    skills_parser.add_argument("--query", default="", help="可选：按名称、描述、标签或路径过滤列表。")
    skills_parser.add_argument("--limit", type=int, default=50, help="最多显示多少个 skill。")

    list_skills_parser = subparsers.add_parser("list-skills", help="兼容别名：列出现有 skill。")
    list_skills_parser.add_argument("--name", default="", help="可选：查看指定 skill 名称或路径。")
    list_skills_parser.add_argument("--names", action="store_true", help="只输出 skill name，每行一个。")
    list_skills_parser.add_argument("--query", default="", help="可选：按名称、描述、标签或路径过滤列表。")
    list_skills_parser.add_argument("--limit", type=int, default=50, help="最多显示多少个 skill。")

    generate_parser = subparsers.add_parser("generate", help="从挖掘簇生成候选 SKILL.md。")
    generate_parser.add_argument("--cluster-id", required=True, help="簇 ID，例如 C03。")
    generate_parser.add_argument("--output-dir", default=None, help="可选：目标输出目录。")
    generate_parser.add_argument("--with-code", action="store_true", help="为 code-backed skill 生成受限 helper code 和 validation.json。")

    verify_parser = subparsers.add_parser("verify", help="验证候选技能目录或 SKILL.md。")
    verify_parser.add_argument("--skill", required=True, help="候选技能目录或 SKILL.md 路径。")

    evolve_parser = subparsers.add_parser("evolve", help="用本地指标和 Pareto 选择演化候选技能。")
    evolve_target = evolve_parser.add_mutually_exclusive_group()
    evolve_target.add_argument("--cluster-id", default=None, help="簇 ID，例如 C03。")
    evolve_target.add_argument("--all-entrypoints", action="store_true", help="演化 mining report 中的全部生成入口。")
    evolve_parser.add_argument("--budget", type=int, default=50, help="每个簇最多评估的本地候选变体数。")
    evolve_parser.add_argument("--output-dir", default=None, help="可选：单个演化候选的目标目录。")

    self_evolve_parser = subparsers.add_parser(
        "self-evolve",
        help="一键运行本地自进化：演化候选技能，并默认生成验证预览。",
    )
    self_evolve_parser.add_argument("cluster_id", nargs="?", default="", help="可选簇 ID，例如 C03；省略时选择最高优先级簇。")
    self_evolve_parser.add_argument("--all-entrypoints", action="store_true", help="自进化 mining report 中的全部生成入口。")
    self_evolve_parser.add_argument("--budget", type=int, default=20, help="每个簇最多评估的本地候选变体数。")
    self_evolve_parser.add_argument("--output-dir", default=None, help="可选：单个演化候选的目标目录。")
    self_evolve_parser.add_argument("--no-validate", action="store_true", help="只演化，不生成 validation.json 预览。")
    self_evolve_parser.add_argument("--approve-validation", action="store_true", help="确认后在沙盒中执行 validation.json 命令。")
    self_evolve_parser.add_argument("--evaluate", action="store_true", help="演化后运行 evolved 评估报告。")
    self_evolve_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="评估使用的基础 JSONL 轨迹文件。")
    self_evolve_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="评估使用的处理后轨迹路径。")
    self_evolve_parser.add_argument("--tool-events", default=None, help="可选：评估时使用的工具事件 JSONL 路径。")
    self_evolve_parser.add_argument("--no-tool-events", action="store_true", help="评估时不合并 .diaevo/tool_events.jsonl。")
    self_evolve_parser.add_argument("--top-k", type=int, default=5, help="评估推荐指标使用的 Top-K 截断值。")

    validate_parser = subparsers.add_parser("validate", help="运行已审批的候选技能 validation.json 命令。")
    validate_parser.add_argument("--skill", required=True, help="候选技能目录或 SKILL.md 路径。")
    validate_parser.add_argument("--approve", action="store_true", help="预览和安全检查后执行验证命令。")

    queue_parser = subparsers.add_parser("queue-promotion", help="将已验证候选加入人工晋升审核队列。")
    queue_parser.add_argument("--skill", required=True, help="候选技能目录或 SKILL.md 路径。")

    promote_parser = subparsers.add_parser("promote", help="把已审批队列项晋升到本地注册表。")
    promote_parser.add_argument("--queue-id", required=True, help="晋升队列项 ID。")
    promote_parser.add_argument("--approve", action="store_true", help="人工确认后更新注册表。")
    promote_parser.add_argument("--registry", default=None, help="可选：注册表 JSON 路径。")

    label_parser = subparsers.add_parser("label-promotion", help="给晋升队列项添加人工审核标签。")
    label_parser.add_argument("--queue-id", required=True, help="晋升队列项 ID。")
    label_parser.add_argument(
        "--label",
        action="append",
        choices=sorted(PROMOTION_LABELS),
        required=True,
        help="审核标签；可重复。",
    )
    label_parser.add_argument("--note", default="", help="可选：审核备注。")
    label_parser.add_argument("--reviewer", default="", help="可选：审核人 ID。")

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

    review_script_parser = subparsers.add_parser("review-script", help="审核 skill 目录中的只读 helper 脚本状态。")
    review_script_parser.add_argument("--skill", required=True, help="候选技能目录或 SKILL.md 路径。")
    review_script_parser.add_argument("--status", choices=sorted(SCRIPT_REVIEW_STATUSES), required=True, help="脚本审核状态。")
    review_script_parser.add_argument("--note", default="", help="可选：审核备注。")
    review_script_parser.add_argument("--reviewer", default="", help="可选：审核人 ID。")
    review_script_parser.add_argument("--approve", action="store_true", help="确认写入 code_artifacts.json 审核状态。")

    adapt_parser = subparsers.add_parser("adapt-skill", help="将外部 skill 或 demo 项目适配为 DiaEvo 候选技能。")
    adapt_parser.add_argument("--source", default=None, help="本地源目录或受支持的 GitHub URL。")
    adapt_parser.add_argument("--fixture", choices=["garden-web-design-website"], default=None, help="要适配的已知外部 fixture。")
    adapt_parser.add_argument("--source-commit", default=None, help="受支持 GitHub fixture 的固定来源 commit。")
    adapt_parser.add_argument("--source-subdir", default=None, help="来源子目录。")
    adapt_parser.add_argument("--output-dir", default=None, help="候选技能输出目录。")
    adapt_parser.add_argument("--refresh-cache", action="store_true", help="适配前刷新外部 fixture 缓存。")
    adapt_parser.add_argument("--offline", action="store_true", help="只使用已有本地来源/缓存，不访问网络。")
    adapt_parser.add_argument("--with-gepa", action="store_true", help="记录 GEPA 增强请求；确定性适配仍会在无 GEPA 时运行。")
    adapt_parser.add_argument("--dry-run", action="store_true", help="预览适配结果，不写入候选技能。")
    adapt_parser.add_argument(
        "--mode",
        choices=["auto", "skill-package", "project-summary"],
        default="auto",
        help="适配模式；auto 会保留源 SKILL.md 包并总结 demo 项目。",
    )

    demo_parser = subparsers.add_parser("demo", help="用样例数据运行完整 MVP 流程。")
    demo_parser.add_argument("--task", default="给当前项目生成测试修复 skill", help="推荐阶段使用的任务。")
    demo_parser.add_argument("--cluster-id", default="", help="要生成的簇；默认选择最高覆盖缺口。")

    subparsers.add_parser("home", help="打开仪表盘和交互式终端。")

    subparsers.add_parser("tools", help="列出本地工具说明和审批要求。")

    feedback_parser = subparsers.add_parser("feedback", help="将工具事件日志回灌到处理后的轨迹。")
    feedback_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="基础 JSONL 轨迹文件。")
    feedback_parser.add_argument("--output", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后 JSONL 输出路径。")
    feedback_parser.add_argument("--tool-events", default=None, help="可选：工具事件 JSONL 路径。")

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

    eval_parser = subparsers.add_parser("evaluate", help="运行当前工程算法的基线指标。")
    eval_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="基础 JSONL 轨迹文件。")
    eval_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后的轨迹 JSONL 路径。")
    eval_parser.add_argument("--tool-events", default=None, help="可选：工具事件 JSONL 路径。")
    eval_parser.add_argument("--no-tool-events", action="store_true", help="不合并 .diaevo/tool_events.jsonl。")
    eval_parser.add_argument("--top-k", type=int, default=5, help="推荐指标使用的 Top-K 截断值。")
    eval_parser.add_argument(
        "--duplicate-threshold",
        type=float,
        default=0.92,
        help="候选重复对的余弦相似度阈值。",
    )
    eval_parser.add_argument("--variant", choices=["baseline", "evolved"], default="baseline", help="评估变体。")

    gepa_parser = subparsers.add_parser("evaluate-gepa", help="对单个簇运行可选 GEPA 技能章节优化。")
    gepa_parser.add_argument("--cluster-id", required=True, help="簇 ID，例如 C03。")
    gepa_parser.add_argument("--budget", type=int, default=50, help="最大 GEPA 指标调用次数。")
    gepa_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="基础 JSONL 轨迹文件。")
    gepa_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后的轨迹 JSONL 路径。")
    gepa_parser.add_argument("--tool-events", default=None, help="可选：工具事件 JSONL 路径。")
    gepa_parser.add_argument("--no-tool-events", action="store_true", help="不合并 .diaevo/tool_events.jsonl。")
    gepa_parser.add_argument("--top-k", type=int, default=3, help="所选簇 held-out 指标的 Top-K 截断值。")
    gepa_parser.add_argument("--env", default=None, help=".env 路径；默认使用项目 .env。")
    gepa_parser.add_argument("--model", default=None, help="覆盖 DEEPSEEK_MODEL。")
    gepa_parser.add_argument("--base-url", default=None, help="覆盖 DEEPSEEK_BASE_URL。")
    gepa_parser.add_argument("--max-tokens", type=int, default=None, help="覆盖 DEEPSEEK_MAX_TOKENS。")
    gepa_parser.add_argument("--temperature", type=float, default=None, help="覆盖 DEEPSEEK_TEMPERATURE。")
    gepa_parser.add_argument("--no-thinking", action="store_true", help="本次运行禁用 DeepSeek thinking 配置。")
    gepa_parser.add_argument("--dry-run", action="store_true", help="不导入或调用 GEPA，只演练 seed/local 对比和安全门。")
    gepa_parser.add_argument("--output-dir", default=None, help="可选：GEPA 候选输出目录。")
    gepa_parser.add_argument("--condition", default="single_run", help="写入报告的实验条件标签。")
    gepa_parser.add_argument(
        "--memory-policy",
        choices=["current", "none", "ctm", "epm", "ctm_epm"],
        default="current",
        help="seed/background 构造使用的记忆上下文策略。",
    )
    gepa_parser.add_argument(
        "--racing-policy",
        choices=["off", "cheap_gates"],
        default="off",
        help="GEPA 候选评估使用的低成本本地门控策略。",
    )
    gepa_parser.add_argument(
        "--judge-policy",
        choices=["none", "uncertainty_only"],
        default="none",
        help="不确定候选使用的稀疏 judge 策略。",
    )

    phase4_parser = subparsers.add_parser("evaluate-gepa-phase4", help="运行 Phase 4 GEPA/APO 实验矩阵。")
    phase4_parser.add_argument("--cluster-id", required=True, help="簇 ID，例如 C03。")
    phase4_parser.add_argument("--budgets", default="5,10,25,50", help="逗号分隔的 GEPA budget 列表。")
    phase4_parser.add_argument("--input", default=str(DATA_DIR / "sample_traces.jsonl"), help="基础 JSONL 轨迹文件。")
    phase4_parser.add_argument("--processed", default=str(DATA_DIR / "processed_traces.jsonl"), help="处理后的轨迹 JSONL 路径。")
    phase4_parser.add_argument("--tool-events", default=None, help="可选：工具事件 JSONL 路径。")
    phase4_parser.add_argument("--no-tool-events", action="store_true", help="不合并 .diaevo/tool_events.jsonl。")
    phase4_parser.add_argument("--top-k", type=int, default=3, help="所选簇 held-out 指标的 Top-K 截断值。")
    phase4_parser.add_argument("--env", default=None, help=".env 路径；默认使用项目 .env。")
    phase4_parser.add_argument("--model", default=None, help="覆盖 DEEPSEEK_MODEL。")
    phase4_parser.add_argument("--base-url", default=None, help="覆盖 DEEPSEEK_BASE_URL。")
    phase4_parser.add_argument("--max-tokens", type=int, default=None, help="覆盖 DEEPSEEK_MAX_TOKENS。")
    phase4_parser.add_argument("--temperature", type=float, default=None, help="覆盖 DEEPSEEK_TEMPERATURE。")
    phase4_parser.add_argument("--no-thinking", action="store_true", help="本次运行禁用 DeepSeek thinking 配置。")
    phase4_parser.add_argument("--dry-run", action="store_true", help="不导入或调用 GEPA，运行完整实验矩阵。")
    phase4_parser.add_argument("--output-dir", default=None, help="可选：GEPA 候选根目录。")
    phase4_parser.add_argument("--no-resume", action="store_true", help="忽略已有 Phase 4 报告并重跑全部行。")

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

    tool_parser = subparsers.add_parser("tool", help="用 JSON 或 key=value 参数执行一个本地工具。")
    tool_parser.add_argument("name", help="工具名，例如 list_files 或 read_file。")
    tool_parser.add_argument("--args", default="{}", help="传给工具的 JSON 对象。")
    tool_parser.add_argument("--arg", action="append", default=[], help="key=value 形式的工具参数，可重复。")
    tool_parser.add_argument("--approve", action="store_true", help="执行需要审批的工具。")

    chat_parser = subparsers.add_parser("chat-test", help="使用 .env 运行一次简单的 DeepSeek 聊天测试。")
    chat_parser.add_argument("--prompt", default="用一句话说明 DiaEvo MVP 可以做什么。", help="User prompt.")
    chat_parser.add_argument("--system", default="你是用于测试 Agent 技能挖掘 MVP 的简洁中文助手。", help="System prompt.")
    chat_parser.add_argument("--env", default=None, help=".env 路径；默认使用项目 .env。")
    chat_parser.add_argument("--model", default=None, help="覆盖 DEEPSEEK_MODEL。")
    chat_parser.add_argument("--base-url", default=None, help="覆盖 DEEPSEEK_BASE_URL。")
    chat_parser.add_argument("--max-tokens", type=int, default=None, help="覆盖 DEEPSEEK_MAX_TOKENS。")
    chat_parser.add_argument("--temperature", type=float, default=None, help="覆盖 DEEPSEEK_TEMPERATURE。")
    chat_parser.add_argument("--no-thinking", action="store_true", help="本次测试禁用 DeepSeek thinking 字段。")
    chat_parser.add_argument("--image", action="append", default=[], help="附加图片路径或 URL，并使用 GLM 视觉配置。")
    chat_parser.add_argument("--interactive", action="store_true", help="本地保留对话历史，持续聊天直到 /exit。")

    qq_parser = subparsers.add_parser("qq-bridge", help="通过 OneBot 11 QQ 私聊白名单远程控制 DiaEvo。")
    qq_parser.add_argument("--env", default=None, help=".env 路径；默认使用当前 workspace 或安装目录 .env。")

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
    verify_result = (
        verify_skill(gen_result["skill_dir"])
        if gen_result.get("status") == "candidate" and gen_result.get("skill_dir")
        else {"status": "skipped", "reason": gen_result.get("reason"), "message": gen_result.get("message")}
    )
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


def run_self_evolve(
    cluster_id: str = "",
    *,
    all_entrypoints: bool = False,
    budget: int = 20,
    output_dir: str | None = None,
    validate: bool = True,
    approve_validation: bool = False,
    evaluate: bool = False,
    input_path: str = str(DATA_DIR / "sample_traces.jsonl"),
    processed_path: str = str(DATA_DIR / "processed_traces.jsonl"),
    tool_events_path: str | None = None,
    include_tool_events: bool = True,
    top_k: int = 5,
) -> dict[str, Any]:
    evolution_result = evolve_skill(
        cluster_id or None,
        all_entrypoints=all_entrypoints,
        budget=budget,
        output_dir=output_dir,
    )
    validation_results: list[dict[str, Any]] = []
    if validate:
        for run in evolution_result.get("runs", []):
            if not isinstance(run, dict):
                continue
            output = run.get("output")
            if not isinstance(output, dict) or not output.get("skill_dir"):
                continue
            validation_results.append(
                run_validation(str(output["skill_dir"]), approve=approve_validation)
            )
    evaluation_result: dict[str, Any] = {}
    if evaluate:
        evaluation_result = baseline_report(
            input_path=input_path,
            processed_path=processed_path,
            tool_events_path=tool_events_path,
            include_tool_events=include_tool_events,
            top_k=top_k,
            variant="evolved",
        )

    result = {
        "status": "ok",
        "mode": "self_evolve",
        "cluster_id": cluster_id or "",
        "all_entrypoints": all_entrypoints,
        "budget": budget,
        "validation_mode": "executed" if approve_validation else "preview",
        "evolution": evolution_result,
        "validation": validation_results,
        "evaluation": evaluation_result,
    }
    report_path = REPORTS_DIR / "self_evolution_report.json"
    write_json(report_path, result)
    result["report_path"] = str(report_path)
    return result


def _candidate_score(cluster: dict[str, Any]) -> float:
    return (
        float(cluster.get("coverage_gap") or 0.0) * 3.0
        + float(cluster.get("failure_rate") or 0.0) * 1.5
        + min(float(cluster.get("tool_reuse_count") or 0.0), 10.0) / 10.0
        + min(float(cluster.get("size") or 0.0), 10.0) / 20.0
    )


def _task_card(cluster: dict[str, Any]) -> dict[str, Any]:
    diagnostic = generation_diagnostic(cluster)
    representative = str(cluster.get("representative_task") or "未命名任务").strip()
    terms = diagnostic.get("meaningful_terms") if isinstance(diagnostic.get("meaningful_terms"), list) else []
    tools = cluster.get("top_tools") if isinstance(cluster.get("top_tools"), list) else []
    reason_parts = []
    if float(cluster.get("coverage_gap") or 0.0) >= 0.25:
        reason_parts.append("已有能力覆盖不足")
    if int(cluster.get("tool_reuse_count") or 0) > 0:
        reason_parts.append("相似工具流程反复出现")
    if float(cluster.get("failure_rate") or 0.0) > 0:
        reason_parts.append("历史任务里出现过失败，需要固定排查步骤")
    reason = "，".join(reason_parts) or "任务信号足够清晰，适合沉淀成可复用流程"
    solves = representative
    if terms:
        solves = f"{representative}（关键词：{', '.join(str(term) for term in terms[:4])}）"
    return {
        "title": representative[:80],
        "solves": solves[:180],
        "reason": reason,
        "will_update": "候选 skill",
        "cluster_id": str(cluster.get("id") or ""),
        "score": round(_candidate_score(cluster), 4),
        "tools": [str(item) for item in tools[:5]],
    }


def run_learn(
    *,
    input_path: str = str(DATA_DIR / "sample_traces.jsonl"),
    processed_path: str = str(DATA_DIR / "processed_traces.jsonl"),
    tool_events_path: str | None = None,
    include_tool_events: bool = True,
    clusters: int | None = None,
    output_dir: str | None = None,
    with_code: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    ingest_result = ingest_traces(
        input_path,
        processed_path,
        tool_events_path=tool_events_path,
        include_tool_events=include_tool_events,
    )
    mine_result = mine(processed_path, k=clusters)
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cluster in mine_result.get("clusters", []):
        if not isinstance(cluster, dict):
            continue
        diagnostic = generation_diagnostic(cluster)
        card = _task_card(cluster)
        if diagnostic.get("eligible"):
            eligible.append({**card, "cluster": cluster})
        else:
            skipped.append({**card, "reason_code": diagnostic.get("reason")})
    eligible.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("cluster_id") or "")))
    visible_candidates = [{key: value for key, value in item.items() if key != "cluster"} for item in eligible[:3]]
    if not eligible:
        result = {
            "status": "no_candidate",
            "message": "暂时没有适合沉淀成 skill 的任务。已跳过纯工具事件或缺少任务上下文的候选。",
            "next_step": "先用自然语言完成几个真实任务，或提供包含任务描述、文件和验证命令的轨迹。",
            "workspace": str(WORKSPACE_ROOT),
            "ingest": ingest_result,
            "mine": {
                "trace_count": mine_result.get("trace_count", 0),
                "cluster_count": len(mine_result.get("clusters", []) or []),
            },
            "candidates": [],
            "skipped_count": len(skipped),
        }
        report_path = REPORTS_DIR / "learn_report.json"
        write_json(report_path, result)
        result["report_path"] = str(report_path)
        return result

    selected = eligible[0]
    selected_card = {key: value for key, value in selected.items() if key != "cluster"}
    if dry_run:
        result = {
            "status": "preview",
            "message": "已找到可学习的任务候选，但未写入候选 skill。",
            "workspace": str(WORKSPACE_ROOT),
            "selected_task": selected_card,
            "candidates": visible_candidates,
        }
        report_path = REPORTS_DIR / "learn_report.json"
        write_json(report_path, result)
        result["report_path"] = str(report_path)
        return result

    learned_output_dir = output_dir
    if learned_output_dir is None:
        learned_output_dir = str(CANDIDATE_SKILLS_DIR / semantic_skill_name(selected["cluster"]))
    generated = generate_skill(str(selected["cluster_id"]), output_dir=learned_output_dir, with_code=with_code)
    verify_result = (
        verify_skill(generated["skill_dir"])
        if generated.get("status") == "candidate" and generated.get("skill_dir")
        else {"passed": False, "status": "skipped", "message": generated.get("message")}
    )
    result = {
        "status": "ok" if generated.get("status") == "candidate" else str(generated.get("status") or "skipped"),
        "workspace": str(WORKSPACE_ROOT),
        "selected_task": selected_card,
        "candidates": visible_candidates,
        "generated": generated,
        "verify": verify_result,
        "ingest": {
            "trace_count": ingest_result.get("trace_count", 0),
            "tool_events_ingested": ingest_result.get("tool_events_ingested", 0),
        },
        "mine": {
            "trace_count": mine_result.get("trace_count", 0),
            "cluster_count": len(mine_result.get("clusters", []) or []),
        },
    }
    report_path = REPORTS_DIR / "learn_report.json"
    write_json(report_path, result)
    result["report_path"] = str(report_path)
    return result


def workspace_status() -> dict[str, Any]:
    learn_report = read_json(REPORTS_DIR / "learn_report.json", default={}) or {}
    generated = learn_report.get("generated") if isinstance(learn_report.get("generated"), dict) else {}
    pending_candidates = 0
    candidate_root = WORKSPACE_ROOT / "outputs" / "candidate_skills"
    if candidate_root.exists():
        pending_candidates = sum(1 for item in candidate_root.iterdir() if item.is_dir())
    return {
        "status": "ok",
        "workspace": str(WORKSPACE_ROOT),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro",
        "last_learn_status": learn_report.get("status") or "无",
        "last_skill": generated.get("skill_path") or "",
        "pending_candidates": pending_candidates,
        "report_path": str(REPORTS_DIR / "learn_report.json") if learn_report else "",
    }


def list_skills(
    *,
    name: str = "",
    names_only: bool = False,
    query: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if name.strip():
        context = load_skill_context(name.strip())
        return {"mode": "detail", **context}

    normalized_query = query.strip().lower()
    sources = discover_skill_sources()
    if normalized_query:
        filtered = []
        for source in sources:
            haystack = " ".join(
                [
                    source.name,
                    source.description,
                    source.source,
                    str(source.path),
                    " ".join(source.tags),
                ]
            ).lower()
            if normalized_query in haystack:
                filtered.append(source)
        sources = filtered
    limit = max(1, limit)
    visible = sources[:limit]
    return {
        "status": "ok",
        "mode": "list",
        "names_only": names_only,
        "query": query,
        "skill_count": len(sources),
        "shown": len(visible),
        "skills": [
            {
                "name": source.name,
                "description": source.description,
                "path": str(source.path),
                "source": source.source,
                "tags": list(source.tags),
            }
            for source in visible
        ],
    }


def main(argv: list[str] | None = None) -> int:
    bootstrap_workspace()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None or args.command == "home":
            from ui.interactive_shell import main as shell_main

            return shell_main()
        if args.command == "learn":
            result = run_learn(
                input_path=args.input,
                processed_path=args.processed,
                tool_events_path=args.tool_events,
                include_tool_events=not args.no_tool_events,
                clusters=args.clusters,
                output_dir=args.output_dir,
                with_code=args.with_code,
                dry_run=args.dry_run,
            )
        elif args.command == "status":
            result = workspace_status()
        elif args.command == "ingest":
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
        elif args.command in {"skills", "list-skills"}:
            result = list_skills(name=args.name, names_only=args.names, query=args.query, limit=args.limit)
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
        elif args.command == "self-evolve":
            if args.cluster_id and args.all_entrypoints:
                parser.error("self-evolve 不能同时指定 cluster_id 和 --all-entrypoints")
            result = run_self_evolve(
                args.cluster_id,
                all_entrypoints=args.all_entrypoints,
                budget=args.budget,
                output_dir=args.output_dir,
                validate=not args.no_validate,
                approve_validation=args.approve_validation,
                evaluate=args.evaluate,
                input_path=args.input,
                processed_path=args.processed,
                tool_events_path=args.tool_events,
                include_tool_events=not args.no_tool_events,
                top_k=args.top_k,
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
        elif args.command == "review-script":
            result = review_script(
                args.skill,
                status=args.status,
                note=args.note,
                reviewer=args.reviewer,
                approve=args.approve,
            )
        elif args.command == "adapt-skill":
            result = adapt_external_skill(
                source=args.source,
                output_dir=args.output_dir,
                source_commit=args.source_commit,
                source_subdir=args.source_subdir,
                fixture=args.fixture,
                refresh_cache=args.refresh_cache,
                offline=args.offline,
                with_gepa=args.with_gepa,
                dry_run=args.dry_run,
                mode=args.mode,
            )
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
        elif args.command == "qq-bridge":
            from .qq_bridge import run_bridge_from_env

            return run_bridge_from_env(args.env)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_result(args.command, result, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
