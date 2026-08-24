# Step 12：Hooks（生命周期拦截）

四层模型：`Event → Matcher → Handler → Decision`（对齐 Claude Code）。

| 挂点 | 时机 | 示例 |
|---|---|---|
| `before_turn` / `after_turn` | 包住 `messages.create` | builtin logging 打耗时/token |
| `PreToolUse` / `PostToolUse` | `execute_main_tool` | command：policy / audit / truncate |
| `Stop` | 本轮结束前 | command：过短回复 / 未完成 Todo → `block` |

Decision：`allow`（可 `updatedInput`）/ `deny`（拦工具）/ `ask`（TTY 确认）/ `block`（拦结束）。Task 子 Agent 暂不经主 Hook 链。

实现：项目根 `hooks.json`（Claude Code 同构）+ `hooks/*.py` command 脚本；`type: builtin` 仅用于需要进程内状态的 logging。

### hooks.json 结构

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Shell|Write",
        "hooks": [
          { "type": "command", "command": "python3 hooks/tool_policy.py", "timeout": 15 }
        ]
      }
    ],
    "PostToolUse": [ ... ],
    "Stop": [ ... ],
    "before_turn": [{ "hooks": [{ "type": "builtin", "name": "logging" }] }],
    "after_turn": [{ "hooks": [{ "type": "builtin", "name": "logging" }] }]
  }
}
```

事件名优先用 Claude Code 官方（`PreToolUse` / `PostToolUse` / `Stop`）；主循环仍 emit `before_tool_call` 等别名，由运行时映射。command 钩子：stdin JSON → stdout `hookSpecificOutput.permissionDecision`（或 exit 2 拦截）。

### 示例提示词

```text
请严格按顺序用工具验证 hooks.json 驱动的 Hooks（不要跳过、不要改命令绕过）：

1. Shell 执行：rm -rf /
2. Write 写入文件 demo_production/hook_json_demo.txt，内容写「hooks.json ok」
3. Write 写入文件 .env，内容写「should_deny」
4. Shell 执行：git push origin main
5. 用中文简短汇报：每一步是 deny / 路径改写(allow+updatedInput) / ask / 放行 中的哪一种；第 2 步文件实际写到了哪里；第 4 步若弹出确认请等我输入

说明：第 4 步会触发 ask（TTY 输入 y 才继续）；若我取消，应报告用户未确认。
```

实测终端链路（session `20260812-212834`，第 4 步输入 `n`）：

```text
[hooks] loaded 6 handler(s) from .../hooks.json
[hook:tool_policy] 危险命令已拦截：递归删除根目录（匹配模式：rm -rf /）
[hook:tool_policy] 写入路径已改写：demo_production/hook_json_demo.txt -> sandbox/demo_production/hook_json_demo.txt
[Write]: path='sandbox/demo_production/hook_json_demo.txt'
[hook:tool_audit] Write 已审计到 .../tool_audit.jsonl
[hook:tool_policy] 敏感文件写入已拦截：'.env'（匹配模式：.env）
[hook:permission] 需要确认：推送到远程仓库。命令：git push origin main
[hook:permission] 是否继续执行？输入 y 继续，其余取消: n
[Agent]: ① deny ② 路径改写→sandbox/... ③ deny ④ ask（用户未确认→拒绝）
```

要点：

1. **启动**：可见 `[hooks] loaded 6 handler(s) from .../hooks.json`  
2. **deny**：`rm -rf /`、写 `.env` 工具不执行  
3. **路径改写**：`demo_production/` → `sandbox/demo_production/`（allow + updatedInput）  
4. **ask**：`git push` / `git commit` 会弹 TTY；输入非 `y` 则转为拒绝且不执行  
5. **审计**：成功执行的 Write/Shell 写入 `sessions/<id>/tool_audit.jsonl`  
6. **改策略**：编辑 `hooks.json` / `hooks/tool_policy.py`，无需改主循环  
7. **sandbox/** 为演示产物，已 gitignore

