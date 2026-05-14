# SkillMiner: 面向自进化 Agent 的 Skill 挖掘、推荐与安全演化系统

## 摘要

SkillMiner 是一个面向编码 Agent 的本地 CLI 工作台，目标是让 Agent 从真实使用轨迹中挖掘可复用工作流，推荐已有 skill，生成候选 `SKILL.md`，并通过验证、评估和人工 promotion 形成保守的自进化闭环。项目与数据挖掘课程内容直接相关：系统使用任务聚类、关联规则、频繁序列挖掘、异构图排序、推荐评估和多目标优化等方法，把 Agent 的任务轨迹转化为可复用的技能资产。

当前原型已经实现从 trace ingest、mining、recommendation、generation、local evolution、verification、validation、promotion 到 evaluation 的完整闭环。Phase 2 质量门槛在样例语料上已经满足：held-out usefulness 为 `improved`，candidate discovery 为 `improved`，recommendation status 为 `neutral`，安全 false-negative rate 保持 `0.0`。

## 团队成员及分工

> 程仕畅

## 问题定义与项目动机

现代编码 Agent 已经具备工具调用、插件加载和静态 skill 使用能力，但“如何从真实任务过程中自动发现新 skill、验证其安全性，并决定是否纳入推荐系统”仍然是一个未完全解决的问题。传统静态 skill 库依赖人工编写，难以覆盖快速变化的项目场景；完全自动化安装又会带来安全风险。

本项目将该问题建模为一个数据挖掘与安全推荐问题：

1. 从任务轨迹、工具调用序列、错误类型、项目上下文和已有 skill 使用记录中构造结构化数据。
2. 挖掘高频任务簇、成功工具序列、任务到 skill 的关联规则和覆盖缺口。
3. 对已有 skill 与候选 skill 进行排序推荐。
4. 对覆盖缺口生成候选 skill，并通过静态验证、批准式 validation、重复检测和人工 promotion 控制风险。
5. 用 held-out traces 评估候选 skill 是否真正提升可发现性和推荐效果。

项目的核心动机是把 Agent 的经验沉淀从“手工写提示词/文档”推进到“可度量、可验证、可回滚的 skill 自进化闭环”。

## 课程相关性

本项目覆盖了数据挖掘中的多个典型主题：

| 数据挖掘主题 | 在 SkillMiner 中的对应实现 |
| --- | --- |
| 文本表示与相似度 | 使用 TF-IDF 和 cosine similarity 表示任务、trace 和 skill 文档。 |
| 聚类分析 | 使用 seeded K-Means 对任务轨迹聚类，识别高频任务簇和覆盖缺口。 |
| 关联规则挖掘 | 使用 Apriori-style antecedent enumeration 挖掘项目特征、任务标签、工具与 skill 的关联。 |
| 序列模式挖掘 | 使用 PrefixSpan-style subsequence mining 发现成功任务中的常见工具调用序列。 |
| 图挖掘与排序 | 构建 task-skill-tool 异构图，并使用 Personalized PageRank 排序。 |
| 推荐系统 | 综合 similarity、rules、PageRank、usage、success rate、risk、cost 进行 Top-K 推荐。 |
| 模型评估 | 使用 Precision@K、MRR、recommendation lift、held-out split 和安全 false-negative rate。 |
| 多目标优化 | 本地 evolution 使用 verifier、安全性、证据对齐、非重复性、特异性和长度的 Pareto/metric scoring。 |

## 主要功能

SkillMiner 当前提供两类入口。

交互式入口：

```powershell
.\skillminer.ps1
```

脚本化入口：

