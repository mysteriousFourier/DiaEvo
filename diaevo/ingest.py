from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import PluginRecord, SkillRecord, TraceRecord
from .paths import DATA_DIR, DIAEVO_DIR, REPORTS_DIR, ensure_project_dirs
from .storage import read_json, read_jsonl, write_json, write_jsonl


DEFAULT_TOOL_EVENTS_PATH = DIAEVO_DIR / "tool_events.jsonl"


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


def _event_task(event: dict[str, Any], index: int) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    tool = str(event.get("tool") or "unknown_tool")
    if result.get("command"):
        return f"Tool event {tool}: {result['command']}"
    if args.get("path"):
        return f"Tool event {tool}: {args['path']}"
    if result.get("path"):
        return f"Tool event {tool}: {result['path']}"
    if args.get("query"):
        return f"Tool event {tool}: {args['query']}"
    if args.get("url"):
        return f"Tool event {tool}: {args['url']}"
    return f"Tool event {tool} #{index}"


def _event_files(event: dict[str, Any]) -> list[str]:
    values: list[str] = []
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    for source in (args, result):
        path = source.get("path")
        if path:
            values.append(str(path))
        paths = source.get("paths")
        if isinstance(paths, list):
            values.extend(str(item) for item in paths if item)
    return sorted(set(values))


def _event_commands(event: dict[str, Any]) -> list[str]:
    values: list[str] = []
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    for source in (args, result):
        command = source.get("command")
        if command:
            values.append(str(command))
    return values


def _event_failure_type(event: dict[str, Any]) -> str:
    status = str(event.get("status") or "").lower()
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    if status == "requires_approval":
        return "approval-required"
    if status == "error":
        text = str(result.get("error") or event.get("error") or "").lower()
        if "outside workspace" in text:
            return "workspace-boundary"
        if "network" in text or "http" in text:
            return "network"
        if "timeout" in text:
            return "timeout"
        return "tool-error"
    if bool(event.get("destructive")):
        return "destructive-tool"
    if bool(event.get("approval_required")) and not bool(event.get("approved")):
        return "approval-required"
    return ""


def tool_events_to_traces(events: list[dict[str, Any]]) -> list[TraceRecord]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        turn_id = str(event.get("turn_id") or event.get("id") or f"event-{len(grouped) + 1}")
        grouped[turn_id].append(event)

    traces: list[TraceRecord] = []
    tool_counts = Counter(str(event.get("tool") or "unknown_tool") for event in events)
    for index, (turn_id, turn_events) in enumerate(grouped.items(), start=1):
        ordered = sorted(turn_events, key=lambda item: str(item.get("started_at") or ""))
        event_key = "|".join(str(event.get("id") or "") for event in ordered) or turn_id
        trace_id = f"EVT-{hashlib.sha1(event_key.encode('utf-8')).hexdigest()[:10]}"
        tools = [str(event.get("tool") or "unknown_tool") for event in ordered]
        statuses = [str(event.get("status") or "unknown").lower() for event in ordered]
        failure_types = sorted({value for event in ordered if (value := _event_failure_type(event))})
        success_count = sum(1 for status in statuses if status == "ok")
        event_count = len(ordered)
        outcome = "success" if event_count and success_count == event_count else "failure"
        tags = ["tool-event", "feedback"]
        if any(bool(event.get("approval_required")) for event in ordered):
            tags.append("approval-gated")
        if any(bool(event.get("destructive")) for event in ordered):
            tags.append("destructive")
        if any(str(event.get("risk") or "") in {"medium", "high", "network"} for event in ordered):
            tags.append("risk")
        traces.append(
            TraceRecord(
                id=trace_id,
                task=_event_task(ordered[0], index),
                files=sorted({file_name for event in ordered for file_name in _event_files(event)}),
                tools=tools,
                commands=[command for event in ordered for command in _event_commands(event)],
                outcome=outcome,
                error_type=failure_types[0] if failure_types else "",
                retries=max(0, event_count - len(set(tools))),
                tags=sorted(set(tags)),
                source="tool_event",
                event_count=event_count,
                tool_success_rate=success_count / event_count if event_count else 0.0,
                tool_failure_types=failure_types,
                tool_reuse_count=sum(max(0, tool_counts[tool] - 1) for tool in set(tools)),
                source_event_ids=[str(event.get("id") or "") for event in ordered if event.get("id")],
                raw={"turn_id": turn_id, "events": ordered},
            )
        )
    return traces


def load_tool_event_traces(path: str | Path | None = None) -> list[TraceRecord]:
    target = Path(path) if path else DEFAULT_TOOL_EVENTS_PATH
    if not target.exists():
        return []
    return tool_events_to_traces(read_jsonl(target))


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
    failure_types = Counter(failure for trace in traces for failure in trace.tool_failure_types)
    skills = Counter(skill for trace in traces for skill in trace.used_skills)
    sources = Counter(trace.source or "trace" for trace in traces)
    success_count = sum(1 for trace in traces if trace.success)
    tool_event_traces = [trace for trace in traces if trace.source == "tool_event"]
    return {
        "trace_count": len(traces),
        "success_count": success_count,
        "failure_count": len(traces) - success_count,
        "success_rate": round(success_count / len(traces), 4) if traces else 0.0,
        "sources": dict(sources.most_common()),
        "tool_event_trace_count": len(tool_event_traces),
        "tool_event_success_rate": round(
            sum(trace.tool_success_rate for trace in tool_event_traces) / len(tool_event_traces), 4
        )
        if tool_event_traces
        else 0.0,
        "outcomes": dict(outcomes.most_common()),
        "languages": dict(languages.most_common()),
        "frameworks": dict(frameworks.most_common()),
        "top_tools": dict(tools.most_common(12)),
        "error_types": dict(errors.most_common()),
        "tool_failure_types": dict(failure_types.most_common()),
        "used_skills": dict(skills.most_common()),
        "tool_reuse_count": sum(trace.tool_reuse_count for trace in traces),
    }


def ingest_traces(
    input_path: str | Path,
    output_path: str | Path | None = None,
    tool_events_path: str | Path | None = None,
    include_tool_events: bool = True,
) -> dict[str, Any]:
    ensure_project_dirs()
    traces = load_traces(input_path)
    event_path = Path(tool_events_path) if tool_events_path else DEFAULT_TOOL_EVENTS_PATH
    tool_event_traces = load_tool_event_traces(event_path) if include_tool_events else []
    seen_ids = {trace.id for trace in traces}
    appended_tool_event_traces = [trace for trace in tool_event_traces if trace.id not in seen_ids]
    traces.extend(appended_tool_event_traces)
    target = Path(output_path) if output_path else DATA_DIR / "processed_traces.jsonl"
    write_jsonl(target, [trace.to_mapping() for trace in traces])
    summary = summarize_traces(traces)
    summary["input_path"] = str(Path(input_path))
    summary["tool_events_path"] = str(event_path)
    summary["tool_events_seen"] = len(tool_event_traces)
    summary["tool_events_ingested"] = len(appended_tool_event_traces)
    summary["processed_path"] = str(target)
    write_json(REPORTS_DIR / "ingest_summary.json", summary)
    return summary
