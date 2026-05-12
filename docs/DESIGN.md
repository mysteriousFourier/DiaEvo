# SkillMiner MVP Design

## Problem

Static Agent skill systems require users to know which skill exists and when to invoke it. SkillMiner adds a data-mining loop: observe task traces, identify reusable task clusters and tool sequences, rank existing or plugin-backed skills, generate candidate skills for coverage gaps, then verify safety before installation.

## Architecture

1. Data layer: JSONL task traces, seed skill registry, plugin metadata.
2. Mining layer: TF-IDF features, K-Means clustering, association rules, frequent sequence mining, heterogeneous task-skill-tool graph.
3. Recommendation layer: weighted scoring over semantic similarity, rule confidence, PageRank, usage decay, success rate, risk, and cost.
4. Generation layer: creates trace-driven candidate `SKILL.md` directories from high-gap clusters, failure hotspots, and high-reuse tool paths.
5. Verification layer: checks required frontmatter, required candidate sections, optional executable validation metadata, dangerous commands, credential-like text, and parent-path usage.
6. Tool layer: explicit local tool schemas, workspace boundary checks, approval previews, and per-call event logging for agent actions that feeds back into trace ingestion.
7. UI layer: local PowerShell launcher, terminal dashboard, live prompt bar, slash menu, tool result blocks, and DeepSeek chat bridge.
8. Configuration layer: lightweight `.env` reader/writer for DeepSeek model, base URL, and API key.

## Current MVP Choices

The MVP avoids heavy dependencies so it can run in constrained course environments. It uses TF-IDF instead of neural embeddings and in-repo implementations instead of scikit-learn, mlxtend, or networkx. The modules are intentionally isolated so the full project can swap in stronger algorithms later.

The interactive shell is intentionally still a lightweight Python terminal renderer rather than a full Ink/React application. It implements the pieces needed for the MVP: a startup card, workspace trust confirmation, prompt bar, slash command menu, keyboard selection, multiline input through `Ctrl+J`, and DeepSeek chat calls.

The MVP is not yet a full coding-agent runtime. It now has a local tool execution layer with schemas for `web_search`, `web_fetch`, `list_files`, `read_file`, `write_file`, `edit_file`, `delete_file`, `apply_patch`, and `run_shell`; workspace boundary checks; approval previews; and per-call event logs. These tools are exposed through CLI, slash commands, and the interactive DeepSeek chat loop. Model-requested tool calls render as terminal blocks, gated tools require explicit approval, and bounded tool results are fed back into conversation history. Streaming tool progress is still out of scope.

The first autonomous evolution loop is now implemented as `tool_events -> ingest -> mine -> generate -> verify -> recommend -> feedback`. `ingest` folds `.skillminer/tool_events.jsonl` into processed traces by default, `mine` emits generation entrypoints, `generate` writes evidence-backed candidate skills, `verify` enforces the candidate contract, and `recommend` uses configurable weights from `data/recommender_weights.json`.

## Runtime Entry Points

- `.\skillminer.ps1`: primary entry point. With no args it opens the interactive shell; with args it runs the scriptable CLI.
- `.\skillminer-home.ps1`: renders the dashboard only.
- `python -m skillminer.cli <command>`: package-level CLI used by the launcher.
- `python -m ui.terminal_home`: direct dashboard renderer.

The PowerShell launchers set `PYTHONPATH`, `PYTHONUTF8`, and `PYTHONIOENCODING`, then run the project-local `.venv` Python.

## Interactive Commands

- `/ingest`: load `data/sample_traces.jsonl`.
- `/mine`: run clustering, rules, sequences, and graph mining.
- `/recommend <task>`: rank skills for a task.
- `/generate <cluster-id>`: create `outputs/candidate_skills/<cluster-id>/SKILL.md`.
- `/verify <cluster-id/path>`: verify a generated candidate.
- `/demo`: run the full MVP loop.
- `/feedback`: fold `.skillminer/tool_events.jsonl` back into processed traces.
- `/tools`: list local tool schemas and approval requirements.
- `/tool <name> <json|key=value...> [--approve]`: execute a local tool. Gated tools return a preview unless `--approve` is present.
- `/model <name>`: update `DEEPSEEK_MODEL` in `.env`, reset chat config, and redraw the dashboard.
- `/baseurl <url>`: update `DEEPSEEK_BASE_URL` in `.env` and reset chat config.
- `/key [api-key]`: update `DEEPSEEK_API_KEY`; without an argument it uses hidden input.
- `/home`: redraw dashboard.
- `/help`: print command help.
- `/exit`: quit.

Normal non-slash text is sent to DeepSeek using the current `.env` values and local tool schemas.

## File Responsibilities

- `skillminer/cli.py`: scriptable command dispatch and demo pipeline.
- `skillminer/deepseek_chat.py`: DeepSeek-compatible chat completion client.
- `skillminer/env.py`: local dotenv loader plus targeted key writer.
- `skillminer/tool_layer.py`: local tool registry, schemas, workspace boundary checks, approval previews, execution, and event logging.
- `docs/AUTONOMOUS_EVOLUTION_LOOP.md`: paper-to-implementation mapping, Hermes layering, current gaps, and algorithm buckets for the evolution loop.
- `ui/cli_style.py`: dashboard, mascot, trust dialog, terminal colors, and model label.
- `ui/prompt_bar.py`: live prompt rendering, slash menu, keyboard navigation, multiline input.
- `ui/interactive_shell.py`: shell loop, slash command dispatch, DeepSeek chat state.
- `ui/tool_render.py`: terminal rendering for tool call previews and results.
- `ui/terminal_home.py`: dashboard-only entry point.

## Recommended Screenshots

1. `.\skillminer.ps1` after `demo`, showing the dashboard and current model label.
2. Slash menu after typing `/`, including `/model`, `/baseurl`, and `/key`.
3. `outputs/reports/mining_report.json` or terminal output of `.\skillminer.ps1 mine`.
4. Candidate skill generation and `verify` output.

## Future Work

- Add streaming progress and richer status updates for model-driven tool turns.
- Harden web search/fetch with source attribution, bounded summaries, and a more robust provider than best-effort DuckDuckGo HTML parsing.
- Expand codebase file tools with read-before-write staleness tracking, richer patch validation, and diff display controls.
- Add real evaluation metrics: Precision@K, Recall@K, MRR, NDCG.
- Add sandbox replay for historical tasks.
- Add contextual bandit selection for cold start.
- Integrate with Claude Code skill directories only after user confirmation.
- Add PDF-backed citation verification for the final report.
- Replace the lightweight prompt renderer with prompt_toolkit/Textual/Ink if full cursor movement, mouse selection, and richer autocompletion become necessary.
- Add provider abstraction if models beyond DeepSeek-compatible chat completions are needed.
