#!/usr/bin/env python3
"""Stop：过短回复或未完成 Todo 时 block（主循环最多追一轮）。"""

from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("invalid stdin json", file=sys.stderr)
        return 1

    if int(data.get("retry") or 0) >= 1:
        return 0

    reply = data.get("reply") or ""
    if reply and len(str(reply).strip()) < 10:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": "回答似乎不完整（少于10个字符），请检查并重新生成更完整的回复。",
                },
                ensure_ascii=False,
            )
        )
        return 0

    todos = data.get("todos") or []
    unfinished = [
        t
        for t in todos
        if (t.get("status") if isinstance(t, dict) else "")
        not in ("completed", "cancelled")
    ]
    if unfinished:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": "仍有未完成待办，Stop Hook 要求继续执行。",
                },
                ensure_ascii=False,
            )
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
