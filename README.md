# DiaEvo

DiaEvo 是一个本地 CLI 工作台，用于从 Agent 的真实任务轨迹中挖掘、推荐、生成、验证和演化可复用技能。

它不是单独的 GEPA 实验脚本，也不是单纯的 `SKILL.md` 生成器。当前产品形态是 `diaevo`：一个同时支持交互式终端和脚本化命令的工具，包含仪表盘、斜杠命令、DeepSeek 聊天、OpenAI 兼容工具调用、需审批的本地工具、轨迹捕获、技能挖掘、候选生成、验证、晋升审核、评估和安全沙盒。

长期目标是让 CLI 从自己的使用中学习：工具调用和任务结果沉淀为轨迹，轨迹产生挖掘证据，证据驱动技能推荐和候选生成，候选技能经过演化、验证、校验、去重和人工审核后进入本地注册表，再影响下一轮推荐和生成。

## 语言策略

DiaEvo 默认中文优先。用户可见内容、交互提示、生成报告、挖掘和知识图谱快照、可见 HTML 页面，以及引导中文用户体验的 prompt 都应使用中文。只有命令名、JSON 字段、代码标识符、外部 API 名、论文题名、引用信息和 `SKILL.md` 必需章节标题等兼容性表面保留英文。

模型对话和工具说明禁止使用 emoji。终端回答默认用 Rich 渲染 Markdown；没有 Rich 或非 TTY 环境下会降级为纯文本。可用 `DIAEVO_OUTPUT=terminal|plain|json` 调整输出策略。

## 当前状态

当前检查点：**Phase 7 安全代码演化研究**。

已实现的保守技能循环：

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

已实现的知识图谱循环：

```text
traces + tool_events + web_search/web_fetch + 可选 conversation log
  -> 待审核 KG 候选
  -> 已审核 active KG
  -> 可编辑 KG workbench
  -> 可选 /kg_answer on 或 answer-kg --strict
```

当前硬边界：

- 生成技能在验证、校验和人工晋升前都是草稿。
- `validate` 在审批后才执行命令，并且在 `.tmp/validation-runs/<id>/workspace` 的一次性沙盒副本中运行。
- `promote` 只更新 `data/skill_registry.json`，不会安装外部技能。
- 知识图谱候选在审核通过前不是 active facts。
- 严格图谱约束回答只能由用户显式启用；交互式终端用 `/kg_answer on` 进入、`/kg_answer off` 退出。
- Phase 7 代码 patch 只允许在沙盒副本中评估，真实工作区不会被自动改写。
- GEPA 是可选优化后端，不是默认依赖。

## 快速开始

```powershell
cd <DiaEvo安装目录>
.\install-diaevo.ps1
cd D:\path\to\your\workspace
diaevo
```

安装脚本只保留根目录一个入口文件，并在 `%LOCALAPPDATA%\DiaEvo\bin` 生成 `diaevo.cmd` 和 `diaevo-home.cmd` 命令 shim。它会把这个 shim 目录写入当前用户 PATH，并从用户 PATH 中移除旧的项目根目录入口。当前终端和新开的 PowerShell/CMD 都可以直接使用 `diaevo`；在任意目录运行 `diaevo` 都会把当前目录作为 DiaEvo workspace，并打开交互式终端 shell。

交互式命令示例：

```text
/ingest
/mine
/recommend fix failing pytest import path
/generate C03
/verify outputs/candidate_skills/C03
/tools
/tool read_file path=README.md limit=20
/model deepseek-v4-pro
/image screenshots\stage1_desktop.png 这张页面还有哪些 AI 味？
/baseurl https://api.deepseek.com
/key
/home
/exit
```

普通文本会按 `.env` 配置发送给 DeepSeek。模型可以请求本地工具；只读工具直接执行，写入、删除、补丁、shell 和网络工具会先显示预览并等待审批。

图片理解使用 `/image <图片路径或URL> <问题>`，走 OpenAI 兼容的 GLM 视觉配置。默认视觉模型是 `glm-4.6v-flash`，运行时读取 `GLM_VISION_API_KEY`、`GLM_VISION_BASE_URL`、`GLM_VISION_MODEL`、`GLM_VISION_MAX_TOKENS`、`GLM_VISION_TEMPERATURE` 和 `GLM_VISION_TIMEOUT`。进程内视觉请求会串行化，最高并发为 1。`diaevo chat-test --image <path> --prompt "..."` 可用于非交互式 smoke test。

