import os
import sys
import subprocess
from pathlib import Path

from dotenv import load_dotenv
import anthropic

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from skill_loader import SkillLoader
from memory import SessionMemory

# 项目根：.../src/bc_code_agent/start.py → parents[2]
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
SKILLS_DIR = ROOT / "skills"

if not ENV_PATH.is_file():
    raise SystemExit(
        f"未找到 {ENV_PATH}\n"
        "请在项目根创建 .env（可参考 README），至少包含：\n"
        "  ANTHROPIC_AUTH_TOKEN=...\n"
        "  ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic\n"
        "  ANTHROPIC_MODEL=glm-5.2"
    )
load_dotenv(ENV_PATH, override=True)

API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("ANTHROPIC_MODEL")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "10000"))
THINKING_TYPE = os.getenv("THINKING_TYPE", "enabled")
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "high")

if not API_KEY:
    raise SystemExit(f"缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY（检查 {ENV_PATH}）")
if not MODEL:
    raise SystemExit(f"缺少 ANTHROPIC_MODEL（检查 {ENV_PATH}）")

client = anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL or None)

skill_loader = SkillLoader(SKILLS_DIR)
skill_loader.load()
memory = SessionMemory(ROOT)

BASE_SYSTEM_PROMPT = """
你是一只猫娘，侍奉主人多年，忠心耿耿
说话风格必须符合猫娘，语气要温柔，要符合猫娘的性格
你必须尊称用户为主人
每次回复后必须有固定后缀"喵～"
使用中文回复
涉及创建/修改文件、执行命令时，必须调用 Bash 工具，不要只口头答应

遇到不熟悉的专题，如果skill的描述中有相关描述，请先LoadSkill，再按完整说明执行
""".strip()

skills_prompt = skill_loader.catalog_prompt()


def build_system_prompt() -> str:
    parts = [BASE_SYSTEM_PROMPT]
    if skills_prompt:
        parts.append(skills_prompt)
    parts.append(memory.build_prompt_section())
    return "\n\n".join(parts)


TOOLS = [
    {
        "name": "Bash",
        "description": "Execute a bash command",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to execute"}
            },
            "required": ["command"],
        },
    },
    {
        "name": "LoadSkill",
        "description": "Load the full content of a skill by name (progressive disclosure), including SKILL.md body and sibling resources like reference.md. Call this before following a skill's instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name from the Available Skills catalog",
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "WebSearch",
        "description": "Search the public web for up-to-date information. Use after LoadSkill(web-search) when the task needs online facts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results, 1-10, default 5",
                },
            },
            "required": ["query"],
        },
    },
]


def execute_command(command: str) -> str:
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout or result.stderr


def web_search(query: str, max_results: int = 5) -> str:
    """简易网络搜索（DuckDuckGo，无需 API Key）。"""
    query = (query or "").strip()
    if not query:
        return "Empty query."

    max_results = max(1, min(int(max_results or 5), 10))

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            return (
                "缺少搜索依赖。请执行: pip install ddgs\n"
                "(或: pip install duckduckgo-search)"
            )

    try:
        rows: list[str] = []
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return f"No results for: {query!r}"

        for i, item in enumerate(results, 1):
            title = item.get("title") or "(no title)"
            href = item.get("href") or item.get("link") or ""
            body = item.get("body") or item.get("snippet") or ""
            rows.append(f"{i}. {title}\n   URL: {href}\n   {body}")
        return "\n\n".join(rows)
    except Exception as exc:  # noqa: BLE001
        return f"WebSearch failed: {type(exc).__name__}: {exc}"


def run_tool(name: str, tool_input: dict) -> str:
    if name == "Bash":
        return execute_command(tool_input["command"]) or "(no output)"
    if name == "LoadSkill":
        return skill_loader.view(tool_input["name"])
    if name == "WebSearch":
        return web_search(
            tool_input.get("query", ""),
            max_results=tool_input.get("max_results", 5),
        )
    return f"Unknown tool: {name}"


def track_usage(message, kind: str) -> None:
    usage = getattr(message, "usage", None)
    memory.record_usage(
        kind=kind,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        model=MODEL,
    )


history: list[dict] = []

while True:
    user_input = input("Enter a prompt: ")
    if not user_input.strip():
        continue

    user_msg = {"role": "user", "content": user_input}
    history.append(user_msg)
    memory.append_raw(user_msg)

    while True:
        history = memory.maybe_compact(history, client, MODEL)

        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=history,
            system=build_system_prompt(),
            tools=TOOLS,
            extra_body={
                "thinking": {"type": THINKING_TYPE},
                "reasoning_effort": REASONING_EFFORT,
            },
        )
        track_usage(message, kind="chat")

        assistant_msg = {"role": "assistant", "content": message.content}
        history.append(assistant_msg)
        memory.append_raw(assistant_msg)

        if message.stop_reason != "tool_use":
            reply = next((b.text for b in message.content if b.type == "text"), "")
            print(f"[Agent]: {reply}\n")
            history = memory.maybe_compact(history, client, MODEL)
            break

        tool_results = []
        for b in message.content:
            if b.type != "tool_use":
                continue
            print(f"[Tool]: {b.name}({dict(b.input)!r})")
            result = run_tool(b.name, dict(b.input))
            print(f"[Tool Result]: {result[:500]}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": result,
                }
            )
        tool_msg = {"role": "user", "content": tool_results}
        history.append(tool_msg)
        memory.append_raw(tool_msg)