```powershell
.\skillminer.ps1 ingest --input data/sample_traces.jsonl
.\skillminer.ps1 mine
.\skillminer.ps1 recommend --task "fix failing pytest import path" --language python --framework pytest
.\skillminer.ps1 generate --cluster-id C03
.\skillminer.ps1 evolve --cluster-id C03 --budget 50
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 validate --skill outputs/candidate_skills/C03/evolved --approve
.\skillminer.ps1 queue-promotion --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 label-promotion --queue-id <id> --label merge-needed --note "merge with nearest skill"
.\skillminer.ps1 promote --queue-id <id> --approve
.\skillminer.ps1 evaluate --variant evolved --top-k 3 --no-tool-events
```

主要功能包括：

- 本地终端工作台：dashboard、slash commands、DeepSeek chat bridge、工具调用审批。
- Trace ingest：读取 JSONL 任务轨迹，并把 `.skillminer/tool_events.jsonl` 转化为可挖掘 trace。
- Mining：生成任务簇、关联规则、频繁工具序列、图统计、coverage gaps 和 generation entrypoints。
- Recommendation：为新任务推荐已有 skill 或插件能力，并给出 score explanation。
- Generation：从 mining cluster 生成 trace-grounded `SKILL.md` 草稿。
- Evolution：对候选 skill 的结构化 sections 做本地 metric/Pareto 优化。
- Verification：检查 frontmatter、必需章节、安全模式、credential pattern、路径和 validation metadata。
- Validation：根据 `validation.json` 在用户批准后运行验证命令，记录 stdout/stderr/exit code。
- Promotion：人工 review 队列，支持 `accepted`、`rejected`、`merge-needed`、`too-broad`、`duplicate`、`unsafe` 标签。
- Evaluation：输出 baseline/evolved 指标、held-out 诊断、duplicate 结果和 safety holdout。

## 系统架构

整体闭环如下：

```text
user chat / slash command / scriptable CLI
  -> local tools and skill commands
  -> .skillminer/tool_events.jsonl + data/*.jsonl traces
  -> ingest / feedback
  -> mine
  -> recommend or generate
  -> evolve
  -> verify
  -> validate
  -> queue-promotion
  -> promote
  -> evaluate
  -> future runs use updated traces, registry, and evolution memory
```

模块划分：

| 层次 | 主要文件 | 职责 |
| --- | --- | --- |
| CLI dispatch | `skillminer/cli.py`, `skillminer.ps1` | 统一命令入口和 JSON 输出。 |
| Interactive shell | `ui/interactive_shell.py`, `ui/prompt_bar.py`, `ui/cli_style.py` | 终端 UI、slash menu、聊天循环。 |
| Tool layer | `skillminer/tool_layer.py`, `skillminer/tool_chat.py` | 本地文件/命令/网络工具、审批和工具事件记录。 |
| Trace model | `skillminer/models.py`, `skillminer/ingest.py` | JSONL 解析、事件归一化、registry/plugin 读取。 |
| Mining | `features.py`, `clustering.py`, `association_rules.py`, `sequence_mining.py`, `skill_graph.py`, `miner.py` | 文本表示、聚类、关联规则、序列挖掘和图排序。 |
| Recommendation | `skillminer/recommender.py` | 综合多种信号的 Top-K skill 推荐。 |
| Generation | `skillminer/generator.py` | 从 cluster 渲染候选 `SKILL.md`。 |
| Evolution | `skillminer/evolution.py`, `skillminer/quality.py` | 本地 section 优化、重复检测和 evolution memory。 |
| Validation | `skillminer/validation_runner.py` | 批准式 command replay 和反馈记录。 |
| Promotion | `skillminer/promotion.py` | 人工 review queue 与本地 registry gate。 |
| Evaluation | `skillminer/evaluation.py` | baseline/evolved/held-out/safety metrics。 |

## 数据表示

每条任务 trace 使用 JSONL 表示，包含任务文本、项目环境、工具序列、命令、结果、标签和使用过的 skill：

```json
{
  "id": "T001",
  "task": "Fix failing Python CLI tests caused by import paths",
  "project": {
    "language": "python",
    "frameworks": ["pytest"],
    "files": ["skillminer/cli.py", "tests/test_cli.py"]
  },
  "tools": ["rg", "read", "edit", "pytest"],
  "commands": ["pytest -q"],
  "outcome": "success",
  "used_skills": ["test-failure-repair"],
  "duration_sec": 480,
  "retries": 1,
  "tags": ["testing", "debug"]
}
```

