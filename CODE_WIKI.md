# DiaEvo Code Wiki

## 1. 项目概述

**DiaEvo** 是一个从 Agent 任务轨迹（trace）中挖掘、推荐、生成、验证和演化 Agent 技能（skill）的 Python 系统。其核心理念是：通过分析 Agent 执行任务时留下的工具调用轨迹，自动发现技能覆盖缺口，生成候选技能，经静态验证和人工审核后逐步提升 Agent 的能力。

### 核心能力

| 能力 | 说明 |
|------|------|
| 轨迹摄入 | 从 JSONL 格式的任务轨迹和工具事件日志中提取结构化数据 |
| 数据挖掘 | TF-IDF 特征提取 → K-Means 聚类 → 关联规则挖掘 → 频繁序列挖掘 → 图排名 |
| 技能推荐 | 基于语义相似度、关联规则、PageRank 图排名和加权评分的混合推荐 |
| 候选生成 | 从覆盖缺口最大的聚类中自动生成 SKILL.md 候选文档 |
| 静态验证 | 对候选技能进行安全模式检测、凭据泄露检测、结构完整性检查 |
| 技能演化 | 本地多目标优化（Pareto）+ 可选 GEPA/APO 深度优化 |
| 沙盒验证 | 在 disposable sandbox 中运行验证命令，收集测试证据 |
| 知识图谱 | 构建可审核的增量知识图谱，支持 GraphRAG 式向量检索 |
| 代码演化 | Phase 7 安全代码演化研究：在沙盒中评估候选 patch |
| 人工审核 | Promotion 队列、标签系统、重写草案、脚本审核 |

---

## 2. 项目架构

### 2.1 目录结构

```
diaevo/
├── diaevo/                    # 核心 Python 包
│   ├── __init__.py            # 包入口
│   ├── cli.py                 # CLI 命令行入口（argparse）
│   ├── models.py              # 数据模型（TraceRecord, SkillRecord, PluginRecord）
│   ├── paths.py               # 路径常量和目录初始化
│   ├── env.py                 # .env 环境变量加载
│   ├── storage.py             # JSON/JSONL 读写工具
│   ├── ingest.py              # 轨迹摄入和预处理
│   ├── features.py            # TF-IDF 特征提取和余弦相似度
│   ├── clustering.py          # K-Means 聚类
│   ├── association_rules.py   # Apriori 关联规则挖掘
│   ├── sequence_mining.py     # PrefixSpan 风格频繁序列挖掘
│   ├── skill_graph.py         # 技能-任务-工具图和 Personalized PageRank
│   ├── miner.py               # 挖掘流水线（聚类+规则+序列+图+覆盖缺口）
│   ├── recommender.py         # 混合技能推荐引擎
│   ├── generator.py           # 候选技能文档生成
│   ├── verifier.py            # 候选技能静态验证器
│   ├── evolution.py           # 本地多目标技能演化
│   ├── validation_runner.py   # 沙盒验证执行器
│   ├── promotion.py           # 技能晋升队列和审核流程
│   ├── quality.py             # 候选质量评估（去重、节审查）
│   ├── evaluation.py          # 基线评估指标（P@K, MRR, Lift, 覆盖缺口）
│   ├── gepa_adapter.py        # GEPA/APO 深度优化适配器
│   ├── code_evolution.py      # Phase 7 安全代码演化
│   ├── knowledge_graph.py     # 增量知识图谱构建和 GraphRAG 检索
│   ├── tool_layer.py          # 本地工具层（文件操作、Shell、Web、KG 查询）
│   ├── deepseek_chat.py       # DeepSeek/OpenAI 兼容 API 客户端
│   ├── tool_chat.py           # 工具调用消息格式适配
│   ├── mining_snapshot.py     # 挖掘结果快照导出
│   └── script_artifacts.py    # 技能脚本审核管理
├── ui/                        # 交互式 UI 模块
│   ├── interactive_shell.py   # 交互式 Shell 主入口
│   ├── terminal_home.py       # 终端 Dashboard 首页
│   ├── prompt_bar.py          # 输入提示栏
│   ├── output_policy.py       # 输出策略（emoji 过滤等）
│   ├── progress.py            # 进度指示器
│   ├── cli_style.py           # CLI 样式常量
│   ├── theme.py               # 主题配置
│   ├── widgets.py             # UI 组件
│   ├── tool_render.py         # 工具调用结果渲染
│   └── window_title.py        # 终端窗口标题
├── data/                      # 数据目录（运行时生成）
│   ├── sample_traces.jsonl    # 示例轨迹数据
│   ├── processed_traces.jsonl # 处理后的轨迹
│   ├── skill_registry.json    # 技能注册表
│   ├── plugin_metadata.json   # 插件元数据
│   ├── candidate_skills/      # 候选技能目录
│   ├── mining_snapshots/      # 挖掘快照
│   ├── knowledge_graph/       # 知识图谱数据
│   └── reports/               # 评估报告
├── docs/
│   └── DESIGN.md              # 设计文档
├── pyproject.toml             # 项目配置
├── requirements.txt           # 依赖声明
├── .env.example               # 环境变量模板
└── diaevo.ps1                 # PowerShell 启动脚本
```

