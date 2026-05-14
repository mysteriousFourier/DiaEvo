from pathlib import Path


def test_handoff_records_chinese_first_language_rule() -> None:
    text = Path("docs/HANDOFF.md").read_text(encoding="utf-8")

    assert "Must-Read Language Rule" in text
    assert "user-facing content must be Chinese by default" in text
    assert "Treat any new English user-facing text as a bug" in text


def test_handoff_records_phase5_ready_after_phase4_dry_run_gate() -> None:
    handoff = Path("docs/HANDOFF.md").read_text(encoding="utf-8")
    advanced = Path("docs/HANDOFF_ADVANCED_SKILL_EVOLUTION.md").read_text(encoding="utf-8")
    loop = Path("docs/AUTONOMOUS_EVOLUTION_LOOP.md").read_text(encoding="utf-8")

    combined = "\n".join([handoff, advanced, loop])
    assert "Phase 4 dry-run/reporting gate 已完成，Phase 5 可以开始" in combined
    assert "outputs/reports/gepa_phase4_experiments.json" in combined
    assert "dry_run=true" in combined
    assert "safety_false_negative_rate == 0.0" in combined
    assert ".tmp/validation-runs/<id>" in combined
