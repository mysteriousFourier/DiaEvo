from pathlib import Path

import shutil

from diaevo.cli import build_parser, render_cli_result, run_learn, workspace_status
from diaevo.generator import generate_skill
from diaevo.ingest import ingest_traces
from diaevo.miner import mine
from diaevo.storage import read_json
from diaevo.validation_runner import run_validation
from diaevo.verifier import verify_skill


def test_generate_and_verify_candidate_skill():
    ingest_traces("data/sample_traces.jsonl")
    report = mine(k=4)
    cluster_id = report["generation_entrypoints"][0]["cluster_id"]
    generated = generate_skill(cluster_id)
    assert generated["status"] == "candidate"
    skill_path = Path(generated["skill_path"])
    assert skill_path.exists()
    text = skill_path.read_text(encoding="utf-8")
    assert "## Operating Steps" in text
    assert "## Failure Fallbacks" in text
    assert "## Verification Suggestions" in text
    assert "任务关键词" in text
    assert "人工审核" in text
    assert "轨迹 ID" not in text
    assert "工具复用次数" not in text
    result = verify_skill(generated["skill_dir"])
    assert result["passed"]
    assert result["risk_score"] < 0.5
    metadata = read_json(Path(generated["skill_dir"]) / "metadata.json")
    assert metadata["evidence"]["cluster_id"] == cluster_id
    assert metadata["evidence"]["trace_ids"]


def test_generate_code_backed_skill_validates_in_sandbox(tmp_path):
    ingest_traces("data/sample_traces.jsonl")
    report = mine(k=4)
    cluster_id = report["generation_entrypoints"][0]["cluster_id"]
    output_dir = Path(".tmp") / "tests" / tmp_path.name / "code-backed"
    shutil.rmtree(output_dir, ignore_errors=True)
    generated = generate_skill(cluster_id, output_dir=output_dir, with_code=True)
    skill_dir = Path(generated["skill_dir"])

    assert generated["code_backed"] is True
    assert (skill_dir / "scripts" / "skill_flow.py").exists()
    assert (skill_dir / "code_artifacts.json").exists()
    assert (skill_dir / "validation.json").exists()
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## Executable Artifacts" in text
    artifacts = read_json(skill_dir / "code_artifacts.json")
    assert artifacts["entrypoint"] == "scripts/skill_flow.py"
    assert artifacts["review_status"] == "pending"
    assert artifacts["fallback_mode"] == "skill_md"
    validation = read_json(skill_dir / "validation.json")
    assert validation["commands"] == [f"python {skill_dir.as_posix()}/scripts/skill_flow.py --describe"]

    verify_result = verify_skill(skill_dir)
    assert verify_result["passed"]
    assert any(item["code"] == "script_not_approved" for item in verify_result["findings"])

    preview = run_validation(skill_dir)
    assert preview["status"] == "requires_approval"
    validated = run_validation(skill_dir, approve=True)
    assert validated["status"] == "passed"
    assert "read_only_skill_flow" in validated["results"][0]["stdout"]
    assert Path(validated["sandbox_workspace"]).exists()
    assert (skill_dir / "scripts" / "skill_flow.py").exists()
    updated_artifacts = read_json(skill_dir / "code_artifacts.json")
    assert updated_artifacts["last_validation_status"] == "passed"
    assert updated_artifacts["last_sandbox_report_path"]


