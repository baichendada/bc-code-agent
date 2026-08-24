import os
import sys
import atexit
import time
import json
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import anthropic

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from skill_loader import SkillLoader
from memory import SessionMemory, format_session_list
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
from goal import (
    ACTION_LABELS,
    CLEAR_ALIASES,
    DEFAULT_EVALUATOR_MAX_TOKENS,
    DEFAULT_STOP_HOOK_BLOCK_CAP,
    MAX_GOAL_LENGTH,
    GoalController,
    GoalError,
    PromptGoalEvaluator,
)
from permissions import PermissionsConfig
from bg_jobs import BACKGROUND, format_task_list, install_cleanup
from cron import CRON_TOOL_SCHEMAS, CronError, CronStore, format_status_message
from workflow import (
    WORKFLOW_TOOL_SCHEMA,
    WorkflowError,
    WorkflowRegistry,
    WorkflowRuntime,
    WorkflowRunner,
)

# 项目根：.../src/bc_code_agent/start.py → parents[2]
ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
SKILLS_DIR = ROOT / "skills"
MCP_CONFIG = ROOT / "mcp.json"


def _parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="bc-code-agent")
    parser.add_argument(
        "--session",
        metavar="ID",
        default=None,
        help="restore conversation from sessions/<ID>",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="print saved sessions and exit",
    )
    return parser.parse_args()


CLI = _parse_cli()
if CLI.list_sessions:
    print(format_session_list(ROOT / "sessions"))
    raise SystemExit(0)
if CLI.session:
    session_dir = ROOT / "sessions" / CLI.session
    if not session_dir.is_dir():
        raise SystemExit(
            f"session not found: {CLI.session}\n"
            "Use --list-sessions to see ids."
        )

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

# Goal Loop（Step 14）配置：evaluator 默认同主模型，可换便宜模型
GOAL_EVALUATOR_MODEL = os.getenv("GOAL_EVALUATOR_MODEL") or MODEL
GOAL_EVALUATOR_MAX_TOKENS = int(
    os.getenv("GOAL_EVALUATOR_MAX_TOKENS", str(DEFAULT_EVALUATOR_MAX_TOKENS))
)
GOAL_BLOCK_CAP = int(
    os.getenv("GOAL_BLOCK_CAP", str(DEFAULT_STOP_HOOK_BLOCK_CAP))
)

if not API_KEY:
    raise SystemExit(f"缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY（检查 {ENV_PATH}）")
if not MODEL:
    raise SystemExit(f"缺少 ANTHROPIC_MODEL（检查 {ENV_PATH}）")

client = anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL or None)
set_workspace(ROOT)


def create_with_retry(
    *, attempts: int = 3, base_wait: float = 2.0, **kwargs
) -> Any:
    """API 调用带指数退避重试：网络/超时类 + 所有 5xx（含 503/504/529）重试；
    400/认证等不重试。"""
    retryable = (anthropic.APIConnectionError, anthropic.APITimeoutError)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return client.messages.create(**kwargs)
        except retryable as exc:
            last_exc = exc
            if attempt == attempts:
                break
            print(
                f"[API] 临时失败（第 {attempt}/{attempts} 次）："
                f"{type(exc).__name__}: {exc} — {base_wait ** attempt:.0f}s 后重试"
            )
            time.sleep(base_wait ** attempt)
        except anthropic.APIStatusError as exc:
            # 503 ServiceUnavailable / 504 DeadlineExceeded / 529 Overloaded
            # 都不是 InternalServerError 子类，按 status_code >= 500 统一重试
            if exc.status_code < 500:
                raise
            last_exc = exc
            if attempt == attempts:
                break
            print(
                f"[API] 服务端失败（第 {attempt}/{attempts} 次）：HTTP {exc.status_code} "
                f"— {base_wait ** attempt:.0f}s 后重试"
            )
            time.sleep(base_wait ** attempt)
        except Exception:  # noqa: BLE001
            # 400/认证错误等：不重试，直接抛出
            raise
    raise last_exc if last_exc is not None else RuntimeError("unknown API failure")

