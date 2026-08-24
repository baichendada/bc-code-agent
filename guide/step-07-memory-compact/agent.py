"""Step 07 · Memory Compact：无 API，支持 --check。"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

COMPACT_THRESHOLD = 8
KEEP_RECENT = 4


@dataclass
class SegmentSummary:
    segment: int
    goals: list[str]
    decisions: list[str]
    facts: list[str]
    open_todos: list[str]

    def to_prompt(self) -> str:
        lines = [f"[Memory Summary #{self.segment}]"]
        lines.extend(f"- goal: {value}" for value in self.goals)
        lines.extend(f"- decision: {value}" for value in self.decisions)
        lines.extend(f"- fact: {value}" for value in self.facts)
        lines.extend(f"- open todo: {value}" for value in self.open_todos)
        return "\n".join(lines)


def message_text(message: dict[str, Any]) -> str:
    """教学消息只包含字符串 content 或 text block。"""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", "")) for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def deterministic_summary(messages: list[dict[str, Any]], segment: int) -> SegmentSummary:
    """从标记行提取状态，不调用模型，结果完全可测试。"""
    buckets = {
        "goal": [],
        "decision": [],
        "fact": [],
        "todo": [],
    }
    for message in messages:
        for line in message_text(message).splitlines():
            match = re.match(
                r"^(?:- )?(goal|decision|fact|(?:open )?todo):\s*(.+)$",
                line.strip(), re.IGNORECASE
            )
            if match:
                kind = "todo" if match.group(1).lower() == "open todo" else match.group(1).lower()
                buckets[kind].append(match.group(2).strip())

    return SegmentSummary(
        segment=segment,
        goals=_dedupe(buckets["goal"]),
        decisions=_dedupe(buckets["decision"]),
        facts=_dedupe(buckets["fact"]),
        open_todos=_dedupe(buckets["todo"]),
    )


class MemoryCompactor:
    """原始层、摘要层、working history 的最小实现。"""

    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.dir / "transcript.jsonl"
        self.summary_path = self.dir / "summaries.jsonl"
        self.summaries: list[SegmentSummary] = []

    def append_raw(self, message: dict[str, Any]) -> None:
        """原始层只追加，不改写历史。"""
        with self.transcript_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(message, ensure_ascii=False) + "\n")

    def maybe_compact(
        self, history: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], bool]:
        if len(history) < COMPACT_THRESHOLD:
            return history, False

        old_messages = history[:-KEEP_RECENT]
        recent = history[-KEEP_RECENT:]
        summary = deterministic_summary(old_messages, len(self.summaries) + 1)
        self.summaries.append(summary)
        with self.summary_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(summary), ensure_ascii=False) + "\n")

        working = [{"role": "user", "content": summary.to_prompt()}, *recent]
        return working, True


DEMO_MESSAGES = [
    {"role": "user", "content": "goal: 修复登录页在移动端的布局"},
    {"role": "assistant", "content": "decision: 先复现 375px 宽度下的问题"},
    {"role": "user", "content": "fact: 失败原因是按钮容器没有设置最小宽度"},
    {"role": "assistant", "content": "todo: 补充移动端回归测试"},
    {"role": "user", "content": "fact: 相关测试文件是 tests/test_login.py"},
    {"role": "assistant", "content": "fact: 相关测试文件是 tests/test_login.py"},
    {"role": "user", "content": "goal: 修复登录页在移动端的布局"},
    {"role": "assistant", "content": "decision: 修改按钮容器后运行回归测试"},
]


def run_demo(root: Path) -> tuple[MemoryCompactor, list[dict[str, Any]]]:
    compactor = MemoryCompactor(root)
    history: list[dict[str, Any]] = []

    for message in DEMO_MESSAGES:
        compactor.append_raw(message)
        history.append(message)
        history, compacted = compactor.maybe_compact(history)

    print(f"raw transcript: {len(DEMO_MESSAGES)} messages")
    print(f"summaries: {len(compactor.summaries)}")
    print(f"working history: {len(history)} messages (compacted={compacted})")
    print("\n" + message_text(history[0]))
    return compactor, history


def check() -> int:
    with tempfile.TemporaryDirectory(prefix="bc-guide-step07-") as temp:
        root = Path(temp)
        compactor, history = run_demo(root)

        raw_count = sum(
            1 for line in compactor.transcript_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        summary_saved = json.loads(
            compactor.summary_path.read_text(encoding="utf-8").strip()
        )
        summary = compactor.summaries[0]

        assert raw_count == len(DEMO_MESSAGES)
        assert summary_saved == asdict(summary)
        assert len(history) == KEEP_RECENT + 1 and history[0]["role"] == "user"
        assert history[1:] == DEMO_MESSAGES[-KEEP_RECENT:]
        assert (summary.goals, summary.facts, summary.open_todos) == (
            ["修复登录页在移动端的布局"],
            ["失败原因是按钮容器没有设置最小宽度"],
            ["补充移动端回归测试"],
        )
        assert summary.decisions == ["先复现 375px 宽度下的问题"]

    print("\ncheck: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run deterministic assertions")
    args = parser.parse_args()

    if args.check:
        return check()
    with tempfile.TemporaryDirectory(prefix="bc-guide-step07-") as temp:
        run_demo(Path(temp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
