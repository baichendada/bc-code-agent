"""Step 12 · MCP：fake registry 动态发现工具，并入统一 handlers。

无 API 演示：
    py -3.13 guide/step-12-mcp/agent.py
自检：
    py -3.13 guide/step-12-mcp/agent.py --check
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]

    def api_schema(self, global_name: str) -> dict[str, Any]:
        return {
            "name": global_name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class FakeMcpServer:
    name: str
    tools: tuple[McpTool, ...]
    connected: bool = False

    def connect(self) -> None:
        self.connected = True

    def list_tools(self) -> tuple[McpTool, ...]:
        if not self.connected:
            raise RuntimeError(f"MCP server is not connected: {self.name}")
        return self.tools


class FakeMcpRegistry:
    """模拟 MCP server 注册、连接和 list_tools 动态发现。"""

    def __init__(self) -> None:
        self.servers: dict[str, FakeMcpServer] = {}

    def register(self, server: FakeMcpServer) -> None:
        self._validate_name(server.name)
        if server.name in self.servers:
            raise RuntimeError(f"duplicate MCP server: {server.name}")
        server.connect()
        self.servers[server.name] = server

    def discover_tools(self) -> dict[str, McpTool]:
        discovered: dict[str, McpTool] = {}
        for server_name, server in self.servers.items():
            for tool in server.list_tools():
                self._validate_name(tool.name)
                global_name = f"mcp__{server_name}__{tool.name}"
                if global_name in discovered:
                    raise RuntimeError(f"duplicate MCP tool: {global_name}")
                discovered[global_name] = tool
        return discovered

    @staticmethod
    def _validate_name(name: str) -> None:
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError(f"invalid MCP name: {name!r}")
        if "__" in name:
            raise ValueError(f"MCP name cannot contain '__': {name!r}")


def add(tool_input: dict[str, Any]) -> str:
    return str(int(tool_input["a"]) + int(tool_input["b"]))


def weather_now(tool_input: dict[str, Any]) -> str:
    return f"{tool_input['city']}: 26C (offline fake)"


def docs_lookup(tool_input: dict[str, Any]) -> str:
    topic = str(tool_input["topic"])
    return f"{topic}: model requests, host executes"


class ToolHub:
    """内置工具与动态 MCP 工具共享同一个发现和调度入口。"""

    def __init__(self) -> None:
        self.builtin_handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "add": add,
        }
        self.builtin_schemas: dict[str, dict[str, Any]] = {
            "add": {
                "name": "add",
                "description": "Add two integers.",
                "input_schema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                },
            },
        }
        self.mcp_tools: dict[str, McpTool] = {}
        self.handlers = dict(self.builtin_handlers)

    def connect(self, registry: FakeMcpRegistry) -> None:
        discovered = registry.discover_tools()
        handlers: dict[str, Callable[[dict[str, Any]], str]] = dict(
            self.builtin_handlers
        )
        for global_name, tool in discovered.items():
            if global_name in handlers:
                raise RuntimeError(f"tool name conflict: {global_name}")
            handlers[global_name] = tool.handler
        self.mcp_tools = discovered
        self.handlers = handlers

    def tool_specs(self) -> list[dict[str, Any]]:
        specs = list(self.builtin_schemas.values())
        specs.extend(
            tool.api_schema(global_name)
            for global_name, tool in self.mcp_tools.items()
        )
        return specs

    def execute_tool(self, name: str, tool_input: dict[str, Any]) -> str:
        handler = self.handlers.get(name)
        if handler is None:
            raise RuntimeError(f"unknown tool: {name}")
        return handler(tool_input)


def make_registry() -> FakeMcpRegistry:
    registry = FakeMcpRegistry()
    registry.register(
        FakeMcpServer(
            "weather",
            (
                McpTool(
                    "now",
                    "Return deterministic fake weather.",
                    {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    weather_now,
                ),
            ),
        )
    )
    registry.register(
        FakeMcpServer(
            "docs",
            (
                McpTool(
                    "lookup",
                    "Look up a deterministic guide note.",
                    {
                        "type": "object",
                        "properties": {"topic": {"type": "string"}},
                        "required": ["topic"],
                    },
                    docs_lookup,
                ),
            ),
        )
    )
    return registry


def make_hub() -> ToolHub:
    hub = ToolHub()
    hub.connect(make_registry())
    return hub


def demo() -> None:
    hub = make_hub()
    names = [spec["name"] for spec in hub.tool_specs()]
    print(f"tools: {', '.join(names)}")
    print(f"add(2, 5) -> {hub.execute_tool('add', {'a': 2, 'b': 5})}")
    print(
        "mcp__weather__now(shanghai) -> "
        + hub.execute_tool("mcp__weather__now", {"city": "shanghai"})
    )
    print(
        "mcp__docs__lookup(tool-loop) -> "
        + hub.execute_tool("mcp__docs__lookup", {"topic": "tool-loop"})
    )


def check() -> None:
    hub = make_hub()
    specs = hub.tool_specs()
    names = [spec["name"] for spec in specs]
    assert names == ["add", "mcp__weather__now", "mcp__docs__lookup"]
    assert all(
        name.startswith("mcp__") and len(name.split("__")) >= 3
        for name in names
        if name != "add"
    )
    assert set(hub.handlers) == set(names)
    assert hub.execute_tool("add", {"a": 2, "b": 5}) == "7"
    assert (
        hub.execute_tool("mcp__weather__now", {"city": "shanghai"})
        == "shanghai: 26C (offline fake)"
    )
    assert (
        hub.execute_tool("mcp__docs__lookup", {"topic": "tool-loop"})
        == "tool-loop: model requests, host executes"
    )

    registry = make_registry()
    try:
        registry.register(FakeMcpServer("weather", ()))
    except RuntimeError as error:
        assert "duplicate MCP server" in str(error)
    else:
        raise AssertionError("duplicate server was accepted")

    colliding_builtin = ToolHub()
    colliding_builtin.builtin_handlers["mcp__weather__now"] = add
    colliding_builtin.builtin_schemas["mcp__weather__now"] = {
        "name": "mcp__weather__now",
        "description": "collision",
        "input_schema": {"type": "object"},
    }
    try:
        colliding_builtin.connect(make_registry())
    except RuntimeError as error:
        assert "tool name conflict" in str(error)
    else:
        raise AssertionError("global tool conflict was accepted")

    try:
        FakeMcpRegistry._validate_name("bad__name")
    except ValueError as error:
        assert "cannot contain '__'" in str(error)
    else:
        raise AssertionError("reserved name was accepted")

    print("check: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run deterministic assertions")
    args = parser.parse_args()
    demo() if not args.check else check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
