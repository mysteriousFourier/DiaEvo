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


def _list_items(values: list[str], empty: str = "No strong signal yet.") -> list[str]:
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
        lines.append(f"- `{kind}` score `{score:.2f}`: {reason}")
    return lines or ["- `baseline_pattern`: Similar tasks appear in traces, but more evidence is needed before promotion."]


def _workflow_steps(tools: list[str], errors: list[str], failure_types: list[str]) -> list[str]:
    steps = [
        "Read the task, inspect the project files that match the cluster signals, and identify the smallest reproducible workflow.",
        "Prefer the successful tool sequence mined from traces before introducing a new tool.",
    ]
    if tools:
        steps.append("Run or emulate the recurring tool path: " + " -> ".join(f"`{tool}`" for tool in tools[:6]) + ".")
    if errors or failure_types:
        joined = ", ".join(f"`{value}`" for value in [*errors, *failure_types][:6])
        steps.append(f"If the task shows {joined}, reproduce the failing case before editing.")
    steps.extend(
        [
            "Make the narrowest workspace-scoped change that addresses the observed failure or coverage gap.",
            "Run the closest validation command from the trace evidence and record pass/fail feedback for later mining.",
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
    description = f"Trace-driven workflow for tasks similar to: {representative[:120]}"
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
        f"Use this candidate when a task is similar to: `{representative}`.",
        "",
        "This skill is a draft generated from mining evidence. Do not install or promote it until verification passes and a human approves the candidate.",
        "",
        "## Trigger Signals",
        "",
        "Task terms:",
        *_list_items(terms[:6]),
        "",
        "Files or extensions:",
        *_list_items([f".{value}" for value in extensions[:5]], "No recurring file extension signal."),
        "",
        "Tools:",
        *_list_items(tools[:6], "No recurring tool signal."),
        "",
        "Failures:",
        *_list_items([*errors, *failure_types][:6], "No recurring failure signal."),
        "",
        "## Mined Evidence",
        "",
        *_explanation_text(cluster),
        "",
        f"- Source cluster: `{cluster_id}`",
        f"- Trace ids: `{', '.join(str(item) for item in cluster.get('trace_ids', []))}`",
        f"- Cluster size: `{cluster.get('size', 0)}`",
        f"- Source counts: `{source_counts}`",
        f"- Failure rate: `{failure_rate:.2f}`",
        f"- Coverage gap: `{coverage_gap:.2f}`",
        f"- Event count: `{int(cluster.get('event_count', 0) or 0)}`",
        f"- Tool success rate: `{float(cluster.get('tool_success_rate', 0.0) or 0.0):.2f}`",
        f"- Tool reuse count: `{int(cluster.get('tool_reuse_count', 0) or 0)}`",
        "",
        "## Operating Steps",
        "",
        *_numbered(_workflow_steps(tools, errors, failure_types)),
        "",
        "## Failure Fallbacks",
        "",
        "- If validation fails, capture the exact command, failing output category, and files touched before retrying.",
        "- If a tool requires approval, stop at preview and ask for explicit human approval before running it.",
        "- If a command would write outside the current workspace, refuse the action and propose a workspace-local alternative.",
        "- If the same validation fails twice, stop broadening the patch and summarize the smallest unresolved failure.",
        "- If new dependencies are needed, treat installation as a separate approval-gated step.",
        "",
        "## Verification Suggestions",
        "",
        "- Run `skillminer verify --skill <candidate-dir>` before considering promotion.",
        "- Prefer the nearest validation command mined from the traces.",
        "- Confirm the candidate has frontmatter, bounded scope, recovery guidance, and no credential or dangerous-command patterns.",
        "- After use, feed tool events back through `skillminer ingest` so the recommendation and mining reports learn from the result.",
        "",
        "## Safety Constraints",
        "",
        "- Keep all edits inside the active workspace unless the user explicitly approves a broader path.",
        "- Never pipe downloaded content into a shell or PowerShell interpreter.",
        "- Never include real API keys, tokens, passwords, or private credentials in this skill.",
        "- Generated candidates are not installed automatically; promotion requires human review.",
        "",
    ]
    return "\n".join(lines)


def generate_skill(cluster_id: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    ensure_project_dirs()
    report = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    cluster = _find_cluster(cluster_id, report)
    markdown = build_skill_markdown(cluster)
    first_name = slugify(
        f"{cluster_id}-{('-'.join(cluster.get('top_terms', [])[:3]) if cluster.get('top_terms') else 'candidate')}"
    )
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
