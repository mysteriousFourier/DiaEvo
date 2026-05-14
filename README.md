# SkillMiner

SkillMiner 是一个本地 CLI 工作台，用于 Agent 技能挖掘、推荐、生成、验证和自演化。

本项目不是单纯的 GEPA 实验，也不是单纯的 `SKILL.md` 生成器。目标产品形态是 `.\skillminer.ps1`：一个兼具脚本化和交互式终端体验的工具，包含仪表盘、斜杠命令、DeepSeek 聊天、OpenAI 兼容工具调用、需审批的本地工具、轨迹捕获、技能挖掘、候选生成、验证、晋升审核和评估。

长期目标是让 CLI 从自身使用中学习。工具调用和任务结果会沉淀为轨迹；轨迹产出挖掘证据；证据驱动技能推荐和候选生成；候选技能经过验证、校验、演化和人工审核；被接受的结果再进入下一轮循环。GEPA 应作为这个循环中的可选反思优化器，并继续使用 SkillMiner 的挖掘、 verifier、validation、重复检查和 held-out 指标作为评估与安全层。

## 语言策略

SkillMiner 在本项目中默认中文优先。用户可见内容、交互提示、生成报告、挖掘/KG 快照、可见 HTML 页面，以及引导中文用户体验的 prompt 都应默认使用中文。只有命令名、JSON 字段、代码标识符、引用信息和 `SKILL.md` 必需章节标题等兼容性表面保留英文。

## 当前循环

当前已实现的保守循环是：

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

当前演化是对结构化 `SKILL.md` 章节进行的无依赖本地指标/Pareto 优化，是后续接入 GEPA 的基线和安全脚手架。

已审核知识图谱支线循环是：

```text
traces + tool_events + web_search/web_fetch + optional conversation log
  -> reviewed active KG
  -> editable KG workbench (`kg` / `/kg`)
  -> optional answer-kg --strict / manual kg_answer tool
```

当前硬边界：

- 生成的技能在通过验证、校验和人工晋升前都只是草稿。
- `validate` 只有在审批后才执行命令，并默认阻止危险、安装和网络模式。
- `promote` 只更新 `data/skill_registry.json`，不会安装外部技能。
- 知识图谱候选在审核者接受前不是 active facts。
- 严格图谱约束回答是显式 CLI/工具开关，不是默认聊天行为。
- 当前没有启用生产代码自演化。
- GEPA 是计划中的可选后端，不是必需依赖。

## 快速开始

```powershell
cd D:\codex\skillminer
.\skillminer.ps1
```

不带参数运行 `skillminer.ps1` 会打开交互式终端 shell。首次运行需要确认工作区可信，然后可以使用斜杠命令或普通聊天：

```text
/ingest
/mine
/recommend fix failing pytest import path
/generate C03
/verify outputs/candidate_skills/C03
/tools
/tool read_file path=README.md limit=20
/model deepseek-v4-pro
/baseurl https://api.deepseek.com
/key
/home
/exit
```

普通文本会按 `.env` 配置发送给 DeepSeek；模型可以请求本地工具。只读工具会直接运行；写入、删除、补丁、shell 和网络工具会先显示预览并要求审批。

## 脚本化命令

```powershell
.\skillminer.ps1 ingest --input data/sample_traces.jsonl
.\skillminer.ps1 mine
.\skillminer.ps1 export-mining-snapshot --date 260513
.\skillminer.ps1 recommend --task "fix failing pytest import path" --language python --framework pytest
.\skillminer.ps1 generate --cluster-id C03
.\skillminer.ps1 evolve --cluster-id C03 --budget 50
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 validate --skill outputs/candidate_skills/C03/evolved --approve
.\skillminer.ps1 queue-promotion --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 label-promotion --queue-id <id> --label merge-needed --note "merge with nearest skill"
.\skillminer.ps1 promote --queue-id <id> --approve
.\skillminer.ps1 kg --date 260513
.\skillminer.ps1 kg --apply-edit path\to\skillminer_kg_edit_260513.json --approve
.\skillminer.ps1 answer-kg --query "which tools support pytest traces?" --strict
.\skillminer.ps1 feedback
.\skillminer.ps1 evaluate --variant evolved --top-k 3
.\skillminer.ps1 evaluate-gepa --cluster-id C03 --budget 50 --top-k 3
.\skillminer.ps1 tools
.\skillminer.ps1 tool read_file --arg path=README.md --arg limit=20
.\skillminer.ps1 chat-test --interactive
.\skillminer-home.ps1
```

