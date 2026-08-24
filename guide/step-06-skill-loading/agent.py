"""Step 06 · Skill 加载：目录进 system，正文按需进 tool_result。

默认不访问网络；加 --check 运行协议自检。
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


_SKILL_ROWS = (
    (
        "release-review",
        "Checklist before publishing a Python package.",
        "# Release Review\n\n发布前必须完成：\n1. 跑完整测试\n"
        "2. 更新 changelog\n3. 检查版本号\n4. 用干净环境安装一次包\n"
        "DO_NOT_INLINE_MARKER: keep this body out of the catalog.",
    ),
    (
        "sql-style",
        "Write conservative SQL and explain destructive statements.",
        "# SQL Style\n\n只读查询优先；UPDATE/DELETE 必须带 WHERE，并说明影响范围。",
    ),
    (
        "domain-terms",
        "Explain product vocabulary used by this workspace.",
        "# Domain Terms\n\nGuide: 用户手册；Harness: 承载模型的本地程序。",
    ),
)
SKILLS = {name: Skill(name, description, body) for name, description, body in _SKILL_ROWS}


def catalog_prompt() -> str:
    """只渲染 name 和 description，正文留在本地注册表。"""
    lines = [
        "# Available Skills",
        "若某个 description 与任务相关，先 LoadSkill(name)，再按正文执行。",
    ]
    lines += [f"- {skill.name}: {skill.description}" for skill in SKILLS.values()]
    return "\n".join(lines)


SYSTEM_PROMPT = "You are a coding agent. Use tools when they help.\n\n" + catalog_prompt()


@dataclass(frozen=True)
class FakeContent:
    """模拟响应里的 text block 或 tool_use block。"""

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class FakeResponse:
    content: tuple[FakeContent, ...]
    stop_reason: str


class ScriptedModel:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = deque(responses)
        self.turns = 0

    def messages_create(self, messages: list[dict[str, Any]]) -> FakeResponse:
        self.turns += 1
        if not self.responses:
            raise RuntimeError("ScriptedModel exhausted before a final answer")
        return self.responses.popleft()


TOOLS = [{
    "name": "LoadSkill",
    "description": "Load full skill content by exact name before following it.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Exact catalog name."}},
        "required": ["name"],
    },
}]


def load_skill(name: str) -> str:
    skill = SKILLS.get(name)
    if skill is None:
        known = ", ".join(sorted(SKILLS)) or "(none)"
        return f"Skill not found: {name!r}. Known: {known}"
    return f"# Skill: {skill.name}\nDescription: {skill.description}\n\n{skill.body}"


TOOL_HANDLERS = {"LoadSkill": load_skill}


def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}"
    try:
        return str(handler(**tool_input))
    except Exception as exc:
        return f"Tool error: {type(exc).__name__}: {exc}"


def agent_loop(model: ScriptedModel, messages: list[dict[str, Any]]) -> str:
    """LoadSkill 复用 Step 05 的内层工具循环。"""
    while True:
        response = model.messages_create(messages)
        print(f"turn {model.turns} stop_reason={response.stop_reason}")
        messages.append({
            "role": "assistant",
            "content": [block.to_dict() for block in response.content],
        })
        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            return "".join(block.text or "" for block in response.content)

        results = []
        for call in calls:
            assert call.id is not None and call.input is not None
            print(f"  tool_use {call.name}({call.input})")
            output = execute_tool(call.name, call.input)
            print(f"  tool_result {output.splitlines()[0]}")
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})


def make_model() -> ScriptedModel:
    load_call = FakeContent("tool_use", id="toolu_skill_01", name="LoadSkill",
                            input={"name": "release-review"})
    final_text = FakeContent(
        "text",
        text="发布前依次完成：完整测试、changelog、版本号检查、干净环境安装。",
    )
    return ScriptedModel([
        FakeResponse((load_call,), "tool_use"),
        FakeResponse((final_text,), "end_turn"),
    ])


def run_demo() -> None:
    print(f"system prompt:\n{SYSTEM_PROMPT}\n")
    messages = [{"role": "user", "content": "发布前应该检查什么？"}]
    answer = agent_loop(make_model(), messages)
    print(f"\nfinal answer: {answer}")


def blocks(messages: list[dict[str, Any]], role: str, block_type: str) -> list[dict[str, Any]]:
    found = []
    for message in messages:
        content = message.get("content") if message["role"] == role else None
        if isinstance(content, list):
            found.extend(block for block in content if block.get("type") == block_type)
    return found


def check() -> None:
    assert "DO_NOT_INLINE_MARKER" not in SYSTEM_PROMPT
    assert "- release-review: Checklist" in SYSTEM_PROMPT
    loaded = load_skill("release-review")
    assert "DO_NOT_INLINE_MARKER" in loaded and "跑完整测试" in loaded
    assert "release-review" in load_skill("release")

    model = make_model()
    messages = [{"role": "user", "content": "check"}]
    answer = agent_loop(model, messages)
    assert model.turns == 2 and "完整测试" in answer
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    used = blocks(messages, "assistant", "tool_use")
    returned = blocks(messages, "user", "tool_result")
    assert [block["id"] for block in used] == ["toolu_skill_01"]
    assert [block["tool_use_id"] for block in returned] == [block["id"] for block in used]
    print("check: ok")


if __name__ == "__main__":
    check() if "--check" in sys.argv[1:] else run_demo()
