# Autonomous Evolution Research Handoff

## Current State

SkillMiner is at a stable MVP checkpoint in `D:\codex\skillminer`.
The local tool bridge is connected to DeepSeek chat, gated tools require explicit approval, and tool results are fed back into conversation history.

The next phase is research-first: identify a practical fully automatic evolution loop, compare it against the local codebase, and only then decide what to implement.

## Research Boundary

This pass did not complete a systematic survey or optimization of the existing algorithms. The loop is now wired end to end, but the current scoring, clustering, sequence mining, and candidate-generation logic are still engineering baselines.

The next handoff owner should start with:

1. Run `skillminer evaluate` to baseline the current algorithms on `data/sample_traces.jsonl` plus `.skillminer/tool_events.jsonl`.
2. Compare the current implementations against stronger candidates from the paper corpus and standard optimization methods.
3. Optimize one algorithmic slice at a time, with before/after metrics written to `outputs/reports/`.
4. Keep human approval as the install/promote boundary until there is a validated promotion policy.

## Canonical Sources

Use this directory as the paper corpus for the next phase:

```text
D:\download\claude-code-main\references
```

Relevant PDFs in that directory:

```text
BAGEL Bootstrapping Agents by Guiding Exploration with Language.pdf
CASCADE Cumulative Agentic Skill Creation.pdf
GC-DPG Graph-Constrained Dual-Phase Generation for Safe and.pdf
GraphRAG Leveraging Graph-Based Efficiency to Minimize Hallucinations in LLM-Driven RAG for Finance Data.pdf
Knowledge Graph-Guided Retrieval Augmented Generation.pdf
LLMsinthe Imaginarium.pdf
Mitigating Hallucination by Integrating Knowledge Graphs into LLM Inference– a Systematic Literature Review.pdf
SkillWeaver Web Agents can Self-Improve by Discovering.pdf
Trial and Error Exploration-Based Trajectory Optimization.pdf
```

Hermes design notes live in the repository-root project plan document.
Use that material as a design reference, not as a source of drop-in implementation.

## What To Study

Focus on the full loop, not just isolated algorithms:

1. Trace collection and normalization.
2. Skill or workflow discovery from traces.
3. Automatic candidate generation.
4. Verification and safety gating.
5. Promotion or install decisions.
6. Feedback from success/failure and later reuse.
7. Recursive improvement over time.

## Hermes Focus

Hermes is a design reference for the project, not a codebase to port.

The project plan frames Hermes around:

- Cross-session memory.
- Creating skills from experience.
- Improving skills through use.
- A self-evolving closed loop rather than a one-shot recommender.

That makes Hermes useful for architecture and policy choices, but the next implementation should still stay in the Python prototype and stay grounded in the paper corpus above.

## Paper-to-Workflow Map

- `BAGEL`: bootstrapped exploration and synthetic trajectory generation.
- `SkillWeaver`: discover, refine, and reuse skills from agent tasks.
- `CASCADE`: cumulative skill creation and evolution over time.
- `Trial and Error`: exploration-based trajectory optimization.
- `LLMs in the Imaginarium`: simulated trial-and-error tool learning.
- `KG-Guided RAG`, `GraphRAG`, `GC-DPG`, and the hallucination review: grounding, verification, and hallucination control.

## Optimization Candidates

Use these as the first-pass optimization menu for the research sweep:

- `FP-Growth` and `Apriori`: association rules between task traits and skill usage.
- `PrefixSpan`: frequent tool-sequence discovery.
- `HDBSCAN` and `K-Means`: task clustering and coverage-gap discovery.
- `Personalized PageRank`: skill-tool graph ranking.
- `Thompson Sampling` and `UCB`: cold-start skill exploration versus exploitation.
- `DPO` and pairwise preference optimization: learn from success/failure comparisons.
- `NSGA-II` and Pareto reranking: trade off success, safety, cost, and coverage.
- `Bayesian Optimization`: tune scoring weights and thresholds.

The MVP already has simple in-repo versions of several of these ideas, so the research task is to decide which ones deserve a stronger prototype and which ones should stay as future work.

Treat the list above as a research queue, not as a claim that those algorithms have already been evaluated or improved.

## Research Questions

- What does Hermes contribute that is not already covered by Claude Code or the paper corpus?
- Which Hermes ideas are architecture-level and which are implementation details?
- Which optimization algorithms should be treated as research candidates versus prototype candidates?
- What qualifies as "fully automatic" in this project?
- Which checkpoints still need human approval?
- Which paper pattern best fits the current SkillMiner architecture?
- Which signals should drive automatic promotion: trace success, verification score, reuse, or downstream recommendation lift?
- Can tool events, diffs, and approvals become a closed feedback loop for skill evolution?

## Suggested Next Work

1. Read the PDFs in the canonical paper directory and write a comparison matrix.
2. Extract candidate automation loops from the papers and Hermes notes.
3. Map each loop to existing modules in `skillminer/`.
4. Separate algorithm candidates into research-only, prototype-ready, and later-phase buckets.
5. Choose one loop for a prototype, probably starting with trace-driven discovery plus verification.
6. Extend the existing evaluation report only when a new algorithm slice needs an additional metric.
7. Do not claim an optimization improvement until a baseline and a measured delta exist.

## Relevant Code Paths

- `skillminer/ingest.py`
- `skillminer/miner.py`
- `skillminer/recommender.py`
- `skillminer/generator.py`
- `skillminer/verifier.py`
- `skillminer/tool_layer.py`
- `skillminer/tool_chat.py`
- `skillminer/deepseek_chat.py`
- `ui/interactive_shell.py`

## Constraints

- Do not auto-install generated skills.
- Keep destructive actions behind explicit approval.
- Keep research artifacts separate from implementation until the loop is chosen.
- Use the local paper corpus before widening the search.
- Treat Hermes as a design reference, not as a source of drop-in code.
- Treat optimization of the existing algorithms as the next owner’s first research task.
