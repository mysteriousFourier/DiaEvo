# GEPA Skill Evolution Guide

This document explains how GEPA works and how SkillMiner can use the current mining and evaluation pipeline as the data layer for a GEPA-powered skill evolution loop.

## Bottom Line

The integration is feasible.

SkillMiner already has the pieces GEPA needs:

- trace data from `data/*.jsonl` and `.skillminer/tool_events.jsonl`
- mined clusters, coverage gaps, failure hotspots, and high-reuse tool paths
- generated candidate `SKILL.md` drafts
- a verifier that returns structured safety and quality findings
- baseline metrics in `skillminer/evaluation.py`

GEPA supplies the missing piece: a reflective optimizer that repeatedly evaluates a text artifact, reads rich diagnostic feedback, mutates the artifact, and keeps candidates that improve on held-out examples or Pareto objectives.

The right architecture is:

```text
SkillMiner mining/evaluation -> GEPA candidate optimization -> SkillMiner verification/recommendation -> feedback ingestion
```

Do not replace SkillMiner's mining layer with GEPA. Use SkillMiner to discover what should be optimized, and use GEPA to optimize the text artifacts that SkillMiner produces.

## Primary References

- GEPA paper: https://arxiv.org/abs/2507.19457
- GEPA documentation: https://gepa-ai.github.io/gepa/
- GEPA quick start: https://gepa-ai.github.io/gepa/guides/quickstart/
- GEPA `optimize_anything`: https://gepa-ai.github.io/gepa/blog/2026/02/18/introducing-optimize-anything/
- GEPA `optimize_anything` API: https://gepa-ai.github.io/gepa/api/optimize_anything/optimize_anything/
- GEPA `EngineConfig` API: https://gepa-ai.github.io/gepa/api/optimize_anything/EngineConfig/
- GEPA `gskill`: https://gepa-ai.github.io/gepa/guides/gskill/
- GEPA GitHub: https://github.com/gepa-ai/gepa
- DSPy GEPA optimizer docs: https://dspy.ai/learn/optimization/optimizers/

The public GEPA paper and docs report that GEPA performs best when rollouts are expensive, data is scarce, models are API-only, and textual traces are useful. That matches SkillMiner's use case: skill quality is expensive to validate, real traces are limited, and verifier/tool outputs are textual.

## Public Benchmark Evidence

The public GEPA paper reports that GEPA can outperform GRPO and MIPROv2 on several prompt-optimization tasks while using fewer rollouts. Treat those results as evidence that GEPA is a strong text-artifact optimizer, not as proof that it will automatically improve SkillMiner without a local evaluator.

Useful takeaways:

- GEPA is strongest when feedback contains rich textual diagnostics, not just scalar rewards.
- GEPA is especially attractive when rollouts are expensive and examples are limited.
- GEPA can optimize prompts, code, agent architectures, vector graphics, configurations, and other text-representable artifacts through `optimize_anything`.
- GEPA's `gskill` workflow is the closest public reference for repository-specific skill learning and coding-agent behavior improvement.

Local proof still requires a SkillMiner benchmark:

```text
seed SkillMiner candidate vs GEPA-evolved candidate
same clusters
same verifier
same held-out trace set
same safety holdout
same cost budget
```

Do not infer local superiority from external benchmark numbers alone.

## Verified Public API Shape

The current public `optimize_anything` API is:

```python
optimize_anything(
    seed_candidate: str | dict[str, str] | None = None,
    *,
    evaluator: Callable,
    dataset: list | None = None,
    valset: list | None = None,
    objective: str | None = None,
    background: str | None = None,
    config: GEPAConfig | None = None,
)
```

Important `EngineConfig` fields for SkillMiner:

| Field | Use |
| --- | --- |
| `max_metric_calls` | Main evaluation budget. |
| `max_candidate_proposals` | Cap on generated proposals. |
| `candidate_selection_strategy` | `pareto`, `current_best`, `epsilon_greedy`, or `top_k_pareto`. |
| `frontier_type` | Frontier tracking mode. `hybrid` is the `optimize_anything` default; `objective` is useful for multi-objective scalar metrics. |
| `acceptance_criterion` | `strict_improvement` or `improvement_or_equal`. |
| `parallel` / `max_workers` | Parallel evaluator execution. Start with `parallel=False` for repo-local side-effect safety. |
| `cache_evaluation` | Avoid repeated evaluator calls for the same candidate/example pair. |
| `capture_stdio` | Route evaluator `stdout`/`stderr` into ASI for quick experiments. |

