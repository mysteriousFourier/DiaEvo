from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .models import PluginRecord, SkillRecord, TraceRecord
from .paths import DATA_DIR, REPORTS_DIR, ensure_project_dirs
from .storage import read_json, read_jsonl, write_json, write_jsonl


def load_traces(path: str | Path) -> list[TraceRecord]:
    records = read_jsonl(path)
    traces: list[TraceRecord] = []
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        trace = TraceRecord.from_mapping(record, index=index)
        if trace.id in seen:
            raise ValueError(f"Duplicate trace id: {trace.id}")
        seen.add(trace.id)
        traces.append(trace)
    return traces


def load_skill_registry(path: str | Path | None = None) -> list[SkillRecord]:
    target = Path(path) if path else DATA_DIR / "skill_registry.json"
    values = read_json(target, default=[])
    if not isinstance(values, list):
        raise ValueError(f"Skill registry must be a JSON list: {target}")
    skills = [SkillRecord.from_mapping(item) for item in values]
    return [skill for skill in skills if skill.name and skill.description]


def load_plugins(path: str | Path | None = None) -> list[PluginRecord]:
    target = Path(path) if path else DATA_DIR / "plugin_metadata.json"
    values = read_json(target, default=[])
    if not isinstance(values, list):
        raise ValueError(f"Plugin metadata must be a JSON list: {target}")
    plugins = [PluginRecord.from_mapping(item) for item in values]
    return [plugin for plugin in plugins if plugin.name and plugin.description]


def summarize_traces(traces: list[TraceRecord]) -> dict[str, Any]:
    outcomes = Counter(trace.outcome for trace in traces)
    languages = Counter(trace.project_language or "unknown" for trace in traces)
    frameworks = Counter(framework for trace in traces for framework in trace.frameworks)
    tools = Counter(tool for trace in traces for tool in trace.tools)
    errors = Counter(trace.error_type for trace in traces if trace.error_type)
    skills = Counter(skill for trace in traces for skill in trace.used_skills)
    success_count = sum(1 for trace in traces if trace.success)
    return {
        "trace_count": len(traces),
        "success_count": success_count,
        "failure_count": len(traces) - success_count,
        "success_rate": round(success_count / len(traces), 4) if traces else 0.0,
        "outcomes": dict(outcomes.most_common()),
        "languages": dict(languages.most_common()),
        "frameworks": dict(frameworks.most_common()),
        "top_tools": dict(tools.most_common(12)),
        "error_types": dict(errors.most_common()),
        "used_skills": dict(skills.most_common()),
    }


def ingest_traces(input_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    ensure_project_dirs()
    traces = load_traces(input_path)
    target = Path(output_path) if output_path else DATA_DIR / "processed_traces.jsonl"
    write_jsonl(target, [trace.to_mapping() for trace in traces])
    summary = summarize_traces(traces)
    summary["input_path"] = str(Path(input_path))
    summary["processed_path"] = str(target)
    write_json(REPORTS_DIR / "ingest_summary.json", summary)
    return summary
