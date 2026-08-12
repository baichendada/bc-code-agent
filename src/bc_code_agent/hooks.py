"""Hooks：生命周期拦截、注入与审计（Step 12）。

四层模型：Event → Matcher → Handler → Decision
对齐 Claude Code；教学版 Handler 用 Python 类方法。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class HookDecision:
    """结构化决策。deny=拦工具；block=拦结束本轮；ask=需确认；allow=放行。"""

    def __init__(
        self,
        action: str,
        reason: str = "",
        updated_input: dict[str, Any] | None = None,
    ) -> None:
        self.action = action  # allow | deny | ask | block
        self.reason = reason
        self.updated_input = updated_input

    @property
    def is_blocking(self) -> bool:
        return self.action in ("deny", "block")

    def to_message(self) -> str:
        prefix = {
            "deny": "拒绝",
            "block": "阻止",
            "ask": "需要确认",
            "allow": "已放行",
        }
        label = prefix.get(self.action, self.action)
        msg = f"[HookDecision: {label}] {self.reason}"
        if self.updated_input:
            msg += f"（参数已改写：{list(self.updated_input.keys())}）"
        return msg


def confirm_hook_decision(decision: HookDecision) -> bool:
    """ask 决策：TTY 输入 y 才继续；非交互 fail-closed。"""
    print(f"\n[hook:permission] {decision.reason}")
    if not sys.stdin.isatty():
        print("[hook:permission] 当前不是交互式终端，默认拒绝执行。\n")
        return False
    try:
        answer = input(
            "[hook:permission] 是否继续执行？输入 y 继续，其余取消: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


class Hook:
    """Hook 基类：方法名即 Event；matcher 过滤工具名。"""

    name: str = ""
    matcher: str = "*"

    def matches(self, tool_name: str | None) -> bool:
        if not tool_name or self.matcher in ("*", ""):
            return True
        patterns = [p.strip() for p in self.matcher.replace(",", "|").split("|")]
        return tool_name in patterns

    def before_turn(self, ctx: dict[str, Any]) -> Any:
        pass

    def after_turn(self, ctx: dict[str, Any]) -> Any:
        pass

    def before_tool_call(self, ctx: dict[str, Any]) -> Any:
        pass

    def after_tool_call(self, ctx: dict[str, Any]) -> Any:
        pass

    def on_user_input(self, ctx: dict[str, Any]) -> Any:
        pass

    def on_stop(self, ctx: dict[str, Any]) -> Any:
        pass

    def on_session_start(self, ctx: dict[str, Any]) -> Any:
        pass


class HookRegistry:
    """按注册顺序触发；工具事件走 matcher；allow 不短路，deny/ask/block 短路。"""

    def __init__(self) -> None:
        self._hooks: list[Hook] = []

    def register(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def emit(
        self,
        event: str,
        ctx: dict[str, Any] | None = None,
        tool_matcher: str | None = None,
    ) -> Any:
        ctx = {} if ctx is None else ctx
        for hook in self._hooks:
            if tool_matcher and event in ("before_tool_call", "after_tool_call"):
                if not hook.matches(tool_matcher):
                    continue
            method = getattr(hook, event, None)
            if method is None:
                continue
            try:
                result = method(ctx)
                if result is None:
                    continue
                if isinstance(result, HookDecision):
                    if result.action == "allow":
                        if result.updated_input is not None:
                            ctx["input"] = result.updated_input
                            ctx["_hook_updated_input"] = result.updated_input
                            ctx["_hook_updated_reason"] = result.reason
                        continue
                    return result
                return result
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[hook error] {event} in "
                    f"{hook.name or hook.__class__.__name__}: {exc}"
                )
        return None


class LoggingHook(Hook):
    """记录每轮 LLM 耗时与 token。"""

    name = "logging"

    def before_turn(self, ctx: dict[str, Any]) -> Any:
        ctx["_start"] = time.perf_counter()

    def after_turn(self, ctx: dict[str, Any]) -> Any:
        start = ctx.get("_start")
        if start is None:
            return
        duration_ms = (time.perf_counter() - start) * 1000
        usage = ctx.get("usage")
        if usage:
            print(
                f"[hook:logging] turn finished in {duration_ms:.1f}ms | "
                f"input={getattr(usage, 'input_tokens', '?')} "
                f"output={getattr(usage, 'output_tokens', '?')}"
            )
        else:
            print(f"[hook:logging] turn finished in {duration_ms:.1f}ms")


class ToolAuditHook(Hook):
    """写类 / 命令类工具调用记入 session JSONL。"""

    name = "tool_audit"
    matcher = "Write|Shell"

    def __init__(self, audit_file: Path) -> None:
        self.audit_file = Path(audit_file)

    def after_tool_call(self, ctx: dict[str, Any]) -> Any:
        name = ctx.get("name", "")
        entry = {
            "ts": datetime.now().isoformat(),
            "tool": name,
            "input": ctx.get("input"),
            "duration_ms": ctx.get("duration_ms"),
        }
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[hook:tool_audit] {name} 已审计到 {self.audit_file}")


class ToolPolicyHook(Hook):
    """before_tool_call：危险 deny、高敏 ask、敏感路径 deny、演示路径改写。"""

    name = "tool_policy"
    matcher = "Write|Shell"

    SENSITIVE_PATTERNS = [
        ".env",
        ".env.local",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        ".ssh/",
        "secrets/",
        ".aws/credentials",
    ]

    DANGEROUS_PATTERNS = [
        ("rm -rf /", "递归删除根目录"),
        ("rm -rf ~", "递归删除用户目录"),
        ("DROP TABLE", "删除数据库表"),
        ("DROP DATABASE", "删除数据库"),
        ("mkfs.", "格式化文件系统"),
        ("dd if=", "直接磁盘写入"),
        ("> /dev/sda", "覆写磁盘设备"),
        ("chmod 777 /", "开放根目录权限"),
        (":(){ :|:& };:", "fork bomb"),
    ]

    HIGH_SENSITIVITY = [
        ("git push", "推送到远程仓库"),
        ("git commit", "提交代码变更"),
        ("npm publish", "发布 npm 包"),
        ("pip install", "安装 Python 依赖"),
        ("docker build", "构建 Docker 镜像"),
        ("docker push", "推送 Docker 镜像"),
        ("kubectl apply", "应用 Kubernetes 配置"),
        ("terraform apply", "执行 Terraform 变更"),
    ]

    source_prefix = "demo_production/"
    target_prefix = "sandbox/demo_production/"

    def _normalize_path(self, path: str) -> str:
        normalized = str(path).strip().replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def _match_pattern(
        self, value: str, patterns: list
    ) -> tuple[str, str] | None:
        for item in patterns:
            pattern, description = item if isinstance(item, tuple) else (item, item)
            if pattern in value:
                return pattern, description
        return None

    def _rewrite_path_prefix(
        self, path: str, source_prefix: str, target_prefix: str
    ) -> str | None:
        normalized = self._normalize_path(path)
        if normalized.startswith(source_prefix):
            return target_prefix + normalized[len(source_prefix) :]
        return None

    def before_tool_call(self, ctx: dict[str, Any]) -> Any:
        inp = dict(ctx.get("input") or {})
        name = ctx.get("name", "")

        if name == "Shell":
            command = str(inp.get("command") or "")
            dangerous = self._match_pattern(command, self.DANGEROUS_PATTERNS)
            if dangerous:
                pattern, description = dangerous
                reason = f"危险命令已拦截：{description}（匹配模式：{pattern}）"
                print(f"[hook:tool_policy] {reason}")
                return HookDecision(action="deny", reason=reason)

            high = self._match_pattern(command, self.HIGH_SENSITIVITY)
            if high:
                _, description = high
                return HookDecision(
                    action="ask",
                    reason=f"需要确认：{description}。命令：{command[:120]}",
                )
            return None

        if name != "Write":
            return None

        updated_input = None
        raw_path = str(inp.get("path", ""))
        path = self._normalize_path(raw_path)
        new_path = self._rewrite_path_prefix(
            path, self.source_prefix, self.target_prefix
        )
        if new_path:
            updated_input = dict(inp)
            updated_input["path"] = new_path
            path = new_path
            print(f"[hook:tool_policy] 写入路径已改写：{raw_path} -> {new_path}")

        sensitive = self._match_pattern(path, self.SENSITIVE_PATTERNS)
        if sensitive:
            pattern, _ = sensitive
            reason = f"敏感文件写入已拦截：'{path}'（匹配模式：{pattern}）"
            print(f"[hook:tool_policy] {reason}")
            return HookDecision(action="deny", reason=reason)

        if updated_input:
            return HookDecision(
                action="allow",
                reason=f"写入路径已改写到沙箱：{updated_input['path']}",
                updated_input=updated_input,
            )
        return None


class OutputFormattingHook(Hook):
    """截断过长工具输出，防止撑爆上下文。"""

    name = "output_format"
    matcher = "*"

    def __init__(self, max_output_chars: int = 4000) -> None:
        self.max_output_chars = max_output_chars

    def after_tool_call(self, ctx: dict[str, Any]) -> Any:
        output = ctx.get("output", "")
        if isinstance(output, str) and len(output) > self.max_output_chars:
            truncated = (
                output[: self.max_output_chars]
                + f"\n\n[... 输出已截断，原始共 {len(output)} 字符，"
                + f"显示前 {self.max_output_chars} 字符]"
            )
            ctx["output"] = truncated
            ctx["_truncated"] = True
            print(
                f"[hook:output_format] 输出截断：原始 {len(output)} -> "
                f"{self.max_output_chars} 字符"
            )


class StopQualityGateHook(Hook):
    """on_stop：过短回复或未完成 Todo 时 block（主循环最多追一轮）。"""

    name = "stop_quality_gate"

    def on_stop(self, ctx: dict[str, Any]) -> Any:
        reply = ctx.get("reply", "")
        if int(ctx.get("retry") or 0) >= 1:
            return None

        if reply and len(str(reply).strip()) < 10:
            return HookDecision(
                action="block",
                reason="回答似乎不完整（少于10个字符），请检查并重新生成更完整的回复。",
            )

        todos = ctx.get("todos") or []
        unfinished = [
            t
            for t in todos
            if (t.get("status") if isinstance(t, dict) else getattr(t, "status", ""))
            not in ("completed", "cancelled")
        ]
        if unfinished:
            ctx["_has_unfinished_todos"] = True
            return HookDecision(
                action="block",
                reason="仍有未完成待办，Stop Hook 要求继续执行。",
            )
        return None


def build_default_hooks(session_dir: Path) -> HookRegistry:
    """主 Agent 默认 hook 链。"""
    registry = HookRegistry()
    registry.register(LoggingHook())
    registry.register(ToolPolicyHook())
    registry.register(ToolAuditHook(Path(session_dir) / "tool_audit.jsonl"))
    registry.register(OutputFormattingHook(max_output_chars=4000))
    registry.register(StopQualityGateHook())
    return registry
