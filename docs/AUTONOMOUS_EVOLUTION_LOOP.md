# Autonomous Evolution Loop

This document fixes the current research and implementation target for SkillMiner's first automatic evolution loop. It uses the local paper corpus in `D:\download\claude-code-main\references` as architecture input, while keeping the runnable MVP dependency-free.

## Research Boundary

This pass did not complete a systematic optimization-algorithm survey or tune the existing algorithms. The implemented work is an engineering baseline for the closed loop, plus a lightweight paper-to-architecture mapping. The next handoff owner must treat optimization research as the first task before changing the current scoring, clustering, sequence-mining, or generation-entrypoint algorithms.

Required next research deliverables:

1. Audit the current in-repo algorithms: TF-IDF, K-Means, Apriori-style rules, PrefixSpan-style sequence mining, Personalized PageRank, coverage-gap scoring, and recommendation weights.
2. Compare them against stronger candidates from the paper corpus and standard optimization methods: FP-Growth, HDBSCAN, UCB, Thompson Sampling, Pareto reranking, Bayesian optimization, and preference learning.
3. Run `skillminer evaluate` before optimization to record Precision@K, MRR, coverage-gap hit rate, verifier pass rate, candidate duplicate rate, recommendation lift, and safety false-negative rate.
4. Use the reproducible baseline on `data/sample_traces.jsonl` plus `.skillminer/tool_events.jsonl` as the before snapshot.
5. Optimize one algorithmic slice at a time and record before/after metrics in `outputs/reports/baseline_metrics.json` or a derivative report.

## Implemented Loop

The current loop is:

```text
tool_events -> ingest -> mine -> generate -> verify -> recommend -> feedback
```

The first safety boundary is deliberate: SkillMiner can generate and verify candidate skills, but it does not auto-install them. Promotion still requires human review.

## Paper Comparison

| Source | Relevant Pattern | SkillMiner Mapping | Status |
| --- | --- | --- | --- |
| BAGEL Bootstrapping Agents by Guiding Exploration with Language | Bootstrap useful agent behavior from guided exploration trajectories. | Treat `.skillminer/tool_events.jsonl` as early trajectory evidence that can be mined into reusable workflows. | Prototype-ready for real traces; synthetic exploration is later work. |
| SkillWeaver Web Agents can Self-Improve by Discovering | Discover and reuse skills from repeated web-agent tasks. | Mine clusters, frequent tool paths, and missing-skill coverage gaps; generate candidate `SKILL.md` drafts. | Implemented as a lightweight trace-driven generator. |
| CASCADE Cumulative Agentic Skill Creation | Skills accumulate over time and later tasks reuse prior skills. | `used_skills`, success rates, reuse counts, and recommendation weights form the cumulative layer. | Partially implemented; automatic promotion is still gated. |
| Trial and Error Exploration-Based Trajectory Optimization | Optimize future behavior from trial outcomes. | Tool success rate, failure types, retries, and validation output are logged as feedback signals. | Implemented as feedback fields; no optimizer yet. |
| LLMs in the Imaginarium | Simulated trial-and-error can improve tool use. | Could create sandbox replay traces before candidate promotion. | Future work. |
| GC-DPG Graph-Constrained Dual-Phase Generation for Safe and | Constrain generation with graph and safety controls. | Existing skill graph plus verifier safety gates constrain recommendations and generated candidates. | Partially implemented; graph constraints are still scoring signals, not hard generation constraints. |
| GraphRAG and KG-Guided RAG papers | Use graph structure to ground retrieval and reduce unsupported outputs. | Skill graph, association rules, and mined evidence are included in reports and generated candidates. | Lightweight grounding implemented. |
| Hallucination review | Add explicit grounding and verification to reduce fabricated behavior. | Candidate skills include mined evidence, safety constraints, fallback guidance, and verifier gates. | Implemented for static checks; executable checks are a pluggable entry point. |

## Current Implementation

