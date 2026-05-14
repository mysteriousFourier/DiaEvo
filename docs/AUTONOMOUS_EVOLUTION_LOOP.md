# Autonomous Skill Evolution Loop

## Purpose

This document defines the autonomous skill-evolution target for SkillMiner.

SkillMiner is an integrated CLI workbench. The autonomous loop is not a separate service: it is the learning layer behind the CLI. The CLI records real tool use and task outcomes, mines reusable patterns, recommends skills, generates candidates, evolves them, verifies safety, validates behavior, queues human review, and feeds outcomes back into the next run.

The final skill-evolution effect is:

```text
SkillMiner gets better at helping the user because the CLI learns reusable skills from the user's own tasks.
```

## Current Implemented Loop

```text
tool_events
  -> ingest / feedback
  -> mine
  -> recommend existing skills
  -> generate candidate SKILL.md
  -> evolve candidate sections
  -> verify
  -> validate
  -> queue-promotion
  -> promote to local registry
  -> evaluate
  -> feed outcomes into future traces and memory
```

The current optimizer is local metric/Pareto. It is conservative and dependency-free. It exists to prove the evaluator, memory, and safety gates before adding GEPA.

## Final Target

The final target is a safe self-evolving skill loop inside the CLI:

- The user works through `.\skillminer.ps1`.
- Local tool use is captured as structured traces.
- SkillMiner mines repeated workflows and failure modes.
- The recommender suggests relevant existing skills before the user repeats work.
- Coverage gaps produce candidate skills with mined evidence.
- GEPA or the local optimizer improves structured skill text using rich ASI.
- Validation and held-out traces prove usefulness.
- Duplicate and merge policies prevent skill-library clutter.
- Human review decides promotion.
- Promoted skills influence future recommendations and future generation.

The system should eventually optimize skills, generator policy, validation suggestions, and graph-to-skill policy. Code evolution is only a later research phase after disposable sandbox replay and human labels are stable.

## Phase Plan

Current source-of-truth checkpoint: **Phase 6 human feedback learning is complete; Phase 7 safe code-evolution research is next**. Older Phase 4/Phase 5-start checkpoint text below is historical.

Historical Phase 4/5 checkpoint is obsolete; use the source-of-truth checkpoint above.

### Phase 0: Integrated CLI Foundation

Goal: one local command surface for use and learning.

Implemented capabilities:

- `skillminer.ps1` interactive shell and scriptable CLI.
- DeepSeek chat with local tool schemas.
- Approval-gated workspace tools.
- Tool event logging to `.skillminer/tool_events.jsonl`.
- Dashboard and slash command menu.

Completion criteria:

- Read-only tools run directly.
- Gated tools preview and require approval.
- Tool events are sanitized and ingestible.

### Phase 1: Conservative Skill Loop

Goal: complete the basic generate/evolve/verify/validate/promote/evaluate loop without external install or code mutation.

Implemented capabilities:

- Trace ingestion and mining.
- Candidate `SKILL.md` generation.
- Local metric/Pareto evolution.
- Verifier safety checks.
- Approval-gated validation replay.
- Human promotion queue and local registry update.
- Baseline/evolved evaluation reports.

Completion criteria:

- `python -m pytest -q` passes.
- `skillminer evaluate --variant evolved` writes metrics.
- Safety false-negative rate remains `0.0`.
- Promotion stays manual.

### Phase 2: Candidate Quality Hardening

Goal: make evolved skills demonstrably better before widening automation.

Implemented:

- Deterministic held-out trace split.
- Held-out recommendation usefulness metrics.
- Actionable duplicate checks against registry and candidates.
- Validation feedback into evolution memory.
- Promotion feedback into evolution memory.
- Baseline-vs-evolved candidate comparisons.
- Stable overlay recommendation gate plus raw augmented-registry diagnostics.

Completion criteria:

