# SkillMiner Design

## Product Shape

SkillMiner is an integrated local CLI workbench for Agent skill operations. Its primary surface is:

```powershell
.\skillminer.ps1
```

The command opens an interactive terminal shell when no subcommand is supplied and acts as a scriptable JSON CLI when subcommands are supplied. The same project also exposes `skillminer` and `skillminer-home` console scripts through `pyproject.toml`.

SkillMiner combines five roles in one local tool:

1. Interactive DeepSeek-powered terminal assistant.
2. Approval-gated local tool runner.
3. Trace and tool-event recorder.
4. Skill mining, recommendation, generation, verification, validation, and promotion CLI.
5. Skill self-evolution benchmark and optimizer host.

The design goal is a CLI that improves its skill system from actual use while keeping explicit user approval around risky operations.

## End-To-End Data Flow

```text
user chat / slash command / scriptable CLI
  -> local tools and skill commands
  -> .skillminer/tool_events.jsonl + data/*.jsonl traces
  -> ingest / feedback
  -> mine
  -> recommend or generate
  -> evolve
  -> verify
  -> validate
  -> queue-promotion
  -> promote
  -> evaluate
  -> future runs use updated traces, registry, and evolution memory
```

This flow is deliberately conservative. The current system can evolve skill text and registry metadata, but it does not auto-install external skills or mutate production code.

## Architecture

| Layer | Files | Responsibility |
| --- | --- | --- |
| CLI dispatch | `skillminer/cli.py`, `skillminer.ps1`, `skillminer-home.ps1` | Scriptable command surface, default interactive shell entry, UTF-8 and project-local Python setup. |
| Interactive shell | `ui/interactive_shell.py`, `ui/prompt_bar.py`, `ui/cli_style.py`, `ui/tool_render.py`, `ui/terminal_home.py` | Dashboard, workspace trust, slash menu, prompt input, DeepSeek chat loop, model/base URL/API key commands, tool preview rendering. |
| Chat bridge | `skillminer/deepseek_chat.py`, `skillminer/tool_chat.py`, `skillminer/env.py` | OpenAI-compatible DeepSeek calls, tool schema conversion, tool result messages, local `.env` loading and updates. |
| Tool layer | `skillminer/tool_layer.py` | Workspace-bounded file tools, shell/network tools, approval gates, previews, event logging. |
| Trace model | `skillminer/models.py`, `skillminer/ingest.py`, `skillminer/storage.py` | JSONL parsing, trace normalization, tool event conversion, registry/plugin loading, summary reports. |
| Mining | `skillminer/features.py`, `skillminer/clustering.py`, `skillminer/association_rules.py`, `skillminer/sequence_mining.py`, `skillminer/skill_graph.py`, `skillminer/miner.py` | TF-IDF, seeded K-Means, association rules, frequent tool sequences, task-skill-tool graph, coverage-gap and generation-entrypoint reports. |
| Recommendation | `skillminer/recommender.py`, `data/recommender_weights.json` | Rank registry and plugin-backed skills with similarity, rules, PageRank, usage, success, coverage, risk, and cost; optional Pareto reranking. |
| Candidate generation | `skillminer/generator.py` | Render trace-grounded `SKILL.md` drafts from mining clusters. |
| Verification | `skillminer/verifier.py` | Check frontmatter, required sections, safety patterns, credential patterns, suspicious paths, dependency hints, and validation metadata. |
| Evolution | `skillminer/evolution.py`, `skillminer/quality.py`, `data/evolution_memory.json` | Local metric/Pareto section optimization, duplicate checks, memory retrieval/update, ASI-style feedback storage. |
| Validation | `skillminer/validation_runner.py` | Approval-gated `validation.json` command replay with stdout/stderr/exit status captured. |
| Promotion | `skillminer/promotion.py` | Human-reviewed promotion queue and local registry update gate. |
| Evaluation | `skillminer/evaluation.py` | Baseline and evolved metrics, held-out split, recommendation metrics, candidate duplicate metrics, safety holdout. |

## Command Model

Commands are intentionally symmetric across script and interactive use.

Scriptable commands:

```text
ingest, mine, recommend, generate, verify, evolve, validate,
queue-promotion, promote, demo, home, tools, feedback, evaluate,
tool, chat-test
```

Interactive slash commands:

```text
/ingest, /mine, /recommend, /generate, /verify, /demo, /feedback,
/tools, /tool, /model, /baseurl, /key, /home, /help, /exit
```

The interactive shell sends non-slash text to DeepSeek. The model can request local tools. Tool calls use the same `tool_layer.py` handlers as CLI tool commands, so traces and approval behavior stay consistent.

