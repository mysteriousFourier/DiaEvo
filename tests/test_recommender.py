from skillminer.ingest import ingest_traces
from skillminer.miner import mine
from skillminer.recommender import recommend


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