def test_generate_skips_tool_event_only_cluster(tmp_path, monkeypatch):
    import diaevo.generator as generator

    monkeypatch.setattr(generator, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(generator, "CANDIDATE_SKILLS_DIR", tmp_path / "candidate_skills")
    (tmp_path / "mining_report.json").write_text(
        """
{
  "clusters": [
    {
      "id": "C03",
      "size": 2,
      "source_counts": {"tool_event": 2},
      "representative_task": "Tool event recommend_skills #2",
      "top_terms": ["recommend_skills", "event", "feedback", "tool"],
      "top_tools": ["recommend_skills"],
      "file_extensions": []
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    generated = generate_skill("C03")

    assert generated["status"] == "skipped"
    assert generated["reason"] == "tool_event_only_cluster"
    assert "不适合生成" in generated["message"] or "工具事件日志" in generated["message"]
    assert not (tmp_path / "candidate_skills" / "C03" / "SKILL.md").exists()


def test_cli_renders_skipped_generate_as_plain_diagnostic():
    rendered = render_cli_result(
        "generate",
        {
            "status": "skipped",
            "cluster_id": "C03",
            "reason": "tool_event_only_cluster",
            "message": "该簇只包含工具事件日志。",
            "workspace": "D:\\codex\\diaevo",
            "diagnostic": {
                "representative_task": "Tool event recommend_skills #2",
                "top_tools": ["recommend_skills"],
            },
        },
    )

    assert "生成候选 skill：跳过 C03" in rendered
    assert "该簇只包含工具事件日志" in rendered
    assert "recommend_skills" in rendered
    assert "{" not in rendered


def test_cli_accepts_generate_with_code():
    args = build_parser().parse_args(["generate", "--cluster-id", "C03", "--with-code"])

    assert args.command == "generate"
    assert args.cluster_id == "C03"
    assert args.with_code is True


def test_cli_accepts_learn_and_status_commands():
    learn_args = build_parser().parse_args(["learn", "--dry-run", "--no-tool-events"])
    status_args = build_parser().parse_args(["status"])

    assert learn_args.command == "learn"
    assert learn_args.dry_run is True
    assert learn_args.no_tool_events is True
    assert status_args.command == "status"


def test_cli_accepts_self_evolve_direct_command():
    args = build_parser().parse_args(["self-evolve", "C03", "--budget", "7", "--no-validate"])

    assert args.command == "self-evolve"
    assert args.cluster_id == "C03"
    assert args.budget == 7
    assert args.no_validate is True


def test_cli_accepts_skills_commands():
    args = build_parser().parse_args(["skills", "--names", "--query", "web", "--limit", "3"])
    alias_args = build_parser().parse_args(["list-skills", "--name", "web-design-engineer"])

    assert args.command == "skills"
    assert args.names is True
    assert args.query == "web"
    assert args.limit == 3
    assert alias_args.command == "list-skills"
    assert alias_args.name == "web-design-engineer"


def test_cli_renders_skills_list_plain():
    rendered = render_cli_result(
        "skills",
        {
            "status": "ok",
            "mode": "list",
            "skill_count": 1,
            "skills": [
                {
                    "name": "web-design-engineer",
                    "description": "Build visual web artifacts.",
                    "path": "skills/web-design-engineer",
                    "source": "installed",
                    "tags": ["web"],
                }
            ],
        },
    )

    assert "现有 skills：1" in rendered
    assert "web-design-engineer" in rendered
    assert "skills/web-design-engineer" in rendered


def test_cli_renders_learn_plain_without_internal_fields():
    rendered = render_cli_result(
        "learn",
        {
            "status": "ok",
            "selected_task": {
                "title": "修复 pytest 失败",
                "solves": "修复 pytest 失败",
                "reason": "已有能力覆盖不足",
            },
            "generated": {
                "name_hint": "pytest-failure-repair",
                "skill_path": "outputs/candidate_skills/C01/SKILL.md",
            },
            "verify": {"passed": True},
            "report_path": "outputs/reports/learn_report.json",
        },
    )

    assert "做了什么" in rendered
    assert "任务名：修复 pytest 失败" in rendered
    assert "pytest-failure-repair" in rendered
    assert "C03" not in rendered
    assert "trace_ids" not in rendered
    assert "{" not in rendered


def test_learn_dry_run_selects_task_card_without_writing_skill():
    ingest_traces("data/sample_traces.jsonl", include_tool_events=False)
    report = run_learn(include_tool_events=False, dry_run=True, clusters=4)

    assert report["status"] == "preview"
    assert report["selected_task"]["title"]
    assert len(report["candidates"]) <= 3
    assert "cluster_id" in report["selected_task"]
    rendered = render_cli_result("learn", report)
    assert "任务名：" in rendered
    assert "trace_ids" not in rendered


def test_status_reports_recent_learning(monkeypatch, tmp_path):
    import diaevo.cli as cli

    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(cli, "REPORTS_DIR", reports)
    monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
    (reports / "learn_report.json").write_text(
        '{"status":"ok","generated":{"skill_path":"outputs/candidate_skills/C01/SKILL.md"}}',
        encoding="utf-8",
    )

    result = workspace_status()
    rendered = render_cli_result("status", result)

    assert result["last_learn_status"] == "ok"
    assert "当前状态" in rendered
    assert "最近生成" in rendered


def test_cli_renders_skill_names_only():
    rendered = render_cli_result(
        "skills",
        {
            "status": "ok",
            "mode": "list",
            "names_only": True,
            "skill_count": 2,
            "skills": [
                {"name": "alpha", "description": "A"},
                {"name": "beta", "description": "B"},
            ],
        },
    )

    assert rendered == "alpha\nbeta"


def test_verifier_blocks_code_artifact_forbidden_helper_capability(tmp_path):
    skill_dir = tmp_path / "bad-code-backed"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: bad-code-backed",
                "description: generated helper code safety test with enough context",
                "tags: [code, safety]",
                "source_cluster: C99",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Only for helper verifier testing.",
                "",
                "## Trigger Signals",
                "- helper",
                "",
                "## Operating Steps",
                "1. Describe the helper flow.",
                "",
                "## Failure Fallbacks",
                "- Stop.",
                "",
                "## Verification Suggestions",
                "- Verify helper constraints.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "code_artifacts.json").write_text(
        '{"schema":"diaevo.code_backed_skill.v1","entrypoint":"scripts/skill_flow.py"}',
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "skill_flow.py").write_text("import subprocess\n", encoding="utf-8")

    result = verify_skill(skill_dir)
    assert not result["passed"]
    assert any(item["code"] == "forbidden_helper_capability" for item in result["findings"])


def test_verifier_blocks_dangerous_command(tmp_path):
    skill_dir = tmp_path / "bad"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: bad",
                "description: dangerous generated skill for testing with enough context",
                "tags: [security]",
                "source_cluster: C99",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Only for verifier testing.",
                "",
                "## Trigger Signals",
                "- security",
                "",
                "## Operating Steps",
                "1. Run `rm -rf /`.",
                "",
                "## Failure Fallbacks",
                "- Stop.",
                "",
                "## Verification Suggestions",
                "- Verify the verifier blocks this.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = verify_skill(skill_dir)
    assert not result["passed"]
    assert any(item["code"] == "dangerous_command" for item in result["findings"])
