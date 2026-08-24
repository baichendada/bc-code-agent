# bc-code-agent

逐步做一个属于白晨的 Agent —— 从一句"喵～"到能自己干活、自己停、自己定时、自己编排的完整终端 Agent。

> 20 个 Step 全链路完成（1–16 + 19 + 20）；每步详细设计 + 真实会话实录见 [`docs/`](docs/README.md)。

## 项目收官（2026-08-25）

| 维度 | 能力 |
|---|---|
| 对话与执行 | 流式对话 / 工具循环 / 语义化文件工具 / 受限 Shell / MCP |
| 记忆与上下文 | 三层记忆 + 压缩 / Todo 清单 / Session 续聊 |
| 自主与验收 | Goal Loop（独立评估器判定完成，不自己说自己完成） |
| 安全与闸门 | 权限管道（deny>ask>allow）/ Hooks 策略链 / 危险命令黑名单 / YOLO 可配 |
| 协作与委派 | Task 子 Agent（4 个 profile）/ AgentTeam（mailbox + 消息工具） |
| 异步与调度 | 后台任务（完成注入）/ Cron 定时轮 / Workflow 固定编排（parallel/pipeline） |
| 教学形态 | 每步独立实录（真实会话）/ [`guide/`](guide/README.md) 18 章可运行教程（17/18 暂缓）/ 178 项测试 |

**设计主线**：所有的"停"都要核验（Goal）、所有的"动"都要过闸（Permission + Hook）、所有"长事"都可恢复可观察（记忆 / journal / 状态落盘）。

**体验三步**：

```text
python src/bc_code_agent/start.py
/goal 运行 date 并汇报当前时间
/cron add */5 * * * * 检查 git 状态并汇报
/workflow          （然后让模型执行 test-fix-retest / review-changes）
```

**诚实的边界**：这是学习与演示稿 —— 单进程、单会话、终端交互；不要拿它上生产，也别对重要目录裸跑。

## 进度

- [x] **Step 1–4**：对话（单轮 → 循环 → history 记忆 → system 人设） — [详情](docs/steps/step-02.md)
- [x] **Step 5–8**：工具调用 / Skill 渐进式披露 / 三层记忆压缩 / Todo 清单 — [详情](docs/steps/step-05.md)
- [x] **Step 9–13**：Task 子 Agent / AgentTeam / MCP / Hooks / Session 续聊 — [详情](docs/steps/step-09.md)
- [x] **Step 14**：Goal Loop（可验证终点 + 独立核验才停） — [详情](docs/steps/step-14.md)
- [x] **Step 15**：Permission 管道（工具前 allow/ask/deny 一等公民） — [详情](docs/steps/step-15.md)
- [x] **Step 16**：Background Shell（慢命令后台 + 完成通知） — [详情](docs/steps/step-16.md)
- [ ] **Step 17/18**：Task 图 + Team v2 —— **暂缓（可选扩展）**：多执行者协作方向；单人场景已被 Workflow 的 parallel/pipeline、Todo、Cron 覆盖；将来要做"无人值守多 Agent 系统"时一起做，17 先于 18
- [x] **Step 19**：Cron（到点触发，三线程模型） — [详情](docs/steps/step-19.md)
- [x] **Step 20**：Workflow Runtime（YAML 固定编排 + journal 审计） — [详情](docs/steps/step-20.md)

## Guide：从零实现终端 Agent

[`guide/README.md`](guide/README.md) 是一条按学习曲线重排的从零教程：Step 01–16、19–20 共 18 章已完成；Step 17/18（Task 图、Team v2）作为多执行者进阶主题暂缓。

每章都包含：

- 固定讲解结构：问题 → 解决方案 → 图示 → 工作原理 → 试一下 → 常见坑 → 接下来
- Mermaid 图示，标出机制在 Agent loop 中的位置
- 最小离线 `agent.py`，不要求 API Key 即可运行教学演示
- `--check` 自检，覆盖当章核心行为与边界

逐章运行自检（PowerShell）：

```powershell
foreach ($agent in Get-ChildItem guide/step-*/agent.py | Sort-Object FullName) {
  py -3.13 $agent.FullName --check
}
```

也可以直接进入单章，例如：

```bash
python guide/step-05-tool-loop/agent.py
python guide/step-05-tool-loop/agent.py --check
```

