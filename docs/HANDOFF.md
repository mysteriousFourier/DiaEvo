# SkillMiner Handoff

## Current State

SkillMiner is a working integrated local CLI in `D:\codex\skillminer`.

## Must-Read Language Rule

The target user is Chinese and the default model interaction is Chinese. All user-facing content must be Chinese by default, including CLI help shown to users, slash-command descriptions, interactive prompts, generated reports, mining/KG snapshots, visible HTML pages, and prompt text that will be shown to or steer Chinese user interaction.

Keep English only when it is required by a code/API contract, external standard, field name, command name, test fixture, or verifier contract. For example, `SKILL.md` section headings such as `When To Use`, `Trigger Signals`, and `Operating Steps` stay English because the verifier and Agent Skills contract depend on them, but generated section bodies should be Chinese.

Do not make future handoff owners rediscover this requirement. Treat any new English user-facing text as a bug unless there is an explicit compatibility reason.

It has two operating modes:

- Interactive workbench: `.\skillminer.ps1` opens a terminal dashboard, slash commands, DeepSeek chat, and approval-gated local tools.
- Scriptable CLI: `.\skillminer.ps1 <command>` emits JSON command results for ingestion, mining, recommendation, generation, verification, evolution, validation, promotion, evaluation, and tool execution.

The implemented conservative skill self-evolution loop is:

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

The implemented knowledge graph loop is:

```text
traces + tool_events + web_search/web_fetch + optional conversation log
  -> reviewed active KG
  -> editable KG workbench (`kg` / `/kg`)
  -> optional answer-kg --strict / manual kg_answer tool
```

The current optimizer is local metric/Pareto. It is intentionally dependency-free and deterministic. GEPA is the intended optional next optimizer backend, but it should sit behind the existing evaluator and safety gates.

Historical checkpoints before Phase 6 are obsolete; use the latest checkpoint below as the source of truth.

## Latest Checkpoint

The older `Current checkpoint` line immediately above is superseded by this section.

Phase 5 disposable sandbox validation is complete. Phase 6 human feedback learning is now complete. The next handoff owner can start Phase 7 safe code-evolution research directly.