`skillminer.ps1` 会设置 `PYTHONPATH`、UTF-8 终端 I/O，并使用项目本地 `.venv` Python。`pyproject.toml` 里也暴露了 `skillminer` 和 `skillminer-home` 控制台脚本。

## 主要能力

| Area | What it does |
| --- | --- |
| Interactive CLI | Dashboard, trust prompt, slash menu, multiline input, DeepSeek chat bridge, runtime model/base URL/API key commands. |
| Local tool layer | `list_files`, `read_file`, `write_file`, `edit_file`, `delete_file`, `apply_patch`, `run_shell`, `web_search`, and `web_fetch` with workspace boundaries and approval gates. |
| Trace capture | Every local tool call appends sanitized JSONL to `.skillminer/tool_events.jsonl`; `ingest` and `feedback` can fold those events into processed traces. |
| Mining | TF-IDF features, K-Means clusters, association rules, frequent tool sequences, task-skill-tool graph, coverage gaps, failure hotspots, and high-reuse paths. |
| Mining snapshot | Human-readable mining evidence packages under `data/mining_snapshots/YYMMDD/` with Markdown, CSV, graph edges, and summary JSON. |
| Knowledge graph | Reviewable incremental KG deltas from traces, user conversation JSONL, tool events, web_search/web_fetch evidence, and mining reports; accepted facts export to `data/knowledge_graph/YYMMDD/`. |
| Editable KG | `kg` / `/kg` opens an editable graph workbench: edit nodes and relations while viewing the graph, save browser drafts, and export JSON edits for approved write-back. |
| Graph-vector KG retrieval | Accepted KG nodes, triples, and claims are converted into local TF-IDF sparse vectors; `answer-kg` first retrieves vector seed hits, then expands a graph evidence subgraph before answering. |
| KG answer switch | `answer-kg --strict` and the manual `kg_answer` tool answer only from accepted graph-vector evidence subgraphs; the model cannot auto-select strict KG mode from the chat tool list. |
| Recommendation | Weighted ranker over semantic similarity, rules, PageRank, usage decay, success rate, coverage gap, recent reuse, risk, and cost; optional Pareto reranking. |
| Generation | Evidence-backed candidate `SKILL.md` from mined clusters. |
| Verification | Frontmatter and required sections, safety patterns, credential patterns, parent paths, dependency hints, and validation metadata. |
| Evolution | Local metric/Pareto candidate section optimization, duplicate checks, held-out evaluation, and evolution memory. |
| Validation | Approval-gated replay of `validation.json` commands with stdout/stderr/status captured for feedback. |
| Promotion | Human-reviewed queue, section-aware duplicate report, review labels, and local registry update only after explicit approval. |
| Evaluation | Baseline/evolved reports with Precision@K, MRR, lift, duplicate rate, verifier pass rate, held-out usefulness diagnostics, memory summary, and safety false-negative rate. |

当前 KG 已实现 GraphRAG-like 图结构向量检索：`graph_vector_index.json` 导出可检索 KG 文档和稀疏向量词项，`graph_vector_demo.md` 展示示例查询如何从向量种子扩展到证据子图。当前向量后端是本地 TF-IDF，后续可替换为 embedding/vector DB 后端，但回答路径已经是“向量召回 + 图扩展 + 严格证据约束”。

## Skill Self-Evolution Phases

当前检查点：**Phase 4 dry-run/reporting gate 已完成，Phase 5 可以开始**。Phase 0-3 已实现。Phase 4 已生成 `evaluate-gepa-phase4` dry-run 矩阵报告：`outputs/reports/gepa_phase4_experiments.json`，范围为 cluster `C02`、budget `5`、7 个条件、0 个失败，且 `safety_false_negative_rate == 0.0`。因为该报告是 `dry_run=true`，它证明的是实验框架、断点续跑/报告、控制变量和安全统计已经可用；它不等价于真实 non-dry-run GEPA 成本胜出实验。下一位交接者可以直接开始 Phase 5 disposable sandbox validation。

The stage goal is to reach reliable skill self-evolution: SkillMiner should produce evolved skills that are measurably more useful on held-out traces while keeping safety false-negative rate at zero.

