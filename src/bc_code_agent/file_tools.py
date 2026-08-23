"""语义化文件/搜索工具 + 受限 Shell。"""

from __future__ import annotations

import os
import re
import subprocess
import fnmatch
from pathlib import Path
from typing import Any

from security import match_sensitive_path, match_shell_command

# 慢命令防护：Shell 默认超时（秒）
SHELL_TIMEOUT = 120

# Read 单文件上限：防止把超大文件整读进内存
MAX_READ_BYTES = 2_000_000

# 搜索要跳过的目录（Grep 的 Python fallback / Glob 通用排除；sandbox 是演示产物，不排除）
EXCLUDE_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "sessions",
}

WORKSPACE: Path | None = None

FILE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "Read",
        "description": "Read a file from the workspace. Prefer over Shell for viewing file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to project root or absolute)",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based line number to start reading from",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "Write",
        "description": "Write or overwrite a file in the workspace. Creates parent directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (relative to project root or absolute)",
                },
                "contents": {
                    "type": "string",
                    "description": "Full file contents to write",
                },
            },
            "required": ["path", "contents"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents by regex. Prefer over Shell rg/grep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: project root)",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional glob filter, e.g. *.py",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Max matches to return (default 200)",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files by glob pattern. Prefer over Shell find/ls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. **/*.py",
                },
                "target_directory": {
                    "type": "string",
                    "description": "Directory to search (default: project root)",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Shell",
        "description": (
            "Run a shell command for tasks not covered by Read/Write/Grep/Glob "
            "(e.g. date, git, python scripts). Do not use for reading/writing files or searching code.\n"
            "background=true runs a slow independent command in the background: returns a task id "
            "immediately; the result is injected as a [Background] notification in a later turn. "
            "Only use for commands you do NOT need to block on (e.g. install, full test suite)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "background": {
                    "type": "boolean",
                    "description": "true = background execution (immediate task id, result notification later)",
                },
            },
            "required": ["command"],
        },
    },
]


def set_workspace(root: Path) -> None:
    global WORKSPACE
    WORKSPACE = root.resolve()


def _workspace_root() -> Path:
    if WORKSPACE is None:
        raise RuntimeError("file_tools workspace not configured")
    return WORKSPACE


def resolve_path(path: str) -> Path:
    raw = (path or "").strip()
    if not raw:
        raise ValueError("Empty path")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _workspace_root() / p
    resolved = p.resolve()

    try:
        resolved.relative_to(_workspace_root())
    except ValueError as exc:
        raise ValueError(f"Path outside workspace: {path}") from exc
    return resolved


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(_workspace_root()))
    except ValueError:
        return str(path)


def read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    file_path = resolve_path(path)
    if not file_path.is_file():
        return f"Error: not a file: {_display_path(file_path)}"

    total_bytes = file_path.stat().st_size
    truncated = total_bytes > MAX_READ_BYTES
    if truncated:
        # 按字节截断（中文等宽字符 3 字节/字，read(N) 字符数会偏差）
        with file_path.open("rb") as f:
            data = f.read(MAX_READ_BYTES)
        text = data.decode("utf-8", errors="replace")
    else:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    start = max((offset or 1) - 1, 0)
    end = start + limit if limit is not None else None
    if start >= len(lines):
        return f"Error: offset {offset} beyond file length ({len(lines)} lines)"

    selected = lines[start:end]
    width = len(str(start + len(selected)))
    numbered = [
        f"{str(i).rjust(width)}|{line}" for i, line in enumerate(selected, start=start + 1)
    ]
    header = f"{_display_path(file_path)} ({len(lines)} lines)"
    body = "\n".join(numbered)
    if truncated:
        body += (
            f"\n\n[... 文件过大，仅显示前 {MAX_READ_BYTES} 字节"
            f"（原始 {total_bytes} 字节）]"
        )
    return header + "\n" + body


