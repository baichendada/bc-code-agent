"""共享安全策略：危险 Shell 命令 / 敏感路径。

单一事实源：主 Agent 的 hooks/tool_policy.py（独立进程）与内置工具
file_tools 的兜底检查都从这里取模式表，避免策略分散两处漂移。

注意：本模块可能被以「security」与「bc_code_agent.security」两个名字加载
（项目内裸名导入 vs hook 脚本的包导入），因此必须保持纯函数/无状态。

这只是教学级黑名单（子串匹配），不是完整沙箱。
"""

from __future__ import annotations

# (模式, 描述)：命中即 deny，任何调用方（主 Agent / 子 Agent / 队友）一律拦截
DANGEROUS_COMMANDS: list[tuple[str, str]] = [
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

# 敏感路径（子串，自动规范化 /）：Write 命中即 deny
SENSITIVE_PATH_PATTERNS: list[str] = [
    ".env",
    ".env.local",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    ".ssh/",
    "secrets/",
    ".aws/credentials",
]


def normalize_path(path: str) -> str:
    """统一路径写法，便于子串匹配（Windows 反斜杠 → /）。"""
    normalized = str(path or "").strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def match_shell_command(command: str) -> tuple[str, str] | None:
    """危险命令检查：命中返回 (pattern, description)，否则 None。"""
    cmd = (command or "").strip()
    if not cmd:
        return None
    for pattern, description in DANGEROUS_COMMANDS:
        if pattern in cmd:
            return pattern, description
    return None


def match_sensitive_path(path: str) -> str | None:
    """敏感路径检查：命中返回匹配的 pattern，否则 None。"""
    p = normalize_path(path)
    if not p:
        return None
    for pattern in SENSITIVE_PATH_PATTERNS:
        if pattern in p:
            return pattern
    return None
