# 知识图谱快照 260513

本文件夹是已审核 DiaEvo 知识图谱的可读导出，只包含 active KG 中 accepted 的事实和证据。

## 摘要

- 已审核实体数量：`4`
- 已审核三元组数量：`3`
- 已审核声明数量：`0`
- 证据路径数量：`1`

## 文件说明

- `graph_visualization.html`：可直接打开并编辑节点/关系的知识图谱工作台。
- `entities.csv`：轨迹、工具、技能、消息、来源、簇、规则、序列等图节点。
- `triples.csv`：带置信度和来源的已审核 subject-predicate-object 事实。
- `claims.csv`：带置信度和来源的已审核文本声明。
- `graph_edges.csv`：适合图可视化工具读取的边表。
- `evidence_paths.md`：每条已审核事实背后的路径、URL 和摘要。
- `confidence_summary.md`：置信度分桶和来源类型统计。
- `graph_vector_index.json`：图结构向量检索索引，记录可检索 KG 文档、稀疏向量词项和索引元数据。
- `graph_vector_retrieval.md`：图结构向量检索层说明和索引样例。
- `graph_vector_demo.md`：示例查询的向量召回种子与图扩展证据子图。
