"""语义化文件/搜索工具 + 受限 Shell。"""

from __future__ import annotations

import re
import subprocess
import fnmatch
from pathlib import Path
from typing import Any

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
            "(e.g. date, git, python scripts). Do not use for reading/writing files or searching code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
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
    return header + "\n" + "\n".join(numbered)


def write_file(path: str, contents: str) -> str:
    file_path = resolve_path(path)
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
    if glob:
        cmd.insert(-1, "--glob")
        cmd.insert(-1, glob)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=_workspace_root(),
            timeout=30,
        )
    except FileNotFoundError:
        return None

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
        files = [p for p in search_path.rglob("*") if p.is_file()]

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

    if search_path.is_dir() and not search_path.exists():
        return f"Error: directory not found: {_display_path(search_path)}"
    if search_path.is_file() and not search_path.exists():
        return f"Error: file not found: {_display_path(search_path)}"

    rg_result = _grep_with_rg(pattern, search_path, glob, head_limit)
    if rg_result is not None:
        return rg_result
    return _grep_with_python(pattern, search_path, glob, head_limit)


def glob_files(pattern: str, target_directory: str | None = None) -> str:
    pattern = (pattern or "").strip()
    if not pattern:
        return "Error: empty pattern"

    base = resolve_path(target_directory or ".")
    if not base.is_dir():
        return f"Error: not a directory: {_display_path(base)}"

    if pattern.startswith("**/"):
        matches = sorted(base.glob(pattern, case_sensitive=False))
    elif "**" in pattern:
        matches = sorted(base.glob(pattern, case_sensitive=False))
    else:
        matches = sorted(base.rglob(pattern))

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


def shell_command(command: str) -> str:
    command = (command or "").strip()
    if not command:
        return "Error: empty command"

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=_workspace_root(),
    )
    output = result.stdout or result.stderr or ""
    if not output.strip():
        return "(no output)" if result.returncode == 0 else f"Exit {result.returncode} (no output)"
    if result.returncode != 0:
        return f"{output.rstrip()}\n[exit {result.returncode}]"
    return output.rstrip()


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
        return shell_command(tool_input.get("command", ""))
    return None