| Phase | Goal | Completion signal |
| --- | --- | --- |
| Phase 0: Integrated CLI foundation | One local command surface for chat, tools, mining, recommendation, generation, verification, validation, promotion, and evaluation. | Interactive and scriptable commands work; tool events are logged and ingestible. |
| Phase 1: Conservative skill loop | Generate and locally evolve `SKILL.md` candidates without external installs or code mutation. | `evaluate --variant evolved` reports stable metrics; verifier pass rate and safety false-negative rate are tracked. |
| Phase 2: Quality hardening | Improve candidate usefulness before widening automation. | Held-out usefulness improves on the sample corpus, duplicate recommendations include merge/specialize proposals, validation feedback and promotion labels enter memory. |
| Phase 3: Optional GEPA adapter | Add GEPA behind the existing evaluator to optimize structured skill sections with ASI. | `evaluate-gepa` compares seed, local evolved, and GEPA candidates; GEPA candidates beat local/Pareto candidates on held-out traces without safety regression. |
| Phase 4: Low-cost GEPA/APO research | 实现 CTM/EPM memory、CAPO racing、budget controls、dense metrics、sparse judge policy 的受控实验报告。 | dry-run 矩阵报告已写入 `outputs/reports/gepa_phase4_experiments.json`；安全率保持 `0.0`；真实 non-dry-run 成本胜出实验后置。 |
| Phase 5: Sandbox-backed validation | 在一次性 workspace 副本里运行 validation，再进入更丰富 replay 或 patch guidance。 | 下一阶段现在可以开始：validation 必须捕获 diff、touched files、stdout/stderr、exit code、duration，且不能修改真实 workspace。 |
| Phase 6: Learned promotion and policy evolution | Learn from human labels and accepted/rejected candidates. | Human labels feed memory; promotion policy improves acceptance rate while staying manual. |
| Phase 7: Safe code-evolution research | Only after sandbox replay and labels are stable, explore GEPA/gskill-style patch guidance or code evolution. | Code changes remain sandboxed, revertible, and human-reviewed before real application. |

## GEPA Direction

GEPA is the planned optimizer, not the whole system. SkillMiner should continue to own trace ingestion, mining, verification, recommendation, reporting, and safety gates. GEPA should own reflective mutation, candidate pool management, Pareto selection, merge, and optimization budget handling.

First GEPA target:

```text
generated structured SKILL.md sections
  -> evaluator renders SKILL.md
  -> verifier + duplicate + evidence + validation + held-out metrics return ASI
  -> GEPA mutates sections
  -> SkillMiner verifies, evaluates, and queues for human promotion
```

The adapter uses the existing DeepSeek OpenAI-compatible API configuration from `.env`. Required local settings are `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`; optional settings include `DEEPSEEK_MAX_TOKENS`, `DEEPSEEK_TEMPERATURE`, and `DEEPSEEK_TIMEOUT`. The real key stays only in `.env`; GEPA reports store provider/model metadata but never the raw key.

GEPA remains optional. Use `--dry-run` to exercise seed/local comparison, selected-cluster held-out reporting, and safety checks without importing or calling GEPA:

```powershell
.\skillminer.ps1 evaluate-gepa --cluster-id C03 --budget 5 --top-k 3 --dry-run
```

Without `--dry-run`, the command requires the external GEPA/LiteLLM stack to be installed in the active environment. A missing dependency fails clearly and does not affect `generate`, `evolve`, or `evaluate`.

Phase 4 batch experiments use the same evaluator but write a row-oriented cost/usefulness report:

```powershell
.\skillminer.ps1 evaluate-gepa-phase4 --cluster-id C03 --budgets 5,10,25,50 --top-k 3 --no-tool-events
```

For non-network validation of the experiment matrix:

```powershell
.\skillminer.ps1 evaluate-gepa-phase4 --cluster-id C03 --budgets 5,10 --top-k 3 --no-tool-events --dry-run
```

`evaluate-gepa-phase4` writes `outputs/reports/gepa_phase4_experiments.json`. It compares local evolved, seed-only GEPA, CTM/EPM memory ablations, CAPO-style cheap gates, and sparse judge policy. Candidates remain drafts; the command does not validate, promote, install, or mutate production code.

The Phase 4 report is written after every condition row. Re-running the same command resumes from completed rows by default; pass `--no-resume` to discard the previous matrix and rerun all rows.

最新 Phase 4 交接状态：

- 报告：`outputs/reports/gepa_phase4_experiments.json`
- 范围：dry-run 矩阵，cluster `C02`，budget `5`
- 行：`local_evolved`、`gepa_seed_only`、`gepa_ctm`、`gepa_epm`、`gepa_ctm_epm`、`gepa_racing`、`gepa_sparse_judge`
- 结果：`failures == []`，所有行 `safety_false_negative_rate == 0.0`；所有 GEPA 行 `adoption_status == not_applicable`，原因是 dry-run 模式不会生成真实 GEPA candidate。
- 下一阶段：Phase 5 disposable sandbox validation 可以开始；真实 non-dry-run GEPA budget sweep 保持可选，并与 Phase 5 分离。