`GEPAResult.best_candidate` is the key output to render back into `SKILL.md`.

## What GEPA Optimizes

GEPA can optimize any artifact that can be represented as text and scored by an evaluator:

- prompts
- skill files
- code
- agent architectures
- JSON/YAML policies
- routing or scheduling strategies
- RAG instructions
- verifier or generator templates

For SkillMiner, the first target should be generated `SKILL.md` content, not Python code. Optimizing skills is safer, easier to evaluate, and directly aligned with GEPA's prompt/skill optimization strengths.

## Core Idea

Traditional optimizers often collapse a run into a scalar reward:

```text
candidate -> run -> score
```

GEPA uses richer feedback:

```text
candidate -> run -> score + trace + errors + test output + verifier findings
```

The extra textual diagnostics are called Actionable Side Information, or ASI. GEPA gives this ASI to a reflection model, which diagnoses failures and proposes targeted edits to the candidate.

For SkillMiner, ASI should include:

- cluster summary
- representative task
- mined tool path
- failure hotspot explanation
- verifier findings
- validation command output
- generated patch or operation trace, if available
- safety concerns
- duplicate-similarity evidence

## Algorithm Model

GEPA is best understood as a Genetic-Pareto reflective search loop.

### State

GEPA maintains:

- a pool of candidate artifacts
- evaluation scores for candidates
- per-example or per-objective best candidates
- a Pareto frontier
- traces and ASI from candidate evaluations
- optional merge/refinement history

### Candidate

A candidate is the thing being optimized. It can be a string or a dictionary of named text components.

For SkillMiner, prefer a structured candidate:

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

Then render those fields into `SKILL.md` for verification. This is better than optimizing one large Markdown blob because each section can evolve independently and the verifier contract stays stable.

### Dataset

GEPA has three common modes:

- single-task search: optimize one artifact for one hard problem
- multi-task search: optimize one artifact across a training set
- generalization: optimize on train examples and select on validation examples

SkillMiner should use generalization mode:

```text
train examples: mined traces and cluster tasks
val examples: held-out traces or time-split recent traces
test examples: untouched final evaluation set
```

Use time-based or cluster-heldout splits when possible. Random splits can leak very similar tasks across train and validation.

### Loop

One GEPA iteration is:

1. Select a candidate from the candidate pool, usually from the Pareto frontier.
2. Evaluate it on a minibatch.
3. Capture score, traces, outputs, failures, and ASI.
4. Ask a reflection model to diagnose what failed and propose edits.
5. Mutate one or more text components.
6. Evaluate the new candidate on the minibatch.
7. Accept it if it improves under the configured criterion.
8. Evaluate/update validation scores.
9. Update the Pareto frontier.
10. Optionally merge complementary candidates.
11. Stop when budget, timeout, score threshold, or no-improvement criteria fire.

The important difference from a simple hill climber is the Pareto frontier: a candidate that is not best on average can still survive if it is best for a subset of examples or objectives. That matters for skill evolution because one skill draft may be excellent for dependency repair while another is better for test failure debugging.

### Pseudocode

This is conceptual pseudocode for understanding the algorithm:

```python
candidate_pool = {seed_candidate}
train_scores = {}
val_scores = {}
pareto_frontier = {seed_candidate}

while budget_remaining():
    parent = select_candidate(
        frontier=pareto_frontier,
        strategy="pareto",
    )

    batch = sample_training_examples()
    feedback = []

    for example in batch:
        score, side_info = evaluator(parent, example)
        feedback.append((example, score, side_info))
        train_scores[parent, example.id] = score

    reflection_prompt = build_reflection_prompt(
        objective=objective,
        background=background,
        candidate=parent,
        feedback=feedback,
        best_known_outputs=get_best_known_outputs(),
    )

    child = reflection_lm_propose(parent, reflection_prompt)

    child_feedback = []
    for example in batch:
        score, side_info = evaluator(child, example)
        child_feedback.append((example, score, side_info))
        train_scores[child, example.id] = score

    if acceptance_criterion(parent, child, batch):
        candidate_pool.add(child)

        for val_example in validation_policy(child):
            score, side_info = evaluator(child, val_example)
            val_scores[child, val_example.id] = score

        pareto_frontier = update_pareto_frontier(
            candidates=candidate_pool,
            scores=val_scores or train_scores,
            frontier_type="hybrid",
        )

    if should_merge(pareto_frontier):
        merged = merge_complementary_candidates(pareto_frontier)
        candidate_pool.add(merged)

best_candidate = select_final_candidate(pareto_frontier, val_scores)
```

