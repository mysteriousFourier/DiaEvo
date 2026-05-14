from pathlib import Path

import pytest

from diaevo.cli import build_parser
import diaevo.gepa_adapter as gepa_adapter
from diaevo.gepa_adapter import (
    GEPAUnavailableError,
    _cheap_gate_result,
    _filter_memory,
    _judge_uncertainty_reasons,
    evaluate_gepa,
    evaluate_gepa_phase4,
)
from diaevo.evolution import CandidateEval
from diaevo.ingest import ingest_traces
from diaevo.miner import mine
from diaevo.paths import REPORTS_DIR
from diaevo.storage import read_json


def _cluster_id(tmp_path):
    ingest_traces("data/sample_traces.jsonl", tmp_path / "processed.jsonl", include_tool_events=False)
    report = mine(tmp_path / "processed.jsonl", k=4)
    return report["clusters"][0]["id"]


def test_evaluate_gepa_dry_run_writes_redacted_report(tmp_path, monkeypatch):
    for key in list(("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_TEMPERATURE")):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=sk-real-test-secret",
                "DEEPSEEK_BASE_URL=https://api.deepseek.com",
                "DEEPSEEK_MODEL=deepseek-v4-pro",
                "DEEPSEEK_MAX_TOKENS=128",
                "DEEPSEEK_TEMPERATURE=0.1",
            ]
        ),
        encoding="utf-8",
    )
    cluster_id = _cluster_id(tmp_path)

    report = evaluate_gepa(
        cluster_id,
        budget=2,
        processed_path=tmp_path / "processed_gepa.jsonl",
        include_tool_events=False,
        env_path=str(env_path),
        dry_run=True,
        top_k=3,
    )

    assert report["status"] == "dry_run"
    assert report["provider"]["provider"] == "deepseek"
    assert report["provider"]["api_key_configured"] is True
    assert "api_key" not in report["provider"]
    assert report["comparison"]["gepa"]["status"] == "skipped_dry_run"
    assert "seed" in report["comparison"]
    assert "local_evolved" in report["comparison"]
    assert report["safety_eval"]["metrics"]["safety_false_negative_rate"] == 0.0
    saved_text = Path(REPORTS_DIR / "gepa_skill_optimization.json").read_text(encoding="utf-8")
    assert "sk-real-test-secret" not in saved_text
    saved = read_json(REPORTS_DIR / "gepa_skill_optimization.json")
    assert saved["provider"]["api_key_source"] == ".env:DEEPSEEK_API_KEY"


def test_evaluate_gepa_dry_run_records_phase4_controls(tmp_path, monkeypatch):
    for key in list(("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_TEMPERATURE")):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=sk-real-test-secret",
                "DEEPSEEK_BASE_URL=https://api.deepseek.com",
                "DEEPSEEK_MODEL=deepseek-v4-pro",
            ]
        ),
        encoding="utf-8",
    )
    cluster_id = _cluster_id(tmp_path)

    report = evaluate_gepa(
        cluster_id,
        budget=3,
        processed_path=tmp_path / "processed_gepa.jsonl",
        include_tool_events=False,
        env_path=str(env_path),
        dry_run=True,
        top_k=3,
        condition="gepa_ctm_epm",
        memory_policy="ctm_epm",
        racing_policy="cheap_gates",
        judge_policy="uncertainty_only",
    )

    assert report["phase4_controls"] == {
        "memory_policy": "ctm_epm",
        "racing_policy": "cheap_gates",
        "judge_policy": "uncertainty_only",
    }
    assert report["experiment"]["condition"] == "gepa_ctm_epm"
    assert report["experiment"]["budget"] == 3
    assert report["experiment"]["metric_calls"] == 0
    assert report["cost"]["racing_rejected_count"] == 0


def test_memory_policy_filtering():
    memory = {
        "correct_templates": [{"schema": "correct_template.v2"}],
        "error_patterns": [{"schema": "verifier_feedback.v2"}],
        "validation_patterns": [{"schema": "validation_feedback.v2"}],
        "duplicate_patterns": [{"schema": "duplicate_feedback.v2"}],
        "promotion_patterns": [{"schema": "promotion_feedback.v2"}],
    }

    assert _filter_memory(memory, "none") == {
        "correct_templates": [],
        "error_patterns": [],
        "validation_patterns": [],
        "duplicate_patterns": [],
        "promotion_patterns": [],
    }
    assert len(_filter_memory(memory, "ctm")["correct_templates"]) == 1
    assert _filter_memory(memory, "ctm")["error_patterns"] == []
    assert _filter_memory(memory, "epm")["correct_templates"] == []
    assert len(_filter_memory(memory, "epm")["validation_patterns"]) == 1
    assert len(_filter_memory(memory, "ctm_epm")["promotion_patterns"]) == 1


def _candidate_eval(**overrides):
    base = {
        "candidate_id": "x",
        "score": 0.5,
        "scores": {"verifier": 1.0, "evidence_alignment": 0.2},
        "passed": True,
        "rejected": False,
        "rejection_reason": "",
        "warning_count": 0,
        "error_count": 0,
        "duplicate_similarity": 0.83,
        "length": 500,
        "findings": [],
        "side_info": {"duplicate": {"recommended_action": "merge"}},
    }
    base.update(overrides)
    return CandidateEval(**base)


