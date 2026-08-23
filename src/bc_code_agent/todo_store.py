"""会话级 Todo 清单：外部进度状态，防止复杂任务迷路。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

STATUSES = ("pending", "in_progress", "completed", "cancelled")


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"


class TodoStore:
    def __init__(self, session_dir: Path) -> None:
        self.path = Path(session_dir) / "todos.json"
        self.items: list[TodoItem] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [
                TodoItem(
                    id=str(x.get("id") or ""),
                    content=str(x.get("content") or ""),
                    status=str(x.get("status") or "pending"),
                )
                for x in raw
                if x.get("id") and x.get("content")
            ]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # 与 memory/team_store 同策略：损坏文件改名保留，不阻塞启动
            try:
                self.path.rename(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            print(f"[Todo] 无法解析 {self.path}，已跳过（备份为 .json.bak）")
            self.items = []

    def _save(self) -> None:
        self.path.write_text(
            json.dumps([asdict(x) for x in self.items], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

    def read(self) -> str:
        if not self.items:
            return "Todo list is empty."
        lines = [f"Todo ({self._progress_text()}):"]
        for item in self.items:
            mark = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
                "cancelled": "[-]",
            }.get(item.status, "[?]")
            lines.append(f"{mark} {item.id}: {item.content} ({item.status})")
        return "\n".join(lines)

    def write(self, todos: list[dict]) -> str:
        """全量替换 todo 列表。"""
        if not isinstance(todos, list) or not todos:
            return "TodoWrite failed: `todos` must be a non-empty list."

        parsed: list[TodoItem] = []
        seen: set[str] = set()
        in_progress = 0

        for i, raw in enumerate(todos):
            if not isinstance(raw, dict):
                return f"TodoWrite failed: item {i} is not an object."
            item_id = str(raw.get("id") or "").strip() or f"t{i+1}"
            content = str(raw.get("content") or "").strip()
            status = str(raw.get("status") or "pending").strip()
            if not content:
                return f"TodoWrite failed: item {item_id!r} missing content."
            if status not in STATUSES:
                return (
                    f"TodoWrite failed: invalid status {status!r}. "
                    f"Use one of {', '.join(STATUSES)}."
                )
            if item_id in seen:
                return f"TodoWrite failed: duplicate id {item_id!r}."
            seen.add(item_id)
            if status == "in_progress":
                in_progress += 1
            parsed.append(TodoItem(id=item_id, content=content, status=status))

        if in_progress > 1:
            return "TodoWrite failed: at most one item may be in_progress."

        self.items = parsed
        self._save()
        print(f"[Todo] updated {self._progress_text()}")
        return self.read()

    def prompt_section(self) -> str:
        """注入 system，让模型每轮都能看到进度。"""
        return "# Current Todos\n" + self.read() + "\n"

    def _progress_text(self) -> str:
        done = sum(1 for x in self.items if x.status == "completed")
        total = len(self.items)
        return f"{done}/{total} completed"
