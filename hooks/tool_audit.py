#!/usr/bin/env python3
"""PostToolUse：把 Write/Shell 调用追加到 session 的 tool_audit.jsonl。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("invalid stdin json", file=sys.stderr)
        return 1

    session_dir = data.get("session_dir") or ""
    if not session_dir:
        print("[hook:tool_audit] missing session_dir", file=sys.stderr)
        return 1

    tool_name = data.get("tool_name") or ""
    entry = {
        "ts": datetime.now().isoformat(),
        "tool": tool_name,
        "input": data.get("tool_input"),
        "duration_ms": data.get("duration_ms"),
    }
    path = Path(session_dir) / "tool_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[hook:tool_audit] {tool_name} 已审计到 {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
