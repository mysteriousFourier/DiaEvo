# SkillMiner

SkillMiner is an integrated local CLI workbench for Agent skill mining, recommendation, generation, verification, and self-evolution.

The project is not just a GEPA experiment and not just a `SKILL.md` generator. The intended product shape is `.\skillminer.ps1`: a scriptable and interactive terminal tool with a dashboard, slash commands, DeepSeek chat, OpenAI-compatible tool calls, approval-gated local tools, trace capture, skill mining, candidate generation, validation, promotion review, and evaluation.

The long-term goal is a CLI that learns from its own use. Tool calls and task outcomes become traces; traces produce mined workflow evidence; evidence drives skill recommendation and candidate generation; candidate skills are verified, validated, evolved, and reviewed; accepted outcomes feed the next cycle. GEPA should become the optional reflective optimizer inside this loop, using SkillMiner's mining, verifier, validation, duplicate checks, and held-out metrics as the evaluator and safety layer.

## Current Loop

The implemented conservative loop is:

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

Current evolution is dependency-free local metric/Pareto optimization over structured `SKILL.md` sections. It is the baseline and safety scaffold for later GEPA integration.

Hard boundaries today:

- Generated skills are drafts until verified, validated, and manually promoted.
- `validate` executes commands only after approval and blocks dangerous, install, and network patterns by default.
- `promote` updates only `data/skill_registry.json`; it does not install external skills.
- No production-code self-evolution is enabled.
- GEPA is a planned optional backend, not a required dependency.

## Quick Start

```powershell
cd D:\codex\skillminer
.\skillminer.ps1
```

With no arguments, `skillminer.ps1` opens the interactive terminal shell. On first run, confirm workspace trust. Then use slash commands or normal chat:

```text
/ingest
/mine
/recommend fix failing pytest import path
/generate C03
/verify outputs/candidate_skills/C03
/tools
/tool read_file path=README.md limit=20
/model deepseek-v4-pro
/baseurl https://api.deepseek.com
/key
/home
/exit
```

Normal text is sent to DeepSeek using `.env`; the model can request local tools. Read-only tools run directly. Write, delete, patch, shell, and network tools show a preview and require approval.

## Scriptable Commands

```powershell
.\skillminer.ps1 ingest --input data/sample_traces.jsonl
.\skillminer.ps1 mine
.\skillminer.ps1 recommend --task "fix failing pytest import path" --language python --framework pytest
.\skillminer.ps1 generate --cluster-id C03
.\skillminer.ps1 evolve --cluster-id C03 --budget 50
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 validate --skill outputs/candidate_skills/C03/evolved --approve
.\skillminer.ps1 queue-promotion --skill outputs/candidate_skills/C03/evolved
.\skillminer.ps1 label-promotion --queue-id <id> --label merge-needed --note "merge with nearest skill"
.\skillminer.ps1 promote --queue-id <id> --approve
.\skillminer.ps1 feedback
.\skillminer.ps1 evaluate --variant evolved --top-k 3
.\skillminer.ps1 tools
.\skillminer.ps1 tool read_file --arg path=README.md --arg limit=20
.\skillminer.ps1 chat-test --interactive
.\skillminer-home.ps1
```

`skillminer.ps1` sets `PYTHONPATH`, UTF-8 terminal I/O, and uses the project-local `.venv` Python. The package also exposes console scripts in `pyproject.toml`: `skillminer` and `skillminer-home`.

## Main Capabilities

