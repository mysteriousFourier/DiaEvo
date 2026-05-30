from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import scripts.run_garden_skill_migration_evolution_experiment as experiment_module

from scripts.run_garden_skill_migration_evolution_experiment import (
    REFERENCE_SUBDIR,
    STAGES,
    TASK_PRESETS,
    _adoption_decision,
    _cache_summary_from_calls,
    _reference_repo_error,
    _prompt_leak_findings,
    _sanitize_stage_html,
    _skill_excerpt,
    _safe_html_title,
    _stage_messages,
    _stage1_feedback_summary,
    _stage_user_prompt,
    _system_prompt,
    _call_stage_llm,
    _prepare_reference_repo,
    extract_full_html,
    run_cache_first_comparison,
    run_experiment,
)


class MockLLM:
    def generate(self, *, stage, system_prompt: str, user_prompt: str) -> dict:
        html = _html(stage.stage_id)
        return {
            "status": "ok",
            "provider": "mock",
            "model": "mock-html",
            "base_url": "mock://local",
            "response_text": f"```html\n{html}\n```",
            "response_summary": stage.stage_id,
            "usage": {"total_tokens": 1},
        }


class CacheAwareMockLLM(MockLLM):
    def __init__(self):
        self.message_calls = []

    def generate_with_messages(self, *, stage, messages: list[dict], trace_system_prompt: str, trace_user_prompt: str) -> dict:
        self.message_calls.append({"stage": stage.stage_id, "messages": messages})
        html = _html(stage.stage_id)
        hit = 4096 if len(self.message_calls) > 1 else 0
        miss = 900 if hit else 1200
        return {
            "status": "ok",
            "provider": "mock",
            "model": "mock-html",
            "base_url": "mock://local",
            "response_text": f"```html\n{html}\n```",
            "response_summary": stage.stage_id,
            "usage": {
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
                "prompt_tokens": hit + miss,
                "total_tokens": hit + miss + 1,
            },
            "trace_system_prompt": trace_system_prompt,
            "trace_user_prompt": trace_user_prompt,
        }


class RetryLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, *, stage, system_prompt: str, user_prompt: str) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "status": "ok",
                "provider": "mock",
                "model": "mock-html",
                "base_url": "mock://local",
                "response_text": "<!doctype html><html><body>被截断",
                "response_summary": "truncated",
                "usage": {"total_tokens": 8192},
            }
        return {
            "status": "ok",
            "provider": "mock",
            "model": "mock-html",
            "base_url": "mock://local",
            "response_text": _html(stage.stage_id),
            "response_summary": "retry-ok",
            "usage": {"total_tokens": 1},
        }


