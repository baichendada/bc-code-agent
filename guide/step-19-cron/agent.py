"""Step 19 · Cron：把到点触发变成完整 Agent turn。

教学版完全离线，不调用 API，也不启动系统 cron。

运行：
    py -3.13 guide/step-19-cron/agent.py
自检：
    py -3.13 guide/step-19-cron/agent.py --check
"""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import threading
from typing import Iterator


def parse_field(raw: str, low: int, high: int, field: str) -> frozenset[int] | None:
    """解析一个 cron 字段；None 表示通配符。"""
    if not raw:
        raise ValueError(f"{field} field cannot be empty")
    if raw == "*":
        return None

    values: set[int] = set()
    for part in raw.split(","):
        base, slash, step_text = part.partition("/")
        if not base or (slash and not step_text):
            raise ValueError(f"invalid {field} term: {part!r}")

        if base == "*":
            start, end = low, high
        elif "-" in base:
            left, _, right = base.partition("-")
            if not left.isdigit() or not right.isdigit():
                raise ValueError(f"invalid {field} range: {part!r}")
            start, end = int(left), int(right)
        else:
            if not base.isdigit():
                raise ValueError(f"invalid {field} value: {part!r}")
            start = end = int(base)

        if start < low or end > high or start > end:
            raise ValueError(
                f"{field} value out of range [{low}, {high}]: {part!r}"
            )

        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"invalid {field} step: {part!r}")
            step = int(step_text)

        values.update(range(start, end + 1, step))

    if not values:
        raise ValueError(f"{field} field has no values")
    return frozenset(values)


@dataclass(frozen=True)
class CronSpec:
    """标准五字段 cron：minute hour day-of-month month day-of-week。"""

    expression: str
    minutes: frozenset[int] | None
    hours: frozenset[int] | None
    days_of_month: frozenset[int] | None
    months: frozenset[int] | None
    days_of_week: frozenset[int] | None  # 0=Sunday, 6=Saturday

    @classmethod
    def parse(cls, expression: str) -> "CronSpec":
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError("cron expression must have exactly 5 fields")

        minutes = parse_field(fields[0], 0, 59, "minute")
        hours = parse_field(fields[1], 0, 23, "hour")
        days_of_month = parse_field(fields[2], 1, 31, "day-of-month")
        months = parse_field(fields[3], 1, 12, "month")
        days_of_week = parse_field(fields[4], 0, 6, "day-of-week")
        return cls(
            expression,
            minutes,
            hours,
            days_of_month,
            months,
            days_of_week,
        )

    def matches(self, at: datetime) -> bool:
        if self.minutes is not None and at.minute not in self.minutes:
            return False
        if self.hours is not None and at.hour not in self.hours:
            return False
        if self.months is not None and at.month not in self.months:
            return False

        # Python weekday() 周一为 0；cron 约定周日为 0。
        cron_weekday = (at.weekday() + 1) % 7
        dom_hit = self.days_of_month is None or at.day in self.days_of_month
        dow_hit = self.days_of_week is None or cron_weekday in self.days_of_week

        if self.days_of_month is None and self.days_of_week is None:
            return True
        if self.days_of_month is None:
            return dow_hit
        if self.days_of_week is None:
            return dom_hit
        # cron 的历史约定：日和星期都被限制时，二者命中任意一个即可。
        return dom_hit or dow_hit


@dataclass(frozen=True)
class CronJob:
    job_id: str
    spec: CronSpec
    prompt: str


@dataclass(frozen=True)
class PendingTurn:
    run_at: datetime
    job_id: str
    prompt: str


class PendingQueue:
    def __init__(self) -> None:
        self._items: deque[PendingTurn] = deque()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def add(self, item: PendingTurn) -> None:
        with self._lock:
            self._items.append(item)

    def ready(self, now: datetime) -> list[PendingTurn]:
        with self._lock:
            return [item for item in self._items if item.run_at <= now]

    def remove(self, item: PendingTurn) -> None:
        with self._lock:
            self._items.remove(item)


class CronScheduler:
    """每分钟扫描一次；同一 job 同一分钟只入队一次。"""

    def __init__(self) -> None:
        self.jobs: list[CronJob] = []
        self.fired_keys: set[tuple[str, datetime]] = set()

    def add(self, job_id: str, expression: str, prompt: str) -> None:
        if any(job.job_id == job_id for job in self.jobs):
            raise ValueError(f"duplicate cron job id: {job_id}")
        self.jobs.append(CronJob(job_id, CronSpec.parse(expression), prompt))

    def tick(self, now: datetime, queue: PendingQueue) -> int:
        minute = now.replace(second=0, microsecond=0)
        added = 0
        for job in self.jobs:
            key = (job.job_id, minute)
            if job.spec.matches(minute) and key not in self.fired_keys:
                queue.add(PendingTurn(minute, job.job_id, job.prompt))
                self.fired_keys.add(key)
                added += 1
        return added


class TurnGate:
    """同一时刻只允许一个用户轮或定时轮进入主循环。"""

    def __init__(self, timeout: float = 0.02) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()

    @contextmanager
    def try_turn(self, timeout: float | None = None) -> Iterator[bool]:
        wait = self.timeout if timeout is None else timeout
        acquired = self._lock.acquire(timeout=wait)
        try:
            yield acquired
        finally:
            if acquired:
                self._lock.release()