Step 01 额外提供 `--real`，用于把离线 fake stream 换成真实 Anthropic Messages API。

## 目录结构

```
src/bc_code_agent/
  start.py          入口：主循环 / run_turn / 用户轮与定时轮（agent_lock）/ 斜杠命令
  goal.py          Goal Loop：独立评估器（无工具）+ goal.json 落盘
  permissions.py   权限管道：permissions.json 规则（deny>ask>allow）+ YOLO
  bg_jobs.py       后台任务：进程组管理 + 完成队列（注入下一轮）
  cron.py          Cron：五段表达式 + 调度/投递线程 + cron.json
  workflow.py      Workflow 引擎：YAML 注册 + 4 种 step + schema + journal
  subagents.py     Task 子 Agent（4 profile + 结构化输出 schema）
  team_store/team_runtime.py   AgentTeam：队友配置 / mailbox / 后台线程
  mcp_hub.py       MCP Host（stdio 连接，仅主 Agent）
  hooks.py         Hooks 链（Event→Matcher→Handler→Decision）
  file_tools.py    语义化文件工具 + 受限 Shell（含危险命令兜底）
  memory.py        三层记忆 + 压缩 + working.json
  todo_store.py / skill_loader.py / security.py / tool_executor.py
hooks/             hooks.json 引用的事件脚本（policy/audit/truncate/stop_gate）
workflows/         固定编排示例（test-fix-retest / review-changes）
skills/            Skill 目录（渐进式披露）
sessions/          会话落盘（gitignore）
tests/             本地测试（gitignore，178 项）
docs/              每步详解 + 索引
guide/             从零写终端 Agent 的教程（18 章已完成，17/18 暂缓）
```

## 常用命令速查

| 命令 | 作用 |
|---|---|
| `/goal <条件>` | 设定目标；跑完才回提示符（独立评估器判定） |
| `/goal` / `/goal clear` | 查看状态 / 清除 |
| `/permissions` | 查看权限规则与模式；`/permissions test <工具> [参数JSON]` 试匹配 |
| `/bg` / `/bg kill <id>` / `/bg clear` | 后台任务列表 / 停止 / 清理 |
| `/cron` / `/cron add <分 时 日 月 周> <prompt>` | 定时任务管理（run-now / rm / pause / resume） |
| `/workflow` / `/workflow status <runId>` | Workflow 注册表 / 运行状态 |
| `/listTeam` / `/inbox <队友> <内容>` | 队伍 / 向队友发消息 |
| `YOLO=1` | 权限 ask 自动放行（deny 仍生效），无人值守用 |

## 快速开始

```bash
pip3 install -r requirements.txt   # anthropic / python-dotenv / ddgs / mcp / pyyaml
```

在项目根创建 `.env`（已 gitignore，勿提交密钥）：

```bash
ANTHROPIC_AUTH_TOKEN=your-token
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_MODEL=glm-5.2
MAX_TOKENS=10000

# 智谱思考配置（见 docs.bigmodel.cn 深度思考）
THINKING_TYPE=enabled
REASONING_EFFORT=high

# Goal Loop（Step 14）：评估器默认同主模型，可换便宜模型省 token
# GOAL_EVALUATOR_MODEL=glm-4-flash
# GOAL_EVALUATOR_MAX_TOKENS=512
# GOAL_BLOCK_CAP=8

# Permission（Step 15）：YOLO=1 让 ask 自动放行（deny 仍生效），无人值守时用
# YOLO=1
```

```bash
python3 src/bc_code_agent/start.py
python3 src/bc_code_agent/start.py --list-sessions
python3 src/bc_code_agent/start.py --session <session_id>
```

说明：

- `.env` 会覆盖 shell / `~/.zshrc` 里同名的 `ANTHROPIC_*`
- 智谱 Anthropic 接口模型名用 `glm-5.2`（不要用 Claude Code 别名 `glm-5.2[1M]`）
- 没有 node/npx 环境时设 `MCP_ENABLED=0` 跳过 MCP（启动不再等连接超时）
- 运行测试：`python3 -m unittest discover -s tests -p "test_*.py"`（覆盖安全策略、工具沙箱、记忆裁剪、hook 链路、Team/Todo 存储、goal/permission/bg/cron/workflow）
