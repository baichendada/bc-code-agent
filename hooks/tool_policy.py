#!/usr/bin/env python3
"""PreToolUse：危险 Shell deny、高敏 ask、敏感 Write deny、demo 路径改写。

模式表来自 src/bc_code_agent/security.py（与内置工具兜底共用同一事实源）；
沙箱路径改写是本脚本特有的"教学 demo"行为。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 项目根（hook 脚本 hooks/xxx.py 的上一层）；用于绝对路径相对化
ROOT = Path(__file__).resolve().parents[1]

# 让独立进程脚本也能 import 项目内共享模块
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bc_code_agent import security  # noqa: E402

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

SOURCE_PREFIX = "demo_production/"
TARGET_PREFIX = "sandbox/demo_production/"


def _match(value: str, patterns: list) -> tuple[str, str] | None:
    for item in patterns:
        pattern, description = item if isinstance(item, tuple) else (item, item)
        if pattern in value:
            return pattern, description
    return None


def _emit(decision: str, reason: str, updated_input: dict | None = None) -> None:
    out: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if updated_input is not None:
        out["hookSpecificOutput"]["updatedInput"] = updated_input
    print(json.dumps(out, ensure_ascii=False))


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("invalid stdin json", file=sys.stderr)
        return 1

    tool_name = data.get("tool_name") or ""
    tool_input = dict(data.get("tool_input") or {})

    if tool_name == "Shell":
        command = str(tool_input.get("command") or "")
        dangerous = security.match_shell_command(command)
        if dangerous:
            pattern, description = dangerous
            reason = f"危险命令已拦截：{description}（匹配模式：{pattern}）"
            print(f"[hook:tool_policy] {reason}", file=sys.stderr)
            _emit("deny", reason)
            return 0
        high = _match(command, HIGH_SENSITIVITY)
        if high:
            _, description = high
            _emit("ask", f"需要确认：{description}。命令：{command[:120]}")
            return 0
        return 0

    if tool_name != "Write":
        return 0

    updated_input = None
    raw_path = str(tool_input.get("path", ""))
    path = security.normalize_path(raw_path)
    if path.startswith(SOURCE_PREFIX):
        new_path = TARGET_PREFIX + path[len(SOURCE_PREFIX) :]
        updated_input = dict(tool_input)
        updated_input["path"] = new_path
        path = security.normalize_path(new_path)
        print(
            f"[hook:tool_policy] 写入路径已改写：{raw_path} -> {new_path}",
            file=sys.stderr,
        )

    # 敏感检查口径与内置 Write 一致：按项目根相对路径匹配，
    # 避免项目放在 ...\secrets\repo 时普通写入被绝对路径子串误杀
    check_path = raw_path
    if Path(raw_path).is_absolute():
        try:
            check_path = os.path.relpath(raw_path, ROOT)
        except ValueError:
            pass  # 不同盘符：保持原样（宁严勿松）
    sensitive = security.match_sensitive_path(check_path)
    if sensitive:
        reason = f"敏感文件写入已拦截：'{path}'（匹配模式：{sensitive}）"
        print(f"[hook:tool_policy] {reason}", file=sys.stderr)
        _emit("deny", reason)
        return 0

    if updated_input:
        _emit(
            "allow",
            f"写入路径已改写到沙箱：{updated_input['path']}",
            updated_input,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