class ScheduledAgent:
    """离线演示 Agent：真实实现会在这里进入完整 tools loop。"""

    def __init__(self, messages: list[dict[str, str]] | None = None) -> None:
        self.messages: list[dict[str, str]] = messages or []
        self.gate = TurnGate()

    def run_user_turn(self, prompt: str) -> list[dict[str, str]]:
        with self.gate.try_turn(timeout=5) as acquired:
            if not acquired:
                raise TimeoutError("could not acquire turn gate")
            return self._append_turn("user", prompt)

    def try_run_pending(self, item: PendingTurn, now: datetime) -> list[dict[str, str]] | None:
        with self.gate.try_turn() as acquired:
            if not acquired or item.run_at > now:
                return None
            return self._append_turn("cron", f"[cron:{item.job_id}] {item.prompt}")

    def drain(self, queue: PendingQueue, now: datetime) -> list[list[dict[str, str]]]:
        turns: list[list[dict[str, str]]] = []
        for item in queue.ready(now):
            turn = self.try_run_pending(item, now)
            if turn is not None:
                queue.remove(item)
                turns.append(turn)
        return turns

    def _append_turn(self, source: str, prompt: str) -> list[dict[str, str]]:
        turn = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": f"完成 {source} 轮：{prompt}"},
        ]
        self.messages.extend(turn)
        return turn


def demo() -> None:
    spec = CronSpec.parse("*/15 9-17 * * 1-5")
    print(f"expression: {spec.expression}")

    scheduler = CronScheduler()
    scheduler.add("weekly-report", "*/15 9-17 * * 1-5", "检查本周进度并写一段摘要")

    queue = PendingQueue()
    before = datetime(2026, 8, 25, 9, 14)
    due_at = datetime(2026, 8, 25, 9, 15, 30)
    print(f"{before:%H:%M} match: {spec.matches(before)}")
    scheduler.tick(before, queue)
    print(f"pending after 09:14 tick: {len(queue)}")

    print(f"{due_at:%H:%M} match: {spec.matches(due_at)}")
    scheduler.tick(due_at, queue)
    print(f"pending after 09:15 tick: {len(queue)}")

    agent = ScheduledAgent()
    agent.run_user_turn("现在可以继续刚才的实现")
    scheduled = agent.drain(queue, due_at)
    print(f"scheduled turns executed: {len(scheduled)}")

    scheduler.tick(due_at, queue)
    print(f"pending after duplicate tick: {len(queue)}")

    scheduler.add("one-more", "* * * * *", "再演示一次锁竞争")
    scheduler.tick(due_at, queue)
    item = queue.ready(due_at)[0]
    with agent.gate.try_turn() as user_holds_gate:
        assert user_holds_gate
        blocked = agent.try_run_pending(item, due_at)
        print(f"scheduled waits while user turn owns gate: {blocked is None}")
    executed = agent.try_run_pending(item, due_at)
    if executed is not None:
        queue.remove(item)
    print(f"queued turn executed after gate release: {executed is not None}")
    print(f"transcript messages: {len(agent.messages)}")


def check() -> None:
    spec = CronSpec.parse("*/15 9-17 * * 1-5")
    assert spec.matches(datetime(2026, 8, 25, 9, 15))
    assert not spec.matches(datetime(2026, 8, 25, 9, 14))
    assert not spec.matches(datetime(2026, 8, 22, 9, 15))  # Saturday

    for expression in (
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 7",
        "1- * * * *",
        "*/0 * * * *",
        "* * * *",
    ):
        try:
            CronSpec.parse(expression)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid expression accepted: {expression}")

    dom_or_dow = CronSpec.parse("0 0 1 * 1")
    assert dom_or_dow.matches(datetime(2026, 9, 1))  # 1 号，虽非周一
    assert dom_or_dow.matches(datetime(2026, 8, 31))  # 周一，虽非 1 号

    scheduler = CronScheduler()
    scheduler.add("job", "*/15 * * * *", "run")
    queue = PendingQueue()
    assert scheduler.tick(datetime(2026, 8, 25, 9, 14), queue) == 0
    assert queue.ready(datetime(2026, 8, 25, 9, 15)) == []
    assert scheduler.tick(datetime(2026, 8, 25, 9, 15), queue) == 1
    assert scheduler.tick(datetime(2026, 8, 25, 9, 15), queue) == 0

    agent = ScheduledAgent()
    user_turn = agent.run_user_turn("hello")
    assert [message["role"] for message in user_turn] == ["user", "assistant"]
    scheduled_turns = agent.drain(queue, datetime(2026, 8, 25, 9, 15, 30))
    assert len(scheduled_turns) == 1
    assert len(queue) == 0
    assert len(agent.messages) == 4

    scheduler.add("second", "* * * * *", "second")
    scheduler.tick(datetime(2026, 8, 25, 9, 15), queue)
    item = queue.ready(datetime(2026, 8, 25, 9, 15))[0]
    with agent.gate.try_turn() as acquired:
        assert acquired
        assert agent.try_run_pending(item, datetime(2026, 8, 25, 9, 15)) is None
    assert len(queue) == 1
    assert agent.try_run_pending(item, datetime(2026, 8, 25, 9, 15)) is not None
    queue.remove(item)

    print("cron checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else demo()
