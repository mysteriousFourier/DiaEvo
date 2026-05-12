# GEPA Skill Evolution Guide

## Bottom Line

GEPA is the intended reflective optimizer for SkillMiner's skill self-evolution loop.

SkillMiner is the integrated CLI, data layer, evaluator, and safety layer. GEPA should not replace ingestion, mining, verification, validation, recommendation, evaluation, or promotion. GEPA should optimize text artifacts discovered and governed by SkillMiner.

Current project phase: **Phase 2: Quality hardening**. GEPA integration is a Phase 3 target, not an implemented feature. The immediate gate is to make the existing local/Pareto evolved skills measurably better on held-out traces with safety false-negative rate fixed at `0.0`.

Recommended architecture:

```text
SkillMiner CLI usage
  -> traces and tool events
  -> mining and generation
  -> GEPA optimizes structured skill sections
  -> SkillMiner verifies, validates, de-duplicates, evaluates, and queues promotion
  -> outcomes feed future traces and memory
```

## Why GEPA Fits

GEPA works best when:

- the artifact is text-representable
- examples are limited
- rollouts are expensive
- evaluation can return rich textual diagnostics
- multiple objectives matter

SkillMiner matches that profile:

- generated `SKILL.md` is text
- traces are scarce but meaningful
- validation and agent replay are expensive
- verifier/duplicate/validation reports are rich ASI
- usefulness, safety, specificity, non-duplication, length, and cost are all objectives

## First Target

The first GEPA target should be structured `SKILL.md` candidate sections:

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

Do not optimize production code first. Do not optimize a raw Markdown blob first. Structured sections keep diffs reviewable and preserve the verifier contract.

## Ownership Boundary

SkillMiner owns:

- trace normalization
- tool event capture
- cluster mining
- generation entrypoint selection
- `SKILL.md` rendering contract
- verifier and validation gates
- duplicate checks
- recommendation ranking
- held-out evaluation
- promotion queue
- memory persistence

GEPA owns:

- reflective mutation
- candidate pool management
- Pareto frontier selection
- section-aware merge proposals
- optimization budget handling

## Evaluator Contract

The GEPA evaluator should do this:

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

Hard reject:

- dangerous command pattern
- credential-like content
- missing required section
- auto-install instruction
- auto-promote instruction
- workspace-external write instruction

Initial scoring shape:

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

Safety remains a hard constraint, not something GEPA can trade away for task success.

## ASI Shape

GEPA quality depends on ASI quality. Return concrete diagnostics, not just a scalar.

Recommended ASI fields:

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

Good ASI should tell GEPA what to change:

- add a missing section
- narrow overbroad triggers
- remove unsupported tools
- strengthen approval gates
- merge fallback guidance
- specialize against a duplicate
- improve validation suggestion

## Data Splits

Use three split concepts:

| Split | Purpose |
| --- | --- |
| Train | Examples used for GEPA mutation and local metric scoring. |
| Validation / held-out | Examples used to choose candidates and measure generalization. |
| Safety holdout | Dangerous and credential cases never optimized against directly. |

Current implementation has deterministic held-out trace splitting in `skillminer/evaluation.py`. Future improvements should add time split and cluster holdout once trace volume supports them.

## Pareto Objectives

Track objectives separately:

- usefulness / held-out task success
- verifier correctness
- safety
- evidence coverage
- specificity
- non-duplication
- validation quality
- length / cost

Do not collapse these too early. GEPA's Pareto frontier is valuable because the best safe candidate and the most useful candidate can differ early in the run.

## Merge Policy

GEPA can propose merged candidates, but SkillMiner should enforce section-aware merge rules:

- Merge `Failure Fallbacks` aggressively when non-conflicting.
- Merge `Safety Constraints` conservatively.
- Merge `Operating Steps` only if tool paths and validation commands are compatible.
- Preserve trace IDs and source clusters.
- Do not merge contradictory validation commands.
- If candidates are near duplicates without complementary value, reject or specialize instead.

## Adapter Shape

Target file:

```text
skillminer/gepa_adapter.py
```

Target command:

```text
skillminer evaluate-gepa --cluster-id C03 --budget 50
```

Target report:

```text
outputs/reports/gepa_skill_optimization.json
```

Minimal adapter responsibilities:

1. Load mining report and selected cluster.
2. Build seed candidate from existing generator/evolution helpers.
3. Build train and held-out examples.
4. Retrieve evolution memory matches.
5. Define evaluator with verifier, duplicate, evidence, validation, and held-out signals.
6. Call GEPA if installed.
7. Render best candidate to an output directory.
8. Write comparison report: seed vs local evolved vs GEPA.

GEPA must remain optional. If dependency or API key is missing, the command should fail clearly without breaking default SkillMiner commands.

## Cost Strategy

Use the low-cost APO plan from `docs/talk_whit_GEPA.md`:

- Dense automatic metric inner loop.
- Sparse LLM-as-judge outer loop.
- CAPO-style racing to stop poor candidates early.
- MemAPO-style CTM/EPM memory to improve future seeds.
- Optional PMPO/MoPPS-like pre-screen or bandit rollout selection later.

Track cost:

- metric evaluations
- reflection calls
- judge calls
- token usage, if provider reports it
- wall time
- accepted useful candidates per cost

Do not run a judge on every candidate by default.

## Benchmark Conditions

Compare at least:

| Condition | Description |
| --- | --- |
| Seed generated | `generate` output from mined cluster. |
| Local evolved | Current `evolve` output. |
| GEPA skill text | GEPA-optimized structured sections. |
| GEPA + merge | Future section-aware merge candidate. |

Metrics:

- verifier pass rate
- safety false-negative rate
- duplicate rate and duplicate action distribution
- held-out evolved candidate top-K hit rate
- held-out Precision@K and MRR
- recommendation lift
- validation pass rate
- human acceptance rate, once labels exist
- cost per useful accepted candidate

Decision rule:

```text
Adopt GEPA only if held-out usefulness improves and safety false-negative rate does not regress.
```

Verifier-only improvement is not enough.

## Later Optimization Targets

After skill text:

1. Generator policy: text instructions for converting clusters into `SKILL.md`.
2. Validation metadata: `validation.json` command suggestions and expected results.
3. Graph-to-skill policy: how graph neighborhoods decide extension vs standalone skill.
4. Recommendation policy: qualitative reranking and risk explanations.
5. Patch guidance: natural-language strategy only.
6. Code evolution: sandbox-only and human-reviewed.

## Failure Modes

| Failure | Countermeasure |
| --- | --- |
| Overfitting tiny traces | Use held-out clusters/time split, penalize hardcoded non-recurring files. |
| Optimizing verifier compliance only | Require held-out usefulness and human acceptance signals. |
| Reflection hallucination | Include mined evidence, trace IDs, duplicate examples, and unsupported-claim penalties. |
| Safety regression | Hard reject unsafe candidates and keep safety holdout untouched. |
| Cost drift | Cache evaluations, use racing, sparse judge, and cost reports. |
| Skill-library clutter | Use duplicate actions and section-aware merge/specialize review. |

## Success Milestones

Short term:

- GEPA adapter runs on one cluster with a small budget.
- Report compares seed, local evolved, and GEPA candidate.
- Safety false-negative rate remains `0.0`.

Medium term:

- GEPA candidates improve held-out usefulness.
- Duplicate actions become reviewable merge/specialize proposals.
- Validation output and promotion labels improve future candidates.

Long term:

- SkillMiner's integrated CLI mines real usage, GEPA evolves skills, SkillMiner verifies/recommends them, humans govern promotion, and the system improves from feedback without weakening safety boundaries.
