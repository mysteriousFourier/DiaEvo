from __future__ import annotations

import sys

from .claude_style import render_home

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_reports() -> tuple[dict, dict, dict]:
    from skillminer.paths import REPORTS_DIR
    from skillminer.storage import read_json

    ingest = read_json(REPORTS_DIR / "ingest_summary.json", default={}) or {}
    mining = read_json(REPORTS_DIR / "mining_report.json", default={}) or {}
    recommendations = read_json(REPORTS_DIR / "recommendations.json", default={}) or {}
    return ingest, mining, recommendations


def render_plain() -> str:
    return render_home()


def render_rich() -> bool:
    return False


def main() -> int:
    if not render_rich():
        print(render_plain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
