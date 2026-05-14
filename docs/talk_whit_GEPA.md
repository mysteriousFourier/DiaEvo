# 低成本 GEPA / APO 备忘

## 背景

本文记录如何让 GEPA 在 DiaEvo 中变得实用。

DiaEvo 是一个集成本地 CLI 工作台。GEPA 不应成为独立研究脚本，而应作为 CLI 技能自演化循环中的可选优化器。核心工程问题是成本：如何获得 GEPA 级别的 reflection 和 Pareto search，同时避免过多 rollout、judge 和 token 消耗。

## APO 相关方向

| 方向 | 例子 | 机制 | 对 DiaEvo 的意义 |
| --- | --- | --- | --- |
| 文本梯度 / 反馈编辑 | TextGrad、ProTeGi、PromptWizard | LLM 生成诊断反馈或伪梯度，再编辑文本。 | 与 GEPA 的 ASI 和 reflection 思路一致。 |
| 演化搜索 | PromptBreeder、EvoPrompt、CAPO | 维护 prompt population，并在迭代中 mutation/selection。 | GEPA 提供更强的 Pareto frontier 和 merge 行为。 |
| 记忆 / 经验复用 | MemAPO、REMO、ExpeL | 把优化经验当作跨任务可复用记忆。 | 可以把 DiaEvo 的 validation、duplicate、promotion 反馈带到后续优化。 |
| 低成本前向或选择方法 | PMPO、MoPPS、LatentPrompt | 用 forward loss、bandit 或 pre-screening 减少完整 rollout 和 judge 调用。 | 控制 GEPA token、延迟和成本。 |

## MemAPO 风格记忆

核心思想：不要把每次优化当作孤立事件，而是保存可复用经验。

DiaEvo 映射：

- CTM：Correct-Template Memory
  - 成功章节模板
  - 有用触发模式
  - 好的 fallback / safety 表达
  - 已通过的 validation suggestions
- EPM：Error-Pattern Memory
  - verifier failures
  - validation failures
  - duplicate / merge 问题
  - 人工拒绝原因

当前项目中，`data/evolution_memory.json` 已保存 `correct_templates`、`error_patterns`、`validation_patterns`、`duplicate_patterns` 和 `promotion_patterns`。后续重点是更好的检索与摘要，让 memory 成为 GEPA seed context 和 ASI。

推荐集成：

```text
retrieve CTM/EPM for current cluster
  -> include memory in seed candidate and evaluator ASI
  -> GEPA optimizes
  -> store successful sections in CTM
  -> store failures/rejections in EPM
```

## CAPO 风格 Racing

核心思想：尽早拒绝弱候选，而不是完整评估每个候选。

DiaEvo 可先运行便宜检查：

- 必需章节是否完整
- 是否含危险命令
- 是否含凭据模式
- 长度是否超限
- 是否有 mined evidence 覆盖
- 是否超过 duplicate threshold

只有通过便宜检查的候选才进入昂贵 validation 或 judge。还应加入长度/成本惩罚，避免技能文本膨胀。

推荐流程：

```text
candidate proposed
  -> cheap local gates
  -> clearly bad 时拒绝并返回 ASI
  -> plausible 时运行 fuller evaluator
```

## PMPO / MoPPS 风格成本控制

核心思想：避免每个候选都进行完整生成或 judge。

DiaEvo 可近似实现：

- 使用本地指标作为 dense inner-loop scoring。
- 用不确定性启发式决定何时调用 LLM judge：
  - metric volatility
  - usefulness 和 safety 信号冲突
  - near-duplicate ambiguity
  - verifier pass 但 held-out regression
- 候选和反馈足够多后，再考虑 bandit allocation。

建议策略：

```text
80-90% local metric evaluation
10-20% sparse judge/reflection calls
```

具体比例应通过实验测量，而不是预设。

## Dense Metric Inner Loop + Sparse Judge Outer Loop

这是 DiaEvo 的实际策略。

内循环：

- verifier pass/fail
- warning cleanliness
- evidence alignment
- duplicate score
- specificity
- length
- validation metadata quality
- held-out recommendation proxy

外循环：

- 只有本地指标不足时才调用 LLM-as-judge。
- 只有 GEPA 需要文本诊断和 mutation 时才调用 reflection model。
- 人工审核仍是 promotion 的最终决定。

推荐分阶段接受：

```text
local hard gates
  -> local metric improvement
  -> sparse judge if uncertain
  -> held-out check
  -> human promotion
```

## 优化变量

DiaEvo 的首批变量不是代码，而是结构化文本章节：

```text
when_to_use
trigger_signals
operating_steps
failure_fallbacks
verification_suggestions
safety_constraints
```

后续变量：

- cluster-to-skill generator policy
- validation suggestion policy
- graph-to-skill synthesis policy
- recommendation/reranking policy
- patch guidance text
- sandbox-only code patches

## 目标函数

应使用多目标 Pareto，而不是单一平均分：

```text
maximize usefulness
maximize evidence coverage
maximize specificity
maximize non-duplication
maximize validation quality
minimize safety risk
minimize length/cost
```

硬约束：

- 无危险命令
- 无凭据
- 无 workspace 外写入
- 无自动安装
- 无自动晋升
- validation 和 promotion 必须审批

## 推荐低成本 GEPA 循环

```text
输入：
  seed candidate
  mined cluster/traces
  CTM/EPM memory
  budget

循环：
  1. 检索与 cluster 相关的 memory。
  2. 从 Pareto frontier 选择 parent。
  3. GEPA 提出 child mutation。
  4. 运行 cheap hard gates 和 CAPO-style racing。
  5. 在 train examples 上运行本地指标。
  6. 只有高不确定性或指标波动时触发 sparse judge。
  7. 硬门通过且 Pareto 目标改善时接受。
  8. 在 held-out examples 上评估。
  9. 更新 frontier。
  10. 更新 CTM/EPM。

输出：
  best candidate
  comparison report
  cost report
  memory updates
```

