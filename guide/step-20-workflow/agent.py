"""Step 20 · Workflow：固定路径、条件执行与 JSONL 审计。

教学版使用内置 registry，不依赖 PyYAML，也不调用 API。

运行：
    py -3.13 guide/step-20-workflow/agent.py
自检：
    py -3.13 guide/step-20-workflow/agent.py --check
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def collect_facts(files: int, tests_passed: bool) -> dict[str, Any]:
    """内置 command：真实实现会调用文件读取、Shell 等工具。"""
    return {
        "ok": True,
        "files": files,
        "tests_passed": tests_passed,
    }


def mark_ready(version: str) -> dict[str, Any]:
    return {"ok": True, "message": f"{version} is ready"}


COMMAND_REGISTRY = {
    "collect_facts": collect_facts,
    "mark_ready": mark_ready,
}


def offline_agent(prompt: str, context: dict[str, Any]) -> dict[str, Any]:
    """内置 agent：真实实现会创建一个带工具循环的子 Agent。"""
    facts = context["outputs"]["collect_facts"]
    return {
        "approved": bool(facts["ok"] and facts["tests_passed"]),
        "summary": f"{facts['files']} files reviewed for prompt: {prompt}",
    }


# 这个字典刻意保持 YAML 的形状。完整实现从 .yaml 文件读入后，
# 也是交给同一个 WorkflowRunner，而不是另写一套执行逻辑。
WORKFLOW_REGISTRY: dict[str, dict[str, Any]] = {
    "release-guard": {
        "steps": [
            {
                "name": "collect_facts",
                "type": "command",
                "command": "collect_facts",
                "args": {"files": 3, "tests_passed": True},
            },
            {
                "name": "agent_review",
                "type": "agent",
                "prompt": "评审当前变更，只有测试通过才允许标记 ready",
                "run_if": "outputs.collect_facts.ok == true",
            },
            {
                "name": "mark_ready",
                "type": "command",
                "command": "mark_ready",
                "args": {"version": "v1.2.3"},
                "run_if": "outputs.agent_review.approved == true",
            },
            {
                "name": "request_human_review",
                "type": "agent",
                "prompt": "测试未通过，请人工介入",
                "run_if": "outputs.agent_review.approved == false",
            },
        ]
    }
}


@dataclass(frozen=True)
class WorkflowResult:
    workflow: str
    status: str
    steps: list[dict[str, str]]
    outputs: dict[str, Any]


class Journal:
    """JSONL 审计日志：一行一个事件，只追加，不覆盖。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sequence = 0

    def append(self, event_type: str, **fields: Any) -> dict[str, Any]:
        self.sequence += 1
        record = {
            "seq": self.sequence,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "type": event_type,
            **fields,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _parse_scalar(raw: str) -> str | bool | int:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.isdigit():
        return int(raw)
    return raw.strip("'\"")


def _lookup(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(f"unknown condition path: {path}")
        value = value[key]
    return value


def _lookup_output(context: dict[str, Any], path: str) -> Any:
    """条件只能读取 outputs.<step>.<field>，不能访问宿主任意上下文。"""
    parts = path.split(".")
    if len(parts) < 2 or parts[0] != "outputs":
        raise ValueError(f"condition path must start with 'outputs.<field>': {path!r}")
    return _lookup(context, path)


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """受限条件语言，只允许读取 outputs 下的一条路径。"""
    for operator in ("==", "!="):
        if operator in expression:
            left, right = (part.strip() for part in expression.split(operator, 1))
            actual = _lookup_output(context, left)
            expected = _parse_scalar(right)
            return actual == expected if operator == "==" else actual != expected

    path = expression.strip()
    return bool(_lookup_output(context, path))


class WorkflowRunner:
    def __init__(
        self,
        journal: Journal,
        registry: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.journal = journal
        self.registry = registry if registry is not None else WORKFLOW_REGISTRY

    def run(self, workflow_name: str) -> WorkflowResult:
        if workflow_name not in self.registry:
            raise KeyError(f"unknown workflow: {workflow_name}")

        workflow = self.registry[workflow_name]
        steps = workflow.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"workflow {workflow_name!r} has no steps")

        context: dict[str, Any] = {"outputs": {}}
        statuses: list[dict[str, str]] = []
        self._validate_steps(workflow_name, steps)
        self.journal.append("workflow_started", workflow=workflow_name)

        try:
            for index, step in enumerate(steps):
                name = step["name"]
                condition = step.get("run_if")
                self.journal.append(
                    "step_started",
                    workflow=workflow_name,
                    index=index,
                    step=name,
                    step_type=step["type"],
                )
                try:
                    if condition is not None and not evaluate_condition(condition, context):
                        status = {"step": name, "status": "skipped", "run_if": condition}
                        statuses.append(status)
                        self.journal.append(
                            "step_skipped",
                            workflow=workflow_name,
                            index=index,
                            step=name,
                            run_if=condition,
                        )
                        continue

                    output = self._execute(step, context)
                    context["outputs"][name] = output
                    status = {"step": name, "status": "ran"}
                    statuses.append(status)
                    self.journal.append(
                        "step_finished",
                        workflow=workflow_name,
                        index=index,
                        step=name,
                        output=output,
                    )
                except Exception as error:
                    self.journal.append(
                        "step_failed",
                        workflow=workflow_name,
                        index=index,
                        step=name,
                        error=f"{type(error).__name__}: {error}",
                    )
                    raise
        except Exception as error:
            self.journal.append(
                "workflow_failed",
                workflow=workflow_name,
                error=f"{type(error).__name__}: {error}",
            )
            raise

        result = WorkflowResult(workflow_name, "finished", statuses, context["outputs"])
        self.journal.append(
            "workflow_finished",
            workflow=workflow_name,
            status=result.status,
            steps=statuses,
        )
        return result

    def _validate_steps(self, workflow_name: str, steps: list[dict[str, Any]]) -> None:
        names: set[str] = set()
        for step in steps:
            name = step.get("name")
            kind = step.get("type")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError(f"invalid or duplicate step name in {workflow_name}: {name!r}")
            names.add(name)
            if kind not in {"command", "agent"}:
                raise ValueError(f"unsupported step type in {workflow_name}.{name}: {kind!r}")
            if kind == "command":
                command = step.get("command")
                if not isinstance(command, str) or command not in COMMAND_REGISTRY:
                    raise ValueError(f"unknown command in {workflow_name}.{name}: {command!r}")
            elif not isinstance(step.get("prompt"), str) or not step["prompt"].strip():
                raise ValueError(f"agent step requires prompt: {workflow_name}.{name}")

    def _execute(self, step: dict[str, Any], context: dict[str, Any]) -> Any:
        if step["type"] == "command":
            handler = COMMAND_REGISTRY[step["command"]]
            return handler(**step.get("args", {}))
        return offline_agent(step["prompt"], context)


def _run_demo() -> tuple[WorkflowResult, int]:
    with tempfile.TemporaryDirectory(prefix="bc-workflow-") as temporary:
        journal = Journal(Path(temporary) / "journal.jsonl")
        result = WorkflowRunner(journal).run("release-guard")
        return result, len(journal.records())


def demo() -> None:
    result, event_count = _run_demo()
    print(f"workflow: {result.workflow}")
    for step in result.steps:
        if step["status"] == "skipped":
            print(f"step {step['step']}: skipped ({step['run_if']})")
        else:
            print(f"step {step['step']}: ran")
    print(f"workflow status: {result.status}")
    print(f"journal events: {event_count} (temporary JSONL)")
    print(f"ready message: {result.outputs['mark_ready']['message']}")


def check() -> None:
    context = {
        "outputs": {
            "facts": {"ok": True, "count": 3},
            "review": {"approved": False},
        }
    }
    assert evaluate_condition("outputs.facts.ok == true", context)
    assert evaluate_condition("outputs.facts.count == 3", context)
    assert evaluate_condition("outputs.review.approved == false", context)
    assert evaluate_condition("outputs.review.approved != true", context)
    assert evaluate_condition("outputs.facts.ok", context)
    try:
        evaluate_condition("settings.enabled == false", context)
    except ValueError:
        pass
    else:
        raise AssertionError("non-outputs condition path was accepted")

    try:
        evaluate_condition("outputs.facts.missing", context)
    except KeyError:
        pass
    else:
        raise AssertionError("unknown condition path was accepted")

    with tempfile.TemporaryDirectory(prefix="bc-workflow-check-") as temporary:
        journal = Journal(Path(temporary) / "journal.jsonl")
        result = WorkflowRunner(journal).run("release-guard")
        assert result.status == "finished"
        assert [step["status"] for step in result.steps] == ["ran", "ran", "ran", "skipped"]
        assert result.outputs["agent_review"]["approved"] is True
        assert "request_human_review" not in result.outputs

        records = journal.records()
        assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
        assert records[0]["type"] == "workflow_started"
        assert records[-1]["type"] == "workflow_finished"
        assert sum(record["type"] == "step_started" for record in records) == 4
        assert sum(record["type"] == "step_finished" for record in records) == 3
        assert sum(record["type"] == "step_skipped" for record in records) == 1

        invalid_registry = {
            "bad": {
                "steps": [
                    {"name": "unknown", "type": "command", "command": "missing"},
                ]
            }
        }
        failed_journal = Journal(Path(temporary) / "failed-journal.jsonl")
        try:
            WorkflowRunner(failed_journal, invalid_registry).run("bad")
        except ValueError:
            pass
        else:
            raise AssertionError("unknown command was accepted")
        assert failed_journal.records() == []

        COMMAND_REGISTRY["fail"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        failure_registry = {
            "fails": {
                "steps": [
                    {"name": "explode", "type": "command", "command": "fail"},
                ]
            }
        }
        runtime_journal = Journal(Path(temporary) / "runtime-failed.jsonl")
        try:
            WorkflowRunner(runtime_journal, failure_registry).run("fails")
        except RuntimeError:
            pass
        else:
            raise AssertionError("step exception was swallowed")
        finally:
            COMMAND_REGISTRY.pop("fail", None)

        events = [record["type"] for record in runtime_journal.records()]
        assert events == [
            "workflow_started",
            "step_started",
            "step_failed",
            "workflow_failed",
        ]

    print("workflow checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else demo()