skill_loader = SkillLoader(SKILLS_DIR)
skill_loader.load()
memory = SessionMemory(ROOT, session_id=CLI.session)
todos = TodoStore(memory.dir)
team_store = TeamStore(memory.dir)
history: list[dict] = memory.load_working_history()
if history:
    print(f"[Memory] restored {len(history)} working message(s)")


def persist_history() -> None:
    memory.save_working(history)


atexit.register(persist_history)

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

Permission（工具权限）规则：
1. 工具调用前会经过 permissions.json 声明式权限检查（allow / ask / deny 三档，deny 优先，default=ask）。
2. 若 tool_result 含 [Permission: 拒绝]，说明该操作被规则或主人拦下：不要改写法绕开，向主人说明并换安全方案。
3. 触发 ask 时终端会等主人输入 y；非交互环境会默认拒绝（fail-closed）。YOLO=1 时 ask 自动放行。
4. 拒绝后不要立刻用近似参数重试同一操作（视为绕过尝试）。
Goal（目标循环）规则：
1. 主人用 /goal <完成条件> 设定后，本会话会持续工作直到条件满足、判定不可能完成、或连续多次核验失败，不会每轮停下来等主人。
2. 每轮结束时有一个独立评估器检查对话里的证据；它没有工具，只能看到对话内容，不会替你做验证。
3. 运行验证命令（如测试、typecheck）后，必须清晰汇报命令与结果（如 exit code、关键输出），让独立评估器能据此判断条件是否满足。
4. 只有评估器判定通过才算达成；不要自行宣称“完成了”。
5. 评估未通过时会把你缺失的证据再次发给你，继续工作即可。

Background（后台任务）规则：
1. 只有“独立、不阻塞后续步骤”的慢命令才用 Shell(background=true)（如安装依赖、完整测试套件）；后续步骤依赖其结果的命令必须同步执行。
2. 后台调用立即返回任务 id（如 bg_0001）；完成结果会在后续轮次以 [Background] 通知注入对话，收到后据此继续。
3. 用 /bg 查看任务状态；/bg kill <id> 可停止。子 Agent 不支持后台（会降级为同步执行）。

Cron（定时任务）规则：
1. 需要周期性执行的任务可注册 ScheduleCron（5 段表达式：分 时 日 月 周），到点会以 [Scheduled] 消息触发一轮。
2. 定时轮中的权限 ask 默认拒绝（不抢终端确认）；需要放行请改 permissions.json 或设 YOLO=1。
3. 用 ListCrons / CancelCron 查看与取消；主人也可用 /cron 管理。