## 常用脚本命令

```powershell
diaevo ingest --input data/sample_traces.jsonl
diaevo mine
diaevo export-mining-snapshot --date 260513
diaevo recommend --task "fix failing pytest import path" --language python --framework pytest
diaevo generate --cluster-id C03
diaevo generate --cluster-id C03 --with-code
diaevo evolve --cluster-id C03 --budget 50
diaevo verify --skill outputs/candidate_skills/C03/evolved
diaevo validate --skill outputs/candidate_skills/C03/evolved --approve
diaevo queue-promotion --skill outputs/candidate_skills/C03/evolved
diaevo label-promotion --queue-id <id> --label merge-needed --note "merge with nearest skill"
diaevo rewrite-promotion --queue-id <id> --action auto
diaevo promote --queue-id <id> --approve
diaevo kg
diaevo kg --no-open --port 8910
diaevo export-kg-snapshot --date 260513
diaevo kg --apply-edit path\to\DiaEvo_kg_edit_260513.json --approve
diaevo answer-kg --query "which tools support pytest traces?" --strict
diaevo feedback
diaevo evaluate --variant evolved --top-k 3
diaevo evaluate-gepa --cluster-id C03 --budget 50 --top-k 3
diaevo evaluate-gepa-phase4 --cluster-id C03 --budgets 5,10 --top-k 3 --dry-run
diaevo evaluate-code-evolution --task "fix failing pytest path"
diaevo evaluate-code-evolution --task "fix failing pytest path" --test-command "python -m pytest -q" --collect-baseline
diaevo evaluate-code-evolution --task "fix failing pytest path" --patch-file .tmp\candidate.patch --allowed-path diaevo --test-command "python -m pytest -q" --approve
diaevo tools
diaevo tool read_file --arg path=README.md --arg limit=20
diaevo chat-test --interactive
diaevo-home
```

`diaevo` / `diaevo-home` 会通过 `%LOCALAPPDATA%\DiaEvo\bin` 下的命令 shim 设置 `PYTHONPATH`、`DIAEVO_WORKSPACE` 和 UTF-8 Python I/O，并使用安装目录本地 `.venv` Python。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 交互式工作台 | 默认 `diaevo` 打开终端首页，包含可信工作区确认、仪表盘、斜杠菜单、多行输入、`/home`、`/tools` 和 `/tool`。 |
| 模型聊天 | 通过 `.env` 和运行时 `/model`、`/baseurl`、`/key` 配置 DeepSeek 或 OpenAI 兼容接口；普通文本进入带工具调用的聊天循环。 |
| 图像理解 | `/image <path|url> <问题>` 使用 GLM 视觉模型理解图片，默认 `glm-4.6v-flash`，并发上限为 1，结果会写回主会话历史。 |
| 本地工具层 | `list_files`、`read_file`、`write_file`、`edit_file`、`delete_file`、`apply_patch`、`run_shell`、`web_search`、`web_fetch`，带工作区边界、只读/写入分级和审批门。 |
| 轨迹捕获与反馈 | 本地工具调用会写入 `.diaevo/tool_events.jsonl`；`ingest` 规范化样例/真实轨迹，`feedback` 将工具事件折叠回可挖掘轨迹。 |
| 挖掘快照 | 使用 TF-IDF、K-Means、关联规则、频繁序列、task-skill-tool 图和覆盖缺口生成 `data/mining_snapshots/YYMMDD/` 可读证据包。 |
| 推荐解释 | `recommend` 综合文本相似度、规则、PageRank、使用记录、成功率、覆盖缺口、风险和成本，并输出每个 skill 的 score explanation。 |
| 候选生成 | `generate` 从挖掘簇生成证据支撑的 `SKILL.md` 草稿，保留触发信号、操作步骤、验证建议和风险边界；`--with-code` 可生成受限 helper code、`code_artifacts.json` 和 `validation.json`。 |
| 技能演化 | `evolve` 用本地指标、Pareto 选择和演化记忆优化候选章节；GEPA/LiteLLM 是可选优化后端，不影响默认 CLI。 |
| 安全验证 | `verify` 检查 frontmatter、必需章节、危险命令、凭据样文本、可疑路径和依赖安装提示。 |
| 沙盒校验 | `validate` 在用户 `--approve` 后，把候选复制到 `.tmp/validation-runs/<id>/workspace` 一次性沙盒中执行 `validation.json` 命令，并记录 stdout/stderr/exit code/duration/touched files/diff。 |
| 晋升治理 | `queue-promotion`、`label-promotion`、`rewrite-promotion` 和 `promote --approve` 组成显式人工审核链，只更新本地注册表，不自动安装外部技能。 |
| 知识图谱治理 | `build-kg-delta`、`review-kg-delta`、`apply-kg-delta` 是隐藏底层命令；accepted 实体、三元组、声明和证据路径写入 `data/knowledge_graph/current/`。 |
| 可编辑总体 KG | `kg` / `/kg` 绑定本地端口并自动打开 active KG 的总体可视化 URL，默认不生成日期快照；只有显式 `export-kg-snapshot` 或 `kg --output-dir ...` 才导出快照。 |
| 严格 KG 回答 | 交互式终端可用 `/kg_answer on` 进入图谱约束回答模式，`/kg_answer off` 退出；CLI 可用 `answer-kg --strict`，工具层可手动调用 `kg_answer(strict=true)`。 |
| 图结构向量检索 | accepted KG 节点、三元组和声明会转换成可检索文档；默认使用可复现 TF-IDF，`answer-kg --vector-backend dense` 使用 `sentence-transformers` dense embedding 召回种子，再扩展图证据子图。默认 HF 镜像是 `https://hf-mirror.com`。 |
| 评估报告 | `evaluate`、`evaluate-gepa`、`evaluate-gepa-phase4` 输出 baseline/evolved、held-out、重复率、安全 holdout、人工反馈记忆和 GEPA 对比报告。 |
| Phase 7 代码演化研究 | `evaluate-code-evolution` 默认只生成 patch strategy；`--collect-baseline` 在沙盒中收集失败/通过测试证据；提供 patch 时只在沙盒中应用和验证，不直接改真实工作区。 |

