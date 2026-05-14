# Advanced Skill Evolution Handoff

## Mission

Advance SkillMiner from a conservative local skill-evolution loop to reliable, GEPA-ready skill self-evolution inside the integrated CLI.

Do not widen into external skill installation, automatic promotion, or direct production-code evolution. The next owner starts at Phase 7 safe code-evolution research; keep promotion manual and keep any code or patch work sandbox-only until human review.

Historical checkpoints before Phase 6 are obsolete; use the latest checkpoint below as the source of truth.

Update: `gepa==0.1.1` and `litellm==1.83.14` are installed in the project `.venv`. A real `evaluate-gepa --cluster-id C03 --budget 2 --top-k 3 --no-tool-events` smoke run completed through DeepSeek API. The result was `not_adopted` because it did not beat local evolved usefulness, while `safety_false_negative_rate` remained `0.0`.

Latest checkpoint: **Phase 6 human feedback learning is complete; Phase 7 safe code-evolution research is next**. This supersedes the older Phase 4/Phase 5-start checkpoint lines above. Approved validation now runs in disposable workspace copies under `.tmp/validation-runs/<id>/workspace`, captures stdout/stderr/exit code/duration/touched files/diff, and never applies sandbox changes back to the real workspace automatically.

## Current Baseline

Implemented loop:

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

Implemented modules:

- `skillminer/evolution.py`: local metric/Pareto optimizer and evolution memory.
- `skillminer/quality.py`: duplicate detection and action recommendations.
- `skillminer/validation_runner.py`: approval-gated validation replay.
- `skillminer/promotion.py`: human promotion queue and local registry update.
- `skillminer/evaluation.py`: baseline/evolved/held-out/safety metrics.
- `data/evolution_memory.json`: correct templates and error/validation/duplicate/promotion patterns.

Hard boundaries:

- No external skill auto-install.
- No optimizer-driven production-code mutation.
- No full autonomous promotion.
- No network/dependency installation during validation unless a future explicit policy allows it.
- GEPA remains optional until the evaluator is strong enough.

## Completed Phase 2 Priorities

### 1. Make Held-Out Usefulness Actionable

Implemented:

- Add per-cluster held-out usefulness summaries.
- Show which traces failed to recommend the evolved candidate and why.
- Compare seed, local evolved, and future GEPA candidates under the same held-out split.
- Keep safety regression cases outside all optimization/training splits.
- Use a stable overlay gate so draft candidates do not disturb installed skill ranking.
- Retain raw augmented-registry deltas as diagnostics.

Current sample-corpus result:

- `heldout_usefulness_status == improved`
- `heldout_candidate_discovery_status == improved`
- `heldout_recommendation_status == neutral`
- `heldout_evolved_candidate_top_k_hit_rate_delta == 0.1428`
- `safety_false_negative_rate == 0.0`

### 2. Add Section-Aware Merge And Specialization

Implemented:

- For `specialize`, add narrower activation rules and clearer trigger signals.
- For `merge`, produce a reviewer-facing section merge proposal.
- Merge `Failure Fallbacks` and `Safety Constraints` conservatively.
- Merge `Operating Steps` only when tool paths and validation commands are compatible.
- Preserve trace IDs, source clusters, and mined evidence.

Remaining:

- Add an explicit merge/specialize rewrite command; current support produces reviewer-facing proposals only.

### 3. Strengthen Evolution Memory

Implemented:

- Store successful section templates by task family, tool path, failure type, validation status, and promotion outcome.
- Store verifier, validation, duplicate, and human rejection patterns in a consistent schema.
- Retrieve CTM/EPM-like memory with `FeatureStore` before candidate generation/evolution.
- Include memory matches in evaluator ASI.

Remaining:

- Feed promotion labels into recommendation/evolution scoring policy, not only memory retrieval.

## Phase 3 Engineering Priorities

### 4. Prepare Optional GEPA Adapter

GEPA can now be added behind the existing evaluator. Do not replace SkillMiner's mining, verification, duplicate checks, validation, promotion, or reporting.

