from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .evolution import record_validation_feedback
from .paths import WORKSPACE_ROOT, REPORTS_DIR, ensure_project_dirs
from .storage import write_json
from .tool_layer import resolve_workspace_path, workspace_relative
from .validation_runner import (
    _check_command,
    _coerce_output,
    _create_sandbox,
    _diff_touched_files,
    _now,
    _subprocess_command,
    _truncate,
    _workspace_snapshot,
)
from .verifier import CREDENTIAL_PATTERNS, DANGEROUS_PATTERNS


CODE_EVOLUTION_REPORT_PATH = REPORTS_DIR / "code_evolution_report.json"

RESTRICTED_PATCH_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
}


def _safe_timeout(value: int | str | None) -> int:
    try:
        raw = int(value or 60)
    except (TypeError, ValueError):
        raw = 60
    return max(1, min(raw, 300))


def _normalize_commands(values: list[str] | tuple[str, ...] | None) -> list[str]:
    commands = [str(item).strip() for item in values or [] if str(item).strip()]
    return commands or ["python -m pytest -q"]


def _normalize_rel_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _allowed_prefixes(values: list[str] | tuple[str, ...] | None) -> list[str]:
    prefixes: list[str] = []
    for value in values or []:
        resolved = resolve_workspace_path(value)
        prefixes.append(workspace_relative(resolved).replace("\\", "/").strip("/"))
    return sorted({prefix for prefix in prefixes if prefix and prefix != "."})


def _patch_path_token(raw: str) -> str:
    token = raw.strip()
    if "\t" in token:
        token = token.split("\t", 1)[0]
    if token == "/dev/null":
        return ""
    if token.startswith("a/") or token.startswith("b/"):
        token = token[2:]
    return _normalize_rel_path(token)


def extract_patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            path = _patch_path_token(line[4:])
            if path:
                paths.append(path)
    return sorted(set(paths))


