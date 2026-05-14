from pathlib import Path

from diaevo.generator import generate_skill
from diaevo.ingest import ingest_traces
from diaevo.miner import mine
from diaevo.verifier import verify_skill


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
    assert "任务关键词" in text
    assert "人工审核" in text
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
