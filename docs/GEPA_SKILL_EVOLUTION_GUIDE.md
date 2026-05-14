# GEPA 技能演化指南

## 结论

GEPA 是 DiaEvo 技能自演化循环的可选反思式优化器。

DiaEvo 负责 CLI、数据层、evaluator 和安全层。GEPA 不应替代 ingest、mine、verify、validate、recommend、evaluate 或 promote。GEPA 只优化由 DiaEvo 发现、评估和治理的文本制品。

当前项目阶段：**Phase 6 人工反馈学习已完成，Phase 7 安全代码演化研究已开始**。GEPA 仍然是可选依赖；任何 patch guidance 或 code evolution 必须停留在沙盒中，直到人工审查。

推荐架构：

```text
DiaEvo CLI 使用
  -> traces 和 tool events
  -> mining 和 generation
  -> GEPA 优化结构化 skill sections
  -> DiaEvo verify / validate / de-duplicate / evaluate / queue promotion
  -> outcomes 回流到未来 traces 和 memory
```

## 为什么 GEPA 适合

GEPA 适合以下条件：

- 优化对象能表示为文本。
- 示例数量有限但质量较高。
- rollout 或 validation 成本较高。
- evaluator 能返回丰富文本诊断。
- 目标是多目标权衡，而不是单一分数。

DiaEvo 符合这些条件：

- `SKILL.md` 是文本。
- trace 数据稀缺但含有真实任务证据。
- validation、agent replay 和 LLM judge 成本较高。
- verifier、duplicate、validation 和 promotion 报告能构成 ASI。
- usefulness、safety、specificity、non-duplication、length 和 cost 都是目标。

## 首个优化目标

首个 GEPA 目标应是结构化 `SKILL.md` 章节：

```python
candidate = {
    "when_to_use": "...",
    "trigger_signals": "...",
    "operating_steps": "...",
    "failure_fallbacks": "...",
    "verification_suggestions": "...",
    "safety_constraints": "...",
}
```

不要先优化生产代码，也不要先优化整块原始 Markdown。结构化章节让 diff 可审查，并保留 verifier 契约。

## 权责边界

DiaEvo 负责：

- trace normalization
- tool event capture
- cluster mining
- generation entrypoint selection
- `SKILL.md` rendering contract
- verifier 和 validation gates
- duplicate checks
- recommendation ranking
- held-out evaluation
- promotion queue
- memory persistence

GEPA 负责：

- reflective mutation
- candidate pool management
- Pareto frontier selection
- section-aware merge proposals
- optimization budget handling

## Evaluator 合约

GEPA evaluator 应执行以下流程：

```text
candidate sections
  -> render SKILL.md
  -> verify_skill
  -> nearest_duplicate
  -> evidence alignment score
  -> specificity/length score
  -> optional validation feedback lookup
  -> optional held-out usefulness estimate
  -> return aggregate score + structured ASI
```

硬拒绝条件：

- 危险命令模式。
- 疑似凭据内容。
- 缺失必需章节。
- 自动安装指令。
- 自动晋升指令。
- workspace 外写入指令。

初始评分形状：

```text
score =
  verifier
  + required section completeness
  + mined evidence alignment
  + specificity
  + non-duplication
  + validation hint quality
  + length control
  - hard penalties
```

安全是硬约束，不能被 GEPA 用任务成功率交换。

## ASI 形状

GEPA 的质量取决于 ASI 质量。返回具体诊断，不要只返回标量。

推荐 ASI 字段：

```json
{
  "input": {
    "cluster_id": "C03",
    "representative_task": "...",
    "trace_ids": ["T001", "T007"],
    "top_terms": ["pytest", "parser"],
    "top_tools": ["rg", "read", "pytest"],
    "top_failures": ["missing-import"]
  },
  "candidate": {
    "rendered_skill": "...",
    "section_lengths": {}
  },
  "feedback": {
    "verifier": {},
    "duplicate": {},
    "validation": {},
    "heldout": {}
  },
  "scores": {
    "verifier": 1.0,
    "safety": 1.0,
    "evidence_alignment": 0.8,
    "specificity": 0.7,
    "non_duplicate": 0.9,
    "length": 0.8
  },
  "edit_direction": "specialize trigger signals and add pytest-specific fallback"
}
```

好的 ASI 应指出该怎么改：

- 补充缺失章节。
- 收窄过宽触发条件。
- 删除证据不支持的工具。
- 强化审批门。
- 合并 fallback 指导。
- 针对重复项做 specialize。
- 改善 validation suggestion。

## 数据切分

| 切分 | 用途 |
| --- | --- |
| Train | GEPA mutation 和本地指标评分。 |
| Validation / held-out | 选择候选并测量泛化。 |
| Safety holdout | 危险和凭据样例，不直接用于优化。 |

当前 `diaevo/evaluation.py` 已实现确定性 held-out trace split。后续在 trace 规模足够时再加入 time split 和 cluster holdout。

## Pareto 目标

需要分开追踪的目标：

- usefulness / held-out task success
- verifier correctness
- safety
- evidence coverage
- specificity
- non-duplication
- validation quality
- length / cost