候选 skill 使用标准 `SKILL.md` 结构：

```text
---
name: ...
description: ...
tags: [...]
source_cluster: C03
status: candidate
---

## When To Use
## Trigger Signals
## Operating Steps
## Failure Fallbacks
## Verification Suggestions
## Safety Constraints
## Mined Evidence
```

## 关键技术与方法

### 1. TF-IDF 表示与相似度

当前 MVP 为了降低依赖成本，使用标准库实现的 TF-IDF 代替 sentence-transformer embedding。任务、trace 和 skill 文档被转化为 sparse-like vector，并使用 cosine similarity 作为推荐和 duplicate detection 的基础信号。

### 2. 任务聚类与覆盖缺口

系统使用 seeded K-Means 对任务 trace 聚类，统计每个 cluster 的代表任务、top terms、top tools、failure rate、coverage gap 和 tool reuse。coverage gap 高的 cluster 会成为 candidate skill generation 的入口。

### 3. 关联规则

系统从 trace item 中挖掘类似：

```text
project_language=python + framework=pytest + tag=debug -> skill=test-failure-repair
```

的规则，用 rule confidence 和 lift 支撑推荐评分。

### 4. 序列模式挖掘

成功任务中的工具序列会被挖掘为频繁 subsequence，例如：

```text
read -> edit -> pytest -> rg -> python
```

这些序列会进入候选 skill 的 operating steps 或 trigger evidence。

### 5. 异构图排序

系统构建 task-skill-tool-file/plugin 相关的轻量图，并使用 Personalized PageRank 作为推荐信号之一。图结构有助于在冷启动或直接文本相似度不足时发现间接相关 skill。

### 6. 综合推荐评分

候选 skill `s` 对任务 `t` 的评分可以概括为：

```text
Score(s|t) =
  w1 * semantic_similarity(t, s)
+ w2 * rule_confidence(t -> s)
+ w3 * graph_score(t, s)
+ w4 * usage_decay(s)
+ w5 * success_rate(s)
+ w6 * coverage_gap_signal(s)
- w7 * risk(s)
- w8 * cost(s)
```

当前权重来自 `data/recommender_weights.json`，后续可通过 Bayesian optimization 或 human feedback 学习。

### 7. 多目标本地演化

`evolve` 针对结构化 skill sections 生成多个 variants，并从以下目标打分：

- verifier pass/fail
- warning cleanliness
- mined evidence alignment
- non-duplication
- specificity
- safety
- bounded length

危险命令、credential pattern、缺失必需章节属于 hard rejection。非 hard-rejected 候选进入 Pareto/metric selection，最后写入 `outputs/candidate_skills/<cluster>/evolved/`。

### 8. Section-aware duplicate review

重复检测不只返回相似度，还返回 reviewer action：

- `keep`
- `specialize`
- `merge`
- `reject_duplicate`

当候选与已有 skill 或其他候选相似时，系统会生成 section-level merge/specialize proposal，保留 trace IDs、source clusters 和 mined evidence，减少重复 skill 堆积。

## 安全与风险控制

项目刻意采用保守边界：

- 所有生成 skill 都是草稿，不会自动安装。
- 文件工具限制在 workspace 内。
- 写文件、删除、shell、network 工具需要审批。
- verifier 检查危险命令、credential-like 文本、可疑路径和依赖安装提示。
- validation 默认阻断危险、安装和网络命令，只有 `--approve` 后才会执行允许的验证命令。
- promotion 必须人工批准，只更新 `data/skill_registry.json`。
- evaluation 固定测量 `safety_false_negative_rate`，当前样例结果为 `0.0`。

这意味着系统可以自动挖掘、生成、评分和排队，但不绕过人工 review 执行高风险操作。

