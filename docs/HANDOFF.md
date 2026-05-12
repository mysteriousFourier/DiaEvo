# SkillMiner Handoff

## Current State

SkillMiner is a working MVP in `D:\codex\skillminer`. It has a git repository with rollback commits after each major change. The current shell opens with:

```powershell
cd D:\codex\skillminer
.\skillminer.ps1
```

The shell supports a custom terminal dashboard, workspace trust confirmation, live prompt bar, slash command menu, keyboard navigation, multiline input with `Ctrl+J`, DeepSeek chat through `.env`, and model-requested local tool calls with approval prompts.

The latest important commits are:

- Connect local tool execution layer to DeepSeek chat turns (current handoff phase)
- Add local tool execution layer
- `a791869 Adjust mascot accent color`
- `5154d95 Add interactive DeepSeek config commands`
- `aa23310 Apply custom shell colors and mascot`
- `5b7f644 Customize terminal shell styling`
- `9dd5cf9 Prevent empty multiline prompt growth`
- `a12524e Support multiline prompt input`
- `d1a0f25 Support slash menu keyboard selection`

## What Is Implemented

- Trace ingestion from JSONL.
- TF-IDF feature extraction.
- In-repo K-Means clustering.
- Association rule mining.
- Frequent sequence mining.
- Heterogeneous graph scoring.
- Skill recommendation with score explanations.
- Candidate `SKILL.md` generation.
- Static skill verification.
- Claude Code-inspired local tool layer with explicit schemas, workspace boundary checks, approval previews, terminal result blocks, and JSONL event logging.
- DeepSeek chat tool bridge with OpenAI-compatible tool schemas, structured tool-call parsing, interactive approval prompts, bounded tool-result messages, and support for legacy `function_call` responses.
- DeepSeek chat smoke test and interactive chat.
- Claude Code-inspired terminal UI, now customized with local colors and mascot.
- Runtime model/base URL/API key configuration through slash commands.

## Important Commands

Interactive shell:

```text
/ingest
/mine
/recommend 给当前项目生成测试修复 skill
/generate C03
/verify C03
/demo
/tools
/tool list_files path=. recursive=false
/model deepseek-v4-flash
/baseurl https://api.deepseek.com
/key
/home
/help
/exit
```

Scriptable commands:

```powershell
.\skillminer.ps1 demo
.\skillminer.ps1 tools
.\skillminer.ps1 tool read_file --arg path=README.md --arg limit=5
.\skillminer.ps1 chat-test --prompt "用一句话说明 SkillMiner MVP 可以做什么。"
.\skillminer.ps1 recommend --task "给当前项目生成测试修复 skill"
```

## Configuration

Runtime secrets and model settings live in `.env`, which is ignored by git.

Relevant keys:

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_MAX_TOKENS=4096
DEEPSEEK_TEMPERATURE=0.3
DEEPSEEK_REASONING_EFFORT=high
DEEPSEEK_THINKING=enabled
DEEPSEEK_TIMEOUT=60
```

`/model`, `/baseurl`, and `/key` update `.env` and reset the current chat config. `/model` redraws the dashboard so the model label under the mascot updates immediately.

## File Map

- `README.md`: user-facing quick start and command reference.
- `docs/DESIGN.md`: architecture and design choices.
- `docs/HANDOFF.md`: this handoff document.
- `skillminer/cli.py`: scriptable CLI and demo pipeline.
- `skillminer/deepseek_chat.py`: DeepSeek-compatible chat client.
- `skillminer/tool_chat.py`: model-facing tool schemas, tool-call parsing, and tool-result message shaping.
- `skillminer/env.py`: dotenv load/write helpers.
- `skillminer/tool_layer.py`: tool schemas, workspace path checks, approval previews, execution handlers, and `.skillminer/tool_events.jsonl` logging.
- `skillminer/ingest.py`: trace validation and normalization.
- `skillminer/miner.py`: mining orchestration.
- `skillminer/recommender.py`: skill recommendation scoring.
- `skillminer/generator.py`: candidate skill generation.
- `skillminer/verifier.py`: candidate skill safety checks.
- `ui/cli_style.py`: dashboard, trust dialog, colors, mascot, model label.
- `ui/prompt_bar.py`: live prompt, slash menu, keyboard selection, multiline input.
- `ui/interactive_shell.py`: interactive loop, slash dispatch, chat state.
- `ui/tool_render.py`: first-class terminal blocks for tool previews and results.
- `ui/terminal_home.py`: dashboard-only entry point.
- `skillminer.ps1`: primary PowerShell launcher.
- `skillminer-home.ps1`: dashboard launcher.

## Git And Ignored Files

Do not commit:

- `.env`
- `.venv/`
- `.uv-cache/`
- `.skillminer/`
- generated reports under `outputs/reports/`
- generated candidate skills under `outputs/candidate_skills/`
- tool event logs under `.skillminer/tool_events.jsonl`
- `data/processed_traces.jsonl`
- `.idea/`

These are ignored in `.gitignore`. The `.pytest_cache/` directory may show a permission warning during `git status --ignored`; it is not part of source control.

## Verification Checklist

Before handing off a code change, run:

```powershell
.\.venv\Scripts\python.exe -m compileall skillminer ui
.\skillminer.ps1 tools
.\skillminer.ps1 tool read_file --arg path=README.md --arg limit=3
.\skillminer.ps1 demo
.\skillminer-home.ps1
git diff --check
git status --short --ignored
```

`pytest` is listed in `requirements.txt`, but the current `.venv` may not have it installed. If test dependencies are installed later, run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Known Limits

- The terminal UI is a lightweight renderer, not a full Ink/React clone.
- `Ctrl+J` is the supported multiline shortcut. Shift+Enter is not reliably detectable with the current `msvcrt` reader.
- Cursor movement inside the current input buffer is not implemented; editing is append/backspace only.
- Slash menu selection supports up/down, Tab completion, and Enter confirmation for bare command prefixes.
- The DeepSeek client is synchronous and non-streaming.
- Web tools exist as gated `web_search` and `web_fetch` schemas/handlers. They are model-callable but still use best-effort HTML parsing rather than a robust search provider.
- Coding-agent file tools exist as CLI/slash-callable and model-callable local tools for listing, reading, writing, editing, deleting, applying patches, and running shell commands.
- Write staleness tracking is basic; unlike Claude Code, the MVP does not yet require a prior read timestamp before writes/edits.
- Generated skills are never auto-installed.

## Suggested Next Work

- Add streaming tool progress and richer per-turn status display for model-driven tool calls.
- Harden web search/fetch with source attribution. Keep search and fetch separate, save URL/title/snippet/content metadata, and pass only bounded summaries back into the model.
- Add stronger codebase editing semantics. Require read-before-write staleness checks, preserve user changes, prefer structured patches, and record before/after diffs for rollback and later skill mining.
- Add a provider abstraction around `DeepSeekConfig` if OpenAI-compatible, Anthropic, or local models should be selectable.
- Add streaming responses to make chat feel closer to coding-agent CLIs.
- Replace `msvcrt` prompt handling with `prompt_toolkit` if full cursor movement and Shift+Enter are required.
- Add evaluation metrics: Precision@K, Recall@K, MRR, NDCG.
- Add replay-based verification for mined skills.
- Add an explicit install gate for verified skills.
