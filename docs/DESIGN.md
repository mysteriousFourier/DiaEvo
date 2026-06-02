# DiaEvo 设计说明

## 产品形态

DiaEvo 是一个本地 CLI 工作台，主入口是：

```powershell
diaevo
```

不带子命令时，它打开交互式终端 shell；带子命令时，它作为脚本化 JSON CLI 使用。`diaevo-home` 用于单独打开终端首页。

DiaEvo 同时承担五个角色：

1. DeepSeek 驱动的交互式终端助手。
2. 带审批门的本地工具执行器。
3. 轨迹和工具事件记录器。
4. 技能挖掘、推荐、生成、验证、校验和晋升 CLI。
5. 技能自演化评估和优化宿主。

设计目标是让 CLI 能从真实使用中改进自己的技能系统，同时把高风险操作放在清晰的用户审批边界之后。

## 语言和终端输出策略

DiaEvo 默认中文优先。用户可见内容、报告、交互提示、可见页面和引导中文交互的 prompt 都应使用中文。

英文只保留在兼容性表面：代码标识符、CLI 命令名、JSON 字段名、外部 API 名、测试 fixture、论文题名、引用信息，以及 `SKILL.md` 必需章节标题。新增用户可见英文说明时，应能说明具体兼容原因。

模型输出和工具说明禁止使用 emoji。终端输出策略由 `ui/output_policy.py` 管理：TTY 环境默认用 Rich 渲染 Markdown，非 TTY 或 `DIAEVO_OUTPUT=plain` 时转成纯文本。`ui/progress.py` 提供轻量状态动效，用于模型请求、工具调用和命令执行。

## 端到端数据流

```text
用户聊天 / 斜杠命令 / 脚本命令
  -> 本地工具和技能命令
  -> .diaevo/tool_events.jsonl + data/*.jsonl
  -> ingest / feedback
  -> mine
  -> reviewed KG / editable KG workbench
  -> recommend 或 generate
  -> evolve
  -> verify
  -> validate
  -> review-script（仅 code-backed skill）
  -> queue-promotion
  -> rewrite-promotion / promote
  -> evaluate
  -> 后续运行使用更新后的轨迹、注册表和演化记忆
```

这条链路刻意保守：当前系统可以演化技能文本、报告和注册表元数据，但不会自动安装外部技能，也不会把优化器生成的代码改动写回真实工作区。

## 模块职责

| 层 | 文件 | 职责 |
| --- | --- | --- |
| CLI 分发 | `diaevo/cli.py`、`install-diaevo.ps1` 生成的用户级 shim | 脚本化命令、默认交互入口、UTF-8 和项目本地 Python 设置。 |
| 交互终端 | `ui/interactive_shell.py`、`ui/prompt_bar.py`、`ui/cli_style.py`、`ui/tool_render.py`、`ui/output_policy.py`、`ui/progress.py` | 仪表盘、可信工作区确认、斜杠菜单、输入框、模型对话、工具预览、Markdown/纯文本渲染、状态动效。 |
| 聊天桥接 | `diaevo/deepseek_chat.py`、`diaevo/tool_chat.py`、`diaevo/env.py` | OpenAI 兼容 DeepSeek 调用、工具 schema 转换、工具结果消息、本地 `.env` 读取和更新。 |
| 工具层 | `diaevo/tool_layer.py` | 工作区内文件工具、shell/网络工具、审批预览、事件日志。 |
| 轨迹模型 | `diaevo/models.py`、`diaevo/ingest.py`、`diaevo/storage.py` | JSONL 解析、轨迹规范化、工具事件转换、注册表和插件元数据加载。 |
| 挖掘 | `diaevo/features.py`、`diaevo/clustering.py`、`diaevo/association_rules.py`、`diaevo/sequence_mining.py`、`diaevo/skill_graph.py`、`diaevo/miner.py` | TF-IDF、K-Means、关联规则、频繁序列、task-skill-tool 图、覆盖缺口和生成入口。 |
| 知识图谱 | `diaevo/knowledge_graph.py`、`data/knowledge_graph/` | 从轨迹、工具事件、web 证据、会话日志和挖掘报告生成待审核实体、三元组、声明和证据路径。 |
| 推荐 | `diaevo/recommender.py`、`data/recommender_weights.json` | 综合相似度、规则、PageRank、使用情况、成功率、覆盖缺口、风险和成本，支持 Pareto rerank。 |
| 候选生成 | `diaevo/generator.py` | 从挖掘簇渲染有证据支撑的 `SKILL.md` 草稿；可选生成只读 helper code、`code_artifacts.json` 和 `validation.json`。 |
| 脚本制品 | `diaevo/script_artifacts.py` | 读取和更新 code-backed skill 的脚本入口、审查状态、validation 摘要和 `SKILL.md` 回退策略。 |
| 静态验证 | `diaevo/verifier.py` | 检查 frontmatter、必需章节、安全模式、凭据模式、可疑路径、依赖提示和 validation 元数据。 |
| 演化 | `diaevo/evolution.py`、`diaevo/quality.py`、`data/evolution_memory.json` | 本地指标/Pareto 优化、重复检查、记忆检索和 ASI 记录。 |
| 校验 | `diaevo/validation_runner.py` | 审批后的 `validation.json` 命令在一次性沙盒副本中运行，捕获 stdout/stderr/exit code/duration/touched files/diff。 |
| 代码演化研究 | `diaevo/code_evolution.py` | Phase 7 patch strategy 和沙盒内候选 patch 评估。 |
| 晋升 | `diaevo/promotion.py` | 人工晋升队列、审核标签、`rewrite-promotion` 草稿和本地注册表写入门。 |
| 评估 | `diaevo/evaluation.py`、`diaevo/gepa_adapter.py` | baseline/evolved/held-out/safety 指标、GEPA 可选对比和实验矩阵。 |