## Safety Model

Safety is implemented at multiple layers:

- Workspace trust prompt before interactive use.
- Workspace path boundary checks for file tools.
- Approval previews for writes, edits, deletes, patches, shell commands, and network tools.
- Sanitized tool event logging that redacts key/token/secret/password-like fields.
- Candidate verifier for dangerous commands, credential-like text, parent paths, missing sections, and dependency hints.
- Validation runner blocks dangerous, install, and network commands unless policy explicitly allows them.
- Promotion requires manual approval and writes only to `data/skill_registry.json`.
- Safety false-negative rate is measured in evaluation and should remain `0.0`.

Safety constraints are not a weighted preference in the self-evolution loop. Dangerous candidates are hard failures.

## Current Algorithm Choices

The MVP stays dependency-free by default:

- Standard-library TF-IDF in `features.py`.
- In-repo seeded K-Means in `clustering.py`.
- Apriori-style association rule enumeration in `association_rules.py`.
- PrefixSpan-style subsequence support in `sequence_mining.py`.
- Lightweight Personalized PageRank in `skill_graph.py`.
- Local metric/Pareto evolution in `evolution.py`.

Optional heavier dependencies are listed under the `full` extra in `pyproject.toml`, but current commands should run without them.

## Skill Self-Evolution Design

The current skill evolution target is structured `SKILL.md` text, not production code.

Candidate sections:

```text
When To Use
Trigger Signals
Operating Steps
Failure Fallbacks
Verification Suggestions
Safety Constraints
```

The local optimizer generates variants and scores them using:

- verifier pass/fail
- warning cleanliness
- mined evidence alignment
- duplicate similarity
- specificity
- safety
- bounded length

Evolution memory stores:

- successful templates
- verifier error patterns
- validation feedback patterns
- duplicate patterns
- promotion review patterns

This local implementation is the baseline and scaffold for the later GEPA adapter.

## GEPA Integration Boundary

GEPA should be integrated as an optional optimizer behind the current evolution/evaluation interface.

SkillMiner owns:

- traces and tool-event normalization
- cluster and graph mining
- generation entrypoint selection
- verification and validation gates
- duplicate checks
- recommendation and evaluation reports
- promotion queue and human approval

GEPA should own:

- reflective mutation of structured candidates
- candidate pool management
- Pareto frontier selection
- section-aware merge
- optimization budget handling

The first GEPA target is `SKILL.md` section optimization. Later targets can include generator policy, validation metadata suggestions, graph-to-skill policy, and, only after sandbox replay exists, patch guidance or code evolution research.

## Phase Roadmap

Current checkpoint: **Phase 2: Quality hardening, pre-Phase 3 gate satisfied on the sample corpus**. Phase 0 and Phase 1 are in place. Phase 2 reports improved held-out usefulness without safety false-negative regression. Phase 3 GEPA adapter work can start behind the existing evaluator and safety gate.

| Phase | Engineering target | Main risk to control |
| --- | --- | --- |
| 0. Integrated CLI | Unified interactive/scriptable tool, tool schemas, event logging, DeepSeek bridge. | Tool execution safety and trace hygiene. |
| 1. Conservative skill loop | Generate/evolve/verify/validate/queue/promote/evaluate skill candidates. | Candidate safety and manual promotion boundary. |
| 2. Quality hardening | Held-out metrics, stable overlay gate, actionable duplicate checks, validation feedback memory, promotion reports. | Optimizing verifier compliance instead of usefulness. |
| 3. GEPA adapter | Optional GEPA section optimizer using SkillMiner evaluator and ASI. | Cost and hallucinated unsupported instructions. |
| 4. Low-cost APO/GEPA | CTM/EPM memory, CAPO racing, metric inner loop, sparse LLM judge. | Token/cost drift and overfitting tiny traces. |
| 5. Disposable sandbox | Clone workspace for validation replay and diff capture. | Accidental real workspace mutation. |
| 6. Human feedback learning | Add accepted/rejected/merge/unsafe labels into memory and promotion policy. | Premature auto-promotion. |
| 7. Code evolution research | Sandbox-only GEPA/gskill-style patch guidance or code mutations. | Applying unreviewed code changes. |

## Success Criteria For The Current Stage

Before widening scope:

- `python -m pytest -q` passes.
- `skillminer evaluate --variant evolved` writes stable reports.
- Safety false-negative rate remains `0.0`.
- Evolved candidate verifier pass rate does not regress.
- Duplicate recommendations are actionable.
- Held-out usefulness improves on the sample corpus without disturbing existing skill ranking.
- Human promotion remains required.
