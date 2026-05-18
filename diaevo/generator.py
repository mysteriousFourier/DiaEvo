from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paths import CANDIDATE_SKILLS_DIR, REPORTS_DIR, ensure_project_dirs
from .storage import read_json, write_json


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "candidate-skill"


def _find_cluster(cluster_id: str, report: dict[str, Any]) -> dict[str, Any]:
    wanted = cluster_id.upper()
    for cluster in report.get("clusters", []):
        if str(cluster.get("id", "")).upper() == wanted:
            return cluster
    raise ValueError(f"Cluster not found in mining report: {cluster_id}")


def _frontmatter(name: str, description: str, tags: list[str], risk: float, cluster_id: str) -> str:
    tag_text = "[" + ", ".join(f'"{tag}"' for tag in tags[:8]) + "]"
    return "\n".join(
        [
            "---",
            f'name: "{name}"',
            f'description: "{description}"',
            f"tags: {tag_text}",
            f'source_cluster: "{cluster_id}"',
            f"risk_score: {risk:.2f}",
            "status: candidate",
            "---",
            "",
        ]
    )


def _as_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item)]


def _list_items(values: list[str], empty: str = "暂无强信号。") -> list[str]:
    if not values:
        return [f"- {empty}"]
    return [f"- `{value}`" for value in values]


def _numbered(values: list[str], start: int = 1) -> list[str]:
    return [f"{index}. {value}" for index, value in enumerate(values, start=start)]


def _explanation_text(cluster: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    explanations = cluster.get("explanations", [])
    if not isinstance(explanations, list):
        explanations = []
    for item in explanations:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "unknown")
        reason = str(item.get("reason") or "")
        score = float(item.get("score") or 0.0)
        lines.append(f"- `{kind}` 分数 `{score:.2f}`：{reason}")
    return lines or ["- `baseline_pattern`：轨迹中出现了相似任务，但在 promotion 前仍需要更多证据。"]


def _workflow_steps(tools: list[str], errors: list[str], failure_types: list[str]) -> list[str]:
    steps = [
        "先阅读任务，检查与聚类信号匹配的项目文件，确认最小可复现工作流。",
        "优先复用从成功轨迹中挖掘出的工具序列，不要一开始就引入新工具。",
    ]
    if tools:
        steps.append("运行或模拟反复出现的工具路径：" + " -> ".join(f"`{tool}`" for tool in tools[:6]) + "。")
    if errors or failure_types:
        joined = ", ".join(f"`{value}`" for value in [*errors, *failure_types][:6])
        steps.append(f"如果任务出现 {joined}，先复现失败场景，再开始编辑。")
    steps.extend(
        [
            "只做能解决当前失败或覆盖缺口的最小 workspace 范围修改。",
            "运行轨迹证据中最接近的验证命令，并记录 pass/fail 反馈，供后续挖掘使用。",
        ]
    )
    return steps


