#!/usr/bin/env python3
"""PostToolUse：截断过长 tool_response，经 updatedToolOutput 写回。"""

from __future__ import annotations

import json
import sys

MAX_CHARS = 4000


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("invalid stdin json", file=sys.stderr)
        return 1

    output = data.get("tool_response")
    if not isinstance(output, str) or len(output) <= MAX_CHARS:
        return 0

    truncated = (
        output[:MAX_CHARS]
        + f"\n\n[... 输出已截断，原始共 {len(output)} 字符，"
        + f"显示前 {MAX_CHARS} 字符]"
    )
    print(
        f"[hook:output_format] 输出截断：原始 {len(output)} -> {MAX_CHARS} 字符",
        file=sys.stderr,
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": truncated,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
