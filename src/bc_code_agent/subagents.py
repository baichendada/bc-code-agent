"""子 Agent：按 profile 限制工具集，独立 context，只向主 Agent 返回摘要。"""

from __future__ import annotations

from typing import Any, Callable

from file_tools import FILE_TOOL_SCHEMAS
from tool_executor import ToolExecutor

SUBAGENT_TYPES = ("explore", "general", "review", "research")

DEFAULT_MAX_TURNS = 25

PROFILES: dict[str, dict[str, Any]] = {
    "explore": {
        "tools": ["Read", "Grep", "Glob"],
        "max_turns": 10,
        "system": (
            "你是代码探索子 Agent。只读：不修改文件，不跑 Shell，不搜索外网。"
            "目标：定位文件、理解实现、回答「在哪 / 怎么工作」。"
            "返回：简短结论 + 关键路径列表；不要贴大段代码。"
        ),
    },
    "general": {
        "tools": ["Read", "Write", "Grep", "Glob", "Shell"],
        "max_turns": 20,
        "system": (
            "你是执行子 Agent。按指令完成有边界的子任务。"
            "返回：做了什么、改了哪些文件、结果或错误。不要猫娘口吻。"
        ),
    },
    "review": {
        "tools": ["Read", "Grep", "Glob"],
        "max_turns": 12,
        "system": (
            "你是代码审查子 Agent。只读：不修改，不跑 Shell，不搜索外网。"
            "目标：找 bug、安全风险、边界问题、与给定标准的不一致。"
            "返回固定结构：\n"
            "## 结论\nPASS / NEEDS_FIX / BLOCKED\n"
            "## Findings\n- [🔴 critical] 文件:行 — 问题 — 建议\n"
            "- [🟡 warning] ...\n- [🔵 nit] ...\n"
            "## 已覆盖范围\n## 未覆盖项\n"
            "不要改代码，只报告。"
        ),
    },
    "research": {
        "tools": ["WebSearch", "Read", "LoadSkill"],
        "max_turns": 15,
        "system": (
            "你是外部调研子 Agent。通过 WebSearch 与 LoadSkill 收集公开信息。"
            "不写本地文件，不跑 Shell，不用 Grep/Glob 搜代码库。"
            "流程：涉及网络搜索时先 LoadSkill(web-search)，再 WebSearch（可换关键词 1～3 次）。"
            "Read 仅用于读取主 Agent 指定的本地参考文件路径。"
            "返回：\n"
            "## 结论要点\n（2～8 条可验证事实）\n"
            "## 来源\n（标题 + URL）\n"
            "## 未覆盖 / 不确定\n"
            "禁止编造未搜索到的内容；结果为空时如实说明。"
        ),
    },
}

LOAD_SKILL_SCHEMA: dict[str, Any] = {
    "name": "LoadSkill",
    "description": (
        "Load the full content of a skill by name. "
        "For web research, call LoadSkill(web-search) before WebSearch."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name from the catalog",
            }
        },
        "required": ["name"],
    },
}

WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "WebSearch",
    "description": "Search the public web for up-to-date information.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Short search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results, 1-10, default 5",
            },
        },
        "required": ["query"],
    },
}

TASK_TOOL_SCHEMA: dict[str, Any] = {
    "name": "Task",
    "description": (
        "Delegate a bounded subtask to a subagent with isolated context. "
        "Subagents cannot delegate further. "
        "Multiple independent Tasks in one turn may run in parallel. "
        "explore=read-only code exploration; "
        "general=read/write/shell execution; "
        "review=read-only code review with graded findings; "
        "research=web search and external investigation (WebSearch/LoadSkill)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "enum": list(SUBAGENT_TYPES),
                "description": "Subagent profile",
            },
            "description": {
                "type": "string",
                "description": "Short title for logging (3-10 words)",
            },
            "prompt": {
                "type": "string",
                "description": "Detailed task instructions for the subagent",
            },
        },
        "required": ["subagent_type", "prompt"],
    },
}


def _schema_by_name(name: str) -> dict[str, Any] | None:
    if name == "LoadSkill":
        return LOAD_SKILL_SCHEMA
    if name == "WebSearch":
        return WEB_SEARCH_SCHEMA
    for schema in FILE_TOOL_SCHEMAS:
        if schema["name"] == name:
            return schema
    return None


def tools_for_profile(profile: str) -> list[dict[str, Any]]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown subagent profile: {profile!r}")
    schemas: list[dict[str, Any]] = []
    for name in PROFILES[profile]["tools"]:
        schema = _schema_by_name(name)
        if schema is not None:
            schemas.append(schema)
    return schemas


def max_turns_for_profile(profile: str) -> int:
    if profile not in PROFILES:
        return DEFAULT_MAX_TURNS
    return int(PROFILES[profile].get("max_turns", DEFAULT_MAX_TURNS))


def run_subagent(
    *,
    client: Any,
    model: str,
    profile: str,
    prompt: str,
    load_skill: Callable[[str], str],
    web_search: Callable[..., str],
    max_tokens: int,
    thinking_type: str,
    reasoning_effort: str,
    track_usage: Callable[[Any, str], None] | None = None,
    permission_checker: Callable[[str, dict], str | None] | None = None,
) -> str:
    if profile not in PROFILES:
        return f"Unknown subagent_type: {profile!r}. Known: {', '.join(SUBAGENT_TYPES)}"

    spec = PROFILES[profile]
    system = spec["system"]
    tools = tools_for_profile(profile)
    max_turns = max_turns_for_profile(profile)
    executor = ToolExecutor(
        allowed=set(spec["tools"]),
        prefix=f"子·{profile}·",
        load_skill=load_skill,
        web_search=web_search,
        permission_checker=permission_checker,
        background_allowed=False,
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt.strip()}]

    for turn in range(max_turns):
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            system=system,
            tools=tools,
            extra_body={
                "thinking": {"type": thinking_type},
                "reasoning_effort": reasoning_effort,
            },
        )
        if track_usage is not None:
            track_usage(message, kind=f"subagent:{profile}")

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": message.content}
        messages.append(assistant_msg)

        if message.stop_reason != "tool_use":
            text = next((b.text for b in message.content if b.type == "text"), "")
            return text.strip() or "(subagent finished with no text)"

        tool_results: list[dict[str, Any]] = []
        for block in message.content:
            if block.type != "tool_use":
                continue
            result = executor.run(block.name, dict(block.input))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return (
        f"Subagent {profile!r} reached max turns ({max_turns}) "
        "without finishing. Try a narrower prompt or split the task."
    )