## 命令模型

脚本化命令包括：

```text
ingest, mine, export-mining-snapshot, recommend, generate, verify, evolve,
validate, queue-promotion, label-promotion, rewrite-promotion, review-script, promote,
adapt-skill, kg, answer-kg, demo, home, tools, feedback, evaluate, evaluate-gepa,
evaluate-gepa-phase4, evaluate-code-evolution, tool, chat-test
```

交互式斜杠命令包括：

```text
/ingest, /mine, /kg, /recommend, /generate, /verify, /demo, /feedback,
/kg_answer on|off|status, /tools, /tool, /model, /talk, /image,
/baseurl, /key, /home, /help, /exit
```

非斜杠文本会发送给 DeepSeek。模型可请求本地工具，但工具调用仍使用 `tool_layer.py` 的同一套审批、边界和事件日志逻辑。

## 安全模型

安全边界分布在多层：

- 交互式入口先确认工作区可信。
- 文件工具必须留在 workspace 内。
- 写入、编辑、删除、补丁、shell 和网络工具必须先预览并审批。
- 工具事件日志会清洗 key/token/secret/password 等敏感字段。
- verifier 检查危险命令、凭据样文本、父级路径、缺失章节和依赖安装提示。
- validation runner 默认阻止危险、安装和网络命令；审批后也只在沙盒副本中运行。
- code-backed skill 的 helper 脚本 v1 只能是 `scripts/skill_flow.py` 只读助手；未通过人工 `review-script` 审查或 validation 未通过时，推荐结果必须回退到 `SKILL.md`。
- promotion 必须人工审批，只写 `data/skill_registry.json`。
- KG 候选事实必须审核通过后才进入 active KG。
- 交互式终端通过 `/kg_answer on` / `/kg_answer off` 开关严格图谱约束回答模式；CLI 用 `answer-kg --strict`，工具层可手动调用 `kg_answer(strict=true)`。自动聊天工具列表仍不暴露 `kg_answer`。
- 评估必须持续监控 `safety_false_negative_rate == 0.0`。

安全约束不是加权偏好。危险候选是硬失败。

## 当前算法选择

当前实现优先低依赖和可复现：

- `features.py` 使用标准库 TF-IDF。
- `clustering.py` 使用仓库内 seeded K-Means。
- `association_rules.py` 枚举 Apriori 风格关联规则。
- `sequence_mining.py` 挖掘 PrefixSpan 风格子序列。
- `skill_graph.py` 实现轻量 Personalized PageRank。
- `knowledge_graph.py` 使用确定性抽取实现 review-first KG；图结构向量检索默认可复现 TF-IDF，也支持显式 dense embedding 后端。
- `evolution.py` 使用本地指标/Pareto 演化。

`pyproject.toml` 的 `full` extra 保留了更重的可选依赖，但核心命令不依赖它们。Rich 是默认依赖，用于终端 Markdown 渲染。

当前第三方库边界：