## 安装与运行说明

### 运行环境

- Windows PowerShell
- Python 3.13 可用环境
- 项目本地 `.venv`
- 依赖见 `requirements.txt` 和 `pyproject.toml`

### 安装依赖

```powershell
cd D:\codex\skillminer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 快速验证

```powershell
python -m pytest -q
python -m skillminer.cli evaluate --variant evolved --top-k 3 --no-tool-events
```

### 常用命令

```powershell
.\skillminer.ps1 ingest --input data/sample_traces.jsonl
.\skillminer.ps1 mine
.\skillminer.ps1 recommend --task "fix failing pytest import path" --language python --framework pytest
.\skillminer.ps1 generate --cluster-id C03
.\skillminer.ps1 evolve --cluster-id C03 --budget 50
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 evaluate --variant evolved --top-k 3 --no-tool-events
```

## 实验设置

### 数据集

当前实验使用 `data/sample_traces.jsonl`，包含 24 条任务 trace。trace 覆盖 Python、pytest、React/Vite、CLI、UI、文档、推荐、验证和安全检查等场景。

数据摘要：

| 指标 | 数值 |
| --- | ---: |
| trace count | 24 |
| success count | 22 |
| failure count | 2 |
| success rate | 0.9167 |
| referenced skill count | 9 |
| registry skill coverage | 1.0 |

### 评估指标

推荐质量：

- Precision@K
- MRR
- recommendation lift
- held-out candidate top-K hit rate

候选质量：

- verifier pass rate
- duplicate rate
- baseline-vs-evolved comparison
- section-aware duplicate action

安全性：

- safety false-negative count
- safety false-negative rate

### Held-out 策略

`evaluate --variant evolved` 使用 deterministic trace-id hash split，把样例 trace 分成 train 和 held-out。系统先在 train traces 上 mine/generate/evolve，再在 held-out traces 上评估推荐和候选 discoverability。

## 实验结果

当前 Phase 2 样例语料结果如下：

| 指标 | 结果 |
| --- | ---: |
| heldout_usefulness_status | improved |
| heldout_candidate_discovery_status | improved |
| heldout_recommendation_status | neutral |
| heldout_evolved_candidate_top_k_hit_rate | 0.2857 |
| heldout_seed_candidate_top_k_hit_rate | 0.1429 |
| heldout_evolved_candidate_top_k_hit_rate_delta | 0.1428 |
| heldout_mrr | 0.8571 |
| heldout_mrr_delta | 0.0 |
| heldout_precision_at_3 | 0.3333 |
| heldout_recommendation_lift | 0.6071 |
| evolved_verifier_pass_rate | 1.0 |
| safety_false_negative_rate | 0.0 |

结果说明：

1. Local evolved candidates 的 held-out discoverability 相比 seed candidates 提升，top-K hit rate delta 为 `0.1428`。
2. 通过 stable overlay gate，草稿候选不会扰动已安装 skill 的相对排序，因此 recommendation status 为 `neutral`，没有出现 gate-level regression。
3. Raw augmented registry 的 MRR 仍可能下降，因为把草稿候选直接插入临时 registry 会改变共享 TF-IDF/graph 上下文。系统将该指标保留为诊断项，而不作为 Phase 2 通过条件。
4. 安全 holdout 中 dangerous command、credential pattern、curl pipe shell 等样例均未出现 false negative，安全 false-negative rate 为 `0.0`。

## 不同输入和参数下的行为

### 普通推荐任务

输入：

```powershell
.\skillminer.ps1 recommend --task "fix failing pytest import path" --language python --framework pytest
```

预期行为：系统综合任务文本、语言、framework、已有 registry 和 mining report，返回 Top-K skill，例如测试修复、安全 review 或 CLI 相关 skill，并附带分数解释。

### 生成候选 skill

输入：

```powershell
.\skillminer.ps1 generate --cluster-id C03
```

预期行为：系统基于 C03 cluster 的 representative task、trace ids、top tools、failure hotspot 和 coverage gap 生成 `outputs/candidate_skills/C03/SKILL.md`。

### 演化候选 skill

输入：

```powershell
.\skillminer.ps1 evolve --cluster-id C03 --budget 50
```

预期行为：系统生成多个 structured-section variants，经过 verifier、安全、duplicate、specificity 和 evidence alignment 评分，选择 Pareto frontier 中的最优候选，并写入 `outputs/candidate_skills/C03/evolved/`。

### 安全验证

输入：

```powershell
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03/evolved
```

预期行为：verifier 检查 skill 文档结构和安全风险。含 `rm -rf /`、credential-like pattern 或 `curl | sh` 的候选会被标记为错误或高风险，不会进入自动 promotion。

## 截图

> 按课程要求最终报告需要至少三张截图。用户已说明截图暂时不用管，因此本节先保留占位，提交最终版前再补图。

建议补充以下截图：

1. 终端首页与 slash menu。
2. `mine` 或 `recommend` 的 JSON/表格输出。
3. `evaluate --variant evolved` 的 Phase 2 指标输出。

```text
图 1：SkillMiner 终端工作台首页（待补）
图 2：SkillMiner 推荐/挖掘输出（待补）
图 3：SkillMiner held-out evaluation 指标（待补）
```

## 讨论

SkillMiner 的主要创新点在于把 Agent skill 管理问题拆成一个可度量的数据挖掘闭环，而不是只依赖人工写 skill 或模型自由生成。系统能够从任务轨迹中抽取数据挖掘信号，再把这些信号用于推荐、候选生成、安全验证和 held-out 评估。

当前结果说明：即使不依赖大型外部 embedding 或 GEPA，标准库 TF-IDF、K-Means、关联规则、频繁序列、PageRank 和本地 Pareto scoring 也能形成一个可运行的 conservative baseline。Phase 2 的 improved 结果主要来自候选 discoverability 的提升，同时 stable overlay 避免了草稿候选破坏已安装 skill 排序。

从工程角度看，本项目重视可复现和安全边界：生成结果只进入候选目录，validation 和 promotion 都需要显式批准，危险内容通过 verifier 与 safety holdout 检查。

## 局限性

当前版本仍有以下限制：

- 样例数据规模较小，只有 24 条 trace，统计结论仍偏 prototype。
- 当前文本表示使用 TF-IDF，尚未接入 sentence-transformer 或其他语义 embedding。
- Recommender 权重是人工配置，不是从真实用户反馈中学习得到。
- Validation 仍在真实 workspace 中运行批准命令，尚未实现 disposable sandbox clone。
- GEPA adapter scaffold 已实现，真实 GEPA smoke 已通过 DeepSeek API 跑通；当前小预算结果未超过 local evolved，因此不自动采用。
- Promotion labels 已写入 memory，但还没有进入推荐/演化 scoring policy。
- UI 是轻量 Python terminal renderer，不是完整 Textual/Ink 应用。
- 部分样例 trace 存在 mojibake，但结构化字段仍可用。

## 未来工作

短期工作：

1. 用更高但受控的 budget 继续运行 `evaluate-gepa`，比较 seed、local evolved 和 GEPA candidates。
2. 把 section merge/specialize proposal 变成显式 rewrite command。
3. 将 promotion labels 纳入 recommendation/evolution scoring。
4. 添加低成本 GEPA/APO 控制，包括 CTM/EPM memory、CAPO racing、dense metric inner loop 和 sparse LLM judge。
5. 实现 disposable sandbox validation，避免验证命令污染真实 workspace。

中长期工作：

1. 引入更强 embedding、HDBSCAN、FP-Growth、learned reranker 等算法替代当前 baseline。
2. 增大 trace 数据集并加入真实项目开发轨迹。
3. 使用 bandit 或 Bayesian optimization 调整推荐权重。
4. 添加低成本 GEPA/APO 控制，包括 CTM/EPM memory、CAPO racing、dense metric inner loop 和 sparse LLM judge。
5. 在 sandbox 和人工 review 稳定后，研究 patch guidance 或 code evolution。

## 复现性说明

项目包含源码、依赖说明、样例数据和测试。核心检查命令为：

```powershell
python -m pytest -q
python -m py_compile skillminer\evaluation.py skillminer\evolution.py skillminer\promotion.py skillminer\quality.py skillminer\validation_runner.py skillminer\cli.py skillminer\recommender.py
python -m skillminer.cli evaluate --variant evolved --top-k 3 --no-tool-events
python -m skillminer.cli evaluate-gepa --cluster-id C03 --budget 2 --top-k 3 --no-tool-events
```

最近一次验证结果：

```text
python -m pytest -q -> 42 passed
safety_false_negative_rate -> 0.0
heldout_usefulness_status -> improved
evaluate-gepa smoke -> completed, not_adopted, safety_false_negative_rate 0.0
```

## 参考文献

[1] HAN Jiawei, PEI Jian, TONG Hanghang. Data Mining: Concepts and Techniques[M]. 4th ed. Cambridge: Morgan Kaufmann/Elsevier, 2022.

[2] Anthropic. Claude Code source snapshot[CP/DK]. D:\download\claude-code-main, 2026-05-11.

[3] Nous Research. Hermes Agent: The Self-Improving AI Agent[EB/OL]. (2026)[2026-05-11]. https://hermes-ai.net/.

[4] Nous Research. hermes-agent: The agent that grows with you[CP/OL]. GitHub, 2026[2026-05-11]. https://github.com/NousResearch/hermes-agent.

[5] Agent Skills. Agent Skills Overview[EB/OL]. [2026-05-11]. https://agentskills.io/.

[6] MURTY Shikhar, MANNING Christopher, SHAW Peter, JOSHI Mandar, LEE Kenton. BAGEL: Bootstrapping Agents by Guiding Exploration with Language[J/OL]. arXiv:2403.08140, 2024. https://arxiv.org/abs/2403.08140.

[7] ZHENG Boyuan, FATEMI Michael Y., JIN Xiaolong, et al. SkillWeaver: Web Agents can Self-Improve by Discovering and Honing Skills[J/OL]. arXiv:2504.07079, 2025. https://arxiv.org/abs/2504.07079.

[8] HUANG Xu, CHEN Junwu, FEI Yuxing, LI Zhuohan, SCHWALLER Philippe, CEDER Gerbrand. CASCADE: Cumulative Agentic Skill Creation through Autonomous Development and Evolution[J/OL]. arXiv:2512.23880, 2025. https://arxiv.org/abs/2512.23880.

[9] SONG Yifan, YIN Da, YUE Xiang, HUANG Jie, LI Sujian, LIN Bill Yuchen. Trial and Error: Exploration-Based Trajectory Optimization of LLM Agents[C/OL]//Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics. Bangkok: ACL, 2024: 7584-7600. https://aclanthology.org/2024.acl-long.409/.

[10] WANG Boshi, FANG Hao, EISNER Jason, VAN DURME Benjamin, SU Yu. LLMs in the Imaginarium: Tool Learning through Simulated Trial and Error[C/OL]//Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics. Bangkok: ACL, 2024: 10583-10604. https://aclanthology.org/2024.acl-long.570/.

[11] ZHU Xiangrong, XIE Yuexiang, LIU Yi, LI Yaliang, HU Wei. Knowledge Graph-Guided Retrieval Augmented Generation[C/OL]//Proceedings of NAACL-HLT 2025. Albuquerque: ACL, 2025: 8912-8924. https://aclanthology.org/2025.naacl-long.449/.

[12] BARRY Mariam, CAILLAUT Gaetan, HALFTERMEYER Pierre, et al. GraphRAG: Leveraging Graph-Based Efficiency to Minimize Hallucinations in LLM-Driven RAG for Finance Data[C/OL]//Proceedings of the Workshop on Generative AI and Knowledge Graphs. Abu Dhabi: International Committee on Computational Linguistics, 2025: 54-65. https://aclanthology.org/2025.genaik-1.6/.

[13] WAGNER Robin, KITZELMANN Emanuel, BOERSCH Ingo. Mitigating Hallucination by Integrating Knowledge Graphs into LLM Inference: A Systematic Literature Review[C/OL]//Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics, Student Research Workshop. Vienna: ACL, 2025: 795-805. https://aclanthology.org/2025.acl-srw.53/.

[14] AGRAWAL Garima, KUMARAGE Tharindu, ALGHAMDI Zeyad, LIU Huan. Can Knowledge Graphs Reduce Hallucinations in LLMs?: A Survey[C/OL]//Proceedings of NAACL-HLT 2024. Mexico City: ACL, 2024: 3947-3960. https://doi.org/10.18653/v1/2024.naacl-long.219.

[15] XIE Haihua, HE Miao. GC-DPG: Graph-Constrained Dual-Phase Generation for Safe and Verifiable Chinese Medical Question Answering[C/OL]//GLOW@WWW 2026 accepted papers. 2026[2026-05-11]. https://glow-workshop.github.io/www2026/.

[16] GEPA AI. GEPA Documentation: Optimize Anything API and LiteLLM adapter[EB/OL]. [2026-05-13]. https://gepa-ai.github.io/gepa/api/optimize_anything/optimize_anything/; https://gepa-ai.github.io/gepa/api/optimize_anything/make_litellm_lm/.

[17] Python Package Index. gepa 0.1.1: A framework for optimizing textual system components[EB/OL]. [2026-05-13]. https://pypi.org/project/gepa/.

[18] LiteLLM. LiteLLM Documentation and Python package metadata[EB/OL]. [2026-05-13]. https://docs.litellm.ai/; https://pypi.org/project/litellm/.

[19] DeepSeek-AI. DeepSeek-R1 README and model notes[CP/OL]. GitHub, 2025[2026-05-13]. https://github.com/deepseek-ai/DeepSeek-R1.

[20] DeepSeek-AI. DeepSeek-V3 README and benchmark notes[CP/OL]. GitHub, 2025[2026-05-13]. https://github.com/deepseek-ai/DeepSeek-V3.

[21] AGRAWAL Lakshya A., TAN Shangyin, SOYLU Dilara, et al. GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning[J/OL]. arXiv:2507.19457v2, 2026-02-14[2026-05-14]. DOI:10.48550/arXiv.2507.19457. https://arxiv.org/abs/2507.19457.

[22] ZEHLE Tom, SCHLAGER Moritz, HEISS Timo, FEURER Matthias. CAPO: Cost-Aware Prompt Optimization[C/OL]//Proceedings of the Fourth International Conference on Automated Machine Learning. PMLR, 2025, 293:18/1-45[2026-05-14]. https://proceedings.mlr.press/v293/zehle25a.html.

[23] LIANG Guanbao, BEI Yuanchen, ZHOU Sheng, et al. Generalizable Self-Evolving Memory for Automatic Prompt Optimization[J/OL]. arXiv:2603.21520v1, 2026-03-23[2026-05-14]. DOI:10.48550/arXiv.2603.21520. https://arxiv.org/abs/2603.21520.

[24] ZHAO ChenZhuo, LIU Ziqian, WANG Xinda, LU Junting, RUAN Chaoyi. PMPO: Probabilistic Metric Prompt Optimization for Small and Large Language Models[C/OL]//Findings of the Association for Computational Linguistics: EMNLP 2025. Suzhou: ACL, 2025-11:14728-14761[2026-05-14]. DOI:10.18653/v1/2025.findings-emnlp.795. https://aclanthology.org/2025.findings-emnlp.795/.
