# Step 20：Workflow Runtime（固定编排）

固定路径别让模型每步想：`workflows/*.yaml` 注册编排，模型只需给 name/args；运行记录 journal，断点可续跑。

| 项 | 说明 |
|---|---|
| 注册 | `workflows/*.yaml`（YAML，需 pyyaml）：name（slug）/ description / phases / steps；坏文件跳过并告警 |
| Step 类型 | `command`（本地命令）/ `agent`（子 Agent，profile+prompt）/ `parallel`（多 agent 并发 barrier）/ `pipeline`（items × stages，每 item 独立走完） |
| 条件 | `run_if: always \| prev_failed \| prev_succeeded \| <step_id>.failed \| <step_id>.succeeded`（引用任此前置步骤；跳过不污染 prev 状态）；**skipped 不计入失败**（全通过场景 = completed） |
| 结构化输出 | agent step 可选 `schema`：简版校验（type/required/properties），失败重试一次，仍失败 → 该步骤 failed |
| 安全 | `Workflow` 工具本身权限 ask；**command 步骤与模型 Shell 同链**（危险黑名单 → permission_gate → PreToolUse Hook，含高敏 ask，全链路生效） |
| 数据流 | prompt 支持 `{args.key}` 与 **`{steps.<id>.value\|output}`**（前序步骤结果注入，如诊断 → 修复） |
| journal | `sessions/<id>/workflows/<runId>/`：快照 json + `<runId>.journal.jsonl`（每步 {key, value} 审计记录）+ 排他锁（O_EXCL） |
| 语义 | **本版不做断点续跑**：journal 只作审计（`/workflow status` 看每步状态）；失败后重跑 = 全新执行（失败步骤可反复重试） |
| 命令/工具 | `/workflow`（list）/ `/workflow status <runId>`；工具 `Workflow`（name/args） |
| 边界 | run 目录在 session 下：`--session` 恢复同一 session 可看历史 run；本版不做断点续跑 |

实现：`src/bc_code_agent/workflow.py`（registry / schema 校验 / journal / runtime）+ 示例 `workflows/test-fix-retest.yaml`、`workflows/review-changes.yaml`。

### 实录（2026-08-25）

**列表与权限：**

```text
/workflow
name                描述
review-changes       多维度并行审查变更（3 维度审计并发 + 每维度复核流水线）
test-fix-retest      运行项目测试；失败则 review 定位 → general 修复 → 复测（展开 2 轮）

模型调用 Workflow 工具 → [Permission: 拒绝] 用户未确认（非交互 fail-closed）
→ 模型换合规路径：改用 3 个并行 review Task 手动复刻审计（不硬绕）
```

**执行链路（YOLO=1 实测，test-fix-retest）：**

```text
[Workflow]: {'name': 'test-fix-retest'}
[Workflow] test-1: 执行中...
[Workflow] diagnose-1: 执行中...      ← test-1 失败 → run_if=test-1.failed 命中
[Workflow] agent(定位失败原因) profile=review
[Workflow] fix-1: 执行中...          ← diagnose-1.succeeded → 修复执行
journal: 每步一行 {key, value}（含失败记录）
```

**交互实测（2026-08-25，session=`20260825-024410`；review-changes 完整链路）：**

```text
Enter a prompt: 请用 Workflow 工具执行 review-changes，将以下代码作为 args.changes 传入...
[permission] 工具调用 Workflow 匹配规则「Workflow」：需要确认
[permission] 是否继续执行？输入 y 继续，其余取消: y
[Workflow]: {'name': 'review-changes', 'args': {'changes': 'def get_user(name): ...'}}
[Workflow] audit-dimensions: 执行中...
[Workflow] agent(安全审计) profile=review      ┐
[Workflow] agent(性能审计) profile=review      ├─ 3 个 review 子 Agent 并发（各自独立 context）
[Workflow] agent(正确性审计) profile=review    ┘
[Workflow] verify-pipeline: 执行中...
[Workflow] agent(复核) profile=review ×3        ← security/performance/correctness 各复核一次
[Workflow] review-changes-...-95761: completed
[result]: audit-dimensions: succeeded / verify-pipeline: succeeded
[Agent]: 汇报真实审计结论（SQL 注入 Critical + 无锁并发竞态 High，两处分别命中二维度）
```

