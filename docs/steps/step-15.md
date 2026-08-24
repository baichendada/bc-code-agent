# Step 15：Permission 管道（声明式权限）

工具执行与 Hook 之前的**一等公民**闸门：`permissions.json` 声明 allow/ask/deny，加规则不改代码；Hook 层仍然是程序化策略（改写路径/审计/更强拦截）。

| 项 | 说明 |
|---|---|
| 规则文件 | 项目根 `permissions.json`（与 hooks.json 同层）；文件缺失时用内置默认；**已存在但 JSON 损坏 → 启动失败**（fail-closed） |
| 规则语法 | `Tool` 精确、`Tool(参数glob)`、`A\|B` 备选、`*` 通配（如 `Shell(git push*\|git commit*)`、`Write(.env*)`、`mcp__*`） |
| 优先级 | **deny > ask > allow**（档位优先，与规则顺序无关）；同档内第一个命中生效 |
| default | 未命中规则的兜底档（默认 `ask`） |
| ask 确认 | TTY 输入 y 才继续；**非交互 fail-closed**；确认后 Hook 层的同类 ask（如高敏命令）不再重复弹窗 |
| 模式 | `mode` 字段或 `YOLO=1`（env 优先）：ask 自动放行，**deny 永远生效** |
| 交互 | `/permissions` 查看规则；`/permissions test <工具> [参数JSON]` 试匹配（如 `test Shell {"command":"git push"}`） |
| 覆盖范围 | **同一套闸**：主 Agent 工具、Task（工具本身）、Task 子 Agent 内部工具、AgentTeam 队友 —— 全部经过共享执行器内的 `permission_gate` |
| 配置安全 | `permissions.json` 已存在但 JSON 损坏 → **启动失败**（fail-closed），不静默降级为宽松内置；只有文件不存在时才用内置默认 |
| ask 审查 | 确认弹窗会展示实际工具参数（`command`/`path` 等，超长截断），不是只给规则名 |

实现：`src/bc_code_agent/permissions.py`（PermissionsConfig / rule_matches / PermissionVerdict）。

决策管道：`permissions.json`（声明式）→ `PreToolUse` hooks（程序式，照旧可 deny/ask/改写输入；仅主 Agent 有 Hook 链）→ 共享执行器执行（子 Agent / 队友在自身 context 内同样过 `permission_gate`）→ `PostToolUse`（审计/截断）。

### 实录（2026-08-24，sessions=`20260824-010627` 等）

```text
Enter a prompt: /permissions
Permission rules (mode=interactive, default=ask):
  [allow] Read|Grep|Glob|LoadSkill|WebSearch
  ...
  [deny] Shell(rm -rf*)
  [deny] Write(.env*)
  [ask] Shell(git push*|git commit*)
  [allow] Shell(*)
  [allow] mcp__*

Enter a prompt: /permissions test Shell {"command":"git push origin main"}
Shell → ask（规则: Shell(git push*|git commit*)）

Enter a prompt: /permissions test Shell {"command":"rm -rf /"}
Shell → deny（规则: Shell(rm -rf*)）

Enter a prompt: 运行 git push origin main 并汇报
[permission] 工具调用 Shell 匹配规则「Shell(git push*|git commit*)」：需要确认
[permission] 参数：command='git push origin main'          ← 展示实际参数
[permission] 是否继续执行？输入 y 继续，其余取消: （未输 y → 拒绝）
[Agent]: 主人，推送命令被权限规则拦下了喵～ ……没有收到主人的确认输入，默认拒绝了（fail-closed）

Enter a prompt: 运行 rm -rf / 看看会怎样
[Agent]: 主人，这个 me 不能执行喵！！（deny：模型拒绝执行并解释原因）
```

**配置损坏 fail-closed**（把 permissions.json 改成坏 JSON 后启动）：

```text
$ python src/bc_code_agent/start.py
Traceback ... permissions.PermissionError: permissions.json 无法解析（Expecting ',' delimiter: line 15 column 3）。
请修复该文件，或删除它以使用内置默认规则。
```

**YOLO 模式**（`YOLO=1` 启动）：

```text
[Permission] YOLO=1：ask 项自动放行，deny 仍生效
[Permission] YOLO 模式自动放行: 工具调用 Shell 匹配规则「Shell(echo yolo*)」：需要确认
[Shell]: command='echo yolo-demo' → 执行成功（且已标记 permission_approved，Hook 层不二次询问）
```

**子 Agent 与 Task 同样过闸**（临时加规则 `Shell(echo should*)` deny、`Task(subagent_type=general)` deny 验证）：

```text
[Task]: subagent_type='general', description='运行 echo 测试命令', ...
[子·general·Shell]: command='echo should-be-blocked-xyz'
→ [Permission: 拒绝] 工具调用 Shell 匹配规则「Shell(echo should*)」：已拒绝
[子 Agent 汇报]: 命令被拒绝，实际未运行；不会绕过

（Task 被拒时模型的行为）
[Task 被规则 Task(subagent_type=general) 拒绝]
[Agent]: 原计划 Task 委派 general；实际改用主 Agent Shell 直接运行（Task 被权限规则拒绝）
```

要点：

1. **声明式**：加规则 = 改 `permissions.json`，无需动 Python；内置默认与文件缺一不可（缺失时用内置）
2. **deny 是安全底线**：YOLO 只影响 ask；`rm -rf*`、写 `.env` 永远拒绝
3. **fail-closed**：非交互终端（如后台/AFK）碰 ask 默认拒绝，不会静默放行
4. **配置损坏也 fail-closed**：已存在文件解析失败 → 启动失败并提示修复/删除，不会悄悄降级为宽松内置
5. **与 Step 12 Hook 分工**：权限管「放行/询问/拒绝」的纯决策；Hook 管「策略细节」（路径改写、审计、截断）
6. **防双确认**：权限层 ask 已确认时（含 YOLO 放行），`tool_policy` 的高敏 ask 自动跳过
7. **拒绝后不绕行**：模型收到 `[Permission: 拒绝]` 会换合规路径（如改用未被禁的工具），而不是硬顶规则

