# SkillMiner

SkillMiner is a Python MVP for a self-evolving Agent Skill acquisition and recommendation system. It follows the project plan in `D:\codex\自进化Agent项目计划.md`: ingest task traces, mine reusable patterns, recommend skills, generate candidate `SKILL.md` files, and verify them before installation.

The implementation intentionally keeps the MVP light. It uses standard-library TF-IDF, K-Means, Apriori-style association rules, PrefixSpan-style subsequence mining, and Personalized PageRank. Heavier dependencies such as `sentence-transformers`, `mlxtend`, and `networkx` can replace these modules later without changing the CLI shape.

## Quick Start

```powershell
cd D:\codex\skillminer
python -m skillminer.cli ingest --input data/sample_traces.jsonl
python -m skillminer.cli mine
python -m skillminer.cli recommend --task "给当前项目生成测试修复 skill" --language python --framework pytest
python -m skillminer.cli generate --cluster-id C03
python -m skillminer.cli verify --skill outputs/candidate_skills/C03
python -m ui.terminal_home
```

If the machine does not have `python` on PATH, use `uv` with a project-local cache:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run --with pytest python -m skillminer.cli demo
```

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
- Add an installation gate that requires user confirmation before moving verified skills into a live skill directory.

## Safety Boundary

SkillMiner never auto-installs generated skills. `generate` only writes a candidate draft, and `verify` blocks dangerous commands, credential-like text, and suspicious paths. Any skill with scripts, external dependencies, or plugin-backed execution should remain behind explicit user confirmation.