不要过早折叠成一个平均值。GEPA 的 Pareto frontier 有价值，因为“最安全候选”和“最有用候选”在早期可能不同。

## Merge 策略

GEPA 可以提出合并候选，但 DiaEvo 必须执行 section-aware merge 规则：

- `Failure Fallbacks` 在不冲突时可以积极合并。
- `Safety Constraints` 必须保守合并。
- `Operating Steps` 只有在工具路径和 validation 命令兼容时才合并。
- 保留 trace IDs 和 source clusters。
- 不合并互相矛盾的 validation 命令。
- 近重复且没有互补价值时，应 reject 或 specialize。

## Adapter 形状

目标文件：

```text
diaevo/gepa_adapter.py
```

目标命令：

```powershell
diaevo evaluate-gepa --cluster-id C03 --budget 50
```

目标报告：

```text
outputs/reports/gepa_skill_optimization.json
```

最小职责：

1. 加载 mining report 和目标 cluster。
2. 用现有 generator/evolution helper 构造 seed candidate。
3. 构造 train 和 held-out examples。
4. 检索 evolution memory。
5. 定义包含 verifier、duplicate、evidence、validation 和 held-out 信号的 evaluator。
6. GEPA 已安装时调用 GEPA。
7. 将 best candidate 渲染到输出目录。
8. 写出 seed vs local evolved vs GEPA 对比报告。

GEPA 必须保持可选。如果依赖或 API key 缺失，命令应清晰失败，不能影响默认 DiaEvo 命令。

DeepSeek provider 策略：

- GEPA reflection/model calls 使用项目 `.env` 中的 DeepSeek 配置。
- 必需配置：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。
- 可选配置：`DEEPSEEK_MAX_TOKENS`、`DEEPSEEK_TEMPERATURE`、`DEEPSEEK_TIMEOUT`。
- 真实 `DEEPSEEK_API_KEY` 只能留在 `.env`，报告可记录 provider/model/base URL 和 `api_key_configured`，但不能记录原始 key。
- `evaluate-gepa --dry-run` 不导入 GEPA，不调用 DeepSeek/GEPA，只检查 seed/local 对比、报告形状和安全门。

## 成本策略

使用 `docs/talk_whit_GEPA.md` 中的低成本 APO 方案：

- dense automatic metric inner loop
- sparse LLM-as-judge outer loop
- CAPO-style racing
- MemAPO-style CTM/EPM memory
- 未来可选 PMPO/MoPPS 风格 pre-screen 或 bandit rollout selection

需要追踪：

- metric evaluations
- reflection calls
- judge calls
- token usage
- wall time
- accepted useful candidates per cost

默认不要对每个候选都调用 judge。

## Benchmark 条件

至少比较：

| 条件 | 说明 |
| --- | --- |
| Seed generated | mined cluster 的 `generate` 输出。 |
| Local evolved | 当前 `evolve` 输出。 |
| GEPA skill text | GEPA 优化后的结构化章节。 |
| GEPA + merge | 未来 section-aware merge 候选。 |

指标：

- verifier pass rate
- safety false-negative rate
- duplicate rate 和 duplicate action distribution
- held-out evolved candidate top-K hit rate
- held-out Precision@K 和 MRR
- recommendation lift
- validation pass rate
- human acceptance rate
- cost per useful accepted candidate

采用规则：

```text
只有 held-out usefulness 改善且 safety false-negative rate 不回退时，才采用 GEPA 候选。
```

只改善 verifier 合规性不够。

## 后续优化目标

技能文本之后，才考虑：

1. Generator policy。
2. Validation metadata。
3. Graph-to-skill policy。
4. Recommendation policy。
5. Patch guidance 自然语言策略。
6. Code evolution，且必须 sandbox-only 和 human-reviewed。

## 失败模式与对策

| 失败模式 | 对策 |
| --- | --- |
| 过拟合少量 trace | 使用 held-out cluster/time split，惩罚硬编码偶发文件。 |
| 只优化 verifier 合规 | 要求 held-out usefulness 和人工接受信号。 |
| reflection 幻觉 | ASI 中包含 mined evidence、trace IDs、重复样例和 unsupported-claim penalty。 |
| 安全回退 | 硬拒绝 unsafe candidate，并保持 safety holdout 不参与优化。 |
| 成本漂移 | 缓存评估、使用 racing、sparse judge 和 cost reports。 |
| 技能库膨胀 | 使用 duplicate actions 和 section-aware merge/specialize 审核。 |

## 成功里程碑

短期：

- GEPA adapter 能在一个 cluster 上低预算运行。
- 报告比较 seed、local evolved 和 GEPA candidate。
- `safety_false_negative_rate == 0.0`。

中期：

- GEPA 候选改善 held-out usefulness。
- duplicate actions 变成可审查的 merge/specialize proposals。
- validation output 和 promotion labels 改善未来候选。

长期：

- DiaEvo 的集成 CLI 从真实使用中挖掘，GEPA 演化技能，DiaEvo 验证和推荐，人工治理 promotion，系统在不削弱安全边界的情况下持续改进。
