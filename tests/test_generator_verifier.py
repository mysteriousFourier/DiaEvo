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
    text = skill_path.read_text(encoding="utf-8")
    assert "## Operating Steps" in text
    assert "## Failure Fallbacks" in text
    assert "## Verification Suggestions" in text
    result = verify_skill(generated["skill_dir"])
    assert result["passed"]
    assert result["risk_score"] < 0.5


def test_verifier_blocks_dangerous_command(tmp_path):
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: bad",
                "description: dangerous generated skill for testing with enough context",
                "tags: [security]",
                "source_cluster: C99",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Only for verifier testing.",
                "",
                "## Trigger Signals",
                "- security",
                "",
                "## Operating Steps",
                "1. Run `rm -rf /`.",
                "",
                "## Failure Fallbacks",
                "- Stop.",
                "",
                "## Verification Suggestions",
                "- Verify the verifier blocks this.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = verify_skill(skill_dir)
    assert not result["passed"]
    assert any(item["code"] == "dangerous_command" for item in result["findings"])