def build_skill_markdown(cluster: dict[str, Any]) -> str:
    cluster_id = str(cluster.get("id", "C00"))
    terms = _as_strings(cluster.get("top_terms"))
    tools = _as_strings(cluster.get("top_tools"))
    errors = _as_strings(cluster.get("top_errors"))
    failure_types = _as_strings(cluster.get("top_failure_types"))
    extensions = _as_strings(cluster.get("file_extensions"))
    representative = str(cluster.get("representative_task", ""))
    failure_rate = float(cluster.get("failure_rate", 0.0) or 0.0)
    coverage_gap = float(cluster.get("coverage_gap", 0.0) or 0.0)
    name_terms = "-".join(terms[:3]) if terms else cluster_id.lower()
    name = slugify(f"{cluster_id}-{name_terms}")
    description = f"面向相似任务的轨迹驱动工作流：{representative[:120]}"
    risk = min(1.0, 0.20 + failure_rate * 0.30 + coverage_gap * 0.25)
    source_counts = cluster.get("source_counts", {})
    if not isinstance(source_counts, dict):
        source_counts = {}
    lines = [
        _frontmatter(name, description.replace('"', "'"), terms + tools + errors + failure_types, risk, cluster_id),
        f"# {name}",
        "",
        "## When To Use",
        "",
        f"当任务与以下代表任务相似时使用本候选技能：`{representative}`。",
        "",
        "本技能是由挖掘证据生成的草稿。只有通过 verification 且人工审核通过后，才能 promotion。",
        "",
        "## Trigger Signals",
        "",
        "任务关键词：",
        *_list_items(terms[:6]),
        "",
        "文件或扩展名：",
        *_list_items([f".{value}" for value in extensions[:5]], "暂无反复出现的文件扩展名信号。"),
        "",
        "工具：",
        *_list_items(tools[:6], "暂无反复出现的工具信号。"),
        "",
        "失败类型：",
        *_list_items([*errors, *failure_types][:6], "暂无反复出现的失败信号。"),
        "",
        "## Mined Evidence",
        "",
        *_explanation_text(cluster),
        "",
        f"- 来源簇：`{cluster_id}`",
        f"- 轨迹 ID：`{', '.join(str(item) for item in cluster.get('trace_ids', []))}`",
        f"- 簇大小：`{cluster.get('size', 0)}`",
        f"- 来源计数：`{source_counts}`",
        f"- 失败率：`{failure_rate:.2f}`",
        f"- 覆盖缺口：`{coverage_gap:.2f}`",
        f"- 事件数量：`{int(cluster.get('event_count', 0) or 0)}`",
        f"- 工具成功率：`{float(cluster.get('tool_success_rate', 0.0) or 0.0):.2f}`",
        f"- 工具复用次数：`{int(cluster.get('tool_reuse_count', 0) or 0)}`",
        "",
        "## Operating Steps",
        "",
        *_numbered(_workflow_steps(tools, errors, failure_types)),
        "",
        "## Failure Fallbacks",
        "",
        "- 如果验证失败，先记录准确命令、失败输出类别和涉及文件，再决定是否重试。",
        "- 如果工具需要审批，停在 preview，等待用户明确批准后再执行。",
        "- 如果命令会写出当前 workspace，拒绝执行，并提出 workspace-local 替代方案。",
        "- 如果同一个验证连续失败两次，停止扩大改动范围，汇总最小未解决失败。",
        "- 如果需要新增依赖，把安装作为单独的审批步骤处理。",
        "",
        "## Verification Suggestions",
        "",
        "- 在考虑 promotion 前运行 `DiaEvo verify --skill <candidate-dir>`。",
        "- 优先使用从轨迹中挖掘出的最接近验证命令。",
        "- 确认候选技能包含 frontmatter、边界清晰的适用范围、恢复建议，且没有 credential 或 dangerous-command pattern。",
        "- 使用后通过 `DiaEvo ingest` 或 `feedback` 回灌工具事件，让推荐和挖掘报告学习结果。",
        "",
        "## Safety Constraints",
        "",
        "- 除非用户明确批准更大范围，否则所有编辑必须限制在当前 workspace 内。",
        "- 不要把下载内容直接 pipe 到 shell 或 PowerShell 解释器。",
        "- 不要在技能中包含真实 API key、token、password 或私有凭据。",
        "- 生成的候选技能不会自动安装；promotion 必须经过人工审核。",
        "",
    ]
    return "\n".join(lines)


def _code_backed_section() -> str:
    return "\n".join(
        [
            "## Executable Artifacts",
            "",
            "本候选技能包含受限 helper code，用于把固定流程固化为可验证的本地步骤。",
            "",
            "- `scripts/skill_flow.py`：只读流程助手；默认只输出聚类信号和建议步骤，不修改 workspace。",
            "- `code_artifacts.json`：记录 helper 的允许能力、入口和安全边界。",
            "- `validation.json`：在 disposable sandbox 中运行 helper 的 `--describe` smoke。",
            "",
            "helper code 仍是候选制品，必须通过 verifier、validation 和人工 promotion 后才能进入真实使用。",
            "",
        ]
    )