### 2.2 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLI (cli.py)                               │
│  ingest | mine | recommend | generate | verify | evolve | kg ...   │
└──────┬──────────┬──────────┬──────────┬──────────┬────────────────┘
       │          │          │          │          │
       ▼          ▼          ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│  ingest  │ │  miner   │ │recommender│ │generator │ │  knowledge   │
│          │ │          │ │          │ │          │ │   _graph     │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘
     │            │            │            │               │
     ▼            ▼            ▼            ▼               ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
│  models  │ │features  │ │skill_    │ │verifier  │ │  tool_layer  │
│          │ │clustering│ │graph     │ │          │ │              │
│          │ │assoc_    │ │assoc_    │ │          │ │  deepseek_   │
│          │ │rules     │ │rules     │ │          │ │  chat        │
│          │ │sequence  │ │sequence  │ │          │ │              │
└──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────┘
       │            │            │            │               │
       └────────────┴────────────┴────────────┘               │
                    │                                         │
                    ▼                                         ▼
             ┌──────────┐                             ┌──────────────┐
             │ storage  │                             │   ui/        │
             │ paths    │                             │ interactive  │
             │ env      │                             │ terminal_home│
             └──────────┘                             └──────────────┘
```

---

## 3. 核心数据流水线

DiaEvo 的核心数据流遵循以下阶段：

```
Trace 数据 → 摄入(ingest) → 挖掘(mine) → 推荐(recommend) → 生成(generate)
    → 验证(verify) → 演化(evolve) → 沙盒验证(validate) → 晋升(promote)
