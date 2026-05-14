import json
import pytest

from diaevo.cli import build_parser
from diaevo.knowledge_graph import (
    answer_kg,
    apply_kg_delta,
    build_kg_delta,
    export_kg_snapshot,
    graph_vector_search,
    kg_workbench,
    review_kg_delta,
    visualize_kg,
)
from diaevo.storage import read_json, read_jsonl
from diaevo.tool_layer import execute_tool, tool_schemas
from diaevo.tool_chat import chat_tool_schemas


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in records) + "\n", encoding="utf-8")


def _sample_trace_path(tmp_path):
    traces = tmp_path / "traces.jsonl"
    _write_jsonl(
        traces,
        [
            {
                "id": "T-KG-001",
                "task": "mine pytest tool usage from traces",
                "project": {"language": "python", "frameworks": ["pytest"], "files": ["diaevo/cli.py"]},
                "tools": ["rg", "pytest"],
                "commands": ["pytest tests/test_knowledge_graph.py -q"],
                "outcome": "success",
                "used_skills": ["test-failure-repair"],
                "tags": ["testing", "kg"],
            }
        ],
    )
    return traces


def _sample_tool_events_path(tmp_path):
    events = tmp_path / "tool_events.jsonl"
    _write_jsonl(
        events,
        [
            {
                "id": "evt-search",
                "turn_id": "turn-1",
                "tool": "web_search",
                "args": {"query": "knowledge graph constrained generation"},
                "status": "ok",
                "approval_required": True,
                "approved": True,
                "read_only": True,
                "destructive": False,
                "risk": "network",
                "started_at": "2026-05-13T00:00:00+00:00",
                "ended_at": "2026-05-13T00:00:01+00:00",
                "result": {
                    "status": "ok",
                    "tool": "web_search",
                    "query": "knowledge graph constrained generation",
                    "results": [{"title": "Graph constrained generation paper", "url": "https://example.org/kg"}],
                },
            },
            {
                "id": "evt-fetch",
                "turn_id": "turn-1",
                "tool": "web_fetch",
                "args": {"url": "https://example.org/kg"},
                "status": "ok",
                "approval_required": True,
                "approved": True,
                "read_only": True,
                "destructive": False,
                "risk": "network",
                "started_at": "2026-05-13T00:00:02+00:00",
                "ended_at": "2026-05-13T00:00:03+00:00",
                "result": {
                    "status": "ok",
                    "tool": "web_fetch",
                    "url": "https://example.org/kg",
                    "status_code": 200,
                    "content_type": "text/html",
                    "truncated": False,
                    "content": "A page about knowledge graph constrained generation.",
                },
            },
        ],
    )
    return events


def test_build_kg_delta_queues_reviewable_candidates_with_web_confidence(tmp_path):
    queue = tmp_path / "review_queue.jsonl"
    current = tmp_path / "current"
    delta_dir = tmp_path / "deltas"

    result = build_kg_delta(
        traces_path=_sample_trace_path(tmp_path),
        tool_events_path=_sample_tool_events_path(tmp_path),
        include_mining=False,
        queue_path=queue,
        current_dir=current,
        delta_dir=delta_dir,
    )

    assert result["status"] == "ok"
    assert result["queued_count"] > 0
    entries = read_jsonl(queue)
    assert all(item["status"] == "pending" for item in entries)
    confidences = {entry["item"]["source_type"]: entry["item"]["confidence"] for entry in entries}
    assert confidences["web_fetch"] > confidences["web_search"]
    delta = read_json(result["delta_path"])
    assert delta["entity_count"] >= 1
    assert delta["evidence_path_count"] >= 1


def test_review_and_apply_only_accepts_reviewed_kg_items(tmp_path):
    queue = tmp_path / "review_queue.jsonl"
    current = tmp_path / "current"
    build_kg_delta(
        traces_path=_sample_trace_path(tmp_path),
        tool_events_path=_sample_tool_events_path(tmp_path),
        include_mining=False,
        queue_path=queue,
        current_dir=current,
        delta_dir=tmp_path / "deltas",
    )
    entries = read_jsonl(queue)
    accepted_id = entries[0]["review_id"]
    rejected_id = entries[1]["review_id"]

    review_kg_delta(accepted_id, status="accepted", note="validated", reviewer="tester", queue_path=queue)
    review_kg_delta(rejected_id, status="rejected", note="not needed", reviewer="tester", queue_path=queue)
    applied = apply_kg_delta(queue_path=queue, current_dir=current)

    assert applied["applied_count"] == 1
    triples = read_jsonl(current / "triples.jsonl")
    claims = read_jsonl(current / "claims.jsonl")
    active_ids = {item["id"] for item in [*triples, *claims]}
    assert accepted_id in active_ids
    assert rejected_id not in active_ids


