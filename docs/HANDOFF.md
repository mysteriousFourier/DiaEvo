# SkillMiner Handoff

## Current State

SkillMiner is a working MVP in `D:\codex\skillminer`.
The current shell supports a terminal dashboard, workspace trust confirmation, live prompt bar, slash command menu, keyboard navigation, multiline input with `Ctrl+J`, DeepSeek chat through `.env`, and model-requested local tool calls with approval prompts.

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
- Local tool execution with workspace boundary checks, approval previews, terminal result blocks, and JSONL event logging.
- DeepSeek chat tool bridge with OpenAI-compatible tool schemas, structured tool-call parsing, approval prompts, bounded tool-result messages, and legacy `function_call` support.
- DeepSeek chat smoke test and interactive chat.
- Terminal UI styling and runtime model/base URL/API key configuration through slash commands.

## How To Start

```powershell
cd D:\codex\skillminer
.\skillminer.ps1
```

Useful commands:

```text
/ingest
/mine
/recommend <task>
/generate <cluster-id>
/verify <cluster-id/path>
/demo
/tools
/tool list_files path=. recursive=false
/model <name>
/baseurl <url>
/key
/home
/help
/exit
```

Scriptable examples:

```powershell
.\skillminer.ps1 demo
.\skillminer.ps1 tools
.\skillminer.ps1 tool read_file --arg path=README.md --arg limit=5
.\skillminer.ps1 chat-test --prompt "Summarize what SkillMiner MVP does in one sentence."
.\skillminer.ps1 recommend --task "Generate a test-fix skill for the current project"
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

`/model`, `/baseurl`, and `/key` update `.env` and reset the current chat config.

## File Map

- `README.md`: user-facing quick start and command reference.
- `docs/DESIGN.md`: architecture and design choices.
- `docs/HANDOFF.md`: this handoff document.
- `docs/HANDOFF_AUTONOMOUS_EVOLUTION_RESEARCH.md`: next-phase research brief.
- `skillminer/cli.py`: scriptable CLI and demo pipeline.
- `skillminer/deepseek_chat.py`: DeepSeek-compatible chat client.
- `skillminer/tool_chat.py`: model-facing tool schemas, tool-call parsing, and tool-result message shaping.
- `skillminer/tool_layer.py`: tool schemas, workspace path checks, approval previews, execution handlers, and event logging.
- `ui/interactive_shell.py`: interactive loop, slash dispatch, and chat state.
- `ui/prompt_bar.py`: live prompt, slash menu, keyboard selection, multiline input.
- `ui/tool_render.py`: terminal blocks for tool previews and results.
- `ui/terminal_home.py`: dashboard-only entry point.

## Known Limits

- The terminal UI is a lightweight renderer, not a full Ink/React clone.
- `Ctrl+J` is the supported multiline shortcut. Shift+Enter is not reliably detectable with the current `msvcrt` reader.
- Cursor movement inside the current input buffer is not implemented; editing is append/backspace only.
- Slash menu selection supports up/down, Tab completion, and Enter confirmation for bare command prefixes.
- The DeepSeek client is synchronous and non-streaming.
- Web tools still use best-effort HTML parsing rather than a robust search provider.
- Write staleness tracking is basic; unlike Claude Code, the MVP does not yet require a prior read timestamp before writes or edits.
- Generated skills are never auto-installed.

## Next Step

The next phase is research-first and is documented in [docs/HANDOFF_AUTONOMOUS_EVOLUTION_RESEARCH.md](docs/HANDOFF_AUTONOMOUS_EVOLUTION_RESEARCH.md).