- Evolved candidates improve at least one held-out usefulness metric.
- Duplicate recommendations are actionable: keep, specialize, merge, or reject.
- Validation failures produce reusable memory patterns.
- Safety false-negative rate remains `0.0`.

Current sample-corpus result:

```text
heldout_usefulness_status == improved
heldout_candidate_discovery_status == improved
heldout_recommendation_status == neutral
safety_false_negative_rate == 0.0
```

### Phase 3: Optional GEPA Adapter

Goal: add GEPA as an optimizer backend behind the existing SkillMiner evaluator.

Target module:

```text
skillminer/gepa_adapter.py
```

Target command:

```text
skillminer evaluate-gepa --cluster-id C03 --budget 50
```

Evaluator contract:

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

Completion criteria:

- GEPA output is written to `outputs/reports/gepa_skill_optimization.json`.
- GEPA candidates beat seed/local candidates on held-out usefulness.
- GEPA candidates do not regress verifier pass rate or safety false-negative rate.
- GEPA remains optional and dependency-gated.

### Phase 4: Low-Cost GEPA/APO

Goal: make GEPA affordable enough for repeated CLI use through controlled experiments, not by assuming a single cost strategy will work.

当前实现状态：

- `evaluate-gepa-phase4` 已存在，并会写入 `outputs/reports/gepa_phase4_experiments.json`。
- 当前报告是 C02、budget 5 的 dry-run 矩阵，包含 7 行：`local_evolved`、`gepa_seed_only`、`gepa_ctm`、`gepa_epm`、`gepa_ctm_epm`、`gepa_racing`、`gepa_sparse_judge`。
- 当前报告没有 failures，所有行都保持 `safety_false_negative_rate == 0.0`。
- 所有 adoption status 都是 `not_applicable`，因为 dry-run 模式不会生成真实 GEPA candidate。
- 这足以进入 Phase 5 sandbox validation；真实 non-dry-run cost sweep 是可选后续证据。

Use `docs/talk_whit_GEPA.md` as the research source:

- MemAPO-style memory:
  - CTM: correct-template memory.
  - EPM: error-pattern memory.
- CAPO racing:
  - early reject poor candidates before full evaluation.
  - length penalty to prevent prompt/skill bloat.
- PMPO/MoPPS-style selection:
  - cheap pre-screening or bandit rollout selection.
- Evaluation layering:
  - dense automatic metric inner loop.
  - sparse LLM-as-judge outer loop.

Research protocol:

- Treat Phase 4 as an experiment suite over GEPA controls, budgets, and memory inputs.
- Keep one fixed evaluation split per experiment batch so seed, local evolved, and GEPA remain comparable.
- Change one main variable at a time: memory context, racing policy, budget, judge policy, or candidate selector.
- Record both success and failure cases, including `not_adopted` runs, because repeated failure patterns are EPM input.
- Do not promote any GEPA candidate from Phase 4 automatically; promotion remains a later human decision.

Initial experiment matrix:

| Condition | Purpose |
| --- | --- |
| `local_evolved` | Conservative baseline. |
| `gepa_seed_only` | Measure GEPA with the current seed and no extra memory summary. |
| `gepa_ctm_epm` | Test whether retrieved memory improves GEPA starts. |
| `gepa_racing` | Test whether cheap hard gates reduce wasted metric calls. |
| `gepa_sparse_judge` | Test whether judge calls help only on uncertain candidates. |
| `gepa_budget_sweep` | Compare budgets such as 5, 10, 25, and 50 under the same split. |

Completion criteria:

- Cost per accepted useful candidate is tracked.
- Most candidate filtering happens through local metrics.
- LLM judge calls are sparse and justified by uncertainty or metric volatility.
- Memory reuse improves later GEPA starts.
- The experiment report identifies which control improved usefulness per cost and which controls should be rejected or deferred.

### Phase 5: Disposable Sandbox Validation

Goal: make validation replay safe enough for richer ASI and future patch guidance.