Implemented scaffold:

- Add `skillminer/gepa_adapter.py`.
- Add `skillminer evaluate-gepa --cluster-id C03 --budget 50`.
- Use DeepSeek OpenAI-compatible API settings from `.env` without writing the raw key to reports.
- Support `--dry-run` for seed/local comparison and safety checks without importing or calling GEPA.
- Convert mining clusters and held-out traces to GEPA examples.
- Render structured candidate dicts into `SKILL.md`.
- Reuse verifier, duplicate checks, evidence alignment, validation feedback, and held-out metrics.
- Return scalar score plus ASI.
- Write `outputs/reports/gepa_skill_optimization.json`.

Remaining tasks:

- Keep GEPA optional for normal CLI usage.
- Real non-dry-run Phase 4 cost sweeps remain optional and separate from Phase 5; do not block sandbox validation on them.

Proposed command:

```text
skillminer evaluate-gepa --cluster-id C03 --budget 50
```

Acceptance:

- GEPA run compares seed, local evolved, and GEPA candidate.
- GEPA is adopted only if held-out usefulness improves and safety false-negative rate stays `0.0`.

### 5. Use Promotion Labels For Policy Learning

Implemented:

- Add label fields: accepted, rejected, merge-needed, too-broad, duplicate, unsafe.
- Feed labels into `data/evolution_memory.json`.
- Add promotion report comparing seed/local evolved/GEPA candidate.
- Require validation status `passed` for `ready_for_manual_promotion`, except future explicitly documentation-only candidates.
- Add `useful-after-use` and `not-useful-after-use` review labels.
- Add `rewrite-promotion` to generate merge/specialize/reject_duplicate draft artifacts without promotion.
- Feed promotion labels into recommendation/evolution scoring, not only memory retrieval.

Remaining:

- Keep promotion explicit and manual.
- Carry the same feedback policy into Phase 7 patch guidance/code evolution research.

## Phase 4 Experimental GEPA/APO Cost Strategy

Use `docs/talk_whit_GEPA.md` as the cost design input. Phase 4 is research work: the next owner should run small controlled experiments and record before/after metrics instead of assuming GEPA will improve from a larger budget alone.

已完成的 dry-run/reporting gate：

- 已完成命令：dry-run 模式的 `evaluate-gepa-phase4`。
- 报告：`outputs/reports/gepa_phase4_experiments.json`。
- 范围：cluster `C02`，budget `5`，7 行。
- 条件：`local_evolved`、`gepa_seed_only`、`gepa_ctm`、`gepa_epm`、`gepa_ctm_epm`、`gepa_racing`、`gepa_sparse_judge`。
- 结果：`failures == []`，所有行 `safety_false_negative_rate == 0.0`。
- Adoption status：每行都是 `not_applicable`，因为 dry-run 模式不会生成真实 GEPA candidate。
- 解释：Phase 4 的 harness/reporting/safety gate 已可用；真实 non-dry-run GEPA budget sweep 是可选证据，不是 Phase 5 的 blocker。

Baseline observation:

- Real GEPA smoke with `--budget 2` completed through DeepSeek.
- Result was `not_adopted`: it did not beat local evolved usefulness.
- Safety remained acceptable: `safety_false_negative_rate == 0.0`.
- This means the next phase should improve search efficiency and ASI quality, not just spend more calls.

Recommended experimental loop:

```text
seed candidate + CTM memory
  -> local metric inner loop
  -> CAPO-style racing rejects weak candidates early
  -> sparse LLM-as-judge only on uncertain/volatile candidates
  -> GEPA updates Pareto frontier
  -> EPM stores failures
```

Experiment variables:

- budget: compare 5, 10, 25, and 50 under the same held-out split before going higher.
- memory: compare no memory, CTM only, EPM only, and CTM+EPM summaries.
- racing: compare full GEPA evaluation against cheap-gate early rejection.
- judge: compare no judge against sparse judge only for near-duplicate, metric-disagreement, or held-out-regression cases.
- selection: compare GEPA default Pareto behavior against local pre-screened parent/candidate selection.