| Area | What it does |
| --- | --- |
| Interactive CLI | Dashboard, trust prompt, slash menu, multiline input, DeepSeek chat bridge, runtime model/base URL/API key commands. |
| Local tool layer | `list_files`, `read_file`, `write_file`, `edit_file`, `delete_file`, `apply_patch`, `run_shell`, `web_search`, and `web_fetch` with workspace boundaries and approval gates. |
| Trace capture | Every local tool call appends sanitized JSONL to `.skillminer/tool_events.jsonl`; `ingest` and `feedback` can fold those events into processed traces. |
| Mining | TF-IDF features, K-Means clusters, association rules, frequent tool sequences, task-skill-tool graph, coverage gaps, failure hotspots, and high-reuse paths. |
| Recommendation | Weighted ranker over semantic similarity, rules, PageRank, usage decay, success rate, coverage gap, recent reuse, risk, and cost; optional Pareto reranking. |
| Generation | Evidence-backed candidate `SKILL.md` from mined clusters. |
| Verification | Frontmatter and required sections, safety patterns, credential patterns, parent paths, dependency hints, and validation metadata. |
| Evolution | Local metric/Pareto candidate section optimization, duplicate checks, held-out evaluation, and evolution memory. |
| Validation | Approval-gated replay of `validation.json` commands with stdout/stderr/status captured for feedback. |
| Promotion | Human-reviewed queue, section-aware duplicate report, review labels, and local registry update only after explicit approval. |
| Evaluation | Baseline/evolved reports with Precision@K, MRR, lift, duplicate rate, verifier pass rate, held-out usefulness diagnostics, memory summary, and safety false-negative rate. |

## Skill Self-Evolution Phases

Current checkpoint: **Phase 2: Quality hardening, pre-Phase 3 gate satisfied on the sample corpus**. Phase 0 and Phase 1 are implemented baselines. Phase 2 now reports improved held-out usefulness while keeping safety false-negative rate at `0.0`. Phase 3, the optional GEPA adapter, has not started and should stay behind this evaluator and safety gate.

The stage goal is to reach reliable skill self-evolution: SkillMiner should produce evolved skills that are measurably more useful on held-out traces while keeping safety false-negative rate at zero.

| Phase | Goal | Completion signal |
| --- | --- | --- |
| Phase 0: Integrated CLI foundation | One local command surface for chat, tools, mining, recommendation, generation, verification, validation, promotion, and evaluation. | Interactive and scriptable commands work; tool events are logged and ingestible. |
| Phase 1: Conservative skill loop | Generate and locally evolve `SKILL.md` candidates without external installs or code mutation. | `evaluate --variant evolved` reports stable metrics; verifier pass rate and safety false-negative rate are tracked. |
| Phase 2: Quality hardening | Improve candidate usefulness before widening automation. | Held-out usefulness improves on the sample corpus, duplicate recommendations include merge/specialize proposals, validation feedback and promotion labels enter memory. |
| Phase 3: Optional GEPA adapter | Add GEPA behind the existing evaluator to optimize structured skill sections with ASI. | GEPA candidates beat local/Pareto candidates on held-out traces without safety regression. |
| Phase 4: Low-cost GEPA/APO | Add MemAPO-style CTM/EPM memory, CAPO racing, dense metric inner loop, and sparse LLM-as-judge outer loop. | Cost per useful evolved candidate falls while acceptance and held-out usefulness improve. |
| Phase 5: Sandbox-backed validation | Run validation in disposable workspace copies before richer replay or patch guidance. | Validation captures diffs, touched files, stdout/stderr, exit code, and duration without mutating the real workspace. |
| Phase 6: Learned promotion and policy evolution | Learn from human labels and accepted/rejected candidates. | Human labels feed memory; promotion policy improves acceptance rate while staying manual. |
| Phase 7: Safe code-evolution research | Only after sandbox replay and labels are stable, explore GEPA/gskill-style patch guidance or code evolution. | Code changes remain sandboxed, revertible, and human-reviewed before real application. |

## GEPA Direction

GEPA is the planned optimizer, not the whole system. SkillMiner should continue to own trace ingestion, mining, verification, recommendation, reporting, and safety gates. GEPA should own reflective mutation, candidate pool management, Pareto selection, merge, and optimization budget handling.

First GEPA target:

