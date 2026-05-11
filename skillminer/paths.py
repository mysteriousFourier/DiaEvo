from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORTS_DIR = OUTPUTS_DIR / "reports"
CANDIDATE_SKILLS_DIR = OUTPUTS_DIR / "candidate_skills"


def ensure_project_dirs() -> None:
    for path in (DATA_DIR, OUTPUTS_DIR, REPORTS_DIR, CANDIDATE_SKILLS_DIR):
        path.mkdir(parents=True, exist_ok=True)