```

### 3.1 阶段 1：轨迹摄入（Ingest）

**入口**：[ingest.py](file:///d:/codex/diaevo/diaevo/ingest.py)

- `ingest_traces(input_path, output_path)` — 读取原始 JSONL 轨迹，校验字段，合并工具事件日志，输出标准化 `TraceRecord`
- `load_traces(path)` — 加载已处理的轨迹文件
- `load_skill_registry(path)` — 加载技能注册表
- `load_plugins(path)` — 加载插件元数据

### 3.2 阶段 2：数据挖掘（Mine）

**入口**：[miner.py](file:///d:/codex/diaevo/diaevo/miner.py)

- `mine(traces_path)` — 执行完整挖掘流水线：
  1. 加载轨迹 → TF-IDF 特征提取
  2. K-Means 聚类（自动或指定 K）
  3. Apriori 关联规则挖掘
  4. PrefixSpan 频繁序列挖掘
  5. 构建 skill-task-tool 图 + Personalized PageRank
  6. 识别覆盖缺口 → 生成入口点

### 3.3 阶段 3：技能推荐（Recommend）

**入口**：[recommender.py](file:///d:/codex/diaevo/diaevo/recommender.py)

- `recommend(task, top_k)` — 混合推荐：
  1. TF-IDF 语义相似度匹配
  2. 关联规则匹配
  3. PageRank 图排名
  4. 加权线性评分 / Pareto 重排序

### 3.4 阶段 4：候选生成（Generate）

**入口**：[generator.py](file:///d:/codex/diaevo/diaevo/generator.py)

- `generate_skill(cluster_id)` — 从挖掘报告的聚类信息生成 SKILL.md 候选文档
- `build_skill_markdown(cluster)` — 构建包含 frontmatter 和标准章节的 Markdown

### 3.5 阶段 5：静态验证（Verify）

**入口**：[verifier.py](file:///d:/codex/diaevo/diaevo/verifier.py)

- `verify_skill(skill_dir)` — 静态验证候选技能：
  - 危险命令模式检测（rm -rf, curl|sh 等）
  - 凭据泄露检测（api_key, password 等）
  - 自动晋升指令检测
  - 结构完整性检查（必需章节）
  - 长度限制检查
  - 与已有技能的重复度检测

### 3.6 阶段 6：技能演化（Evolve）

**入口**：[evolution.py](file:///d:/codex/diaevo/diaevo/evolution.py)

- `evolve_skill(cluster_id, budget)` — 本地多目标优化：
  - 对每个覆盖缺口聚类生成多个候选变体
  - 使用 `CandidateEval` 评分（安全性、证据对齐、重复度）
  - Pareto 非支配排序选择最优候选
  - 记录演化记忆（正确模板、错误模式、验证模式、晋升模式）

### 3.7 阶段 7：沙盒验证（Validate）

**入口**：[validation_runner.py](file:///d:/codex/diaevo/diaevo/validation_runner.py)

- `run_validation(skill_dir, approve)` — 在 disposable sandbox 中执行验证命令：
  - 创建工作区临时副本
  - 运行 `validation.json` 中定义的命令
  - 捕获 stdout/stderr/exit code/耗时/修改文件
  - 生成 diff 和验证报告

### 3.8 阶段 8：晋升审核（Promote）

**入口**：[promotion.py](file:///d:/codex/diaevo/diaevo/promotion.py)

- `queue_promotion(skill_dir)` — 将验证通过的候选加入晋升队列
- `promote(queue_id, approve)` — 人工审核后正式晋升到技能注册表
- `label_promotion(queue_id, labels)` — 附加人工审核标签
- `rewrite_promotion(queue_id, action)` — 根据标签生成 merge/specialize/reject 重写草案

---

## 4. 主要模块详解

### 4.1 models.py — 数据模型

| 类 | 说明 |
|----|------|
| `TraceRecord` | 任务轨迹记录，包含 id, task, tools, used_skills, success, outcome, frameworks, file_extensions, tags, error_type, document 等字段 |
| `SkillRecord` | 技能注册记录，包含 name, description, tags, path, permissions, usage_count, success_count, failure_count, risk, cost 等字段 |
| `PluginRecord` | 插件元数据记录，包含 name, description, commands, as_skill() 方法 |

### 4.2 features.py — 特征工程

| 函数/类 | 说明 |
|---------|------|
| `tokenize(text)` | 分词：提取字母数字和 CJK 字符 token |
| `FeatureStore` | TF-IDF 特征存储，支持 `from_documents()` 构建和 `nearest()` 查询 |
| `cosine(a, b)` | 余弦相似度计算 |

### 4.3 clustering.py — 聚类

| 函数 | 说明 |
|------|------|
| `cluster_traces(vectors, k)` | 对 TF-IDF 向量执行 K-Means 聚类，自动确定 K 值（肘部法则） |
| `explain_clusters(traces, vectors, labels)` | 为每个聚类生成解释（代表任务、top 工具、top 错误） |

### 4.4 association_rules.py — 关联规则

| 函数 | 说明 |
|------|------|
| `mine_association_rules(traces, min_support, min_confidence, max_antecedent)` | Apriori 风格关联规则挖掘，从轨迹属性推导 skill 推荐规则 |
| `match_rules(items, rules)` | 匹配给定属性集合的关联规则 |
| `trace_items(trace)` | 从 TraceRecord 提取事务项集（lang, framework, tool, tag, error, outcome） |

### 4.5 sequence_mining.py — 频繁序列

| 函数 | 说明 |
|------|------|
| `mine_frequent_sequences(traces, min_support, max_len, successful_only)` | PrefixSpan 风格子序列支持度计数 |
| `matching_sequences(task_tools, patterns)` | 匹配工具序列中的频繁子序列 |

### 4.6 skill_graph.py — 图排名

| 函数 | 说明 |
|------|------|
| `build_skill_graph(traces, skills, plugins)` | 构建 skill-task-tool-tag 无向加权图 |
| `personalized_pagerank(graph, seeds, damping, iterations)` | 个性化 PageRank 排名 |
| `seeds_for_task(task_text, project_items)` | 根据任务描述和项目属性生成 PageRank 种子 |

### 4.7 recommender.py — 推荐引擎

| 函数 | 说明 |
|------|------|
| `recommend(task, top_k, ...)` | 混合推荐主函数，融合语义相似度、关联规则、PageRank 和加权评分 |
| `_semantic_candidates(task, store, traces, top_k)` | TF-IDF 语义相似度召回 |
| `_rule_candidates(items, rules)` | 关联规则召回 |
| `_graph_candidates(seeds, graph, skills, top_k)` | PageRank 图排名召回 |
| `_weighted_score(similarity, rule_confidence, pagerank, risk, cost, usage, ...)` | 加权线性评分 |

### 4.8 generator.py — 候选生成

| 函数 | 说明 |
|------|------|
| `generate_skill(cluster_id, output_dir, with_code)` | 从挖掘报告生成候选 SKILL.md |
| `build_skill_markdown(cluster)` | 构建 Markdown 文档（frontmatter + 标准章节） |

SKILL.md 标准章节结构：
- **When To Use** — 使用时机
- **Trigger Signals** — 触发信号
- **Operating Steps** — 操作步骤
- **Failure Fallbacks** — 失败回退
- **Verification Suggestions** — 验证建议
- **Safety Constraints** — 安全约束
- **Mined Evidence** — 挖掘证据

### 4.9 verifier.py — 静态验证

| 函数/常量 | 说明 |
|-----------|------|
| `DANGEROUS_PATTERNS` | 危险命令正则列表（rm -rf, curl\|sh, chmod 777 等） |
| `CREDENTIAL_PATTERNS` | 凭据泄露正则列表（api_key, password, secret 等） |
| `verify_skill(skill_dir)` | 执行完整静态验证，返回 passed/findings/risk_score |
| `parse_frontmatter(text)` | 解析 SKILL.md 的 YAML frontmatter |

### 4.10 evolution.py — 技能演化

| 函数/类 | 说明 |
|---------|------|
| `evolve_skill(cluster_id, budget, ...)` | 本地多目标演化主函数 |
| `CandidateEval` | 候选评估结果数据类（score, passed, rejected, findings, duplicate_similarity, side_info） |
| `_score_candidate(candidate_id, candidate, cluster, known_texts)` | 综合评分（安全性 40% + 证据对齐 30% + 重复度 20% + 结构 10%） |
| `_seed_candidate(cluster, memory_matches)` | 从聚类和记忆生成种子候选 |
| `memory_summary()` | 返回演化记忆摘要 |
| `record_validation_feedback(feedback)` | 记录验证反馈到演化记忆 |

### 4.11 knowledge_graph.py — 知识图谱

| 函数/类 | 说明 |
|---------|------|
| `KGBuilder` | 知识图谱构建器，管理 entities/triples/claims/evidence |
| `build_kg_delta(...)` | 构建增量 KG 候选（从轨迹、工具事件、对话、挖掘报告） |
| `review_kg_delta(review_id, status)` | 审核 KG 候选（accepted/rejected/needs_source 等） |
| `apply_kg_delta(...)` | 将已接受的 KG 候选写入 active KG |
| `graph_vector_search(query, ...)` | GraphRAG 式向量检索（TF-IDF 或 dense embedding） |
| `answer_kg(query, ...)` | 基于 KG 的问答 |
| `kg_workbench(...)` | 打开可编辑知识图谱工作台（HTML + 本地 HTTP 服务器） |
| `export_kg_snapshot(...)` | 导出 KG 快照（CSV + Markdown + HTML 可视化） |

### 4.12 tool_layer.py — 工具层

| 工具 | 风险 | 需审批 | 说明 |
|------|------|--------|------|
| `list_files` | low | 否 | 列出工作区目录文件 |
| `read_file` | low | 否 | 读取工作区文件 |
| `write_file` | medium | 是 | 创建/覆盖工作区文件（带 diff 预览） |
| `edit_file` | medium | 是 | 替换文件中的精确字符串（带 diff 预览） |
| `delete_file` | high | 是 | 删除文件或目录 |
| `apply_patch` | medium | 是 | 应用 unified diff |
| `run_shell` | high | 是 | 执行本地 Shell 命令 |
| `web_fetch` | network | 是 | 获取 URL 内容 |
| `web_search` | network | 是 | DuckDuckGo HTML 搜索 |
| `kg_answer` | low | 否 | 从已审核 KG 回答 |

关键函数：
- `execute_tool(name, args, approve)` — 执行工具并记录事件日志
- `resolve_workspace_path(value)` — 解析并验证工作区路径安全性
- `tool_schemas()` — 返回所有工具的 OpenAI 兼容 schema

### 4.13 deepseek_chat.py — LLM 客户端

| 函数/类 | 说明 |
|---------|------|
| `DeepSeekConfig` | LLM 配置数据类（api_key, base_url, model, max_tokens, temperature, thinking） |
| `config_from_env()` | 从 .env 构建 DeepSeek 配置 |
| `chat_completion(messages, config, tools)` | OpenAI 兼容 Chat Completion API 调用 |
| `chat_once(prompt, system, config)` | 单轮对话 |
| `vision_chat_once(prompt, image_paths, system, config)` | 多模态对话（GLM-4V 视觉模型） |
| `interactive_chat(system, config)` | 交互式多轮对话 |
| `run_chat_test(...)` | CLI chat-test 命令实现 |

### 4.14 evaluation.py — 评估框架

| 函数 | 说明 |
|------|------|
| `baseline_report(...)` | 生成完整基线评估报告 |
| `evaluate_recommendations(traces, ...)` | 评估推荐质量（P@K, MRR, Lift） |
| `evaluate_coverage_gaps(mine_report)` | 评估覆盖缺口命中率 |
| `evaluate_candidates(mine_report, ...)` | 评估候选质量（验证通过率、重复率） |
| `evaluate_heldout_usefulness(...)` | 留出集有用性评估 |
| `evaluate_safety_regressions()` | 安全回归测试 |
| `deterministic_trace_split(traces, holdout_ratio)` | 确定性训练/留出分割 |

### 4.15 gepa_adapter.py — GEPA/APO 优化

| 函数 | 说明 |
|------|------|
| `evaluate_gepa(cluster_id, budget, ...)` | 单次 GEPA 优化实验 |
| `evaluate_gepa_phase4(cluster_id, budgets, ...)` | Phase 4 多条件实验矩阵 |
| `_make_gepa_evaluator(cluster, ...)` | 创建 GEPA 评估器（含 racing gate 和 sparse judge） |

实验条件矩阵：
- `local_evolved` — 本地演化基线
- `gepa_seed_only` — GEPA 无记忆
- `gepa_ctm` — GEPA + 正确模板记忆
- `gepa_epm` — GEPA + 错误模式记忆
- `gepa_ctm_epm` — GEPA + 完整记忆
- `gepa_racing` — GEPA + cheap gates racing
- `gepa_sparse_judge` — GEPA + racing + 不确定性判断

### 4.16 code_evolution.py — 代码演化

| 函数 | 说明 |
|------|------|
| `run_code_evolution(task, patch_file, ...)` | Phase 7 安全代码演化 |
| `extract_patch_paths(patch_text)` | 从 unified diff 提取文件路径 |

安全边界：
- 候选 patch 只在 disposable sandbox 中应用
- 真实工作区不被自动修改
- 受限路径保护（.git, .env, node_modules 等）
- 危险模式和凭据模式检测

### 4.17 quality.py — 质量评估

| 函数/类 | 说明 |
|---------|------|
| `SkillText` | 技能文本数据类（name, text, source, path） |
| `collect_skill_texts(...)` | 收集注册表和候选技能文本 |
| `nearest_duplicate(text, known_texts)` | 查找最近重复技能 |
| `similarity_pairs(texts)` | 计算所有候选对的相似度 |
| `duplicate_action(similarity)` | 根据相似度推荐动作（keep/specialize/merge/reject_duplicate） |
| `section_review_proposal(candidate_text, nearest_text, action)` | 生成节审查提案 |

### 4.18 mining_snapshot.py — 挖掘快照

| 函数 | 说明 |
|------|------|
| `export_mining_snapshot(...)` | 导出人类可读的挖掘发现包（CSV + Markdown + README） |

---

## 5. 依赖关系

### 5.1 外部依赖

| 依赖 | 用途 | 必需/可选 |
|------|------|-----------|
| Python >= 3.11 | 运行时 | 必需 |
| scikit-learn | K-Means 聚类、TF-IDF | 必需 |
| numpy | 数值计算 | 必需（scikit-learn 依赖） |
| sentence-transformers | KG dense embedding 检索 | 可选 |
| gepa | GEPA/APO 深度优化 | 可选 |
| litellm | GEPA 的 LLM 调用层 | 可选（gepa 依赖） |

### 5.2 模块依赖关系图

```
cli.py
 ├── ingest.py ──── models.py, storage.py, paths.py, env.py
 ├── miner.py ──── features.py, clustering.py, association_rules.py,
 │                  sequence_mining.py, skill_graph.py, ingest.py
 ├── recommender.py ──── features.py, skill_graph.py, association_rules.py,
 │                       sequence_mining.py, ingest.py
 ├── generator.py ──── miner.py, verifier.py, storage.py
 ├── verifier.py ──── features.py, quality.py, ingest.py
 ├── evolution.py ──── generator.py, verifier.py, quality.py, features.py
 ├── validation_runner.py ──── verifier.py, storage.py, paths.py
 ├── promotion.py ──── quality.py, ingest.py, storage.py
 ├── evaluation.py ──── recommender.py, miner.py, generator.py, verifier.py,
 │                       evolution.py, quality.py
 ├── gepa_adapter.py ──── evaluation.py, evolution.py, deepseek_chat.py
 ├── code_evolution.py ──── validation_runner.py, evolution.py, tool_layer.py
 ├── knowledge_graph.py ──── features.py, ingest.py, miner.py, storage.py
 ├── tool_layer.py ──── knowledge_graph.py, paths.py
 ├── deepseek_chat.py ──── env.py, ui/output_policy.py, ui/progress.py
 ├── tool_chat.py ──── tool_layer.py
 ├── mining_snapshot.py ──── ingest.py, miner.py, storage.py
 └── script_artifacts.py