Workflow（固定编排）规则：
1. 固定路径的任务（如 测试→修复→复测、多维度审查）用 Workflow 工具执行，编排在 workflows/*.yaml 注册（本人只需给 name/args）。
2. agent 步骤可能返回结构化 JSON（schema）——按结构处理，不要忽略字段。
3. 运行完成后可 /workflow status <runId> 查看每步状态（journal 审计）。
4. 用 /workflow list 看注册表。
""".strip()

skills_prompt = skill_loader.catalog_prompt()


def shell_env_note() -> str:
    """告诉模型当前 Shell 工具的环境，避免首命令就用 POSIX 语法撞墙。"""
    import platform
    system = platform.system()
    if system == "Windows":
        return (
            "# Shell 环境注意\n"
            "本机 Shell 走 Windows cmd（不是 bash）：POSIX 语法（sleep、ls、"
            "date '+%Y-%m-%d'、`;` 分隔等）不可用，会报「不是内部或外部命令」。\n"
            "跨平台/复杂命令请用：powershell -NoProfile -Command \"...\"；"
            "查看目录用 dir；等待用 Start-Sleep -Seconds N。"
        )
    return ""


def build_system_prompt() -> str:
    parts = [BASE_SYSTEM_PROMPT]
    note = shell_env_note()
    if note:
        parts.append(note)
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
    *CRON_TOOL_SCHEMAS,
    WORKFLOW_TOOL_SCHEMA,
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


_TOTAL_TOKENS = 0


def track_usage(message, kind: str) -> None:
    global _TOTAL_TOKENS
    usage = getattr(message, "usage", None)
    _TOTAL_TOKENS += int(getattr(usage, "input_tokens", 0) or 0) + int(
        getattr(usage, "output_tokens", 0) or 0
    )
    memory.record_usage(
        kind=kind,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        model=MODEL,
    )


def confirm_permission(verdict, tool_name: str, tool_input: dict) -> bool:
    """ask 决策：展示实际工具参数，TTY 输入 y 才继续；非交互 fail-closed。"""
    preview = brief_tool_input(tool_name, tool_input)
    print(f"\n[permission] {verdict.reason}")
    print(f"[permission] 参数：{preview}")
    if not sys.stdin.isatty():
        print("[permission] 当前不是交互式终端，默认拒绝执行。\n")
        return False
    try:
        answer = input(
            "[permission] 是否继续执行？输入 y 继续，其余取消: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def permission_gate(name: str, tool_input: dict) -> tuple[str | None, bool]:
    """统一权限闸门：主 Agent 工具、Task 子 Agent、AgentTeam 队友共用。

    返回 (拒绝消息 | None, 是否已确认放行)；消息为 None 表示放行。
    """
    verdict = permissions.check(name, tool_input)
    if verdict.decision == "deny":
        return f"[Permission: 拒绝] {verdict.reason}（匹配规则: {verdict.rule}）", False
    if verdict.decision == "ask":
        # 顺序：YOLO 优先（放行），否则定时轮 fail-closed（不抢终端）
        if permissions.effective_mode() == "yolo":
            print(f"[Permission] YOLO 模式自动放行: {verdict.reason}")
            return None, True
        if is_scheduled_turn():
            print(f"[Permission] 定时任务运行中，跳过交互确认 → 拒绝: {verdict.reason}")
            return f"[Permission: 拒绝] 定时任务中未确认：{verdict.reason}", False
        if confirm_permission(verdict, name, tool_input):
            return None, True
        return f"[Permission: 拒绝] 用户未确认：{verdict.reason}", False
    return None, False


def executor_permission_checker(name: str, tool_input: dict) -> str | None:
    """共享执行器的权限钩子（子 Agent / 队友）：返回非 None 即拒绝。"""
    msg, _approved = permission_gate(name, tool_input)
    return msg


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
    permission_checker=executor_permission_checker,
)
atexit.register(team.shutdown)
if team_store.has_active_team():
    team.resume_workers()

goal_controller = GoalController(
    PromptGoalEvaluator(
        client=client,
        model=GOAL_EVALUATOR_MODEL,
        max_tokens=GOAL_EVALUATOR_MAX_TOKENS,
        track_usage=track_usage,
    ),
    block_cap=GOAL_BLOCK_CAP,
    state_path=memory.dir / "goal.json",
)
_goal_restored = goal_controller.restore()
if _goal_restored:
    print(f"[Goal] {_goal_restored}")

permissions = PermissionsConfig.load(project_root=ROOT)
if permissions.effective_mode() == "yolo":
    print("[Permission] YOLO=1：ask 项自动放行，deny 仍生效")

install_cleanup()

# ---- Cron（Step 19）：存储 + 调度/投递线程 + agent_lock ----

cron_store = CronStore(memory.dir / "cron.json")
try:
    cron_store.load()
except CronError as exc:
    raise SystemExit(str(exc)) from exc

agent_lock = threading.Lock()          # 用户轮 / 定时轮互斥（scheduled turn 不抢终端）
_SCHEDULED_TURN = threading.Event()    # “当前是否定时轮”全局标志（Event 线程安全）
_CRON_STOP = threading.Event()


def is_scheduled_turn() -> bool:
    return _SCHEDULED_TURN.is_set()


def cron_scheduler_loop() -> None:
    """每秒检查到期任务 → 标记 pending + 落盘。"""
    while not _CRON_STOP.wait(1.0):
        try:
            cron_store.poll_due(datetime.now())
        except Exception as exc:  # noqa: BLE001
            print(f"[Cron] 调度检查出错: {type(exc).__name__}: {exc}")


def cron_queue_loop() -> None:
    """0.2s 看一次：有到期任务且锁空闲 → 抢锁投递一轮 scheduled turn。"""
    while not _CRON_STOP.wait(0.2):
        if not agent_lock.acquire(blocking=False):
            continue
        try:
            jobs = cron_store.take_pending()
            if not jobs:
                continue
            text = format_status_message(jobs)
            print(f"[Cron] 到点投递 {len(jobs)} 个定时任务")
            run_turn({"role": "user", "content": text}, scheduled=True)
            cron_store.ack(jobs)
        except Exception as exc:  # noqa: BLE001
            # 兜底：任何异常都不能杀死队列线程（Kron 停摆 = 定时任务集体失效）
            print(f"[Cron] 投递循环出错: {type(exc).__name__}: {exc}")
        finally:
            agent_lock.release()


def _start_cron_threads() -> None:
    for target, name in ((cron_scheduler_loop, "cron-scheduler"), (cron_queue_loop, "cron-queue")):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()


def _stop_cron_threads() -> None:
    _CRON_STOP.set()


def lead_cron_dispatch(name: str, tool_input: dict) -> str:
    """模型侧 cron 工具：ScheduleCron / ListCrons / CancelCron。"""
    if name == "ScheduleCron":
        try:
            job = cron_store.add(
                str(tool_input.get("cron", "")),
                str(tool_input.get("prompt", "")),
                recurring=bool(tool_input.get("recurring", True)),
            )
            return f"已注册 {job.id}: {job.cron} → {job.prompt[:60]}"
        except CronError as exc:
            return f"[Cron] 注册失败: {exc}"
    if name == "ListCrons":
        return cron_store.list_text()
    if name == "CancelCron":
        return cron_store.remove(str(tool_input.get("id", "")))
    return f"Unknown cron tool: {name}"


# ---- Workflow（Step 20）：注册表 + 运行时（生产 runner 复用 run_subagent）----


def workflow_command_guard(name: str, tool_input: dict) -> str | None:
    """command 步骤的闸（与模型 Shell 同链）：黑名单 → 权限管道 → PreToolUse Hook。
    任一环节拒绝即拦截（scheduled 轮中 Hook ask 同样不弹窗）。"""
    command = str(tool_input.get("command") or "")
    from security import match_shell_command

    hit = match_shell_command(command)
    if hit:
        pattern, description = hit
        return f"Security: 危险命令已拦截：{description}（匹配模式：{pattern}）"
    blocked, approved = permission_gate("Shell", tool_input)
    if blocked is not None:
        return blocked

    ctx: dict = {"name": "Shell", "input": tool_input}
    if approved:
        ctx["permission_approved"] = True
    decision = HOOKS.emit("before_tool_call", ctx, tool_matcher="Shell")
    if isinstance(decision, HookDecision):
        if decision.is_blocking:
            return decision.to_message()
        if decision.action == "ask":
            if is_scheduled_turn():
                return f"[Permission: 拒绝] 定时任务中未确认：{decision.reason}"
            if not confirm_hook_decision(decision):
                return f"[Permission: 拒绝] 用户未确认：{decision.reason}"
    elif isinstance(decision, str):
        return decision
    return None


workflow_registry = WorkflowRegistry(ROOT / "workflows")
workflow_registry.load()
workflow_runtime = WorkflowRuntime(
    workflow_registry,
    runner=WorkflowRunner(),
    store_dir=memory.dir / "workflows",
    cwd=ROOT,
    command_guard=workflow_command_guard,
)


class _LiveWorkflowRunner(WorkflowRunner):
    """生产 runner：agent step 走现有 run_subagent（含 schema 结构化输出）。"""

    def run_agent(self, profile: str, prompt: str, schema: dict | None, label: str) -> Any:
        print(f"[Workflow] agent({label}) profile={profile}")
        return run_subagent(
            client=client,
            model=MODEL,
            profile=profile,
            prompt=prompt,
            load_skill=skill_loader.view,
            web_search=web_search,
            max_tokens=MAX_TOKENS,
            thinking_type=THINKING_TYPE,
            reasoning_effort=REASONING_EFFORT,
            track_usage=track_usage,
            permission_checker=executor_permission_checker,
            schema=schema,
        )


workflow_runtime.runner = _LiveWorkflowRunner()


def lead_workflow_dispatch(name: str, tool_input: dict) -> str:
    """模型侧 Workflow 工具：name/args（本版不做断点续跑）。"""
    workflow_name = str(tool_input.get("name", ""))
    args = dict(tool_input.get("args") or {})
    try:
        run_id = workflow_runtime.start(workflow_name, args)
        result = workflow_runtime.run(run_id)
        steps = result["steps"]
        summary = "\n".join(
            f"  {sid}: {rec.get('status')}" for sid, rec in steps.items()
        )
        return (
            f"[Workflow] {workflow_name} run={run_id} → {result['status']}\n"
            f"{summary}"
        )
    except WorkflowError as exc:
        return f"[Workflow] 运行失败: {exc}"

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
    cron_dispatch=lead_cron_dispatch,
    workflow_dispatch=lead_workflow_dispatch,
)

HOOKS = load_hooks_from_config(
    ROOT / "hooks.json",
    project_root=ROOT,
    session_dir=memory.dir,
)
HOOKS.emit("on_session_start", {"session_dir": str(memory.dir)})


def execute_main_tool(block) -> str:
    """主 Agent 工具入口：声明式权限 → Hook 链 → 执行。"""
    name = block.name
    inp = dict(block.input)

    blocked, approved = permission_gate(name, inp)
    if blocked is not None:
        return blocked

    tool_ctx: dict = {"name": name, "input": inp}
    if approved:
        # 权限层已确认：Hook 层的同类 ask（如 tool_policy 的高敏命令）不再重复弹窗
        tool_ctx["permission_approved"] = True
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
        permission_checker=executor_permission_checker,
    )


def run_task_block(block) -> tuple[str, str]:
    tool_input = dict(block.input)
    # Task 本身也过权限闸（P1：防止通过 Task 绕过 Shell/Write 规则）
    blocked, _approved = permission_gate("Task", tool_input)
    if blocked is not None:
        return block.id, blocked
    print(f"[Task]: {brief_tool_input('Task', tool_input)}")
    summary = execute_task(tool_input)
    print(f"[主上下文压缩]: 子 Agent 回传 {len(summary)} 字")
    return block.id, summary


GOAL_RUN = "goal-run"


def handle_permissions_command(raw: str) -> bool:
    """/permissions：查看规则与模式；/permissions test <Tool> [json] 试匹配。"""
    line = raw.strip()
    if not line.lower().startswith("/permissions"):
        return False
    arg = line[len("/permissions"):].strip()
    if arg.lower().startswith("test"):
        rest = arg[4:].strip()
        if not rest:
            print("用法: /permissions test <工具名> [参数JSON]\n")
            return True
        parts = rest.split(None, 1)
        tool_name = parts[0]
        tool_input: dict[str, Any] = {}
        if len(parts) > 1:
            try:
                tool_input = json.loads(parts[1])
            except json.JSONDecodeError:
                print(f"参数 JSON 解析失败：{parts[1]!r}\n")
                return True
        verdict = permissions.check(tool_name, tool_input)
        print(f"{tool_name} → {verdict.decision}（规则: {verdict.rule}）\n")
        return True
    print(permissions.describe() + "\n")
    print("用法: /permissions test <工具名> [参数JSON]  （试匹配，如 /permissions test Shell {\"command\":\"git push\"}）")
    print()
    return True


def inject_background(history: list[dict]) -> int:
    """每轮 LLM 调用前收集完成的后台任务，作为通知注入对话（不新建 tool_result）。"""
    notifications = BACKGROUND.collect()
    if not notifications:
        return 0
    text = "\n\n".join(notifications)
    for item in notifications:
        print(item)
    block = {"type": "text", "text": text}
    if history and history[-1].get("role") == "user":
        content = history[-1].get("content")
        if isinstance(content, list):
            history[-1]["content"] = list(content) + [block]
        else:
            history[-1]["content"] = [
                {"type": "text", "text": str(content)},
                block,
            ]
        persist_history()
    else:
        msg = {"role": "user", "content": [block]}
        history.append(msg)
        memory.append_raw(msg)
        persist_history()
    return len(notifications)


def wait_background(manager) -> bool:
    """Goal defer 后等待后台任务完成。
    任务超时/失败都会让 running() 归 0，等待自然结束，不会无限挂等。"""
    while manager.running() > 0:
        time.sleep(1.0)
    return True


def handle_background_command(raw: str) -> bool:
    """/bg：任务列表；/bg kill <id> 停止；/bg clear 清已完成记录。"""
    line = raw.strip()
    if not line.lower().startswith("/bg"):
        return False
    arg = line[3:].strip()
    if not arg:
        print(format_task_list() + "\n")
        return True
    cmd, _, rest = arg.partition(" ")
    cmd_lower = cmd.lower()
    if cmd_lower == "kill" and rest.strip():
        print(f"{BACKGROUND.kill(rest.strip())}\n")
        return True
    if cmd_lower == "clear":
        count = BACKGROUND.clear_finished()
        print(f"[Background] 已清除 {count} 个已完成任务记录\n")
        return True
    print("用法: /bg | /bg kill <bg_id> | /bg clear\n")
    return True


def handle_goal_command(raw: str) -> str | None:
    """处理 /goal；返回 'goal-run' 表示条件已注入、应进入内层循环。"""
    line = raw.strip()
    if not line.lower().startswith("/goal"):
        return None
    arg = line[len("/goal"):].strip()
    if not arg:
        print(goal_controller.status(_TOTAL_TOKENS) + "\n")
        return "handled"
    if arg.lower() in CLEAR_ALIASES:
        cleared = goal_controller.clear()
        if cleared:
            print(f"[Goal] 已清除: {cleared}\n")
        else:
            print("[Goal] 当前没有激活的 goal\n")
        return "handled"
    if len(arg) > MAX_GOAL_LENGTH:
        print(f"[Goal] 条件过长（上限 {MAX_GOAL_LENGTH} 字符）\n")
        return "handled"
    try:
        goal_controller.set_goal(arg, tokens_at_start=_TOTAL_TOKENS)
    except GoalError as exc:
        print(f"[Goal] {exc}\n")
        return "handled"
    print(f"[Goal] 激活: {arg}\n")
    return GOAL_RUN


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


def handle_workflow_command(raw: str) -> bool:
    """/workflow：list（注册表）/ status <runId>（运行状态）。"""
    line = raw.strip()
    if not line.lower().startswith("/workflow"):
        return False
    arg = line[len("/workflow"):].strip()
    if not arg:
        print(workflow_registry.list_text() + "\n")
        return True
    cmd, _, rest = arg.partition(" ")
    if cmd.lower() == "status" and rest.strip():
        print(workflow_runtime.format_status(rest.strip()) + "\n")
        return True
    print("用法: /workflow | /workflow status <runId>\n")
    return True


def handle_cron_command(raw: str) -> bool:
    """/cron：列表 / <5 段表达式> <prompt> 添加 / rm <id> / run-now <id> / pause|resume <id>。"""
    line = raw.strip()
    if not line.lower().startswith("/cron"):
        return False
    arg = line[len("/cron"):].strip()
    if not arg:
        print(cron_store.list_text() + "\n")
        return True

    cmd, _, rest = arg.partition(" ")
    cmd_lower = cmd.lower()
    rest = rest.strip()

    if cmd_lower == "rm" and rest:
        print(f"{cron_store.remove(rest.split()[0])}\n")
        return True
    if cmd_lower == "run-now" and rest:
        job = cron_store.jobs.get(rest.split()[0])
        if job is None:
            print(f"任务不存在: {rest.split()[0]}\n")
            return True
        print(f"[Cron] 手动触发 {job.id}\n")
        with agent_lock:
            run_turn({"role": "user", "content": f"[Scheduled] {job.prompt}"}, scheduled=True)
            cron_store.ack([job])
        return True
    if cmd_lower in ("pause", "resume") and rest:
        enabled = cmd_lower == "resume"
        print(f"{cron_store.set_enabled(rest.split()[0], enabled)}\n")
        return True

    # 添加：前 5 个 token 为表达式，其余为 prompt（prompt 可含空格，无需引号）
    tokens = rest.split()
    if len(tokens) >= 6:
        expr = " ".join(tokens[:5])
        prompt = " ".join(tokens[5:])
        try:
            job = cron_store.add(expr, prompt)
        except CronError as exc:
            print(f"[Cron] 添加失败: {exc}\n")
            return True
        print(f"[Cron] 已注册 {job.id}: {job.cron} → {job.prompt[:60]}\n")
        return True

    print("用法: /cron | /cron add <分 时 日 月 周> <prompt> | /cron rm <id> | /cron run-now <id> | /cron pause|resume <id>\n")
    print("示例: /cron add */2 * * * * 运行 git status 并汇报当前分支状态\n")
    return True


