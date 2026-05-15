from pathlib import Path

import shutil

from diaevo.cli import build_parser
from diaevo.generator import generate_skill
from diaevo.ingest import ingest_traces
from diaevo.miner import mine
from diaevo.storage import read_json
from diaevo.validation_runner import run_validation
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


def test_generate_code_backed_skill_validates_in_sandbox(tmp_path):
    ingest_traces("data/sample_traces.jsonl")
    report = mine(k=4)
    cluster_id = report["clusters"][0]["id"]
    output_dir = Path(".tmp") / "tests" / tmp_path.name / "code-backed"
    shutil.rmtree(output_dir, ignore_errors=True)
    generated = generate_skill(cluster_id, output_dir=output_dir, with_code=True)
    skill_dir = Path(generated["skill_dir"])

    assert generated["code_backed"] is True
    assert (skill_dir / "scripts" / "skill_flow.py").exists()
    assert (skill_dir / "code_artifacts.json").exists()
    assert (skill_dir / "validation.json").exists()
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## Executable Artifacts" in text
    artifacts = read_json(skill_dir / "code_artifacts.json")
    assert artifacts["entrypoint"] == "scripts/skill_flow.py"
    validation = read_json(skill_dir / "validation.json")
    assert validation["commands"] == [f"python {skill_dir.as_posix()}/scripts/skill_flow.py --describe"]

    verify_result = verify_skill(skill_dir)
    assert verify_result["passed"]

    preview = run_validation(skill_dir)
    assert preview["status"] == "requires_approval"
    validated = run_validation(skill_dir, approve=True)
    assert validated["status"] == "passed"
    assert "read_only_skill_flow" in validated["results"][0]["stdout"]
    assert Path(validated["sandbox_workspace"]).exists()
    assert (skill_dir / "scripts" / "skill_flow.py").exists()


def test_cli_accepts_generate_with_code():
    args = build_parser().parse_args(["generate", "--cluster-id", "C03", "--with-code"])

    assert args.command == "generate"
    assert args.cluster_id == "C03"
    assert args.with_code is True


def test_verifier_blocks_code_artifact_forbidden_helper_capability(tmp_path):
    skill_dir = tmp_path / "bad-code-backed"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: bad-code-backed",
                "description: generated helper code safety test with enough context",
                "tags: [code, safety]",
                "source_cluster: C99",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Only for helper verifier testing.",
                "",
                "## Trigger Signals",
                "- helper",
                "",
                "## Operating Steps",
                "1. Describe the helper flow.",
                "",
                "## Failure Fallbacks",
                "- Stop.",
                "",
                "## Verification Suggestions",
                "- Verify helper constraints.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "code_artifacts.json").write_text(
        '{"schema":"diaevo.code_backed_skill.v1","entrypoint":"scripts/skill_flow.py"}',
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "skill_flow.py").write_text("import subprocess\n", encoding="utf-8")

    result = verify_skill(skill_dir)
    assert not result["passed"]
    assert any(item["code"] == "forbidden_helper_capability" for item in result["findings"])


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
