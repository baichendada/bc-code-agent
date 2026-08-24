"""Step 08 · Todo：全量更新会话级任务清单。

无 API 版本：
    py -3.13 guide/step-08-todo/agent.py
    py -3.13 guide/step-08-todo/agent.py --check
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATUSES = ("pending", "in_progress", "completed", "cancelled")


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"


class TodoStore:
    """会话级 TODO：每次写入都是整表替换。"""

    def __init__(self, session_dir: Path) -> None:
        self.path = Path(session_dir) / "todos.json"
        self.items: list[TodoItem] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.items = [
            TodoItem(
                id=str(item.get("id", "")),
                content=str(item.get("content", "")),
                status=str(item.get("status", "pending")),
            )
            for item in raw
            if item.get("id") and item.get("content")
        ]

    def _save(self) -> None:
        payload = [asdict(item) for item in self.items]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read(self) -> str:
        if not self.items:
            return "Todo list is empty."
        done = sum(item.status == "completed" for item in self.items)
        lines = [f"Todo ({done}/{len(self.items)} completed):"]
        marks = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
            "cancelled": "[-]",
        }
        for item in self.items:
            mark = marks.get(item.status, "[?]")
            lines.append(f"{mark} {item.id}: {item.content} ({item.status})")
        return "\n".join(lines)

    def write(self, todos: list[dict[str, Any]]) -> str:
        """校验通过后才替换 self.items；失败时保持旧清单。"""
        if not isinstance(todos, list) or not todos:
            return "TodoWrite failed: todos must be a non-empty list."

        parsed: list[TodoItem] = []
        seen: set[str] = set()
        in_progress = 0

        for index, raw in enumerate(todos):
            if not isinstance(raw, dict):
                return f"TodoWrite failed: item {index} is not an object."
            item_id = str(raw.get("id") or "").strip() or f"t{index + 1}"
            content = str(raw.get("content") or "").strip()
            status = str(raw.get("status") or "pending").strip()
            if not content:
                return f"TodoWrite failed: {item_id} missing content."
            if status not in STATUSES:
                return f"TodoWrite failed: invalid status {status!r}."
            if item_id in seen:
                return f"TodoWrite failed: duplicate id {item_id!r}."
            if status == "in_progress":
                in_progress += 1
            seen.add(item_id)
            parsed.append(TodoItem(id=item_id, content=content, status=status))

        if in_progress > 1:
            return "TodoWrite failed: at most one item may be in_progress."

        self.items = parsed
        self._save()
        return self.read()


def run_demo(root: Path) -> TodoStore:
    session_dir = Path(root) / "session-demo"
    session_dir.mkdir(parents=True)
    store = TodoStore(session_dir)

    def plan(analyze: str, write: str, test: str) -> list[dict[str, Any]]:
        return [
            {"id": "analyze", "content": "分析现有权限入口", "status": analyze},
            {"id": "write", "content": "写权限判断函数", "status": write},
            {"id": "test", "content": "补测试", "status": test},
        ]

    updates = [
        plan("pending", "pending", "pending"),
        plan("in_progress", "pending", "pending"),
        plan("in_progress", "in_progress", "pending"),
        plan("completed", "in_progress", "pending"),
    ]

    print("initial:")
    print(store.write(updates[0]) + "\n")
    print("start analyze:")
    print(store.write(updates[1]) + "\n")
    print("invalid update:")
    print(store.write(updates[2]) + "\n")
    print("valid update:")
    print(store.write(updates[3]))
    return store


def check() -> int:
    with tempfile.TemporaryDirectory(prefix="bc-guide-step08-") as temp:
        root = Path(temp)
        store = run_demo(root)

        assert [item.id for item in store.items] == ["analyze", "write", "test"]
        assert [item.status for item in store.items] == [
            "completed", "in_progress", "pending"
        ]
        saved = json.loads(store.path.read_text(encoding="utf-8"))
        assert saved == [asdict(item) for item in store.items]

        restored = TodoStore(root / "session-demo")
        assert restored.items == store.items
        assert restored.read() == store.read()

        invalid = store.write([
            {"id": "analyze", "content": "重复 ID", "status": "pending"},
            {"id": "analyze", "content": "重复 ID", "status": "pending"},
        ])
        assert invalid.startswith("TodoWrite failed")
        assert store.items == restored.items

    print("\ncheck: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run deterministic assertions")
    args = parser.parse_args()

    if args.check:
        return check()
    with tempfile.TemporaryDirectory(prefix="bc-guide-step08-") as temp:
        run_demo(Path(temp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
