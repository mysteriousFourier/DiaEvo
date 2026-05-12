from skillminer.association_rules import mine_association_rules
from skillminer.clustering import cluster_traces
from skillminer.ingest import ingest_traces, load_traces
from skillminer.sequence_mining import mine_frequent_sequences


def test_mining_pipeline_finds_clusters_rules_and_sequences():
    traces = load_traces("data/sample_traces.jsonl")
    _, _, clusters = cluster_traces(traces, k=4)
    rules = mine_association_rules(traces, min_support=1, min_confidence=0.1)
    sequences = mine_frequent_sequences(traces, min_support=2)
    assert clusters
    assert any(cluster.coverage_gap > 0 for cluster in clusters)
    assert any(cluster.explanations for cluster in clusters)
    assert any(rule["skill"] == "test-failure-repair" for rule in rules)
    assert any("pytest" in sequence["sequence"] for sequence in sequences)


def test_ingest_merges_tool_events(tmp_path):
    event_log = tmp_path / "tool_events.jsonl"
    event_log.write_text(
        '{"id":"e1","turn_id":"turn1","tool":"read_file","args":{"path":"README.md"},"status":"ok","approval_required":false,"approved":false,"read_only":true,"destructive":false,"risk":"low","started_at":"2026-05-12T00:00:00+00:00","ended_at":"2026-05-12T00:00:01+00:00","result":{"status":"ok","tool":"read_file","path":"README.md"}}\n',
        encoding="utf-8",
    )
    output = tmp_path / "processed.jsonl"

    summary = ingest_traces("data/sample_traces.jsonl", output, tool_events_path=event_log)
    traces = load_traces(output)

    assert summary["tool_events_seen"] == 1
    assert summary["tool_events_ingested"] == 1
    assert any(trace.source == "tool_event" for trace in traces)
    assert summary["sources"]["tool_event"] == 1

    second_output = tmp_path / "processed-again.jsonl"
    second_summary = ingest_traces(output, second_output, tool_events_path=event_log)
    assert second_summary["tool_events_seen"] == 1
    assert second_summary["tool_events_ingested"] == 0
    assert second_summary["trace_count"] == summary["trace_count"]
