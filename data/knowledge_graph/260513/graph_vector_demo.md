# 图结构向量检索演示

本报告展示 KG 回答不是单纯关键词匹配：系统先用向量检索召回相关 KG 文档，再沿 subject-object 图关系扩展证据子图。严格回答只允许使用 accepted 事实。

## 查询：T001 USES_SKILL test-failure-repair

- 检索状态：`ok`
- 检索模式：`graph_vector_tfidf`
- 种子命中数：`3`
- 子图实体数：`4`
- 子图关系数：`3`
- 子图声明数：`0`

### 向量种子

- `triple` `triple:3c2f130e2629d2cb` score=`0.723524`
- `entity` `skill:test-failure-repair` score=`0.368634`
- `entity` `trace:t001` score=`0.302865`

### 图扩展关系

- T001 `USES_SKILL` test-failure-repair (confidence `0.95`)
- T001 `DESCRIBES_TASK` 给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败 (confidence `0.95`)
- T001 `USES_TOOL` pytest (confidence `0.95`)

## 查询：T001 DESCRIBES_TASK 给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败

- 检索状态：`ok`
- 检索模式：`graph_vector_tfidf`
- 种子命中数：`3`
- 子图实体数：`4`
- 子图关系数：`3`
- 子图声明数：`0`

### 向量种子

- `triple` `triple:9694bbd373d8f197` score=`0.94206`
- `entity` `task:27ef8d58840869` score=`0.813366`
- `entity` `trace:t001` score=`0.113033`

### 图扩展关系

- T001 `USES_SKILL` test-failure-repair (confidence `0.95`)
- T001 `DESCRIBES_TASK` 给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败 (confidence `0.95`)
- T001 `USES_TOOL` pytest (confidence `0.95`)

## 查询：T001 USES_TOOL pytest

- 检索状态：`ok`
- 检索模式：`graph_vector_tfidf`
- 种子命中数：`3`
- 子图实体数：`4`
- 子图关系数：`3`
- 子图声明数：`0`

### 向量种子

- `triple` `triple:ee6aad05e48a3218` score=`0.694969`
- `entity` `trace:t001` score=`0.328437`
- `entity` `tool:pytest` score=`0.243615`

### 图扩展关系

- T001 `USES_SKILL` test-failure-repair (confidence `0.95`)
- T001 `DESCRIBES_TASK` 给 Python CLI 项目补 pytest，并修复导入路径导致的测试失败 (confidence `0.95`)
- T001 `USES_TOOL` pytest (confidence `0.95`)
