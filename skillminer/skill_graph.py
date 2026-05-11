from __future__ import annotations

from collections import defaultdict

from .association_rules import trace_items
from .models import PluginRecord, SkillRecord, TraceRecord


Graph = dict[str, dict[str, float]]


def add_edge(graph: Graph, left: str, right: str, weight: float = 1.0) -> None:
    if left == right:
        return
    graph[left][right] += weight
    graph[right][left] += weight


def build_skill_graph(
    traces: list[TraceRecord],
    skills: list[SkillRecord],
    plugins: list[PluginRecord] | None = None,
) -> Graph:
    graph: Graph = defaultdict(lambda: defaultdict(float))
    skill_names = {skill.name for skill in skills}
    for trace in traces:
        task_node = f"task:{trace.id}"
        for item in trace_items(trace):
            add_edge(graph, task_node, item, 0.7)
        for tool in trace.tools:
            add_edge(graph, task_node, f"tool:{tool.lower()}", 0.9)
        for skill_name in trace.used_skills:
            skill_node = f"skill:{skill_name}"
            add_edge(graph, task_node, skill_node, 1.3 if trace.success else 0.5)
            skill_names.add(skill_name)
    for skill in skills:
        skill_node = f"skill:{skill.name}"
        for tag in skill.tags:
            add_edge(graph, skill_node, f"tag:{tag.lower()}", 0.8)
        for permission in skill.permissions:
            add_edge(graph, skill_node, f"permission:{permission.lower()}", 0.35)
    for plugin in plugins or []:
        plugin_node = f"plugin:{plugin.name}"
        add_edge(graph, plugin_node, f"skill:plugin:{plugin.name}", 0.8)
        for command in plugin.commands:
            add_edge(graph, plugin_node, f"tool:{command.lower()}", 0.6)
    for name in skill_names:
        graph[f"skill:{name}"]
    return {node: dict(edges) for node, edges in graph.items()}


def personalized_pagerank(
    graph: Graph,
    seeds: dict[str, float],
    damping: float = 0.85,
    iterations: int = 40,
) -> dict[str, float]:
    nodes = sorted(graph)
    if not nodes:
        return {}
    total_seed = sum(seeds.values())
    if total_seed <= 0:
        seeds = {node: 1.0 for node in nodes}
        total_seed = len(nodes)
    personalization = {node: seeds.get(node, 0.0) / total_seed for node in nodes}
    rank = {node: 1.0 / len(nodes) for node in nodes}
    for _ in range(iterations):
        next_rank = {node: (1.0 - damping) * personalization.get(node, 0.0) for node in nodes}
        for node in nodes:
            edges = graph.get(node, {})
            total_weight = sum(edges.values())
            if total_weight <= 0:
                continue
            share = rank[node] * damping
            for neighbor, weight in edges.items():
                if neighbor in next_rank:
                    next_rank[neighbor] += share * (weight / total_weight)
        norm = sum(next_rank.values())
        if norm:
            rank = {node: score / norm for node, score in next_rank.items()}
        else:
            rank = next_rank
    return rank


def seeds_for_task(task_text: str, project_items: set[str]) -> dict[str, float]:
    seeds = {item: 1.0 for item in project_items}
    if "tag:skill" in seeds:
        seeds["tag:skill"] = 0.35
    for raw in task_text.lower().replace("/", " ").replace("\\", " ").split():
        token = raw.strip(".,:;()[]{}'\"")
        if token:
            if token in {"skill", "skills"}:
                weight = 0.2
            else:
                weight = 0.5
            seeds[f"tag:{token}"] = max(seeds.get(f"tag:{token}", 0.0), weight)
            seeds[f"tool:{token}"] = max(seeds.get(f"tool:{token}", 0.0), 0.4)
    return seeds
