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
    description = f"Reusable workflow for tasks like: {representative[:120]}"
    risk = min(1.0, 0.25 + failure_rate * 0.35 + coverage_gap * 0.25)
    lines = [
        _frontmatter(name, description.replace('"', "'"), terms + tools + errors, risk, cluster_id),
        f"# {name}",
        "",
        "## When To Use",
        "",
        f"Use this skill for tasks similar to `{representative}`.",
        "",
        "Trigger signals:",
    ]
    for value in terms[:6]:
        lines.append(f"- Task term: `{value}`")
    for value in extensions[:5]:
        lines.append(f"- File type: `.{value}`")
    for value in errors[:5]:
        lines.append(f"- Error pattern: `{value}`")
    lines.extend(
        [
            "",
            "## Workflow",
            "",
            "1. Inspect the project context and confirm the relevant files before editing.",
            "2. Reuse the stable tool sequence observed in successful traces.",
        ]
    )
    for tool in tools[:6]:
        lines.append(f"   - Prefer `{tool}` when it matches the current environment.")
    lines.extend(
        [
            "3. If the task involves a past failure pattern, reproduce the smallest failing case first.",
            "4. Apply the change in a narrow scope, then run the closest available verification command.",
            "5. Record the outcome so this skill can be updated with success and failure evidence.",
            "",
            "## Safety Checks",
            "",
            "- Do not install external dependencies without user confirmation.",
            "- Do not write outside the active workspace.",
            "- Treat generated scripts as drafts until deterministic checks pass.",
            "- If verification fails twice, stop and summarize the failure instead of broadening scope.",
            "",
            "## Evidence",
            "",
            f"- Source cluster: `{cluster_id}`",
            f"- Cluster size: `{cluster.get('size', 0)}`",
            f"- Failure rate: `{float(cluster.get('failure_rate', 0.0) or 0.0):.2f}`",
            f"- Coverage gap: `{float(cluster.get('coverage_gap', 0.0) or 0.0):.2f}`",
            f"- Trace IDs: `{', '.join(str(item) for item in cluster.get('trace_ids', []))}`",
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
