from pathlib import Path
import shutil

from skillminer import evolution
from skillminer.storage import read_json
from skillminer.validation_runner import run_validation


def _write_skill(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: validation-test",
                "description: generated validation skill with enough context for testing",
                "tags: [validation]",
                "source_cluster: C01",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Use only in tests.",
                "",
                "## Trigger Signals",
                "- validation",
                "",
                "## Operating Steps",
                "1. Run a safe command.",
                "",
                "## Failure Fallbacks",
                "- Stop.",
                "",
                "## Verification Suggestions",
                "- Run validation.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_validation_requires_approval(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-approval"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    (skill_dir / "validation.json").write_text('{"commands":["python --version"],"timeout_sec":30}', encoding="utf-8")

    result = run_validation(skill_dir)

    assert result["status"] == "requires_approval"
    assert result["commands"] == ["python --version"]


def test_validation_blocks_network_command(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-network"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    (skill_dir / "validation.json").write_text('{"commands":["curl https://example.com"],"network":false}', encoding="utf-8")

    result = run_validation(skill_dir, approve=True)

    assert result["status"] == "blocked"
    assert any("network" in finding for finding in result["findings"])


def test_validation_executes_after_approval(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-exec"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    (skill_dir / "validation.json").write_text('{"commands":["python --version"],"timeout_sec":30}', encoding="utf-8")

    result = run_validation(skill_dir, approve=True)

    assert result["status"] == "passed"
    assert result["approved"] is True
    assert result["results"][0]["returncode"] == 0


def test_validation_records_failure_in_evolution_memory(tmp_path, monkeypatch):
    memory_path = tmp_path / "memory.json"
    monkeypatch.setattr(evolution, "MEMORY_PATH", memory_path)
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-memory"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    (skill_dir / "validation.json").write_text(
        '{"commands":["python -c \\"import sys; sys.exit(3)\\""],"timeout_sec":30}',
        encoding="utf-8",
    )

    result = run_validation(skill_dir, approve=True)
    memory = read_json(memory_path, default={})

    assert result["status"] == "failed"
    assert memory["validation_patterns"]
    assert memory["validation_patterns"][-1]["status"] == "failed"
    assert memory["validation_patterns"][-1]["returncode"] != 0
