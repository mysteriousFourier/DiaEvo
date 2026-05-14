from pathlib import Path
import shutil
import sys

from diaevo.cli import build_parser
from diaevo.code_evolution import extract_patch_paths, run_code_evolution
from diaevo.storage import read_json


def _python_command(args: str) -> str:
    executable = f'"{sys.executable}"'
    if sys.platform.startswith("win"):
        executable = f"& {executable}"
    return f"{executable} {args}"


def test_code_evolution_strategy_only_writes_report(tmp_path):
    result = run_code_evolution(
        task="为失败测试规划一个最小 patch",
        test_commands=[_python_command("--version")],
        output_dir=tmp_path,
    )

    assert result["status"] == "strategy_only"
    assert result["real_workspace_mutated"] is False
    assert result["strategy"]["validation_commands"] == [_python_command("--version")]
    saved = read_json(tmp_path / "code_evolution_report.json")
    assert saved["phase"] == "phase7_safe_code_evolution_research"


def test_code_evolution_requires_approval_for_patch(tmp_path):
    root = Path("outputs") / "candidate_skills" / "test_code_evolution" / tmp_path.name / "requires_approval"
    shutil.rmtree(root, ignore_errors=True)
    target = root / "phase7_target.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n", encoding="utf-8", newline="\n")
    patch = root / "candidate.patch"
    patch.write_text(
        "\n".join(
            [
                f"--- a/{target.as_posix()}",
                f"+++ b/{target.as_posix()}",
                "@@ -1 +1 @@",
                "-old",
                "+new",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_code_evolution(
        task="在沙盒中试改一个文本文件",
        patch_file=patch,
        test_commands=[_python_command("--version")],
        allowed_paths=[target.parent],
        output_dir=tmp_path,
    )

    assert result["status"] == "requires_approval"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_code_evolution_applies_patch_only_inside_sandbox(tmp_path):
    root = Path("outputs") / "candidate_skills" / "test_code_evolution" / tmp_path.name / "sandbox_apply"
    shutil.rmtree(root, ignore_errors=True)
    target = root / "phase7_sandbox_target.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n", encoding="utf-8", newline="\n")
    patch = root / "candidate.patch"
    patch.write_text(
        "\n".join(
            [
                f"--- a/{target.as_posix()}",
                f"+++ b/{target.as_posix()}",
                "@@ -1 +1 @@",
                "-old",
                "+new",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_code_evolution(
        task="在沙盒中验证候选 patch",
        patch_file=patch,
        test_commands=[_python_command("--version")],
        allowed_paths=[target.parent],
        approve=True,
        output_dir=tmp_path,
    )

    assert result["status"] == "passed"
    assert result["apply_result"]["status"] == "passed"
    assert result["results"][0]["returncode"] == 0
    assert result["real_workspace_mutated"] is False
    assert target.read_text(encoding="utf-8") == "old\n"
    sandbox_target = Path(result["sandbox_workspace"]) / target
    assert sandbox_target.read_text(encoding="utf-8") == "new\n"
    assert any(item["path"] == target.as_posix() for item in result["touched_files"])


def test_code_evolution_blocks_out_of_scope_patch(tmp_path):
    root = Path("outputs") / "candidate_skills" / "test_code_evolution" / tmp_path.name / "blocked"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    patch = root / "bad.patch"
    patch.write_text(
        "\n".join(
            [
                "--- a/README.md",
                "+++ b/README.md",
                "@@ -1 +1 @@",
                "-# DiaEvo",
                "+# Changed",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_code_evolution(
        task="禁止越界 patch",
        patch_file=patch,
        allowed_paths=["diaevo"],
        output_dir=tmp_path,
    )

    assert result["status"] == "blocked"
    assert any(item["code"] == "patch_path_not_allowed" for item in result["findings"])


def test_extract_patch_paths_handles_dev_null_and_prefixes():
    patch = "\n".join(["--- /dev/null", "+++ b/diaevo/new_file.py", "--- a/README.md", "+++ b/README.md"])

    assert extract_patch_paths(patch) == ["README.md", "diaevo/new_file.py"]


def test_cli_accepts_code_evolution_arguments():
    args = build_parser().parse_args(
        [
            "evaluate-code-evolution",
            "--task",
            "验证候选 patch",
            "--patch-file",
            "candidate.patch",
            "--allowed-path",
            "diaevo",
            "--test-command",
            "python -m pytest -q",
            "--approve",
        ]
    )

    assert args.command == "evaluate-code-evolution"
    assert args.patch_file == "candidate.patch"
    assert args.allowed_path == ["diaevo"]
    assert args.approve is True
