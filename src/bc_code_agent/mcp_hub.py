"""MCP Host 侧：连接配置中的 servers，把 tools 映射进主 Agent。

MVP：仅主 Agent 使用；默认 stdio 连接 filesystem（限制在项目根）。
工具名：mcp__{server}__{tool}

环境变量 MCP_ENABLED=0 可整体跳过（npx/依赖不可用时快速启动）。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

# 连接启动上限：npx 首次拉包可能慢，但不该让主循环死等
CONNECT_TIMEOUT_SEC = 20


def _content_to_text(result: Any) -> str:
    if getattr(result, "is_error", False):
        prefix = "MCP tool error: "
    else:
        prefix = ""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        # fallback dump
        if hasattr(block, "model_dump"):
            parts.append(json.dumps(block.model_dump(), ensure_ascii=False))
        else:
            parts.append(str(block))
    if parts:
        return prefix + "\n".join(parts)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return prefix + json.dumps(structured, ensure_ascii=False)
    return prefix + "(empty MCP result)"


def anthropic_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def _tool_schema(server_name: str, tool: Any) -> dict[str, Any]:
    """MCP SDK 不同版本 Tool 属性名不同（input_schema / inputSchema），统一兼容。"""
    name = getattr(tool, "name", "")
    input_schema = getattr(tool, "input_schema", None) or getattr(
        tool, "inputSchema", None
    )
    if hasattr(input_schema, "model_dump"):
        input_schema = input_schema.model_dump(by_alias=True, exclude_none=True)
    elif not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    desc = getattr(tool, "description", None) or f"MCP tool {name} from {server_name}"
    return {
        "name": anthropic_name(server_name, name),
        "description": f"[MCP:{server_name}] {desc}",
        "input_schema": input_schema,
    }


def parse_anthropic_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    rest = name[len("mcp__") :]
    server, sep, tool = rest.partition("__")
    if not sep or not server or not tool:
        return None
    return server, tool


class McpHub:
    """后台事件循环上保持 MCP Client 连接；同步 API 供主循环调用。"""

    def __init__(self, config_path: Path, *, default_root: Path) -> None:
        self.config_path = Path(config_path)
        self.default_root = Path(default_root).resolve()
        self._loop = asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._clients: dict[str, Any] = {}
        self._cm_stack: list[Any] = []  # async context managers to exit
        self._tool_schemas: list[dict[str, Any]] = []
        self._tool_index: dict[str, tuple[str, str]] = {}  # anth_name -> (server, tool)
        self._error: str | None = None

    @property
    def tool_schemas(self) -> list[dict[str, Any]]:
        return list(self._tool_schemas)

    def start(self) -> str:
        """启动连接；返回状态摘要。失败不抛，返回错误说明。"""
        if os.getenv("MCP_ENABLED", "1") == "0":
            return "MCP: disabled (MCP_ENABLED=0)"
        if self._thread and self._thread.is_alive():
            return self.status_text()

        self._ready.clear()
        self._error = None

        def runner() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._connect_all())
            except Exception as exc:  # noqa: BLE001
                self._error = f"{type(exc).__name__}: {exc}"
            finally:
                self._ready.set()
            try:
                self._loop.run_forever()
            finally:
                try:
                    self._loop.run_until_complete(self._disconnect_all())
                except Exception:  # noqa: BLE001
                    pass
                self._loop.close()

        self._thread = threading.Thread(target=runner, name="mcp-hub", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=CONNECT_TIMEOUT_SEC)
        return self.status_text()

    def stop(self) -> None:
        if not self._loop.is_running():
            return
        fut = asyncio.run_coroutine_threadsafe(self._disconnect_all(), self._loop)
        try:
            fut.result(timeout=15)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def status_text(self) -> str:
        if self._error:
            return f"MCP: failed — {self._error}"
        if not self._clients:
            return "MCP: no servers connected"
        lines = [f"MCP: {len(self._clients)} server(s), {len(self._tool_schemas)} tool(s)"]
        for name in self._clients:
            n = sum(1 for s, _ in self._tool_index.values() if s == name)
            lines.append(f"  - {name}: {n} tools")
        return "\n".join(lines)

    def catalog_prompt(self) -> str:
        if not self._tool_schemas:
            return ""
        lines = [
            "# MCP Tools",
            "以下工具来自 MCP server，仅主 Agent 可用。命名：`mcp__{server}__{tool}`。",
            "项目内读写可继续用内置 Read/Write/Grep/Glob；MCP 工具适合按 MCP 约定探索同一工作区。",
            "",
        ]
        for schema in self._tool_schemas:
            desc = (schema.get("description") or "").split("\n")[0][:120]
            lines.append(f"- `{schema['name']}`: {desc}")
        lines.append("")
        return "\n".join(lines)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        parsed = parse_anthropic_name(name)
        if parsed is None:
            return f"Not an MCP tool: {name}"
        server, tool = parsed
        if server not in self._clients:
            return f"MCP server not connected: {server}"
        if not self._loop.is_running():
            return "MCP event loop not running."

        async def _call() -> str:
            client = self._clients[server]
            result = await client.call_tool(tool, arguments or {})
            return _content_to_text(result)

        fut = asyncio.run_coroutine_threadsafe(_call(), self._loop)
        try:
            return fut.result(timeout=120)
        except Exception as exc:  # noqa: BLE001
            return f"MCP call failed: {type(exc).__name__}: {exc}"

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            # default filesystem server on project root
            return {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": [
                            "-y",
                            "@modelcontextprotocol/server-filesystem",
                            str(self.default_root),
                        ],
                    }
                }
            }
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    async def _connect_all(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        cfg = self._load_config()
        servers = cfg.get("mcpServers") or {}
        if not isinstance(servers, dict) or not servers:
            self._error = "mcp.json has no mcpServers"
            return

        for server_name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            command = str(spec.get("command") or "").strip()
            args = list(spec.get("args") or [])
            if not command:
                continue
            # substitute ${ROOT}
            args = [
                str(self.default_root) if a in ("${ROOT}", "$ROOT") else str(a)
                for a in args
            ]
            params = StdioServerParameters(command=command, args=args)
            transport_cm = stdio_client(params)
            read, write = await transport_cm.__aenter__()
            self._cm_stack.append(transport_cm)

            session_cm = ClientSession(read, write)
            session = await session_cm.__aenter__()
            self._cm_stack.append(session_cm)
            await session.initialize()
            self._clients[server_name] = session

            listed = await session.list_tools()
            for tool in listed.tools:
                self._tool_schemas.append(_tool_schema(server_name, tool))
                self._tool_index[anthropic_name(server_name, tool.name)] = (
                    server_name,
                    tool.name,
                )
            print(
                f"[MCP] connected `{server_name}` "
                f"({len(listed.tools)} tools) via {command}"
            )

        if not self._clients and not self._error:
            self._error = "no MCP servers started"

    async def _disconnect_all(self) -> None:
        self._clients.clear()
        self._tool_schemas.clear()
        self._tool_index.clear()
        while self._cm_stack:
            cm = self._cm_stack.pop()
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