def _path_within_allowed(path: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    rel = _normalize_rel_path(path)
    return any(rel == prefix or rel.startswith(f"{prefix.rstrip('/')}/") for prefix in prefixes)


def _patch_path_findings(paths: list[str], allowed_prefixes: list[str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not paths:
        findings.append(
            {
                "severity": "error",
                "code": "patch_missing_file_headers",
                "message": "patch 缺少 unified diff 文件头。",
            }
        )
        return findings
    for rel_path in paths:
        pure = PurePosixPath(rel_path)
        parts = set(pure.parts)
        if pure.is_absolute() or ".." in parts or not rel_path:
            findings.append(
                {
                    "severity": "error",
                    "code": "patch_path_outside_workspace",
                    "message": f"patch 路径必须留在工作区内：{rel_path}",
                }
            )
            continue
        if parts.intersection(RESTRICTED_PATCH_PARTS) or pure.name == ".env" or pure.name.startswith(".env."):
            findings.append(
                {
                    "severity": "error",
                    "code": "patch_restricted_path",
                    "message": f"patch 不允许触碰受限路径：{rel_path}",
                }
            )
            continue
        try:
            resolve_workspace_path(rel_path)
        except Exception as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "patch_path_resolution_failed",
                    "message": f"patch 路径解析失败：{rel_path}: {exc}",
                }
            )
            continue
        if not _path_within_allowed(rel_path, allowed_prefixes):
            findings.append(
                {
                    "severity": "error",
                    "code": "patch_path_not_allowed",
                    "message": f"patch 路径不在 --allowed-path 范围内：{rel_path}",
                }
            )
    return findings


def _patch_text_findings(patch_text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, patch_text, flags=re.IGNORECASE | re.DOTALL):
            findings.append(
                {
                    "severity": "error",
                    "code": "dangerous_patch_pattern",
                    "message": f"patch 命中危险模式：{pattern}",
                }
            )
    for pattern in CREDENTIAL_PATTERNS:
        if re.search(pattern, patch_text, flags=re.IGNORECASE):
            findings.append(
                {
                    "severity": "error",
                    "code": "credential_patch_pattern",
                    "message": f"patch 可能包含凭据材料：{pattern}",
                }
            )
    return findings


def _command_findings(commands: list[str], *, network: bool) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for command in commands:
        for message in _check_command(command, network=network):
            findings.append({"severity": "error", "code": "blocked_validation_command", "message": message})
    return findings


def _strategy(task: str, paths: list[str], commands: list[str], allowed_paths: list[str]) -> dict[str, Any]:
    target_paths = paths or allowed_paths
    return {
        "summary": "Phase 7 仅生成沙盒内代码演化建议；真实工作区不会被自动修改。",
        "task": task,
        "target_paths": target_paths,
        "steps": [
            "先用只读搜索确认任务影响面和现有测试。",
            "把候选修改整理成最小 unified diff，并限制在允许路径内。",
            "在 disposable workspace 副本中应用 patch。",
            "运行确定性的验证命令并捕获 stdout、stderr、exit code、耗时、touched files 和 diff。",
            "把报告交给人工审查；只有人工显式应用后才能进入真实工作区。",
        ],
        "validation_commands": commands,
        "safety_boundary": "sandbox-only；无自动 promotion；无默认网络；无真实工作区写回。",
    }


def _run_shell_commands(commands: list[str], workspace: Path, timeout: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        started = _now()
        monotonic_started = time.monotonic()
        try:
            cmd, shell = _subprocess_command(command)
            completed = subprocess.run(
                cmd,
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=shell,
            )
            status = "passed" if completed.returncode == 0 else "failed"
            results.append(
                {
                    "command": command,
                    "status": status,
                    "returncode": completed.returncode,
                    "started_at": started,
                    "ended_at": _now(),
                    "duration_sec": round(time.monotonic() - monotonic_started, 4),
                    "stdout": _truncate(completed.stdout),
                    "stderr": _truncate(completed.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                {
                    "command": command,
                    "status": "timeout",
                    "returncode": None,
                    "started_at": started,
                    "ended_at": _now(),
                    "duration_sec": round(time.monotonic() - monotonic_started, 4),
                    "stdout": _truncate(_coerce_output(exc.stdout)),
                    "stderr": _truncate(_coerce_output(exc.stderr)),
                }
            )
    return results


def _git_apply_step(workspace: Path, patch_text: str, timeout: int) -> dict[str, Any]:
    started = _now()
    monotonic_started = time.monotonic()
    base_cmd = ["git", "apply", "--ignore-whitespace"]
    check = subprocess.run(
        [*base_cmd, "--check", "-"],
        cwd=workspace,
        input=patch_text,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check.returncode != 0:
        return {
            "command": "git apply --check",
            "status": "failed",
            "returncode": check.returncode,
            "started_at": started,
            "ended_at": _now(),
            "duration_sec": round(time.monotonic() - monotonic_started, 4),
            "stdout": _truncate(check.stdout),
            "stderr": _truncate(check.stderr),
        }
    applied = subprocess.run(
        [*base_cmd, "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=patch_text,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "command": "git apply",
        "status": "passed" if applied.returncode == 0 else "failed",
        "returncode": applied.returncode,
        "started_at": started,
        "ended_at": _now(),
        "duration_sec": round(time.monotonic() - monotonic_started, 4),
        "stdout": _truncate(applied.stdout),
        "stderr": _truncate(applied.stderr),
    }


def _sandbox_baseline_evidence(
    *,
    result: dict[str, Any],
    commands: list[str],
    timeout: int,
    report_path: Path,
) -> dict[str, Any]:
    sandbox = _create_sandbox(WORKSPACE_ROOT.resolve())
    sandbox_dir = Path(sandbox["run_dir"])
    sandbox_workspace = Path(sandbox["workspace"])
    sandbox_artifacts = Path(sandbox["artifacts"])
    before_snapshot = _workspace_snapshot(sandbox_workspace)
    command_results = _run_shell_commands(commands, sandbox_workspace, timeout)
    after_snapshot = _workspace_snapshot(sandbox_workspace)
    touched_files, diff_text = _diff_touched_files(before_snapshot, after_snapshot)
    diff_path = sandbox_artifacts / "baseline_diff.patch"
    touched_files_path = sandbox_artifacts / "baseline_touched_files.json"
    diff_path.write_text(diff_text, encoding="utf-8")
    write_json(touched_files_path, touched_files)
    status = "baseline_passed"
    if any(item.get("status") != "passed" for item in command_results):
        status = "baseline_failed"
    sandbox_report_path = sandbox_artifacts / "code_evolution_baseline_report.json"
    result.update(
        {
            "status": status,
            "baseline_collected": True,
            "sandbox_run_id": sandbox["run_id"],
            "sandbox_dir": str(sandbox_dir),
            "sandbox_workspace": str(sandbox_workspace),
            "artifacts_dir": str(sandbox_artifacts),
            "results": command_results,
            "touched_files": touched_files,
            "diff_path": str(diff_path),
            "touched_files_path": str(touched_files_path),
            "sandbox_report_path": str(sandbox_report_path),
            "patch_guidance_inputs": {
                "task": result["task"],
                "commands": commands,
                "failing_commands": [item for item in command_results if item.get("status") != "passed"],
                "touched_files": touched_files,
                "diff_path": str(diff_path),
            },
            "message": "已在 disposable sandbox 中收集 baseline 验证证据；真实工作区未修改。",
            "updated_at": _now(),
        }
    )
    write_json(sandbox_report_path, result)
    write_json(report_path, result)
    record_validation_feedback(
        {
            "status": status,
            "skill_dir": "phase7-code-evolution-baseline",
            "commands": commands,
            "results": command_results,
            "sandbox_run_id": sandbox["run_id"],
            "sandbox_dir": str(sandbox_dir),
            "sandbox_workspace": str(sandbox_workspace),
            "artifacts_dir": str(sandbox_artifacts),
            "sandbox_report_path": str(sandbox_report_path),
            "diff_path": str(diff_path),
            "touched_files_path": str(touched_files_path),
            "touched_files": touched_files,
        }
    )
    result["report_path"] = str(report_path)
    return result


def run_code_evolution(
    *,
    task: str,
    patch_file: str | Path | None = None,
    test_commands: list[str] | tuple[str, ...] | None = None,
    allowed_paths: list[str] | tuple[str, ...] | None = None,
    approve: bool = False,
    timeout_sec: int = 60,
    network: bool = False,
    output_dir: str | Path | None = None,
    collect_baseline: bool = False,
) -> dict[str, Any]:
    ensure_project_dirs()
    task_text = str(task or "").strip()
    if not task_text:
        raise ValueError("code-evolution requires --task")
    timeout = _safe_timeout(timeout_sec)
    commands = _normalize_commands(test_commands)
    allowed = _allowed_prefixes(allowed_paths)
    patch_text = ""
    patch_path = ""
    if patch_file:
        patch_source = resolve_workspace_path(patch_file, must_exist=True)
        patch_path = workspace_relative(patch_source)
        patch_text = patch_source.read_text(encoding="utf-8", errors="replace")
    patch_paths = extract_patch_paths(patch_text) if patch_text else []
    report_path = Path(output_dir) / "code_evolution_report.json" if output_dir else CODE_EVOLUTION_REPORT_PATH

    result: dict[str, Any] = {
        "phase": "phase7_safe_code_evolution_research",
        "mode": "sandbox_only",
        "task": task_text,
        "strategy": _strategy(task_text, patch_paths, commands, allowed),
        "patch_file": patch_path,
        "patch_paths": patch_paths,
        "allowed_paths": allowed,
        "commands": commands,
        "timeout_sec": timeout,
        "network": bool(network),
        "collect_baseline": bool(collect_baseline),
        "approval_required": bool(patch_text),
        "approved": False,
        "real_workspace_mutated": False,
        "safety_boundary": "候选代码修改只允许在 disposable sandbox 中应用；报告不会把变更写回真实工作区。",
    }

    if not patch_text:
        findings = _command_findings(commands, network=bool(network))
        if findings:
            result["findings"] = findings
            result["status"] = "blocked"
            result["message"] = "baseline 验证命令未通过 Phase 7 安全检查。"
            write_json(report_path, result)
            result["report_path"] = str(report_path)
            return result
        if collect_baseline:
            return _sandbox_baseline_evidence(result=result, commands=commands, timeout=timeout, report_path=report_path)
        result.update(
            {
                "status": "strategy_only",
                "message": "未提供 --patch-file；已生成自然语言 patch strategy，等待人工提供候选 diff。",
            }
        )
        write_json(report_path, result)
        result["report_path"] = str(report_path)
        return result

    findings = [
        *_patch_path_findings(patch_paths, allowed),
        *_patch_text_findings(patch_text),
        *_command_findings(commands, network=bool(network)),
    ]
    result["findings"] = findings
    if findings:
        result["status"] = "blocked"
        result["message"] = "候选 patch 或验证命令未通过 Phase 7 安全检查。"
        write_json(report_path, result)
        result["report_path"] = str(report_path)
        return result
    if not approve:
        result["status"] = "requires_approval"
        result["message"] = "确认后使用 --approve 在 disposable sandbox 中应用 patch 并运行验证。"
        write_json(report_path, result)
        result["report_path"] = str(report_path)
        return result

    sandbox = _create_sandbox(WORKSPACE_ROOT.resolve())
    sandbox_dir = Path(sandbox["run_dir"])
    sandbox_workspace = Path(sandbox["workspace"])
    sandbox_artifacts = Path(sandbox["artifacts"])
    before_snapshot = _workspace_snapshot(sandbox_workspace)

    apply_timeout = max(1, min(timeout, 60))
    apply_result = _git_apply_step(sandbox_workspace, patch_text, apply_timeout)
    command_results: list[dict[str, Any]] = []
    if apply_result["status"] == "passed":
        command_results = _run_shell_commands(commands, sandbox_workspace, timeout)

    after_snapshot = _workspace_snapshot(sandbox_workspace)
    touched_files, diff_text = _diff_touched_files(before_snapshot, after_snapshot)
    diff_path = sandbox_artifacts / "diff.patch"
    touched_files_path = sandbox_artifacts / "touched_files.json"
    diff_path.write_text(diff_text, encoding="utf-8")
    write_json(touched_files_path, touched_files)

    status = "passed"
    if apply_result["status"] != "passed" or any(item.get("status") != "passed" for item in command_results):
        status = "failed"
    sandbox_report_path = sandbox_artifacts / "code_evolution_report.json"
    result.update(
        {
            "status": status,
            "approved": True,
            "sandbox_run_id": sandbox["run_id"],
            "sandbox_dir": str(sandbox_dir),
            "sandbox_workspace": str(sandbox_workspace),
            "artifacts_dir": str(sandbox_artifacts),
            "apply_result": apply_result,
            "results": command_results,
            "touched_files": touched_files,
            "diff_path": str(diff_path),
            "touched_files_path": str(touched_files_path),
            "sandbox_report_path": str(sandbox_report_path),
            "rollback": {
                "mode": "discard_or_review_sandbox",
                "real_workspace_mutated": False,
                "note": "sandbox 保留用于人工审查；真实工作区未自动应用 patch。",
            },
            "updated_at": _now(),
        }
    )
    write_json(sandbox_report_path, result)
    write_json(report_path, result)
    record_validation_feedback(
        {
            "status": status,
            "skill_dir": "phase7-code-evolution",
            "commands": commands,
            "results": [apply_result, *command_results],
            "sandbox_run_id": sandbox["run_id"],
            "sandbox_dir": str(sandbox_dir),
            "sandbox_workspace": str(sandbox_workspace),
            "artifacts_dir": str(sandbox_artifacts),
            "sandbox_report_path": str(sandbox_report_path),
            "diff_path": str(diff_path),
            "touched_files_path": str(touched_files_path),
            "touched_files": touched_files,
        }
    )
    result["report_path"] = str(report_path)
    return result
