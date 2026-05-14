from pathlib import Path


def test_public_docs_record_chinese_first_language_rule() -> None:
    text = Path("README.md").read_text(encoding="utf-8") + "\n" + Path("docs/DESIGN.md").read_text(encoding="utf-8")

    assert "默认中文优先" in text
    assert "用户可见内容" in text
    assert "禁止使用 emoji" in text


def test_public_docs_record_current_phase_and_sandbox_boundary() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    design = Path("docs/DESIGN.md").read_text(encoding="utf-8")
    loop = Path("docs/AUTONOMOUS_EVOLUTION_LOOP.md").read_text(encoding="utf-8")

    combined = "\n".join([readme, design, loop])
    assert "Phase 6 人工反馈学习已完成，Phase 7 安全代码演化研究已开始" in combined
    assert "outputs/reports/gepa_phase4_experiments.json" in combined
    assert "safety_false_negative_rate == 0.0" in combined
    assert ".tmp/validation-runs/<id>" in combined
