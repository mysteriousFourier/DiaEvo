from ui.interactive_shell import ChatConfigState


def test_kg_answer_command_toggles_mode() -> None:
    from ui import interactive_shell

    state = ChatConfigState()
    kg_mode = interactive_shell.KGAnswerMode()

    assert interactive_shell._dispatch_command("/kg_answer on", state, kg_mode) is True
    assert kg_mode.enabled is True

    assert interactive_shell._dispatch_command("/kg_answer off", state, kg_mode) is True
    assert kg_mode.enabled is False


def test_kg_answer_turn_uses_dense_backend(monkeypatch) -> None:
    from ui import interactive_shell

    captured = {}

    def fake_execute_tool(name, args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        return {"status": "ok", "tool": name, "answer": "kg answer"}

    monkeypatch.setattr(interactive_shell, "execute_tool", fake_execute_tool)

    answer = interactive_shell._kg_answer_turn("pytest tool usage", interactive_shell.KGAnswerMode(enabled=True))

    assert answer == "kg answer"
    assert captured["name"] == "kg_answer"
    assert captured["args"]["query"] == "pytest tool usage"
    assert captured["args"]["strict"] is True
    assert captured["args"]["vector_backend"] == "dense"