| Layer | Files | Current Behavior |
| --- | --- | --- |
| Event capture | `skillminer/tool_layer.py` | Every local tool call appends a sanitized JSONL event to `.skillminer/tool_events.jsonl`. |
| Ingestion | `skillminer/ingest.py`, `skillminer/models.py` | Base JSONL traces and tool events are normalized into `TraceRecord` objects with source, event count, tool success rate, failure types, and reuse counts. |
| Mining | `skillminer/clustering.py`, `skillminer/miner.py` | Reports coverage gaps, failure hotspots, high-reuse paths, association rules, frequent sequences, and generation entrypoints. |
| Generation | `skillminer/generator.py` | Produces trace-driven candidate `SKILL.md` files with usage scope, trigger signals, mined evidence, operating steps, fallbacks, validation suggestions, and safety constraints. |
| Verification | `skillminer/verifier.py` | Checks required frontmatter, required candidate sections, dangerous commands, credential patterns, suspicious paths, dependency hints, and optional `validation.json`. |
| Recommendation | `skillminer/recommender.py`, `data/recommender_weights.json` | Keeps static scoring while adding configurable success, recent reuse, risk, cost, and coverage-gap weights. |
| Feedback | `skillminer/cli.py` | `ingest` folds tool events by default; `feedback` is an explicit alias for folding event logs into processed traces. |
| Evaluation | `skillminer/evaluation.py` | Runs the current baseline and writes comparable recommendation, coverage, candidate, verifier, duplicate, lift, and safety metrics. |

## Implementation Gaps

| Gap | Impact | Suggested Next Step |
| --- | --- | --- |
| No candidate promotion workflow | Verified candidates remain drafts. | Add `promote --skill <dir>` with a human approval gate and registry update. |
| No executable sandbox replay | `validation.json` is accepted but not produced by the verifier. | Add a runner that executes declared validation commands in a bounded workspace and writes `validation.json`. |
| No bandit or preference learner | Weights are configurable but not learned from outcomes. | Add UCB or Thompson Sampling over skill choices using recommendation feedback after the baseline report has enough outcome volume. |
| No replacement decision yet for stronger algorithms | Current weights and mining heuristics are engineering defaults, now with baseline metrics but not yet optimized. | Compare one algorithmic slice at a time against the `evaluate` report before tuning or replacing it. |
| No diff-level quality signal | Tool events know tools and paths, but not semantic patch quality. | Add optional patch summaries and validation deltas to event logs. |
| No duplicate skill merging | Similar generated candidates can accumulate. | Add candidate similarity checks against registry and previous candidate folders. |
| Limited graph constraints | Graph proximity affects ranking, not generation policy. | Use graph neighborhoods to require evidence before generating high-risk workflow steps. |

## Hermes Layering

Hermes should be treated as an architecture reference rather than code to port. The useful layering for this prototype is:

| Hermes-Like Layer | SkillMiner Equivalent | Practical Rule |
| --- | --- | --- |
| Cross-session memory | `data/processed_traces.jsonl`, `.skillminer/tool_events.jsonl`, `outputs/reports/*.json` | Store only sanitized operational evidence that can be re-mined. |
| Experience abstraction | Clusters, rules, frequent sequences, skill graph | Convert raw actions into reusable signals before generation. |
| Skill creation | `generator.py` candidate drafts | Generate candidates only from clusters with evidence and include fallback instructions. |
| Skill improvement | Event feedback and verifier output | Update recommendation/generation evidence after every real tool run. |
| Safety boundary | `verifier.py` plus human promotion | Never install or merge generated skills without explicit approval. |

## Algorithm Buckets

| Bucket | Algorithms | Rationale |
| --- | --- | --- |
| Implemented now | TF-IDF, K-Means, Apriori-style rules, PrefixSpan-style subsequences, Personalized PageRank | These fit the dependency-free MVP and provide enough evidence for the first loop. |
| Prototype-ready next | Pareto reranking, lightweight duplicate detection, UCB or Thompson Sampling overlays | These can improve recommendation and promotion decisions without heavy infrastructure once baseline metrics are stable. |
| Later phase | HDBSCAN, sentence transformers, DPO/preference learning, Bayesian optimization, full GraphRAG | Useful after there is enough real event data to justify extra dependencies and evaluation cost. |

The bucket list above is not an optimization study. It is a starting queue for the next owner to evaluate with metrics and controlled changes.

## Operating Policy

1. Ingest real traces and tool events before mining.
2. Generate candidates only from mining report entrypoints.
3. Require verifier pass before a candidate can be recommended for promotion.
4. Require human approval before install, merge, dependency installation, external network use, or workspace-external writes.
5. Feed later tool events back through `skillminer feedback` so the next mining run reflects real outcomes.
6. Do not claim an algorithm is improved until baseline and after-change metrics are recorded.
