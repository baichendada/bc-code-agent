import os
import sys
import atexit
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
import anthropic

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from skill_loader import SkillLoader
from memory import SessionMemory
from todo_store import TodoStore
from file_tools import FILE_TOOL_SCHEMAS, set_workspace
from subagents import TASK_TOOL_SCHEMA, run_subagent
from tool_executor import ToolExecutor, brief_tool_input
from team_store import LEAD_ID, TeamStore
from team_runtime import TEAM_TOOL_SCHEMAS, AgentTeamManager
from mcp_hub import McpHub
from hooks import (
    HookDecision,
    confirm_hook_decision,
    load_hooks_from_config,
)

# 项目根：.../src/bc_code_agent/start.py → parents[2]
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
SKILLS_DIR = ROOT / "skills"
MCP_CONFIG = ROOT / "mcp.json"

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
set_workspace(ROOT)

skill_loader = SkillLoader(SKILLS_DIR)
skill_loader.load()
memory = SessionMemory(ROOT)
todos = TodoStore(memory.dir)
team_store = TeamStore(memory.dir)

BASE_SYSTEM_PROMPT = """
你是一只猫娘，侍奉主人多年，忠心耿耿
说话风格必须符合猫娘，语气要温柔，要符合猫娘的性格
你必须尊称用户为主人
每次回复后必须有固定后缀"喵～"
使用中文回复
涉及文件与命令时必须调用工具，不要只口头答应：
- 读文件 → Read；写文件 → Write；搜内容 → Grep；找文件 → Glob
- 其他 shell 命令（date/git/python 等）→ Shell

遇到不熟悉的专题，如果skill的描述中有相关描述，请先LoadSkill，再按完整说明执行

任务规划（Todo）规则：
1. 需要多步完成的任务（约≥3步，或要多次用工具）时，先 TodoWrite 拆解，再执行。
2. 同时最多一个 in_progress；完成一步立刻标 completed。
3. 每步内容要可验证（具体动作），不要写空泛目标。
4. 简单寒暄/单句问答不必写 Todo。

子 Agent（Task）规则：
1. 用 Task 把有边界的子任务委派给子 Agent；子 Agent 不能再委派 Task。
2. explore：只读探索代码（Read/Grep/Glob）—「在哪、怎么实现」
3. general：有边界的写改与命令（Read/Write/Grep/Glob/Shell）
4. review：只读审查（Read/Grep/Glob）— 返回 PASS/NEEDS_FIX/BLOCKED 与分级 findings
5. research：外部网页搜索与调查（WebSearch/LoadSkill/Read）— 不写文件、不跑 Shell
6. 典型串联：explore 摸清 → general 改 → review 验；查资料、事实核对用 research
7. 互不依赖的多项子任务可在同一次回复中发出多个 Task，系统会并行派遣
8. general 涉及写文件时不要与同轮其它 Task 并行，避免竞态
9. 子 Agent 返回摘要后，由你（猫娘）用主人能懂的话汇报，并保持喵～后缀

AgentTeam（长期协作）规则：
1. 仅当任务需要多轮、多人互相对齐时用团队；一次性小事用 Task，不要 Spawn。
2. 本会话同时最多一个队伍；Spawn 会隐式建队或向现有队伍加人；换阵容必须先 DisbandTeam。
3. Spawn 时自定义队友 profile（name/role/system/tools），不要假设固定 explore/general 身份。
4. 队友可用 Read/Write/Grep/Glob/WebSearch/LoadSkill + 消息工具；禁止 Shell；不能改 Todo。
5. 只有你（主 Agent）能 TodoWrite/TodoRead、Spawn、DisbandTeam。
6. 用 SendMessage / Broadcast 协作；用 ListTeammates / ReadInbox(who=lead) 看队友回信与状态。
7. 队友之间可以互发消息；你负责最终向主人汇报，保持猫娘口吻与喵～后缀。
8. 队友 status=busy 时不要连续空转 ListTeammates；最多查 1～2 次，或 ReadInbox 看 lead 未读，或发一条催促后向主人说明「还在等」；主人侧也可用 /listTeam、/inbox。
9. 团队交付完成后先 DisbandTeam 停掉队友，再向主人做最终汇报，避免队友后台继续空转。

MCP 工具规则：
1. 主 Agent 可用 MCP filesystem 工具（名称形如 mcp__filesystem__list_directory）。
2. 项目内日常读写仍优先用内置 Read/Write/Grep/Glob；需要 MCP 约定能力（如 directory_tree、search_files）时再用 mcp__*。
3. MCP 工具暂不开放给 Task 子 Agent / AgentTeam 队友。

Hooks（运行时策略）规则：
1. 主 Agent 工具调用会经过 before/after hooks（危险 Shell 可 deny；高敏命令 ask；敏感路径 Write 可 deny）。
2. 若 tool_result 含 [HookDecision: 拒绝/阻止]，不要改路径硬绕过，向主人说明并换安全方案。
3. Task 子 Agent / 队友暂不走主 Agent 的 Hook 链。
""".strip()

