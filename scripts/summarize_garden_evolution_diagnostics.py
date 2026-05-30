from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STAGE1 = "stage1_migrated_skill"
STAGE2 = "stage2_local_evolved"
PROMPT_STRATEGIES = ("legacy", "cache_first")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _round_or_none(value: float | None, places: int = 3) -> float | None:
    return round(value, places) if value is not None else None


def _bad_case_count(score: Any) -> int | None:
    if not isinstance(score, dict):
        return None
    cases = score.get("bad_cases")
    if isinstance(cases, list):
        return len(cases)
    return None


def _unique_report_dirs(roots: list[Path]) -> list[Path]:
    found: dict[Path, float] = {}
    for root in roots:
        if root.name == "reports":
            marker = root / "stage_scores.json"
            if not marker.exists():
                marker = root / "final_status.json"
            if marker.exists():
                found[root.resolve(strict=False)] = marker.stat().st_mtime
        direct = root / "reports"
        marker = direct / "stage_scores.json"
        if not marker.exists():
            marker = direct / "final_status.json"
        if marker.exists():
            found[direct.resolve(strict=False)] = marker.stat().st_mtime
        if root.exists():
            for path in root.rglob("stage_scores.json"):
                if path.parent.name != "reports":
                    continue
                found[path.parent.resolve(strict=False)] = path.stat().st_mtime
            for path in root.rglob("final_status.json"):
                if path.parent.name != "reports":
                    continue
                found.setdefault(path.parent.resolve(strict=False), path.stat().st_mtime)
    return sorted(found, key=lambda item: found[item], reverse=True)


def _comparison_files(roots: list[Path]) -> list[Path]:
    found: dict[Path, float] = {}
    for root in roots:
        direct = root / "reports" / "cache_first_comparison.json"
        if direct.exists():
            found[direct.resolve(strict=False)] = direct.stat().st_mtime
        if root.name == "reports" and (root / "cache_first_comparison.json").exists():
            found[(root / "cache_first_comparison.json").resolve(strict=False)] = (
                root / "cache_first_comparison.json"
            ).stat().st_mtime
        if root.exists():
            for path in root.rglob("cache_first_comparison.json"):
                found[path.resolve(strict=False)] = path.stat().st_mtime
    return sorted(found, key=lambda item: found[item], reverse=True)


def _infer_prompt_strategy(reports_dir: Path, final_report: dict[str, Any]) -> str:
    value = final_report.get("prompt_strategy")
    if isinstance(value, str) and value:
        return value
    parent_name = reports_dir.parent.name
    if parent_name in PROMPT_STRATEGIES:
        return parent_name
    return ""


def summarize_report_dir(reports_dir: Path) -> dict[str, Any]:
    final_report = _read_json(reports_dir / "final_experiment_report.json", default=None)
    if final_report is None:
        final_report = _read_json(reports_dir / "final_status.json", default={}) or {}
    stage_scores = _read_json(reports_dir / "stage_scores.json", default=None)
    if stage_scores is None and isinstance(final_report, dict):
        stage_scores = final_report.get("stage_scores")
    stage_scores = stage_scores or {}
    prompt_cache = _read_json(reports_dir / "prompt_cache_summary.json", default=None)
    if prompt_cache is None and isinstance(final_report, dict):
        prompt_cache = final_report.get("prompt_cache")
    prompt_cache = prompt_cache or {}
    adoption = _read_json(reports_dir / "adoption_decision.json", default=None)
    if adoption is None and isinstance(final_report, dict):
        adoption = final_report.get("adoption_decision")
    adoption = adoption or {}

    stage1_score = stage_scores.get(STAGE1, {}) if isinstance(stage_scores, dict) else {}
    stage2_score = stage_scores.get(STAGE2, {}) if isinstance(stage_scores, dict) else {}
    stage1_aggregate = _as_float(stage1_score.get("aggregate") if isinstance(stage1_score, dict) else None)
    stage2_aggregate = _as_float(stage2_score.get("aggregate") if isinstance(stage2_score, dict) else None)
    stage_delta = None if stage1_aggregate is None or stage2_aggregate is None else stage2_aggregate - stage1_aggregate

    stage1_bad_cases = _bad_case_count(stage1_score)
    stage2_bad_cases = _bad_case_count(stage2_score)
    bad_case_delta = (
        None if stage1_bad_cases is None or stage2_bad_cases is None else stage2_bad_cases - stage1_bad_cases
    )
    quality_floor_passed = (
        None
        if stage_delta is None or bad_case_delta is None
        else stage_delta >= 0 and bad_case_delta <= 0
    )

    runtime = final_report.get("runtime") if isinstance(final_report.get("runtime"), dict) else {}
    duration_seconds = _as_float(runtime.get("duration_seconds")) if isinstance(runtime, dict) else None

    return {
        "experiment_root": str(reports_dir.parent),
        "reports_dir": str(reports_dir),
        "status": final_report.get("status", ""),
        "task_id": (final_report.get("task") or {}).get("task_id", "") if isinstance(final_report.get("task"), dict) else "",
        "prompt_strategy": _infer_prompt_strategy(reports_dir, final_report),
        "stage1_aggregate": _round_or_none(stage1_aggregate),
        "stage2_aggregate": _round_or_none(stage2_aggregate),
        "stage2_minus_stage1": _round_or_none(stage_delta),
        "stage1_bad_cases": stage1_bad_cases,
        "stage2_bad_cases": stage2_bad_cases,
        "bad_case_delta": bad_case_delta,
        "quality_floor_passed": quality_floor_passed,
        "adoption_status": adoption.get("status", ""),
        "adopted": str(adoption.get("status", "")).startswith("adopted"),
        "final_source": adoption.get("final_source", ""),
        "cache": {
            "hit_ratio": _as_float(prompt_cache.get("hit_ratio")) or 0.0,
            "hit_tokens": _as_int(prompt_cache.get("hit_tokens")),
            "miss_tokens": _as_int(prompt_cache.get("miss_tokens")),
            "prompt_tokens": _as_int(prompt_cache.get("prompt_tokens")),
            "llm_call_count": _as_int(prompt_cache.get("call_count")),
            "reported_call_count": _as_int(prompt_cache.get("reported_call_count")),
        },
        "runtime": {
            "duration_seconds": _round_or_none(duration_seconds),
        },
    }


