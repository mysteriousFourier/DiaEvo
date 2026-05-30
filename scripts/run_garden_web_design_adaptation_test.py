from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from diaevo.paths import CANDIDATE_SKILLS_DIR, REPORTS_DIR, ensure_project_dirs
from diaevo.skill_adapter import GARDEN_WEB_DESIGN_FIXTURE, adapt_external_skill
from diaevo.storage import write_json
from diaevo.verifier import verify_skill


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def run(*, output_dir: str | Path | None = None, offline: bool = False, refresh_cache: bool = False, with_gepa: bool = False) -> dict[str, Any]:
    ensure_project_dirs()
    target = Path(output_dir) if output_dir else CANDIDATE_SKILLS_DIR / "garden-web-design-website"
    adaptation = adapt_external_skill(
        fixture="garden-web-design-website",
        source_commit=GARDEN_WEB_DESIGN_FIXTURE["commit"],
        output_dir=target,
        offline=offline,
        refresh_cache=refresh_cache,
        with_gepa=with_gepa,
    )
    verify_result = verify_skill(target)
    report = {
        "schema": "diaevo.garden_web_design_adaptation_test.v1",
        "status": "passed" if verify_result.get("passed") else "failed",
        "fixture": GARDEN_WEB_DESIGN_FIXTURE,
        "candidate_dir": str(target),
        "adaptation": adaptation,
        "verify_result": verify_result,
        "acceptance": {
            "fixed_commit": GARDEN_WEB_DESIGN_FIXTURE["commit"],
            "source_subdir": GARDEN_WEB_DESIGN_FIXTURE["subdir"],
            "verify_passed": bool(verify_result.get("passed")),
            "llm_policy": "optional_enhancement",
            "network_policy": "fixed_cache",
        },
    }
    write_json(REPORTS_DIR / "garden_web_design_adaptation.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Garden web-design-website adaptation acceptance test.")
    parser.add_argument("--output-dir", default=None, help="Candidate output directory.")
    parser.add_argument("--offline", action="store_true", help="Use only existing fixture cache.")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh fixture cache before adapting.")
    parser.add_argument("--with-gepa", action="store_true", help="Record optional GEPA enhancement request.")
    args = parser.parse_args()
    report = run(
        output_dir=args.output_dir,
        offline=args.offline,
        refresh_cache=args.refresh_cache,
        with_gepa=args.with_gepa,
    )
    _print_json(report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