The `docs/talk_whit_GEPA.md` research notes add the cost strategy:

- MemAPO-style CTM/EPM memory for reusable success templates and error patterns.
- CAPO racing for early rejection of bad candidates.
- PMPO/MoPPS or bandit-like selection to reduce full rollouts.
- Dense automated metrics in the inner loop and sparse LLM-as-judge in the outer loop.

## Data Files

| Path | Purpose |
| --- | --- |
| `data/sample_traces.jsonl` | Seed trace dataset. |
| `data/processed_traces.jsonl` | Normalized traces written by `ingest` or `feedback`. |
| `data/skill_registry.json` | Local skill registry used by recommendation and promotion. |
| `data/plugin_metadata.json` | Plugin-backed capability metadata used as recommendation candidates. |
| `data/recommender_weights.json` | Ranker weight configuration. |
| `data/evolution_memory.json` | Success templates and error/validation/duplicate/promotion patterns. |
| `data/mining_snapshots/YYMMDD/` | Human-readable mining snapshots for reports and demos. |
| `data/knowledge_graph/review_queue.jsonl` | Pending/accepted/rejected KG candidate facts and claims with reviewer labels. |
| `data/knowledge_graph/current/` | Active accepted KG entities, triples, claims, and evidence paths. |
| `data/knowledge_graph/YYMMDD/` | Chinese editable KG workbench and exports with entities, triples, claims, graph edges, HTML editor, confidence summary, and evidence paths. |
| `.skillminer/tool_events.jsonl` | Local tool event log, ignored by git. |
| `outputs/reports/*.json` | Ingest, mining, recommendation, validation, promotion, evolution, and evaluation reports. |
| `outputs/candidate_skills/<cluster>/` | Generated and evolved skill candidates. |

## Trace Format

Each JSONL trace contains task, project, tool, command, outcome, and optional skill labels:

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

## Development Checks

```powershell
python -m pytest -q
python -m skillminer.cli evaluate --variant evolved --top-k 3 --no-tool-events
```

Expected core invariant for the current phase:

```text
safety_false_negative_rate == 0.0
```

`evaluate --variant evolved` also reports seed vs local evolved held-out deltas, failed evolved-candidate recommendation reasons, per-cluster usefulness status, raw augmented-registry diagnostics, and `memory_summary` counts for validation, duplicate, and promotion feedback. The Phase 2 gate uses a stable overlay: existing skill recommendation order is preserved while candidate discoverability is measured separately.

`evaluate-gepa` writes `outputs/reports/gepa_skill_optimization.json`, compares seed/local evolved/GEPA candidates for one cluster, and keeps `safety_false_negative_rate == 0.0` as a hard adoption gate.

`evaluate-gepa-phase4` 会写入 `outputs/reports/gepa_phase4_experiments.json`，包含每个条件的 `memory_policy`、`racing_policy`、`judge_policy`、调用次数、耗时、held-out 指标、安全率和 adoption status。dry-run 模式下，`not_applicable` adoption row 是预期结果，因为不会生成真实 GEPA candidate；真实运行里，`not_adopted` row 是实验证据，不是命令失败。

Current sample-corpus Phase 2 result:

```text
heldout_usefulness_status == improved
heldout_candidate_discovery_status == improved
heldout_recommendation_status == neutral
heldout_evolved_candidate_top_k_hit_rate_delta == 0.1428
safety_false_negative_rate == 0.0
evolved_verifier_pass_rate == 1.0
```

`raw_evolved_mrr_delta` is still reported as a diagnostic because a fully augmented temporary registry can perturb shared TF-IDF/graph ranking context. It is not the Phase 2 gate metric unless candidates are actually promoted into the registry.

## Documentation Map

- `docs/DESIGN.md`: architecture and implementation responsibilities.
- `docs/HANDOFF.md`: current state, commands, verification, and known limits.
- `docs/AUTONOMOUS_EVOLUTION_LOOP.md`: skill self-evolution operating loop and phase plan.
- `docs/HANDOFF_ADVANCED_SKILL_EVOLUTION.md`: next engineering tasks for quality hardening and GEPA adapter work.
- `docs/GEPA_SKILL_EVOLUTION_GUIDE.md`: GEPA integration design.
- `docs/talk_whit_GEPA.md`: APO/GEPA cost-reduction research notes.
