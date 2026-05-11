# SkillMiner

SkillMiner is a Python MVP for a self-evolving Agent skill acquisition and recommendation system. It ingests task traces, mines reusable workflow patterns, recommends existing skills, generates candidate `SKILL.md` files, and verifies them before installation.

The implementation intentionally keeps the MVP light. It uses standard-library TF-IDF, K-Means, Apriori-style association rules, PrefixSpan-style subsequence mining, and Personalized PageRank. Heavier dependencies such as `sentence-transformers`, `mlxtend`, and `networkx` can replace these modules later without changing the CLI shape.

## Quick Start

```powershell
cd D:\codex\skillminer
.\skillminer.ps1
```

Running `.\skillminer.ps1` with no arguments opens the Claude Code-style terminal shell. On first run, choose `1` to trust the workspace. Then type normal text to chat through DeepSeek, or use slash commands:

```text
/ingest
/mine
/recommend 给当前项目生成测试修复 skill
/generate C03
/verify C03
/demo
/home
/exit
```

Run explicit subcommands when you want scriptable output:

```powershell
.\skillminer.ps1 ingest --input data/sample_traces.jsonl
.\skillminer.ps1 mine
.\skillminer.ps1 recommend --task "给当前项目生成测试修复 skill" --language python --framework pytest
.\skillminer.ps1 generate --cluster-id C03
.\skillminer.ps1 verify --skill outputs/candidate_skills/C03
.\skillminer-home.ps1
```

`skillminer.ps1` is the local PowerShell launcher. It sets `PYTHONPATH`, enables UTF-8 terminal I/O, and runs the project-local `.venv` Python, so the prototype behaves like a project command without requiring a global install.

## Claude Code-Style UI

The UI was replicated by reading the local Claude Code source tree:

- Startup card: `D:\download\claude-code-main\src\components\LogoV2\LogoV2.tsx`
- Clawd terminal logo: `D:\download\claude-code-main\src\components\LogoV2\Clawd.tsx`
- Right-side feeds: `D:\download\claude-code-main\src\components\LogoV2\feedConfigs.tsx`
- Workspace trust dialog: `D:\download\claude-code-main\src\components\TrustDialog\TrustDialog.tsx`
- Bottom prompt and footer: `D:\download\claude-code-main\src\components\PromptInput\PromptInput.tsx`

SkillMiner's matching Python renderer lives in `ui/claude_style.py`, with the shell loop in `ui/interactive_shell.py`. It is not a full Ink/React terminal app; it is a lightweight Python terminal renderer that preserves the same first-screen structure, trust confirmation, orange bordered card, Clawd logo, feed column, prompt box, `? for shortcuts` footer, and `❯` input line.

## DeepSeek Chat Smoke Test

Create a local `.env` from `.env.example`, then fill in your real key:

```powershell
copy .env.example .env
notepad .env
```

Run a one-shot chat test:

```powershell
.\skillminer.ps1 chat-test --prompt "用一句话说明 SkillMiner MVP 可以做什么。"
```

Run a tiny interactive conversation:

```powershell
.\skillminer.ps1 chat-test --interactive
```

The command uses `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` from `.env`. It defaults to `https://api.deepseek.com` and `deepseek-v4-pro`. `DEEPSEEK_MAX_TOKENS` controls maximum output length, not context length; the template uses `4096` as a practical default for testing.

## MVP Commands

- `ingest`: validates JSONL traces and writes `data/processed_traces.jsonl`.
- `mine`: writes `outputs/reports/mining_report.json` with clusters, rules, sequences, and graph stats.
- `recommend`: writes `outputs/reports/recommendations.json` with score explanations.
- `generate`: creates `outputs/candidate_skills/<cluster-id>/SKILL.md`.
- `verify`: checks candidate skill format and static safety.
- `demo`: runs the full loop on sample data.

## Data Format

Each JSONL trace contains:

```json
{
  "id": "T001",
  "task": "给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败",
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

## Extension Points

- Replace `skillminer/features.py` with sentence-transformer embeddings.
- Replace `skillminer/clustering.py` with HDBSCAN or scikit-learn K-Means.
- Replace `skillminer/association_rules.py` with mlxtend FP-Growth.
- Replace `skillminer/skill_graph.py` with networkx for richer heterogeneous graph analysis.
- Add `skillminer/evaluation.py` for Precision@K, Recall@K, MRR, and NDCG experiments.
- Add a richer terminal layer using Textual, prompt_toolkit, or Ink if you want true multi-line editing and live footer navigation.
- Add an installation gate that requires user confirmation before moving verified skills into a live skill directory.

## Safety Boundary

SkillMiner never auto-installs generated skills. `generate` only writes a candidate draft, and `verify` blocks dangerous commands, credential-like text, and suspicious paths. Any skill with scripts, external dependencies, or plugin-backed execution should remain behind explicit user confirmation.
