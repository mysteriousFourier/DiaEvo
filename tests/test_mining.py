from skillminer.association_rules import mine_association_rules
from skillminer.clustering import cluster_traces
from skillminer.ingest import load_traces
from skillminer.sequence_mining import mine_frequent_sequences


def test_mining_pipeline_finds_clusters_rules_and_sequences():
    traces = load_traces("data/sample_traces.jsonl")
    _, _, clusters = cluster_traces(traces, k=4)
    rules = mine_association_rules(traces, min_support=1, min_confidence=0.1)
    sequences = mine_frequent_sequences(traces, min_support=2)
    assert clusters
    assert any(cluster.coverage_gap > 0 for cluster in clusters)
    assert any(rule["skill"] == "test-failure-repair" for rule in rules)
    assert any("pytest" in sequence["sequence"] for sequence in sequences)
