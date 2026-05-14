import json

from skillminer import recommender
from skillminer.recommender import recommend


def test_recommendation_reports_human_feedback_signal(tmp_path, monkeypatch):
    memory_path = tmp_path / "evolution_memory.json"
    memory_path.write_text(
        json.dumps(
            {
                "promotion_patterns": [
                    {
                        "schema": "promotion_feedback.v2",
                        "queue_id": "q1",
                        "skill_dir": "skills/test-failure-repair",
                        "labels": ["useful-after-use"],
                        "promotion_outcome": "approved",
                        "feedback_policy": {"score": 0.35, "direction": "positive"},
                        "promotion_report": {"candidate": {"name": "test-failure-repair"}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(recommender, "DATA_DIR", tmp_path)

    result = recommend(
        task="pytest failure repair",
        traces_path="data/sample_traces.jsonl",
        registry_path="data/skill_registry.json",
        plugin_path="data/plugin_metadata.json",
        top_k=3,
    )

    assert result["human_feedback_policy"]["indexed_skill_count"] >= 1
    matched = [item for item in result["recommendations"] if item["skill"] == "test-failure-repair"]
    assert matched
    assert matched[0]["human_feedback"] > 0.5
    assert "human feedback" in matched[0]["reason"]