Status: complete. Phase 5 and Phase 6 no longer need a handoff owner; the next handoff owner starts at Phase 7. Phase 5 did not require waiting for a real non-dry-run GEPA cost win; sandbox validation was the safety blocker for richer replay and future code evolution.

Required behavior:

- Copy workspace into `.tmp/validation-runs/<id>`.
- Run validation commands in the copy.
- No network by default.
- Timeout every command.
- Capture stdout, stderr, return code, duration, touched files, and diff.
- Never apply sandbox changes to the real workspace automatically.

Completion criteria:

- Validation feedback can include diff and touched-file evidence.
- Failed validation leaves the real workspace untouched.
- GEPA can use richer command output as ASI without direct production mutation.

Implemented behavior:

- Approved validation creates `.tmp/validation-runs/<id>/workspace`.
- The sandbox copy excludes `.git`, `.venv`, `.tmp`, caches, and recursive report outputs.
- The report captures stdout, stderr, exit code, duration, touched files, and diff artifacts.
- Sandbox changes are never applied back to the real workspace automatically.

### Phase 6: Human Feedback Learning

Goal: make promotion review outcomes train the system.

Add labels:

- accepted
- rejected
- merge-needed
- too-broad
- duplicate
- unsafe
- useful-after-use
- not-useful-after-use

Feed labels into:

- evolution memory
- duplicate policy
- recommendation scoring
- GEPA ASI
- generator policy evaluation

Completion criteria:

- Promotion reports compare baseline vs evolved vs GEPA candidates.
- Human labels affect future retrieval and candidate scoring.
- Promotion remains manual.
- `rewrite-promotion` emits explicit merge/specialize/reject_duplicate draft artifacts without updating the registry.
- Validation sandbox diff/touched-file evidence is retained as ASI for later phases.

Status: complete.

### Phase 7: Safe Code-Evolution Research

Goal: explore GEPA/gskill-style code or patch evolution only after the skill loop is proven.

Allowed sequence:

1. Skill text only.
2. Validation metadata suggestions.
3. Natural-language patch strategy.
4. Sandbox-only code patches.
5. Human-reviewed application to real workspace.

Required controls:

- disposable sandbox
- deterministic tests
- patch diff capture
- automatic rollback inside sandbox
- no network by default
- no workspace-external writes
- final human review

This phase is out of scope until held-out usefulness, sandbox replay, and human feedback labels are stable.

## ASI Sources

Actionable Side Information should include:

- mined cluster summary
- representative task
- trace IDs
- top terms/tools/errors/failure types
- frequent sequences
- association rules
- graph neighborhoods
- verifier findings
- validation command output
- duplicate nearest match and action
- promotion labels
- held-out recommendation failures
- safety holdout results

Weak ASI says "failed". Strong ASI says what failed, where it came from, and what edit direction is useful.

## Operating Policy

1. Ingest real traces and tool events before mining.
2. Generate/evolve only from mining entrypoints or explicit cluster IDs.
3. Keep generated skills as drafts.
4. Require verifier pass before promotion review.
5. Require approval for validation execution.
6. Require human approval for promotion.
7. Do not install external skills automatically.
8. Do not mutate production code through the optimizer.
9. Do not adopt GEPA results unless held-out usefulness improves and safety does not regress.
10. Record before/after metrics for every algorithmic change.

## Current Metrics To Watch

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
- human acceptance rate, once labels exist

## Success Definition

Short term:

- The CLI can mine, generate, evolve, verify, validate, queue, promote, and evaluate skills.
- Evolved skills are safe and measurable.

Medium term:

- Evolved skills improve held-out usefulness.
- Duplicate/merge decisions reduce library clutter.
- Validation and promotion feedback improve future candidates.

Long term:

- GEPA-backed skill evolution reliably produces skills that the CLI recommends and users accept.
- The skill library improves from real use without weakening safety gates.
- SkillMiner becomes a practical local CLI workbench that gets better the more it is used.