```

### 5.3 核心依赖链

```
paths.py → env.py → storage.py → models.py
    → features.py → clustering.py / association_rules.py / sequence_mining.py
    → skill_graph.py → miner.py → recommender.py
    → generator.py → verifier.py → evolution.py
    → validation_runner.py → promotion.py
    → evaluation.py → gepa_adapter.py
    → knowledge_graph.py → tool_layer.py → deepseek_chat.py
```

---

## 6. CLI 命令参考

通过 `python -m diaevo <command>` 或 `diaevo <command>` 调用：

| 命令 | 说明 |
|------|------|
| `ingest` | 摄入和标准化轨迹数据 |
| `mine` | 运行聚类、关联规则、序列和图挖掘 |
| `export-mining-snapshot` | 导出人类可读挖掘快照 |
| `recommend` | 推荐技能 |
| `generate` | 从聚类生成候选 SKILL.md |
| `verify` | 静态验证候选技能 |
| `evolve` | 本地多目标技能演化 |
| `validate` | 沙盒验证候选技能 |
| `queue-promotion` | 将候选加入晋升队列 |
| `promote` | 晋升已审核候选到注册表 |
| `label-promotion` | 附加人工审核标签 |
| `rewrite-promotion` | 生成重写草案 |
| `review-script` | 审核技能脚本 |
| `kg` | 打开知识图谱工作台 |
| `answer-kg` | 从知识图谱回答问题 |
| `evaluate` | 运行基线评估 |
| `evaluate-gepa` | 运行 GEPA 优化实验 |
| `evaluate-gepa-phase4` | 运行 Phase 4 实验矩阵 |
| `evaluate-code-evolution` | 沙盒代码演化评估 |
| `tool` | 执行单个本地工具 |
| `chat-test` | DeepSeek 对话测试 |
| `demo` | 运行完整 MVP 演示 |
| `home` | 打开交互式 Dashboard |
| `tools` | 列出工具 schema |
| `feedback` | 合并工具事件日志到轨迹 |

---

## 7. 项目运行方式

### 7.1 安装

```bash
# 克隆项目
git clone <repo-url>
cd diaevo

