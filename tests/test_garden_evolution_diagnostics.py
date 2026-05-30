from __future__ import annotations

import json
from pathlib import Path

from scripts.summarize_garden_evolution_diagnostics import build_diagnostic_summary, render_markdown


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_run(
    root: Path,
    *,
    strategy: str,
    stage1: float,
    stage2: float,
    stage1_bad_cases: int,
    stage2_bad_cases: int,
    hit_ratio: float,
    miss_tokens: int,
    duration_seconds: float | None = None,
) -> None:
    _write_json(
        root / "reports" / "stage_scores.json",
        {
            "stage1_migrated_skill": {
                "aggregate": stage1,
                "bad_cases": [{"label": f"s1_{index}"} for index in range(stage1_bad_cases)],
            },
            "stage2_local_evolved": {
                "aggregate": stage2,
                "bad_cases": [{"label": f"s2_{index}"} for index in range(stage2_bad_cases)],
            },
        },
    )
    _write_json(
        root / "reports" / "prompt_cache_summary.json",
        {
            "hit_ratio": hit_ratio,
            "hit_tokens": 100,
            "miss_tokens": miss_tokens,
            "prompt_tokens": miss_tokens + 100,
            "call_count": 3,
            "reported_call_count": 3,
        },
    )
    _write_json(
        root / "reports" / "adoption_decision.json",
        {
            "status": "adopted_local_evolved" if stage2 > stage1 else "not_adopted",
            "final_source": "local_evolved" if stage2 > stage1 else "migrated",
        },
    )
    report = {
        "status": "migration_evolution_passed",
        "prompt_strategy": strategy,
        "task": {"task_id": "test_task"},
    }
    if duration_seconds is not None:
        report["runtime"] = {"duration_seconds": duration_seconds}
    _write_json(root / "reports" / "final_experiment_report.json", report)


def test_diagnostic_summary_extracts_stage_delta_cache_and_verdict(tmp_path):
    comparison_root = tmp_path / "comparison"
    _write_run(
        comparison_root / "legacy",
        strategy="legacy",
        stage1=7.0,
        stage2=7.2,
        stage1_bad_cases=2,
        stage2_bad_cases=1,
        hit_ratio=0.0,
        miss_tokens=1000,
        duration_seconds=20.0,
    )
    _write_run(
        comparison_root / "cache_first",
        strategy="cache_first",
        stage1=7.0,
        stage2=7.4,
        stage1_bad_cases=2,
        stage2_bad_cases=1,
        hit_ratio=0.5,
        miss_tokens=700,
        duration_seconds=16.0,
    )
    _write_json(
        comparison_root / "reports" / "cache_first_comparison.json",
        {"cache_first_vs_legacy": {"hit_ratio_delta": 0.5, "miss_tokens_delta": -300}},
    )

    summary = build_diagnostic_summary([comparison_root], limit=10)

    assert summary["run_count"] == 2
    runs = {Path(run["experiment_root"]).name: run for run in summary["runs"]}
    assert runs["cache_first"]["stage2_minus_stage1"] == 0.4
    assert runs["cache_first"]["bad_case_delta"] == -1
    assert runs["cache_first"]["quality_floor_passed"] is True
    assert runs["cache_first"]["cache"]["llm_call_count"] == 3
    assert summary["comparisons"][0]["miss_tokens_reduction_ratio"] == 0.3
    assert summary["comparisons"][0]["runtime_reduction_ratio"] == 0.2
    assert summary["comparisons"][0]["speed_verdict"] == "speed_candidate"
    assert "Stage2-Stage1" in render_markdown(summary)


def test_diagnostic_marks_quality_strategy_when_cache_first_spends_more_tokens(tmp_path):
    comparison_root = tmp_path / "comparison"
    _write_run(
        comparison_root / "legacy",
        strategy="legacy",
        stage1=7.0,
        stage2=7.1,
        stage1_bad_cases=1,
        stage2_bad_cases=1,
        hit_ratio=0.0,
        miss_tokens=1000,
    )
    _write_run(
        comparison_root / "cache_first",
        strategy="cache_first",
        stage1=7.0,
        stage2=7.6,
        stage1_bad_cases=1,
        stage2_bad_cases=1,
        hit_ratio=0.4,
        miss_tokens=1200,
    )
    _write_json(
        comparison_root / "reports" / "cache_first_comparison.json",
        {"cache_first_vs_legacy": {"hit_ratio_delta": 0.4, "miss_tokens_delta": 200}},
    )

    summary = build_diagnostic_summary([comparison_root], limit=10)

    assert summary["comparisons"][0]["speed_verdict"] == "quality_strategy_not_speed"


def test_diagnostic_marks_comparison_incomplete_when_child_run_is_missing(tmp_path):
    comparison_root = tmp_path / "comparison"
    _write_run(
        comparison_root / "legacy",
        strategy="legacy",
        stage1=7.0,
        stage2=7.1,
        stage1_bad_cases=1,
        stage2_bad_cases=1,
        hit_ratio=0.0,
        miss_tokens=1000,
    )
    _write_json(
        comparison_root / "reports" / "cache_first_comparison.json",
        {"cache_first_vs_legacy": {"hit_ratio_delta": 0.0, "miss_tokens_delta": 0}},
    )

    summary = build_diagnostic_summary([comparison_root], limit=10)

    assert summary["comparisons"][0]["miss_tokens_reduction_ratio"] is None
    assert summary["comparisons"][0]["speed_verdict"] == "incomplete_comparison"


def test_diagnostic_includes_failed_partial_run_from_final_status(tmp_path):
    root = tmp_path / "failed"
    _write_json(
        root / "reports" / "final_status.json",
        {
            "status": "failed_extract_html",
            "experiment_root": str(root),
            "prompt_strategy": "cache_first",
            "stage": "stage2_local_evolved",
            "stage_scores": {
                "stage1_migrated_skill": {"aggregate": 9.143, "bad_cases": []},
                "stage2_local_evolved": {
                    "aggregate": 0,
                    "bad_cases": [{"label": "html_extract_failed"}],
                },
            },
            "prompt_cache": {
                "hit_ratio": 0.08,
                "miss_tokens": 12521,
                "call_count": 3,
                "reported_call_count": 3,
            },
            "runtime": {"duration_seconds": 761.297},
        },
    )

    summary = build_diagnostic_summary([root], limit=10)

    assert summary["run_count"] == 1
    run = summary["runs"][0]
    assert run["status"] == "failed_extract_html"
    assert run["stage2_minus_stage1"] == -9.143
    assert run["quality_floor_passed"] is False
    assert run["cache"]["miss_tokens"] == 12521
    assert run["runtime"]["duration_seconds"] == 761.297
