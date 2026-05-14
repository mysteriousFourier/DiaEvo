from diaevo.evaluation import baseline_report, deterministic_trace_split, precision_at_k, reciprocal_rank
from diaevo.ingest import load_traces


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
    assert "memory_summary" in report
    assert metrics["safety_false_negative_rate"] == 0.0
    assert report["ingest_summary"]["tool_events_ingested"] == 1


def test_deterministic_trace_split_is_stable(tmp_path):
    processed_path = tmp_path / "processed.jsonl"
    baseline_report(input_path="data/sample_traces.jsonl", processed_path=processed_path, include_tool_events=False)
    traces = load_traces(processed_path)

    first_train, first_holdout = deterministic_trace_split(traces)
    second_train, second_holdout = deterministic_trace_split(list(reversed(traces)))

    assert {trace.id for trace in first_train} == {trace.id for trace in second_train}
    assert {trace.id for trace in first_holdout} == {trace.id for trace in second_holdout}
    assert first_holdout
    assert len(first_train) + len(first_holdout) == len(traces)


def test_evolved_report_includes_heldout_metrics(tmp_path):
    report = baseline_report(
        input_path="data/sample_traces.jsonl",
        processed_path=tmp_path / "processed.jsonl",
        include_tool_events=False,
        top_k=3,
        variant="evolved",
    )

    metrics = report["metrics"]
    assert report["report_path"].endswith("evolved_metrics.json")
    assert "heldout_trace_count" in metrics
    assert "heldout_mrr" in metrics
    assert "heldout_usefulness_status" in metrics
    assert "heldout_recommendation_status" in metrics
    assert "heldout_candidate_discovery_status" in metrics
    assert "raw_evolved_mrr_delta" in metrics
    assert "heldout_failed_evolved_candidate_count" in metrics
    assert "heldout_seed_mrr" in metrics
    assert "heldout_evolved_candidate_top_k_hit_rate" in metrics
    assert "baseline_vs_evolved_count" in metrics
    assert report["heldout_eval"]["split"]["heldout_ids"]
    assert "cluster_summaries" in report["heldout_eval"]
    assert "failed_recommendations" in report["heldout_eval"]
    assert "raw_augmented_recommendation_eval" in report["heldout_eval"]
    assert "asi" in report["heldout_eval"]
    assert "memory_summary" in report
    assert metrics["heldout_usefulness_status"] == "improved"
    assert metrics["heldout_candidate_discovery_status"] == "improved"
    assert metrics["heldout_recommendation_status"] == "neutral"
    assert metrics["heldout_evolved_candidate_top_k_hit_rate_delta"] > 0
    assert metrics["heldout_mrr_delta"] == 0.0
    assert metrics["raw_evolved_mrr_delta"] < 0
    assert metrics["safety_false_negative_rate"] == 0.0
