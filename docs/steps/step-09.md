# Step 9：语义化工具 + 子 Agent（Task）

Step 5 的单一 `Bash` 拆成语义工具，便于按 profile 做 least-privilege；复杂子任务用 `Task` 委派独立 context，只回传摘要。

| 项 | 说明 |
|---|---|
| 文件工具 | `Read` / `Write` / `Grep` / `Glob` / `Shell`（`file_tools.py`，路径限制在项目根） |
| 委派工具 | `Task(subagent_type, prompt, description?)` |
| 子 Agent | `explore`（只读探索）、`general`（读写执行）、`review`（只读审查）、`research`（WebSearch 调研） |
| 并发 | 同轮多个 `Task` 用线程池并行（互不依赖的 research 等） |
| 轮次上限 | 各 profile 独立 `max_turns`（explore 10 / review 12 / research 15 / general 20） |
| 分发 | `tool_executor.py` 统一主/子工具执行，子 Agent 日志带 `子·profile·` 前缀 |
| 深度 | 子 Agent 不能再 `Task` |

实现：`file_tools.py`、`subagents.py`、`tool_executor.py`。

### 实录（2026-08-12，session=`20260812-161104`）

用户任务（生活场景，单行粘贴避免 `input()` 只读第一行）：

> 主人周末杭州两日游预算两千以内：先 TodoWrite；并行 Task(research) 查景点美食和周末天气穿衣；Task(general) 写「杭州周末攻略.md」；Task(review) 审查；主 Agent 不要自己 WebSearch/Write。

```text
[TodoWrite] → 4 步 todo（第一次两路 in_progress 被拒，合并后通过）
[并发派遣 2 个子 Agent...]
[Task] research ×2（景点美食 / 天气穿衣）
  [子·research·LoadSkill]: web-search
  [子·research·WebSearch]: ...（境外引擎超时，子 Agent 诚实标注未搜到）
[主上下文压缩]: 子 Agent 回传 1135 / 2105 字

[Task] general → [子·general·Write]: 杭州周末攻略.md（188 行）
[Task] review → 结论 NEEDS_FIX（预算表分项与合计不自洽）
[Task] general → 修正预算表（经济型 ¥930–1300 / 舒适型 ¥1720–2100）

[Memory] compact trigger: history=21 >= 20 → working history 21 → 1
[Todo] updated 6/6 completed
[Agent]: 猫娘口吻汇报交付；注明 WebSearch 网络超时、文档含诚信声明
```

要点：

1. **主 Agent 委派，子 Agent 干活**：终端可见 `子·research·` / `子·general·` / `子·review·`，主 history 只收摘要  
2. **并行 Task**：两个 research 同轮派出，`[并发派遣 2 个子 Agent...]`  
3. **review 闭环**：`NEEDS_FIX` 后 general 再改 md  
4. **网络与代码分离**：`ddgs` 连境外搜索引擎超时属环境问题；子 Agent 架构与委派链路正常  
5. 生成物 `杭州周末攻略.md` 为本地演示产物（已 gitignore），不入库  

