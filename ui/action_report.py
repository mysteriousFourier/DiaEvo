from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

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
        pieces = [f"思考  {self.next_step}"]
        if self.skill_text != "未加载 skill":
            pieces.append(f"已加载 skill：{self.skill_text}。")
        if self.queued_inputs:
            pieces.append(f"另有 {self.queued_inputs} 条输入排队。")
        return " ".join(pieces)


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
    purpose = short_value(last_user, 48) if last_user else "继续处理当前工具结果"
    next_step = (
        "根据刚才的结果继续判断。"
        if round_index
        else "先判断是否需要工具。"
    )
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
            "运行本地命令验证。",
            "command",
            args,
        )
    if call.name == "web_fetch":
        return f"获取网页证据。{_url_suffix(args.get('url'))}".strip()
    if call.name == "web_search":
        return _arg_reason("搜索外部资料。", "query", args)
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
    suffix = f" {key}={short_value(value, 48)}" if value else ""
    return f"{prefix}{suffix}".strip()


def _url_suffix(value: object) -> str:
    if not value:
        return ""
    parsed = urlparse(str(value))
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    return f"来源 {host}" if host else ""
