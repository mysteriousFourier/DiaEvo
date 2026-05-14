# 挖掘快照 260513

本文件夹是 SkillMiner 从任务轨迹中导出的可读挖掘证据包，用于直观看到聚类、关联规则、频繁序列、覆盖缺口和图边。

## 摘要

- 轨迹来源：`D:\codex\skillminer\data\processed_traces.jsonl`
- 轨迹数量：`25`
- 特征数量：`406`
- 聚类数量：`4`
- 导出的关联规则：`30`
- 导出的频繁序列：`29`
- 覆盖缺口入口：`2`
- 图边数量：`151`

## 关键发现

1. 覆盖缺口最高的簇是 `C02`，缺口为 `0.2800`，代表任务：rich 未安装时终端首页崩溃，增加纯文本 fallback
2. 最常见工具序列是 `read`，支持度为 `22`。
3. 最强导出关联规则：`framework:argparse` -> `skill:cli-polish`，置信度 `1.0000`，提升度 `11.5000`。

## 文件说明

- `clusters.md` / `clusters.csv`：任务簇、代表任务、关键词、工具、覆盖缺口和失败率。
- `association_rules.md` / `association_rules.csv`：从轨迹挖掘出的 trace-to-skill 规则，包含支持度、置信度和提升度。
- `frequent_sequences.md` / `frequent_sequences.csv`：反复出现的工具调用子序列。
- `skill_coverage_gaps.md` / `skill_coverage_gaps.csv`：适合生成或演化候选技能的簇。
- `graph_edges.csv`：trace-skill-tool 共现图边，可用于图可视化。
- `summary.json`：机器可读快照元数据。

## 报告使用方式

可将本文件夹作为“系统确实执行了数据挖掘流程”的可见证据，而不是只展示机器可读 JSON。
Markdown 文件适合直接阅读，CSV 文件可导入 Excel 或绘图工具。