def write_file(path: str, contents: str) -> str:
    file_path = resolve_path(path)
    # 按 workspace 相对路径做敏感检查：避免工作区路径本身含 secrets/.env 等词被误杀
    # （与 hooks/tool_policy.py 对原始相对路径的口径保持一致）
    try:
        rel = str(file_path.relative_to(_workspace_root()))
    except ValueError:
        rel = str(file_path)
    hit = match_sensitive_path(rel)
    if hit:
        return (
            f"Security: 敏感路径写入已拦截：'{_display_path(file_path)}'"
            f"（匹配模式：{hit}）"
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(contents, encoding="utf-8")
    line_count = 0 if contents == "" else contents.count("\n") + (0 if contents.endswith("\n") else 1)
    return f"Wrote {_display_path(file_path)} ({line_count} lines)"


def _grep_with_rg(
    pattern: str,
    search_path: Path,
    glob: str | None,
    head_limit: int,
) -> str | None:
    search_arg = str(search_path)
    try:
        search_arg = str(search_path.relative_to(_workspace_root())) or "."
    except ValueError:
        pass
    cmd = ["rg", "--line-number", "--no-heading", "--color=never", pattern, search_arg]
    # rg 默认只跳隐藏目录 + 尊重 .gitignore；非 git 仓库或未 ignore 目录要显式排除。
    # 用 !**/{name}/** 排除任意层级的嵌套目录（仅 !{name}/** 只排根层级）
    for name in sorted(EXCLUDE_DIR_NAMES):
        cmd.insert(-1, "--glob")
        cmd.insert(-1, f"!**/{name}/**")
    if glob:
        cmd.insert(-1, "--glob")
        cmd.insert(-1, glob)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=_workspace_root(),
            timeout=30,
        )
    except FileNotFoundError:
        return None

    if proc.stdout is None:
        return f"Grep failed: no output captured (exit {proc.returncode})"
    output = proc.stdout.strip()
    if proc.returncode not in (0, 1):
        err = (proc.stderr or proc.stdout or "").strip()
        return f"Grep failed: {err or proc.returncode}"

    if not output:
        return f"No matches for pattern: {pattern!r}"

    lines = output.splitlines()
    if len(lines) > head_limit:
        lines = lines[:head_limit]
        lines.append(f"... truncated to {head_limit} matches")
    return "\n".join(lines)


def _grep_with_python(
    pattern: str,
    search_path: Path,
    glob: str | None,
    head_limit: int,
) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Invalid regex: {exc}"

    matches: list[str] = []
    files: list[Path]
    if search_path.is_file():
        files = [search_path]
    else:
        # os.walk 剪枝：跳过 .git / node_modules / sessions 等噪声目录
        files = []
        for root, dirs, names in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]
            for name in names:
                files.append(Path(root) / name)

    for file_path in sorted(files):
        rel = _display_path(file_path)
        if glob and not fnmatch.fnmatch(file_path.name, glob):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append(f"{rel}:{line_no}:{line}")
                if len(matches) >= head_limit:
                    matches.append(f"... truncated to {head_limit} matches")
                    return "\n".join(matches)

    if not matches:
        return f"No matches for pattern: {pattern!r}"
    return "\n".join(matches)


def grep_search(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    head_limit: int = 200,
) -> str:
    pattern = (pattern or "").strip()
    if not pattern:
        return "Error: empty pattern"

    search_path = resolve_path(path or ".")
    head_limit = max(1, min(int(head_limit or 200), 2000))

    if not search_path.exists():
        return f"Error: path not found: {_display_path(search_path)}"

    rg_result = _grep_with_rg(pattern, search_path, glob, head_limit)
    if rg_result is not None:
        return rg_result
    return _grep_with_python(pattern, search_path, glob, head_limit)


def _glob_safe(base: Path, pattern: str) -> list[Path]:
    """Path.glob 的 case_sensitive 参数是 Python 3.12+；旧版本回退。"""
    try:
        return list(base.glob(pattern, case_sensitive=False))
    except TypeError:
        return list(base.glob(pattern))