# 安装依赖
pip install -e .

# 或安装完整可选依赖（含 sentence-transformers）
pip install -e ".[full]"
```

### 7.2 环境配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入必要配置
# DEEPSEEK_API_KEY=sk-your-key-here    # DeepSeek API 密钥（chat-test/GEPA 需要）
# DIAEVO_KG_VECTOR_BACKEND=tfidf       # KG 向量检索后端（tfidf/dense/auto）
# DIAEVO_KG_EMBEDDING_MODEL=...        # dense 后端模型名
# DIAEVO_HF_ENDPOINT=https://hf-mirror.com  # HF 镜像
```

### 7.3 快速开始

```bash
# 1. 运行完整 MVP 演示
python -m diaevo demo

# 2. 逐步执行流水线
python -m diaevo ingest --input data/sample_traces.jsonl --output data/processed_traces.jsonl
python -m diaevo mine --traces data/processed_traces.jsonl
python -m diaevo recommend --task "给项目添加测试"
python -m diaevo generate --cluster-id C01
python -m diaevo verify --skill data/candidate_skills/C01

# 3. 打开交互式 Dashboard
python -m diaevo home

# 4. 打开知识图谱工作台
python -m diaevo kg

# 5. 运行基线评估
python -m diaevo evaluate

# 6. DeepSeek 对话测试
python -m diaevo chat-test --prompt "你好"
```

