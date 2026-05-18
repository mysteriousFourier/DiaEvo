from diaevo.ingest import ingest_traces
from diaevo.miner import mine
from diaevo.recommender import recommend
from diaevo.storage import write_json


def test_recommendation_penalizes_high_risk_plugin():
    ingest_traces("data/sample_traces.jsonl")
    mine()
    result = recommend("修复 pytest 失败并生成测试修复 skill", top_k=6, project_language="python", frameworks=["pytest"])
    assert "weights" in result
    assert "coverage_gap" in result["recommendations"][0]
    names = [item["skill"] for item in result["recommendations"]]
    assert "test-failure-repair" in names[:3]
    high_risk = [item for item in result["recommendations"] if item["skill"] == "plugin:remote-shell-installer"]
    assert not high_risk or high_risk[0]["risk"] >= 0.8


def test_recommendation_reports_script_execution_metadata(tmp_path):
    registry = tmp_path / "registry.json"
    plugins = tmp_path / "plugins.json"
    write_json(plugins, [])
    write_json(
        registry,
        [
            {
                "name": "scripted-skill",
                "description": "Use an approved read-only helper script for repeated skill diagnostics.",
                "tags": ["script", "diagnostic"],
                "path": "skills/scripted-skill",
                "permissions": ["workspace-read"],
                "usage_count": 1,
                "success_count": 1,
                "failure_count": 0,
                "risk": 0.1,
                "cost": 0.1,
                "source": "generated-candidate",
                "installed": False,
                "script": {
                    "review_status": "approved",
                    "entrypoint": "scripts/skill_flow.py",
                    "fallback_mode": "skill_md",
                    "last_validation_status": "passed",
                },
            },
            {
                "name": "fallback-skill",
                "description": "Use text instructions when helper script review is still pending.",
                "tags": ["script", "fallback"],
                "path": "skills/fallback-skill",
                "permissions": ["workspace-read"],
                "usage_count": 1,
                "success_count": 1,
                "failure_count": 0,
                "risk": 0.1,
                "cost": 0.1,
                "source": "generated-candidate",
                "installed": False,
                "script": {
                    "review_status": "pending",
                    "entrypoint": "scripts/skill_flow.py",
                    "fallback_mode": "skill_md",
                    "last_validation_status": "passed",
                },
            },
        ],
    )

    result = recommend("script diagnostic fallback", registry_path=registry, plugin_path=plugins, top_k=2)
    by_name = {item["skill"]: item for item in result["recommendations"]}

    assert by_name["scripted-skill"]["script_available"] is True
    assert by_name["scripted-skill"]["execution_mode"] == "script"
    assert by_name["fallback-skill"]["script_available"] is False
    assert by_name["fallback-skill"]["execution_mode"] == "skill_md_fallback"
    assert "pending" in by_name["fallback-skill"]["fallback_reason"]
