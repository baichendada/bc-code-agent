"""Permission 管道（Step 15）：声明式 允许/询问/拒绝，插在工具执行与 Hook 之前。

- permissions.json 声明规则；优先级固定 deny > ask > allow（档位优先，与规则顺序无关）
- 未命中规则时按 default 兜底（默认 ask）
- YOLO=1：ask 自动放行，deny 永远生效（安全底线）
- 规则语法：`Tool` 精确、`Tool(key=glob)` 参数模式、`Tool(pattern)` 任意参数、`A|B` 备选、`mcp__*` 通配
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 与 hooks.json 的同级位置：项目根
DEFAULT_CONFIG_PATH = "permissions.json"

DEFAULT_RULES: list[dict[str, str]] = [
    # 只读/内部工具：直接放行
    {"match": "Read|Grep|Glob|LoadSkill|WebSearch", "decision": "allow"},
    {"match": "TodoRead|TodoWrite", "decision": "allow"},
    {"match": "ListTeammates|ReadInbox|SendMessage|Broadcast", "decision": "allow"},
    {"match": "Spawn|DisbandTeam|Task", "decision": "allow"},
    # 安全底线：永远拒绝（优先级高于一切）
    {"match": "Shell(rm -rf*)", "decision": "deny"},
    {"match": "Write(.env*)", "decision": "deny"},
    # 高敏操作：需要确认
    {"match": "Shell(git push*|git commit*)", "decision": "ask"},
    # 其余 shell 放行（具体危险命令由 deny 档/Hook 再把关）
    {"match": "Shell(*)", "decision": "allow"},
    # MCP 默认放行（规则可收紧为 ask）
    {"match": "mcp__*", "decision": "allow"},
]

DECISIONS = ("deny", "ask", "allow")


class PermissionError(Exception):
    """permissions 配置不可安全使用。"""


@dataclass(frozen=True)
class PermissionVerdict:
    decision: str  # allow | ask | deny
    rule: str = ""
    reason: str = ""


def _match_one(tool_name: str, pattern: str) -> bool:
    """工具名匹配：fnmatch（支持 * ?），'|'' 在调用方按备选拆开。"""
    return fnmatch.fnmatchcase(tool_name, pattern)


def _match_tool_pattern(pattern_set: str, tool_name: str) -> bool:
    for pat in pattern_set.split("|"):
        pat = pat.strip()
        if not pat:
            continue
        if _match_one(tool_name, pat):
            return True
    return False


def _match_args(rule_pattern: str, tool_name: str, tool_input: dict[str, Any]) -> bool:
    """规则 'Tool(...)' 内参数模式的匹配。

    支持两种写法（可逗号组合，全部满足才算命中）：
    - key=glob：只匹配该参数的值（如 Shell(command=git push*)）
    - glob：匹配所有参数的拼接（如 Shell(rm -rf*)）
    """
    inner = rule_pattern[len(tool_name):].strip()
    if inner.startswith("("):
        inner = inner[1:]
    if inner.endswith(")"):
        inner = inner[:-1]
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    if not parts:
        return True

    strings = {str(k): str(v) for k, v in tool_input.items()}

    def _one(part: str) -> bool:
        if "=" in part:
            key, _, pattern = part.partition("=")
            key = key.strip()
            pattern = pattern.strip()
            value = strings.get(key)
            return value is not None and fnmatch.fnmatchcase(value, pattern)
        # 裸 pattern：匹配任一参数值（如 Shell(rm -rf*) 匹配 command 值）
        return any(fnmatch.fnmatchcase(v, part) for v in strings.values())

    for part in parts:
        # 逗号 = 多个条件（AND）；条件内 | = 备选（OR）
        alternatives = [p.strip() for p in part.split("|") if p.strip()]
        if not alternatives or not any(_one(alt) for alt in alternatives):
            return False
    return True