- Approved `validate` runs now execute `validation.json` commands inside `.tmp/validation-runs/<id>/workspace`.
- The sandbox copy excludes `.git`, `.venv`, `.tmp`, caches, and recursive report outputs.
- Validation captures stdout, stderr, exit code, duration, touched files, and a sandbox diff.
- Sandbox changes are never applied back to the real workspace automatically.
- Recent checks: `uv run --with pytest python -m pytest -q`; `.\.venv\Scripts\python.exe -m skillminer.cli evaluate --variant evolved --top-k 3 --no-tool-events`.
- Current invariant remains `safety_false_negative_rate == 0.0`.

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
| Knowledge graph | Incremental entities/triples/claims/evidence paths from traces, tool events, web evidence, optional conversation logs, and mining reports; all candidates require review before becoming active facts. |
| Editable KG | `kg` / `/kg` opens a graph workbench where users edit nodes and relations while viewing the graph, then export JSON edits for approved write-back. |
| Graph-vector KG retrieval | Accepted KG nodes, triples, and claims are vectorized with a local TF-IDF sparse index; `answer-kg` retrieves vector seed hits and expands a graph evidence subgraph. |
| KG answer switch | `answer-kg --strict` and the manual `kg_answer` tool constrain answers to accepted graph-vector evidence subgraphs when explicitly enabled; the model-facing chat tool list does not expose `kg_answer`. |
| Recommendation | Weighted ranker with optional Pareto rerank and plugin-backed skill candidates. |
| Candidate generation | Trace-grounded `SKILL.md` drafts from mining clusters. |
| Verification | Static candidate contract and safety checks. |
| Evolution | Local metric/Pareto structured-section optimization with evolution memory. |
| Validation | Approval-gated `validation.json` command replay inside disposable sandbox workspace copies, with stdout/stderr/exit code/duration/touched files/diff captured. |
| Promotion | Human queue and local registry update only after approval, with review labels and rewrite drafts feeding future scoring. |
| Evaluation | Baseline/evolved metrics, held-out split, stable overlay recommendation gate, raw augmented diagnostics, duplicate checks, safety holdout, and human-feedback-aware scoring. |
| Phase 4 GEPA/APO report | `evaluate-gepa-phase4` 会写出低成本 GEPA/APO 矩阵。当前报告是 C02、budget 5 的 dry-run，包含 7 行、0 个失败，安全 false-negative rate 为 `0.0`。 |

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
| `skillminer/knowledge_graph.py` | KG delta generation, review queue, active graph application, snapshot export, and graph-constrained answering. |
| `skillminer/recommender.py` | Skill recommendation. |
| `skillminer/generator.py` | Seed candidate generation. |
| `skillminer/evolution.py` | Local optimizer, memory, validation/promotion feedback recording. |
| `skillminer/quality.py` | Shared duplicate/actionability helpers. |
| `skillminer/evaluation.py` | Baseline/evolved/held-out/safety metrics. |
| `skillminer/gepa_adapter.py` | Optional GEPA adapter scaffold using DeepSeek `.env`, existing verifier/duplicate/evaluation gates, and JSON comparison reports. |
| `skillminer/validation_runner.py` | Approved sandbox validation replay and artifact capture. |
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
.\skillminer.ps1 kg --date 260513
.\skillminer.ps1 kg --apply-edit path\to\skillminer_kg_edit_260513.json --approve
.\skillminer.ps1 answer-kg --query "which tools support pytest traces?" --strict
.\skillminer.ps1 feedback
.\skillminer.ps1 evaluate --variant evolved --top-k 3
.\skillminer.ps1 evaluate-gepa --cluster-id C03 --budget 50 --top-k 3
```

Notes:

- `validate` previews or blocks unless `--approve` is supplied.
- `promote` previews unless `--approve` is supplied.
- `label-promotion` accepts `accepted`, `rejected`, `merge-needed`, `too-broad`, `duplicate`, and `unsafe`; blocking labels prevent promotion and are written to evolution memory.
- Promotion updates only `data/skill_registry.json`.
- Candidate outputs and reports are under `outputs/`.
- Tool events live under `.skillminer/tool_events.jsonl`.
- KG review data lives under `data/knowledge_graph/`; pending candidates must be accepted before `apply-kg-delta` makes them active.

## Knowledge Graph Boundary

The KG layer is separate from the existing PageRank skill graph. The PageRank graph remains a recommender feature; the KG is the stricter evidence layer for reviewed facts, claims, source paths, and confidence.

Candidate KG facts are generated from structured sources only in v1: traces, tool events, approved web_search/web_fetch results, optional conversation JSONL, and mining reports. `web_fetch` evidence is scored above `web_search` snippets, and both remain pending until review. GC-DPG is treated only as an inspiration for graph-constrained answering: it is not used as the KG construction method.

Strict answering is opt-in and user-controlled. Use `answer-kg --strict` or manual `/tool kg_answer ...` with `strict=true` when the model must answer only from accepted KG facts and cite evidence paths. Normal chat and recommendation are not forced into strict KG mode, and the Agent cannot silently select `kg_answer` through automatic tool calling.

Accepted KG facts can be viewed and edited with:

```powershell
.\skillminer.ps1 kg --date 260513
```

The workbench writes `graph_visualization.html`, `entities.csv`, `triples.csv`, `claims.csv`, `graph_edges.csv`, `evidence_paths.md`, `confidence_summary.md`, `graph_vector_index.json`, `graph_vector_retrieval.md`, and `graph_vector_demo.md`, and returns `visualization_path`. Editing happens inside the HTML page; write-back requires exporting JSON from the page and applying it with `kg --apply-edit ... --approve`.

Current KG type: this is now a reviewed GraphRAG-like graph-vector KG. It keeps symbolic entities/triples/claims, builds a local TF-IDF sparse vector index over accepted KG documents, retrieves vector seed hits, and expands a cited graph evidence subgraph for strict answers. It is not yet backed by a dense embedding model or external vector database, but the implemented path is graph-vector retrieval rather than plain symbolic lookup.

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

The Phase 3 scaffold is now:

```text
skillminer/gepa_adapter.py
  -> converts mining clusters/traces into GEPA examples
  -> renders structured candidates into SKILL.md
  -> calls existing verifier, duplicate checks, validation feedback, and held-out evaluator
  -> returns ASI for reflection
  -> writes outputs/reports/gepa_skill_optimization.json
```

Do not replace mining, verification, recommendation, or promotion with GEPA. GEPA should optimize text artifacts discovered and governed by SkillMiner.

GEPA model calls use the existing DeepSeek OpenAI-compatible API configuration from project `.env`:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
DEEPSEEK_MAX_TOKENS
DEEPSEEK_TEMPERATURE
DEEPSEEK_TIMEOUT
```

The real key stays in `.env`; do not copy it into docs, reports, candidate skills, ASI, or test snapshots. `evaluate-gepa --dry-run` exercises seed/local comparison and safety reporting without importing or calling GEPA. Without `--dry-run`, missing GEPA/LiteLLM dependencies fail clearly without breaking default commands.

## Known Limits

- Validation replay currently runs approved commands in the real workspace; disposable clone replay is not implemented.
- GEPA adapter scaffold is implemented and a real DeepSeek-backed smoke run completed; the first low-budget run was `not_adopted` because it did not beat local evolved usefulness.
- Phase 4 矩阵报告已实现，并已完成一轮 dry-run 矩阵。它不是真实 non-dry-run GEPA cost sweep，因为 `dry_run=true` 行不会生成 GEPA candidates。
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

Phase 6 is complete. The next practical phase is Phase 7: safe code-evolution research.

1. Keep promotion explicit and manual; do not add automatic promotion.
2. Use sandbox replay artifacts, promotion labels, and rewrite drafts as richer ASI for the next optimizer loop.
3. Keep `safety_false_negative_rate == 0.0` as the hard invariant.
4. Only start patch guidance or code evolution after sandbox replay remains stable on real tasks.

No Phase 5 work remains in this handoff.