Track:

- metric calls
- reflection calls
- judge calls
- total tokens, if available
- wall time
- cost per accepted candidate
- held-out usefulness per cost
- duplicate/safety rejection count
- `not_adopted` reason distribution

Do not make every GEPA iteration an expensive judge call.

Phase 4 success is not "GEPA always wins." Success is a report that shows which low-cost controls improve held-out usefulness per cost while preserving safety, and which controls should be rejected.

## Phase 5 Start: Disposable Sandbox Validation

Status: complete. `skillminer/validation_runner.py` now creates disposable workspace copies after approval and safety checks, excludes `.git`, `.venv`, `.tmp`, caches, and recursive report outputs, runs `validation.json` commands in the sandbox, and writes report artifacts under `.tmp/validation-runs/<id>/artifacts`.

这是下一阶段，现在可以开始。在 patch guidance 或 code evolution 之前：

- Create disposable workspace copies under `.tmp/validation-runs/<id>`.
- Run validation there.
- Capture diffs and touched files.
- Never apply sandbox changes automatically.

This is the blocker for all code evolution work.

## Suggested Next Sequence

Phase 6 is complete:

1. Promotion labels now feed recommendation/evolution scoring.
2. Reviewer-facing section merge/specialize proposals are now surfaced through `rewrite-promotion`.
3. Promotion remains manual and approval-gated.
4. Sandbox validation artifacts now feed richer ASI for GEPA/local evolution without applying sandbox changes.
5. The next owner can start Phase 7 patch guidance or code evolution research from this baseline.

No Phase 5 work remains in this handoff.
Implemented Phase 4 command surface:

```text
skillminer evaluate-gepa-phase4 --cluster-id C03 --budgets 5,10,25,50 --top-k 3 --no-tool-events
skillminer evaluate-gepa-phase4 --cluster-id C03 --budgets 5,10 --top-k 3 --no-tool-events --dry-run
```

Reports:

- `outputs/reports/gepa_skill_optimization.json`: single `evaluate-gepa` run with embedded experiment row.
- `outputs/reports/gepa_phase4_experiments.json`: Phase 4 matrix report.
- The Phase 4 matrix report is written after every row and resumes completed rows by default; use `--no-resume` to rerun all rows.

## Acceptance Criteria For The Next Phase

- `python -m pytest -q` passes.
- `skillminer evaluate --variant evolved` writes stable metrics.
- `safety_false_negative_rate == 0.0`.
- Evolved verifier pass rate does not regress.
- Duplicate rate decreases or duplicate recommendations become actionable and reviewable.
- Held-out usefulness improves under stable overlay metrics; raw augmented-registry regressions are reported as diagnostics.
- Validation and promotion feedback affect future memory.
- Human promotion remains required.

## References

- `docs/DESIGN.md`: architecture and phase roadmap.
- `docs/AUTONOMOUS_EVOLUTION_LOOP.md`: self-evolution operating loop.
- `docs/GEPA_SKILL_EVOLUTION_GUIDE.md`: GEPA adapter design.
- `docs/talk_whit_GEPA.md`: low-cost APO/GEPA notes.
- GEPA Optimize Anything API: https://gepa-ai.github.io/gepa/api/optimize_anything/optimize_anything/
- GEPA LiteLLM adapter: https://gepa-ai.github.io/gepa/api/optimize_anything/make_litellm_lm/
- PyPI `gepa` package: https://pypi.org/project/gepa/
- LiteLLM docs and package: https://docs.litellm.ai/ and https://pypi.org/project/litellm/
- GEPA paper: https://arxiv.org/abs/2507.19457
- CAPO Cost-Aware Prompt Optimization: https://proceedings.mlr.press/v293/zehle25a.html
- MemAPO Generalizable Self-Evolving Memory: https://arxiv.org/abs/2603.21520
- PMPO Probabilistic Metric Prompt Optimization: https://aclanthology.org/2025.findings-emnlp.795/
