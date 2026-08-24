"""Step 05 · 工具调用：用假响应演示 tool_use 内层循环。

默认不访问网络；加 --check 运行协议自检。
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FakeContent:
    """模拟 Anthropic 响应里的 text / tool_use block。"""

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class FakeResponse:
    """保留 Messages API 最核心的 content 和 stop_reason。"""

    content: tuple[FakeContent, ...]
    stop_reason: str


class ScriptedModel:
    """按固定剧本返回响应，让工具协议可以离线复现。"""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = deque(responses)
        self.turns = 0

    def messages_create(self, messages: list[dict[str, Any]]) -> FakeResponse:
        self.turns += 1
        if not self.responses:
            raise RuntimeError("ScriptedModel exhausted before a final answer")
        return self.responses.popleft()


def text_block(value: str) -> FakeContent:
    return FakeContent(type="text", text=value)


def tool_use_block(id: str, name: str, input: dict[str, Any]) -> FakeContent:
    return FakeContent(type="tool_use", id=id, name=name, input=input)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "add",
        "description": "Add two integers.",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "format_report",
        "description": "Format one integer as a short report line.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}, "title": {"type": "string"}},
            "required": ["value", "title"],
        },
    },
]


def add(a: int, b: int) -> str:
    return str(a + b)


def format_report(value: int, title: str) -> str:
    return f"{title}: {value}"


TOOL_HANDLERS = {"add": add, "format_report": format_report}


def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}"
    try:
        return str(handler(**tool_input))
    except Exception as exc:  # 工具错误也要回到模型，不能让协议中断
        return f"Tool error: {type(exc).__name__}: {exc}"


def agent_loop(model: ScriptedModel, messages: list[dict[str, Any]]) -> str:
    """有 tool_use 就执行并继续；没有 tool_use 就停止。"""
    while True:
        response = model.messages_create(messages)
        print(f"turn {model.turns} stop_reason={response.stop_reason}")
        messages.append({
            "role": "assistant",
            "content": [block.to_dict() for block in response.content],
        })

        tool_calls = [b for b in response.content if b.type == "tool_use"]
        if not tool_calls:
            texts = [block.text or "" for block in response.content]
            return "".join(texts)

        results: list[dict[str, Any]] = []
        for call in tool_calls:
            assert call.id is not None and call.input is not None
            print(f"  tool_use {call.name}({call.input})")
            output = execute_tool(call.name, call.input)
            print(f"  tool_result {output}")
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})


def make_model() -> ScriptedModel:
    add_call = tool_use_block("toolu_add_01", "add", {"a": 3, "b": 4})
    format_call = tool_use_block("toolu_format_01", "format_report",
                                 {"value": 7, "title": "库存"})
    return ScriptedModel([
        FakeResponse((text_block("我先计算合计。"), add_call), "tool_use"),
        FakeResponse((format_call,), "tool_use"),
        FakeResponse((text_block("库存: 7"),), "end_turn"),
    ])


def run_demo() -> None:
    messages = [{"role": "user", "content": "3 加 4 后写成库存报告。"}]
    answer = agent_loop(make_model(), messages)
    print(f"\nfinal answer: {answer}")


def check() -> None:
    model = make_model()
    messages = [{"role": "user", "content": [{"type": "text", "text": "check"}]}]
    answer = agent_loop(model, messages)

    assert model.turns == 3 and answer == "库存: 7"
    assert [message["role"] for message in messages] == [
        "user", "assistant", "user", "assistant", "user", "assistant"
    ]

    used_ids = [b["id"] for m in messages if m["role"] == "assistant"
                for b in m["content"] if b.get("type") == "tool_use"]
    result_ids = [b["tool_use_id"] for m in messages if m["role"] == "user"
                  for b in m["content"] if b.get("type") == "tool_result"]
    assert used_ids == ["toolu_add_01", "toolu_format_01"]
    assert result_ids == used_ids
    assert messages[-1]["content"][0]["type"] == "text"
    print("check: ok")


if __name__ == "__main__":
    check() if "--check" in sys.argv[1:] else run_demo()
