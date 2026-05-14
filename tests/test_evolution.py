from pathlib import Path

from diaevo.evolution import evolve_skill, pareto_frontier, render_candidate_skill
from diaevo.ingest import ingest_traces
from diaevo.miner import mine


def test_evolve_skill_writes_verified_candidate(tmp_path):
    ingest_traces("data/sample_traces.jsonl", tmp_path / "processed.jsonl", include_tool_events=False)
    report = mine(tmp_path / "processed.jsonl", k=4)
    cluster_id = report["clusters"][0]["id"]

    result = evolve_skill(cluster_id, budget=4, output_dir=tmp_path / "evolved", memory_path=tmp_path / "memory.json")

    run = result["runs"][0]
    skill_path = Path(run["output"]["skill_path"])
    assert skill_path.exists()
    assert run["best_candidate"]["passed"]
    assert "evidence_alignment" in run["best_candidate"]["scores"]
    text = skill_path.read_text(encoding="utf-8")
    assert "## Mined Evidence" in text
    assert "## Safety Constraints" in text


def test_render_candidate_includes_required_sections():
    cluster = {
        "id": "C99",
        "representative_task": "Fix pytest failure",
        "top_terms": ["pytest"],
        "top_tools": ["pytest"],
        "trace_ids": ["T1"],
    }
    candidate = {
        "when_to_use": "Use for pytest failures.",
        "trigger_signals": "- pytest",
        "operating_steps": "1. Reproduce.",
        "failure_fallbacks": "- Stop.",
        "verification_suggestions": "- Run verifier.",
        "safety_constraints": "- Stay in workspace.",
    }

    markdown = render_candidate_skill(candidate, cluster)

    assert "## When To Use" in markdown
    assert "## Trigger Signals" in markdown
    assert "## Mined Evidence" in markdown
    assert "source_cluster" in markdown


def test_pareto_frontier_keeps_non_dominated_candidates():
    from diaevo.evolution import CandidateEval

    def item(name: str, verifier: float, evidence: float, safety: float) -> CandidateEval:
        return CandidateEval(
            candidate_id=name,
            score=verifier + evidence + safety,
            scores={
                "verifier": verifier,
                "evidence_alignment": evidence,
                "non_duplicate": 1.0,
                "specificity": evidence,
                "safety": safety,
                "length": 1.0,
            },
            passed=True,
            rejected=False,
            rejection_reason="",
            warning_count=0,
            error_count=0,
            duplicate_similarity=0.0,
            length=100,
            findings=[],
            side_info={},
        )

    frontier = pareto_frontier([item("safe", 1.0, 0.4, 1.0), item("evidence", 1.0, 0.9, 0.8)])
    names = {candidate.candidate_id for candidate in frontier}
    assert names == {"safe", "evidence"}
