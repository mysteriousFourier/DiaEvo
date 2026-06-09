# DiaEvo

DiaEvo 是一个本地 CLI 工作台，用于从 Agent 的真实任务轨迹中挖掘、推荐、生成、验证和演化可复用技能。skill进化参考了[Hermes-agent](https://github.com/NousResearch/hermes-agent)，工作流与外观参考了[codex](https://github.com/openai/codex)，提高缓存命中率参考了[reasonix](https://github.com/esengine/deepseek-reasonix)

它不是单独的 GEPA 实验脚本，也不是单纯的 `SKILL.md` 生成器。当前产品形态是 `diaevo`：一个同时支持交互式终端和脚本化命令的工具，包含仪表盘、斜杠命令、DeepSeek 聊天、OpenAI 兼容工具调用、需审批的本地工具、轨迹捕获、技能挖掘、候选生成、验证、晋升审核、评估和安全沙盒。

长期目标是让 CLI 从自己的使用中学习：工具调用和任务结果沉淀为轨迹，轨迹产生挖掘证据，证据驱动技能推荐和候选生成，候选技能经过演化、验证、校验、去重和人工审核后进入本地注册表，再影响下一轮推荐和生成。

## 语言策略

DiaEvo 默认中文优先。用户可见内容、交互提示、生成报告、挖掘和知识图谱快照、可见 HTML 页面，以及引导中文用户体验的 prompt 都应使用中文。只有命令名、JSON 字段、代码标识符、外部 API 名、论文题名、引用信息和 `SKILL.md` 必需章节标题等兼容性表面保留英文。

模型对话和工具说明禁止使用 emoji。终端回答默认用 Rich 渲染 Markdown；没有 Rich 或非 TTY 环境下会降级为纯文本。可用 `DIAEVO_OUTPUT=terminal|plain|json` 调整输出策略。

## 当前状态

当前检查点：**Phase 7 安全代码演化研究**。

文档核对日期：**2026-06-02**。本 README、`docs/DESIGN.md`、`docs/AUTONOMOUS_EVOLUTION_LOOP.md` 和 `docs/GEPA_SKILL_EVOLUTION_GUIDE.md` 已按当前 CLI help、模块导入和依赖声明核对。

已实现的保守技能循环：

```text
tool_events -> ingest -> mine -> generate -> evolve -> verify -> validate -> queue-promotion -> promote -> feedback/evaluate
```

已实现的协同 skill 脚本闭环：

```text
generate --with-code
  -> SKILL.md + scripts/skill_flow.py + code_artifacts.json + validation.json
  -> verify
  -> validate in disposable sandbox
  -> review-script
  -> queue-promotion / promote
  -> recommend exposes script or SKILL.md fallback metadata
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
- `generate --with-code` 只生成只读 helper 脚本；脚本未人工审查或未通过 validation 时，推荐结果会回退到纯 `SKILL.md` 执行方式。
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
/learn
/skill
/status
/kg
/talk 现在适合沉淀什么 skill？
/model deepseek-v4-pro
/image screenshots\stage1_desktop.png 这张页面还有哪些 AI 味？
/home
/exit
```

普通文本会按 `.env` 配置发送给 DeepSeek。模型可以请求本地工具；只读工具直接执行，写入、删除、补丁、shell 和网络工具会先显示预览并等待审批。

内部流水线命令仍保留给调试和脚本兼容。交互式终端中运行 `/debug` 可查看 `ingest`、`mine`、`recommend`、`generate`、`verify` 等高级命令；日常不需要输入 cluster id。

图片理解使用 `/image <图片路径或URL> <问题>`，走 OpenAI 兼容的 GLM 视觉配置。默认视觉模型是 `glm-4.6v-flash`，运行时读取 `GLM_VISION_API_KEY`、`GLM_VISION_BASE_URL`、`GLM_VISION_MODEL`、`GLM_VISION_MAX_TOKENS`、`GLM_VISION_TEMPERATURE` 和 `GLM_VISION_TIMEOUT`。进程内视觉请求会串行化，最高并发为 1。`diaevo chat-test --image <path> --prompt "..."` 可用于非交互式 smoke test。

## 常用脚本命令

```powershell
diaevo learn
diaevo status
diaevo skills --query pytest
diaevo ingest --input data/sample_traces.jsonl
diaevo mine
diaevo export-mining-snapshot --date 260513
diaevo recommend --task "fix failing pytest import path" --language python --framework pytest
diaevo generate --cluster-id <cluster-id>
diaevo generate --cluster-id <cluster-id> --with-code
diaevo evolve --cluster-id <cluster-id> --budget 50
diaevo verify --skill <candidate-skill-dir>\evolved
diaevo validate --skill <candidate-skill-dir>\evolved --approve
diaevo queue-promotion --skill <candidate-skill-dir>\evolved
diaevo label-promotion --queue-id <id> --label merge-needed --note "merge with nearest skill"
diaevo rewrite-promotion --queue-id <id> --action auto
diaevo review-script --skill <candidate-skill-dir> --status approved --note "只读脚本已审查" --reviewer <name> --approve
diaevo promote --queue-id <id> --approve
diaevo adapt-skill --source path\to\external-skill
diaevo kg
diaevo kg --no-open --port 8910
diaevo export-kg-snapshot --date 260513
diaevo kg --apply-edit path\to\DiaEvo_kg_edit_260513.json --approve
diaevo answer-kg --query "which tools support pytest traces?" --strict
diaevo feedback
diaevo evaluate --variant evolved --top-k 3
diaevo evaluate-gepa --cluster-id <cluster-id> --budget 50 --top-k 3
diaevo evaluate-gepa-phase4 --cluster-id <cluster-id> --budgets 5,10 --top-k 3 --dry-run
diaevo evaluate-code-evolution --task "fix failing pytest path"
diaevo evaluate-code-evolution --task "fix failing pytest path" --test-command "python -m pytest -q" --collect-baseline
diaevo evaluate-code-evolution --task "fix failing pytest path" --patch-file .tmp\candidate.patch --allowed-path diaevo --test-command "python -m pytest -q" --approve
diaevo tools
diaevo tool read_file --arg path=README.md --arg limit=20
diaevo chat-test --interactive
diaevo-home
```

`diaevo` / `diaevo-home` 会通过 `%LOCALAPPDATA%\DiaEvo\bin` 下的命令 shim 设置 `PYTHONPATH`、`DIAEVO_WORKSPACE` 和 UTF-8 Python I/O，并使用安装目录本地 `.venv` Python。

## QQ 远程入口

普通 `diaevo` 启动时只打开本地交互式终端，不会自动连接 QQ。需要远程入口时，在终端输入 `/qq`；当 `DIAEVO_QQ_ENABLED=true` 时，DiaEvo 会连接 OneBot 11 协议端，接收指定 QQ 号私聊。输入 `/qqquit` 会退出 DiaEvo 的 QQ 远程入口。推荐协议端是 NapCatQQ。DiaEvo 可以按 `.env` 中的启动命令自动拉起 NapCat，但不会内嵌 QQ 登录逻辑；二维码、登录窗口和账号状态仍由 NapCatQQ 负责。

`.env` 示例：

```env
DIAEVO_QQ_ENABLED=true
DIAEVO_QQ_ALLOWED_USERS=123456789
DIAEVO_QQ_ONEBOT_WS_URL=ws://127.0.0.1:3001
DIAEVO_QQ_ONEBOT_HTTP_URL=http://127.0.0.1:3000
DIAEVO_QQ_ACCESS_TOKEN=
DIAEVO_QQ_APPROVAL_TTL_SECONDS=300
DIAEVO_QQ_MAX_MESSAGE_CHARS=1800
DIAEVO_QQ_NAPCAT_AUTOSTART=true
DIAEVO_QQ_NAPCAT_AUTO_INSTALL=true
DIAEVO_QQ_NAPCAT_DOWNLOAD_URL=https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.Windows.OneKey.zip
DIAEVO_QQ_NAPCAT_INSTALL_DIR=
DIAEVO_QQ_NAPCAT_COMMAND=
DIAEVO_QQ_NAPCAT_STARTUP_WAIT_SECONDS=25
```

安装可选依赖后，启动 `diaevo`，再在交互式终端输入 `/qq` 即可同时使用电脑终端和手机 QQ：

```powershell
pip install -e ".[qq]"
diaevo
/qq
```

如果 `DIAEVO_QQ_NAPCAT_AUTOSTART=true`，DiaEvo 会先检查 `DIAEVO_QQ_ONEBOT_WS_URL` 和 `DIAEVO_QQ_ONEBOT_HTTP_URL` 对应端口是否已经监听；没有监听时会自动从 PATH、npm 全局目录、DiaEvo clone/安装目录、当前 workspace 和常见安装目录寻找 NapCat 启动项。若仍未找到且 `DIAEVO_QQ_NAPCAT_AUTO_INSTALL=true`，Windows 下会下载 NapCat 一键包到 DiaEvo clone/安装目录的 `.tmp\napcat` 并从那里启动，再等待 OneBot 服务可连接。`DIAEVO_QQ_NAPCAT_INSTALL_DIR` 可覆盖下载目录；相对路径按 DiaEvo clone/安装目录解析，不按当前 workspace 解析。`DIAEVO_QQ_NAPCAT_COMMAND` 只是可选覆盖项，只有自动发现/自动安装失败或你想指定自定义启动脚本时才需要填写。若启动后仍需要扫码登录，二维码会出现在 NapCatQQ 自己的窗口或控制台中。

别人 clone 项目后，按常规 Python 依赖安装、复制 `.env.example` 为 `.env` 并填好 QQ 白名单和 API key，即可在本机终端用 `/qq` 显式启用；NapCat 下载产物在 clone 目录的 `.tmp/napcat`，不会提交到 git。非 Windows 环境请自行安装 NapCat 并设置 `DIAEVO_QQ_NAPCAT_COMMAND`。

首版只响应 `DIAEVO_QQ_ALLOWED_USERS` 中的私聊 QQ 号。电脑终端输入和 QQ 私聊输入进入同一个交互式会话历史：在电脑前可以直接输入，不在电脑前可用手机继续当前任务。主模型最终回复、工具审批预览、状态和命令输出会同步发给最近发消息的白名单 QQ；工具成功后的中间结果不会额外发送“已完成”占位提示，等主任务最终回复即可。

远程普通文本会作为当前会话的新输入，并会中断当前主任务后交给模型处理；远程斜杠命令会排队走同一套交互式命令，不会打断正在执行的主任务。`/talk <问题>` 是旁路提问：它会读取主会话最近上下文，不写入主历史，不中断当前模型或工具工作，并把回答发回发起这次 `/talk` 的 QQ 用户。写入、删除、patch、shell 和网络工具仍会先生成预览并等待审批；手机可回复 `1`、`同意` 或 `/approve` 允许一次，回复 `2` 表示本轮不再询问该工具，回复 `3`、`拒绝` 或 `/deny` 拒绝。`/key` 和 `/vision-key` 在 QQ 入口禁用，请在本机终端设置密钥。

`diaevo qq-bridge` 仍保留为独立调试入口：它不共享本地交互式终端会话，只适合验证 OneBot 收发、白名单和确认码审批是否工作。

远程消息和审批审计写入 `.diaevo/qq_remote_events.jsonl`，本地工具事件仍写入 `.diaevo/tool_events.jsonl`。QQ 机器人登录和自动化使用可能受平台规则影响，请自行确认账号用途和合规边界。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 交互式工作台 | 默认 `diaevo` 打开终端首页，包含可信工作区确认、仪表盘、斜杠菜单、多行输入、`/home`、`/tools` 和 `/tool`。 |
| 模型聊天 | 通过 `.env` 和运行时 `/model`、`/baseurl`、`/key` 配置 DeepSeek 或 OpenAI 兼容接口；普通文本进入带工具调用的聊天循环。 |
| QQ 远程入口 | 可选配置启用后，交互式终端输入 `/qq` 会通过 OneBot 11/NapCatQQ 接收白名单 QQ 私聊，`/qqquit` 退出远程入口，与本地终端共享同一个交互式会话。 |
| 图像理解 | `/image <path|url> <问题>` 使用 GLM 视觉模型理解图片，默认 `glm-4.6v-flash`，并发上限为 1，结果会写回主会话历史。 |
| 本地工具层 | `list_files`、`read_file`、`write_file`、`edit_file`、`delete_file`、`apply_patch`、`run_shell`、`web_search`、`web_fetch`，带工作区边界、只读/写入分级和审批门；`web_search` 和 `arxiv_search` 的原始结果只用于终端展示，进入主模型上下文前会先由旁路筛选器压缩成相关摘要。 |
| 轨迹捕获与反馈 | 本地工具调用会写入 `.diaevo/tool_events.jsonl`；`ingest` 规范化样例/真实轨迹，`feedback` 将工具事件折叠回可挖掘轨迹。 |
| 挖掘快照 | 使用 TF-IDF、K-Means、关联规则、频繁序列、task-skill-tool 图和覆盖缺口生成 `data/mining_snapshots/YYMMDD/` 可读证据包。 |
| 推荐解释 | `recommend` 综合文本相似度、规则、PageRank、使用记录、成功率、覆盖缺口、风险和成本，并输出每个 skill 的 score explanation、脚本审查状态和 `SKILL.md` 回退原因。 |
| 候选生成 | `generate` 从挖掘簇生成证据支撑的 `SKILL.md` 草稿，保留触发信号、操作步骤、验证建议和风险边界；`--with-code` 可生成只读 helper code、`code_artifacts.json` 和 `validation.json`，默认 `review_status=pending`。 |
| 外部技能适配 | `adapt-skill` 将外部 skill 或 demo 项目转成 DiaEvo 候选技能，仍需 verifier、validation、人工 review 和 promotion。 |
| 技能演化 | `evolve` 用本地指标、Pareto 选择和演化记忆优化候选章节；GEPA/LiteLLM 是可选优化后端，不影响默认 CLI。 |
| 安全验证 | `verify` 检查 frontmatter、必需章节、危险命令、凭据样文本、可疑路径和依赖安装提示。 |
| 沙盒校验 | `validate` 在用户 `--approve` 后，把候选复制到 `.tmp/validation-runs/<id>/workspace` 一次性沙盒中执行 `validation.json` 命令，记录 stdout/stderr/exit code/duration/touched files/diff，并把脚本 validation 摘要写回 `code_artifacts.json`。 |
| 脚本审查 | `review-script` 显式标注 helper 脚本为 `pending`、`approved` 或 `rejected`；脚本未 approved 时，skill 仍可按 `SKILL.md` 回退使用。 |
| 晋升治理 | `queue-promotion`、`label-promotion`、`rewrite-promotion`、`review-script` 和 `promote --approve` 组成显式人工审核链，只更新本地注册表，不自动安装外部技能。 |
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
| `outputs/candidate_skills/<cluster>/code_artifacts.json` | code-backed skill 的脚本入口、审查状态、validation 摘要和回退策略。 |
| `data/mining_snapshots/YYMMDD/` | 可读挖掘快照。 |
| `data/knowledge_graph/current/` | 已审核 active KG 和总体可编辑 HTML 工作台；`kg` 会通过本地 `127.0.0.1` URL 打开它。 |
| `data/knowledge_graph/YYMMDD/` | 显式导出的知识图谱快照。 |
| `.diaevo/tool_events.jsonl` | 本地工具事件日志，默认不提交。 |
| `outputs/reports/*.json` | ingest、mining、recommendation、validation、promotion、evolution、evaluation 报告。 |
| `outputs/candidate_skills/<cluster>/` | 生成和演化后的候选技能。 |

## 第三方库

DiaEvo 核心算法优先使用标准库实现。当前实际用到的第三方库按用途分为：

| 库 | 类型 | 使用位置 | 说明 |
| --- | --- | --- | --- |
| `rich>=13.7` | 默认运行时 | `ui.output_policy` | 终端 Markdown 渲染；无 Rich 或非 TTY 时降级为纯文本。 |
| `pytest>=8.0` | 开发/测试 | `tests/`、开发检查 | 测试运行器，不是普通 CLI 运行所需。 |
| `sentence-transformers` | 可选功能 | `answer-kg --vector-backend dense` | dense 图结构向量检索；默认 TF-IDF 后端不需要它。 |
| `gepa` / LiteLLM stack | 可选优化后端 | `evaluate-gepa` 非 `--dry-run` | GEPA 章节优化；缺失时不影响默认 CLI。 |

`pyproject.toml` 的 `full` extra 还保留 `numpy`、`pandas`、`scikit-learn`、`networkx`、`mlxtend`、`textual` 和 `pyyaml` 作为后续扩展点。当前挖掘、聚类、PageRank、关联规则、序列挖掘、DeepSeek/GLM OpenAI 兼容调用和网络工具都使用标准库实现。

完整说明见 `docs/THIRD_PARTY_LIBRARIES.md`。

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
- `docs/THIRD_PARTY_LIBRARIES.md`：第三方库、可选依赖和标准库实现边界。
- `docs/talk_whit_GEPA.md`：低成本 APO/GEPA 研究备忘。
- `docs/REFERENCES.md`：独立参考文献清单，便于提交材料复用。

## 参考文献

完整参考文献见 `docs/REFERENCES.md`。
