#!/usr/bin/env python3
"""PreToolUse：危险 Shell deny、高敏 ask、敏感 Write deny、demo 路径改写。"""

from __future__ import annotations

import json
import sys

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

SOURCE_PREFIX = "demo_production/"
TARGET_PREFIX = "sandbox/demo_production/"


def _match(value: str, patterns: list) -> tuple[str, str] | None:
    for item in patterns:
        pattern, description = item if isinstance(item, tuple) else (item, item)
        if pattern in value:
            return pattern, description
    return None


def _normalize(path: str) -> str:
    normalized = str(path).strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


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
        dangerous = _match(command, DANGEROUS_PATTERNS)
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
    path = _normalize(raw_path)
    if path.startswith(SOURCE_PREFIX):
        new_path = TARGET_PREFIX + path[len(SOURCE_PREFIX) :]
        updated_input = dict(tool_input)
        updated_input["path"] = new_path
        path = new_path
        print(
            f"[hook:tool_policy] 写入路径已改写：{raw_path} -> {new_path}",
            file=sys.stderr,
        )

    sensitive = _match(path, SENSITIVE_PATTERNS)
    if sensitive:
        pattern, _ = sensitive
        reason = f"敏感文件写入已拦截：'{path}'（匹配模式：{pattern}）"
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