### 7.4 PowerShell 脚本

项目提供 `diaevo.ps1` 作为 Windows 下的快捷启动脚本。

---

## 8. 关键设计决策

### 8.1 安全优先

- 所有候选技能在晋升前必须通过静态验证和人工审核
- 沙盒验证在 disposable 副本中执行，不修改真实工作区
- 代码演化（Phase 7）严格限制在 sandbox-only 模式
- 工具层对写操作和 Shell 命令强制审批机制
- 凭据和密钥在日志和报告中自动脱敏

### 8.2 增量知识图谱

- 采用 delta + review queue 模式：新知识先进入待审核队列
- 支持 7 种审核状态：pending, accepted, rejected, needs_source, low_confidence, conflict, stale
- GraphRAG 检索：先向量召回种子节点，再沿图关系扩展证据子图
- 支持本地 TF-IDF 和 dense embedding 两种检索后端

### 8.3 演化记忆

- 正确模板记忆（CTM）：记录验证通过的技能模板
- 错误模式记忆（EPM）：记录验证失败的错误模式
- 验证模式记忆：记录沙盒验证的产物信息
- 重复模式记忆：记录重复检测结果
- 晋升模式记忆：记录人工审核反馈

### 8.4 评估方法论

- 确定性训练/留出分割（基于 trace ID 哈希）
- 多维度指标：P@K, MRR, Recommendation Lift, 覆盖缺口命中率, 验证通过率, 重复率, 安全假阴性率
- 基线 vs 演化候选对比
- 安全回归测试用例（dangerous_rm_rf, credential_pattern, curl_pipe_shell）

