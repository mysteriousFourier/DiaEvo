from __future__ import annotations

from dataclasses import dataclass

from diaevo.tool_chat import RequestedToolCall


@dataclass(frozen=True)
class TurnReport:
    purpose: str
    files: str
    tools: str
    next_step: str
    skill_text: str
    queued_inputs: int = 0

    def render(self) -> str:
        queued = f"；待处理输入：{self.queued_inputs}" if self.queued_inputs else ""
        return (
            f"计划  目标：{self.purpose}；文件：{self.files}；工具：{self.tools}；"
            f"skill：{self.skill_text}{queued}；下一步：{self.next_step}"
        )


def short_value(value: object, limit: int = 80) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def active_skill_names(messages: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for message in messages:
        if message.get("role") != "system":
            continue
        content = str(message.get("content") or "")
        if not content.startswith("[Loaded skill:"):
            continue
        name = content.split("]", 1)[0].removeprefix("[Loaded skill:").strip()
        if name and name not in names:
            names.append(name)
    return names


def build_turn_report(
    messages: list[dict[str, object]],
    round_index: int,
    *,
    queued_inputs: int = 0,
    tools: str = "本地工具按需选择",
) -> TurnReport:
    last_user = next(
        (str(item.get("content") or "") for item in reversed(messages) if item.get("role") == "user"),
        "",
    )
    skills = active_skill_names(messages)
    purpose = short_value(last_user, 96) if last_user else "继续处理当前工具结果"
    next_step = "请求模型决定回答或下一批工具调用" if round_index else "请求模型理解任务并选择下一步"
    return TurnReport(
        purpose=purpose,
        files="由模型按需声明并通过工具确认",
        tools=tools,
        next_step=next_step,
        skill_text=", ".join(skills) if skills else "未加载 skill",
        queued_inputs=queued_inputs,
    )


def tool_reason(call: RequestedToolCall) -> str:
    args = call.args
    if call.name == "list_files":
        path = short_value(args.get("path") or ".")
        return f"查看目录结构，确认接下来该读哪些文件。path={path}"
    if call.name == "read_file":
        return _file_reason("读取相关文件内容，用现有代码和文档支撑下一步判断。", args)
    if call.name == "write_file":
        return _file_reason("写入文件以落地当前修改。", args)
    if call.name == "edit_file":
        return _file_reason("按定位到的片段做局部替换。", args)
    if call.name == "delete_file":
        return _file_reason("删除不再需要的文件或目录。", args)
    if call.name == "apply_patch":
        return "应用补丁，把已确定的代码改动写入工作区。"
    if call.name == "run_shell":
        return _arg_reason(
            "运行本地命令验证或获取结果，通常用于代码检查、测试或环境诊断。"
            "先看 stderr/stdout，再决定下一步。",
            "command",
            args,
        )
    if call.name == "web_fetch":
        return _arg_reason("获取指定网页内容作为外部证据。", "url", args)
    if call.name == "web_search":
        return _arg_reason("搜索外部资料，找到可参考的信息来源。", "query", args)
    if call.name == "arxiv_search":
        return _arg_reason("检索 arXiv 论文元数据，支持学术文献调研。", "query", args)
    if call.name == "recommend_skills":
        return _arg_reason("根据任务推荐可用 skill。", "task", args)
    if call.name == "load_skill_context":
        return _arg_reason("加载指定 skill 的工作流上下文。", "name", args)
    return f"调用 {call.name} 获取完成任务所需的信息或执行结果。"


def _file_reason(prefix: str, args: dict[str, object]) -> str:
    path = args.get("path")
    suffix = f" path={short_value(path)}" if path else ""
    return f"{prefix}{suffix}".strip()


def _arg_reason(prefix: str, key: str, args: dict[str, object]) -> str:
    value = args.get(key)
    suffix = f" {key}={short_value(value)}" if value else ""
    return f"{prefix}{suffix}".strip()