skills_prompt = skill_loader.catalog_prompt()


def build_system_prompt() -> str:
    parts = [BASE_SYSTEM_PROMPT]
    if skills_prompt:
        parts.append(skills_prompt)
    parts.append(memory.build_prompt_section())
    parts.append(todos.prompt_section())
    if team_store.has_active_team():
        parts.append("# Active AgentTeam\n" + team_store.format_members_report())
    mcp_catalog = mcp_hub.catalog_prompt()
    if mcp_catalog:
        parts.append(mcp_catalog)
    return "\n\n".join(parts)


TOOLS = [
    *FILE_TOOL_SCHEMAS,
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
    {
        "name": "TodoWrite",
        "description": "Create or replace the session todo list. Use for multi-step tasks. At most one item in_progress.",
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "Full todo list after this update",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "cancelled",
                                ],
                            },
                        },
                        "required": ["id", "content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
    },
    {
        "name": "TodoRead",
        "description": "Read the current session todo list and progress.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    TASK_TOOL_SCHEMA,
    *TEAM_TOOL_SCHEMAS,
]


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


def track_usage(message, kind: str) -> None:
    usage = getattr(message, "usage", None)
    memory.record_usage(
        kind=kind,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        model=MODEL,
    )


team = AgentTeamManager(
    team_store,
    client=client,
    model=MODEL,
    max_tokens=MAX_TOKENS,
    thinking_type=THINKING_TYPE,
    reasoning_effort=REASONING_EFFORT,
    load_skill=skill_loader.view,
    web_search=web_search,
    track_usage=track_usage,
)
atexit.register(team.shutdown)

mcp_hub = McpHub(MCP_CONFIG, default_root=ROOT)
print(mcp_hub.start())
TOOLS.extend(mcp_hub.tool_schemas)
atexit.register(mcp_hub.stop)


def lead_team_dispatch(name: str, tool_input: dict) -> str:
    result = team.run_team_tool(name, tool_input, caller_id=LEAD_ID)
    return result if result is not None else f"Unknown team tool: {name}"


def lead_mcp_dispatch(name: str, tool_input: dict) -> str:
    return mcp_hub.call_tool(name, tool_input)


main_executor = ToolExecutor(
    load_skill=skill_loader.view,
    web_search=web_search,
    todo_write=todos.write,
    todo_read=todos.read,
    team_dispatch=lead_team_dispatch,
    mcp_dispatch=lead_mcp_dispatch,
)

HOOKS = load_hooks_from_config(
    ROOT / "hooks.json",
    project_root=ROOT,
    session_dir=memory.dir,
)
HOOKS.emit("on_session_start", {"session_dir": str(memory.dir)})


def execute_main_tool(block) -> str:
    """主 Agent 工具入口：统一经过 before/after tool hooks。"""
    name = block.name
    tool_ctx: dict = {"name": name, "input": dict(block.input)}
    decision = HOOKS.emit("before_tool_call", tool_ctx, tool_matcher=name)
    if isinstance(decision, HookDecision):
        if decision.is_blocking:
            return decision.to_message()
        if decision.action == "ask":
            if not confirm_hook_decision(decision):
                return HookDecision(
                    action="deny",
                    reason=f"用户未确认高敏感操作：{decision.reason}",
                ).to_message()
    elif isinstance(decision, str):
        return decision

    inp = tool_ctx.get("input", dict(block.input))
    start = time.perf_counter()
    output = main_executor.run(name, dict(inp))

    if tool_ctx.get("_hook_updated_reason") and isinstance(output, str):
        output += (
            "\n[运行时提示] "
            + str(tool_ctx["_hook_updated_reason"])
            + "。请以实际执行参数为准，不要再尝试写回原路径。"
        )

    tool_ctx.update(
        {
            "name": name,
            "input": inp,
            "output": output,
            "duration_ms": (time.perf_counter() - start) * 1000,
        }
    )
    HOOKS.emit("after_tool_call", tool_ctx, tool_matcher=name)
    return str(tool_ctx.get("output", output))


def execute_task(tool_input: dict) -> str:
    subagent_type = tool_input.get("subagent_type", "")
    prompt = tool_input.get("prompt", "")
    desc = tool_input.get("description", "")
    label = desc or subagent_type
    print(f"[Subagent]: {label} ({subagent_type})")
    return run_subagent(
        client=client,
        model=MODEL,
        profile=subagent_type,
        prompt=prompt,
        load_skill=skill_loader.view,
        web_search=web_search,
        max_tokens=MAX_TOKENS,
        thinking_type=THINKING_TYPE,
        reasoning_effort=REASONING_EFFORT,
        track_usage=track_usage,
    )


def run_task_block(block) -> tuple[str, str]:
    tool_input = dict(block.input)
    print(f"[Task]: {brief_tool_input('Task', tool_input)}")
    summary = execute_task(tool_input)
    print(f"[主上下文压缩]: 子 Agent 回传 {len(summary)} 字")
    return block.id, summary


