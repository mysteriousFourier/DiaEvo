from __future__ import annotations

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
            f"source_cluster: \"{cluster_id}\"",
            f"risk_score: {risk:.2f}",
            "status: candidate",
            "---",
            "",
        ]
    )


def build_skill_markdown(cluster: dict[str, Any]) -> str:
    cluster_id = str(cluster.get("id", "C00"))
    terms = [str(term) for term in cluster.get("top_terms", [])]
    tools = [str(tool) for tool in cluster.get("top_tools", [])]
    errors = [str(error) for error in cluster.get("top_errors", [])]
    extensions = [str(ext) for ext in cluster.get("file_extensions", [])]
    representative = str(cluster.get("representative_task", ""))
    failure_rate = float(cluster.get("failure_rate", 0.0) or 0.0)
    coverage_gap = float(cluster.get("coverage_gap", 0.0) or 0.0)
    name_terms = "-".join(terms[:3]) if terms else cluster_id.lower()
    name = slugify(f"{cluster_id}-{name_terms}")
    description = f"适用于类似任务的可复用工作流：{representative[:120]}"
    risk = min(1.0, 0.25 + failure_rate * 0.35 + coverage_gap * 0.25)
    lines = [
        _frontmatter(name, description.replace('"', "'"), terms + tools + errors, risk, cluster_id),
        f"# {name}",
        "",
        "## 何时使用",
        "",
        f"当任务与 `{representative}` 相似时使用这个技能。",
        "",
        "触发信号：",
    ]
    for value in terms[:6]:
        lines.append(f"- 任务关键词：`{value}`")
    for value in extensions[:5]:
        lines.append(f"- 文件类型：`.{value}`")
    for value in errors[:5]:
        lines.append(f"- 错误模式：`{value}`")
    lines.extend(
        [
            "",
            "## 工作流",
            "",
            "1. 先检查项目上下文，并在编辑前确认相关文件。",
            "2. 复用成功轨迹中稳定出现的工具顺序。",
        ]
    )
    for tool in tools[:6]:
        lines.append(f"   - 如果当前环境适配，优先使用 `{tool}`。")
    lines.extend(
        [
            "3. 如果任务涉及历史失败模式，先复现最小失败用例。",
            "4. 在尽量窄的范围内修改，然后运行最接近的验证命令。",
            "5. 记录结果，便于后续用成功和失败证据更新这个技能。",
            "",
            "## 安全检查",
            "",
            "- 未经用户确认，不要安装外部依赖。",
            "- 不要写入当前工作区之外的路径。",
            "- 生成的脚本在确定性检查通过前都应视为草稿。",
            "- 如果验证连续失败两次，停止扩大修改范围，并总结失败原因。",
            "",
            "## 证据",
            "",
            f"- 来源聚类：`{cluster_id}`",
            f"- 聚类规模：`{cluster.get('size', 0)}`",
            f"- 失败率：`{float(cluster.get('failure_rate', 0.0) or 0.0):.2f}`",
            f"- 覆盖缺口：`{float(cluster.get('coverage_gap', 0.0) or 0.0):.2f}`",
            f"- 轨迹 ID：`{', '.join(str(item) for item in cluster.get('trace_ids', []))}`",
            "",
        ]
    )
    return "\n".join(lines)


def generate_skill(cluster_id: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    ensure_project_dirs()
    report = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    cluster = _find_cluster(cluster_id, report)
    markdown = build_skill_markdown(cluster)
    first_name = slugify(f"{cluster_id}-{('-'.join(cluster.get('top_terms', [])[:3]) if cluster.get('top_terms') else 'candidate')}")
    target_root = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / cluster_id.upper()
    target_root.mkdir(parents=True, exist_ok=True)
    skill_path = target_root / "SKILL.md"
    skill_path.write_text(markdown, encoding="utf-8")
    metadata = {
        "cluster_id": cluster_id.upper(),
        "skill_dir": str(target_root),
        "skill_path": str(skill_path),
        "name_hint": first_name,
        "status": "candidate",
    }
    write_json(target_root / "metadata.json", metadata)
    return metadata