def run_turn(user_msg: dict, scheduled: bool = False) -> None:
    """注入一条消息并跑完整轮（内层 LLM + 工具循环直到停）。
    用户轮与定时轮共用；scheduled=True 时权限 ask 不弹窗（不抢终端）。"""
    global history
    if scheduled:
        _SCHEDULED_TURN.set()
    try:
        history.append(user_msg)
        memory.append_raw(user_msg)
        persist_history()

        stop_gate_retries = 0
        while True:
            history = memory.maybe_compact(history, client, MODEL)
            inject_background(history)

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
                    persist_history()
                    print(f"[Agent]: {msg}\n")
                    break
            elif isinstance(short, str):
                assistant_msg = {"role": "assistant", "content": short}
                history.append(assistant_msg)
                memory.append_raw(assistant_msg)
                persist_history()
                print(f"[Agent]: {short}\n")
                break

            try:
                message = create_with_retry(
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
            except Exception as exc:  # noqa: BLE001
                msg = (
                    f"[API] 多次重试后仍失败：{type(exc).__name__}: {exc}\n"
                    "本轮未执行。请稍后重试，或检查网络 / ANTHROPIC_BASE_URL。"
                )
                print(msg)
                assistant_msg = {"role": "assistant", "content": msg}
                history.append(assistant_msg)
                memory.append_raw(assistant_msg)
                persist_history()
                break
            track_usage(message, kind="chat")
            turn_ctx.update({"message": message, "usage": getattr(message, "usage", None)})
            HOOKS.emit("after_turn", turn_ctx)

            assistant_msg = {"role": "assistant", "content": message.content}
            history.append(assistant_msg)
            memory.append_raw(assistant_msg)
            persist_history()

            if message.stop_reason != "tool_use":
                reply = next((b.text for b in message.content if b.type == "text"), "")

                if goal_controller.active is not None:
                    decision = goal_controller.evaluate_after_turn(
                        history, background_running=BACKGROUND.running() > 0
                    )
                    if decision.action == "defer":
                        print("[Background] goal 等待后台任务完成...")
                        wait_background(BACKGROUND)
                        continue
                    if decision.action == "block":
                        print(f"[Goal] 未达成: {decision.reason}")
                        reminder = {
                            "role": "user",
                            "content": (
                                "[Goal 仍在进行]\n"
                                f"条件: {goal_controller.active.condition}\n"
                                f"评估器: {decision.reason}\n"
                                "请继续工作，并把缺失的验证证据输出到对话中（例如命令与 exit code）。"
                            ),
                        }
                        history.append(reminder)
                        memory.append_raw(reminder)
                        persist_history()
                        continue
                    label = ACTION_LABELS.get(decision.action, decision.action)
                    print(f"[Goal] {label}: {decision.reason}")
                    print(f"[Agent]: {reply}\n")
                    break

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
                    persist_history()
                    stop_gate_retries += 1
                    continue

                reply = stop_ctx.get("reply", reply)
                print(f"[Agent]: {reply}\n")
                history = memory.maybe_compact(history, client, MODEL)
                persist_history()
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
            persist_history()
    finally:
        if scheduled:
            _SCHEDULED_TURN.clear()


_start_cron_threads()
atexit.register(_stop_cron_threads)


while True:
    try:
        user_input = input("Enter a prompt: ")
    except (EOFError, KeyboardInterrupt):
        print()
        break

    if not user_input.strip():
        continue

    goal_action = handle_goal_command(user_input)
    if goal_action == "handled":
        continue

    if goal_action == GOAL_RUN:
        # /goal <条件>：把条件本身作为本轮用户消息注入，立即开始工作
        user_msg = {"role": "user", "content": user_input[len("/goal"):].strip()}
    else:
        if handle_cron_command(user_input):
            continue
        if handle_workflow_command(user_input):
            continue
        if handle_permissions_command(user_input):
            continue
        if handle_background_command(user_input):
            continue
        if handle_slash_command(user_input):
            continue
        user_msg = {"role": "user", "content": user_input}

    with agent_lock:
        run_turn(user_msg)
