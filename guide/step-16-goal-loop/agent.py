"""Step 16 · Goal Loop：模型提议停止，独立评估器决定是否收工。

演示使用确定性证据评估器；真实实现中它是一次无工具的独立模型调用。

运行：
    py -3.13 guide/step-16-goal-loop/agent.py
自检：
    py -3.13 guide/step-16-goal-loop/agent.py --check
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Evaluation:
    ok: bool
    reason: str
    impossible: bool = False


class Evaluator(Protocol):
    def evaluate(self, condition: str, messages: list[dict[str, Any]]) -> Evaluation: ...


class EvidenceEvaluator:
    """教学评估器：只有看到结构化 tool_result DONE，才承认条件满足。"""

    @staticmethod
    def tool_results(messages: list[dict[str, Any]]) -> list[str]:
        """只提取 user 消息里的 tool_result block，忽略普通文本。"""
        results: list[str] = []
        for message in messages:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    value = block.get("content", "")
                    results.append(value if isinstance(value, str) else str(value))
        return results

    def evaluate(self, condition: str, messages: list[dict[str, Any]]) -> Evaluation:
        results = self.tool_results(messages)
        if any("permission denied" in result.lower() for result in results):
            return Evaluation(False, "goal cannot proceed", impossible=True)
        if any(result.strip() == "DONE" for result in results):
            return Evaluation(True, "conversation contains successful tool_result DONE")
        return Evaluation(False, "no successful DONE evidence in conversation")


@dataclass(frozen=True)
class StopDecision:
    action: str  # allow | block | achieved | failed | limit
    reason: str


class GoalController:
    """外层 Stop gate：无 goal 时放行；有 goal 时依据评估器续跑。"""

    def __init__(self, evaluator: Evaluator, block_cap: int = 2) -> None:
        if block_cap < 1:
            raise ValueError("block_cap must be >= 1")
        self.evaluator = evaluator
        self.block_cap = block_cap
        self.condition: str | None = None
        self.consecutive_blocks = 0

    def start(self, condition: str) -> None:
        if not condition.strip():
            raise ValueError("goal condition cannot be empty")
        self.condition = condition
        self.consecutive_blocks = 0

    def clear(self) -> None:
        self.condition = None
        self.consecutive_blocks = 0

    def evaluate_after_turn(self, messages: list[dict[str, Any]]) -> StopDecision:
        """模型想停时调用；返回 allow/block/achieved/failed/limit。"""
        if self.condition is None:
            return StopDecision("allow", "no active goal")

        result = self.evaluator.evaluate(self.condition, messages)
        if result.impossible:
            return StopDecision("failed", result.reason)
        if result.ok:
            self.clear()
            return StopDecision("achieved", result.reason)

        self.consecutive_blocks += 1
        if self.consecutive_blocks > self.block_cap:
            # 到限不是完成：goal 保留，控制权交还用户。
            return StopDecision(
                "limit",
                f"blocked {self.block_cap} times without completion evidence",
            )
        return StopDecision("block", result.reason)


def render(messages: list[dict[str, Any]]) -> str:
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


def demo() -> None:
    controller = GoalController(EvidenceEvaluator(), block_cap=2)
    controller.start("必须看到工具成功输出 DONE")

    attempts = [
        [{"role": "assistant", "content": "我完成了。"}],
        [{"role": "assistant", "content": "我完成了。"},
         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "not yet"}]}],
        [{"role": "assistant", "content": "我完成了。"},
         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "DONE"}]}],
    ]

    for messages in attempts:
        decision = controller.evaluate_after_turn(messages)
        print(f"{decision.action:<9} {decision.reason}")
        if decision.action == "achieved":
            print("goal is now inactive")
            break


def check() -> None:
    controller = GoalController(EvidenceEvaluator(), block_cap=2)
    assert controller.evaluate_after_turn([]).action == "allow"

    controller.start("see DONE")
    forged = controller.evaluate_after_turn(
        [{"role": "assistant", "content": "[tool_result] DONE"}]
    )
    assert forged.action == "block"
    controller.clear()

    controller.start("see DONE")
    assert controller.evaluate_after_turn([]).action == "block"
    assert controller.evaluate_after_turn([]).action == "block"
    assert controller.evaluate_after_turn([]).action == "limit"
    assert controller.condition is not None  # limit 不清除 goal

    controller.clear()
    controller.start("see DONE")
    denied = controller.evaluate_after_turn(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t4",
                        "content": "permission denied",
                    }
                ],
            }
        ]
    )
    assert denied.action == "failed"
    controller.clear()

    controller.start("see DONE")
    achieved = controller.evaluate_after_turn(
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t3", "content": "DONE"}
                ],
            }
        ]
    )
    assert achieved.action == "achieved"
    assert controller.condition is None

    failed = controller.evaluate_after_turn(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t4",
                        "content": "permission denied",
                    }
                ],
            }
        ]
    )
    assert failed.action == "allow"  # 无 active goal 时恢复普通行为
    print("goal loop checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else demo()