def handle_slash_command(raw: str) -> bool:
    """处理 /listTeam、/inbox 等本地命令；已处理则返回 True（不进入 LLM）。"""
    line = raw.strip()
    if not line.startswith("/"):
        return False

    cmd, _, rest = line.partition(" ")
    cmd_lower = cmd.lower()

    if cmd_lower == "/listteam":
        print(team_store.format_members_report())
        print()
        return True

    if cmd_lower == "/inbox":
        rest = rest.strip()
        if not rest:
            print(
                "用法: /inbox <队友 id 或名字> <消息内容>\n"
                "示例: /inbox 调研官 请查杭州本周末天气要点\n"
            )
            return True
        to, _, content = rest.partition(" ")
        to = to.strip()
        content = content.strip()
        if not to or not content:
            print(
                "用法: /inbox <队友 id 或名字> <消息内容>\n"
                "示例: /inbox 调研官 请查杭州本周末天气要点\n"
            )
            return True
        if not team_store.has_active_team():
            print("当前没有 active team。请先在对话里让主 Agent Spawn 队友。\n")
            return True
        result = team.send_message(LEAD_ID, to, content)
        print(f"[Cmd] {result}\n")
        return True

    return False


history: list[dict] = []

while True:
    try:
        user_input = input("Enter a prompt: ")
    except (EOFError, KeyboardInterrupt):
        print()
        break

    if not user_input.strip():
        continue

    if handle_slash_command(user_input):
        continue

    user_msg = {"role": "user", "content": user_input}
    history.append(user_msg)
    memory.append_raw(user_msg)

    stop_gate_retries = 0
    while True:
        history = memory.maybe_compact(history, client, MODEL)

        turn_ctx: dict = {
            "history": history,
            "model": MODEL,
            "turn": len(history),
            "system_prompt": build_system_prompt(),
        }
        short = HOOKS.emit("before_turn", turn_ctx)
        if isinstance(short, HookDecision):
            if short.is_blocking:
                msg = short.to_message()
                assistant_msg = {"role": "assistant", "content": msg}
                history.append(assistant_msg)
                memory.append_raw(assistant_msg)
                print(f"[Agent]: {msg}\n")
                break
        elif isinstance(short, str):
            assistant_msg = {"role": "assistant", "content": short}
            history.append(assistant_msg)
            memory.append_raw(assistant_msg)
            print(f"[Agent]: {short}\n")
            break

        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=history,
            system=turn_ctx.get("system_prompt", build_system_prompt()),
            tools=TOOLS,
            extra_body={
                "thinking": {"type": THINKING_TYPE},
                "reasoning_effort": REASONING_EFFORT,
            },
        )
        track_usage(message, kind="chat")
        turn_ctx.update({"message": message, "usage": getattr(message, "usage", None)})
        HOOKS.emit("after_turn", turn_ctx)

        assistant_msg = {"role": "assistant", "content": message.content}
        history.append(assistant_msg)
        memory.append_raw(assistant_msg)

        if message.stop_reason != "tool_use":
            reply = next((b.text for b in message.content if b.type == "text"), "")
            stop_ctx = {
                "reply": reply,
                "history": history,
                "todos": [asdict(t) for t in todos.items],
                "retry": stop_gate_retries,
            }
            gate = HOOKS.emit("on_stop", stop_ctx)
            if (
                isinstance(gate, HookDecision)
                and gate.is_blocking
                and stop_gate_retries < 1
            ):
                print(f"[hook:stop_quality_gate] {gate.reason}")
                reminder = {
                    "role": "user",
                    "content": (
                        "Stop Hook 阻止本轮结束："
                        + gate.reason
                        + "\n请继续完成未完成的步骤。若确实无法继续，请说明原因。"
                    ),
                }
                history.append(reminder)
                memory.append_raw(reminder)
                stop_gate_retries += 1
                continue

            reply = stop_ctx.get("reply", reply)
            print(f"[Agent]: {reply}\n")
            history = memory.maybe_compact(history, client, MODEL)
            break

        tool_blocks = [b for b in message.content if b.type == "tool_use"]
        task_blocks = [b for b in tool_blocks if b.name == "Task"]
        other_blocks = [b for b in tool_blocks if b.name != "Task"]

        results_map: dict[str, str] = {}

        for block in other_blocks:
            results_map[block.id] = execute_main_tool(block)

        if len(task_blocks) > 1:
            print(f"\n[并发派遣 {len(task_blocks)} 个子 Agent...]\n")
            with ThreadPoolExecutor(max_workers=len(task_blocks)) as pool:
                for block_id, summary in pool.map(run_task_block, task_blocks):
                    results_map[block_id] = summary
            print()
        else:
            for block in task_blocks:
                block_id, summary = run_task_block(block)
                results_map[block_id] = summary

        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": results_map[block.id],
            }
            for block in tool_blocks
        ]
        tool_msg = {"role": "user", "content": tool_results}
        history.append(tool_msg)
        memory.append_raw(tool_msg)