def rule_matches(rule_match: str, tool_name: str, tool_input: dict[str, Any]) -> bool:
    """一条规则的 match 串是否能命中该工具调用。"""
    rule_match = rule_match.strip()
    if not rule_match:
        return False
    open_idx = rule_match.find("(")
    if open_idx == -1:
        # 纯工具名（或工具名集合），如 Read|Grep|Glob、mcp__*
        return _match_tool_pattern(rule_match, tool_name)
    tool_part = rule_match[:open_idx]
    if not _match_tool_pattern(tool_part, tool_name):
        return False
    return _match_args(rule_match, tool_part.split("|")[0].strip(), tool_input)


@dataclass
class PermissionsConfig:
    """permissions.json 的加载与决策。"""

    path: Path | None = None
    default: str = "ask"
    mode: str = "interactive"  # interactive | yolo（env YOLO=1 可覆盖）
    rules: list[dict[str, str]] = field(default_factory=lambda: list(DEFAULT_RULES))

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        project_root: Path | None = None,
    ) -> "PermissionsConfig":
        """加载配置文件；缺失/坏 JSON 时回退内置默认（宁严勿松：default 仍 ask）。"""
        config_path = path or (
            Path(project_root) / DEFAULT_CONFIG_PATH if project_root else Path(DEFAULT_CONFIG_PATH)
        )
        if not config_path.is_file():
            return cls(path=config_path)
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[permissions] 无法解析 {config_path}（{exc}），使用内置默认规则")
            return cls(path=config_path)
        if not isinstance(raw, dict):
            print(f"[permissions] {config_path} 不是对象，使用内置默认规则")
            return cls(path=config_path)

        default = str(raw.get("default") or "ask").strip().lower()
        if default not in DECISIONS:
            default = "ask"
        mode = str(raw.get("mode") or "interactive").strip().lower()
        if mode not in ("interactive", "yolo"):
            mode = "interactive"

        rules: list[dict[str, str]] = []
        for item in raw.get("rules") or []:
            if not isinstance(item, dict):
                continue
            match = str(item.get("match") or "").strip()
            decision = str(item.get("decision") or "").strip().lower()
            if not match or decision not in DECISIONS:
                continue
            rules.append({"match": match, "decision": decision})

        print(f"[permissions] loaded {len(rules)} rule(s) from {config_path}")
        return cls(path=config_path, default=default, mode=mode, rules=rules)

    def effective_mode(self) -> str:
        """YOLO=1 环境变量优先于配置文件的 mode。"""
        yolo = os.getenv("YOLO", "").strip().lower()
        if yolo in ("1", "true", "yes", "yolo"):
            return "yolo"
        return self.mode

    def check(self, tool_name: str | None, tool_input: dict[str, Any] | None) -> PermissionVerdict:
        """决策管道：deny → ask → allow → default。档位优先，同档内按规则顺序。"""
        tool_name = str(tool_name or "")
        tool_input = dict(tool_input or {})
        for decision in DECISIONS:
            for rule in self.rules:
                if rule.get("decision") != decision:
                    continue
                if rule_matches(rule.get("match", ""), tool_name, tool_input):
                    reason = self._describe(decision, rule["match"], tool_name)
                    return PermissionVerdict(decision, rule=rule["match"], reason=reason)
        reason = (
            f"工具 {tool_name} 未命中任何规则，按 default={self.default} 处理"
        )
        return PermissionVerdict(self.default, rule="(default)", reason=reason)

    @staticmethod
    def _describe(decision: str, rule: str, tool_name: str) -> str:
        label = {"deny": "已拒绝", "ask": "需要确认", "allow": "已放行"}.get(
            decision, decision
        )
        return f"工具调用 {tool_name} 匹配规则「{rule}」：{label}"

    def describe(self) -> str:
        lines = [
            f"Permission rules (mode={self.effective_mode()}, default={self.default}):",
        ]
        for rule in self.rules:
            lines.append(f"  [{rule['decision']}] {rule['match']}")
        return "\n".join(lines)


def load_default(project_root: Path) -> PermissionsConfig:
    return PermissionsConfig.load(project_root=project_root)
