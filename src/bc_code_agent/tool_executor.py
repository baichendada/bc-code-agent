"""统一工具分发：主 Agent 与子 Agent 共用，prefix 区分日志来源。"""

from __future__ import annotations

from typing import Callable

from file_tools import run_file_tool

_BRIEF_KEYS: dict[str, tuple[str, ...]] = {
    "Read": ("path", "offset", "limit"),
    "Write": ("path",),
    "Grep": ("pattern", "path", "glob"),
    "Glob": ("pattern", "target_directory"),
    "Shell": ("command",),
    "LoadSkill": ("name",),
    "WebSearch": ("query", "max_results"),
    "TodoWrite": (),
    "TodoRead": (),
    "Task": ("subagent_type", "description"),
}


def brief_tool_input(name: str, tool_input: dict) -> str:
    keys = _BRIEF_KEYS.get(name)
    if not keys:
        return repr(tool_input)
    parts: list[str] = []
    for key in keys:
        if key not in tool_input:
            continue
        value = tool_input[key]
        if key == "command" and isinstance(value, str) and len(value) > 80:
            value = value[:80] + "..."
        parts.append(f"{key}={value!r}")
    if name == "TodoWrite" and "todos" in tool_input:
        todos = tool_input["todos"]
        parts.append(f"count={len(todos) if isinstance(todos, list) else 0}")
    if name == "Task" and "prompt" in tool_input:
        prompt = str(tool_input["prompt"])
        if len(prompt) > 60:
            prompt = prompt[:60] + "..."
        parts.append(f"prompt={prompt!r}")
    return ", ".join(parts) if parts else repr(tool_input)


class ToolExecutor:
    """按 allowed 白名单分发工具；prefix 仅用于终端日志。"""

    def __init__(
        self,
        *,
        allowed: set[str] | None = None,
        prefix: str = "",
        load_skill: Callable[[str], str],
        web_search: Callable[..., str],
        todo_write: Callable[[list], str] | None = None,
        todo_read: Callable[[], str] | None = None,
        log: bool = True,
    ) -> None:
        self.allowed = allowed
        self.prefix = prefix
        self.load_skill = load_skill
        self.web_search = web_search
        self.todo_write = todo_write
        self.todo_read = todo_read
        self.log = log

    def _tag(self, name: str) -> str:
        if self.prefix:
            return f"[{self.prefix}{name}]"
        return f"[{name}]"

    def run(self, name: str, tool_input: dict) -> str:
        if self.allowed is not None and name not in self.allowed:
            return f"Tool not allowed: {name}"

        if self.log:
            print(f"{self._tag(name)}: {brief_tool_input(name, tool_input)}")

        file_result = run_file_tool(name, tool_input)
        if file_result is not None:
            if self.log:
                print(f"{self._tag('result')}: {file_result[:500]}")
            return file_result

        if name == "LoadSkill":
            result = self.load_skill(tool_input["name"])
            if self.log:
                print(f"{self._tag('result')}: {result[:500]}")
            return result

        if name == "WebSearch":
            result = self.web_search(
                tool_input.get("query", ""),
                max_results=tool_input.get("max_results", 5),
            )
            if self.log:
                print(f"{self._tag('result')}: {result[:500]}")
            return result

        if name == "TodoWrite":
            if self.todo_write is None:
                return "Tool not allowed: TodoWrite"
            result = self.todo_write(tool_input.get("todos") or [])
            if self.log:
                print(f"{self._tag('result')}: {result[:500]}")
            return result

        if name == "TodoRead":
            if self.todo_read is None:
                return "Tool not allowed: TodoRead"
            result = self.todo_read()
            if self.log:
                print(f"{self._tag('result')}: {result[:500]}")
            return result

        return f"Unknown tool: {name}"