def glob_files(pattern: str, target_directory: str | None = None) -> str:
    pattern = (pattern or "").strip()
    if not pattern:
        return "Error: empty pattern"

    # pathlib 的 glob 中 "**" 结尾只匹配目录自身（如 sandbox/** 仅返回 sandbox 目录），
    # 补 "/*" 让「目录下所有文件」的直觉写法生效
    if pattern.endswith("/**"):
        pattern += "/*"
    elif pattern == "**":
        pattern = "**/*"

    base = resolve_path(target_directory or ".")
    if not base.is_dir():
        return f"Error: not a directory: {_display_path(base)}"

    if pattern.startswith("**/"):
        matches = sorted(_glob_safe(base, pattern))
    elif "**" in pattern:
        matches = sorted(_glob_safe(base, pattern))
    else:
        matches = sorted(base.rglob(pattern))

    # 统一排除 .git / node_modules / sessions 等噪声目录
    matches = [
        p
        for p in matches
        if not any(part in EXCLUDE_DIR_NAMES for part in p.parts)
    ]

    if not matches:
        return f"No files matched: {pattern!r}"

    rel_paths = [_display_path(p) for p in matches if p.is_file()]
    if not rel_paths:
        return f"No files matched: {pattern!r}"

    if len(rel_paths) > 500:
        shown = rel_paths[:500]
        shown.append(f"... and {len(rel_paths) - 500} more")
        return "\n".join(shown)
    return "\n".join(rel_paths)


def shell_command(command: str, timeout: float | None = None) -> str:
    command = (command or "").strip()
    if not command:
        return "Error: empty command"

    # 内置兜底（所有调用方，包括不经过主 Agent Hook 链的子 Agent/队友）
    hit = match_shell_command(command)
    if hit:
        pattern, description = hit
        return f"Security: 危险命令已拦截：{description}（匹配模式：{pattern}）"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            cwd=_workspace_root(),
            timeout=timeout if timeout is not None else SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        limit = timeout if timeout is not None else SHELL_TIMEOUT
        return f"Error: Shell command timed out after {int(limit)}s: {command[:120]}"
    except OSError as exc:
        return f"Error: Shell failed to start: {type(exc).__name__}: {exc}"

    output = result.stdout or result.stderr or ""
    if not output.strip():
        return "(no output)" if result.returncode == 0 else f"Exit {result.returncode} (no output)"
    if result.returncode != 0:
        return f"{output.rstrip()}\n[exit {result.returncode}]"
    return output.rstrip()


def background_shell(command: str) -> str:
    """后台执行 Shell（Step 16）：登记任务后立即返回占位结果。
    危险命令兜底与同步路径一致（子 Agent/队友不经 Hook 链也不漏）。"""
    command = (command or "").strip()
    if not command:
        return "Error: empty command"
    hit = match_shell_command(command)
    if hit:
        pattern, description = hit
        return f"Security: 危险命令已拦截：{description}（匹配模式：{pattern}）"

    from bg_jobs import BACKGROUND

    try:
        task_id = BACKGROUND.start(command, cwd=_workspace_root())
    except ValueError as exc:
        return f"Error: {exc}"
    return f"[Background] 任务 {task_id} 已启动（后台执行中）: {command[:80]}"


def run_file_tool(name: str, tool_input: dict[str, Any]) -> str | None:
    if name == "Read":
        return read_file(
            tool_input["path"],
            offset=tool_input.get("offset"),
            limit=tool_input.get("limit"),
        )
    if name == "Write":
        return write_file(tool_input["path"], tool_input.get("contents", ""))
    if name == "Grep":
        return grep_search(
            tool_input.get("pattern", ""),
            path=tool_input.get("path"),
            glob=tool_input.get("glob"),
            head_limit=tool_input.get("head_limit", 200),
        )
    if name == "Glob":
        return glob_files(
            tool_input.get("pattern", ""),
            target_directory=tool_input.get("target_directory"),
        )
    if name == "Shell":
        if tool_input.get("background"):
            return background_shell(tool_input.get("command", ""))
        return shell_command(tool_input.get("command", ""))
    return None
