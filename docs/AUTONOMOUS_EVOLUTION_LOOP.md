# DiaEvo 自演化循环

## 目标

本文定义 DiaEvo 的技能自演化目标。

DiaEvo 的自演化不是独立服务，而是 CLI 背后的学习层。CLI 记录真实工具使用和任务结果，挖掘可复用模式，推荐已有技能，生成候选技能，演化候选章节，验证安全性，在沙盒中校验行为，进入人工审核队列，并把结果反馈到下一轮。

目标效果：

```text
DiaEvo 越被使用，越能从用户自己的任务中学习可复用技能，并更准确地帮助用户。
```

## 当前已实现循环

```text
tool_events
  -> ingest / feedback
  -> mine
  -> recommend existing skills
  -> generate candidate SKILL.md
  -> evolve candidate sections
  -> verify
  -> validate in disposable sandbox
  -> queue-promotion
  -> rewrite-promotion / promote to local registry
  -> evaluate
  -> outcomes feed future traces and memory
```

当前优化器是本地 metric/Pareto，保守、可复现、低依赖。它用于先证明 evaluator、记忆和安全门，再接入 GEPA。

## 最终目标

长期目标是在 CLI 内部形成安全的技能自演化闭环：

- 用户通过 `diaevo` 工作。
- 本地工具调用被捕获为结构化轨迹。
- DiaEvo 挖掘重复工作流、失败模式和覆盖缺口。
- 推荐器在用户重复劳动前建议已有技能。
- 覆盖缺口生成带挖掘证据的候选技能。
- 本地优化器或 GEPA 用 ASI 改进结构化技能文本。
- validation 和 held-out traces 证明候选是否有用。
- duplicate 和 merge policy 避免技能库膨胀。
- 人工审核决定是否晋升。
- 被晋升的技能影响后续推荐、生成和演化。

系统未来可以优化技能文本、生成策略、validation 建议、图到技能策略。代码演化只能是后期研究，且必须在一次性沙盒和人工审核稳定之后进行。

## 阶段计划

当前可信检查点：**Phase 6 人工反馈学习已完成，Phase 7 安全代码演化研究已开始**。

### Phase 0：集成 CLI 基础

目标：形成统一的本地使用和学习入口。

已实现：

- `diaevo` 交互式 shell 和脚本化 CLI。
- DeepSeek 聊天和本地工具 schema。
- 审批式 workspace 工具。
- 工具事件写入 `.diaevo/tool_events.jsonl`。
- 终端首页、斜杠菜单和状态动效。

完成标准：只读工具直接运行，高风险工具先预览并审批，工具事件可清洗并进入 ingest。

### Phase 1：保守技能循环

目标：在不安装外部技能、不改写生产代码的前提下完成 generate/evolve/verify/validate/promote/evaluate。

已实现：

- 轨迹 ingest 和 mining。
- 候选 `SKILL.md` 生成。
- 本地 metric/Pareto 演化。
- verifier 安全检查。
- 审批后的沙盒 validation replay。
- 人工 promotion queue 和本地 registry 更新。
- baseline/evolved 评估报告。

完成标准：测试通过，`evaluate --variant evolved` 写出指标，`safety_false_negative_rate == 0.0`，promotion 保持人工。

### Phase 2：候选质量加固

目标：在扩大自动化前，让演化技能在 held-out 轨迹上可证明更有用。

已实现：

- 确定性 held-out trace split。
- held-out recommendation usefulness 指标。
- 对 registry 和候选之间的可操作重复检查。
- validation feedback 写入 evolution memory。
- promotion feedback 写入 evolution memory。
- seed/local evolved/GEPA 候选可在同一 split 下比较。
- stable overlay gate 保持已安装技能排序稳定，同时测量候选 discoverability。

样例语料当前结果：

```text
heldout_usefulness_status == improved
heldout_candidate_discovery_status == improved
heldout_recommendation_status == neutral
safety_false_negative_rate == 0.0
```

### Phase 3：可选 GEPA adapter

目标：把 GEPA 加到现有 evaluator 后面，而不是替代 DiaEvo 的挖掘、验证、重复检查、校验、推荐或晋升。

核心合约：

```text
structured candidate sections
  -> render SKILL.md
  -> verify
  -> duplicate check
  -> evidence alignment
  -> optional validation feedback
  -> held-out usefulness signal
  -> scalar score + ASI
```

已实现 scaffold：

- `diaevo/gepa_adapter.py`
- `diaevo evaluate-gepa --cluster-id C03 --budget 50`
- `--dry-run` 不导入 GEPA，不调用模型。
- 报告写入 `outputs/reports/gepa_skill_optimization.json`。

采用标准：GEPA 候选只有在 held-out usefulness 改善且安全率不回退时才可进入后续人工审核。

### Phase 4：低成本 GEPA/APO

目标：用受控实验找到可重复、低成本的 GEPA 使用方式，而不是假设更大预算必然更好。

