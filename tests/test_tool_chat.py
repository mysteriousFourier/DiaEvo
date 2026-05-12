import json

from skillminer.tool_chat import (
    assistant_message_for_history,
    chat_tool_schemas,
    requested_tool_calls,
    summarize_tool_result,
    tool_result_message,
    tool_result_message_for_call,
)


def test_chat_tool_schemas_are_openai_compatible() -> None:
    schemas = {item["function"]["name"]: item for item in chat_tool_schemas()}

    read_file = schemas["read_file"]
    write_file = schemas["write_file"]
    assert read_file["type"] == "function"
    assert read_file["function"]["parameters"]["type"] == "object"
    assert "Runs without approval" in read_file["function"]["description"]
    assert "Requires explicit user approval" in write_file["function"]["description"]


def test_requested_tool_calls_parse_arguments() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "README.md", "limit": 3}'},
            }
        ],
    }

    calls = requested_tool_calls(message)

    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "read_file"
    assert calls[0].args == {"path": "README.md", "limit": 3}


def test_invalid_tool_arguments_become_parse_error() -> None:
    calls = requested_tool_calls(
        {
            "tool_calls": [
                {"id": "bad", "function": {"name": "read_file", "arguments": '{"path":'}},
            ]
        }
    )

    assert calls[0].args["__parse_error__"]
    assert calls[0].args["__raw_arguments__"] == '{"path":'


def test_requested_tool_calls_support_legacy_function_call() -> None:
    calls = requested_tool_calls(
        {"function_call": {"name": "list_files", "arguments": '{"path": ".", "recursive": false}'}}
    )

    assert calls[0].id == "function_call"
    assert calls[0].name == "list_files"
    assert calls[0].args == {"path": ".", "recursive": False}
    assert calls[0].legacy is True


def test_tool_result_message_is_bounded_json() -> None:
    message = tool_result_message("call_1", {"status": "ok", "tool": "read_file", "content": "x" * 5000})
    content = json.loads(message["content"])

    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_1"
    assert content["content"].endswith("<truncated 1000 chars>")


def test_tool_result_message_for_legacy_function_call() -> None:
    call = requested_tool_calls({"function_call": {"name": "list_files", "arguments": "{}"}})[0]
    message = tool_result_message_for_call(call, {"status": "ok", "tool": "list_files"})

    assert message["role"] == "function"
    assert message["name"] == "list_files"


def test_assistant_message_for_history_preserves_tool_calls() -> None:
    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]}

    history = assistant_message_for_history(message)

    assert history["role"] == "assistant"
    assert history["content"] == ""
    assert history["tool_calls"] == [{"id": "call_1"}]


def test_summarize_tool_result_has_total_limit() -> None:
    summary = summarize_tool_result({"content": "x" * 20_000}, limit=100)

    assert len(summary) > 100
    assert "<truncated" in summary