def test_strict_answer_uses_only_accepted_facts_and_refuses_missing_evidence(tmp_path):
    queue = tmp_path / "review_queue.jsonl"
    current = tmp_path / "current"
    build_kg_delta(
        traces_path=_sample_trace_path(tmp_path),
        tool_events_path=_sample_tool_events_path(tmp_path),
        include_mining=False,
        queue_path=queue,
        current_dir=current,
        delta_dir=tmp_path / "deltas",
    )
    entries = read_jsonl(queue)
    for entry in entries:
        if entry["item"]["source_type"] == "trace" and entry["item"].get("predicate") == "USES_TOOL":
            review_kg_delta(entry["review_id"], status="accepted", queue_path=queue)
            break
    apply_kg_delta(queue_path=queue, current_dir=current)

    answer = answer_kg("which tool did T-KG-001 use for pytest", current_dir=current, strict=True)
    assert answer["status"] == "ok"
    assert answer["evidence_paths"]
    assert "USES_TOOL" in answer["answer"]

    missing = answer_kg("what unsupported database migration source exists", current_dir=current, strict=True)
    assert missing["status"] == "insufficient"
    assert "KG insufficient" in missing["answer"]


def test_graph_vector_search_returns_seed_hits_and_graph_expansion(tmp_path):
    queue = tmp_path / "review_queue.jsonl"
    current = tmp_path / "current"
    build_kg_delta(
        traces_path=_sample_trace_path(tmp_path),
        tool_events_path=_sample_tool_events_path(tmp_path),
        include_mining=False,
        queue_path=queue,
        current_dir=current,
        delta_dir=tmp_path / "deltas",
    )
    for entry in read_jsonl(queue):
        item = entry["item"]
        if item.get("source_type") == "trace" and item.get("predicate") in {"DESCRIBES_TASK", "USES_TOOL"}:
            review_kg_delta(entry["review_id"], status="accepted", queue_path=queue)
    apply_kg_delta(queue_path=queue, current_dir=current)

    result = graph_vector_search("pytest tool usage", current_dir=current, strict=True, top_k=2)

    assert result["status"] == "ok"
    assert result["retrieval_mode"] == "graph_vector_tfidf"
    assert result["vector_index"]["backend"] == "local_tfidf"
    assert result["seed_hits"]
    assert result["subgraph"]["triples"]
    predicates = {item["predicate"] for item in result["subgraph"]["triples"]}
    assert "USES_TOOL" in predicates or "DESCRIBES_TASK" in predicates


def test_export_kg_snapshot_writes_human_readable_files(tmp_path):
    queue = tmp_path / "review_queue.jsonl"
    current = tmp_path / "current"
    build_kg_delta(
        traces_path=_sample_trace_path(tmp_path),
        tool_events_path=_sample_tool_events_path(tmp_path),
        include_mining=False,
        queue_path=queue,
        current_dir=current,
        delta_dir=tmp_path / "deltas",
    )
    first_id = read_jsonl(queue)[0]["review_id"]
    review_kg_delta(first_id, status="accepted", queue_path=queue)
    apply_kg_delta(queue_path=queue, current_dir=current)

    output_dir = tmp_path / "kg_snapshot"
    result = export_kg_snapshot(date="260513", output_dir=output_dir, current_dir=current)

    assert result["status"] == "ok"
    assert result["visualization_path"] == str(output_dir / "graph_visualization.html")
    for name in [
        "README.md",
        "entities.csv",
        "triples.csv",
        "claims.csv",
        "graph_edges.csv",
        "graph_visualization.html",
        "graph_vector_demo.md",
        "graph_vector_index.json",
        "graph_vector_retrieval.md",
        "evidence_paths.md",
        "confidence_summary.md",
        "summary.json",
    ]:
        assert (output_dir / name).exists()
    readme = (output_dir / "README.md").read_text(encoding="utf-8")
    assert "知识图谱快照 260513" in readme
    assert "graph_vector_index.json" in readme
    assert "graph_vector_demo.md" in readme
    html = (output_dir / "graph_visualization.html").read_text(encoding="utf-8")
    assert "可编辑知识图谱" in html
    assert "保存节点" in html
    assert "保存关系" in html
    assert "导出编辑 JSON" in html
    assert "accepted" in html
    summary = read_json(output_dir / "summary.json")
    assert summary["retrieval_mode"] == "graph_vector_tfidf"
    index = read_json(output_dir / "graph_vector_index.json")
    assert index["schema"] == "diaevo.kg_graph_vector_index.v1"
    assert index["documents"][0]["sparse_vector"]
    assert "图结构向量检索" in (output_dir / "graph_vector_retrieval.md").read_text(encoding="utf-8")