已实现：

- `evaluate-gepa-phase4` 会写入 `outputs/reports/gepa_phase4_experiments.json`。
- dry-run 矩阵已覆盖 `local_evolved`、`gepa_seed_only`、`gepa_ctm`、`gepa_epm`、`gepa_ctm_epm`、`gepa_racing`、`gepa_sparse_judge`。
- dry-run 报告保持 `safety_false_negative_rate == 0.0`。
- dry-run 中 adoption status 为 `not_applicable` 是预期结果，因为不会生成真实 GEPA candidate。

实验策略：

- CTM/EPM memory：复用成功模板和错误模式。
- CAPO racing：用便宜硬门提前拒绝差候选。
- PMPO/MoPPS 风格选择：用局部指标和不确定性减少 full rollout 或 judge 调用。
- dense metric inner loop + sparse judge outer loop：大部分候选用本地指标，少数不确定候选用 LLM judge。

Phase 4 成功标准不是“GEPA 总是赢”，而是报告能说明哪些控制变量提升了每单位成本的 held-out usefulness，哪些应延后或拒绝。

### Phase 5：一次性沙盒校验

目标：让 validation replay 足够安全，并为未来 patch guidance 提供更丰富 ASI。

状态：已完成。

已实现行为：

- 审批后创建 `.tmp/validation-runs/<id>/workspace`。
- 沙盒副本排除 `.git`、`.venv`、`.tmp`、缓存和递归报告输出。
- validation 命令在沙盒工作区运行。
- 报告捕获 stdout、stderr、exit code、duration、touched files 和 diff。
- 沙盒变更永远不会自动应用回真实 workspace。

### Phase 6：人工反馈学习

目标：让人工审核结果进入系统记忆和后续评分。

已实现：

- promotion labels：`accepted`、`rejected`、`merge-needed`、`too-broad`、`duplicate`、`unsafe`、`useful-after-use`、`not-useful-after-use`。
- 标签写入 evolution memory。
- validation artifacts、duplicate patterns 和 promotion feedback 进入 ASI。
- `rewrite-promotion` 生成 merge/specialize/reject_duplicate 草稿，不晋升、不改 registry。
- promotion 仍然显式人工审批。

状态：已完成。

### Phase 7：安全代码演化研究

目标：只在沙盒和人工审核稳定后，探索 GEPA/gskill 风格的代码或 patch 演化。

状态：已开始。

已实现入口：

- 不提供 patch 时，`evaluate-code-evolution` 只写自然语言 patch strategy。
- 提供 `--patch-file --approve` 时，候选 unified diff 只在 `.tmp/validation-runs/<id>/workspace` 应用。
- 验证命令在沙盒中运行，报告捕获 stdout/stderr/exit code/duration/touched files/diff。
- 真实 workspace 不会被自动修改。

允许顺序：

1. 技能文本。
2. validation 元数据建议。
3. 自然语言 patch strategy。
4. 沙盒内代码 patch。
5. 人工审查后显式应用到真实工作区。

必需控制：一次性沙盒、确定性测试、patch diff 捕获、默认无网络、禁止 workspace 外写入、最终人工审查。

## ASI 来源

Actionable Side Information 应包含：

- 挖掘簇摘要
- 代表任务
- trace IDs
- top terms / tools / errors / failure types
- 频繁序列
- 关联规则
- 图邻域
- verifier findings
- validation 命令输出
- 最近重复项和建议动作
- promotion labels
- held-out recommendation failures
- safety holdout results

弱 ASI 只说“失败”。强 ASI 会说明失败位置、证据来源和可执行编辑方向。

## 操作策略

1. 先 ingest 真实 traces 和 tool events，再运行 mine。
2. 只从挖掘入口或显式 cluster ID 生成/演化。
3. 生成技能保持 draft。
4. 晋升前必须通过 verifier。
5. validation 执行必须审批，且只在沙盒中运行。
6. promotion 必须人工审批。
7. 不自动安装外部技能。
8. 不让优化器直接改写生产代码。
9. 不采用安全回退或 held-out usefulness 未改善的 GEPA 结果。
10. 每个算法变更都记录前后指标。

## 需要持续观察的指标

- Precision@K
- MRR
- recommendation lift
- coverage-gap hit rate
- verifier pass rate
- evolved verifier pass rate
- candidate duplicate rate
- actionable duplicate count
- held-out evolved candidate top-K hit rate
- held-out MRR delta
- safety false-negative rate
- validation pass/failure categories
- human acceptance rate

## 成功定义

短期：CLI 能挖掘、生成、演化、验证、校验、排队、晋升和评估技能。

中期：演化技能提升 held-out usefulness，重复/合并决策减少技能库混乱，validation 和 promotion feedback 改善后续候选。

长期：GEPA-backed 技能演化稳定产出被 CLI 推荐且被用户接受的技能，技能库从真实使用中变好，并且安全门不被削弱。