def _run_by_root(runs: list[dict[str, Any]]) -> dict[Path, dict[str, Any]]:
    return {Path(run["experiment_root"]).resolve(strict=False): run for run in runs}


def _reduction_ratio(baseline: int, candidate: int) -> float | None:
    if baseline <= 0:
        return None
    return round((baseline - candidate) / baseline, 6)


def _runtime_reduction_ratio(legacy_run: dict[str, Any] | None, cache_run: dict[str, Any] | None) -> float | None:
    if not legacy_run or not cache_run:
        return None
    legacy_duration = _as_float((legacy_run.get("runtime") or {}).get("duration_seconds"))
    cache_duration = _as_float((cache_run.get("runtime") or {}).get("duration_seconds"))
    if legacy_duration is None or cache_duration is None or legacy_duration <= 0:
        return None
    return round((legacy_duration - cache_duration) / legacy_duration, 6)


def _speed_verdict(
    *,
    legacy_run: dict[str, Any] | None,
    cache_run: dict[str, Any] | None,
    miss_tokens_reduction_ratio: float | None,
    runtime_reduction_ratio: float | None,
) -> str:
    if not legacy_run or not cache_run:
        return "incomplete_comparison"
    if cache_run and cache_run.get("quality_floor_passed") is False:
        return "quality_regression"
    cache_stage2 = _as_float(cache_run.get("stage2_aggregate") if cache_run else None)
    legacy_stage2 = _as_float(legacy_run.get("stage2_aggregate") if legacy_run else None)
    cache_miss = _as_int((cache_run.get("cache") or {}).get("miss_tokens")) if cache_run else 0
    legacy_miss = _as_int((legacy_run.get("cache") or {}).get("miss_tokens")) if legacy_run else 0
    if (
        cache_stage2 is not None
        and legacy_stage2 is not None
        and cache_stage2 > legacy_stage2
        and cache_miss > legacy_miss
    ):
        return "quality_strategy_not_speed"
    if (
        (miss_tokens_reduction_ratio is not None and miss_tokens_reduction_ratio >= 0.2)
        or (runtime_reduction_ratio is not None and runtime_reduction_ratio >= 0.15)
    ) and (not cache_run or cache_run.get("quality_floor_passed") is not False):
        return "speed_candidate"
    return "no_speed_gain"


