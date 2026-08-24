"""Step 13 · Hooks：在生命周期事件上挂扩展点，不改主循环。

演示 PreToolUse 改写参数 / deny、PostToolUse 截断输出、Stop 放行或阻止。

运行：
    py -3.13 guide/step-13-hooks/agent.py
自检：
    py -3.13 guide/step-13-hooks/agent.py --check
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class HookDecision:
    action: str  # allow | deny | block
    reason: str = ""


@dataclass(frozen=True)
class Binding:
    event: str
    matcher: str
    handler: Callable[[dict[str, Any]], HookDecision | None]
    name: str


class HookRegistry:
    """最小事件总线：按注册顺序执行，deny/block 立即短路。"""

    def __init__(self) -> None:
        self.bindings: list[Binding] = []

    def on(
        self,
        event: str,
        handler: Callable[[dict[str, Any]], HookDecision | None],
        *,
        matcher: str = "*",
        name: str = "hook",
    ) -> None:
        self.bindings.append(Binding(event, matcher, handler, name))

    def emit(self, event: str, ctx: dict[str, Any]) -> HookDecision | None:
        tool_name = str(ctx.get("tool_name", ""))
        for binding in self.bindings:
            if binding.event != event:
                continue
            if binding.matcher not in ("*", tool_name):
                continue
            result = binding.handler(ctx)
            if result and result.action in ("deny", "block"):
                return result
        return None


def make_hooks() -> HookRegistry:
    registry = HookRegistry()

    def normalize_path(ctx: dict[str, Any]) -> HookDecision | None:
        path = str(ctx["input"].get("path", ""))
        if path.startswith("/etc") or path.startswith("C:\\Windows"):
            return HookDecision("deny", f"outside workspace: {path}")
        ctx["input"]["path"] = path.replace("\\", "/")
        return None

    def limit_output(ctx: dict[str, Any]) -> HookDecision | None:
        output = str(ctx.get("output", ""))
        if len(output) > 40:
            ctx["output"] = output[:37] + "..."
        return None

    def require_evidence(ctx: dict[str, Any]) -> HookDecision | None:
        transcript = str(ctx.get("transcript", ""))
        if "tool_result ok" not in transcript:
            return HookDecision("block", "model wants to stop without verification result")
        return None

    registry.on("PreToolUse", normalize_path, matcher="Write", name="normalize-path")
    registry.on("PostToolUse", limit_output, matcher="*", name="truncate-output")
    registry.on("Stop", require_evidence, name="evidence-gate")
    return registry


def run_tool(registry: HookRegistry, tool_name: str, tool_input: dict[str, Any]) -> str:
    """主循环保持稳定：Pre -> execute -> Post。"""
    ctx: dict[str, Any] = {"tool_name": tool_name, "input": dict(tool_input)}
    blocked = registry.emit("PreToolUse", ctx)
    if blocked:
        return f"DENIED: {blocked.reason}"

    output = f"OK {ctx['input']}"
    post_ctx = {"tool_name": tool_name, "output": output}
    registry.emit("PostToolUse", post_ctx)
    return str(post_ctx["output"])


def demo() -> None:
    registry = make_hooks()
    print(run_tool(registry, "Write", {"path": r"src\agent.py", "content": "x"}))
    print(run_tool(registry, "Write", {"path": "/etc/passwd", "content": "x"}))

    long_ctx = {"transcript": "assistant says done"}
    print(registry.emit("Stop", long_ctx))
    good_ctx = {"transcript": "assistant says done; tool_result ok"}
    print(registry.emit("Stop", good_ctx))


def check() -> None:
    registry = make_hooks()
    first = run_tool(registry, "Write", {"path": r"docs\guide.md"})
    assert "docs/guide.md" in first

    denied = run_tool(registry, "Write", {"path": "/etc/hosts"})
    assert denied.startswith("DENIED")

    long_output = run_tool(
        registry,
        "Write",
        {"path": "a.md", "content": "x" * 100},
    )
    assert len(long_output) <= 60

    assert registry.emit("Stop", {"transcript": "no evidence"}) is not None
    assert registry.emit("Stop", {"transcript": "tool_result ok"}) is None
    print("hook checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else demo()