In SkillMiner terms:

```text
candidate = structured SKILL.md sections
example = mined cluster or held-out trace-derived task
evaluator = render -> verify -> score -> return verifier findings as ASI
reflection = edit candidate sections based on ASI
frontier = candidates that are best for different clusters/objectives
```

## Pareto Frontier

The Pareto frontier protects useful specialists.

Suppose three candidates score like this:

| Candidate | pytest task | CLI task | docs task | Average |
| --- | ---: | ---: | ---: | ---: |
| A | 1.0 | 0.2 | 0.2 | 0.47 |
| B | 0.4 | 0.8 | 0.4 | 0.53 |
| C | 0.3 | 0.3 | 0.9 | 0.50 |

A plain average selector prefers B. A Pareto selector may keep A, B, and C because each dominates on a different example. Later, GEPA can mutate or merge them.

For SkillMiner, this is valuable because skills often specialize by:

- language or framework
- command/tool path
- error type
- approval or safety boundary
- repository structure
- validation command

## Reflection

Reflection is the step where GEPA reads ASI and proposes changes.

Good reflection input is concrete:

```python
side_info = {
    "Input": {
        "Trace ID": "T012",
        "Task": "Fix pytest failure after changing parser",
        "Cluster": "C03",
        "Mined Signals": ["pytest", "parser.py", "assertion-error"],
    },
    "Generated Outputs": {
        "Candidate Skill": rendered_skill_md,
        "Agent Trace": "read_file -> edit_file -> run_shell pytest",
    },
    "Feedback": {
        "Verifier Findings": [
            "missing_required_section",
            "dependency_hint",
        ],
        "Validation Output": "FAILED tests/test_parser.py::test_edge_case",
        "Duplicate Similarity": 0.86,
    },
    "scores": {
        "correctness": 0.0,
        "safety": 0.8,
        "specificity": 0.6,
        "non_duplicate": 0.7,
    },
}
```

Weak ASI says only "failed". Strong ASI says what failed, where, and what evidence was missing.

## Acceptance Criteria

GEPA has built-in acceptance criteria such as strict improvement and equal-or-better exploration. SkillMiner should not use a naive aggregate score alone. Skill evolution is multi-objective:

- task success should improve
- verifier errors must not increase
- safety must not regress
- duplicate rate should decrease
- instruction length should remain bounded
- specificity should improve without overfitting

Recommended first acceptance policy:

```text
Accept candidate if:
1. aggregate score improves on the minibatch, and
2. verifier error count is zero, and
3. safety score does not regress, and
4. candidate length is under the configured limit.
```

Later, allow Pareto acceptance across objectives:

```text
Accept if any objective improves and hard safety constraints still pass.
```

Hard constraints should remain outside the weighted score. A candidate that includes a dangerous command should be rejected even if it improves task success.

## Candidate Selection

Useful selection strategies:

- `pareto`: default choice for skill evolution; samples specialists from the Pareto frontier.
- `current_best`: good for quick smoke tests, but can overfit to the average winner.
- `epsilon_greedy`: useful once there are enough candidates and you want controlled exploration.
- `top_k_pareto`: useful if the frontier becomes too large.

Initial recommendation: use Pareto for real runs and current-best for short smoke tests.

## Merge

GEPA can merge complementary candidates. This is important when two skill drafts learn different lessons:

- Candidate A learns a good pytest reproduction sequence.
- Candidate B learns strong safety fallbacks.
- Merge produces a candidate with both.

For SkillMiner, merge should be section-aware:

- merge `Operating Steps` only when tool paths are compatible
- merge `Failure Fallbacks` aggressively
- merge `Safety Constraints` conservatively
- do not merge contradictory validation commands
- preserve mined evidence references

## Mapping GEPA To SkillMiner