def summarize_comparison_file(path: Path, runs_by_root: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    report = _read_json(path, default={}) or {}
    comparison_root = path.parent.parent
    legacy_run = runs_by_root.get((comparison_root / "legacy").resolve(strict=False))
    cache_run = runs_by_root.get((comparison_root / "cache_first").resolve(strict=False))

    legacy_cache = (legacy_run or {}).get("cache") or {}
    cache_first_cache = (cache_run or {}).get("cache") or {}
    if legacy_run and cache_run:
        legacy_miss = _as_int(legacy_cache.get("miss_tokens"))
        cache_miss = _as_int(cache_first_cache.get("miss_tokens"))
        miss_tokens_delta: int | None = cache_miss - legacy_miss
        miss_tokens_reduction_ratio = _reduction_ratio(legacy_miss, cache_miss)
        runtime_reduction = _runtime_reduction_ratio(legacy_run, cache_run)
    else:
        miss_tokens_delta = None
        miss_tokens_reduction_ratio = None
        runtime_reduction = None
    delta = report.get("cache_first_vs_legacy") if isinstance(report.get("cache_first_vs_legacy"), dict) else {}

    return {
        "comparison_root": str(comparison_root),
        "comparison_report": str(path),
        "hit_ratio_delta": _as_float(delta.get("hit_ratio_delta")) or 0.0,
        "miss_tokens_delta": miss_tokens_delta,
        "miss_tokens_reduction_ratio": miss_tokens_reduction_ratio,
        "runtime_reduction_ratio": runtime_reduction,
        "legacy_quality_floor_passed": legacy_run.get("quality_floor_passed") if legacy_run else None,
        "cache_first_quality_floor_passed": cache_run.get("quality_floor_passed") if cache_run else None,
        "legacy_stage2_aggregate": legacy_run.get("stage2_aggregate") if legacy_run else None,
        "cache_first_stage2_aggregate": cache_run.get("stage2_aggregate") if cache_run else None,
        "speed_verdict": _speed_verdict(
            legacy_run=legacy_run,
            cache_run=cache_run,
            miss_tokens_reduction_ratio=miss_tokens_reduction_ratio,
            runtime_reduction_ratio=runtime_reduction,
        ),
    }


def build_diagnostic_summary(roots: list[str | Path], *, limit: int = 12) -> dict[str, Any]:
    root_paths = [Path(root) for root in roots] if roots else [ROOT / "experiments"]
    report_dirs = _unique_report_dirs(root_paths)
    if limit > 0:
        report_dirs = report_dirs[:limit]
    runs = [summarize_report_dir(path) for path in report_dirs]
    runs_by_root = _run_by_root(runs)

    comparison_paths = _comparison_files(root_paths)
    if limit > 0:
        comparison_paths = comparison_paths[:limit]
    comparisons = [summarize_comparison_file(path, runs_by_root) for path in comparison_paths]
    return {
        "schema": "diaevo.garden_evolution_diagnostic_summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": [str(path) for path in root_paths],
        "run_count": len(runs),
        "comparison_count": len(comparisons),
        "runs": runs,
        "comparisons": comparisons,
    }


def _display_path(path_value: str) -> str:
    path = Path(path_value)
    try:
        return path.resolve(strict=False).relative_to(ROOT.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# DiaEvo Evolution Diagnostics",
        "",
        f"- Generated: `{summary.get('generated_at', '')}`",
        f"- Runs: `{summary.get('run_count', 0)}`",
        f"- Comparisons: `{summary.get('comparison_count', 0)}`",
        "",
        "## Runs",
        "",
        "| Experiment | Strategy | Stage1 | Stage2 | Stage2-Stage1 | Bad Case Delta | Quality Floor | Adopted | Hit Ratio | Miss Tokens | LLM Calls | Runtime s |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for run in summary.get("runs", []):
        cache = run.get("cache", {}) if isinstance(run.get("cache"), dict) else {}
        runtime = run.get("runtime", {}) if isinstance(run.get("runtime"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _display_path(str(run.get("experiment_root", ""))),
                    _cell(run.get("prompt_strategy")),
                    _cell(run.get("stage1_aggregate")),
                    _cell(run.get("stage2_aggregate")),
                    _cell(run.get("stage2_minus_stage1")),
                    _cell(run.get("bad_case_delta")),
                    _cell(run.get("quality_floor_passed")),
                    _cell(run.get("adopted")),
                    _cell(cache.get("hit_ratio")),
                    _cell(cache.get("miss_tokens")),
                    _cell(cache.get("llm_call_count")),
                    _cell(runtime.get("duration_seconds")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Comparisons",
            "",
            "| Comparison | Cache Quality Floor | Miss Reduction | Runtime Reduction | Hit Ratio Delta | Miss Delta | Verdict |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for comparison in summary.get("comparisons", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _display_path(str(comparison.get("comparison_root", ""))),
                    _cell(comparison.get("cache_first_quality_floor_passed")),
                    _cell(comparison.get("miss_tokens_reduction_ratio")),
                    _cell(comparison.get("runtime_reduction_ratio")),
                    _cell(comparison.get("hit_ratio_delta")),
                    _cell(comparison.get("miss_tokens_delta")),
                    _cell(comparison.get("speed_verdict")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize DiaEvo Garden migration/evolution experiment reports.")
    parser.add_argument("roots", nargs="*", help="Experiment root(s), comparison root(s), reports dirs, or experiments/.")
    parser.add_argument("--limit", type=int, default=12, help="Maximum recent run and comparison reports to include; <=0 means all.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = build_diagnostic_summary(args.roots or [ROOT / "experiments"], limit=args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
