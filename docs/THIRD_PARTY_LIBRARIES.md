# DiaEvo 第三方库说明

本文按当前代码导入、`pyproject.toml` 和 `requirements.txt` 核对第三方库用途。核对日期：2026-06-02。

## 总体原则

DiaEvo 的核心命令优先使用 Python 标准库实现，降低安装成本和离线运行风险。

当前挖掘、TF-IDF、K-Means、关联规则、频繁序列、PageRank、知识图谱默认检索、DeepSeek/GLM OpenAI 兼容请求、网络工具和沙盒校验都不依赖第三方 HTTP、数据科学或图计算库。

## 当前实际使用

| 库 | 类型 | 声明位置 | 使用位置 | 说明 |
| --- | --- | --- | --- | --- |
| `rich>=13.7` | 默认运行时 | `pyproject.toml`、`requirements.txt` | `ui.output_policy` | 终端 Markdown 渲染。缺失或非 TTY 时降级为纯文本。 |
| `pytest>=8.0` | 开发/测试 | `pyproject.toml[test]`、`requirements.txt` | `tests/` | 测试运行器，不是普通 CLI 运行所需。 |
| `sentence-transformers` | 可选功能 | `pyproject.toml[full]`、`requirements.txt` | `diaevo.knowledge_graph` | 仅在 `answer-kg --vector-backend dense` 或 `DIAEVO_KG_VECTOR_BACKEND=dense` 时动态导入。默认 TF-IDF 后端不需要它。 |
| `gepa` / LiteLLM stack | 可选优化后端 | 文档和运行时报错提示 | `diaevo.gepa_adapter` | 仅在 `evaluate-gepa` 非 `--dry-run` 时动态导入。缺失时默认 CLI、dry-run 和本地演化不受影响。 |

## 保留扩展点

`pyproject.toml` 的 `full` extra 还保留以下库作为后续扩展点：

| 库 | 预期用途 | 当前状态 |
| --- | --- | --- |
| `numpy` | 向量和矩阵计算加速。 | 核心实现未直接导入。 |
| `pandas` | 报表、CSV 和实验数据处理。 | 核心实现未直接导入。 |
| `scikit-learn` | 替代或增强当前标准库 TF-IDF/K-Means。 | 核心实现未直接导入。 |
| `networkx` | 替代或增强当前轻量图算法。 | 核心实现未直接导入。 |
| `mlxtend` | 替代或增强当前关联规则实现。 | 核心实现未直接导入。 |
| `textual` | 后续更完整的 TUI。 | 当前终端 UI 使用自研轻量实现。 |
| `pyyaml` | 后续 YAML 元数据解析。 | 当前 frontmatter/YAML 样式输出使用轻量字符串处理。 |

这些库不应被描述为核心命令的必需依赖，除非对应代码路径开始直接导入并有测试覆盖。

## 外部服务和标准库实现

DeepSeek、GLM 视觉、`web_search`、`web_fetch`、GitHub skill 适配和图片 URL 获取都通过标准库 `urllib`、`socket` 等模块实现，没有引入 `requests`、`httpx` 或 SDK 依赖。

`evaluate-gepa` 的非 dry-run 路径需要用户另行安装 GEPA/LiteLLM 相关栈并配置 API key。这个后端必须保持可选；依赖缺失时只能影响该命令的非 dry-run 优化流程，不能影响默认 `diaevo` 工作台和本地技能循环。

## 文档维护规则

新增第三方库时，需要同步更新：

- `pyproject.toml` 的必需依赖或 optional extra。
- `requirements.txt` 的安装说明。
- 本文档和 README 的第三方库章节。
- 至少一个覆盖新依赖路径的测试或 smoke 命令说明。
