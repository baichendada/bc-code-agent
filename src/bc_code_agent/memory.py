"""三层记忆 + 压缩（history>=20 触发，保留最近 6 条原始消息）。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

COMPACT_THRESHOLD = 20
KEEP_RECENT = 6
MID_KEEP_SEGMENTS = 5

COMPACTION_SYSTEM = """
你是会话压缩器。根据输入的近期对话、已有中期摘要、长期记忆、用户偏好，输出且仅输出一个 JSON 对象（不要 Markdown 代码块以外的说明文字）。

JSON 必须符合以下字段：
{
  "mid_summary": {
    "title": "短标题≤30字",
    "summary": "120～300字，本段在做什么、进展到哪",
    "key_facts": ["本段仍有用的事实"],
    "decisions": ["已做出的决定"]
  },
  "long_term": {
    "goals": ["核心目标；过期不要保留"],
    "constraints": ["硬约束"],
    "standing_facts": ["跨多段仍成立的事实"]
  },
  "open_todos": ["未完成事项"],
  "user_preferences_delta": [
    {"preference": "偏好", "evidence": "依据的用户原话/行为"}
  ],
  "discarded_notes": "本段丢掉了哪些类型的信息"
}

规则：
1. 保留：目标、约束、关键决策、未完成任务、会影响后续行为的事实。
2. 丢弃：寒暄、重复、完整工具大段输出、已过时的尝试细节。
3. mid_summary 只描述「这一段」；long_term 是合并后的全局仍成立内容（可修订旧长期）。
4. user_preferences_delta 必须有 evidence；不确定就输出 []。
5. 不要编造对话里未出现的信息。
6. 中文撰写；字段名保持英文。
""".strip()


@dataclass
class MidSummary:
    title: str
    summary: str
    key_facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class LongTerm:
    goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    standing_facts: list[str] = field(default_factory=list)


@dataclass
class Preference:
    preference: str
    evidence: str
    created_at: str = ""


class SessionMemory:
    def __init__(self, root: Path, session_id: str | None = None) -> None:
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = Path(root) / "sessions" / self.session_id
        self.dir.mkdir(parents=True, exist_ok=True)

        self.transcript_path = self.dir / "transcript.jsonl"
        self.mid_path = self.dir / "mid.jsonl"
        self.long_path = self.dir / "long_term.json"
        self.pref_path = self.dir / "preferences.json"
        self.metrics_path = self.dir / "metrics.jsonl"

        self.mid: list[MidSummary] = []
        self.long_term = LongTerm()
        self.preferences: list[Preference] = []
        self.open_todos: list[str] = []
        self._load_state()

        print(f"[Memory] session={self.session_id} dir={self.dir}")

    def _load_state(self) -> None:
        if self.long_path.is_file():
            data = json.loads(self.long_path.read_text(encoding="utf-8"))
            self.long_term = LongTerm(**data)
        if self.pref_path.is_file():
            raw = json.loads(self.pref_path.read_text(encoding="utf-8"))
            self.preferences = [Preference(**p) for p in raw]
        if self.mid_path.is_file():
            for line in self.mid_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                self.mid.append(MidSummary(**json.loads(line)))

    def append_raw(self, message: dict[str, Any]) -> None:
        """原始层：每条消息追加落盘。"""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "message": _jsonable_message(message),
        }
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def record_usage(
        self,
        *,
        kind: str,
        input_tokens: int | None,
        output_tokens: int | None,
        model: str,
    ) -> None:
        """Token 计量：每次 API 调用后写入。"""
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with self.metrics_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"[Token] {kind}: in={input_tokens} out={output_tokens}"
        )

    def build_prompt_section(self) -> str:
        """拼进 system：长期 + 近期中期 + 偏好 + todos。"""
        parts: list[str] = ["# Session Memory"]

        parts.append("## Long-term memory")
        parts.append(_bullets("Goals", self.long_term.goals))
        parts.append(_bullets("Constraints", self.long_term.constraints))
        parts.append(_bullets("Standing facts", self.long_term.standing_facts))

        parts.append("## Mid-term memory（最近片段）")
        recent_mid = self.mid[-MID_KEEP_SEGMENTS:]
        if not recent_mid:
            parts.append("(empty)")
        else:
            for i, m in enumerate(recent_mid, 1):
                parts.append(f"### {i}. {m.title}")
                parts.append(m.summary)
                if m.key_facts:
                    parts.append("Key facts: " + "; ".join(m.key_facts))
                if m.decisions:
                    parts.append("Decisions: " + "; ".join(m.decisions))

        parts.append("## User preferences")
        if not self.preferences:
            parts.append("(empty)")
        else:
            for p in self.preferences[-20:]:
                parts.append(f"- {p.preference}（依据：{p.evidence}）")

        parts.append("## Open todos")
        parts.append(_bullets(None, self.open_todos) if self.open_todos else "(empty)")

        return "\n".join(parts).rstrip() + "\n"

    def maybe_compact(self, history: list[dict[str, Any]], client: Any, model: str) -> list[dict[str, Any]]:
        """history 条数 >= 20 则压缩；成功后返回裁剪后的 history（保留最近 6 条）。"""
        if len(history) < COMPACT_THRESHOLD:
            return history

        print(f"[Memory] compact trigger: history={len(history)} >= {COMPACT_THRESHOLD}")
        try:
            result = self._run_compaction(history, client, model)
        except Exception as exc:  # noqa: BLE001
            print(f"[Memory] compact failed, keep full history: {exc}")
            return history

        self._apply_compaction(result)
        trimmed = _trim_working_history(history, KEEP_RECENT)
        print(
            f"[Memory] compact ok: mid+1 long updated; "
            f"working history {len(history)} -> {len(trimmed)}"
        )
        return trimmed

    def _run_compaction(
        self, history: list[dict[str, Any]], client: Any, model: str
    ) -> dict[str, Any]:
        payload = {
            "recent_dialogue": [_jsonable_message(m) for m in history],
            "existing_mid": [asdict(m) for m in self.mid[-MID_KEEP_SEGMENTS:]],
            "existing_long_term": asdict(self.long_term),
            "existing_preferences": [asdict(p) for p in self.preferences],
            "existing_open_todos": self.open_todos,
        }
        user_content = (
            "请压缩以下会话状态，只输出 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=COMPACTION_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        usage = getattr(message, "usage", None)
        self.record_usage(
            kind="compact",
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            model=model,
        )

        text = next((b.text for b in message.content if b.type == "text"), "")
        return _parse_json_object(text)

    def _apply_compaction(self, result: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        mid_raw = result.get("mid_summary") or {}
        mid = MidSummary(
            title=str(mid_raw.get("title") or "untitled"),
            summary=str(mid_raw.get("summary") or ""),
            key_facts=list(mid_raw.get("key_facts") or []),
            decisions=list(mid_raw.get("decisions") or []),
            created_at=now,
        )
        self.mid.append(mid)
        with self.mid_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(mid), ensure_ascii=False) + "\n")

        lt = result.get("long_term") or {}
        self.long_term = LongTerm(
            goals=list(lt.get("goals") or []),
            constraints=list(lt.get("constraints") or []),
            standing_facts=list(lt.get("standing_facts") or []),
        )
        self.long_path.write_text(
            json.dumps(asdict(self.long_term), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self.open_todos = [str(x) for x in (result.get("open_todos") or [])]

        for item in result.get("user_preferences_delta") or []:
            pref = str(item.get("preference") or "").strip()
            evidence = str(item.get("evidence") or "").strip()
            if not pref or not evidence:
                continue
            if any(p.preference == pref for p in self.preferences):
                continue
            self.preferences.append(
                Preference(preference=pref, evidence=evidence, created_at=now)
            )
        self.pref_path.write_text(
            json.dumps([asdict(p) for p in self.preferences], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

        discarded = result.get("discarded_notes")
        if discarded:
            print(f"[Memory] discarded_notes: {discarded}")


def _bullets(title: str | None, items: list[str]) -> str:
    if not items:
        return f"{title}: (empty)" if title else "(empty)"
    body = "\n".join(f"- {x}" for x in items)
    return f"{title}:\n{body}" if title else body


def _jsonable_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": message.get("role"),
        "content": _jsonable_content(message.get("content")),
    }


def _jsonable_content(content: Any) -> Any:
    if content is None or isinstance(content, (str, int, float, bool)):
        return content
    if isinstance(content, list):
        return [_jsonable_content(x) for x in content]
    if isinstance(content, dict):
        return {k: _jsonable_content(v) for k, v in content.items()}
    # anthropic SDK blocks
    if hasattr(content, "model_dump"):
        return content.model_dump()
    if hasattr(content, "type"):
        data: dict[str, Any] = {"type": getattr(content, "type", None)}
        for key in ("text", "id", "name", "input", "tool_use_id"):
            if hasattr(content, key):
                data[key] = _jsonable_content(getattr(content, key))
        return data
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("compaction result is not a JSON object")
    return data


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    first = content[0]
    if isinstance(first, dict):
        return first.get("type") == "tool_result"
    return getattr(first, "type", None) == "tool_result"


def _trim_working_history(
    history: list[dict[str, Any]], keep: int
) -> list[dict[str, Any]]:
    """保留最近 keep 条，并保证不以 assistant / 裸 tool_result 开头。"""
    trimmed = history[-keep:]
    while trimmed and (
        trimmed[0].get("role") == "assistant" or _is_tool_result_message(trimmed[0])
    ):
        trimmed = trimmed[1:]
    if not trimmed and history:
        # 至少保住最后一条用户文本，避免空 messages
        for msg in reversed(history):
            if msg.get("role") == "user" and not _is_tool_result_message(msg):
                return [msg]
        return history[-1:]
    return trimmed
