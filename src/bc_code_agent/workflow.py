"""Workflow Runtime（Step 20）：配置文件驱动的固定编排 + journal 断点续跑。

- workflows/*.yaml 注册（校验 name slug / description / steps / run_if），坏文件跳过并告警
- 4 种 step：command / agent / parallel / pipeline；run_if 简单条件（always / prev_failed / prev_succeeded）
- 每步结果写 journal（sessions/<id>/workflows/<runId>.journal.jsonl）；resume 时按
  稳定内容 key 命中缓存直接复用（配置不变不重跑，配置变才重跑下游）
- agent step 可选 schema：强制结构化 JSON（简版校验 + 重试一次）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

try:
    import yaml  # pyyaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

TRUSTED_STEP_TYPES = ("command", "agent", "parallel", "pipeline")
RUN_IF_PREFIXES = ("always", "prev_failed", "prev_succeeded")
AGENT_PROFILES = ("explore", "general", "review", "research")
COMMAND_TIMEOUT = 600

WORKFLOW_TOOL_SCHEMA: dict[str, Any] = {
    "name": "Workflow",
    "description": (
        "Run a saved workflow (fixed registration from workflows/*.yaml). "
        "The model supplies only name/args; orchestration lives in the registry."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Workflow name from /workflow list"},
            "args": {
                "type": "object",
                "description": "Arguments; referenced in prompts as {args.key}",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}


class WorkflowError(Exception):
    """workflow 配置或运行不可安全使用。"""


# ---------- 加载与校验 ----------


def _slug_ok(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name or ""))


def validate_workflow(data: Any) -> str | None:
    """返回错误信息；None = 合法。"""
    if not isinstance(data, dict):
        return "workflow 必须是 YAML 对象"
    name = data.get("name") or ""
    if not name or not isinstance(name, str):
        return "缺少 name（字符串）"
    if not _slug_ok(name):
        return "name 必须是 1-64 位安全 slug（字母数字 . _ -）"
    if not data.get("description"):
        return "缺少 description"
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return "steps 必须是非空列表"
    seen: set[str] = set()
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            return f"step #{i} 必须是对象"
        step_id = step.get("id") or ""
        if not step_id or not isinstance(step_id, str):
            return f"step #{i} 缺少 id"
        if step_id in seen:
            return f"step id 重复: {step_id}"
        seen.add(step_id)
        step_type = step.get("type") or ""
        if step_type not in TRUSTED_STEP_TYPES:
            return f"step {step_id}: 未知 type {step_type!r}（允许 {TRUSTED_STEP_TYPES}）"
        run_if = step.get("run_if", "always")
        if not _valid_run_if(run_if, seen):
            return (
                f"step {step_id}: 非法 run_if {run_if!r}（允许 always / prev_failed / "
                "prev_succeeded / <step_id>.failed / <step_id>.succeeded，且引用步骤须在前）"
            )
        if step_type == "command" and not isinstance(step.get("command"), str):
            return f"step {step_id}: command 类型需要 command 字符串"
        if step_type == "agent":
            profile = step.get("profile") or ""
            if profile not in AGENT_PROFILES:
                return f"step {step_id}: agent profile 必须是 {AGENT_PROFILES}"
            if not isinstance(step.get("prompt"), str):
                return f"step {step_id}: agent 需要 prompt 字符串"
        if step_type == "parallel":
            agents = step.get("agents") or []
            if not isinstance(agents, list) or not agents:
                return f"step {step_id}: parallel 需要非空 agents 列表"
            for j, agent in enumerate(agents, 1):
                if not isinstance(agent, dict) or not isinstance(agent.get("prompt"), str):
                    return f"step {step_id}: agents[{j}] 需要 prompt 字符串"
        if step_type == "pipeline":
            items = step.get("items") or []
            stages = step.get("stages") or []
            if not isinstance(items, list) or not items:
                return f"step {step_id}: pipeline 需要非空 items"
            if not isinstance(stages, list) or not stages:
                return f"step {step_id}: pipeline 需要非空 stages"
    return None


class WorkflowRegistry:
    """扫描 workflows/*.yaml；坏文件跳过并告警（资产集合，不因一个坏文件全挂）。"""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.workflows: dict[str, dict[str, Any]] = {}

    def load(self) -> int:
        count = 0
        if not self.directory.is_dir():
            print(f"[Workflow] 未找到 {self.directory}，注册表为空")
            return 0
        for path in sorted(self.directory.glob("*.yaml")) + sorted(
            self.directory.glob("*.yml")
        ):
            try:
                if yaml is None:
                    raise WorkflowError("缺少 pyyaml 依赖：pip install pyyaml")
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(f"[Workflow] 跳过 {path.name}: 解析失败（{exc}）")
                continue
            err = validate_workflow(data)
            if err:
                print(f"[Workflow] 跳过 {path.name}: {err}")
                continue
            self.workflows[str(data["name"])] = data
            count += 1
        print(f"[Workflow] loaded {count} workflow(s) from {self.directory}")
        return count

    def get(self, name: str) -> dict[str, Any] | None:
        return self.workflows.get(name)

    def list_text(self) -> str:
        if not self.workflows:
            return "没有已注册的 workflow（检查 workflows/*.yaml）"
        lines = ["name                描述"]
        for name, data in self.workflows.items():
            lines.append(f"{name:<20} {str(data.get('description', ''))[:44]}")
        return "\n".join(lines)


# ---------- 简版 schema 校验 ----------

_BASIC_TYPES = {"string": str, "number": (int, float), "boolean": bool,
                "array": list, "object": dict}


def validate_schema_value(schema: dict | None, value: Any, depth: int = 0) -> tuple[bool, str]:
    """简版校验：type + required + properties（递归 2 层）。"""
    if not schema:
        return True, ""
    if depth > 2:
        return True, ""
    expected = _BASIC_TYPES.get(schema.get("type"))
    if expected is not None and not isinstance(value, expected):
        return False, f"期望 {schema['type']}，实际 {type(value).__name__}"
    for key in schema.get("required") or []:
        if key not in value or value[key] is None:
            return False, f"缺少必需字段: {key}"
    for key, sub in (schema.get("properties") or {}).items():
        if key in value and isinstance(sub, dict):
            ok, err = validate_schema_value(sub, value[key], depth + 1)
            if not ok:
                return False, err
    return True, ""


# ---------- journal（审计记录；本版不做断点续跑） ----------


def _stable_hash(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


class WorkflowJournal:
    """append-only jsonl：每步执行历史（status/输出/耗时），供 /workflow status 与人工排查。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(self, key: str, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")
            handle.flush()


def _step_key(workflow_name: str, step: dict[str, Any]) -> str:
    basis = json.dumps(
        {"wf": workflow_name, "step": step}, sort_keys=True, ensure_ascii=False
    )
    return f"step-{_stable_hash(basis) % 10**10:010d}"


# ---------- runtime ----------


def _valid_run_if(run_if: str, seen_ids: set[str]) -> bool:
    """run_if 合法：前缀模式，或 <step_id>.failed/succeeded 且引用步骤已在前。"""
    if run_if in RUN_IF_PREFIXES:
        return True
    if "." in run_if:
        step_id, _, status = run_if.partition(".")
        return bool(step_id) and step_id in seen_ids and status in ("failed", "succeeded")
    return False


def _run_if_met(run_if: str, results: dict[str, Any], prev_status: str) -> bool:
    """运行期判断：前缀模式看最近执行步骤；step 引用看指定步骤的实际状态。"""
    if run_if == "always":
        return True
    if run_if == "prev_failed":
        return prev_status == "failed"
    if run_if == "prev_succeeded":
        return prev_status == "succeeded"
    step_id, _, status = run_if.partition(".")
    record = results.get(step_id)
    if record is None:
        return False
    return record.get("status") == status


def _render(text: str, args: dict[str, Any], results: dict[str, Any]) -> str:
    """模板替换：{args.key}（初始参数）+ {steps.<id>.value|output}（前序步骤结果）。"""

    def repl(match: re.Match[str]) -> str:
        full = match.group(0)
        step_id = match.group(2)
        if step_id is not None:  # {steps.<id>.value|output}
            field = match.group(3)
            record = results.get(step_id)
            if record is None:
                return full
            value = record.get("value")
            if field == "output" and isinstance(value, dict):
                return str(value.get("output", ""))
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False)
            return str(value)
        return str(args.get(match.group(1), full))

    return re.sub(
        r"\{args\.([A-Za-z0-9_]+)\}|\{steps\.([A-Za-z0-9_-]+)\.(value|output)\}",
        repl,
        text,
    )


class WorkflowRunner:
    """agent 执行的注入点：生产=run_subagent；测试=mock。"""

    def run_agent(self, profile: str, prompt: str, schema: dict | None, label: str) -> Any:
        raise NotImplementedError


class WorkflowRuntime:
    """执行注册表里的 workflow：顺序 steps + journal 缓存 + 快照。"""

    def __init__(
        self, registry: WorkflowRegistry, runner: WorkflowRunner, store_dir: Path,
        cwd: Path | None = None,
        command_guard: Callable[[str, dict], str | None] | None = None,
    ) -> None:
        self.registry = registry
        self.runner = runner
        self.store_dir = Path(store_dir)
        self.cwd = Path(cwd) if cwd else Path(store_dir).resolve().parents[1]
        self.command_guard = command_guard
        self._lock = threading.Lock()

    # ---------- run 生命周期 ----------

    def start(self, name: str, args: dict[str, Any] | None = None) -> str:
        workflow = self.registry.get(name)
        if workflow is None:
            raise WorkflowError(f"未知 workflow: {name}（/workflow list 查看）")
        run_id = (
            f"{name}-{time.strftime('%Y%m%d-%H%M%S')}-"
            f"{os.getpid():05d}-{int(time.time() * 1000) % 100000:05d}"
        )
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        lock = run_dir / f"{run_id}.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise WorkflowError(f"run {run_id} 已存在（锁冲突）") from None
        os.close(fd)
        self._write_snapshot(run_id, workflow, args, status="running")
        return run_id

    def run(self, run_id: str) -> dict[str, Any]:
        snapshot = self._read_snapshot(run_id)
        if snapshot is None:
            raise WorkflowError(f"run 不存在: {run_id}")
        workflow = self.registry.get(str(snapshot["name"]))
        if workflow is None:
            raise WorkflowError(f"snapshot 引用的 workflow 不存在: {snapshot['name']}")
        args = snapshot.get("args") or {}
        journal = WorkflowJournal(self._run_dir(run_id) / f"{run_id}.journal.jsonl")
        with self._lock:
            return self._run_locked(run_id, snapshot, workflow, args, journal)

    def _run_locked(self, run_id: str, snapshot: dict[str, Any],
                    workflow: dict[str, Any], args: dict[str, Any],
                    journal: WorkflowJournal) -> dict[str, Any]:
        prev_status = "succeeded"  # 初始无前置（当作成功）
        results: dict[str, Any] = {}
        for step in workflow.get("steps") or []:
            step_id = str(step["id"])
            run_if = step.get("run_if", "always")
            if not _run_if_met(run_if, results, prev_status):
                # 跳过不改 prev_status：条件看“最近执行过”的步骤状态
                results[step_id] = {"status": "skipped", "reason": f"run_if={run_if}"}
                print(f"[Workflow] {step_id}: 跳过（run_if={run_if}）")
                continue

            print(f"[Workflow] {step_id}: 执行中...")
            key = _step_key(str(snapshot["name"]), step)
            start = time.perf_counter()
            try:
                value = self._execute_step(workflow, step, args, results)
                status = "succeeded" if not _step_failed(value) else "failed"
                record = {"status": status, "value": value,
                          "duration_ms": int((time.perf_counter() - start) * 1000)}
            except Exception as exc:  # noqa: BLE001
                print(f"[Workflow] {step_id}: 失败 - {type(exc).__name__}: {exc}")
                record = {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                          "duration_ms": int((time.perf_counter() - start) * 1000)}
            journal.record(key, record)
            results[step_id] = record
            prev_status = str(record.get("status", "succeeded"))

        status = "completed" if all(
            r.get("status") != "failed" for r in results.values()
        ) else "failed"
        self._write_snapshot(run_id, workflow, args, status=status, results=results)
        print(f"[Workflow] {run_id}: {status}")
        return {"run_id": run_id, "status": status, "steps": results}

    # ---------- 步骤执行 ----------

    def _execute_step(self, workflow: dict[str, Any], step: dict[str, Any],
                      args: dict[str, Any], results: dict[str, Any]) -> Any:
        step_type = step["type"]
        if step_type == "command":
            return self._execute_command(step)
        if step_type == "agent":
            return self._execute_agent(step, args, results)
        if step_type == "parallel":
            return self._execute_parallel(step, args, results)
        if step_type == "pipeline":
            return self._execute_pipeline(step, args, results)
        raise WorkflowError(f"未知 step 类型: {step_type}")

    def _execute_command(self, step: dict[str, Any]) -> dict[str, Any]:
        command = str(step["command"])
        # 命令步骤过同一道闸（P1）：黑名单兜底 + 权限管道（deny/ask/yolo/scheduled）
        if self.command_guard is not None:
            denied = self.command_guard("Shell", {"command": command})
            if denied is not None:
                print(f"[Workflow] command 步骤被拒: {denied[:120]}")
                return {"exit_code": None, "output": denied}
        timeout = float(step.get("timeout", COMMAND_TIMEOUT))
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                errors="replace", timeout=timeout, cwd=str(self.cwd),
            )
        except subprocess.TimeoutExpired:
            return {"exit_code": None, "output": f"Error: timeout after {int(timeout)}s"}
        except OSError as exc:
            return {"exit_code": None, "output": f"Error: {type(exc).__name__}: {exc}"}
        output = (proc.stdout or "") + (proc.stderr or "")
        return {"exit_code": proc.returncode, "output": output.strip()[:20000]}

    def _execute_agent(self, step: dict[str, Any], args: dict[str, Any],
                       results: dict[str, Any]) -> Any:
        prompt = _render(str(step["prompt"]), args, results)
        schema = step.get("schema") if isinstance(step.get("schema"), dict) else None
        value = self.runner.run_agent(
            str(step.get("profile", "general")),
            prompt,
            schema,
            str(step.get("label", step["id"])),
        )
        # schema 路径返回字符串 = 结构化输出失败（JSON/校验）→ 视为步骤失败
        if schema is not None and isinstance(value, str):
            raise WorkflowError(value)
        return value

    def _execute_parallel(self, step: dict[str, Any], args: dict[str, Any],
                          results: dict[str, Any]) -> list[Any]:
        agents = step.get("agents") or []
        with ThreadPoolExecutor(max_workers=max(1, len(agents))) as pool:
            futures = [
                pool.submit(self._execute_agent, agent, args, results)
                for agent in agents
            ]
            return [future.result() for future in futures]

    def _execute_pipeline(self, step: dict[str, Any], args: dict[str, Any],
                          results: dict[str, Any]) -> list[Any]:
        items = step.get("items") or []
        stages = step.get("stages") or []

        def run_item(idx_and_item: tuple[int, Any]) -> Any:
            idx, item = idx_and_item
            value = item
            for stage in stages:
                value = self._execute_agent(
                    stage,
                    dict(args, item=value, item_index=idx),
                    results,
                )
            return value

        with ThreadPoolExecutor(max_workers=max(1, len(items))) as pool:
            return list(pool.map(run_item, list(enumerate(items))))

    # ---------- 存储 ----------

    def _run_dir(self, run_id: str) -> Path:
        return self.store_dir / run_id

    def _write_snapshot(self, run_id: str, workflow: dict[str, Any], args: dict[str, Any],
                        *, status: str, results: dict[str, Any] | None = None) -> None:
        data = {"run_id": run_id, "name": workflow["name"], "args": args,
                "status": status, "updated_at": time.time()}
        if results is not None:
            data["results"] = results
        path = self._run_dir(run_id) / f"{run_id}.json"
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _read_snapshot(self, run_id: str) -> dict[str, Any] | None:
        path = self._run_dir(run_id) / f"{run_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def format_status(self, run_id: str) -> str:
        snap = self._read_snapshot(run_id)
        if snap is None:
            return f"run 不存在: {run_id}"
        lines = [f"run {run_id}: {snap.get('status')}（workflow={snap.get('name')}）"]
        results = snap.get("results") or {}
        if results:
            for step_id, record in results.items():
                lines.append(f"  {step_id}: {record.get('status')}")
        return "\n".join(lines)


def _step_failed(value: Any) -> bool:
    """步骤失败判定：command 结果中 exit_code 为 None（被拒/超时/启动失败）或非 0 视为失败。"""
    if isinstance(value, dict) and "exit_code" in value:
        return value["exit_code"] is None or value["exit_code"] != 0
    if isinstance(value, dict) and value.get("status") == "failed":
        return True
    return False
