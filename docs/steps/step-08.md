# Step 8：Todo 任务清单（A 档）

复杂任务用外部清单盯进度，而不是只靠模型「心里记着」。

| 项 | 说明 |
|---|---|
| 工具 | `TodoWrite`（全量更新）、`TodoRead` |
| 状态 | `pending` / `in_progress` / `completed` / `cancelled` |
| 约束 | 多步任务（约 ≥3 步）先写 todo；同时最多 1 个 `in_progress` |
| 落盘 | `sessions/<id>/todos.json` |
| 可见性 | 每轮 system 注入当前 todo；终端打印 `[Todo] x/y completed` |

实现：`src/bc_code_agent/todo_store.py`。

### 实录（2026-08-09，session=`20260809-183828`）

用户任务（多步，必须先 TodoWrite）：

> 帮我在网上搜索一下关于东南亚旅游的相关攻略，然后对每一个攻略进行打分并给出理由，最后参考这些攻略生成一个攻略给我，生成一份 md 文件放到当前目录

```text
[Tool]: TodoWrite({
  todos: [
    {id:1, content:"加载 web-search skill 获取搜索方法", status:in_progress},
    {id:2, content:"搜索东南亚旅游攻略相关资料（多个查询）", status:pending},
    {id:3, content:"对收集到的攻略进行打分评估", status:pending},
    {id:4, content:"参考攻略生成一份综合md攻略文件到当前目录", status:pending}
  ]
})
[Todo] updated 0/4 completed
[Tool Result]:
[>] 1: 加载 web-search skill 获取搜索方法 (in_progress)
[ ] 2: 搜索东南亚旅游攻略相关资料（多个查询） (pending)
[ ] 3: 对收集到的攻略进行打分评估 (pending)
[ ] 4: 参考攻略生成一份综合md攻略文件到当前目录 (pending)

[Tool]: LoadSkill({'name': 'web-search'})
[Tool]: TodoWrite(... id1=completed, id2=in_progress ...)
[Todo] updated 1/4 completed

[Tool]: WebSearch({'query': '东南亚旅游攻略 最佳目的地推荐 2024', 'max_results': 6})
[Tool]: WebSearch({'query': '泰国旅游攻略 自由行 行程 费用 注意事项', ...})
[Tool]: WebSearch({'query': '越南 巴厘岛 新马泰旅游 自由行攻略 行程安排', ...})
[Tool]: WebSearch({'query': '东南亚旅游签证 费用预算 最佳旅游时间', ...})
[Tool]: WebSearch({'query': '越南旅游攻略 胡志明市 河内 会安 美食 景点', ...})
[Tool]: WebSearch({'query': '东南亚旅游 新加坡 马来西亚 自由行 推荐景点', ...})

[Tool]: TodoWrite(... id2=completed, id3=in_progress ...)   # 2/4
[Tool]: TodoWrite(... id3=completed, id4=in_progress ...)   # 3/4
[Tool]: Bash({'command': 'cat > 东南亚旅游攻略.md << \'GUIDE_EOF\' ...'})
[Tool Result]: 213 行 / ~10KB，文件写入当前目录

[Tool]: TodoWrite(... 全部 completed ...)
[Todo] updated 4/4 completed
[Tool Result]:
[x] 1: 加载 web-search skill 获取搜索方法 (completed)
[x] 2: 搜索东南亚旅游攻略相关资料（多个查询） (completed)
[x] 3: 对收集到的攻略进行打分评估 (completed)
[x] 4: 参考攻略生成一份综合md攻略文件到当前目录 (completed)

[Agent]: 汇报：6 次搜索 → 15 份来源打分（BringYou 9.5 最高）→ 生成十大章节综合攻略
```

会话结束时 `todos.json`：

```json
[
  {"id": "1", "content": "加载 web-search skill 获取搜索方法", "status": "completed"},
  {"id": "2", "content": "搜索东南亚旅游攻略相关资料（多个查询）", "status": "completed"},
  {"id": "3", "content": "对收集到的攻略进行打分评估", "status": "completed"},
  {"id": "4", "content": "参考攻略生成一份综合md攻略文件到当前目录", "status": "completed"}
]
```

要点：

1. **先清单后干活**：复杂任务第一动作是 `TodoWrite`，再 `LoadSkill` / `WebSearch` / `Bash`  
2. **推进可观察**：每完成一步就更新 status，终端有 `[Todo] x/y completed`  
3. **同时最多一个 in_progress**：不会出现「四件事都在干」  
4. 生成物 `东南亚旅游攻略.md` 为本地演示产物（已 gitignore），不入库  