Current SkillMiner loop:

```text
tool_events -> ingest -> mine -> generate -> verify -> recommend -> feedback
```

GEPA-enhanced loop:

```text
tool_events
  -> ingest
  -> mine
  -> generate seed candidate
  -> GEPA optimize candidate sections
  -> render SKILL.md
  -> verify
  -> evaluate held-out tasks
  -> recommend or queue for human promotion
  -> feedback
```

SkillMiner should keep ownership of:

- trace normalization
- cluster mining
- generation entrypoint selection
- static verification
- recommendation ranking
- baseline reporting
- safety gates

GEPA should own:

- reflective mutation
- candidate pool management
- Pareto selection
- candidate merge
- optimization budget handling

## What To Optimize First

### Level 1: Generated Skill Content

Optimize candidate `SKILL.md` sections.

This is the safest first target:

- easy to render
- easy to verify
- easy to diff
- no runtime code execution required
- aligns with GEPA/gskill

Score with:

- verifier pass/fail
- required-section completeness
- safety finding count
- duplicate similarity penalty
- alignment with mined evidence
- optional held-out task success

### Level 2: Generator Template

Optimize the generation policy in `skillminer/generator.py`, but keep the output contract fixed.

Candidate artifact:

```text
Instructions for converting a cluster report into a SKILL.md candidate.
```

Score with:

- average verifier pass rate over generated candidates
- duplicate rate
- coverage of mined evidence
- human-readability score, if available
- held-out task usefulness

This can improve all future generated skills, but it has a larger blast radius than optimizing one candidate.

### Level 3: Recommender Policy

GEPA can optimize a JSON scoring policy or a textual reranking policy, but this should come after more feedback data exists.

Good candidates:

- Pareto reranking prompt
- risk-aware explanation policy
- weight presets for different project types
- promotion threshold policy

For numeric weights alone, Bayesian optimization or grid search may be simpler than GEPA. GEPA becomes useful when the policy includes text rules and qualitative tradeoffs.

### Level 4: Code Evolution

Code evolution is possible but should wait until SkillMiner has:

- sandbox replay
- patch summaries
- validation deltas
- stable task fixtures
- robust rollback/isolation

GEPA's `gskill` path is the closest reference: generate verifiable coding tasks, run an agent with a candidate skill, score pass/fail, and return patch/trace/test output as ASI.

## Fitness Function Design

The evaluator is the most important part of the integration. GEPA optimizes whatever the evaluator rewards.

Recommended first score:

```text
score =
  0.35 * verifier_pass
+ 0.20 * required_section_score
+ 0.15 * mined_evidence_alignment
+ 0.10 * validation_hint_quality
+ 0.10 * non_duplicate_score
+ 0.10 * clarity_score
- hard_penalties
```

Hard penalties:

- dangerous command pattern: reject
- credential-like content: reject
- workspace-external write instruction: reject
- auto-install or auto-promote instruction: reject
- missing required section: large penalty or reject

Use multi-objective side scores even if the aggregate score is scalar:

```python
"scores": {
    "verifier": verifier_score,
    "safety": safety_score,
    "evidence_alignment": evidence_score,
    "non_duplicate": duplicate_score,
    "specificity": specificity_score,
    "cost": cost_score,
}
```

This lets Pareto selection preserve candidates that are strong on different objectives.

## Example GEPA Evaluator Skeleton

This is a design sketch, not a drop-in script:

```python
from pathlib import Path
from typing import Any

from gepa.optimize_anything import optimize_anything, GEPAConfig, EngineConfig, ReflectionConfig

from skillminer.verifier import verify_skill


def render_skill(candidate: dict[str, str], cluster: dict[str, Any]) -> str:
    return "\n".join(
        [
            "---",
            f"name: {cluster['id'].lower()}-gepa-candidate",
            f"description: Trace-optimized skill for {cluster['representative_task'][:120]}",
            "tags: [gepa, candidate]",
            f"source_cluster: {cluster['id']}",
            "status: candidate",
            "---",
            "",
            "# GEPA Candidate",
            "",
            "## When To Use",
            candidate["when_to_use"],
            "",
            "## Trigger Signals",
            candidate["trigger_signals"],
            "",
            "## Operating Steps",
            candidate["operating_steps"],
            "",
            "## Failure Fallbacks",
            candidate["failure_fallbacks"],
            "",
            "## Verification Suggestions",
            candidate["verification_suggestions"],
            "",
            "## Safety Constraints",
            candidate["safety_constraints"],
            "",
        ]
    )


def evaluate_candidate(candidate: dict[str, str], example: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    cluster = example["cluster"]
    skill_md = render_skill(candidate, cluster)

    tmp_dir = Path(example["tmp_dir"]) / cluster["id"]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    verify_result = verify_skill(tmp_dir, write_report=False)
    findings = verify_result.get("findings", [])
    error_count = verify_result.get("error_count", 0)
    warning_count = verify_result.get("warning_count", 0)

    safety_blocked = any(
        item.get("code") in {"dangerous_command", "credential_pattern"}
        for item in findings
    )

    verifier_score = 1.0 if verify_result.get("passed") else 0.0
    warning_score = max(0.0, 1.0 - (warning_count * 0.15))
    evidence_score = score_evidence_alignment(skill_md, cluster)
    duplicate_score = score_non_duplicate(skill_md, example.get("known_skill_texts", []))
    clarity_score = score_clarity(skill_md)

    if safety_blocked:
        aggregate = 0.0
    else:
        aggregate = (
            0.40 * verifier_score
            + 0.20 * warning_score
            + 0.20 * evidence_score
            + 0.10 * duplicate_score
            + 0.10 * clarity_score
        )

    side_info = {
        "Input": {
            "Cluster ID": cluster["id"],
            "Representative Task": cluster.get("representative_task", ""),
            "Top Terms": cluster.get("top_terms", []),
            "Top Tools": cluster.get("top_tools", []),
            "Top Failures": cluster.get("top_failure_types", []),
            "Explanations": cluster.get("explanations", []),
        },
        "Generated Outputs": {
            "Rendered Skill": skill_md,
        },
        "Feedback": {
            "Verifier Passed": verify_result.get("passed"),
            "Verifier Findings": findings,
            "Error Count": error_count,
            "Warning Count": warning_count,
        },
        "scores": {
            "verifier": verifier_score,
            "warning_cleanliness": warning_score,
            "evidence_alignment": evidence_score,
            "non_duplicate": duplicate_score,
            "clarity": clarity_score,
            "safety": 0.0 if safety_blocked else 1.0,
        },
    }
    return aggregate, side_info


result = optimize_anything(
    seed_candidate={
        "when_to_use": "Use when the task matches the mined cluster.",
        "trigger_signals": "- Fill from mined evidence.",
        "operating_steps": "1. Inspect the relevant files.\n2. Run the nearest validation.",
        "failure_fallbacks": "- Stop and summarize failures after repeated validation failures.",
        "verification_suggestions": "- Run skillminer verify before promotion.",
        "safety_constraints": "- Keep edits inside the workspace.",
    },
    evaluator=evaluate_candidate,
    dataset=train_examples,
    valset=val_examples,
    objective="Improve generated SkillMiner SKILL.md candidates so they are evidence-backed, safe, reusable, and useful on held-out trace-derived tasks.",
    background="Generated skills must remain drafts. They must not auto-install dependencies, auto-promote themselves, include credentials, or write outside the workspace.",
    config=GEPAConfig(
        engine=EngineConfig(
            max_metric_calls=100,
            candidate_selection_strategy="pareto",
            acceptance_criterion="strict_improvement",
            cache_evaluation=True,
        ),
        reflection=ReflectionConfig(
            reflection_lm="openai/gpt-5.2",
        ),
    ),
)
```

## SkillMiner Data Split Strategy

Use three split types:

### Time Split

Train on older traces, validate on newer traces.

Best for measuring whether skills improve future usage.

### Cluster Holdout

Train on some clusters, validate on related but held-out clusters.

Best for measuring generalization across task families.

### Safety Holdout

Keep dangerous-command, credential, dependency, and workspace-boundary cases out of training.

Best for measuring safety regression.

Do not optimize against the final safety holdout directly. Use it only for final evaluation.

## Benchmark Plan

Run at least four conditions:

| Condition | Description |
| --- | --- |
| Current baseline | Existing `generate -> verify -> evaluate` |
| GEPA skill text | GEPA optimizes candidate skill sections |
| GEPA generator prompt | GEPA optimizes the cluster-to-skill generation policy |
| GEPA + merge | GEPA skill text with merge enabled |

