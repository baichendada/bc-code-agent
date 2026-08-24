"""Step 15 · Background：慢命令不堵主循环，完成通知下一轮注入。

教学版用线程执行一个安全的 Python 函数，不跑系统命令。

演示：
    py -3.13 guide/step-15-background/agent.py
自检：
    py -3.13 guide/step-15-background/agent.py --check
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class Task:
    task_id: str
    label: str
    status: str
    result: str = ""
    event: threading.Event = threading.Event()


class BackgroundManager:
    """登记任务 -> daemon 线程执行 -> 完成通知队列。"""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self._ready: list[str] = []
        self._counter = 0
        self._lock = threading.Lock()

    def start(self, label: str, job: Callable[[], str]) -> str:
        """立即返回任务 id；job 在后台线程中执行。"""
        with self._lock:
            self._counter += 1
            task_id = f"bg_{self._counter:04d}"
            task = Task(task_id=task_id, label=label, status="running")
            self.tasks[task_id] = task

        def run() -> None:
            try:
                result = job()
                task.result = result
                task.status = "completed"
            except Exception as exc:  # 教学版也把异常转成可读通知
                task.result = f"{type(exc).__name__}: {exc}"
                task.status = "failed"
            finally:
                with self._lock:
                    self._ready.append(task.task_id)
                task.event.set()

        threading.Thread(target=run, name=f"bg-{task_id}", daemon=True).start()
        return task_id

    def placeholder(self, task_id: str) -> str:
        """Shell(background=true) 立刻返回给模型的 tool_result。"""
        task = self.tasks[task_id]
        return f"[Background] {task_id} started: {task.label}"

    def collect(self) -> list[str]:
        """取走已完成通知；消费一次后清空，避免下一轮重复注入。"""
        with self._lock:
            ready = self._ready.copy()
            self._ready.clear()

        messages = []
        for task_id in ready:
            task = self.tasks[task_id]
            messages.append(
                f"[Background] {task_id} {task.status}: {task.label}\n{task.result}"
            )
        return messages

    def wait(self, task_id: str) -> None:
        self.tasks[task_id].event.wait()


def inject_background(messages: list[dict], notifications: list[str]) -> None:
    """下一轮调用 LLM 前，把通知并入对话（优先附加到最后一条 user）。"""
    if not notifications:
        return
    text = "\n".join(notifications)
    last = messages[-1] if messages else None
    if last and last.get("role") == "user" and isinstance(last.get("content"), str):
        last["content"] += f"\n\n{text}"
        return
    messages.append({"role": "user", "content": text})


def demo() -> None:
    manager = BackgroundManager()

    def slow_job() -> str:
        time.sleep(0.15)
        return "pytest passed: 3 tests"

    task_id = manager.start("pytest -q", slow_job)
    print(manager.placeholder(task_id))
    print("main loop is free to continue...")

    manager.wait(task_id)
    notifications = manager.collect()
    print("\n".join(notifications))

    messages = [{"role": "user", "content": "查看后台结果"}]
    inject_background(messages, notifications)
    print("\nnext LLM call sees:")
    print(messages[-1]["content"])


def check() -> None:
    manager = BackgroundManager()
    release = threading.Event()
    task_id = manager.start("demo", lambda: "ok" if release.wait() else "failed")

    assert manager.tasks[task_id].status == "running"
    assert manager.collect() == []
    release.set()
    manager.wait(task_id)
    assert manager.tasks[task_id].status == "completed"

    notifications = manager.collect()
    assert len(notifications) == 1
    assert manager.collect() == []

    history = [{"role": "assistant", "content": "hi"}]
    inject_background(history, notifications)
    assert history[-1]["role"] == "user"
    assert "bg_0001 completed" in history[-1]["content"]

    tool_history = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "old", "content": "old"}
            ],
        }
    ]
    inject_background(tool_history, notifications)
    assert len(tool_history) == 2
    assert tool_history[0]["content"][0]["content"] == "old"
    assert tool_history[-1]["role"] == "user"
    assert "bg_0001 completed" in tool_history[-1]["content"]
    print("background checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else demo()
