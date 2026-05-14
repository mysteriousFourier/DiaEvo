from __future__ import annotations

import shutil
import os
from pathlib import Path


INSTALL_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(os.environ.get("DIAEVO_WORKSPACE") or Path.cwd()).resolve(strict=False)
PROJECT_ROOT = WORKSPACE_ROOT
DIAEVO_DIR = WORKSPACE_ROOT / ".diaevo"
DATA_DIR = WORKSPACE_ROOT / "data"
OUTPUTS_DIR = WORKSPACE_ROOT / "outputs"
REPORTS_DIR = OUTPUTS_DIR / "reports"
CANDIDATE_SKILLS_DIR = OUTPUTS_DIR / "candidate_skills"

SEED_DATA_FILES = (
    "skill_registry.json",
    "plugin_metadata.json",
    "recommender_weights.json",
    "sample_traces.jsonl",
)


def ensure_project_dirs() -> None:
    for path in (DIAEVO_DIR, DATA_DIR, OUTPUTS_DIR, REPORTS_DIR, CANDIDATE_SKILLS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def bootstrap_workspace() -> None:
    ensure_project_dirs()
    source_data = INSTALL_ROOT / "data"
    for name in SEED_DATA_FILES:
        source = source_data / name
        target = DATA_DIR / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
