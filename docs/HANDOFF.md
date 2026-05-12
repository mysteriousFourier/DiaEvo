# SkillMiner Handoff

## Current State

SkillMiner is a working integrated local CLI in `D:\codex\skillminer`.

It has two operating modes:

- Interactive workbench: `.\skillminer.ps1` opens a terminal dashboard, slash commands, DeepSeek chat, and approval-gated local tools.
- Scriptable CLI: `.\skillminer.ps1 <command>` emits JSON command results for ingestion, mining, recommendation, generation, verification, evolution, validation, promotion, evaluation, and tool execution.

The implemented conservative skill self-evolution loop is:

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

The current optimizer is local metric/Pareto. It is intentionally dependency-free and deterministic. GEPA is the intended optional next optimizer backend, but it should sit behind the existing evaluator and safety gates.

Current checkpoint: **Phase 2: Quality hardening, pre-Phase 3 gate satisfied on the sample corpus**. Phase 0 and Phase 1 are implemented baselines. Phase 2 now proves improved held-out usefulness through stable candidate overlay metrics, section-aware duplicate checks, validation feedback, promotion feedback, and evolution memory. Phase 3 GEPA integration is not implemented.

## Final Product Direction

The project goal is an integrated CLI agent skill workbench that learns from its own use:

1. The user chats, runs slash commands, or invokes scriptable commands.
2. Local tool calls are logged as sanitized events.
3. Events and base traces are mined into workflow evidence.
4. Existing skills are recommended for future tasks.
5. Coverage gaps produce candidate `SKILL.md` files.
6. Candidate skills are evolved, verified, validated, de-duplicated, and reviewed.
7. Promoted skills update the local registry.
8. New usage and validation outcomes feed the next cycle.

The stage goal is skill self-evolution: evolved skills should become more useful on held-out traces while safety false-negative rate remains zero.

## Implemented Areas

| Area | Status |
| --- | --- |
| Local launchers | `skillminer.ps1` and `skillminer-home.ps1` use project `.venv`, set `PYTHONPATH`, and force UTF-8. |
| Interactive terminal | Dashboard, workspace trust prompt, slash menu, prompt bar, DeepSeek chat bridge. |
| Tool layer | Workspace file tools, shell, web search/fetch, approval previews, event logging. |
| Tool-to-trace feedback | `.skillminer/tool_events.jsonl` is converted into trace records by `ingest`/`feedback`. |
| Mining | TF-IDF, K-Means, association rules, frequent sequences, task-skill-tool graph. |
| Recommendation | Weighted ranker with optional Pareto rerank and plugin-backed skill candidates. |
| Candidate generation | Trace-grounded `SKILL.md` drafts from mining clusters. |
| Verification | Static candidate contract and safety checks. |
| Evolution | Local metric/Pareto structured-section optimization with evolution memory. |
| Validation | Approval-gated `validation.json` command replay. |
| Promotion | Human queue and local registry update only after approval. |
| Evaluation | Baseline/evolved metrics, held-out split, stable overlay recommendation gate, raw augmented diagnostics, duplicate checks, safety holdout. |

## Important Files

| Path | Purpose |
| --- | --- |
| `README.md` | User-facing command and project overview. |
| `docs/DESIGN.md` | Architecture, module responsibilities, phase roadmap. |
| `docs/AUTONOMOUS_EVOLUTION_LOOP.md` | Current self-evolution loop and operating policy. |
| `docs/HANDOFF_ADVANCED_SKILL_EVOLUTION.md` | Next engineering work for quality hardening and GEPA. |
| `docs/GEPA_SKILL_EVOLUTION_GUIDE.md` | GEPA integration design. |
| `docs/talk_whit_GEPA.md` | Low-cost APO/GEPA research notes. |
| `skillminer/cli.py` | Scriptable command dispatch and default shell entry. |
| `ui/interactive_shell.py` | Interactive loop and slash command dispatch. |
| `skillminer/tool_layer.py` | Local tools, approvals, event logging. |
| `skillminer/ingest.py` | Trace and tool event normalization. |
| `skillminer/miner.py` | Mining report orchestration. |
| `skillminer/recommender.py` | Skill recommendation. |
| `skillminer/generator.py` | Seed candidate generation. |
| `skillminer/evolution.py` | Local optimizer, memory, validation/promotion feedback recording. |
| `skillminer/quality.py` | Shared duplicate/actionability helpers. |
| `skillminer/evaluation.py` | Baseline/evolved/held-out/safety metrics. |
| `skillminer/validation_runner.py` | Approved validation replay. |
| `skillminer/promotion.py` | Promotion queue and registry gate. |

## Command Flow

Run the current conservative loop:

```powershell
cd D:\codex\skillminer
.\skillminer.ps1 ingest --input data/sample_traces.jsonl
.\skillminer.ps1 mine
.\skillminer.ps1 generate --cluster-id C03
.\skillminer.ps1 evolve --cluster-id C03 --budget 50
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 validate --skill outputs/candidate_skills/C03/evolved --approve
.\skillminer.ps1 queue-promotion --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 label-promotion --queue-id <id> --label duplicate --note "covered by nearest skill"
.\skillminer.ps1 promote --queue-id <id> --approve
.\skillminer.ps1 feedback
.\skillminer.ps1 evaluate --variant evolved --top-k 3
```

Notes:

- `validate` previews or blocks unless `--approve` is supplied.
- `promote` previews unless `--approve` is supplied.
- `label-promotion` accepts `accepted`, `rejected`, `merge-needed`, `too-broad`, `duplicate`, and `unsafe`; blocking labels prevent promotion and are written to evolution memory.
- Promotion updates only `data/skill_registry.json`.
- Candidate outputs and reports are under `outputs/`.
- Tool events live under `.skillminer/tool_events.jsonl`.

## Current Skill Evolution Behavior

`evolve` works over structured sections:

```text
when_to_use
trigger_signals
operating_steps
failure_fallbacks
verification_suggestions
safety_constraints
```

It scores candidates with verifier pass/fail, warning cleanliness, mined-evidence alignment, non-duplication, specificity, safety, and length. It rejects hard safety failures and records memory entries for successes, verifier findings, validation outcomes, duplicates, and promotion review patterns.

`evaluate --variant evolved` now reports:

- normal recommendation metrics
- evolved candidate counts and verifier pass rate
- duplicate pairs and nearest duplicate actions
- deterministic held-out trace split
- held-out Precision@K, MRR, lift, seed-vs-evolved deltas, per-cluster usefulness status, and evolved-candidate top-K hit diagnostics
- stable overlay gate that preserves existing skill ranking while measuring new candidate discoverability
- raw augmented-registry diagnostics for candidate ranking perturbation
- baseline-vs-evolved candidate comparison
- memory summary for validation, duplicate, and promotion feedback
- safety false-negative rate

Current sample-data Phase 2 result:

```text
heldout_usefulness_status == improved
heldout_candidate_discovery_status == improved
heldout_recommendation_status == neutral
heldout_evolved_candidate_top_k_hit_rate_delta == 0.1428
safety_false_negative_rate == 0.0
evolved_verifier_pass_rate == 1.0
raw_evolved_mrr_delta == -0.0714
```

The negative raw augmented MRR delta is retained as a diagnostic: inserting draft candidates into the temporary registry can perturb shared TF-IDF and graph context. The Phase 2 gate uses stable overlay behavior until a human actually promotes a candidate.

## GEPA Direction

GEPA should be integrated after the current evaluator is stable enough to reward usefulness, not just verifier compliance.

Next GEPA step:

```text
skillminer/gepa_adapter.py
  -> converts mining clusters/traces into GEPA examples
  -> renders structured candidates into SKILL.md
  -> calls existing verifier, duplicate checks, validation feedback, and held-out evaluator
  -> returns ASI for reflection
  -> writes outputs/reports/gepa_skill_optimization.json
```

Do not replace mining, verification, recommendation, or promotion with GEPA. GEPA should optimize text artifacts discovered and governed by SkillMiner.

## Known Limits

- Validation replay currently runs approved commands in the real workspace; disposable clone replay is not implemented.
- GEPA adapter is not implemented.
- No external skill installation workflow is enabled.
- No production-code mutation by the optimizer is enabled.
- Human promotion labels are implemented, but no learned scoring policy uses them yet.
- Duplicate detection is lightweight TF-IDF cosine, though actions now include reviewer-facing section merge/specialize proposals.
- Recommender weights are configured heuristics, not learned from feedback.
- Terminal UI is a lightweight Python renderer, not a full Textual/Ink application.
- Some sample trace text is mojibake, but structured fields are usable.

## Verification

Recent verification command:

```powershell
python -m pytest -q
python -m skillminer.cli evaluate --variant evolved --top-k 3 --no-tool-events
```

Current phase invariant:

```text
safety_false_negative_rate == 0.0
```

## Next Work

Follow `docs/HANDOFF_ADVANCED_SKILL_EVOLUTION.md`.

The next practical sequence is:

1. Add optional GEPA adapter behind the existing evaluator.
2. Compare seed vs local evolved vs GEPA under identical held-out/safety splits.
3. Turn reviewer section merge proposals into an explicit merge/specialize rewrite command.
4. Feed promotion labels into recommendation/evolution scoring, not just memory retrieval.
5. Add low-cost GEPA/APO controls: CTM/EPM memory, CAPO racing, dense metric inner loop, sparse LLM judge.
6. Add disposable sandbox validation before any patch guidance or code evolution.