def test_cheap_gate_rejects_thin_and_unsafe_candidate():
    scored = {
        "side_info": {
            "candidate": {"missing_or_thin_sections": ["when_to_use"]},
            "scores": {"example_alignment": 0.0},
        }
    }
    result = _cheap_gate_result(
        candidate={"when_to_use": "thin"},
        markdown="## When To Use\napi_key: secret\nrm -rf .\n",
        eval_result=_candidate_eval(rejected=True),
        scored=scored,
    )

    assert result["passed"] is False
    assert "credential_pattern" in result["reasons"]
    assert "dangerous_command_pattern" in result["reasons"]
    assert "poor_example_alignment" in result["reasons"]
    assert "static_eval_rejected" in result["reasons"]


def test_cheap_gate_allows_negated_auto_install_policy():
    scored = {
        "side_info": {
            "candidate": {"missing_or_thin_sections": []},
            "scores": {"example_alignment": 0.5},
        }
    }
    result = _cheap_gate_result(
        candidate={"when_to_use": "This section has enough task-grounded context."},
        markdown="Do not auto-promote or auto-install this generated candidate.",
        eval_result=_candidate_eval(duplicate_similarity=0.1, side_info={"duplicate": {"recommended_action": "keep"}}),
        scored=scored,
    )

    assert result["passed"] is True


def test_judge_uncertainty_reasons_are_sparse():
    scored = {"side_info": {"scores": {"aggregate": 0.4}}}
    reasons = _judge_uncertainty_reasons(_candidate_eval(), scored)

    assert "verifier_evidence_disagreement" in reasons
    assert "low_aggregate_after_pass" in reasons
    assert "near_duplicate_ambiguity" not in reasons


def test_judge_near_duplicate_alone_does_not_trigger():
    scored = {"side_info": {"scores": {"aggregate": 0.7}}}
    reasons = _judge_uncertainty_reasons(
        _candidate_eval(scores={"verifier": 1.0, "evidence_alignment": 0.7, "non_duplicate": 0.05}),
        scored,
    )

    assert reasons == []


def test_phase4_dry_run_writes_experiment_matrix(tmp_path, monkeypatch):
    for key in list(("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_TEMPERATURE")):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=sk-real-test-secret\n", encoding="utf-8")
    cluster_id = _cluster_id(tmp_path)

    report = evaluate_gepa_phase4(
        cluster_id,
        budgets="5,10",
        processed_path=tmp_path / "processed_phase4.jsonl",
        include_tool_events=False,
        env_path=str(env_path),
        dry_run=True,
        top_k=3,
        resume=False,
    )

    assert report["status"] == "ok"
    assert report["budgets"] == [5, 10]
    assert len(report["rows"]) == 13
    assert report["rows"][0]["condition"] == "local_evolved"
    assert report["rows"][0]["budget"] == 0
    assert {row["condition"] for row in report["rows"]} >= {"gepa_seed_only", "gepa_racing", "gepa_sparse_judge"}
    saved = read_json(REPORTS_DIR / "gepa_phase4_experiments.json")
    assert saved["phase"] == "phase4_low_cost_gepa_apo"
    assert saved["status"] == "ok"


def test_phase4_resume_skips_completed_rows(tmp_path, monkeypatch):
    for key in list(("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_TEMPERATURE")):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=sk-real-test-secret\n", encoding="utf-8")
    cluster_id = _cluster_id(tmp_path)

    first = evaluate_gepa_phase4(
        cluster_id,
        budgets="5",
        processed_path=tmp_path / "processed_phase4.jsonl",
        include_tool_events=False,
        env_path=str(env_path),
        dry_run=True,
        top_k=3,
        resume=False,
    )
    second = evaluate_gepa_phase4(
        cluster_id,
        budgets="5",
        processed_path=tmp_path / "processed_phase4.jsonl",
        include_tool_events=False,
        env_path=str(env_path),
        dry_run=True,
        top_k=3,
        resume=True,
    )

    assert len(first["rows"]) == 7
    assert len(second["rows"]) == 7
    assert second["status"] == "ok"


def test_cli_accepts_phase4_arguments():
    args = build_parser().parse_args(
        [
            "evaluate-gepa-phase4",
            "--cluster-id",
            "C03",
            "--budgets",
            "5,10",
            "--no-tool-events",
            "--dry-run",
            "--no-resume",
        ]
    )

    assert args.command == "evaluate-gepa-phase4"
    assert args.budgets == "5,10"
    assert args.no_tool_events is True
    assert args.no_resume is True


def test_evaluate_gepa_requires_deepseek_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=sk-your-real-deepseek-api-key\n", encoding="utf-8")
    cluster_id = _cluster_id(tmp_path)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY is missing"):
        evaluate_gepa(
            cluster_id,
            processed_path=tmp_path / "processed_gepa.jsonl",
            include_tool_events=False,
            env_path=str(env_path),
            dry_run=True,
        )


def test_evaluate_gepa_dependency_gate_after_env(tmp_path, monkeypatch):
    for key in list(("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_TEMPERATURE")):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=sk-real-test-secret\n", encoding="utf-8")
    cluster_id = _cluster_id(tmp_path)

    def unavailable_stack():
        raise GEPAUnavailableError("GEPA dependency is not installed")

    monkeypatch.setattr(gepa_adapter, "_import_gepa_stack", unavailable_stack)

    with pytest.raises(RuntimeError, match="GEPA dependency is not installed"):
        evaluate_gepa(
            cluster_id,
            budget=1,
            processed_path=tmp_path / "processed_gepa.jsonl",
            include_tool_events=False,
            env_path=str(env_path),
            dry_run=False,
        )
