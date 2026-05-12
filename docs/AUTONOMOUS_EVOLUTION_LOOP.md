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

Current checkpoint: **Phase 2: Candidate Quality Hardening, pre-Phase 3 gate satisfied on the sample corpus**. Phase 0 and Phase 1 are implemented. Phase 2 now proves held-out usefulness with stable overlay metrics, duplicate decisions are reviewable, validation and promotion outcomes update memory, and safety false-negative rate remains `0.0`. GEPA belongs to Phase 3 and is not implemented yet.

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

Goal: make GEPA affordable enough for repeated CLI use.

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

Completion criteria:

- Cost per accepted useful candidate is tracked.
- Most candidate filtering happens through local metrics.
- LLM judge calls are sparse and justified by uncertainty or metric volatility.
- Memory reuse improves later GEPA starts.

### Phase 5: Disposable Sandbox Validation

Goal: make validation replay safe enough for richer ASI and future patch guidance.

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
