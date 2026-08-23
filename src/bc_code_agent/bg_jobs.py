"""后台任务（Step 16）：慢命令后台跑，完成结果在后续轮次注入。

设计要点：
- Shell(background=true) 显式开启；登记 bg_0001 递增 id，daemon 线程执行，立即返回占位结果
- 完成进入 _ready 队列（消费即清空）；下一轮 LLM 调用前 collect() 注入通知
- 进程组生命周期：POSIX = start_new_session + killpg；Windows = CREATE_NEW_PROCESS_GROUP + taskkill /T
- 任务生命周期 = 进程生命周期（不落盘；--session 恢复后需重新发起）
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

BG_TIMEOUT = 120          # 后台任务超时（秒）
BG_NOTIFY_CHARS = 2000    # 通知里输出的摘要素数
MAX_TASK_HISTORY = 200    # tasks dict 上限（防无限累积）


def _spawn(command: str, cwd: Path) -> subprocess.Popen:
    """独立进程组启动 shell（POSIX 新会话 / Windows 新进程组）。"""
    kwargs: dict[str, Any] = dict(
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _kill_process(proc: subprocess.Popen) -> None:
    """终止整个进程组（命令及其子进程树）。"""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, OSError):
                return
            time.sleep(0.05)


class BackgroundManager:
    """任务登记 + daemon 执行 + 完成队列 + 进程组清理。"""

    def __init__(self, timeout: float = BG_TIMEOUT) -> None:
        self.timeout = timeout
        self.tasks: dict[str, dict[str, Any]] = {}
        self.results: dict[str, str] = {}
        self._ready: list[str] = []
        self._processes: dict[str, subprocess.Popen] = {}
        self._counter = 0
        self._lock = threading.Lock()

    # ---------- 启动 ----------

    def start(self, command: str, cwd: Path | None = None) -> str:
        command = (command or "").strip()
        if not command:
            raise ValueError("background command cannot be empty")

        with self._lock:
            self._counter += 1
            task_id = f"bg_{self._counter:04d}"
            self.tasks[task_id] = {
                "command": command,
                "status": "running",
                "started_at": time.time(),
                "exit_code": None,
            }
        try:
            proc = _spawn(command, cwd or Path.cwd())
        except OSError as exc:
            with self._lock:
                self.tasks[task_id]["status"] = "failed"
                self.results[task_id] = f"Error: failed to start: {exc}"
                self._ready.append(task_id)
            return task_id
        with self._lock:
            self._processes[task_id] = proc

        thread = threading.Thread(
            target=self._run,
            args=(task_id, proc),
            daemon=True,
            name=f"bg-{task_id}",
        )
        thread.start()
        return task_id

    def _run(self, task_id: str, proc: subprocess.Popen) -> None:
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
            output = (stdout or "") + (stderr or "")
            output = output.strip() or "(no output)"
            exit_code = proc.returncode
            status = "completed" if exit_code == 0 else "failed"
            result = (
                output
                if status == "completed"
                else f"Error: command exited with status {exit_code}\n{output}"
            )
        except subprocess.TimeoutExpired:
            result = f"Error: timeout after {int(self.timeout)}s"
            status = "failed"
            exit_code = None
        except OSError as exc:
            result = f"Error: {type(exc).__name__}: {exc}"
            status = "failed"
            exit_code = None
        finally:
            _kill_process(proc)

        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return
            task["status"] = status
            task["exit_code"] = exit_code
            task["finished_at"] = time.time()
            self.results[task_id] = result
            self._ready.append(task_id)
            self._processes.pop(task_id, None)
            self._trim_history()

    def _trim_history(self) -> None:
        if len(self.tasks) <= MAX_TASK_HISTORY:
            return
        finished = [
            tid
            for tid, t in self.tasks.items()
            if t["status"] != "running" and tid not in self._ready
        ]
        for tid in finished[: len(self.tasks) - MAX_TASK_HISTORY]:
            self.tasks.pop(tid, None)
            self.results.pop(tid, None)

    # ---------- 收集 ----------

    def collect(self) -> list[str]:
        """消费完成队列，返回通知文本（取走即清空）。"""
        with self._lock:
            ready = list(self._ready)
            self._ready.clear()
        notifications = []
        for task_id in ready:
            task = self.tasks.get(task_id)
            result = self.results.get(task_id, "")
            if task is None:
                continue
            notifications.append(self._format_notification(task_id, task, result))
        return notifications

    @staticmethod
    def _format_notification(task_id: str, task: dict[str, Any], result: str) -> str:
        status = task["status"]
        label = "完成" if status == "completed" else "失败"
        exit_text = (
            f" (exit {task['exit_code']})"
            if task.get("exit_code") is not None
            else ""
        )
        summary = result[:BG_NOTIFY_CHARS]
        if len(result) > BG_NOTIFY_CHARS:
            summary += f"\n  ...（输出已截断，共 {len(result)} 字符）"
        return (
            f"[Background] {task_id} {label}{exit_text}\n"
            f"  命令: {task['command'][:120]}\n"
            f"  输出: {summary}"
        )

    # ---------- 查询 / 控制 ----------

    def running(self) -> int:
        with self._lock:
            return sum(1 for t in self.tasks.values() if t["status"] == "running")

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self.tasks.values()]

    def kill(self, task_id: str) -> str:
        with self._lock:
            task = self.tasks.get(task_id)
            proc = self._processes.get(task_id)
        if task is None:
            return f"任务不存在: {task_id}"
        if task["status"] != "running" or proc is None:
            return f"任务 {task_id} 已结束（{task['status']}），无需停止"
        _kill_process(proc)
        return f"已发送停止信号: {task_id}"

    def clear_finished(self) -> int:
        """清掉已完成任务的记录（/bg clear）。返回清除数量。"""
        with self._lock:
            finished = [
                tid
                for tid, t in self.tasks.items()
                if t["status"] != "running" and tid not in self._ready
            ]
            for tid in finished:
                self.tasks.pop(tid, None)
                self.results.pop(tid, None)
        return len(finished)

    def shutdown(self) -> None:
        """进程退出时清理全部活跃进程组（atexit）。"""
        with self._lock:
            procs = list(self._processes.values())
        for proc in procs:
            _kill_process(proc)


BACKGROUND = BackgroundManager()


def format_task_list() -> str:
    """/bg 列表渲染。"""
    tasks = BACKGROUND.list_tasks()
    if not tasks:
        return "没有后台任务"
    lines = ["id        状态        用时    命令"]
    for tid, task in BACKGROUND.tasks.items():
        el = task.get("finished_at", time.time()) - task["started_at"]
        lines.append(
            f"{tid:<10} {task['status']:<10} {int(el):>4}s  {task['command'][:60]}"
        )
    return "\n".join(lines)


def install_cleanup() -> None:
    atexit.register(BACKGROUND.shutdown)


import os as _os  # noqa: E402  (Windows 上 SIGTERM 处理有限，尽力而为)

def _sigterm_handler(signum, _frame):  # pragma: no cover - 进程级清理
    BACKGROUND.shutdown()
    raise SystemExit(128 + signum)

if not _os.name == "nt":
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, OSError):
        pass
