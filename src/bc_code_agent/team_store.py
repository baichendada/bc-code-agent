"""AgentTeam 落盘：单 session 唯一队伍、队友配置、mailbox。"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

LEAD_ID = "lead"

# resolve_recipient 的保留别名：Spawn 不得占用，否则队友永远无法被寻址
RESERVED_IDS = frozenset(
    {LEAD_ID, "main", "self", "me", "主人", "猫娘", "自己", "我"}
)

# 队友可选工具（硬禁 Shell / Todo / Spawn / Task / Disband）
TEAMMATE_OPTIONAL_TOOLS = frozenset(
    {"Read", "Write", "Grep", "Glob", "WebSearch", "LoadSkill"}
)
TEAMMATE_MESSAGE_TOOLS = frozenset(
    {"SendMessage", "Broadcast", "ReadInbox", "ListTeammates"}
)
TEAMMATE_FORBIDDEN_TOOLS = frozenset(
    {"Shell", "TodoWrite", "TodoRead", "Spawn", "DisbandTeam", "Task"}
)

STATUSES = ("idle", "busy", "waiting", "done", "failed", "stopped")
TEAM_STATUSES = ("active", "disbanded")

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_\u4e00-\u9fff\-]+")


def slugify(name: str) -> str:
    raw = (name or "").strip() or "teammate"
    s = _SLUG_RE.sub("-", raw).strip("-").lower()
    return s[:40] or "teammate"


@dataclass
class TeamMessage:
    id: str
    from_id: str
    to_id: str
    content: str
    type: str = "direct"  # direct | broadcast
    created_at: str = ""
    read: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from": self.from_id,
            "to": self.to_id,
            "content": self.content,
            "type": self.type,
            "created_at": self.created_at,
            "read": self.read,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TeamMessage:
        return cls(
            id=str(raw.get("id") or ""),
            from_id=str(raw.get("from") or ""),
            to_id=str(raw.get("to") or ""),
            content=str(raw.get("content") or ""),
            type=str(raw.get("type") or "direct"),
            created_at=str(raw.get("created_at") or ""),
            read=bool(raw.get("read", False)),
        )


@dataclass
class TeammateConfig:
    id: str
    name: str
    role: str
    system: str
    tools: list[str]
    status: str = "idle"
    max_turns: int = 15
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TeammateConfig:
        return cls(
            id=str(raw.get("id") or ""),
            name=str(raw.get("name") or ""),
            role=str(raw.get("role") or ""),
            system=str(raw.get("system") or ""),
            tools=list(raw.get("tools") or []),
            status=str(raw.get("status") or "idle"),
            max_turns=int(raw.get("max_turns") or 15),
            created_at=str(raw.get("created_at") or ""),
        )


@dataclass
class TeamState:
    team_id: str
    status: str = "active"
    goal: str = ""
    created_at: str = ""
    members: list[TeammateConfig] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "status": self.status,
            "goal": self.goal,
            "created_at": self.created_at,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TeamState:
        return cls(
            team_id=str(raw.get("team_id") or ""),
            status=str(raw.get("status") or "active"),
            goal=str(raw.get("goal") or ""),
            created_at=str(raw.get("created_at") or ""),
            members=[TeammateConfig.from_dict(m) for m in (raw.get("members") or [])],
        )


def validate_teammate_tools(tools: list[str]) -> tuple[list[str], str | None]:
    """返回清洗后的 tools；出错返回 ([], error)。消息工具自动补齐。"""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tools:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        if name in TEAMMATE_FORBIDDEN_TOOLS:
            return [], f"Tool not allowed for teammates: {name}"
        if name in TEAMMATE_MESSAGE_TOOLS:
            cleaned.append(name)
            seen.add(name)
            continue
        if name not in TEAMMATE_OPTIONAL_TOOLS:
            return [], (
                f"Unknown or forbidden tool for teammates: {name}. "
                f"Optional: {', '.join(sorted(TEAMMATE_OPTIONAL_TOOLS))}. "
                f"Message tools auto-included."
            )
        cleaned.append(name)
        seen.add(name)

    for msg_tool in sorted(TEAMMATE_MESSAGE_TOOLS):
        if msg_tool not in seen:
            cleaned.append(msg_tool)
            seen.add(msg_tool)
    return cleaned, None


class TeamStore:
    """线程安全的 team / inbox 存取。"""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir)
        self.team_dir = self.session_dir / "team"
        self.profiles_dir = self.team_dir / "profiles"
        self.inboxes_dir = self.team_dir / "inboxes"
        self.team_path = self.team_dir / "team.json"
        self._lock = threading.RLock()
        self._msg_seq = 0
        self.state: TeamState | None = None
        self._load()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _ensure_dirs(self) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.inboxes_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        if not self.team_path.is_file():
            self.state = None
            return
        try:
            raw = json.loads(self.team_path.read_text(encoding="utf-8"))
            self.state = TeamState.from_dict(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self.state = None

    def _save(self) -> None:
        if self.state is None:
            return
        self._ensure_dirs()
        self.team_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for member in self.state.members:
            path = self.profiles_dir / f"{member.id}.json"
            path.write_text(
                json.dumps(member.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def has_active_team(self) -> bool:
        with self._lock:
            return self.state is not None and self.state.status == "active"

    def ensure_active_team(self, goal: str = "") -> TeamState:
        with self._lock:
            if self.state is not None and self.state.status == "active":
                if goal and not self.state.goal:
                    self.state.goal = goal
                    self._save()
                return self.state
            self._ensure_dirs()
            team_id = datetime.now().strftime("team-%Y%m%d-%H%M%S")
            self.state = TeamState(
                team_id=team_id,
                status="active",
                goal=goal.strip(),
                created_at=self._now(),
                members=[],
            )
            # lead inbox
            self._inbox_path(LEAD_ID).touch(exist_ok=True)
            self._save()
            return self.state

    def get_member(self, mate_id: str) -> TeammateConfig | None:
        with self._lock:
            if self.state is None:
                return None
            for m in self.state.members:
                if m.id == mate_id:
                    return m
            return None

    def list_members(self) -> list[TeammateConfig]:
        with self._lock:
            if self.state is None or self.state.status != "active":
                return []
            return list(self.state.members)

    def add_member(self, config: TeammateConfig) -> str | None:
        """成功返回 None，失败返回错误信息。"""
        with self._lock:
            if self.state is None or self.state.status != "active":
                return "No active team. Spawn will create one."
            if any(m.id == config.id for m in self.state.members):
                return f"Teammate id already exists: {config.id}"
            if any(m.name == config.name for m in self.state.members):
                return f"Teammate name already exists: {config.name}"
            self.state.members.append(config)
            self._inbox_path(config.id).touch(exist_ok=True)
            self._save()
            return None

    def set_member_status(self, mate_id: str, status: str) -> None:
        with self._lock:
            if self.state is None:
                return
            for m in self.state.members:
                if m.id == mate_id:
                    m.status = status
                    self._save()
                    return

    def mark_disbanded(self) -> None:
        with self._lock:
            if self.state is None:
                return
            self.state.status = "disbanded"
            for m in self.state.members:
                m.status = "stopped"
            self._save()

    def clear_after_disband(self) -> None:
        """解散后允许同 session 再建新队：重置内存态（磁盘保留归档）。"""
        with self._lock:
            self.state = None

    def _inbox_path(self, owner_id: str) -> Path:
        self._ensure_dirs()
        return self.inboxes_dir / f"{owner_id}.jsonl"

    def _next_msg_id(self) -> str:
        self._msg_seq += 1
        # 微秒时间戳：跨进程重启也几乎不冲突
        return f"msg-{self._msg_seq}-{datetime.now().strftime('%H%M%S%f')}"

    def append_message(
        self,
        *,
        from_id: str,
        to_id: str,
        content: str,
        msg_type: str = "direct",
    ) -> TeamMessage:
        with self._lock:
            msg = TeamMessage(
                id=self._next_msg_id(),
                from_id=from_id,
                to_id=to_id,
                content=content.strip(),
                type=msg_type,
                created_at=self._now(),
                read=False,
            )
            path = self._inbox_path(to_id)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
            return msg

    def _read_inbox_raw(self, owner_id: str) -> list[TeamMessage]:
        path = self._inbox_path(owner_id)
        if not path.is_file():
            return []
        messages: list[TeamMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(TeamMessage.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return messages

    def _rewrite_inbox(self, owner_id: str, messages: list[TeamMessage]) -> None:
        path = self._inbox_path(owner_id)
        with path.open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

    def take_unread(self, owner_id: str) -> list[TeamMessage]:
        """取出未读并标记为已读。"""
        with self._lock:
            messages = self._read_inbox_raw(owner_id)
            unread = [m for m in messages if not m.read]
            if not unread:
                return []
            for m in messages:
                if not m.read:
                    m.read = True
            self._rewrite_inbox(owner_id, messages)
            return unread

    def peek_inbox(self, owner_id: str, unread_only: bool = False) -> list[TeamMessage]:
        with self._lock:
            messages = self._read_inbox_raw(owner_id)
            if unread_only:
                return [m for m in messages if not m.read]
            return messages

    def unread_count(self, owner_id: str) -> int:
        return len(self.peek_inbox(owner_id, unread_only=True))

    def resolve_recipient(
        self, to: str, *, self_id: str | None = None
    ) -> str | None:
        """把名字或 id 解析成 inbox owner id；lead / self 可用别名。"""
        to = (to or "").strip()
        if not to:
            return None
        lower = to.lower()
        if lower in ("lead", "main", "主人", "猫娘"):
            return LEAD_ID
        if lower in ("self", "me", "自己", "我"):
            return self_id
        with self._lock:
            if self.state is None:
                return None
            for m in self.state.members:
                if m.id == to or m.name == to:
                    return m.id
        return None

    def format_members_report(self) -> str:
        with self._lock:
            if self.state is None or self.state.status != "active":
                return "No active team."
            lines = [
                f"Team `{self.state.team_id}` status={self.state.status}",
                f"Goal: {self.state.goal or '(none)'}",
                f"Members ({len(self.state.members)}):",
            ]
            for m in self.state.members:
                unread = self.unread_count(m.id)
                tools = ", ".join(
                    t for t in m.tools if t not in TEAMMATE_MESSAGE_TOOLS
                ) or "(message tools only)"
                lines.append(
                    f"- {m.id} | name={m.name} | role={m.role} | "
                    f"status={m.status} | unread={unread} | tools=[{tools}]"
                )
            lead_unread = self.unread_count(LEAD_ID)
            lines.append(f"Lead inbox unread: {lead_unread}")
            return "\n".join(lines)
