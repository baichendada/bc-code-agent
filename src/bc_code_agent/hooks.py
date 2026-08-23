"""Hooks：生命周期拦截（Step 12）。

配置对齐 Claude Code：`hooks.json` → Event → Matcher → Handler。
教学扩展：`type: builtin`（进程内）+ `type: command`（stdin/stdout JSON）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

# 主循环内部事件名 ↔ Claude Code 事件名
EVENT_ALIASES: dict[str, str] = {
    "before_tool_call": "PreToolUse",
    "after_tool_call": "PostToolUse",
    "on_stop": "Stop",
    "on_session_start": "SessionStart",
    "on_user_input": "UserPromptSubmit",
    "before_turn": "before_turn",
    "after_turn": "after_turn",
}

TOOL_EVENTS = {"PreToolUse", "PostToolUse", "before_tool_call", "after_tool_call"}


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


def matcher_matches(matcher: str | None, tool_name: str | None) -> bool:
    """对齐 Claude Code：空/* 全匹配；含 |/, 精确集合；否则按正则搜索。"""
    if not matcher or matcher in ("*", ""):
        return True
    if not tool_name:
        return True
    # 仅字母数字 _ | , 时：按备选精确匹配
    if re.fullmatch(r"[A-Za-z0-9_|*,]+", matcher):
        patterns = [p.strip() for p in matcher.replace(",", "|").split("|") if p.strip()]
        if "*" in patterns:
            return True
        return tool_name in patterns
    try:
        return re.search(matcher, tool_name) is not None
    except re.error:
        return tool_name == matcher


class LoggingBuiltin:
    """进程内 before_turn / after_turn 计时（需要跨 emit 共享 ctx）。"""

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


BUILTIN_FACTORIES: dict[str, Callable[[], Any]] = {
    "logging": LoggingBuiltin,
}


class HookBinding:
    """一条已解析的 hook：事件 + matcher + 可调用 handler。"""

    def __init__(
        self,
        event: str,
        matcher: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        name: str = "",
    ) -> None:
        self.event = event
        self.matcher = matcher or "*"
        self.handler = handler
        self.name = name or "hook"


class HookRegistry:
    """按 hooks.json / 注册顺序触发；allow 不短路，deny/ask/block 短路。"""

    def __init__(self) -> None:
        self._bindings: list[HookBinding] = []
        self.session_dir: Path | None = None
        self.project_root: Path | None = None

    def register_binding(self, binding: HookBinding) -> None:
        self._bindings.append(binding)

    def emit(
        self,
        event: str,
        ctx: dict[str, Any] | None = None,
        tool_matcher: str | None = None,
    ) -> Any:
        ctx = {} if ctx is None else ctx
        if self.session_dir and "session_dir" not in ctx:
            ctx["session_dir"] = str(self.session_dir)
        if self.project_root and "cwd" not in ctx:
            ctx["cwd"] = str(self.project_root)

        canonical = EVENT_ALIASES.get(event, event)
        candidates = {event, canonical}

        for binding in self._bindings:
            if binding.event not in candidates:
                # also allow binding registered under alias of emit event
                if EVENT_ALIASES.get(binding.event, binding.event) not in candidates:
                    continue
            if binding.event in TOOL_EVENTS or canonical in TOOL_EVENTS:
                if not matcher_matches(binding.matcher, tool_matcher):
                    continue
            try:
                result = binding.handler(ctx)
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
                print(f"[hook error] {event} in {binding.name}: {exc}")
        return None


def _build_command_payload(event: str, ctx: dict[str, Any]) -> dict[str, Any]:
    canonical = EVENT_ALIASES.get(event, event)
    payload: dict[str, Any] = {
        "hook_event_name": canonical,
        "cwd": ctx.get("cwd"),
        "session_dir": ctx.get("session_dir"),
        "tool_name": ctx.get("name"),
        "tool_input": ctx.get("input"),
        "tool_response": ctx.get("output"),
        "duration_ms": ctx.get("duration_ms"),
        "reply": ctx.get("reply"),
        "todos": ctx.get("todos"),
        "retry": ctx.get("retry"),
        "permission_approved": ctx.get("permission_approved", False),
        "history_len": len(ctx.get("history") or []),
        "model": ctx.get("model"),
    }
    return payload


def _parse_command_result(
    event: str,
    ctx: dict[str, Any],
    stdout: str,
    stderr: str,
    exit_code: int,
) -> Any:
    if stderr.strip():
        # scripts often log to stderr; surface already printed by subprocess inherit
        pass

    canonical = EVENT_ALIASES.get(event, event)

    if exit_code == 2:
        reason = stderr.strip() or stdout.strip() or "hook exit code 2"
        if canonical in ("Stop", "UserPromptSubmit"):
            return HookDecision(action="block", reason=reason)
        return HookDecision(action="deny", reason=reason)

    text = stdout.strip()
    if not text:
        if exit_code != 0:
            print(
                f"[hook warning] {canonical} exited with code {exit_code} and no "
                f"stdout -> treated as allow (fail-open)"
            )
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        if exit_code != 0:
            print(
                f"[hook warning] {canonical} exited with code {exit_code} and "
                f"unparsable stdout -> treated as allow (fail-open): "
                f"{text[:120]!r}"
            )
        return None
    if not isinstance(data, dict):
        return None

    if data.get("decision") == "block":
        return HookDecision(action="block", reason=str(data.get("reason") or ""))

    hso = data.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        hso = {}

    updated_output = hso.get("updatedToolOutput")
    if updated_output is None:
        updated_output = data.get("updatedToolOutput")
    if isinstance(updated_output, str):
        ctx["output"] = updated_output
        ctx["_truncated"] = True

    perm = hso.get("permissionDecision") or data.get("permissionDecision")
    reason = (
        hso.get("permissionDecisionReason")
        or data.get("permissionDecisionReason")
        or data.get("reason")
        or ""
    )
    updated_input = hso.get("updatedInput") or data.get("updatedInput")
    if isinstance(updated_input, dict) or perm:
        action = str(perm or "allow")
        return HookDecision(
            action=action,
            reason=str(reason),
            updated_input=updated_input if isinstance(updated_input, dict) else None,
        )
    return None


def _command_available(name: str) -> str | None:
    """命令可用性检查；WindowsApps 的应用执行别名（stub）会骗过 which，
    但 cmd 执行时返回 9009，必须视为不可用。"""
    path = shutil.which(name)
    if path is None:
        return None
    if os.name == "nt" and "windowsapps" in path.lower():
        return None
    return path


def _normalize_command(command: str) -> str:
    """跨平台命令兼容：Windows 下可能只有 python 没有 python3。"""
    head = command.split(maxsplit=1)[0] if command.strip() else ""
    if head in ("python3", "python"):
        if _command_available(head) is None:
            alt = "python" if head == "python3" else "python3"
            if _command_available(alt) is not None:
                return command.replace(head, alt, 1)
    return command


def make_command_handler(
    *,
    event: str,
    command: str,
    timeout: float,
    project_root: Path,
    name: str,
) -> Callable[[dict[str, Any]], Any]:
    command = _normalize_command(command)

    def handler(ctx: dict[str, Any]) -> Any:
        payload = _build_command_payload(event, ctx)
        try:
            proc = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                shell=True,
                cwd=str(project_root),
            )
        except subprocess.TimeoutExpired:
            print(f"[hook error] {name} timed out after {timeout}s")
            return None
        except OSError as exc:
            print(
                f"[hook error] {name} failed to start: {type(exc).__name__}: {exc} "
                f"-> 该 Hook 将静默放行（fail-open），请检查命令/解释器"
            )
            return None
        if proc.stderr:
            # keep teaching logs visible
            sys.stderr.write(proc.stderr)
            if not proc.stderr.endswith("\n"):
                sys.stderr.write("\n")
        return _parse_command_result(
            event, ctx, proc.stdout or "", proc.stderr or "", proc.returncode
        )

    return handler


def make_builtin_handler(name: str, event: str) -> Callable[[dict[str, Any]], Any]:
    factory = BUILTIN_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"unknown builtin hook: {name}")
    instance = factory()
    method_name = {
        "PreToolUse": "before_tool_call",
        "PostToolUse": "after_tool_call",
        "Stop": "on_stop",
        "SessionStart": "on_session_start",
        "UserPromptSubmit": "on_user_input",
        "before_turn": "before_turn",
        "after_turn": "after_turn",
        "before_tool_call": "before_tool_call",
        "after_tool_call": "after_tool_call",
        "on_stop": "on_stop",
        "on_session_start": "on_session_start",
        "on_user_input": "on_user_input",
    }.get(event, event)
    method = getattr(instance, method_name, None)
    if method is None:
        # same instance for before/after_turn logging
        def _noop(_ctx: dict[str, Any]) -> Any:
            return None

        return _noop

    # share one instance across before/after by storing on factory cache
    return method


# Shared logging instance so before_turn/_start survives into after_turn
_LOGGING = LoggingBuiltin()


def make_builtin_handler_shared(name: str, event: str) -> Callable[[dict[str, Any]], Any]:
    if name == "logging":
        method = getattr(_LOGGING, event, None) or getattr(
            _LOGGING,
            {
                "before_turn": "before_turn",
                "after_turn": "after_turn",
            }.get(event, ""),
            None,
        )
        if method is None:
            return lambda _ctx: None
        return method
    return make_builtin_handler(name, event)


def load_hooks_from_config(
    config_path: Path,
    *,
    project_root: Path,
    session_dir: Path,
) -> HookRegistry:
    """读取 hooks.json（Claude Code 风格）并注册 bindings。"""
    registry = HookRegistry()
    registry.session_dir = Path(session_dir)
    registry.project_root = Path(project_root).resolve()

    path = Path(config_path)
    if not path.is_file():
        print(f"[hooks] 未找到 {path}，使用空注册表")
        return registry

    raw = json.loads(path.read_text(encoding="utf-8"))
    hooks_root = raw.get("hooks") if isinstance(raw, dict) else None
    if not isinstance(hooks_root, dict):
        print(f"[hooks] {path} 缺少 hooks 对象")
        return registry

    count = 0
    for event_name, groups in hooks_root.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher") or "*")
            handlers = group.get("hooks") or []
            if not isinstance(handlers, list):
                continue
            for spec in handlers:
                if not isinstance(spec, dict):
                    continue
                htype = str(spec.get("type") or "command").strip()
                if htype == "command":
                    command = str(spec.get("command") or "").strip()
                    if not command:
                        continue
                    timeout = float(spec.get("timeout") or 30)
                    name = f"command:{command}"
                    registry.register_binding(
                        HookBinding(
                            event=event_name,
                            matcher=matcher,
                            handler=make_command_handler(
                                event=event_name,
                                command=command,
                                timeout=timeout,
                                project_root=registry.project_root,
                                name=name,
                            ),
                            name=name,
                        )
                    )
                    count += 1
                elif htype == "builtin":
                    bname = str(spec.get("name") or "").strip()
                    if not bname:
                        continue
                    registry.register_binding(
                        HookBinding(
                            event=event_name,
                            matcher=matcher,
                            handler=make_builtin_handler_shared(bname, event_name),
                            name=f"builtin:{bname}",
                        )
                    )
                    count += 1
                else:
                    print(f"[hooks] 暂不支持 type={htype!r}，已跳过")

    print(f"[hooks] loaded {count} handler(s) from {path}")
    return registry


# 兼容旧名
def build_default_hooks(session_dir: Path, project_root: Path | None = None) -> HookRegistry:
    root = Path(project_root) if project_root else Path(session_dir).resolve().parents[1]
    return load_hooks_from_config(
        root / "hooks.json",
        project_root=root,
        session_dir=session_dir,
    )
