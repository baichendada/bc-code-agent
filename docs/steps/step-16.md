# Step 16：Background Shell（后台任务）

慢命令（pytest / pip 安装）不该堵住整轮：`Shell(background=true)` 显式后台执行，立即返回任务 id，完成结果在**后续轮次**注入对话。

| 项 | 说明 |
|---|---|
| 触发 | `Shell(background=true)`（显式参数，不猜“install/test”等关键词）；系统提示词要求“仅独立、不阻塞后续步骤的命令用后台” |
| 超时 | 默认 **1800s**（`BG_TIMEOUT` env 可改，0=不设内部超时）；goal 等待无上限（任务超时/失败后等待自然结束） |
| 生命周期 | 登记 `bg_0001` 递增 → daemon 线程执行（独立进程组）→ 立即返回占位结果 → 完成进队列（collect 消费即清空） |
| 通知 | 不重建 tool_use_id：完成消息是独立事件（`[Background] bg_0001 完成 (exit 0)` + 命令 + 输出摘要 2000 字符） |
| 注入时机 | 每轮 LLM 调用前 `inject_background()`：合并到最后一条 user 消息或新开一条（**完成不唤醒**，普通对话等你下次输入时才带上） |
| Goal 联动 | 模型想停 + 后台在跑 → `defer`（评估器先不评）→ 主循环轮询等待（上限 600s）→ 完成注入 → 继续跑 → 再停才评估 |
| 子 Agent / 队友 | 不支持：`background=true` 自动**降级同步** + 返回提示（无 collect 通道） |
| 控制 | `/bg`（列表）/ `/bg kill <id>`（进程组）/ `/bg clear`（清已完成；待注入的通知不删） |
| 进程清理 | 进程组生命周期：POSIX `start_new_session`+`killpg`；Windows `CREATE_NEW_PROCESS_GROUP`+`taskkill /T`；`atexit` 全清 |
| 持久化 | **不做**：任务生命周期 = 进程生命周期；`--session` 恢复后需重新发起 |

实现：`src/bc_code_agent/bg_jobs.py`（BackgroundManager / 跨平台进程组）+ `file_tools.py`（background_shell）+ `start.py`（注入与 /bg）。

### 实录（2026-08-24，session=`20260824-015421`）

**场景 1：启动 + 列表 + 完成注入（完成不唤醒）**

```text
Enter a prompt: 帮我用 Shell(background=true) 后台执行一个命令：等待 5 秒后输出 bg-test-done，然后告诉我任务 id
[Shell]: command='powershell ... "Start-Sleep -Seconds 5; Write-Output bg-test-done"'
[result]: [Background] 任务 bg_0001 已启动（后台执行中）: powershell ...
[Agent]: 任务 ID bg_0001...等待 5 秒后输出 bg-test-done；也可用 /bg 查看状态 /bg kill 停掉

Enter a prompt: /bg
id        状态        用时    命令
bg_0001    completed     5s  powershell ...

Enter a prompt: 看下后台任务怎么样了
[Background] bg_0001 完成 (exit 0)
  命令: powershell ...
  输出: bg-test-done
[Agent]: 后台任务已完成（exit 0）...
```

**场景 2：控制命令（kill 不存在 id 的 fail-safe / clear）**

```text
Enter a prompt: /bg kill bg_001
任务不存在: bg_001

Enter a prompt: /bg clear
[Background] 已清除 1 个已完成任务记录

Enter a prompt: /bg
没有后台任务
```

**场景 3：Goal + 长任务 —— defer 等待**

```text
/goal 启动一个约 20 秒的后台任务（sleep 20 后输出 bg-goal-ok），它完成后汇报结果
[Goal] 激活: ...
[Shell] → [Background] 任务 bg_0002 已启动...
[Background] goal 等待后台任务完成...      ← defer：评估器先不评，主循环等待
[Background] bg_0002 完成 (exit 0)         ← 20s 后完成，通知注入
  输出: bg-goal-ok
[Token] goal_eval: in=1234 out=48
[Goal] 达成: 任务 bg_0002 已后台启动并在 20 秒后完成（exit 0），输出 bg-goal-ok，助手已汇报结果。
```

要点：

1. **显式参数**：模型自己决定何时后台；system 提示“依赖结果的步骤必须同步执行”
2. **不唤醒**：普通对话任务完成不打断、不自动续跑；结果在最接近的下一轮注入（不丢失）
3. **Goal 内必须等**：goal 语义是“跑完才回”，defer 让后台完成成为续跑触发点（600s 上限防挂死）
4. **降级兜底**：子 Agent / 队友传 background 会降级同步并提示，不会“启动后没人收结果”
5. **进程组清理**：kill 命令/进程退出时杀整棵树（Windows taskkill /T），不留孤儿进程

6. **kill 的 fail-safe**：不存在的 / 已结束的任务 id 会明确回报，不会误杀其它任务

