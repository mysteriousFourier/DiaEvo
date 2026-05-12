from skillminer.evaluation import baseline_report, precision_at_k, reciprocal_rank


def test_ranking_metrics():
    ranked = ["a", "b", "c"]
    relevant = {"b", "d"}

    assert precision_at_k(ranked, relevant, 1) == 0.0
    assert precision_at_k(ranked, relevant, 2) == 0.5
    assert reciprocal_rank(ranked, relevant) == 0.5


def test_baseline_report_writes_required_metrics(tmp_path):
    processed_path = tmp_path / "processed.jsonl"
    event_log = tmp_path / "tool_events.jsonl"
    event_log.write_text(
        '{"id":"e1","turn_id":"turn1","tool":"read_file","args":{"path":"README.md"},"status":"ok","approval_required":false,"approved":false,"read_only":true,"destructive":false,"risk":"low","started_at":"2026-05-12T00:00:00+00:00","ended_at":"2026-05-12T00:00:01+00:00","result":{"status":"ok","tool":"read_file","path":"README.md"}}\n',
        encoding="utf-8",
    )

    report = baseline_report(
        input_path="data/sample_traces.jsonl",
        processed_path=processed_path,
        tool_events_path=event_log,
        top_k=3,
    )

    metrics = report["metrics"]
    assert report["report_path"].endswith("baseline_metrics.json")
    assert metrics["query_count"] > 0
    assert "precision_at_1" in metrics
    assert "precision_at_3" in metrics
    assert "mrr" in metrics
    assert "coverage_gap_hit_rate" in metrics
    assert "verifier_pass_rate" in metrics
    assert "candidate_duplicate_rate" in metrics
    assert "recommendation_lift" in metrics
    assert "safety_false_negative_rate" in metrics
    assert metrics["safety_false_negative_rate"] == 0.0
    assert report["ingest_summary"]["tool_events_ingested"] == 1
