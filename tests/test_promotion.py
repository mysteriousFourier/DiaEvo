from pathlib import Path
import shutil

from diaevo import evolution
from diaevo.promotion import label_promotion, promote, queue_promotion, rewrite_promotion
from diaevo.storage import read_json


def _write_promotable_skill(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: unique-promotion-skill",
                "description: generated promotion skill with enough context for registry testing",
                "tags: [promotion, unique]",
                "source_cluster: C42",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Use for a uniquely named promotion workflow.",
                "",
                "## Trigger Signals",
                "- unique-promotion-signal",
                "",
                "## Operating Steps",
                "1. Review verification output.",
                "2. Queue the candidate.",
                "",
                "## Failure Fallbacks",
                "- Stop if verification fails.",
                "",
                "## Verification Suggestions",
                "- Run verifier and validation.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "validation.json").write_text('{"status":"passed","approved":true,"commands":["python --version"]}', encoding="utf-8")


def test_queue_promotion_records_ready_entry(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-ready"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)

    result = queue_promotion(skill_dir)

    assert result["status"] == "ok"
    assert result["entry"]["recommended_action"] == "ready_for_manual_promotion"
    assert result["entry"]["state"] == "queued"
    assert result["entry"]["review_labels"]
    assert result["entry"]["promotion_report"]["recommended_action"] == "ready_for_manual_promotion"


def test_promote_requires_approval_then_updates_registry(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-promote"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)
    queued = queue_promotion(skill_dir)
    registry = tmp_path / "registry.json"
    registry.write_text("[]", encoding="utf-8")

    preview = promote(queued["queue_id"], registry_path=registry)
    promoted = promote(queued["queue_id"], approve=True, registry_path=registry)

    assert preview["status"] == "requires_approval"
    assert promoted["status"] == "promoted"
    assert "accepted" in promoted["labels"]
    assert "unique-promotion-skill" in registry.read_text(encoding="utf-8")


def test_queue_promotion_records_feedback_memory(tmp_path, monkeypatch):
    memory_path = tmp_path / "memory.json"
    monkeypatch.setattr(evolution, "MEMORY_PATH", memory_path)
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-memory"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)

    result = queue_promotion(skill_dir)
    memory = read_json(memory_path, default={})

    assert result["entry"]["duplicate"]["recommended_action"] in {"keep", "specialize", "merge", "reject_duplicate"}
    assert "section_review" in result["entry"]["duplicate"]
    assert "promotion_report" in result["entry"]
    assert memory["promotion_patterns"]
    assert memory["promotion_patterns"][-1]["queue_id"] == result["queue_id"]
    assert "labels" in memory["promotion_patterns"][-1]


def test_queue_promotion_rejects_registry_duplicate(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-duplicate"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: skill-safety-review",
                "description: Review generated skills for dangerous commands, credential leakage, unauthorized paths, dependency installation, and user confirmation gates.",
                "tags: [security, verification, skill, risk]",
                "source_cluster: C42",
                "status: candidate",
                "---",
                "",
                "## When To Use",
                "Use for generated skill safety review and verification risk checks.",
                "",
                "## Trigger Signals",
                "- security",
                "- verification",
                "- skill",
                "- risk",
                "",
                "## Operating Steps",
                "1. Review generated skills for dangerous commands, credential leakage, unauthorized paths, dependency installation, and user confirmation gates.",
                "2. Check verification risk and safety findings.",
                "",
                "## Failure Fallbacks",
                "- Stop if verification fails.",
                "",
                "## Verification Suggestions",
                "- Run verifier and validation.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "validation.json").write_text('{"status":"passed","approved":true,"commands":["python --version"]}', encoding="utf-8")

    result = queue_promotion(skill_dir)

    assert result["entry"]["recommended_action"] in {"reject_duplicate", "merge", "specialize"}
    assert result["entry"]["duplicate"]["nearest"]
    assert result["entry"]["duplicate"]["section_review"]["action"] in {"reject_duplicate", "merge", "specialize"}


def test_label_promotion_records_human_label_and_blocks_promotion(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-label"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)
    queued = queue_promotion(skill_dir)
    registry = tmp_path / "registry.json"
    registry.write_text("[]", encoding="utf-8")

    labeled = label_promotion(queued["queue_id"], labels=["too-broad"], note="scope overlaps an existing skill")
    promoted = promote(queued["queue_id"], approve=True, registry_path=registry)

    assert labeled["status"] == "labeled"
    assert "too-broad" in labeled["labels"]
    assert labeled["entry"]["state"] == "rejected"
    assert promoted["status"] == "blocked"


def test_label_promotion_accepts_after_use_labels_without_blocking(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-after-use-label"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)
    queued = queue_promotion(skill_dir)
    registry = tmp_path / "registry.json"
    registry.write_text("[]", encoding="utf-8")

    labeled = label_promotion(
        queued["queue_id"],
        labels=["useful-after-use"],
        note="worked well on a later task",
    )
    promoted = promote(queued["queue_id"], approve=True, registry_path=registry)

    assert "useful-after-use" in labeled["labels"]
    assert labeled["entry"]["state"] != "rejected"
    assert promoted["status"] == "promoted"


def test_rewrite_promotion_writes_specialized_draft_without_promoting(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-rewrite"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)
    queued = queue_promotion(skill_dir)
    label_promotion(queued["queue_id"], labels=["too-broad"], note="needs narrower trigger")
    output_dir = Path(".tmp") / "tests" / tmp_path.name / "rewrite-output"

    result = rewrite_promotion(queued["queue_id"], action="specialize", output_dir=output_dir)

    rewritten = output_dir / "SKILL.md"
    assert result["status"] == "ok"
    assert result["action"] == "specialize"
    assert result["draft_written"] is True
    assert rewritten.exists()
    assert "Human Feedback Rewrite Notes" in rewritten.read_text(encoding="utf-8")
    assert "never promotes" in result["safety_boundary"]


def test_rewrite_promotion_writes_merge_proposal_for_merge_needed_label(tmp_path):
    skill_dir = Path(".tmp") / "tests" / tmp_path.name / "promotion-merge-rewrite"
    shutil.rmtree(skill_dir.parent, ignore_errors=True)
    _write_promotable_skill(skill_dir)
    queued = queue_promotion(skill_dir)
    label_promotion(queued["queue_id"], labels=["merge-needed"], note="merge useful sections")
    output_dir = Path(".tmp") / "tests" / tmp_path.name / "merge-output"

    result = rewrite_promotion(queued["queue_id"], output_dir=output_dir)

    assert result["status"] == "ok"
    assert result["action"] == "merge"
    assert (output_dir / "MERGE_PROPOSAL.md").exists()