def _helper_script(cluster: dict[str, Any]) -> str:
    payload = {
        "cluster_id": str(cluster.get("id", "C00")),
        "representative_task": str(cluster.get("representative_task", "")),
        "top_terms": _as_strings(cluster.get("top_terms"))[:8],
        "top_tools": _as_strings(cluster.get("top_tools"))[:8],
        "file_extensions": _as_strings(cluster.get("file_extensions"))[:8],
        "top_errors": _as_strings(cluster.get("top_errors"))[:8],
        "top_failure_types": _as_strings(cluster.get("top_failure_types"))[:8],
        "workflow_steps": _workflow_steps(
            _as_strings(cluster.get("top_tools")),
            _as_strings(cluster.get("top_errors")),
            _as_strings(cluster.get("top_failure_types")),
        ),
    }
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f'''from __future__ import annotations

import argparse
import json


FLOW = {payload_json}


def describe() -> dict[str, object]:
    return {{
        "status": "ok",
        "mode": "read_only_skill_flow",
        "flow": FLOW,
        "safety_boundary": "describe-only; no workspace writes; no shell execution; no network",
    }}


def main() -> int:
    parser = argparse.ArgumentParser(description="Describe a DiaEvo generated skill flow.")
    parser.add_argument("--describe", action="store_true", help="Print the read-only flow description.")
    args = parser.parse_args()
    if not args.describe:
        parser.error("Only --describe is supported for generated helper code.")
    print(json.dumps(describe(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _write_code_artifacts(target_root: Path, cluster: dict[str, Any]) -> dict[str, Any]:
    scripts_dir = target_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    helper_path = scripts_dir / "skill_flow.py"
    helper_path.write_text(_helper_script(cluster), encoding="utf-8")
    skill_relative_helper = f"{target_root.as_posix().strip('/')}/scripts/skill_flow.py"
    validation = {
        "schema": "diaevo.validation.v1",
        "status": "candidate",
        "workspace_only": True,
        "network": False,
        "timeout_sec": 60,
        "commands": [f"python {skill_relative_helper} --describe"],
    }
    write_json(target_root / "validation.json", validation)
    artifacts = {
        "schema": "diaevo.code_backed_skill.v1",
        "status": "candidate",
        "review_status": "pending",
        "entrypoint": "scripts/skill_flow.py",
        "mode": "read_only_skill_flow",
        "fallback_mode": "skill_md",
        "allowed_capabilities": ["describe_flow"],
        "forbidden_capabilities": ["workspace_write", "shell_execution", "network", "dependency_install"],
        "validation_commands": validation["commands"],
        "last_validation_status": "",
        "last_sandbox_report_path": "",
        "source_cluster": str(cluster.get("id", "")),
    }
    write_json(target_root / "code_artifacts.json", artifacts)
    return {
        "code_artifacts_path": str(target_root / "code_artifacts.json"),
        "helper_path": str(helper_path),
        "validation_path": str(target_root / "validation.json"),
        "validation_commands": validation["commands"],
    }


def generate_skill(cluster_id: str, output_dir: str | Path | None = None, *, with_code: bool = False) -> dict[str, Any]:
    ensure_project_dirs()
    report = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    cluster = _find_cluster(cluster_id, report)
    markdown = build_skill_markdown(cluster)
    if with_code:
        markdown = markdown.rstrip() + "\n\n" + _code_backed_section()
    first_name = slugify(
        f"{cluster_id}-{('-'.join(cluster.get('top_terms', [])[:3]) if cluster.get('top_terms') else 'candidate')}"
    )
    target_root = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / cluster_id.upper()
    target_root.mkdir(parents=True, exist_ok=True)
    skill_path = target_root / "SKILL.md"
    skill_path.write_text(markdown, encoding="utf-8")
    code_metadata = _write_code_artifacts(target_root, cluster) if with_code else {}
    metadata = {
        "cluster_id": cluster_id.upper(),
        "skill_dir": str(target_root),
        "skill_path": str(skill_path),
        "name_hint": first_name,
        "status": "candidate",
        "code_backed": bool(with_code),
        **code_metadata,
    }
    write_json(target_root / "metadata.json", metadata)
    return metadata
