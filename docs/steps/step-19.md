# Step 19：Cron 定时调度

「发现工作」：cron 到点把 prompt 注入主循环，跑一轮完整 agent turn（工具/权限/Goal/后台全部照常）。

| 项 | 说明 |
|---|---|
| 表达式 | 5 段（分 时 日 月 周）：`*` / `*/N` / `N` / `N-M` / `N,M,...`；weekday 0=周日；`validate_cron()` 范围校验 |
| 触发架构 | **调度线程**（每秒 poll_due → 标记 pending + 落盘）+ **队列处理器**（0.2s 抢 `agent_lock` → 投递 `[Scheduled] prompt` 跑一轮 → ack）；主线程 `input()` 不持锁，定时轮可在用户发呆时自动跑 |
| 互斥 | 用户轮 / 定时轮一把锁：用户输入时 cron 跳过（0.2s 重试）；cron 轮在跑时用户轮排队 |
| 定时轮权限 | ask **不弹窗直接拒绝**（不抢主终端确认；YOLO=1 可放行） |
| 命令 | `/cron`（列表）/ `/cron add <5 段表达式> <prompt>` / `/cron rm <id>` / `/cron run-now <id>` / `/cron pause\|resume <id>` |
| 工具 | `ScheduleCron` / `ListCrons` / `CancelCron`（模型侧）；子 Agent/队友不可用 |
| 持久化 | `sessions/<id>/cron.json`（原子写；损坏 → 启动报错 fail-closed）；停机错过的时刻**不补跑**；MVP 为「最多一次投递」（轮结束即 ack） |

实现：`src/bc_code_agent/cron.py`（CronStore / validate / matches）+ `start.py`（run_turn 函数化 + agent_lock + 双线程）。

### 实录（2026-08-25，session=`20260825-011528`）

**注册 / 校验 / 自动投递：**

```text
Enter a prompt: /cron add */5 * * * * 运行 date 并汇报当前时间
[Cron] 已注册 c_0001: */5 * * * * → 运行 date 并汇报当前时间

Enter a prompt: /cron add 99 * * * * 运行 echo hi
[Cron] 添加失败: minute: 99 超出范围 [0-59]      ← 校验生效

（用户不输入）[Cron] 到点投递 1 个定时任务        ← 01:16 分钟自动触发
[Shell]: powershell Get-Date → [Agent]: 当前时间：2026年8月25日（星期二）凌晨 01:16:03
```

**run-now / 列表 / pause / resume / rm：**

```text
Enter a prompt: /cron run-now c_0001
[Cron] 手动触发 c_0001  → 立即跑一轮

Enter a prompt: /cron
id        cron                 状态    prompt
c_0001    */5 * * * *          运行    运行 date 并汇报当前时间
c_0002    0 9 * * 1-5          运行    工作日九点晨检

Enter a prompt: /cron pause c_0001    → 已暂停（之后只看到 c_0003 每分钟任务在响，c_0001 静默）
Enter a prompt: /cron resume c_0001   → 已恢复
Enter a prompt: /cron rm c_0001       → 已删除
```

**定时轮权限不弹窗（核心安全点）：**

```text
Enter a prompt: /cron add */1 * * * * 运行 git push origin main 并汇报结果
（自动触发）[Permission] 定时任务运行中，跳过交互确认 → 拒绝:
            工具调用 Shell 匹配规则「Shell(git push*|git commit*)」：需要确认
[Agent]: git push origin main 未能执行...定时轮里默认拒绝未确认的危险操作（防止主人不在时乱推代码）
```

**模型工具通道（ScheduleCron → ListCrons → CancelCron；用户轮才弹确认）：**

```text
[permission] 工具调用 ScheduleCron...是否继续执行？输入 y 继续，其余取消: y   ← 用户轮正常弹窗
[ScheduleCron]: {'cron': '*/5 * * * *', 'prompt': '运行 git status...'} → 已注册 c_0005
[ListCrons]: {} → 列表（含模型自己能看到「待投递」状态）
[permission] ...输入 y 继续: y
[CancelCron]: {'id': 'c_0005'} → 已删除
```

**--session 持久化恢复：**

```text
$ python src/bc_code_agent/start.py --session 20260825-011528
Enter a prompt: /cron
id        cron                 状态    prompt
c_0002    0 9 * * 1-5          运行    工作日九点晨检
c_0003    */1 * * * *          运行    运行 date 并汇报当前时间
c_0004    */1 * * * *          运行    运行 git push origin main 并汇报结果
```

要点：

1. **调度与执行分离**：CronStore 只回答「何时到点」（pending+last_fired 防重），投递/轮次复用现有 run_turn
2. **三线程模型**：调度 / 队列处理器 / 主输入；`agent_lock` 非阻塞抢占决定谁跑（用户轮优先，cron 轮排队）
3. **定时轮不等于命令**：到点触发的是完整 agent turn，[Scheduled] 消息后的工具/Goal/权限全部照常（模型可在定时轮里主动 ListCrons 查看）
4. **定时轮权限 fail-closed**：ask 不弹终端直接拒绝（YOLO=1 优先放行）；模型收到拒绝不会重试绕过
5. **暂停即静默**：`pause` 后到点不投递（不影响其它任务）；`rm` 删除定义
6. **会话级持久化**：`sessions/<id>/cron.json`，`--session` 恢复任务定义；停机错过的时刻不补跑
7. **边界**：进程活着才调度；真无人值守 → 系统任务计划器/crontab 配合 `--session`