---

## 9. 数据格式

### 9.1 轨迹 JSONL

```json
{
  "id": "trace-001",
  "task": "修复测试失败",
  "tools": ["read_file", "edit_file", "run_shell"],
  "used_skills": ["test-fixer"],
  "success": true,
  "outcome": "success",
  "project_language": "python",
  "frameworks": ["pytest"],
  "file_extensions": [".py"],
  "tags": ["testing"],
  "error_type": "",
  "commands": ["python -m pytest"],
  "files": ["tests/test_foo.py"]
}
```

### 9.2 技能注册表 JSON

```json
[
  {
    "name": "test-fixer",
    "description": "修复失败的测试用例",
    "tags": ["testing", "python"],
    "path": "data/skills/test-fixer",
    "permissions": ["workspace-read", "workspace-write"],
    "usage_count": 10,
    "success_count": 8,
    "failure_count": 2,
    "risk": 0.2,
    "cost": 0.3
  }
]
```

### 9.3 SKILL.md 格式

```markdown
---
name: skill-name
description: 技能描述
tags: [tag1, tag2]
source_cluster: C01
status: candidate
risk_score: 0.25
---

## When To Use
...

## Trigger Signals
...

## Operating Steps
...

## Failure Fallbacks
...

## Verification Suggestions
...

## Safety Constraints
...

## Mined Evidence
...
```

---

## 10. 参考文献映射

项目在 `evaluation.py` 中记录了与学术工作的映射关系：

| 来源 | 模式 | DiaEvo 映射 |
|------|------|-------------|
| BAGEL | 引导探索可从轨迹证据引导有用 Agent 行为 | 把 tool_events 和挖掘轨迹作为早期探索证据 |
| SkillWeaver | 自改进 Agent 从重复任务中发现和复用技能 | 聚类任务、挖掘工具路径、生成候选、度量复用 |
| CASCADE | 技能创建是累积的，应跨迭代使用结果反馈 | 在晋升前记录验证和推荐指标 |
| Trial and Error / ETO | 失败是有用的对比证据 | 在基线中包含失败热点、重试压力和安全回归 |
| GC-DPG / GraphRAG | 图约束和验证减少不受支持的生成 | 候选保持在静态验证和人工晋升审核之后 |
