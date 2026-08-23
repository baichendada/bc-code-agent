"""Cron 调度（Step 19）：到点把 prompt 投递给主循环，跑一轮完整 agent turn。

- 五段 cron 表达式（分 时 日 月 周）：`*` / `*/N` / `N` / `N-M` / `N,M,...`
- 调度线程每秒 poll_due()：命中且本分钟未触发且未在投递 → 标记 pending + 落盘
- 队列处理器（queue processor）抢到 agent_lock 后 take_pending() 投递，
  跑完一轮后 ack：一次性任务删除、重复任务保留等下一周期
- 持久化 sessions/<id>/cron.json（tmp + os.replace 原子写；损坏 → 启动报错）
- 学习版本语义：MVP 为「最多一次投递」（一轮结束即确认）；停机错过的时刻不补跑
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FIELD_RANGES = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),  # 0 = 周日
]


class CronError(Exception):
    """cron 配置或命令不可安全使用。"""


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool = True
    enabled: bool = True
    pending_delivery: bool = False
    last_fired: str | None = None
    created_at: float = field(default_factory=time.time)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CronJob":
        return cls(
            id=str(raw["id"]),
            cron=str(raw["cron"]),
            prompt=str(raw["prompt"]),
            recurring=bool(raw.get("recurring", True)),
            enabled=bool(raw.get("enabled", True)),
            pending_delivery=bool(raw.get("pending_delivery", False)),
            last_fired=raw.get("last_fired"),
            created_at=float(raw.get("created_at") or time.time()),
        )


# ---------- 表达式校验 ----------


def _validate_cron_field(field: str, name: str, lo: int, hi: int) -> str | None:
    field = field.strip()
    if not field:
        return f"{name}: 字段为空"

    def check_range(value: int) -> str | None:
        if not lo <= value <= hi:
            return f"{name}: {value} 超出范围 [{lo}-{hi}]"
        return None

    if field == "*":
        return None
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit() or int(step) <= 0:
            return f"{name}: 步长必须为正整数（如 */5）"
        return None
    if "-" in field:
        parts = field.split("-")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return f"{name}: 范围写法应为 N-M（如 9-17）"
        a, b = int(parts[0]), int(parts[1])
        if a > b:
            return f"{name}: 范围起点不能大于终点（{a}-{b}）"
        return check_range(a) or check_range(b)
    if "," in field:
        for item in field.split(","):
            err = _validate_cron_field(item, name, lo, hi)
            if err:
                return f"{name}: {err}"
        return None
    if field.isdigit():
        return check_range(int(field))
    return f"{name}: 无法解析的字段 {field!r}"


def validate_cron(expr: str) -> str | None:
    """返回错误信息；None = 合法。"""
    fields = (expr or "").split()
    if len(fields) != 5:
        return f"需要 5 段（分 时 日 月 周），实际 {len(fields)} 段: {expr!r}"
    for (name, lo, hi), field in zip(FIELD_RANGES, fields):
        err = _validate_cron_field(field, name, lo, hi)
        if err:
            return err
    return None


# ---------- 匹配 ----------


def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    if "-" in field:
        a, b = (int(p) for p in field.split("-"))
        return a <= value <= b
    if "," in field:
        return any(_cron_field_matches(item, value) for item in field.split(","))
    if field.isdigit():
        return int(field) == value
    return False


def cron_matches(expr: str, moment) -> bool:
    fields = (expr or "").split()
    if len(fields) != 5:
        return False
    # cron 语义: weekday 0=周日（与 datetime.weekday() 0=周一 不一致，需转换）
    cron_weekday = (moment.weekday() + 1) % 7
    values = [moment.minute, moment.hour, moment.day, moment.month, cron_weekday]
    return all(_cron_field_matches(f, v) for f, v in zip(fields, values))


# ---------- 存储 ----------

MINUTE_MARKER = "%Y-%m-%d %H:%M"


class CronStore:
    """cron 任务定义 + 运行状态；挂 sessions/<id>/cron.json。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.jobs: dict[str, CronJob] = {}
        self._counter = 0

    # ---------- 持久化 ----------

    def load(self) -> None:
        """启动恢复；文件不存在 → 空；损坏 → 报错（fail-closed，不静默丢任务）。"""
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CronError(f"cron.json 无法解析（{exc}）。请修复该文件，或删除它以从空开始。")
        if not isinstance(raw, list):
            raise CronError("cron.json 顶层必须是数组。请修复或删除该文件。")
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                continue
            job = CronJob.from_dict(item)
            self.jobs[job.id] = job
        self._counter = len(self.jobs)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            [job.to_dict() for job in self.jobs.values()],
            ensure_ascii=False,
            indent=2,
        )
        text = text.encode("utf-8", "replace").decode("utf-8")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------- 任务管理 ----------

    def add(self, cron: str, prompt: str, recurring: bool = True) -> CronJob:
        err = validate_cron(cron)
        if err:
            raise CronError(err)
        prompt = prompt.strip()
        if not prompt:
            raise CronError("prompt 不能为空")
        self._counter += 1
        job = CronJob(
            id=f"c_{self._counter:04d}",
            cron=cron,
            prompt=prompt,
            recurring=recurring,
        )
        self.jobs[job.id] = job
        self.save()
        return job

    def remove(self, job_id: str) -> str:
        job = self.jobs.pop(job_id, None)
        if job is None:
            return f"任务不存在: {job_id}"
        self.save()
        return f"已删除 {job_id}: {job.cron} {job.prompt[:40]}"

    def set_enabled(self, job_id: str, enabled: bool) -> str:
        job = self.jobs.get(job_id)
        if job is None:
            return f"任务不存在: {job_id}"
        job.enabled = enabled
        self.save()
        return f"已{'恢复' if enabled else '暂停'} {job_id}: {job.prompt[:40]}"

    # ---------- 到期与投递 ----------

    def poll_due(self, moment) -> list[CronJob]:
        """到期检查（调度线程每秒调用）。命中 → pending + last_fired + 落盘。"""
        minute_marker = moment.strftime(MINUTE_MARKER)
        due: list[CronJob] = []
        for job in self.jobs.values():
            if not job.enabled or job.pending_delivery:
                continue
            if job.last_fired == minute_marker:
                continue
            if cron_matches(job.cron, moment):
                job.pending_delivery = True
                job.last_fired = minute_marker
                due.append(job)
        if due:
            self.save()
        return due

    def take_pending(self) -> list[CronJob]:
        """队列处理器抢到锁后消费：取出待投递任务并清除 pending。"""
        pending = [j for j in self.jobs.values() if j.pending_delivery]
        if not pending:
            return []
        for job in pending:
            job.pending_delivery = False
        self.save()
        return pending

    def ack(self, jobs: list[CronJob]) -> None:
        """一轮 scheduled turn 结束后确认：一次性任务删除，重复任务保留。"""
        for job in jobs:
            if not job.recurring:
                self.jobs.pop(job.id, None)
        self.save()

    def list_text(self) -> str:
        if not self.jobs:
            return "没有定时任务"
        lines = ["id        cron                 状态    prompt"]
        for job in self.jobs.values():
            state = "暂停" if not job.enabled else ("待投递" if job.pending_delivery else "运行")
            lines.append(
                f"{job.id:<9} {job.cron:<20} {state:<5} {job.prompt[:40]}"
            )
        return "\n".join(lines)


# ---------- 工具 schema（模型侧也可注册/取消/查看） ----------

CRON_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "ScheduleCron",
        "description": (
            "Register a cron job: at each matching time the prompt is delivered "
            "as a scheduled agent turn. Cron format: 5 fields (minute hour day "
            "month weekday), supporting * */N N N-M N,M,..."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cron": {"type": "string", "description": "e.g. '0 9 * * *' (daily 09:00), '*/30 * * * *' (every 30 min)"},
                "prompt": {"type": "string", "description": "Task to run when the schedule fires"},
                "recurring": {
                    "type": "boolean",
                    "description": "true=repeat on each match (default); false=one-shot, removed after first delivery",
                },
            },
            "required": ["cron", "prompt"],
        },
    },
    {
        "name": "ListCrons",
        "description": "List all registered cron jobs with id, schedule, status and prompt.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "CancelCron",
        "description": "Remove a cron job by id (from ListCrons).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
]


def format_status_message(jobs: list[CronJob], prefix: str = "[Scheduled]") -> str:
    return "\n".join(f"{prefix} {job.prompt}" for job in jobs)
