"""Step 11 · Agent Team：单队、Spawn、inbox、消息唤醒与 lead 汇报。

无 API 演示：
    py -3.13 guide/step-11-agent-team/agent.py
自检：
    py -3.13 guide/step-11-agent-team/agent.py --check
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class InboxMessage:
    id: str
    sender: str
    kind: str
    body: str


@dataclass
class Teammate:
    id: str
    role: str
    brief: str
    inbox: list[InboxMessage] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)

    def wake(self, team: "Team", message: InboxMessage) -> None:
        team.wake_log.append(f"{self.id}:{message.id}")
        while self.inbox:
            incoming = self.inbox.pop(0)
            self.messages.append(
                {
                    "role": "user",
                    "content": f"{incoming.sender}({incoming.kind}): {incoming.body}",
                }
            )
            if self.role == "researcher" and incoming.kind == "task":
                self._run_task(team, incoming)
            elif self.id == "lead" and incoming.kind == "report":
                self._accept_report(incoming)
            else:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": f"{self.id} ignored {incoming.kind}",
                    }
                )

    def _run_task(self, team: "Team", task: InboxMessage) -> None:
        call_id = f"{self.id}-tool-{len(self.messages) + 1}"
        self.messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"我处理任务：{task.body}"},
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": "read_notes",
                        "input": {"topic": task.body},
                    },
                ],
            }
        )
        result = read_notes({"topic": task.body})
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": result,
                    }
                ],
            }
        )
        report = f"{self.id} 已完成「{task.body}」：{result}"
        self.messages.append({"role": "assistant", "content": report})
        team.send("lead", self.id, "report", report)

    def _accept_report(self, report: InboxMessage) -> None:
        self.reports.append(report.body)
        self.messages.append(
            {
                "role": "assistant",
                "content": f"已记录来自 {report.sender} 的汇报：{report.body}",
            }
        )


class Team:
    """唯一团队边界：lead 常驻，其他成员由 Spawn 创建。"""

    def __init__(self) -> None:
        self.members: dict[str, Teammate] = {}
        self.wake_log: list[str] = []
        self._next_member = 1
        self._next_message = 1
        lead = Teammate("lead", "lead", "汇总成员汇报并对外返回结论")
        self.members[lead.id] = lead

    def spawn(self, role: str, brief: str) -> str:
        member_id = f"{role}-{self._next_member}"
        self._next_member += 1
        self.members[member_id] = Teammate(member_id, role, brief)
        return member_id

    def send(self, to: str, sender: str, kind: str, body: str) -> str:
        recipient = self.members.get(to)
        if recipient is None:
            raise RuntimeError(f"unknown teammate: {to}")
        message_id = f"msg-{self._next_message}"
        self._next_message += 1
        message = InboxMessage(message_id, sender, kind, body)
        recipient.inbox.append(message)
        recipient.wake(self, message)
        return message_id

    def lead_report(self) -> str:
        lead = self.members["lead"]
        lines = [f"lead received {len(lead.reports)} reports"]
        lines.extend(f"- {report}" for report in lead.reports)
        return "\n".join(lines)


def read_notes(tool_input: dict[str, Any]) -> str:
    topic = str(tool_input["topic"])
    notes = {
        "检查配置": "配置可离线运行",
        "检查端口": "端口约定为本地回环",
    }
    return notes.get(topic, "没有找到相关笔记")


class TeamHarness:
    """把团队操作暴露成统一工具，方便接入 Step 05 的主循环。"""

    def __init__(self) -> None:
        self.team = Team()

    def execute_tool(self, name: str, tool_input: dict[str, Any]) -> str:
        handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "Spawn": lambda value: self.team.spawn(
                str(value["role"]), str(value["brief"])
            ),
            "Message": lambda value: self.team.send(
                str(value["to"]), "lead", "task", str(value["body"])
            ),
            "LeadReport": lambda _: self.team.lead_report(),
        }
        handler = handlers.get(name)
        if handler is None:
            raise RuntimeError(f"unknown team tool: {name}")
        return handler(tool_input)


def run_demo() -> TeamHarness:
    harness = TeamHarness()
    member_id = harness.execute_tool(
        "Spawn", {"role": "researcher", "brief": "读取项目笔记并汇报"}
    )
    print(f"Spawn -> {member_id}")

    first = harness.execute_tool(
        "Message", {"to": member_id, "body": "检查配置"}
    )
    print(f"Message -> {member_id} handled: 检查配置 ({first})")

    second = harness.execute_tool(
        "Message", {"to": member_id, "body": "检查端口"}
    )
    print(f"Message -> {member_id} handled: 检查端口 ({second})")

    report = harness.execute_tool("LeadReport", {})
    print("LeadReport -> " + report.splitlines()[0])
    print("wake order -> " + ", ".join(harness.team.wake_log))
    return harness


def check() -> None:
    harness = run_demo()
    team = harness.team
    assert set(team.members) == {"lead", "researcher-1"}
    assert len(team.members) == 2

    researcher = team.members["researcher-1"]
    lead = team.members["lead"]
    assert researcher.inbox == []
    assert lead.inbox == []
    assert len(researcher.messages) == 8
    assert len(lead.reports) == 2
    assert any("检查配置" in message.get("content", "") for message in researcher.messages)
    assert any("检查端口" in message.get("content", "") for message in researcher.messages)
    assert team.wake_log == [
        "researcher-1:msg-1",
        "lead:msg-2",
        "researcher-1:msg-3",
        "lead:msg-4",
    ]

    tool_uses = [
        block
        for message in researcher.messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_use"
    ]
    assert [call["name"] for call in tool_uses] == ["read_notes", "read_notes"]

    assert "lead received 2 reports" in team.lead_report()
    try:
        team.send("nobody", "lead", "task", "test")
    except RuntimeError as error:
        assert "unknown teammate" in str(error)
    else:
        raise AssertionError("message to unknown teammate was accepted")

    print("check: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run deterministic assertions")
    args = parser.parse_args()
    run_demo() if not args.check else check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