> 注：parallel 子 agent 与 pipeline stage 没有独立 id 字段，label 回退曾因
> `step.get("label", step["id"])` 默认参数先求值而 KeyError —— 已修复为
> `step.get("label") or step.get("id") or "agent"`（加上 parallel/pipeline 真实执行测试）。

要点：

1. **编排进配置，模型只选**：命令清单写在 yaml（人审）；模型不能注入可执行代码
2. **安全不绕行**：command 步骤与模型 Shell 同一道闸（黑名单 + permission_gate）；agent 子步骤走子 Agent 通道（权限照常）
3. **条件引用前置步骤**：`test-1.failed` 表达“测试失败后的分支”，而不是只看紧邻上一步（否则 诊断成功 → 修复被跳过）
4. **失败可重试**：重跑同一 workflow 是全新执行（journal 只审计）；失败步骤不会被“缓存命中”永久卡住
5. **schema 失败即步骤失败**：结构化输出不合规不会混进 completed（坏结果不入审计）

### 已知边界与后续

- 本版**无断点续跑**（resume）：journal 是审计记录不是缓存；跨进程/跨 session 恢复留作将来扩展
- 可能的下一步：模型生成 workflow 脚本（Workflow generate + 确认后入库 = 业界 dynamic workflows 路线）

## 后续规划

前半程已齐：loop / tools / skill / memory / todo / subagent / team mailbox / MCP / hooks / session 续聊 / goal loop / permission 管道 / background shell / cron 调度 / **workflow runtime（Step 20）**。  

```text
完成: Step 1–16 + 19 + 20（单人 agent harness 全链）
暂缓: Step 17/18（Task 图 + Team v2）—— 多执行者协作方向，是“无人值守多 Agent 系统”
      才需要的形态；单人场景已被覆盖：
      · 并发/依赖 → Workflow 的 parallel/pipeline + run_if 引用
      · 任务清单   → Todo（Step 8）；到点发现工作 → Cron（Step 19）
      将来想要时一起做，17（任务图）先于 18（领取 + worktree）
可能的下一步: 模型生成 workflow 脚本（Workflow generate + 确认后入库 = 业界 dynamic workflows 路线）
```

| Step | 做什么 | 为什么排这里 | MVP |
|---|---|---|---|
| 14 Goal Loop ✅ | 外循环：有目标就一直跑，独立核验才停 | 切口就在 `stop_reason != tool_use`；pause 可复用 `--session` | `/goal` + `goal.json` + block 上限 + 独立评估器 |
| 15 Permission ✅ | 工具前 allow/ask/deny | Goal 会无人值守跑更久；Hook 做扩展不是唯一闸门 | `permissions.json`；TTY 确认；YOLO=1 |
| 16 Background Shell ✅ | 慢命令后台，完成再注入 | 否则 Goal 里 pytest/npm 会堵住整轮 | `background=true`；完成写一条消息进下一轮 |
| 17/18 Task 图 + Team v2 ⏸ | 落盘任务 + `blockedBy` + 原子领取 + worktree | **暂缓**：多执行者协作方向；单人场景被 Workflow(parallel/pipeline) + Todo + Cron 覆盖；想要时一起做 | （s10 设计可直接参考，不另起一步） |
| 19 Cron ✅ | 到点自己开火 | 发现工作 ≠ 做完一件事（后者是 Goal） | `cron.json` 到点往 `/goal` 丢一条 |
| 20 Workflow | 固定编排用脚本 + journal | 路径固定时别再让模型每步想 | 一种：测→改→再测，断点可续 |

不要抢跑：先做 Cron/Workflow（没有 Goal 仍是跑一轮就停）；先做 worktree（没有任务图就没有领取对象）；把 Todo 当成 Goal（Todo 是 checklist，Goal 是宿主外循环 + 核验器）。17/18 已判定暂缓（见上）。

### Step 14 MVP 草案

> 已实现（2026-08-23）：GOAL.md → `goal.json`；verifier → 独立评估器（无工具模型）；
> 「连续 2 次失败 block」→ 连续 block 超上限 `limit`（goal 保持激活）；轮次预算 → 由上限控制。
> 刻意不做仍然成立：独立 verifier 模型（默认同主模型）、多数投票、strategist。

## 快速开始

```bash
pip3 install -r requirements.txt   # 含 anthropic / python-dotenv / ddgs / mcp
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
- 运行测试：`python3 -m unittest discover -s tests -p "test_*.py"`（覆盖安全策略、工具沙箱、记忆裁剪、hook 链路、Team/Todo 存储）
