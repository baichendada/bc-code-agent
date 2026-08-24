"""Step 09 · Session：transcript 追加、session id 与 working history 恢复。

无 API 版本：
    py -3.13 guide/step-09-session/agent.py
    py -3.13 guide/step-09-session/agent.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


PERSISTENT_ROOT = Path(__file__).resolve().parent.parent


def new_session_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


class SessionStore:
    """一个 session 目录：transcript.jsonl 是事件档案，working.json 是请求快照。"""

    def __init__(self, root: Path, session_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", session_id):
            raise ValueError(f"invalid session id: {session_id!r}")
        self.session_id = session_id
        self.dir = Path(root) / "sessions" / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.dir / "transcript.jsonl"
        self.working_path = self.dir / "working.json"

    def append_raw(self, message: dict[str, Any]) -> int:
        sequence = self._next_sequence()
        record = {
            "seq": sequence,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "message": message,
        }
        with self.transcript_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return sequence

    def _next_sequence(self) -> int:
        if not self.transcript_path.is_file():
            return 1
        count = 0
        for line in self.transcript_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count + 1

    def save_working(self, messages: list[dict[str, Any]]) -> None:
        payload = {
            "session_id": self.session_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "messages": messages,
        }
        self.working_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_working_history(self) -> list[dict[str, Any]]:
        """优先恢复 working.json；没有快照时从 transcript 重建。"""
        if self.working_path.is_file():
            raw = json.loads(self.working_path.read_text(encoding="utf-8"))
            messages = raw.get("messages", [])
            if isinstance(messages, list):
                return [self._valid_message(message) for message in messages]

        history: list[dict[str, Any]] = []
        if self.transcript_path.is_file():
            for line in self.transcript_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                message = record.get("message")
                if isinstance(message, dict):
                    history.append(self._valid_message(message))
        return history

    @staticmethod
    def _valid_message(message: Any) -> dict[str, Any]:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            raise ValueError(f"invalid message in session: {message!r}")
        content = message.get("content")
        if not isinstance(content, (str, list)) or (
            isinstance(content, str) and not content
        ):
            raise ValueError("session message content must be a non-empty string or list")
        return {"role": message["role"], "content": content}


def run_demo(root: Path, session_id: str) -> tuple[SessionStore, list[dict[str, Any]]]:
    store = SessionStore(root, session_id)
    if store.working_path.is_file() or store.transcript_path.is_file():
        restored = store.load_working_history()
        records = len(
            store.transcript_path.read_text(encoding="utf-8").splitlines()
        )
        print(f"session id: {session_id}")
        print("restarted: existing session restored")
        print(f"transcript records: {records}")
        print(f"restored working history: {len(restored)} messages")
        return store, restored

    messages = [
        {"role": "user", "content": "帮我把这个函数拆成读取和渲染两步"},
        {"role": "assistant", "content": "好的，我先读取当前实现，再给出拆分方案。"},
        {"role": "user", "content": "注意保持公开接口不变"},
        {"role": "assistant", "content": "已记录约束：公开接口保持不变。"},
    ]

    for message in messages:
        store.append_raw(message)
    store.save_working(messages)

    restored = store.load_working_history()
    print(f"session id: {session_id}")
    print("created: new session saved")
    print(f"transcript records: {len(messages)}")
    print(f"restored working history: {len(restored)} messages")
    print(f"restore ok: {restored == messages}")
    return store, restored


def check() -> int:
    with tempfile.TemporaryDirectory(prefix="bc-guide-step09-") as temp:
        root = Path(temp)
        store, restored = run_demo(root, "check-session")
        assert restored[0]["content"] == "帮我把这个函数拆成读取和渲染两步"
        assert restored[-1]["role"] == "assistant"

        records = [
            json.loads(line)
            for line in store.transcript_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["seq"] for record in records] == [1, 2, 3, 4]
        assert all(record["message"]["role"] in {"user", "assistant"} for record in records)

        store.working_path.unlink()
        rebuilt = SessionStore(root, "check-session").load_working_history()
        assert rebuilt == restored

        try:
            SessionStore(root, "../escape")
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal session id was accepted")

    print("\ncheck: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run deterministic assertions")
    parser.add_argument("--session", help="reuse this session id; omit it to create a new one")
    args = parser.parse_args()

    if args.check:
        with tempfile.TemporaryDirectory(prefix="bc-guide-step09-") as temp:
            return check()
        return 0

    if args.session:
        run_demo(PERSISTENT_ROOT, args.session)
    else:
        with tempfile.TemporaryDirectory(prefix="bc-guide-step09-") as temp:
            run_demo(Path(temp), new_session_id())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