## 数据文件

| 路径 | 用途 |
| --- | --- |
| `data/sample_traces.jsonl` | 样例任务轨迹。 |
| `data/processed_traces.jsonl` | `ingest` 或 `feedback` 写出的规范化轨迹。 |
| `data/skill_registry.json` | 本地技能注册表。 |
| `data/plugin_metadata.json` | 插件能力元数据。 |
| `data/recommender_weights.json` | 推荐排序权重。 |
| `data/evolution_memory.json` | 成功模板、错误模式、验证反馈、重复反馈和晋升反馈。 |
| `data/mining_snapshots/YYMMDD/` | 可读挖掘快照。 |
| `data/knowledge_graph/current/` | 已审核 active KG 和总体可编辑 HTML 工作台；`kg` 会通过本地 `127.0.0.1` URL 打开它。 |
| `data/knowledge_graph/YYMMDD/` | 显式导出的知识图谱快照。 |
| `.diaevo/tool_events.jsonl` | 本地工具事件日志，默认不提交。 |
| `outputs/reports/*.json` | ingest、mining、recommendation、validation、promotion、evolution、evaluation 报告。 |
| `outputs/candidate_skills/<cluster>/` | 生成和演化后的候选技能。 |

## 开发检查

```powershell
python -m pytest -q
python -m diaevo.cli evaluate --variant evolved --top-k 3 --no-tool-events
```

当前必须保持的核心不变量：

```text
safety_false_negative_rate == 0.0
```

当前样例语料的 Phase 2 指标仍作为回归参照：

```text
heldout_usefulness_status == improved
heldout_candidate_discovery_status == improved
heldout_recommendation_status == neutral
heldout_evolved_candidate_top_k_hit_rate_delta == 0.1428
safety_false_negative_rate == 0.0
evolved_verifier_pass_rate == 1.0
```

## 文档地图

- `docs/DESIGN.md`：架构、模块职责和阶段路线图。
- `docs/AUTONOMOUS_EVOLUTION_LOOP.md`：自演化循环、阶段策略和代码进化门控计划。
- `docs/GEPA_SKILL_EVOLUTION_GUIDE.md`：GEPA 集成边界和 evaluator 合约。
- `docs/talk_whit_GEPA.md`：低成本 APO/GEPA 研究备忘。
- `docs/FINAL_PROJECT_REPORT.md`：项目报告、实验结果和完整参考文献。
- `docs/REFERENCES.md`：独立参考文献清单，便于提交材料复用。

## 参考文献

完整参考文献见 `docs/REFERENCES.md`。