```text
generated structured SKILL.md sections
  -> evaluator renders SKILL.md
  -> verifier + duplicate + evidence + validation + held-out metrics return ASI
  -> GEPA mutates sections
  -> SkillMiner verifies, evaluates, and queues for human promotion
```

The `docs/talk_whit_GEPA.md` research notes add the cost strategy:

- MemAPO-style CTM/EPM memory for reusable success templates and error patterns.
- CAPO racing for early rejection of bad candidates.
- PMPO/MoPPS or bandit-like selection to reduce full rollouts.
- Dense automated metrics in the inner loop and sparse LLM-as-judge in the outer loop.

## Data Files

| Path | Purpose |
| --- | --- |
| `data/sample_traces.jsonl` | Seed trace dataset. |
| `data/processed_traces.jsonl` | Normalized traces written by `ingest` or `feedback`. |
| `data/skill_registry.json` | Local skill registry used by recommendation and promotion. |
| `data/plugin_metadata.json` | Plugin-backed capability metadata used as recommendation candidates. |
| `data/recommender_weights.json` | Ranker weight configuration. |
| `data/evolution_memory.json` | Success templates and error/validation/duplicate/promotion patterns. |
| `.skillminer/tool_events.jsonl` | Local tool event log, ignored by git. |
| `outputs/reports/*.json` | Ingest, mining, recommendation, validation, promotion, evolution, and evaluation reports. |
| `outputs/candidate_skills/<cluster>/` | Generated and evolved skill candidates. |

## Trace Format

Each JSONL trace contains task, project, tool, command, outcome, and optional skill labels:

```json
{
  "id": "T001",
  "task": "Fix failing Python CLI tests caused by import paths",
  "project": {
    "language": "python",
    "frameworks": ["pytest"],
    "files": ["skillminer/cli.py", "tests/test_cli.py"]
  },
  "tools": ["rg", "read", "edit", "pytest"],
  "commands": ["pytest -q"],
  "outcome": "success",
  "used_skills": ["test-failure-repair"],
  "duration_sec": 480,
  "retries": 1,
  "tags": ["testing", "debug"]
}
```

## Development Checks

```powershell
python -m pytest -q
python -m skillminer.cli evaluate --variant evolved --top-k 3 --no-tool-events
```

Expected core invariant for the current phase:

```text
safety_false_negative_rate == 0.0
```

`evaluate --variant evolved` also reports seed vs local evolved held-out deltas, failed evolved-candidate recommendation reasons, per-cluster usefulness status, raw augmented-registry diagnostics, and `memory_summary` counts for validation, duplicate, and promotion feedback. The Phase 2 gate uses a stable overlay: existing skill recommendation order is preserved while candidate discoverability is measured separately.

Current sample-corpus Phase 2 result:

```text
heldout_usefulness_status == improved
heldout_candidate_discovery_status == improved
heldout_recommendation_status == neutral
heldout_evolved_candidate_top_k_hit_rate_delta == 0.1428
safety_false_negative_rate == 0.0
evolved_verifier_pass_rate == 1.0
```

`raw_evolved_mrr_delta` is still reported as a diagnostic because a fully augmented temporary registry can perturb shared TF-IDF/graph ranking context. It is not the Phase 2 gate metric unless candidates are actually promoted into the registry.

## Documentation Map

- `docs/DESIGN.md`: architecture and implementation responsibilities.
- `docs/HANDOFF.md`: current state, commands, verification, and known limits.
- `docs/AUTONOMOUS_EVOLUTION_LOOP.md`: skill self-evolution operating loop and phase plan.
- `docs/HANDOFF_ADVANCED_SKILL_EVOLUTION.md`: next engineering tasks for quality hardening and GEPA adapter work.
- `docs/GEPA_SKILL_EVOLUTION_GUIDE.md`: GEPA integration design.
- `docs/talk_whit_GEPA.md`: APO/GEPA cost-reduction research notes.