def test_visualize_kg_returns_openable_html_path(tmp_path):
    queue = tmp_path / "review_queue.jsonl"
    current = tmp_path / "current"
    build_kg_delta(
        traces_path=_sample_trace_path(tmp_path),
        tool_events_path=_sample_tool_events_path(tmp_path),
        include_mining=False,
        queue_path=queue,
        current_dir=current,
        delta_dir=tmp_path / "deltas",
    )
    first_id = read_jsonl(queue)[0]["review_id"]
    review_kg_delta(first_id, status="accepted", queue_path=queue)
    apply_kg_delta(queue_path=queue, current_dir=current)

    output_dir = tmp_path / "kg_visual"
    result = visualize_kg(date="260513", output_dir=output_dir, current_dir=current)

    assert result["status"] == "ok"
    assert result["visualization_path"] == str(output_dir / "graph_visualization.html")
    assert (output_dir / "graph_visualization.html").exists()
    assert "可编辑知识图谱 HTML" in result["message"]
    html = (output_dir / "graph_visualization.html").read_text(encoding="utf-8")
    assert "保存节点" in html
    assert "保存关系" in html
    assert "导出编辑 JSON" in html


def test_kg_workbench_can_preview_and_apply_exported_edit(tmp_path):
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(
        json.dumps(
            {
                "schema": "diaevo.kg_editor.v1",
                "entities": [
                    {"id": "trace:manual", "kind": "trace", "label": "手工轨迹", "properties": {}},
                    {"id": "tool:manual", "kind": "tool", "label": "手工工具", "properties": {}},
                ],
                "triples": [
                    {
                        "id": "triple:manual",
                        "subject": "trace:manual",
                        "predicate": "USES_TOOL",
                        "object": "tool:manual",
                        "confidence": 0.9,
                        "status": "accepted",
                        "source_type": "manual_edit",
                        "evidence": [],
                        "properties": {},
                    }
                ],
                "claims": [],
                "evidence_paths": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    current = tmp_path / "current"

    preview = kg_workbench(edit_path=edit_path, current_dir=current)
    assert preview["status"] == "requires_approval"
    assert not (current / "entities.jsonl").exists()

    applied = kg_workbench(edit_path=edit_path, current_dir=current, approve=True)
    assert applied["status"] == "ok"
    entities = read_jsonl(current / "entities.jsonl")
    assert [item["label"] for item in entities] == ["手工轨迹", "手工工具"]
    assert read_jsonl(current / "triples.jsonl")[0]["predicate"] == "USES_TOOL"


def test_cli_accepts_kg_commands_and_tool_schema_exposes_switch():
    args = build_parser().parse_args(["answer-kg", "--query", "pytest tools"])
    assert args.command == "answer-kg"
    assert args.strict is False

    args = build_parser().parse_args(["answer-kg", "--query", "pytest tools", "--strict"])
    assert args.strict is True

    args = build_parser().parse_args(["review-kg-delta", "--review-id", "triple:abc", "--status", "needs_source"])
    assert args.command == "review-kg-delta"
    assert args.status == "needs_source"

    args = build_parser().parse_args(["visualize-kg", "--date", "260513"])
    assert args.command == "visualize-kg"
    assert args.date == "260513"

    args = build_parser().parse_args(["kg", "--date", "260513"])
    assert args.command == "kg"
    assert args.date == "260513"

    args = build_parser().parse_args(["kg", "--apply-edit", "edit.json", "--approve"])
    assert args.apply_edit == "edit.json"
    assert args.approve is True

    schemas = {item["name"]: item for item in tool_schemas()}
    assert schemas["kg_answer"]["read_only"] is True
    assert schemas["kg_answer"]["approval_required"] is False
    chat_names = {item["function"]["name"] for item in chat_tool_schemas()}
    assert "kg_answer" not in chat_names


def test_cli_help_keeps_low_level_kg_commands_out_of_user_surface(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    help_text = capsys.readouterr().out

    assert "kg                  打开可编辑知识图谱工作台" in help_text
    assert "answer-kg           显式使用已审核知识图谱回答" in help_text
    for name in ["build-kg-delta", "review-kg-delta", "apply-kg-delta", "export-kg-snapshot", "visualize-kg"]:
        assert name not in help_text


def test_cli_invalid_command_error_keeps_low_level_kg_commands_out_of_user_surface(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["does-not-exist"])
    error_text = capsys.readouterr().err

    assert "kg" in error_text
    assert "answer-kg" in error_text
    for name in ["build-kg-delta", "review-kg-delta", "apply-kg-delta", "export-kg-snapshot", "visualize-kg"]:
        assert name not in error_text


def test_kg_answer_tool_is_explicit_switch(tmp_path):
    result = execute_tool(
        "kg_answer",
        {"query": "anything", "strict": True, "current_dir": str(tmp_path / "empty")},
        event_log_path=tmp_path / "events.jsonl",
    )

    assert result["status"] == "insufficient"
    assert result["tool"] == "kg_answer"