def _html(stage_id: str) -> str:
    verification = ""
    evolved = ""
    if stage_id in {"stage1_migrated_skill", "stage2_local_evolved", "stage3_final_adopted"}:
        verification = "<aside>截图验收 checklist: desktop mobile overflow build verification</aside>"
    if stage_id in {"stage2_local_evolved", "stage3_final_adopted"}:
        evolved = "<section>本地进化补充：长工单号 overflow-wrap，非专业客服主管可读，避免一站式模板话术。</section>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>temporary title</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2937; background: #f7f7f2; }}
    main {{ max-width: 1180px; margin: auto; padding: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    table {{ width: 100%; table-layout: fixed; border-collapse: collapse; }}
    td, th {{ border-bottom: 1px solid #ddd; padding: 8px; overflow-wrap: anywhere; }}
    @media (max-width: 720px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
<main>
  <h1>物流工单早班运营台</h1>
  <section class="grid">
    <div>急单 18</div><div>负责人 7 人</div><div>快超时 SLA 9 单</div><div>整体忙不忙 队列压力高</div>
  </section>
  <table><thead><tr><th>工单队列</th><th>负责人</th><th>SLA</th><th>处理动作</th></tr></thead>
  <tbody><tr><td>WX-2026-0001</td><td>李敏</td><td>快超时</td><td>升级处理</td></tr></tbody></table>
  <section>移动端需要查看急单、负责人、快超时、整体忙不忙和下一步跟进。</section>
  {verification}
  {evolved}
</main>
</body>
</html>"""


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _make_reference_repo(tmp_path: Path) -> tuple[Path, str]:
    if not shutil.which("git"):
        pytest.skip("git is required for local reference repo smoke test")
    repo = tmp_path / "garden-skills"
    source = repo / REFERENCE_SUBDIR
    (source / "references").mkdir(parents=True)
    (source / "README.md").write_text(
        "# Web Design Engineer\n\nReact Vite visual design workflow with anti-template critique and screenshots.",
        encoding="utf-8",
    )
    (source / "README.zh-CN.md").write_text("# Web Design Engineer\n\n中文前端设计工作流。", encoding="utf-8")
    (source / "manifest.json").write_text(
        """
{
  "name": "web-design-engineer",
  "version": "1.0.0"
}
""".strip(),
        encoding="utf-8",
    )
    (source / "SKILL.md").write_text(
        """---
name: web-design-engineer
description: Garden frontend design skill.
---

# Web Design Engineer

Prefer dense usable product screens, anti-template critique, responsive overflow checks, and screenshot verification.
""",
        encoding="utf-8",
    )
    (source / "references" / "responsive.md").write_text("Use overflow-wrap and mobile screenshots.", encoding="utf-8")
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "tests@example.invalid")
    _run_git(repo, "config", "user.name", "DiaEvo Tests")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "seed web design engineer skill")
    return repo, _run_git(repo, "rev-parse", "HEAD")


def test_extract_full_html_from_fenced_and_bare():
    fenced = "before\n```html\n<!doctype html><html><head><title>x</title></head><body>ok</body></html>\n```"
    bare = "noise <!doctype html><html><body>ok</body></html> tail"

    assert extract_full_html(fenced).startswith("<!doctype html>")
    assert extract_full_html(bare) == "<!doctype html><html><body>ok</body></html>"
    assert extract_full_html("no html") is None


def test_dry_run_plans_only_four_top_level_dirs(tmp_path):
    report = run_experiment(root=tmp_path / "experiment", dry_run=True, reference_repo_dir=tmp_path / "garden-skills")

    assert report["status"] == "dry_run"
    assert report["output_plan"]["top_level_dirs"] == ["frontend_html", "traces", "reports", "skills"]
    assert report["output_plan"]["stage_files"] == [stage.filename for stage in STAGES]
    assert report["reference_repo_check"]["source_subdir"] == "skills/web-design-engineer"
    assert "fixture_check" not in report
    assert not (tmp_path / "experiment" / "frontend_html").exists()


def test_prompt_leak_title_sanitization():
    prompt = "给客服主管做一个每天早上看物流工单的页面，一眼知道急单、负责人、快超时和整体忙不忙。"
    html = f"<!doctype html><html><head><title>{prompt}</title></head><body><h1>{prompt}</h1></body></html>"

    sanitized = _sanitize_stage_html(html, STAGES[0])

    assert "物流工单看板基线版" in sanitized
    assert prompt not in sanitized.split("</title>", 1)[0]
    assert "first_view_contains_full_user_prompt" in _prompt_leak_findings(html, prompt)


def test_safe_title_uses_current_task():
    previous = experiment_module.TASK
    experiment_module.TASK = TASK_PRESETS["photography_portfolio"]
    try:
        assert _safe_html_title(STAGES[2]) == "摄影作品展进化版"
        html = "<!doctype html><html><head><title>bad</title></head><body></body></html>"
        assert "摄影作品展进化版" in _sanitize_stage_html(html, STAGES[2])
    finally:
        experiment_module.TASK = previous


def test_adoption_requires_stage2_improvement_and_verifier_pass():
    scores = {
        "stage1_migrated_skill": {"aggregate": 8.0},
        "stage2_local_evolved": {"aggregate": 7.9},
    }

    decision = _adoption_decision(
        stage_scores=scores,
        verify_migrated={"passed": True},
        verify_local_evolved={"passed": True},
    )

    assert decision["status"] == "not_adopted"


def test_missing_llm_config_blocks_without_fake_html(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=sk-your-real-deepseek-api-key\n", encoding="utf-8")

    report = run_experiment(root=tmp_path / "experiment", with_llm=True, env_path=str(env_path), reference_repo_dir=tmp_path / "garden-skills")

    assert report["status"] == "blocked_missing_llm_config"
    assert not (tmp_path / "experiment" / "frontend_html").exists()


def test_wrong_source_guard_in_stage_prompt():
    prompt = _system_prompt(STAGES[1])

    assert "skills/web-design-engineer" in prompt
    assert "web-design-website" not in prompt


def test_stage_prompt_has_deepseek_verifiable_responsive_constraints():
    prompt = _stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief")

    assert "整体忙不忙" in prompt
    assert "队列压力" in prompt
    assert "table-layout: fixed" in prompt
    assert "minmax(0, 1fr)" in prompt
    assert "overflow-wrap" in prompt
    assert "@media" in prompt


def test_cache_first_messages_keep_skill_before_dynamic_payload():
    messages = _stage_messages(
        STAGES[2],
        system_prompt=_system_prompt(STAGES[2]),
        user_prompt=_stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief\n关键短合同"),
        skill_text="## DeepSeek Execution Brief\n关键短合同",
        feedback=[{"label": "weak_verification", "note": "需要验收"}],
        prompt_strategy="cache_first",
    )

    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert "cache-first" in messages[0]["content"]
    assert STAGES[2].stage_id not in messages[0]["content"]
    assert "weak_verification" in messages[-1]["content"]
    assert "skill_执行摘要" in messages[-1]["content"]
    assert "关键短合同" in messages[-1]["content"]
    assert "skill_摘录" not in messages[-1]["content"]


def test_cache_summary_from_calls_calculates_ratios():
    summary = _cache_summary_from_calls(
        [
            {
                "stage": "stage0_baseline",
                "model": {"usage": {"prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 300, "prompt_tokens": 400}},
            },
            {
                "stage": "stage1_migrated_skill",
                "model": {"usage": {"prompt_cache_hit_tokens": 700, "prompt_cache_miss_tokens": 300, "prompt_tokens": 1000}},
            },
        ]
    )

    assert summary["hit_tokens"] == 800
    assert summary["miss_tokens"] == 600
    assert summary["hit_ratio"] == 0.571429
    assert summary["by_stage"]["stage1_migrated_skill"]["hit_ratio"] == 0.7


def test_custom_photography_task_prompt_is_not_dashboard_biased(tmp_path):
    task = TASK_PRESETS["photography_portfolio"]

    report = run_experiment(root=tmp_path / "experiment", dry_run=True, task=task, reference_repo_dir=tmp_path / "garden-skills")
    previous = experiment_module.TASK
    experiment_module.TASK = task
    try:
        prompt = _stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief")
    finally:
        experiment_module.TASK = previous

    assert report["task"]["task_id"] == "photography_portfolio_exhibition"
    assert "摄影作品展" in prompt
    assert "作品系列" in prompt
    assert "开源或明确可免费复用图片" in prompt
    assert "只生成一个运营 dashboard" not in prompt


def test_photography_prompt_uses_downloaded_local_image_assets():
    task = TASK_PRESETS["photography_portfolio"]
    image_assets = {
        "status": "ok",
        "downloaded": [
            {
                "title": "Local test photo",
                "src": "assets/photos/local_test.jpg",
                "source": "https://example.test/photo",
                "author": "Example Author",
                "license": "CC BY 4.0",
                "license_family": "creative_commons",
            }
        ],
    }
    previous = experiment_module.TASK
    experiment_module.TASK = task
    try:
        prompt = _stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief", image_assets=image_assets)
    finally:
        experiment_module.TASK = previous

    assert "local_src" in prompt
    assert "assets/photos/local_test.jpg" in prompt
    assert "不要继续热链远程图片" in prompt


def test_photography_stage2_cache_first_has_compact_output_contract():
    task = TASK_PRESETS["photography_portfolio"]
    previous = experiment_module.TASK
    experiment_module.TASK = task
    try:
        messages = _stage_messages(
            STAGES[2],
            system_prompt=_system_prompt(STAGES[2]),
            user_prompt=_stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief"),
            skill_text="## DeepSeek Execution Brief\n摄影作品展约束",
            prompt_strategy="cache_first",
        )
    finally:
        experiment_module.TASK = previous

    dynamic_payload = messages[-1]["content"]
    assert "输出压缩合同" in dynamic_payload
    assert "不超过 220 行" in dynamic_payload
    assert "上一轮长篇摄影叙事" in dynamic_payload
    assert "写完 </html> 立即停止" in dynamic_payload


def test_photography_stage2_retry_cache_first_omits_long_skill_summary():
    task = TASK_PRESETS["photography_portfolio"]
    previous = experiment_module.TASK
    experiment_module.TASK = task
    try:
        messages = _stage_messages(
            STAGES[2],
            system_prompt=_system_prompt(STAGES[2]),
            user_prompt=_stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief"),
            skill_text="## DeepSeek Execution Brief\n摄影作品展约束",
            prompt_strategy="cache_first",
            retry=True,
        )
    finally:
        experiment_module.TASK = previous

    dynamic_payload = messages[-1]["content"]
    assert "重试压缩指令" in dynamic_payload
    assert "skill_执行摘要" not in dynamic_payload
    assert "不要写长篇单张作品解析" in dynamic_payload
    assert "必须从 <!doctype html> 开始并以 </html> 结束" in dynamic_payload


def test_logistics_stage2_cache_first_retry_uses_compact_output_contract():
    messages = _stage_messages(
        STAGES[2],
        system_prompt=_system_prompt(STAGES[2]),
        user_prompt=_stage_user_prompt(STAGES[2], skill_text="## DeepSeek Execution Brief"),
        skill_text="## DeepSeek Execution Brief\n运营看板约束",
        prompt_strategy="cache_first",
        retry=True,
    )

    dynamic_payload = messages[-1]["content"]
    assert "输出压缩合同" in dynamic_payload
    assert "不超过 180 行" in dynamic_payload
    assert "6 指标卡 + 6 行工单表" in dynamic_payload
    assert "skill_执行摘要" not in dynamic_payload
    assert "禁止长篇设计说明" in dynamic_payload
    assert "写完 </html> 立即停止" in dynamic_payload


def test_local_evolution_overlay_is_front_loaded_in_skill_excerpt():
    long_migrated = "source workflow\n" + ("x" * 9000)
    skill_text = long_migrated + "\n\n## 本地进化说明\n\n## DeepSeek Execution Brief\n\n关键短合同"

    excerpt = _skill_excerpt(skill_text, limit=7000)

    assert "local-evolution-overlay-first" in excerpt
    assert "## DeepSeek Execution Brief" in excerpt
    assert "关键短合同" in excerpt


def test_photography_retry_instruction_does_not_mention_work_orders():
    task = TASK_PRESETS["photography_portfolio"]
    previous = experiment_module.TASK
    experiment_module.TASK = task
    try:
        instruction = experiment_module._retry_user_instruction()
    finally:
        experiment_module.TASK = previous

    assert "摄影作品展" in instruction
    assert "许可证" in instruction
    assert "工单" not in instruction
    assert "负责人负载" not in instruction


def test_stage_llm_retries_when_first_html_is_truncated(tmp_path):
    client = RetryLLM()

    result, trace, score = _call_stage_llm(
        client=client,
        stage=STAGES[0],
        skill_text="",
        feedback=[],
        frontend_dir=tmp_path / "frontend_html",
        reports_dir=tmp_path / "reports",
        traces_dir=tmp_path / "traces",
    )

    assert client.calls == 2
    assert result["status"] == "ok"
    assert score["aggregate"] > 0
    assert trace["model"]["status"] == "ok"
    assert (tmp_path / "frontend_html" / "stage0_baseline.html").exists()


def test_reference_repo_resolver_clones_and_checks_out_local_git_url(tmp_path):
    repo, commit = _make_reference_repo(tmp_path)
    bare = tmp_path / "garden-skills-bare.git"
    _run_git(tmp_path, "clone", "--bare", str(repo), str(bare))
    checkout = tmp_path / "reference_repos" / "garden-skills"

    result = _prepare_reference_repo(
        repo_dir=checkout,
        reference_url=str(bare),
        commit=commit,
    )

    assert result["status"] == "checked_out"
    assert result["head"] == commit
    assert (checkout / REFERENCE_SUBDIR / "SKILL.md").exists()
    assert result["sparse_checkout"] is True


def test_reference_repo_clone_failure_blocks_without_traceback(tmp_path, monkeypatch):
    def fail_git(args, *, cwd=None):
        raise RuntimeError("模拟 clone 失败")

    monkeypatch.setattr("scripts.run_garden_skill_migration_evolution_experiment._run_git", fail_git)

    with pytest.raises(RuntimeError) as exc:
        _prepare_reference_repo(
            repo_dir=tmp_path / "reference_repos" / "garden-skills",
            reference_url="https://example.invalid/garden-skills.git",
            commit="abc123",
        )

    error = _reference_repo_error(exc.value)
    assert error["type"] == "RuntimeError"
    assert "模拟 clone 失败" in error["message"]


def test_run_experiment_blocks_when_reference_repo_unavailable(tmp_path, monkeypatch):
    def fail_prepare(**kwargs):
        raise RuntimeError("模拟 reference repo 不可用")

    monkeypatch.setattr("scripts.run_garden_skill_migration_evolution_experiment._prepare_reference_repo", fail_prepare)

    report = run_experiment(root=tmp_path / "experiment", llm_client=MockLLM())

    assert report["status"] == "blocked_reference_repo_unavailable"
    assert report["reference_repo_error"]["type"] == "RuntimeError"
    assert (tmp_path / "experiment" / "reports" / "final_status.json").exists()
    assert not (tmp_path / "experiment" / "frontend_html" / "stage0_baseline.html").exists()


def test_garden_migration_evolution_mock_llm_smoke(tmp_path):
    repo, commit = _make_reference_repo(tmp_path)

    report = run_experiment(
        root=tmp_path / "experiment",
        llm_client=MockLLM(),
        reference_repo_dir=repo,
        reference_commit=commit,
    )

    assert report["status"] == "migration_evolution_passed"
    assert report["verify"]["migrated"]["passed"]
    assert report["verify"]["local_evolved"]["passed"]
    assert report["verify"]["final_adopted"]["passed"]
    assert report["adoption_decision"]["stage2_aggregate"] > report["adoption_decision"]["stage1_aggregate"]
    assert report["stage_outputs"]["stage3_final_adopted"]["adopted_from_stage"] == "stage2_local_evolved"
    assert report["stage_scores"]["stage3_final_adopted"]["aggregate"] >= report["stage_scores"]["stage2_local_evolved"]["aggregate"]

    frontend = Path(report["artifacts"]["frontend_html"])
    assert sorted(path.name for path in frontend.glob("*.html")) == [
        "compare.html",
        "stage0_baseline.html",
        "stage1_migrated_skill.html",
        "stage2_local_evolved.html",
        "stage3_final_adopted.html",
    ]
    assert sorted(path.name for path in (tmp_path / "experiment").iterdir()) == ["frontend_html", "reports", "skills", "traces"]
    assert (tmp_path / "experiment" / "traces" / "experiment_traces.jsonl").exists()
    assert (tmp_path / "experiment" / "traces" / "llm_calls.jsonl").exists()
    assert (tmp_path / "experiment" / "traces" / "stage_feedback.jsonl").exists()
    assert (tmp_path / "experiment" / "reports" / "final_experiment_report.md").exists()
    migration = report["migration"]
    assert migration["reference_url"] == "https://github.com/ConardLi/garden-skills.git"
    assert migration["local_repo_path"] == str(repo.resolve(strict=False))
    assert migration["git_head"] == commit
    assert migration["source_subdir"] == "skills/web-design-engineer"
    assert {item["path"] for item in migration["source_file_hashes"]} >= {"SKILL.md", "manifest.json", "README.md", "README.zh-CN.md"}
    assert "fixture" not in migration
    assert report["evolution"]["bad_case_to_local_evolution"]
    assert report["evolution"]["skill_structure"]["score"] >= 0.8
    assert "verifier" in report["adoption_decision"]
    assert "本地进化补充" in (frontend / "stage3_final_adopted.html").read_text(encoding="utf-8")
    local_skill = (tmp_path / "experiment" / "skills" / "local_evolved" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Trigger Boundary" in local_skill
    assert "## DeepSeek Execution Brief" in local_skill
    assert "Reasonix" in local_skill
    assert "## Progressive Disclosure" in local_skill
    assert "## Evaluation Contract" in local_skill
    assert "## Anti-patterns" in local_skill
    assert report["evolution"]["skill_structure"]["signals"]["deepseek_execution_brief"]
    assert (tmp_path / "experiment" / "skills" / "migrated" / "SKILL.md").exists()
    assert (tmp_path / "experiment" / "skills" / "local_evolved" / "SKILL.md").exists()
    assert (tmp_path / "experiment" / "skills" / "final_adopted" / "SKILL.md").exists()


def test_cache_first_strategy_records_prompt_cache_and_messages(tmp_path):
    repo, commit = _make_reference_repo(tmp_path)
    client = CacheAwareMockLLM()

    report = run_experiment(
        root=tmp_path / "experiment",
        llm_client=client,
        reference_repo_dir=repo,
        reference_commit=commit,
        prompt_strategy="cache_first",
    )

    assert report["status"] == "migration_evolution_passed"
    assert report["prompt_strategy"] == "cache_first"
    assert report["prompt_cache"]["reported_call_count"] == 3
    assert report["prompt_cache"]["hit_ratio"] > 0
    assert len(client.message_calls) == 3
    assert all(call["messages"][0]["role"] == "system" for call in client.message_calls)
    assert "prompt_cache_summary" in report["artifacts"]
    assert (tmp_path / "experiment" / "reports" / "prompt_cache_summary.json").exists()


def test_stage1_feedback_summary_turns_score_gaps_into_feedback():
    rows = _stage1_feedback_summary(
        {
            "aggregate": 9.143,
            "business_usability": 10,
            "information_architecture": 10,
            "visual_restraint": 9,
            "anti_template": 8,
            "responsive_risk": 9,
            "verification_readiness": 9,
            "skill_compliance": 9,
        }
    )

    assert rows[0]["label"] == "stage1_score_summary"
    assert "stage2 不能只复刻或同分" in rows[0]["note"]
    assert any(row["label"] == "stage1_rubric_gap_anti_template" for row in rows)


def test_stage1_feedback_summary_option_is_recorded_in_evolution_report(tmp_path):
    repo, commit = _make_reference_repo(tmp_path)

    report = run_experiment(
        root=tmp_path / "experiment",
        llm_client=MockLLM(),
        reference_repo_dir=repo,
        reference_commit=commit,
        stage1_feedback_summary=True,
    )

    assert report["quality_experiment"]["stage1_feedback_summary"] is True
    assert report["evolution"]["stage1_bad_case_count"] == 0
    assert report["evolution"]["stage1_feedback_row_count"] > 0
    assert any(
        item["bad_case"] == "stage1_score_summary"
        for item in report["evolution"]["bad_case_to_local_evolution"]
    )


def test_cache_first_comparison_runs_legacy_and_cache_first(tmp_path):
    repo, commit = _make_reference_repo(tmp_path)
    client = CacheAwareMockLLM()

    report = run_cache_first_comparison(
        root=tmp_path / "comparison",
        llm_client=client,
        reference_repo_dir=repo,
        reference_commit=commit,
    )

    assert report["status"] == "completed"
    assert set(report["strategies"]) == {"legacy", "cache_first"}
    assert report["strategies"]["cache_first"]["prompt_cache"]["hit_ratio"] > 0
    assert (tmp_path / "comparison" / "reports" / "cache_first_comparison.json").exists()
    assert (tmp_path / "comparison" / "legacy" / "frontend_html" / "compare.html").exists()
    assert (tmp_path / "comparison" / "cache_first" / "frontend_html" / "compare.html").exists()


def test_resume_skips_existing_stage_artifacts(tmp_path):
    repo, commit = _make_reference_repo(tmp_path)
    root = tmp_path / "experiment"
    first = run_experiment(root=root, llm_client=MockLLM(), reference_repo_dir=repo, reference_commit=commit)
    assert first["status"] == "migration_evolution_passed"

    class NoCallLLM:
        def generate(self, *, stage, system_prompt: str, user_prompt: str) -> dict:
            raise AssertionError(f"unexpected LLM call for {stage.stage_id}")

    resumed = run_experiment(root=root, llm_client=NoCallLLM(), reference_repo_dir=repo, reference_commit=commit)

    assert resumed["stage_outputs"]["stage0_baseline"]["status"] == "ok"
    assert resumed["stage_outputs"]["stage1_migrated_skill"]["status"] == "ok"
    assert resumed["migration"]["source_subdir"] == "skills/web-design-engineer"
