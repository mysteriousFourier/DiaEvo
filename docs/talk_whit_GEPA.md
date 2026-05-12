# Low-Cost GEPA / APO Notes

## Context

These notes summarize the research direction for making GEPA practical inside SkillMiner.

SkillMiner is an integrated local CLI workbench. GEPA should not become a separate research script; it should become an optional optimizer inside the CLI's skill self-evolution loop. The key engineering question is cost: how to get GEPA-quality reflection and Pareto search without spending excessive rollout or judge calls.

## APO Landscape

Automatic prompt optimization has several relevant lines:

| Line | Examples | Mechanism | Relevance to SkillMiner |
| --- | --- | --- | --- |
| Text-gradient / feedback editing | TextGrad, ProTeGi, PromptWizard | LLM produces diagnostic feedback or pseudo-gradients and edits text. | Same spirit as GEPA ASI and reflection. |
| Evolutionary search | PromptBreeder, EvoPrompt, CAPO | Maintain prompt populations and mutate/select over iterations. | GEPA adds stronger Pareto frontier and merge behavior. |
| Memory / experience reuse | MemAPO, REMO, ExpeL | Treat optimization as reusable cross-task experience. | Complements GEPA by carrying lessons across CLI sessions and task families. |
| Low-cost forward or selection methods | PMPO, MoPPS, LatentPrompt | Reduce full rollout and judge calls with forward loss, bandits, or pre-screening. | Helps control GEPA token and latency cost. |

## MemAPO-Style Memory

Core idea: do not treat each optimization as isolated. Store reusable experience.

SkillMiner mapping:

- CTM: Correct-Template Memory.
  - Successful section templates.
  - Useful trigger patterns.
  - Good fallback/safety phrasing.
  - Validation suggestions that passed.
- EPM: Error-Pattern Memory.
  - Verifier failures.
  - Validation failures.
  - Duplicate/merge problems.
  - Human rejection reasons.

Current project mapping:

- `data/evolution_memory.json` already stores `correct_templates`, `error_patterns`, `validation_patterns`, `duplicate_patterns`, and `promotion_patterns`.
- The next step is better retrieval and summarization so memory becomes GEPA seed context and ASI.

GEPA integration:

```text
retrieve CTM/EPM for the current cluster
  -> include memory in seed candidate and evaluator ASI
  -> GEPA optimizes
  -> store successful sections in CTM
  -> store failures/rejections in EPM
```

## CAPO-Style Racing

Core idea: reject weak candidates early instead of fully evaluating every candidate.

SkillMiner mapping:

- Run cheap checks first:
  - required sections
  - dangerous command patterns
  - credential patterns
  - length limit
  - evidence coverage
  - duplicate threshold
- Only candidates that pass cheap checks reach expensive validation or judge.
- Add length/cost penalties so skills do not bloat.

GEPA integration:

```text
candidate proposed
  -> cheap local gates
  -> if clearly bad, reject and return ASI
  -> if plausible, run fuller evaluator
```

## PMPO / MoPPS-Style Cost Control

Core idea: avoid full generation or judge calls for every candidate.

Possible SkillMiner approximations:

- Use local metrics as dense inner-loop scoring.
- Use simple uncertainty heuristics to decide when to call an LLM judge:
  - metric volatility
  - disagreement between usefulness and safety
  - near-duplicate ambiguity
  - held-out regression despite verifier pass
- Use bandit-like allocation later when there are enough candidates and feedback labels.

Recommended policy:

```text
80-90% local metric evaluation
10-20% sparse judge/reflection calls
```

The exact ratio should be measured, not assumed.

## Dense Metric Inner Loop + Sparse Judge Outer Loop

This is the practical SkillMiner strategy.

Inner loop:

- verifier pass/fail
- warning cleanliness
- evidence alignment
- duplicate score
- specificity
- length
- validation metadata quality
- held-out recommendation proxy

Outer loop:

- LLM-as-judge only when local metrics are insufficient.
- Reflection model only when GEPA needs textual diagnosis and mutation.
- Human review remains final for promotion.

Acceptance should be staged:

```text
local hard gates
  -> local metric improvement
  -> sparse judge if uncertain
  -> held-out check
  -> human promotion
```

## Optimization Variables

For SkillMiner, the first variables are not code. They are structured text sections:

```text
when_to_use
trigger_signals
operating_steps
failure_fallbacks
verification_suggestions
safety_constraints
```

Later variables:

- cluster-to-skill generator policy
- validation suggestion policy
- graph-to-skill synthesis policy
- recommendation/reranking policy
- patch guidance text
- sandbox-only code patches

## Objective

A useful objective is multi-objective Pareto, not a single average:

```text
maximize usefulness
maximize evidence coverage
maximize specificity
maximize non-duplication
maximize validation quality
minimize safety risk
minimize length/cost
```

Hard constraints:

- no dangerous commands
- no credentials
- no workspace-external writes
- no auto-install
- no auto-promotion
- validation and promotion remain approval-gated

## Suggested Low-Cost GEPA Loop

```text
Input:
  seed candidate
  mined cluster/traces
  CTM/EPM memory
  budget

Loop:
  1. Retrieve memory relevant to the cluster.
  2. Select parent from Pareto frontier.
  3. GEPA proposes child mutation.
  4. Run cheap hard gates and CAPO-style racing.
  5. Score local metrics on train examples.
  6. Trigger sparse judge only if uncertainty or metric volatility is high.
  7. Accept if hard gates pass and Pareto objective improves.
  8. Evaluate on held-out examples.
  9. Update frontier.
  10. Store CTM/EPM updates.

Output:
  best candidate
  comparison report
  cost report
  memory updates
```

## Practical Defaults

Initial defaults for SkillMiner:

| Setting | Default |
| --- | --- |
| Artifact | Structured `SKILL.md` sections |
| Train examples | Mined cluster/task traces |
| Held-out examples | Deterministic split first, time/cluster split later |
| Candidate selection | Pareto |
| Acceptance | Strict improvement plus hard safety gates |
| Metric calls | 50-100 for first smoke runs |
| Parallel | False until sandbox isolation exists |
| Judge | Sparse only |
| Reflection model | Strongest affordable model |
| Task model | Same CLI model or smaller model after replay exists |
| Memory | CTM/EPM retrieved through `FeatureStore` first |

## Cost Metrics To Report

The GEPA adapter should report:

- local metric evaluations
- GEPA reflection calls
- LLM judge calls
- validation command calls
- tokens, if provider reports them
- elapsed time
- candidates accepted/rejected
- cost per held-out improvement
- cost per human-accepted candidate, once labels exist

## What Not To Do Yet

- Do not call LLM-as-judge every round.
- Do not make GEPA required for normal CLI usage.
- Do not optimize production code before sandbox replay.
- Do not auto-promote GEPA candidates.
- Do not install external skills automatically.
- Do not let a weighted score override safety gates.

## Success Definition

Short term:

- GEPA or local evolution uses memory and returns richer ASI.
- Candidate quality reports become actionable.

Medium term:

- GEPA improves held-out usefulness at acceptable cost.
- CTM/EPM memory reduces repeated mistakes.
- Duplicate/merge decisions improve review quality.

Long term:

- SkillMiner's CLI becomes a low-cost self-improving skill workbench: real usage creates traces, traces create candidate skills, GEPA improves them, SkillMiner verifies and recommends them, humans govern promotion, and outcomes feed the next cycle.