Metrics:

- verifier pass rate
- safety false-negative rate
- candidate duplicate rate
- held-out task success rate
- recommendation Precision@K
- MRR
- recommendation lift
- average token cost
- average evaluation latency
- human acceptance rate, once available

Decision rule:

```text
Adopt GEPA only if held-out task success improves and safety false-negative rate does not regress.
```

Verifier-only improvement is not enough. A candidate can satisfy formatting rules and still be useless.

## Knowledge Graph Optimization Plan

SkillMiner currently has a lightweight task-skill-tool graph and Personalized PageRank. GEPA can improve the graph layer in three ways.

### 1. Graph-Grounded Skill Generation

Use graph neighborhoods as GEPA background:

```text
Cluster C03 connects to:
- tool: pytest
- skill: test-failure-repair
- error: assertion-error
- file extension: py
```

GEPA then mutates the candidate skill while preserving graph evidence.

Optimization objective:

- maximize evidence coverage
- minimize unsupported instructions
- preserve safety constraints

### 2. Graph Policy Evolution

Optimize a textual policy that decides how graph signals should affect candidate generation:

```text
When a cluster has high PageRank proximity to an installed skill, generate an extension skill rather than a new standalone skill.
When a cluster has high tool reuse but low skill coverage, generate a workflow skill.
When a cluster has high risk, add stronger approval and rollback guidance.
```

This is a good GEPA target because the policy is qualitative and benefits from textual reflection.

### 3. Full GraphRAG Later

A full GraphRAG layer should wait until the graph has more data:

- accepted/rejected skills
- real usage outcomes
- patch validation outcomes
- project-specific file/module nodes
- command success history

GEPA can later optimize graph retrieval prompts, graph expansion rules, or graph-to-skill synthesis policies.

## Code Evolution Plan

Do not let GEPA directly mutate production code in the first integration.

The safe progression is:

### Phase A: Skill Text Only

GEPA optimizes `SKILL.md` candidates. No code changes.

### Phase B: Validation Metadata

GEPA optimizes `validation.json` suggestions:

```json
{
  "commands": ["python -m pytest tests/test_mining.py -q"],
  "expected_outputs": ["passed"],
  "risk": "low"
}
```

The verifier checks the metadata; a separate runner executes only approved commands.

### Phase C: Patch Guidance

GEPA optimizes natural-language patch strategy, not code.

Example:

```text
For parser assertion failures, first inspect tokenizer tests, then reproduce the smallest failing case before touching parser state.
```

### Phase D: Sandboxed Code Evolution

Only after sandbox replay exists, GEPA can optimize code patches or helper scripts. Required controls:

- disposable workspace copy
- deterministic test command
- timeout
- no network by default
- no workspace-external writes
- patch diff capture
- automatic revert of failed candidates inside the sandbox
- final human review before applying to real repo

GEPA's `gskill` is the model to study for this phase: it uses generated coding tasks, agent traces, patch output, and test output as feedback.

## Optimization Ideas For This Project

### Use Verifier Findings As First-Class ASI

Every verifier code should map to a reflection hint:

| Verifier Finding | Reflection Hint |
| --- | --- |
| `missing_required_section` | Add the missing section without changing frontmatter. |
| `dangerous_command` | Remove or replace the unsafe command with approval-gated guidance. |
| `credential_pattern` | Replace credential examples with placeholders and safety warnings. |
| `short_description` | Explain the task boundary and trigger condition. |
| `dependency_hint` | Add explicit approval gating for dependency installation. |

### Optimize By Skill Section

Do not mutate the whole Markdown file blindly. Mutate sections independently:

- trigger precision
- operating sequence
- fallback quality
- verification quality
- safety constraints
- mined evidence summary

This makes GEPA changes easier to review and easier to merge.

### Add Negative Examples

Feed GEPA failures from:

- dangerous command test cases
- credential pattern test cases
- workspace-boundary failures
- duplicate skill examples
- overbroad generated skills

This teaches the reflection model what not to do.

### Make Duplicate Detection Actionable

Instead of returning only `similarity=0.94`, return:

```text
This candidate overlaps with test-failure-repair on pytest reproduction and validation steps, but lacks the parser-specific fallback. Either specialize it or merge it.
```