## Phase 4 实验协议

Phase 4 应作为受控研究阶段。目标不是证明更大 GEPA budget 必然更好，而是找出哪些控制变量能在保持安全不变量的前提下，提高单位成本的 held-out usefulness。

待检验假设：

| 假设 | 衡量方式 |
| --- | --- |
| CTM/EPM memory 改善 GEPA 起点。 | 同预算下 held-out candidate hit rate 或 MRR 更高。 |
| CAPO-style racing 减少浪费调用。 | 每个未拒绝候选的 metric/reflection calls 更低且 usefulness 不降。 |
| Sparse judge 对模糊候选有帮助。 | 只在 near-duplicate、metric-disagreement 或 held-out-regression 场景改善 adoption decision。 |
| 更大 budget 只有在 ASI 强时才有效。 | budget sweep 提升 usefulness per cost，而不是只提升 verifier 分数。 |

建议矩阵：

| 条件 | Memory | Racing | Judge | Budgets |
| --- | --- | --- | --- | --- |
| local baseline | current local memory | n/a | none | n/a |
| GEPA seed only | none | off | none | 5, 10 |
| GEPA memory | CTM+EPM | off | none | 5, 10, 25 |
| GEPA racing | CTM+EPM | on | none | 10, 25 |
| GEPA sparse judge | CTM+EPM | on | uncertainty only | 10, 25 |

每个实验行应写出稳定记录：

```json
{
  "condition": "gepa_racing",
  "cluster_id": "C03",
  "budget": 25,
  "memory_policy": "ctm_epm",
  "racing_policy": "cheap_gates",
  "judge_policy": "none",
  "metric_calls": 0,
  "reflection_calls": 0,
  "judge_calls": 0,
  "elapsed_sec": 0.0,
  "heldout": {},
  "safety_false_negative_rate": 0.0,
  "adoption_status": "not_adopted",
  "not_adopted_reason": ""
}
```

分析规则：

- 每个条件都与 `local_evolved` 和上一个更便宜 GEPA 条件比较。
- verifier-only gain 不足以采用。
- `not_adopted` 是有价值证据，不是失败运行。
- 当 held-out usefulness 持平且 duplicate/safety pressure 上升时停止增加预算。
- 本地指标没有不确定性或冲突前，不增加 sparse judge。

## 实用默认值

| 设置 | 默认 |
| --- | --- |
| Artifact | 结构化 `SKILL.md` 章节 |
| Train examples | 挖掘簇和任务轨迹 |
| Held-out examples | 先用确定性 split，后续再加 time/cluster split |
| Candidate selection | Pareto |
| Acceptance | 严格改善 + 硬安全门 |
| Metric calls | 首批 smoke run 使用 50-100 |
| Parallel | 沙盒隔离稳定前不开启 |
| Judge | 只做 sparse judge |
| Reflection model | 预算内最强模型 |
| Task model | CLI 当前模型，未来可拆成更小 replay model |
| Memory | 先通过 `FeatureStore` 检索 CTM/EPM |

## 成本报告指标

GEPA adapter 应报告：

- local metric evaluations
- GEPA reflection calls
- LLM judge calls
- validation command calls
- token usage
- elapsed time
- candidates accepted/rejected
- cost per held-out improvement
- cost per human-accepted candidate

当前报告：

- `diaevo evaluate-gepa` 写入 `outputs/reports/gepa_skill_optimization.json`。
- `diaevo evaluate-gepa-phase4` 写入 `outputs/reports/gepa_phase4_experiments.json`。
- 每行记录 `condition`、`budget`、`memory_policy`、`racing_policy`、`judge_policy`、调用次数、token、耗时、held-out 指标、安全率和 adoption status。
- `--dry-run` 不导入或调用 GEPA，适合作为 CI-safe 检查。

## 现在不要做

- 不要每轮都调用 LLM-as-judge。
- 不要让 GEPA 成为普通 CLI 使用的必需依赖。
- 不要在 sandbox replay 稳定前优化生产代码。
- 不要自动 promotion GEPA 候选。
- 不要自动安装外部技能。
- 不要让加权分数覆盖安全门。

## 成功定义

短期：

- GEPA 或本地演化使用 memory 并返回更丰富 ASI。
- 候选质量报告可执行。

中期：

- GEPA 以可接受成本改善 held-out usefulness。
- CTM/EPM memory 减少重复错误。
- duplicate / merge 决策改善审核质量。

长期：

- DiaEvo 成为低成本自改进技能工作台：真实使用产生 traces，traces 产生 candidate skills，GEPA 改进它们，DiaEvo 验证和推荐，人工治理 promotion，结果进入下一轮。

## 参考资料

- Agrawal, L. et al. GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning. arXiv:2507.19457.
- GEPA AI. Optimize Anything API. https://gepa-ai.github.io/gepa/api/optimize_anything/optimize_anything/
- GEPA AI. LiteLLM adapter `make_litellm_lm`. https://gepa-ai.github.io/gepa/api/optimize_anything/make_litellm_lm/
- Zehle, S. et al. Cost-Aware Prompt Optimization. Proceedings of Machine Learning Research 293, 2025.
- Liang, J. et al. Generalizable Self-Evolving Memory for Automatic Prompt Optimization. arXiv:2603.21520.
- Zhao, Z. et al. Probabilistic Metric Prompt Optimization for Small and Large Language Models. Findings of EMNLP 2025.
