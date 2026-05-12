# Advanced Skill Evolution Handoff

## Mission

Advance SkillMiner from a conservative local skill-evolution loop to reliable, GEPA-ready skill self-evolution inside the integrated CLI.

Do not widen into external skill installation, automatic promotion, or production-code evolution yet. Phase 2 has reached the pre-Phase 3 gate on the sample corpus; the next owner can start the optional GEPA adapter behind the existing evaluator.

Current checkpoint: **Phase 2: Quality hardening, pre-Phase 3 gate satisfied**. Phase 0 and Phase 1 are implemented baselines. Phase 2 now provides improved held-out usefulness, actionable duplicate handling, validation feedback, promotion feedback, and reviewable memory signals. GEPA is Phase 3 and remains optional.

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

Tasks:

- Add `skillminer/gepa_adapter.py`.
- Convert mining clusters and held-out traces to GEPA examples.
- Render structured candidate dicts into `SKILL.md`.
- Reuse verifier, duplicate checks, evidence alignment, validation feedback, and held-out metrics.
- Return scalar score plus ASI.
- Write `outputs/reports/gepa_skill_optimization.json`.
- Keep GEPA dependency optional.

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

Remaining:

- Learn from review outcomes in recommendation/evolution scoring while keeping promotion explicit and manual.

## Optional GEPA/APO Cost Strategy

Use `docs/talk_whit_GEPA.md` as the cost design input.

Recommended strategy:

```text
seed candidate + CTM memory
  -> local metric inner loop
  -> CAPO-style racing rejects weak candidates early
  -> sparse LLM-as-judge only on uncertain/volatile candidates
  -> GEPA updates Pareto frontier
  -> EPM stores failures
```

Track:

- metric calls
- judge calls
- total tokens, if available
- cost per accepted candidate
- held-out usefulness per cost
- duplicate/safety rejection count

Do not make every GEPA iteration an expensive judge call.

## Sandbox Blocker

Before patch guidance or code evolution:

- Create disposable workspace copies under `.tmp/validation-runs/<id>`.
- Run validation there.
- Capture diffs and touched files.
- Never apply sandbox changes automatically.

This is the blocker for all code evolution work.

## Suggested Next Sequence

1. Implement `gepa_adapter.py` with a small smoke budget.
2. Compare seed vs local evolved vs GEPA under identical held-out/safety splits.
3. Turn section merge/specialize proposals into an explicit rewrite command.
4. Feed promotion labels into recommendation/evolution scoring.
5. Add low-cost APO controls: racing, sparse judge, memory reuse.
6. Add disposable sandbox validation.
7. Only then consider patch guidance or code evolution.

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