- 默认运行时：`rich>=13.7`。
- 开发和测试：`pytest>=8.0`。
- 可选 dense KG 检索：`sentence-transformers`，只在显式 dense backend 时动态导入。
- 可选 GEPA 优化：`gepa` / LiteLLM stack，只在 `evaluate-gepa` 非 `--dry-run` 时动态导入。
- `numpy`、`pandas`、`scikit-learn`、`networkx`、`mlxtend`、`textual` 和 `pyyaml` 仍是 `full` extra 扩展点，不是核心命令必需依赖。

完整清单见 `docs/THIRD_PARTY_LIBRARIES.md`。

## 技能自演化设计

当前默认演化目标是结构化 `SKILL.md` 文本。Phase 7 允许候选 skill 附带受限 helper code，但只作为候选制品在沙盒中验证，不直接进入生产代码。`generate --with-code` 当前会从挖掘簇生成协同 skill 目录：`SKILL.md`、只读 `scripts/skill_flow.py`、`code_artifacts.json` 和 `validation.json`。系统尚不支持“读取任意已有 `SKILL.md` 并反向生成专用脚本”的命令；后续可新增 `generate-script --skill <dir>`。

候选章节：

```text
When To Use
Trigger Signals
Operating Steps
Failure Fallbacks
Verification Suggestions
Safety Constraints
```

本地优化器使用以下信号评分：

- verifier pass/fail
- warning cleanliness
- mined evidence alignment
- duplicate similarity
- specificity
- safety
- bounded length

演化记忆保存成功模板、verifier 错误、validation 结果、重复模式和 promotion 审核模式。这是后续接入 GEPA 的基线和安全脚手架。

code-backed skill 的运行选择规则固定为：

```text
if script.review_status == approved
   and validation.status == passed
   and verifier has no helper hard error:
      execution_mode = script
else:
      execution_mode = skill_md_fallback
```

`recommend` 会输出 `script_available`、`script_status`、`script_entrypoint`、`execution_mode` 和 `fallback_reason`，供调用方决定是否使用脚本。

## 知识图谱设计

KG 层与推荐器中的 PageRank 图不同。PageRank 图是推荐特征；KG 是事实、声明、置信度、来源和严格回答的证据层。

KG v1 是 review-first：

- `build-kg-delta` 从结构化来源生成候选。
- 候选写入 `data/knowledge_graph/review_queue.jsonl`，状态为 `pending`。
- `review-kg-delta` 标注 `accepted`、`rejected`、`needs_source`、`low_confidence`、`conflict` 或 `stale`。
- `apply-kg-delta` 只把 accepted 候选写入 `data/knowledge_graph/current/`。
- `kg` 打开可编辑 HTML 工作台。
- `kg --apply-edit <json> --approve` 才把编辑写回 active KG。

当前检索是“向量召回 + 图扩展 + 证据约束回答”：accepted 实体、三元组和声明会转为 KG 文档。默认后端仍是可复现的本地 TF-IDF；需要真正的 dense 图结构向量检索时，使用 `answer-kg --vector-backend dense` 或 `DIAEVO_KG_VECTOR_BACKEND=dense`，系统会用 `sentence-transformers` 生成 embedding 召回种子，再沿 subject-object 关系扩展证据子图。默认 embedding 模型是 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，默认下载镜像是 `https://hf-mirror.com`，可通过 `DIAEVO_KG_EMBEDDING_MODEL` 和 `DIAEVO_HF_ENDPOINT` 覆盖。

## GEPA 集成边界

GEPA 是可选优化器，应接在当前 evolution/evaluation 接口之后。

DiaEvo 负责：

- 轨迹和工具事件规范化
- 挖掘和生成入口
- `SKILL.md` 渲染契约
- verifier、validation、duplicate 和 safety gate
- 推荐、评估报告和晋升队列
- 记忆持久化

GEPA 负责：

- 结构化候选的反思式 mutation
- 候选池管理
- Pareto frontier 选择
- section-aware merge 提议
- 优化预算控制

首个 GEPA 目标是 `SKILL.md` 章节优化。更晚的目标可以包括生成策略、validation 元数据建议、图到技能策略，以及在沙盒稳定后研究 patch guidance 或代码演化。

## 阶段路线图

当前可信检查点：**Phase 6 人工反馈学习已完成，Phase 7 安全代码演化研究已开始**。

