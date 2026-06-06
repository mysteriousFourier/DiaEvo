from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


INSTALL_ROOT = Path(__file__).resolve().parents[1]


class WorkspaceModules:
    def __init__(self, workspace: Path, *module_names: str) -> None:
        self.workspace = workspace
        self.module_names = module_names
        self.original_workspace = os.environ.get("DIAEVO_WORKSPACE")

    def __enter__(self):
        os.environ["DIAEVO_WORKSPACE"] = str(self.workspace)
        import diaevo.paths as paths

        self.paths = importlib.reload(paths)
        self.modules = [importlib.reload(importlib.import_module(name)) for name in self.module_names]
        return (self.paths, *self.modules)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.original_workspace is None:
            os.environ.pop("DIAEVO_WORKSPACE", None)
        else:
            os.environ["DIAEVO_WORKSPACE"] = self.original_workspace
        import diaevo.paths as paths

        importlib.reload(paths)
        for name in self.module_names:
            importlib.reload(importlib.import_module(name))


def test_bootstrap_workspace_creates_local_dirs_and_seed_data(tmp_path):
    workspace = tmp_path / "project-a"

    with WorkspaceModules(workspace) as (paths,):
        paths.bootstrap_workspace()

        assert paths.WORKSPACE_ROOT == workspace.resolve()
        assert (workspace / ".diaevo").is_dir()
        assert (workspace / "data").is_dir()
        assert (workspace / "outputs" / "reports").is_dir()
        assert (workspace / "outputs" / "candidate_skills").is_dir()
        for name in paths.SEED_DATA_FILES:
            assert (workspace / "data" / name).exists()


def test_paths_install_root_can_come_from_shim_env(tmp_path):
    install_root = tmp_path / "diaevo-install"
    workspace = tmp_path / "selected-workspace"
    original_install_root = os.environ.get("DIAEVO_INSTALL_ROOT")
    original_workspace = os.environ.get("DIAEVO_WORKSPACE")

    try:
        os.environ["DIAEVO_INSTALL_ROOT"] = str(install_root)
        os.environ["DIAEVO_WORKSPACE"] = str(workspace)
        import diaevo.paths as paths

        reloaded = importlib.reload(paths)

        assert reloaded.INSTALL_ROOT == install_root.resolve()
        assert reloaded.WORKSPACE_ROOT == workspace.resolve()
    finally:
        if original_install_root is None:
            os.environ.pop("DIAEVO_INSTALL_ROOT", None)
        else:
            os.environ["DIAEVO_INSTALL_ROOT"] = original_install_root
        if original_workspace is None:
            os.environ.pop("DIAEVO_WORKSPACE", None)
        else:
            os.environ["DIAEVO_WORKSPACE"] = original_workspace
        import diaevo.paths as paths

        importlib.reload(paths)


def test_workspace_env_writes_to_workspace_env_file(tmp_path):
    workspace = tmp_path / "project-b"
    original_model = os.environ.get("DEEPSEEK_MODEL")

    try:
        with WorkspaceModules(workspace, "diaevo.env") as (_paths, env):
            env.write_env_value("DEEPSEEK_MODEL", "workspace-model")
            assert (workspace / ".env").read_text(encoding="utf-8") == "DEEPSEEK_MODEL=workspace-model\n"

            os.environ.pop("DEEPSEEK_MODEL", None)
            env.load_env()
            assert os.environ["DEEPSEEK_MODEL"] == "workspace-model"
    finally:
        if original_model is None:
            os.environ.pop("DEEPSEEK_MODEL", None)
        else:
            os.environ["DEEPSEEK_MODEL"] = original_model


def test_tool_layer_uses_workspace_for_default_event_log_and_paths(tmp_path):
    workspace = tmp_path / "project-c"
    workspace.mkdir()
    (workspace / "README.md").write_text("# workspace\n", encoding="utf-8")

    with WorkspaceModules(workspace, "diaevo.tool_layer") as (_paths, tool_layer):
        result = tool_layer.execute_tool("read_file", {"path": "README.md", "limit": 1})

        assert result["status"] == "ok"
        assert result["path"] == "README.md"
        assert result["event_log"] == str(workspace.resolve() / ".diaevo" / "tool_events.jsonl")
        assert (workspace / ".diaevo" / "tool_events.jsonl").exists()
        try:
            tool_layer.resolve_workspace_path("..")
        except ValueError as exc:
            assert "outside workspace" in str(exc)
        else:
            raise AssertionError("parent path escape should fail")


def test_cli_bootstraps_current_directory_as_workspace(tmp_path):
    env = os.environ.copy()
    env.pop("DIAEVO_WORKSPACE", None)
    env["PYTHONPATH"] = str(INSTALL_ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "diaevo.cli", "tools"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / ".diaevo").is_dir()
    assert (tmp_path / "data" / "skill_registry.json").exists()
    assert (tmp_path / "outputs" / "reports").is_dir()
