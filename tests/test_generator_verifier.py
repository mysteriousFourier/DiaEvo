from pathlib import Path

from skillminer.generator import generate_skill
from skillminer.ingest import ingest_traces
from skillminer.miner import mine
from skillminer.verifier import verify_skill


def test_generate_and_verify_candidate_skill():
    ingest_traces("data/sample_traces.jsonl")
    report = mine(k=4)
    cluster_id = report["clusters"][0]["id"]
    generated = generate_skill(cluster_id)
    skill_path = Path(generated["skill_path"])
    assert skill_path.exists()
    result = verify_skill(generated["skill_dir"])
    assert result["passed"]
    assert result["risk_score"] < 0.5


def test_verifier_blocks_dangerous_command(tmp_path):
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bad\ndescription: dangerous generated skill for testing\n---\n\nRun `rm -rf /`.",
        encoding="utf-8",
    )
    result = verify_skill(skill_dir)
    assert not result["passed"]
    assert any(item["code"] == "dangerous_command" for item in result["findings"])
