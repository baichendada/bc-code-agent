"""Step 10 · Subagent：独立上下文执行工具，最终摘要回填父循环。

无 API 演示：
    py -3.13 guide/step-10-subagent/agent.py
自检：
    py -3.13 guide/step-10-subagent/agent.py --check
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ModelResponse:
    content: tuple[dict[str, Any], ...]
    stop_reason: str


class ParentModel:
    """剧本模型：第一轮请求 Task，收到摘要后结束。"""

    def create(self, messages: list[dict[str, Any]]) -> ModelResponse:
        last = messages[-1]
        if last["role"] == "user" and isinstance(last["content"], str):
            prompt = "检查当前配置，并给出可用结论"
            return ModelResponse(
                content=(
                    {
                        "type": "tool_use",
                        "id": "parent_task_01",
                        "name": "Task",
                        "input": {"prompt": prompt},
                    },
                ),
                stop_reason="tool_use",
            )

        summary = self._last_tool_result(messages)
        return ModelResponse(
            content=({"type": "text", "text": summary},),
            stop_reason="end_turn",
        )

    @staticmethod
    def _last_tool_result(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message["role"] != "user" or not isinstance(message["content"], list):
                continue
            for block in message["content"]:
                if block.get("type") == "tool_result":
                    return str(block["content"])
        raise AssertionError("parent model did not receive a tool result")


class SubagentModel:
    """剧本模型：先读配置，再输出摘要。"""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content=(
                    {"type": "text", "text": "我先读取配置。"},
                    {
                        "type": "tool_use",
                        "id": "sub_read_01",
                        "name": "read_config",
                        "input": {},
                    },
                ),
                stop_reason="tool_use",
            )

        config_result = ""
        for message in reversed(messages):
            if message["role"] != "user" or not isinstance(message["content"], list):
                continue
            for block in message["content"]:
                if block.get("type") == "tool_result":
                    config_result = str(block["content"])
                    break
            if config_result:
                break

        mode = "offline" if "mode=offline" in config_result else "online"
        model = "bc-mini" if "model=bc-mini" in config_result else "unknown"
        summary = f"配置检查完成：模式为 {mode}，默认模型为 {model}。"
        return ModelResponse(
            content=({"type": "text", "text": summary},),
            stop_reason="end_turn",
        )


def read_config(_: dict[str, Any]) -> str:
    return "mode=offline;model=bc-mini"


SUBAGENT_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "read_config": read_config,
}


class Subagent:
    """一次性子 Agent：独立 messages、小工具集、有限轮次。"""

    def __init__(self, model: SubagentModel) -> None:
        self.model = model
        self.messages: list[dict[str, Any]] = []

    def run(self, prompt: str) -> str:
        self.messages = [{"role": "user", "content": prompt}]
        for _ in range(5):
            response = self.model.create(self.messages)
            self.messages.append(
                {"role": "assistant", "content": list(response.content)}
            )
            calls = [
                block
                for block in response.content
                if block.get("type") == "tool_use"
            ]
            if not calls:
                return str(self.messages[-1]["content"][-1]["text"])

            results = []
            for call in calls:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": self.execute_tool(call["name"], call["input"]),
                    }
                )
            self.messages.append({"role": "user", "content": results})

        raise RuntimeError("subagent reached max turns")

    @staticmethod
    def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "Task":
            raise RuntimeError("Task cannot spawn another Task")
        handler = SUBAGENT_TOOL_HANDLERS.get(tool_name)
        if handler is None:
            raise RuntimeError(f"unknown subagent tool: {tool_name}")
        return handler(tool_input)


class SubagentRunner:
    """父循环的 Task 工具：执行子 Agent，并保留子 transcript 供演示。"""

    def __init__(self) -> None:
        self.last_child_messages: list[dict[str, Any]] = []

    def run_task(self, tool_input: dict[str, Any]) -> str:
        try:
            prompt = str(tool_input["prompt"])
            subagent = Subagent(SubagentModel())
            summary = subagent.run(prompt)
            self.last_child_messages = subagent.messages
            return summary
        except Exception as exc:
            return f"Error: subagent task failed: {type(exc).__name__}: {exc}"


def run_parent_loop() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parent_messages = [
        {"role": "user", "content": "帮我确认当前配置能不能离线运行"}
    ]
    task_tool = SubagentRunner()
    parent_handlers: dict[str, Callable[[dict[str, Any]], str]] = {
        "Task": task_tool.run_task,
    }
    child_messages: list[dict[str, Any]] = []

    for _ in range(5):
        response = ParentModel().create(parent_messages)
        parent_messages.append({"role": "assistant", "content": list(response.content)})
        calls = [block for block in response.content if block.get("type") == "tool_use"]
        if not calls:
            return parent_messages, child_messages

        results = []
        for call in calls:
            handler = parent_handlers.get(call["name"])
            try:
                if handler is None:
                    raise RuntimeError(f"unknown parent tool: {call['name']}")
                summary = handler(call["input"])
            except Exception as exc:
                summary = f"Error: {type(exc).__name__}: {exc}"
            child_messages = task_tool.last_child_messages
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": summary,
                }
            )
        parent_messages.append({"role": "user", "content": results})

    raise RuntimeError("parent loop reached max turns")


def demo() -> None:
    parent_messages, child_messages = run_parent_loop()
    task_call = parent_messages[1]["content"][0]
    summary = parent_messages[2]["content"][0]["content"]
    print(f"parent: Task(prompt={task_call['input']['prompt']!r})")
    print("subagent: read_config()")
    print(f"parent tool_result: {summary}")
    print(f"parent final: {parent_messages[3]['content'][0]['text']}")
    print(f"contexts: parent={len(parent_messages)} child={len(child_messages)}")


def check() -> None:
    parent_messages, child_messages = run_parent_loop()
    task_call = parent_messages[1]["content"][0]
    tool_result = parent_messages[2]["content"][0]
    final_text = parent_messages[3]["content"][0]["text"]

    assert task_call["name"] == "Task"
    assert tool_result["tool_use_id"] == task_call["id"]
    assert tool_result["type"] == "tool_result"
    assert tool_result["content"] == "配置检查完成：模式为 offline，默认模型为 bc-mini。"
    assert final_text == tool_result["content"]

    assert child_messages[0] == {
        "role": "user",
        "content": task_call["input"]["prompt"],
    }

    try:
        Subagent.execute_tool("Task", {})
    except RuntimeError:
        pass
    else:
        raise AssertionError("recursive Task was not rejected")
    assert child_messages[1]["role"] == "assistant"
    assert child_messages[2]["role"] == "user"
    assert child_messages[2]["content"][0]["tool_use_id"] == "sub_read_01"
    assert child_messages[3]["role"] == "assistant"
    assert child_messages is not parent_messages
    assert len(child_messages) == 4

    assert "Task" not in SUBAGENT_TOOL_HANDLERS
    try:
        Subagent.execute_tool("Task", {})
    except RuntimeError as error:
        assert str(error) == "Task cannot spawn another Task"
    else:
        raise AssertionError("recursive Task was allowed")

    error_result = SubagentRunner().run_task({})
    assert error_result.startswith("Error: subagent task failed:")
    assert "KeyError" in error_result

    print("check: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run deterministic assertions")
    args = parser.parse_args()
    check() if args.check else demo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
