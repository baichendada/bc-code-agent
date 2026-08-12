"""AgentTeam 运行时：Spawn / 消息 / 队友后台 poll loop。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from file_tools import FILE_TOOL_SCHEMAS
from subagents import LOAD_SKILL_SCHEMA, WEB_SEARCH_SCHEMA
from team_store import (
    LEAD_ID,
    TEAMMATE_MESSAGE_TOOLS,
    TEAMMATE_OPTIONAL_TOOLS,
    TeamMessage,
    TeamStore,
    TeammateConfig,
    slugify,
    validate_teammate_tools,
)
from tool_executor import ToolExecutor

POLL_INTERVAL_SEC = 1.0
DEFAULT_MAX_TURNS = 15

PROFILE_TEMPLATE_HINT = (
    "Fill a custom teammate profile (do NOT reuse Task subagent_type names as ids). "
    "Recommended templates — pick tools from allowed set, customize system text:\n"
    "- researcher: tools=[WebSearch,LoadSkill,Read], system=查外网事实并附来源\n"
    "- writer: tools=[Read,Write,Grep,Glob], system=按指示写/改文件，回报改动\n"
    "- reviewer: tools=[Read,Grep,Glob], system=只读审查，输出 PASS/NEEDS_FIX/BLOCKED\n"
    "- explorer: tools=[Read,Grep,Glob], system=只读摸清代码结构\n"
    f"Allowed optional tools: {', '.join(sorted(TEAMMATE_OPTIONAL_TOOLS))}. "
    "Shell/Todo/Spawn/Task/DisbandTeam are forbidden. "
    "Message tools (SendMessage/Broadcast/ReadInbox/ListTeammates) are always added."
)

TEAM_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "Spawn",
        "description": (
            "Spawn a long-lived teammate on the session's single AgentTeam "
            "(creates the team implicitly if none). "
            "Use for multi-turn collaboration with messaging — not for one-shot Task. "
            + PROFILE_TEMPLATE_HINT
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short display name, e.g. 调研官",
                },
                "role": {
                    "type": "string",
                    "description": "One-line duty description",
                },
                "system": {
                    "type": "string",
                    "description": "Full system prompt / behavior constraints for this teammate",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional tools subset. Message tools auto-included. "
                        "No Shell."
                    ),
                },
                "brief": {
                    "type": "string",
                    "description": "Optional first message from lead into their inbox",
                },
                "goal": {
                    "type": "string",
                    "description": "Optional team goal (set when creating the team)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max LLM turns per wake (default 15)",
                },
            },
            "required": ["name", "role", "system", "tools"],
        },
    },
    {
        "name": "DisbandTeam",
        "description": (
            "Stop all teammate workers and disband the only team in this session. "
            "Required before starting a completely new team."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ListTeammates",
        "description": "List teammates, statuses, unread counts, and team goal.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "SendMessage",
        "description": (
            "Send a direct message to lead or a teammate (by id or name). "
            "Recipient worker wakes on next poll when inbox has mail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient: lead | teammate id | teammate name",
                },
                "content": {
                    "type": "string",
                    "description": "Message body",
                },
            },
            "required": ["to", "content"],
        },
    },
    {
        "name": "Broadcast",
        "description": (
            "Broadcast a message to all teammates' inboxes (not to lead). "
            "Does not auto-run a full-team LLM storm beyond each worker's poll."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Broadcast body"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "ReadInbox",
        "description": (
            "Read messages in an inbox. Lead defaults to lead inbox; "
            "teammates may only read their own unless caller is lead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "who": {
                    "type": "string",
                    "description": (
                        "Inbox owner: lead | teammate id/name | self/me/自己 "
                        "(default: caller's own inbox)"
                    ),
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, only unread (default false for peek)",
                },
                "mark_read": {
                    "type": "boolean",
                    "description": "If true, mark unread as read when reading",
                },
            },
        },
    },
]


def _schema_by_name(name: str) -> dict[str, Any] | None:
    if name == "LoadSkill":
        return LOAD_SKILL_SCHEMA
    if name == "WebSearch":
        return WEB_SEARCH_SCHEMA
    for schema in FILE_TOOL_SCHEMAS:
        if schema["name"] == name:
            return schema
    for schema in TEAM_TOOL_SCHEMAS:
        if schema["name"] == name:
            return schema
    return None


def _format_inbox_for_prompt(messages: list[TeamMessage]) -> str:
    parts = ["你收到以下新消息，请处理。需要协作时用 SendMessage；可向 lead 汇报。\n"]
    for msg in messages:
        parts.append(
            f"---\nfrom: {msg.from_id}\nto: {msg.to_id}\ntype: {msg.type}\n"
            f"id: {msg.id}\n\n{msg.content}\n"
        )
    return "\n".join(parts)


class AgentTeamManager:
    def __init__(
        self,
        store: TeamStore,
        *,
        client: Any,
        model: str,
        max_tokens: int,
        thinking_type: str,
        reasoning_effort: str,
        load_skill: Callable[[str], str],
        web_search: Callable[..., str],
        track_usage: Callable[[Any, str], None] | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.thinking_type = thinking_type
        self.reasoning_effort = reasoning_effort
        self.load_skill = load_skill
        self.web_search = web_search
        self.track_usage = track_usage
        self._workers: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ---------- tools API (caller_id = lead | mate id) ----------

    def run_team_tool(
        self, name: str, tool_input: dict, *, caller_id: str = LEAD_ID
    ) -> str | None:
        if name == "Spawn":
            if caller_id != LEAD_ID:
                return "Only lead can Spawn."
            return self.spawn(tool_input)
        if name == "DisbandTeam":
            if caller_id != LEAD_ID:
                return "Only lead can DisbandTeam."
            return self.disband()
        if name == "ListTeammates":
            return self.store.format_members_report()
        if name == "SendMessage":
            return self.send_message(
                caller_id,
                str(tool_input.get("to") or ""),
                str(tool_input.get("content") or ""),
            )
        if name == "Broadcast":
            return self.broadcast(caller_id, str(tool_input.get("content") or ""))
        if name == "ReadInbox":
            return self.read_inbox(
                caller_id,
                who=tool_input.get("who"),
                unread_only=bool(tool_input.get("unread_only", False)),
                mark_read=bool(tool_input.get("mark_read", False)),
            )
        return None

    def spawn(self, tool_input: dict) -> str:
        name = str(tool_input.get("name") or "").strip()
        role = str(tool_input.get("role") or "").strip()
        system = str(tool_input.get("system") or "").strip()
        tools_raw = tool_input.get("tools") or []
        brief = str(tool_input.get("brief") or "").strip()
        goal = str(tool_input.get("goal") or "").strip()
        max_turns = int(tool_input.get("max_turns") or DEFAULT_MAX_TURNS)
        max_turns = max(1, min(max_turns, 40))

        if not name or not role or not system:
            return "Spawn failed: name, role, system are required."
        if not isinstance(tools_raw, list):
            return "Spawn failed: tools must be a list of strings."

        tools, err = validate_teammate_tools([str(t) for t in tools_raw])
        if err:
            return f"Spawn failed: {err}"

        self.store.ensure_active_team(goal=goal)
        mate_id = slugify(name)
        # avoid collision
        base = mate_id
        n = 2
        while self.store.get_member(mate_id) is not None:
            mate_id = f"{base}-{n}"
            n += 1

        base_system = (
            f"你是团队队友「{name}」。\n"
            f"职司：{role}\n"
            "你没有猫娘人设；用简洁中文协作。\n"
            "禁止：Shell、Todo、Spawn、招新队友。\n"
            "可用 SendMessage / Broadcast 与 lead 或其他队友协作；"
            "重要结果请 SendMessage 给 lead。\n"
            "---\n"
            f"{system}"
        )
        config = TeammateConfig(
            id=mate_id,
            name=name,
            role=role,
            system=base_system,
            tools=tools,
            status="idle",
            max_turns=max_turns,
            created_at=self.store._now(),
        )
        add_err = self.store.add_member(config)
        if add_err:
            return f"Spawn failed: {add_err}"

        self._start_worker(config)
        if brief:
            self.store.append_message(
                from_id=LEAD_ID, to_id=mate_id, content=brief, msg_type="direct"
            )

        print(f"[Team] Spawn {mate_id} ({name}) tools={tools}")
        return (
            f"Spawned teammate `{mate_id}` (name={name}, role={role}).\n"
            f"tools={tools}\n"
            f"{'Initial brief queued.' if brief else 'No brief.'}\n"
            f"{self.store.format_members_report()}"
        )

    def disband(self) -> str:
        if not self.store.has_active_team():
            return "No active team to disband."
        report = self.store.format_members_report()
        self._stop_all_workers()
        self.store.mark_disbanded()
        self.store.clear_after_disband()
        print("[Team] disbanded")
        return f"Team disbanded.\nWas:\n{report}"

    def send_message(self, from_id: str, to: str, content: str) -> str:
        content = (content or "").strip()
        if not content:
            return "SendMessage failed: empty content."
        to_id = self.store.resolve_recipient(to, self_id=from_id)
        if to_id is None:
            return f"SendMessage failed: unknown recipient {to!r}."
        if to_id == from_id:
            return "SendMessage failed: cannot message yourself."
        if not self.store.has_active_team() and to_id != LEAD_ID:
            return "SendMessage failed: no active team."
        msg = self.store.append_message(
            from_id=from_id, to_id=to_id, content=content, msg_type="direct"
        )
        print(f"[Team] msg {from_id} → {to_id} ({len(content)} chars)")
        return f"Sent {msg.id}: {from_id} → {to_id}"

    def broadcast(self, from_id: str, content: str) -> str:
        content = (content or "").strip()
        if not content:
            return "Broadcast failed: empty content."
        if not self.store.has_active_team():
            return "Broadcast failed: no active team."
        members = self.store.list_members()
        sent: list[str] = []
        for m in members:
            if m.id == from_id:
                continue
            self.store.append_message(
                from_id=from_id, to_id=m.id, content=content, msg_type="broadcast"
            )
            sent.append(m.id)
        print(f"[Team] broadcast from {from_id} → {sent}")
        return f"Broadcast to {len(sent)} teammates: {', '.join(sent) or '(none)'}"

    def read_inbox(
        self,
        caller_id: str,
        who: str | None = None,
        unread_only: bool = False,
        mark_read: bool = False,
    ) -> str:
        target = caller_id
        if who:
            resolved = self.store.resolve_recipient(str(who), self_id=caller_id)
            if resolved is None:
                return f"ReadInbox failed: unknown who={who!r}"
            if caller_id != LEAD_ID and resolved != caller_id:
                return "ReadInbox failed: teammates may only read their own inbox."
            target = resolved
        elif caller_id == LEAD_ID:
            target = LEAD_ID

        if mark_read:
            messages = self.store.take_unread(target)
            if unread_only:
                if not messages:
                    return f"Inbox `{target}`: no unread."
            elif not messages:
                messages = self.store.peek_inbox(target, unread_only=False)
        else:
            messages = self.store.peek_inbox(target, unread_only=unread_only)

        if not messages:
            return f"Inbox `{target}`: empty."
        lines = [f"Inbox `{target}` ({len(messages)} messages):"]
        for msg in messages[-50:]:
            flag = "unread" if not msg.read else "read"
            preview = msg.content.replace("\n", " ")
            if len(preview) > 200:
                preview = preview[:200] + "..."
            lines.append(
                f"- [{flag}] {msg.id} {msg.from_id}→{msg.to_id} ({msg.type}): {preview}"
            )
        return "\n".join(lines)

    # ---------- workers ----------

    def _start_worker(self, config: TeammateConfig) -> None:
        with self._lock:
            if config.id in self._workers and self._workers[config.id].is_alive():
                return
            stop = threading.Event()
            self._stops[config.id] = stop
            thread = threading.Thread(
                target=self._worker_loop,
                args=(config.id, stop),
                name=f"teammate-{config.id}",
                daemon=True,
            )
            self._workers[config.id] = thread
            thread.start()

    def _stop_all_workers(self) -> None:
        with self._lock:
            stops = list(self._stops.items())
            workers = list(self._workers.items())
        for mate_id, stop in stops:
            stop.set()
        for mate_id, thread in workers:
            thread.join(timeout=5)
            print(f"[Team] worker stopped: {mate_id}")
        with self._lock:
            self._stops.clear()
            self._workers.clear()

    def shutdown(self) -> None:
        if self.store.has_active_team():
            self._stop_all_workers()
            self.store.mark_disbanded()
            self.store.clear_after_disband()

    def _worker_loop(self, mate_id: str, stop: threading.Event) -> None:
        print(f"[Team] worker start: {mate_id}")
        while not stop.is_set():
            unread = self.store.take_unread(mate_id)
            if not unread:
                stop.wait(POLL_INTERVAL_SEC)
                continue
            config = self.store.get_member(mate_id)
            if config is None:
                break
            self.store.set_member_status(mate_id, "busy")
            try:
                self._run_teammate_turn(config, unread)
                self.store.set_member_status(mate_id, "idle")
            except Exception as exc:  # noqa: BLE001
                print(f"[Team] worker error {mate_id}: {type(exc).__name__}: {exc}")
                self.store.set_member_status(mate_id, "failed")
                self.store.append_message(
                    from_id=mate_id,
                    to_id=LEAD_ID,
                    content=f"[worker error] {type(exc).__name__}: {exc}",
                    msg_type="direct",
                )
                self.store.set_member_status(mate_id, "idle")
        print(f"[Team] worker exit: {mate_id}")

    def _tools_for_member(self, config: TeammateConfig) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for name in config.tools:
            schema = _schema_by_name(name)
            if schema is not None:
                schemas.append(schema)
        return schemas

    def _run_teammate_turn(
        self, config: TeammateConfig, unread: list[TeamMessage]
    ) -> None:
        print(f"[Team] wake {config.id}: {len(unread)} message(s)")
        allowed = set(config.tools)
        # strip lead-only just in case
        allowed -= {"Spawn", "DisbandTeam", "Task", "TodoWrite", "TodoRead", "Shell"}
        sent_to_lead = False

        def team_dispatch(name: str, tool_input: dict) -> str:
            nonlocal sent_to_lead
            result = self.run_team_tool(name, tool_input, caller_id=config.id)
            if name == "SendMessage":
                to_id = self.store.resolve_recipient(
                    str(tool_input.get("to") or ""), self_id=config.id
                )
                if to_id == LEAD_ID:
                    sent_to_lead = True
            return result if result is not None else f"Unknown team tool: {name}"

        executor = ToolExecutor(
            allowed=allowed,
            prefix=f"队·{config.id}·",
            load_skill=self.load_skill,
            web_search=self.web_search,
            team_dispatch=team_dispatch,
        )
        tools = self._tools_for_member(config)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _format_inbox_for_prompt(unread)}
        ]
        max_turns = config.max_turns or DEFAULT_MAX_TURNS

        for _ in range(max_turns):
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=messages,
                system=config.system,
                tools=tools,
                extra_body={
                    "thinking": {"type": self.thinking_type},
                    "reasoning_effort": self.reasoning_effort,
                },
            )
            if self.track_usage is not None:
                self.track_usage(message, kind=f"teammate:{config.id}")

            messages.append({"role": "assistant", "content": message.content})

            if message.stop_reason != "tool_use":
                text = next(
                    (b.text for b in message.content if b.type == "text"), ""
                ).strip()
                # 本轮已 SendMessage 给 lead 则不再 auto-report，避免重复刷屏
                if text and not sent_to_lead:
                    self.store.append_message(
                        from_id=config.id,
                        to_id=LEAD_ID,
                        content=text,
                        msg_type="direct",
                    )
                    print(
                        f"[Team] {config.id} → lead auto-report ({len(text)} chars)"
                    )
                elif text and sent_to_lead:
                    print(
                        f"[Team] {config.id} skip auto-report "
                        f"(already messaged lead, {len(text)} chars leftover)"
                    )
                return

            tool_results: list[dict[str, Any]] = []
            for block in message.content:
                if block.type != "tool_use":
                    continue
                result = executor.run(block.name, dict(block.input))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        self.store.append_message(
            from_id=config.id,
            to_id=LEAD_ID,
            content=f"[notice] reached max_turns={max_turns} without finishing.",
            msg_type="direct",
        )
