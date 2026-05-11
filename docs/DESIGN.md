# SkillMiner MVP Design

## Problem

Static Agent skill systems require users to know which skill exists and when to invoke it. SkillMiner adds a data-mining loop: observe task traces, identify reusable task clusters and tool sequences, rank existing or plugin-backed skills, generate candidate skills for coverage gaps, then verify safety before installation.

## Architecture

1. Data layer: JSONL task traces, seed skill registry, plugin metadata.
2. Mining layer: TF-IDF features, K-Means clustering, association rules, frequent sequence mining, heterogeneous task-skill-tool graph.
3. Recommendation layer: weighted scoring over semantic similarity, rule confidence, PageRank, usage decay, success rate, risk, and cost.
4. Generation layer: creates standard candidate `SKILL.md` directories from high-gap clusters.
5. Verification layer: checks frontmatter, body quality, dangerous commands, credential-like text, and parent-path usage.
6. UI layer: terminal dashboard for demo and screenshots.

## Current MVP Choices

The MVP avoids heavy dependencies so it can run in constrained course environments. It uses TF-IDF instead of neural embeddings and in-repo implementations instead of scikit-learn, mlxtend, or networkx. The modules are intentionally isolated so the full project can swap in stronger algorithms later.

## Recommended Screenshots

1. `python -m ui.terminal_home` after `demo`.
2. `outputs/reports/mining_report.json` or a terminal view of `python -m skillminer.cli mine`.
3. Candidate skill generation and `verify` output.

## Future Work

- Add real evaluation metrics: Precision@K, Recall@K, MRR, NDCG.
- Add sandbox replay for historical tasks.
- Add contextual bandit selection for cold start.
- Integrate with Claude Code skill directories only after user confirmation.
- Add PDF-backed citation verification for the final report.
