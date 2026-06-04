from __future__ import annotations

from pathlib import Path

import pytest

from diaevo.cli import build_parser
from diaevo.skill_adapter import adapt_external_skill
from diaevo.verifier import verify_skill


def _write_fixture(root: Path) -> Path:
    source = root / "web-design-website"
    (source / "src" / "chapters" / "01-opening").mkdir(parents=True)
    (source / "src" / "design").mkdir(parents=True)
    (source / "README.md").write_text(
        "# Web Design Website\n\nA React Vite visual website explaining web design engineer workflow, anti-AI visual critique, screenshots, and verification.",
        encoding="utf-8",
    )
    (source / "package.json").write_text(
        """
{
  "name": "web-design-website",
  "version": "1.0.0",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build"
  },
  "dependencies": {
    "@vitejs/plugin-react": "latest",
    "vite": "latest",
    "react": "latest"
  },
  "devDependencies": {
    "typescript": "latest"
  }
}
""".strip(),
        encoding="utf-8",
    )
    (source / "src" / "chapters" / "01-opening" / "index.tsx").write_text(
        "export function Opening() { return <section className='opening'>Anti AI web design workflow</section>; }",
        encoding="utf-8",
    )
    (source / "src" / "design" / "tokens.css").write_text(
        ":root { --color-surface: oklch(98% 0.01 250); --space: 16px; }",
        encoding="utf-8",
    )
    return source


def _write_skill_package_fixture(root: Path) -> Path:
    source = root / "web-design-engineer"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: web-design-engineer",
                "description: Build high-quality visual Web artifacts using HTML/CSS/JavaScript/React.",
                "tags: [web, design, frontend]",
                "---",
                "",
                "# Web Design Engineer",
                "",
                "## Workflow",
                "",
                "### Step 1: Understand Requirements",
                "Ask only when task intent is genuinely unclear.",
                "",
                "### Step 6: Verification",
                "Walk through the Pre-delivery Checklist before delivery.",
                "",
                "## Design Principles",
                "",
                "Avoid AI-style visual cliches and preserve responsive behavior.",
                "",
                "## Pre-delivery Checklist",
                "",
                "- Renders correctly on mobile and desktop.",
                "- No text overflow.",
                "",
                "## References Routing",
                "",
                "- Critique mode uses `references/critique-guide.md`.",
                "- Patterns use `references/advanced-patterns.md`.",
            ]
        ),
        encoding="utf-8",
    )
    (source / "references" / "advanced-patterns.md").write_text(
        "# Advanced Patterns\n\nResponsive slide engine and data visualization templates.",
        encoding="utf-8",
    )
    (source / "references" / "critique-guide.md").write_text(
        "# Critique Guide\n\nDetailed scoring rubric for design critique.",
        encoding="utf-8",
    )
    (source / "references" / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (source / "references" / "helper.py").write_text("print('do not copy')", encoding="utf-8")
    return source


def test_adapt_external_skill_from_local_vite_fixture(tmp_path):
    source = _write_fixture(tmp_path)
    output = tmp_path / "candidate"

    result = adapt_external_skill(source=source, output_dir=output, offline=True)

    assert result["status"] == "ok"
    assert result["verify_result"]["passed"]
    assert Path(result["output"]["skill_path"]).exists()
    text = (output / "SKILL.md").read_text(encoding="utf-8")
    assert "## 迁移证据" in text
    assert "web-design-website" in text
    assert "React" in text or "react" in text
    assert "## 安全约束" in text
    assert (output / "metadata.json").exists()
    assert (output / "adaptation_report.json").exists()

    verify_result = verify_skill(output)
    assert verify_result["passed"]
    assert result["adaptation_summary"]["mode"] == "project_summary"


def test_adapt_external_skill_records_source_package_without_copying_body_or_references(tmp_path):
    source = _write_skill_package_fixture(tmp_path)
    output = tmp_path / "candidate"

    result = adapt_external_skill(source=source, output_dir=output, offline=True)

    assert result["status"] == "ok"
    assert result["adaptation_summary"]["mode"] == "skill_package"
    assert result["verify_result"]["passed"]
    text = (output / "SKILL.md").read_text(encoding="utf-8")
    assert "## Preserved Source Skill" not in text
    assert "## Workflow" not in text
    assert "## Pre-delivery Checklist" not in text
    assert "Ask only when task intent is genuinely unclear" not in text
    assert "External Reference Metadata" in text
    assert not (output / "references" / "advanced-patterns.md").exists()
    assert not (output / "references" / "critique-guide.md").exists()
    assert not (output / "references" / ".env").exists()
    assert not (output / "references" / "helper.py").exists()
    manifest = (output / "migration_manifest.json").read_text(encoding="utf-8")
    assert "provenance_only_no_external_skill_body_or_references" in manifest
    assert "advanced-patterns.md" in manifest
    assert '"copied_references": []' in manifest
    assert "sensitive_path" in manifest
    assert "unsupported_reference_extension" in manifest


def test_adapt_external_skill_project_summary_mode_ignores_source_skill_md(tmp_path):
    source = _write_skill_package_fixture(tmp_path)
    output = tmp_path / "candidate"

    result = adapt_external_skill(source=source, output_dir=output, offline=True, mode="project-summary")

    assert result["status"] == "ok"
    assert result["adaptation_summary"]["mode"] == "project_summary"
    assert not (output / "migration_manifest.json").exists()
    text = (output / "SKILL.md").read_text(encoding="utf-8")
    assert "## Preserved Source Skill" not in text


def test_adapt_external_skill_dry_run_does_not_write_candidate(tmp_path):
    source = _write_fixture(tmp_path)
    output = tmp_path / "candidate"

    result = adapt_external_skill(source=source, output_dir=output, offline=True, dry_run=True)

    assert result["status"] == "dry_run"
    assert result["preview"]["markdown_chars"] > 1000
    assert result["preview"]["mode"] == "project_summary"
    assert not (output / "SKILL.md").exists()


def test_adapt_external_skill_skill_package_mode_requires_skill_md(tmp_path):
    source = _write_fixture(tmp_path)

    with pytest.raises(FileNotFoundError):
        adapt_external_skill(source=source, output_dir=tmp_path / "candidate", offline=True, mode="skill-package")


def test_adapt_external_skill_offline_missing_fixture_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("diaevo.skill_adapter._fixture_cache_dir", lambda _fixture, _commit=None: tmp_path / "missing")

    with pytest.raises(FileNotFoundError):
        adapt_external_skill(fixture="garden-web-design-website", offline=True, output_dir=tmp_path / "candidate")


def test_cli_accepts_adapt_skill_fixture():
    args = build_parser().parse_args(
        [
            "adapt-skill",
            "--fixture",
            "garden-web-design-website",
            "--offline",
            "--output-dir",
            "outputs/candidate_skills/garden-web-design-website",
            "--mode",
            "skill-package",
        ]
    )

    assert args.command == "adapt-skill"
    assert args.fixture == "garden-web-design-website"
    assert args.offline is True
    assert args.mode == "skill-package"
