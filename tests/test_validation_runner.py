import json
import os
from pathlib import Path
import shutil
import sys

from skillminer import evolution
from skillminer.storage import read_json
from skillminer.validation_runner import run_validation


def _python_command(args: str) -> str:
    executable = f'"{sys.executable}"'
    if os.name == "nt":
        executable = f"& {executable}"
    return f"{executable} {args}"


def _write_validation(root: Path, commands: list[str], *, timeout_sec: int = 30, network: bool | None = None) -> None:
    payload: dict[str, object] = {"commands": commands, "timeout_sec": timeout_sec}
    if network is not None:
        payload["network"] = network
    (root / "validation.json").write_text(json.dumps(payload), encoding="utf-8")


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
    command = _python_command("--version")
    _write_validation(skill_dir, [command])

    result = run_validation(skill_dir)

    assert result["status"] == "requires_approval"
    assert result["commands"] == [command]


def test_validation_blocks_network_command(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-network"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    _write_validation(skill_dir, ["curl https://example.com"], network=False)

    result = run_validation(skill_dir, approve=True)

    assert result["status"] == "blocked"
    assert any("network" in finding for finding in result["findings"])


def test_validation_executes_after_approval(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-exec"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    _write_validation(skill_dir, [_python_command("--version")])

    result = run_validation(skill_dir, approve=True)

    assert result["status"] == "passed"
    assert result["approved"] is True
    assert result["results"][0]["returncode"] == 0
    assert result["sandbox_run_id"]
    assert Path(result["sandbox_workspace"]).exists()
    assert Path(result["sandbox_report_path"]).exists()


def test_validation_runs_in_sandbox_without_touching_real_workspace(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-sandbox"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    marker = Path("sandbox_marker.txt")
    marker.unlink(missing_ok=True)
    _write_validation(
        skill_dir,
        [
            _python_command(
                "-c \"from pathlib import Path; Path('sandbox_marker.txt').write_text('sandbox', encoding='utf-8')\""
            )
        ],
    )

    result = run_validation(skill_dir, approve=True)
    touched_paths = {item["path"] for item in result["touched_files"]}

    assert result["status"] == "passed"
    assert not marker.exists()
    assert "sandbox_marker.txt" in touched_paths
    assert Path(result["diff_path"]).exists()
    assert Path(result["touched_files_path"]).exists()
    assert (Path(result["sandbox_workspace"]) / marker).read_text(encoding="utf-8") == "sandbox"
    saved_validation = read_json(skill_dir / "validation.json", default={})
    assert saved_validation["status"] == "passed"
    assert saved_validation["sandbox_run_id"] == result["sandbox_run_id"]
    assert "results" not in saved_validation


def test_failed_validation_does_not_touch_real_workspace(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-failed-sandbox"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    marker = Path("failed_sandbox_marker.txt")
    marker.unlink(missing_ok=True)
    _write_validation(
        skill_dir,
        [
            _python_command(
                "-c \"from pathlib import Path; import sys; Path('failed_sandbox_marker.txt').write_text('sandbox', encoding='utf-8'); sys.exit(7)\""
            )
        ],
    )

    result = run_validation(skill_dir, approve=True)
    touched_paths = {item["path"] for item in result["touched_files"]}

    assert result["status"] == "failed"
    assert result["results"][0]["returncode"] == 7
    assert not marker.exists()
    assert "failed_sandbox_marker.txt" in touched_paths
    assert (Path(result["sandbox_workspace"]) / marker).exists()


def test_validation_records_failure_in_evolution_memory(tmp_path, monkeypatch):
    memory_path = tmp_path / "memory.json"
    monkeypatch.setattr(evolution, "MEMORY_PATH", memory_path)
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "validation-memory"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_skill(skill_dir)
    _write_validation(skill_dir, [_python_command("-c \"import sys; sys.exit(3)\"")])

    result = run_validation(skill_dir, approve=True)
    memory = read_json(memory_path, default={})

    assert result["status"] == "failed"
    assert memory["validation_patterns"]
    assert memory["validation_patterns"][-1]["status"] == "failed"
    assert memory["validation_patterns"][-1]["returncode"] != 0
    assert memory["validation_patterns"][-1]["artifacts"]["sandbox_run_id"] == result["sandbox_run_id"]
    assert "touched_file_count" in memory["validation_patterns"][-1]["artifacts"]
