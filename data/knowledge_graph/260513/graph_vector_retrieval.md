# 图结构向量检索索引

本文件说明当前 KG 的 GraphRAG-like 检索层：先把已审核节点、关系和声明转成可检索文本，使用本地 TF-IDF 向量召回候选，再沿图结构扩展证据子图。

- 向量后端：`local_tfidf`
- 可检索文档数：`7`
- 词表大小：`34`

## 文档样例

### triple:3c2f130e2629d2cb

- 类型：`triple`
- 非零向量项：`6`
- 主要词项：`3c2f130e2629d2cb, uses_skill, test-failure-repair, triple, t001, trace`
- 文本：triple:3c2f130e2629d2cb T001 USES_SKILL test-failure-repair trace {}

### triple:9694bbd373d8f197

- 类型：`triple`
- 非零向量项：`25`
- 主要词项：`9694bbd373d8f197, describes_task, cli, python, 修复, 入路, 复导, 失败`
- 文本：triple:9694bbd373d8f197 T001 DESCRIBES_TASK 给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败 trace {}

### triple:ee6aad05e48a3218

- 类型：`triple`
- 非零向量项：`6`
- 主要词项：`ee6aad05e48a3218, uses_tool, triple, pytest, t001, trace`
- 文本：triple:ee6aad05e48a3218 T001 USES_TOOL pytest trace {}

### skill:test-failure-repair

- 类型：`entity`
- 非零向量项：`2`
- 主要词项：`skill, test-failure-repair`
- 文本：skill:test-failure-repair skill test-failure-repair

### task:27ef8d58840869

- 类型：`entity`
- 非零向量项：`22`
- 主要词项：`task, 27ef8d58840869, cli, python, 修复, 入路, 复导, 失败`
- 文本：task:27ef8d58840869 task 给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败

### tool:pytest

- 类型：`entity`
- 非零向量项：`2`
- 主要词项：`tool, pytest`
- 文本：tool:pytest tool pytest

### trace:t001

- 类型：`entity`
- 非零向量项：`2`
- 主要词项：`t001, trace`
- 文本：trace:t001 trace T001
