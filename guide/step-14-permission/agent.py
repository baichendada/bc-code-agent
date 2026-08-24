"""Step 14 · Permission：工具执行前的 allow / ask / deny 管道。

教学版不执行任何真实工具，只演示规则如何变成决策。

运行演示：
    py -3.13 guide/step-14-permission/agent.py
运行自检：
    py -3.13 guide/step-14-permission/agent.py --check
"""

from __future__ import annotations

import argparse
import fnmatch
from dataclasses import dataclass
from typing import Any


DECISIONS = ("deny", "ask", "allow")


@dataclass(frozen=True)
class Verdict:
    """一次权限决策。decision 只有 deny / ask / allow 三种。"""

    decision: str
    rule: str
    reason: str


class PermissionGate:
    """最小权限管道：deny > ask > allow > default。"""

    def __init__(self, rules: list[dict[str, str]], default: str = "ask") -> None:
        if default not in DECISIONS:
            raise ValueError(f"default must be one of {DECISIONS}")
        self.rules = rules
        self.default = default

    def check(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> Verdict:
        tool_input = tool_input or {}

        # 档位优先：即使 allow 规则写在前面，deny 也必须赢。
        for decision in DECISIONS:
            for rule in self.rules:
                if rule["decision"] != decision:
                    continue
                pattern = rule["match"]
                if self._matches(pattern, tool_name, tool_input):
                    return Verdict(
                        decision=decision,
                        rule=pattern,
                        reason=f"{tool_name} matched {pattern!r} -> {decision}",
                    )

        return Verdict(
            decision=self.default,
            rule="(default)",
            reason=f"{tool_name} matched no rule -> default={self.default}",
        )

    def decide_and_execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        interactive: bool,
        approved: set[str] | None = None,
    ) -> str:
        """把决策转成执行结果；这里用占位 handler 表示真实工具执行。"""
        verdict = self.check(tool_name, tool_input)
        if verdict.decision == "deny":
            return f"DENIED: {verdict.reason}"

        if verdict.decision == "ask":
            # 非交互环境不能假装用户同意，默认拒绝。
            if not interactive:
                return f"DENIED: non-interactive ask is fail-closed ({verdict.rule})"
            key = f"{tool_name}:{tool_input}"
            if key in (approved or set()):
                pass
            else:
                answer = input(f"Allow {tool_name} {tool_input}? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    return f"DENIED: user rejected {verdict.rule}"

        return f"EXECUTED: {tool_name} {tool_input}"

    @staticmethod
    def _matches(pattern: str, tool_name: str, tool_input: dict[str, Any]) -> bool:
        """支持 Read、Read|Grep、Shell(*)、Write(path=.env*) 四种教学形态。"""
        if "(" not in pattern:
            return any(
                fnmatch.fnmatchcase(tool_name, part.strip())
                for part in pattern.split("|")
            )

        prefix, raw_args = pattern.split("(", 1)
        tool_matches = any(
            fnmatch.fnmatchcase(tool_name, part.strip())
            for part in prefix.split("|")
        )
        if not tool_matches:
            return False

        arg_pattern = raw_args.removesuffix(")")
        if arg_pattern == "*":
            return True
        if "=" not in arg_pattern:
            return any(
                fnmatch.fnmatchcase(str(value), arg_pattern)
                for value in tool_input.values()
            )

        key, _, value_pattern = arg_pattern.partition("=")
        value = tool_input.get(key.strip())
        return value is not None and fnmatch.fnmatchcase(str(value), value_pattern.strip())


RULES = [
    # 宽松规则在前也不影响安全：deny 档位永远先评估。
    {"match": "Read|Grep|Glob", "decision": "allow"},
    {"match": "Shell(git push*)", "decision": "ask"},
    {"match": "Shell(*)", "decision": "allow"},
    {"match": "Write(path=.env*)", "decision": "deny"},
    {"match": "Shell(rm -rf*)", "decision": "deny"},
]


def demo() -> None:
    gate = PermissionGate(RULES)
    calls = [
        ("Read", {"path": "README.md"}),
        ("Shell", {"command": "pytest -q"}),
        ("Shell", {"command": "git push origin main"}),
        ("Shell", {"command": "rm -rf /"}),
        ("Write", {"path": ".env", "content": "SECRET=1"}),
        ("Task", {"prompt": "unknown tool"}),
    ]
    print("decision  tool                     rule")
    print("-" * 78)
    for tool_name, tool_input in calls:
        verdict = gate.check(tool_name, tool_input)
        print(f"{verdict.decision:<8}  {tool_name:<24} {verdict.rule}")

    print("\nNon-interactive ask:")
    print(gate.decide_and_execute("Shell", {"command": "git push"}, interactive=False))


def check() -> None:
    gate = PermissionGate(RULES)
    assert gate.check("Shell", {"command": "rm -rf /"}).decision == "deny"
    assert gate.check("Shell", {"command": "pytest -q"}).decision == "allow"
    assert gate.check("Shell", {"command": "git push origin main"}).decision == "ask"
    assert gate.check("Write", {"path": ".env.local"}).decision == "deny"
    assert gate.check("Task", {}).decision == "ask"

    non_interactive = gate.decide_and_execute(
        "Shell", {"command": "git push"}, interactive=False
    )
    assert non_interactive.startswith("DENIED")
    print("permission gate checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="run offline assertions")
    args = parser.parse_args()
    check() if args.check else demo()