That gives GEPA a concrete edit direction.

### Use Multi-Objective Pareto Search

Track at least:

- correctness
- safety
- specificity
- non-duplication
- evidence coverage
- cost

Do not collapse these too early. Pareto search is useful precisely because the best safe candidate and the most useful candidate may differ early in optimization.

### Mine Stronger Examples Before Scaling GEPA

GEPA quality depends on evaluator quality. Before expensive runs:

- improve tool event trace grouping
- add patch summaries
- add validation command outcomes
- add accepted/rejected skill labels
- add project/module nodes to the graph

## Failure Modes

### Overfitting Tiny Trace Sets

If only a few traces exist, GEPA may learn overly specific instructions. Countermeasures:

- use held-out clusters
- penalize hardcoded file names unless they are recurring signals
- keep generated candidates as drafts

### Optimizing The Verifier Instead Of Usefulness

A skill can pass static checks but still not help. Countermeasures:

- add held-out task success
- add human acceptance labels
- add post-use feedback

### Reflection Hallucination

The reflection model may invent unsupported steps. Countermeasures:

- include mined evidence
- penalize unsupported tool/path claims
- require trace IDs in evidence section

### Safety Regression

GEPA may discover that risky instructions improve task success. Countermeasures:

- hard reject dangerous patterns
- keep safety outside weighted reward
- run final safety holdout

### Cost Drift

GEPA can spend a lot of model calls. Countermeasures:

- smoke test with small budgets
- cache evaluations
- start with verifier-only scoring
- add real task replay later
- track cost per accepted candidate

## Suggested Implementation Roadmap

### Milestone 1: Offline GEPA Adapter

Add `skillminer/gepa_adapter.py`:

- converts mining report clusters to GEPA examples
- renders structured candidates into `SKILL.md`
- calls `verify_skill`
- returns structured ASI
- writes `outputs/reports/gepa_skill_optimization.json`

No external command execution yet.

### Milestone 2: Baseline Comparison

Add:

```text
skillminer evaluate-gepa --cluster-id C03 --budget 50
```

Compare:

- seed candidate
- best GEPA candidate
- current generator output

### Milestone 3: Held-Out Trace Replay

Use trace-derived tasks as validation examples. If a task has known `used_skills`, measure whether the evolved skill would be recommended or selected.

### Milestone 4: Validation Runner

Add approved, sandboxed validation commands. Feed test output into ASI.

### Milestone 5: Promotion Queue

Only after a GEPA candidate passes:

- verifier
- held-out evaluation
- safety holdout
- duplicate check

then queue it for human promotion. Do not auto-install.

## Practical Defaults

Initial run:

```text
max_metric_calls: 50-100
candidate_selection_strategy: pareto
acceptance_criterion: strict_improvement
parallel: false at first
reflection model: strongest affordable model
task model: same agent model used by the project, once replay exists
candidate shape: structured dict sections
dataset size: 10-50 examples
valset size: 5-20 examples
```

Scale only after the evaluator returns useful ASI.

## What Success Looks Like

Short-term success:

- GEPA candidates pass verifier more often than seed candidates.
- GEPA candidates are less duplicate-prone.
- GEPA candidates include more concrete mined evidence.
- Safety false-negative rate stays at zero.

Medium-term success:

- GEPA candidates improve recommendation metrics on held-out traces.
- Human reviewers accept more generated skills.
- Post-use tool events show fewer repeated failures.

Long-term success:

- SkillMiner mines traces, GEPA evolves skills, SkillMiner verifies and recommends, and real usage feeds the next cycle.
- Graph evidence and code validation become ASI.
- The system improves generated skills without weakening human promotion and safety boundaries.

## Final Recommendation

Use GEPA as the evolutionary optimizer after SkillMiner's existing mining and baseline evaluation.

The first production-quality target should be structured `SKILL.md` candidate sections. Keep all code mutation and auto-promotion out of scope until sandbox replay and validation deltas are implemented.

This gives the project a controlled path:

```text
discover patterns with SkillMiner
optimize skill text with GEPA
verify with SkillMiner
evaluate on held-out traces
queue for human promotion
feed outcomes back into traces
```

That is the smallest loop that can improve skill quality while preserving safety and debuggability.