| 阶段 | 工程目标 | 状态和主要风险 |
| --- | --- | --- |
| 0. 集成 CLI | 统一交互式/脚本化入口、工具 schema、事件日志、DeepSeek bridge。 | 已完成；风险是工具执行安全和轨迹卫生。 |
| 1. 保守技能循环 | generate/evolve/verify/validate/queue/promote/evaluate。 | 已完成；风险是候选安全和人工晋升边界。 |
| 2. 质量加固 | held-out 指标、稳定 overlay gate、重复建议、validation/promotion 记忆。 | 已完成；风险是只优化 verifier 合规而非真实有用性。 |
| 3. GEPA adapter | 可选 GEPA 章节优化器，复用 DiaEvo evaluator 和 ASI。 | 已有 scaffold 和 smoke；风险是成本和幻觉。 |
| 4. 低成本 APO/GEPA | CTM/EPM memory、CAPO racing、metric inner loop、sparse judge、dry-run matrix。 | dry-run/reporting gate 已完成；真实 non-dry-run cost sweep 是可选后续。 |
| 5. 一次性沙盒 | 在副本中校验并捕获 diff/touched files。 | 已完成；核心边界是不自动写回真实工作区。 |
| 6. 人工反馈学习 | promotion labels、validation artifacts、rewrite drafts 进入记忆和评分。 | 已完成；风险是过早自动晋升。 |
| 7. 代码演化研究 | `evaluate-code-evolution` 的 strategy-only、sandbox baseline 证据收集和 sandbox-only patch 评估。 | 已开始；风险是未经审查应用代码变更。 |

## 当前阶段成功标准

- `python -m pytest -q` 通过。
- `diaevo evaluate --variant evolved` 写出稳定报告。
- `safety_false_negative_rate == 0.0`。
- evolved candidate verifier pass rate 不回退。
- 重复建议可被人工审查和处理。
- held-out usefulness 在样例语料上不回退。
- validation 和 code evolution 只在沙盒里执行 baseline 收集和候选变更。
- promotion 保持人工审批。

## 外部 Sandbox 评估

`trycua/cua` 是面向 computer-use agents 的开源基础设施，提供本地或云端 VM/container sandbox、截图、鼠标键盘、shell、移动手势、trajectory replay 和 benchmark 支持。它适合 DiaEvo 未来验证 GUI/桌面类 code-backed skill，特别是需要真实窗口、浏览器、桌面应用或移动手势的流程。

当前 Phase 7 的默认后端仍使用本地 disposable workspace sandbox，因为 code-backed skill MVP 只生成 skill 文件夹内的只读 helper code，不需要虚拟桌面、外部安装或网络。CUA 应作为可选 sandbox backend 研究项，接入前必须保持默认无网络、人工审批、报告可复现和不写回真实 workspace 的边界。

## 代码进化门控

Phase 7/8 的代码进化不是无限自改循环，而是预算受限的候选选择系统：

```text
baseline evidence
  -> candidate proposer
  -> cheap static gates
  -> sandbox correctness replay
  -> maintainability and efficiency gates
  -> Pareto frontier
  -> human review
```

默认预算：

| 参数 | 默认值 |
| --- | --- |
| `max_rounds` | `2` |
| `max_candidates_per_round` | `3` |
| `max_total_sandbox_runs` | `6` |
| `max_wall_time` | `10 min` |
| `human_review_required` | `always` |

门控规则：

- correctness gate：必须先有 baseline，再验证候选 build/test/lint/typecheck，不允许降低已有测试结果。
- security gate：路径、危险命令、凭据、网络、安装命令、helper capability 检查失败即停止。
- maintainability gate：限制 touched files、diff 大小、函数长度、复杂度和模块边界；避免为“模块化”进行大范围重构。
- efficiency gate：只有稳定 benchmark 存在时启用，要求多次运行中位数达到显著阈值；没有 benchmark 时禁止自动采纳性能优化。
- selection gate：以 correctness、security、maintainability、performance、diff size 和 sandbox cost 做 Pareto 排序，只输出少量候选给人工 review。

停止条件：

- 任一候选触发 security warning。
- 连续一轮无 correctness 或 quality 改善。
- sandbox 预算耗尽。
- benchmark 噪声大于预设提升阈值。
- 需要跨模块大范围重构或安装新依赖。

外部研究对设计的约束：

- Iterative self-repair 有效但主要收益集中在前几轮，所以默认 `max_rounds = 2`。
- Self-improving coding agents 需要 reflection、benchmark 和更新记录，因此 DiaEvo 必须保留 baseline、candidate、sandbox report 和人工 review 决策。
- Secure code benchmark 显示功能正确不等于安全，因此 security gate 是硬门而非加权项。
- Architectural smell benchmark 显示激进重构可能引入新 smell，因此 maintainability gate 优先限制范围和复杂度，而不是鼓励大改。
