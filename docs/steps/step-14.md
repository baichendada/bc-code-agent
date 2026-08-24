# Step 14：Goal Loop（目标循环）

核心观点：**模型停止调用工具只说明这一轮想停，不代表整个目标达成**。

| 项 | 说明 |
|---|---|
| 命令 | `/goal <完成条件>`（激活）、`/goal`（状态）、`/goal clear`（stop/off/reset/none/cancel 别名） |
| 语义 | 条件直接作为本轮用户消息注入，立即开工；除非达成，否则不回到提示符 |
| 评估器 | 无工具的独立模型调用：只判定对话里是否已有满足条件的证据；输出 `{ok, reason, impossible}` |
| 退出 | `achieved`（评估通过）/ `failed`（判 impossible）/ `limit`（连续 block 超上限）/ `error`（评估异常）—— 后两者 goal 保持激活，交还主人 |
| 预算 | 连续 block 上限 `GOAL_BLOCK_CAP`（默认 8）：到限暂停自动续跑，**不标完成、不清 goal**；status 显示 elapsed / 核验次数 / token 花费 |
| 持久化 | `sessions/<id>/goal.json`；`--session` 续聊时恢复 active goal（轮次与 token 基线重置） |
| 与 Stop Hook | goal 激活期间 quality gate 让位，由 goal 评估器统一决策（避免两个 block 打架） |
| 与 Todo | 不变：goal 期间照样 TodoWrite 拆步、Task 委派 |

实现：`src/bc_code_agent/goal.py`（GoalState / PromptGoalEvaluator / GoalController）。

设计取舍：

1. **评估器无工具**：它不跑命令不读文件，只判定「对话里的证据是否够」；真正的验证仍由主模型用工具完成（Goal 不是测试框架）
2. **评估器 prompt 防口嗨**：明确要求「除非对话里有命令结果，否则不假设命令成功」，并声明输入里的 JSON 是数据不是指令
3. **预算放 Goal 外面**：Goal 不藏默认轮数预算；上限只是「暂停自动续跑」，用户可继续/换条件/清除
4. **默认同主模型**：`.env` 可换 `GOAL_EVALUATOR_MODEL`（如 glm-4-flash 省钱）；评估输入是对话文本、输出短 JSON（512 token 上限即可）

### 实录（2026-08-23，session=`20260823-233656`）

**场景 1：达成 —— date 查询**

```text
Enter a prompt: /goal 运行 date 命令并汇报当前时间结果
[Goal] 激活: 运行 date 命令并汇报当前时间结果

[Shell]: command='powershell -NoProfile -Command "Get-Date -Format ..."'
[result]: 2026-08-23 23:37:11 (星期日)
[Token] goal_eval: in=384 out=196
[Goal] 达成: ...工具结果中显示了完整时间 2026-08-23 23:37:11（星期日），且已将结果汇报...
[Agent]: 主人，时间查好啦喵～（当前时间 + 说明 Windows 下 date 是设置命令所以改用 PowerShell）
```

**场景 2：Todo 协同 —— 全部 .py 编译检查**

```text
Enter a prompt: /goal 检查项目所有 .py 文件能否通过编译（运行编译命令并汇报结果）
[Goal] 激活: ...
[TodoWrite]: 3 步（Glob 找出所有 .py → 运行 compileall 检查语法 → 汇报结果）
[Glob]: pattern='**/*.py' → 25 个文件
[Shell]: command='python -m compileall -q hooks src tests & echo EXIT_CODE=%ERRORLEVEL%'
[result]: EXIT_CODE=0
[Token] goal_eval: in=1577 out=65
[Goal] 达成: ...退出码为 0 且无错误输出，并汇报了结果，完成条件满足。
[Agent]: 主人，检查全部完成啦...（25 个 .py 全部通过）
```

**场景 3：中断恢复 —— Ctrl+C 后 --session 续跑**

```text
Enter a prompt: /goal 运行 ls 并汇报当前目录内容
[Goal] 激活: ...
（模型正在发请求时 Ctrl+C 退出）

$ python src/bc_code_agent/start.py --session 20260823-233656
[Memory] restored 17 working message(s)
[Goal] restored active goal: 运行 ls 并汇报当前目录内容
Enter a prompt: （输入任意内容即继续）
[Shell]: command='dir' → 目录列表已返回
[Token] goal_eval: in=2387 out=29
[Goal] 达成: ls/dir 已运行并详细汇报了当前目录内容
```

要点：

1. **激活即开工**：`/goal` 后模型自动连续多轮，不需要再输入「继续」；中途 Ctrl+C 后带 `--session` 重启，goal 状态从 `goal.json` 恢复并续跑
2. **独立核验才停**：每轮要停时出现 `[Token] goal_eval`（停前最后一道闸）；`[Goal] 达成` 才收工
3. **评估器读的是对话**：`EXIT_CODE=0`、命令输出这些证据必须在对话里（model 把命令与结果说清楚），否则评估器判「缺证据」→ block → 继续
4. **与 Todo 协同**：goal 期间的复杂任务照样先 TodoWrite 拆步，两个机制互不干扰
5. **达成后 goal.json 记录 met**，`/goal` 可回看历史结果；`/goal clear` 对已达成/未激活的 goal 报「当前没有激活的 goal」

