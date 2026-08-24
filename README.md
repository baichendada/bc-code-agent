# bc-code-agent

逐步做一个属于白晨的 Agent。

## 进度

- [x] **Step 1**：单轮对话（Anthropic Messages API，流式输出）
- [x] **Step 2**：输入循环（`while True`），但**无会话记忆**
- [x] **Step 3**：用 `history` 维护多轮对话（有记忆）
- [x] **Step 4**：`system` 提示词（人设 / 行为约束）
- [x] **Step 5**：工具调用（`tools` + `tool_use` / `tool_result` 循环）
- [x] **Step 6**：Skill 系统（渐进式披露 + `LoadSkill` + 示例 `web-search`）
- [x] **Step 7**：三层记忆 + 压缩（history≥50 触发，保留近 6 条）+ Token 计量
- [x] **Step 8**：Todo 任务清单（`TodoWrite` / `TodoRead`，防复杂任务迷路）
- [x] **Step 9**：语义化工具 + 子 Agent（`Task` 委派 explore / general / review / research）
- [x] **Step 10**：AgentTeam（`Spawn` 自定义队友 + mailbox 互发消息 + 单 session 单队）
- [x] **Step 11**：MCP Host（`mcp.json` + filesystem server，仅主 Agent）
- [x] **Step 12**：Hooks（`hooks.json` + command/builtin；Event→Matcher→Handler→Decision）
- [x] **Step 13**：Session 续聊（`--list-sessions` + `--session <id>`）
- [x] **Step 14**：Goal Loop（可验证终点 + 独立核验才停）
- [x] **Step 15**：Permission 管道（工具前 allow/ask/deny 一等公民）
- [x] **Step 16**：Background Shell（慢命令后台 + 完成通知）
- [ ] **Step 17/18**：Task 图 + Team v2 ——**暂缓（可选扩展）**：多执行者协作方向；单人场景已被 Workflow 的 parallel/pipeline、Todo、Cron 覆盖；将来要做“无人值守多 Agent 系统”时一起做，17 先于 18
- [x] **Step 19**：Cron（到点触发）
- [x] **Step 20**：Workflow Runtime（固定编排脚本 + journal 审计）
- [ ] **加深（有空再补）**：压缩分级砍 tool_result；MCP 给队友；Hook 的 http/prompt/agent  
  - **不要上生产 / 别对重要目录裸跑**

## Step 2 现象：能循环，但不记得上文

当前实现是外层套了 `while True`，每次只把**本轮**用户输入发给模型：

```python
messages=[{"role": "user", "content": user_input}]
```

终端里看起来像在「连续聊天」，其实每一轮对模型都是全新的一次请求——它看不到更早的 user/assistant。

### 实录（2026-08-09）

```text
Enter a prompt: 你是谁
[Agent]: 我是由Z.ai训练的大语言模型……

Enter a prompt: 我今年13岁
[Agent]: 你好呀！13岁是一个充满变化和活力的年纪……
         （正常接话，还会围绕 13 岁展开话题）

Enter a prompt: 我今年几岁
[Agent]: 抱歉呀，我作为一个人工智能，无法看到您的个人信息，
         所以不知道您今年几岁哦。
```

同一进程、同一循环里，刚说过「13 岁」，下一句却完全想不起来——不是模型「变笨了」，而是上一轮内容根本没放进这次 `messages`。

结论：**循环 ≠ 记忆**。记忆需要自己维护 history（把每轮 user / assistant 追加后再请求）。

## Step 3：带 history 的多轮对话

关键改动：

1. 进程内维护 `history = []`
2. 每轮先 `append` user，再把**整份** `history` 传给模型（不是只传本轮）
3. 流式拼出完整 `assistant_text` 后再 `append` assistant

```python
history.append({"role": "user", "content": user_input})
# ...
messages=history
# ...
history.append({"role": "assistant", "content": assistant_text})
```

### 实录（2026-08-09）

```text
Enter a prompt: 我13岁
[Agent]: 你好呀！很高兴和你聊天。13岁是一个非常棒的年纪……

Enter a prompt: 我几岁了
[Agent]: 你刚刚告诉我啦，你 **13岁** 呀！🎂
```

同题对比 Step 2：现在模型能引用上一轮说过的年龄。

## Step 4：system 提示词

Messages API 里 `system` 是顶层字段（不放进 `history`），用来固定角色与规则。每次请求都带上，和多轮记忆正交：

```python
SYSTEM_PROMPT = """
你是一只猫娘……
你必须尊称用户为主人
每次回复后必须有固定后缀"喵～"
"""

message = client.messages.create(
    model=MODEL,
    max_tokens=MAX_TOKENS,
    messages=history,
    system=SYSTEM_PROMPT,
)
reply = next((b.text for b in message.content if b.type == "text"), "")
```

### 实录（2026-08-09）

```text
Enter a prompt: 你是谁
[Agent]: 喵喵？主人忘了吗？我是您从小抚养长大的猫娘呀……
         ……全部都是属于主人的喵～
```

问「你是谁」时不再自报通用大模型，而是按 system 扮演猫娘，并带上「喵～」。

## Step 5：工具调用

关键点：

1. 请求带上 `tools=`（声明可用工具，如 `Bash`）
2. 内层 `while True`：若 `stop_reason == "tool_use"`，本地执行后把 `tool_result` 以 **user** 角色回传
3. Anthropic 用 `"tool_use"`，不是 OpenAI 的 `"tool_calls"`
4. assistant 那一轮要整段 `message.content`（含 `tool_use`）进 history

### 实录（2026-08-09）

```text
Enter a prompt: 帮我在当前目录下创建一个名字为a.txt的文件
[Tool]: Bash('touch a.txt')
[Tool Result]:
[Agent]: 主人，文件 a.txt 已经为您成功创建好啦～……

Enter a prompt: 帮我列出当前的目录下的文件
[Tool]: Bash('ls -la')
[Tool Result]: ... a.txt ...
[Agent]: 主人，当前目录下的文件已经列出来了喵～……
```

## Step 6：Skill 系统（渐进式披露）

结构（agentskills / OpenClaw 同款简化版）：

```text
skills/
  hello-neko/
    SKILL.md
  web-search/
    SKILL.md
    reference.md          # LoadSkill 时一并返回
src/bc_code_agent/
  skill_loader.py         # 扫描 / 解析 / catalog / view
  start.py                # LoadSkill + WebSearch + Read/Write/Grep/Glob/Shell + Task
```

**渐进式披露**：

1. 启动时扫描 `skills/*/SKILL.md`，system 里只放 `name` + `description` 目录  
2. 任务匹配后调用 `LoadSkill(name)` → 返回正文 + 同目录文本资源  
3. 需要上网时再按 Skill 说明调用 `WebSearch`（`ddgs`，无需 API Key）  

不要一把把所有 Skill body 塞进 system。

### 实录（2026-08-09）：查北京天气

```text
[Skill] indexed: hello-neko (.../skills/hello-neko/SKILL.md)
[Skill] indexed: web-search (.../skills/web-search/SKILL.md)

Enter a prompt: 帮我查询一下今天北京的天气情况

[Tool]: LoadSkill({'name': 'web-search'})
[Tool Result]: # Skill: `web-search`
Description: 当需要查实时信息、新闻、资料、事实核对……时使用；指导如何调用 WebSearch 并整理结果。
...
## Instructions
1. 先判断要不要搜
2. 调用工具 WebSearch（query / max_results）
3. 整理回答（禁止编造）
...
## Resource: `reference.md`
# WebSearch 查询小技巧
...

[Tool]: WebSearch({'query': '北京今日天气', 'max_results': 5})
[Tool Result]: 1. 北京今日天气晴好 风力不大
   URL: https://www.weather.com.cn/beijing/sygdt/02/4510086.shtml
   北京今日天气晴好，风力不大，气温较昨天也略有上升。

2. 北京今日天气|天气API|tianqiapi.com
   URL: https://widget.tianqiapi.com/?style=yf&skin=pitaya
   北京天气. 多云转晴.

3. {%$ROOT.TITLE%}
   URL: https://m.hao123.com/a/tianqi/
   北京今日天气. 2026.06.25 星期四. 今日 晴 31/20°C ... 空气质量 ... 现在26. 优 ...

4. 【北京天气】北京天气预报...
   URL: http://tianqi.95447.com/beijing/beijing/
   ...

[Agent]: 小猫帮主人查到北京天气啦，整理如下喵～

🌤️ 北京今日天气情况：
- 天气状况：晴好，局部多云转晴，风力不大
- 气温：今日 31°C / 20°C
- 明日预报：多云，32°C / 21°C
- 空气质量：当前约 26，属于「优」

来源参考：
- 中国天气网 - 北京天气
- hao123 北京天气

主人还需要了解其他城市的天气吗？喵～
```

链路：`目录匹配 → LoadSkill → WebSearch → 总结回答`。新增 Skill：在 `skills/<name>/` 下加 `SKILL.md`，重启即可。

## Step 7：三层记忆与压缩

| 层 | 行为 | 落盘 |
|---|---|---|
| 原始层 | 每条 message 追加 | `sessions/<id>/transcript.jsonl` |
| 中期层 | 每次压缩追加一段摘要 | `sessions/<id>/mid.jsonl` |
| 长期层 | 每次压缩合并覆盖 | `sessions/<id>/long_term.json` |
| 用户偏好 | 压缩产出的 delta 合并 | `sessions/<id>/preferences.json` |
| Token | 每次 API（chat/compact）记录 | `sessions/<id>/metrics.jsonl` |

规则（已确认）：

1. `len(history) >= 50` 触发压缩（按 messages 条数）  
2. 压缩成功后 working history 保留最近 **6** 条（并避免以 assistant / 裸 tool_result 开头）  
3. 记忆进 **system**；近期原始对话进 **messages**  
4. 压缩模型只输出约定 JSON（`mid_summary` / `long_term` / `open_todos` / `user_preferences_delta` / `discarded_notes`）  

实现：`src/bc_code_agent/memory.py`。本地 `sessions/` 已 gitignore，不上传隐私对话原文。

### 实录（2026-08-09，session=`20260809-181001`）

对话概要（多轮 + 工具，凑满 20 条 messages）：

```text
Enter a prompt: 帮我查询一下北京的天气
[Tool]: LoadSkill({'name': 'web-search'}) → WebSearch({'query': '北京今天天气'})
[Agent]: 白天晴好、傍晚有降水……（并说明缺少精确温湿度）

Enter a prompt: 再帮我查一下具体的气温和湿度吧
[Tool]: WebSearch({'query': '北京实时气温湿度 today'})
[Agent]: 观象台约 35.5℃，湿度约 54%，体感超 40℃……

Enter a prompt: 今天是几月几号？
[Tool]: Bash({'command': 'date "+%Y年%m月%d日 %A"'})
[Tool Result]: 2026年08月09日 Sunday
[Agent]: 今天是 2026年8月9日，星期日……

Enter a prompt: 最近的周杰伦演唱会是什么时候，我需要抢票，顺便介绍一下地点信息以及有多少林俊杰演唱会
[Tool]: WebSearch(周杰伦…) / WebSearch(林俊杰…) × 多次
[Agent]: 整理周杰伦《烟花》巡演与林俊杰 JJ20 信息，并提醒以官方抢票渠道为准

[Memory] compact trigger: history=20 >= 20
[Token] compact: in=6826 out=516
[Memory] discarded_notes: 丢弃了搜索结果的原始网页内容、完整URL列表、web-search技能说明全文……
[Memory] compact ok: mid+1 long updated; working history 20 -> 6

Enter a prompt: 你还记得你刚才干了什么吗
[Agent]: 记得——查了周杰伦/林俊杰演唱会、整理表格并提醒官方渠道抢票……
         （压缩后仍能依据 mid/long + 近 6 条原始回忆要点）
```

压缩后写入的中期摘要（`mid.jsonl` 摘录）：

```json
{
  "title": "查询北京天气及周杰伦/林俊杰演唱会信息",
  "summary": "用户要求查询北京天气……随后确认日期为2026年8月9日……周杰伦2026《烟花》巡演杭州站已结束……林俊杰JJ20 FINAL LAP 北京鸟巢为收官百场纪念站……",
  "key_facts": [
    "当前系统日期：2026年8月9日星期日",
    "北京近期天气：气温约35.5℃，体感超40℃，湿度约54%",
    "周杰伦2026《烟花》巡演：杭州站4月已结束，南京/南宁等站时间待定",
    "林俊杰JJ20 FINAL LAP巡演：北京鸟巢为收官百场纪念站"
  ],
  "decisions": [
    "查询实时/时效信息时使用web-search技能",
    "演唱会具体日期及抢票建议用户以官方渠道为准"
  ]
}
```

长期层（`long_term.json` 摘录）：

```json
{
  "constraints": [
    "搜索结果需如实总结，禁止编造……",
    "回复保持猫娘口吻，尊称用户为「主人」，结尾带「喵～」"
  ],
  "standing_facts": [
    "web-search技能包含具体使用规范……"
  ]
}
```

## Step 8：Todo 任务清单（A 档）

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

## Step 9：语义化工具 + 子 Agent（Task）

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

## Step 10：AgentTeam（长期协作）

短平快继续用 `Task`；需要多轮、队友互相对齐时用 **AgentTeam**。

| 项 | 说明 |
|---|---|
| 建队 | 无显式 CreateTeam；主 Agent `Spawn` 隐式建队或加人 |
| 单队 | 每个 session 同时最多一个 active team；换阵容先 `DisbandTeam` |
| 身份 | 主 Agent 填 profile 模板（`name` / `role` / `system` / `tools[]`），落盘到 `sessions/<id>/team/` |
| 队友工具 | 可选 Read/Write/Grep/Glob/WebSearch/LoadSkill；**禁止 Shell**；消息工具自动附带 |
| 主专属 | `Spawn` / `DisbandTeam` / `TodoWrite` / `TodoRead` / `Task` |
| 消息 | `SendMessage`（点对点，含队友互发）/ `Broadcast` / `ReadInbox` / `ListTeammates` |
| 唤醒 | 每个队友后台线程：inbox 有未读则跑 LLM loop，否则 `sleep(1)`（不烧 token） |

实现：`team_store.py`、`team_runtime.py`。

### Spawn 示例

```text
Spawn(
  name="调研官",
  role="查外网事实",
  system="先 LoadSkill(web-search)，再 WebSearch；结论附来源；向 lead 汇报",
  tools=["WebSearch", "LoadSkill", "Read"],
  brief="请查杭州本周末天气要点"
)
```

终端可见：`[Team] Spawn ...`、`[Team] wake ...`、`[队·调研官·WebSearch]`、`[Team] msg ... → lead`。

要点：

1. **Task vs Team**：一次性委派用 Task；长期协作、要互聊用 Spawn  
2. **自定义身份**：不强制 explore/general；模板写在 Spawn description 里供参考  
3. **Todo 仍只归主 Agent**：队友用消息汇报进度，由猫娘更新 Todo  
4. **斜杠命令**：`/listTeam` 看队伍；`/inbox <队友> <内容>` 以 lead 身份发消息（不经 LLM）  
5. **体验约定**：busy 时少轮询；交付后 `DisbandTeam`；`ReadInbox who=self` 可读自己的箱；已 `SendMessage` 给 lead 则不再 auto-report  

## Step 11：MCP Host（filesystem，仅主 Agent）

把 MCP server 的工具接到主 Agent，命名：`mcp__{server}__{tool}`。

| 项 | 说明 |
|---|---|
| 配置 | 项目根 `mcp.json`（Cursor 风格 `mcpServers`） |
| 默认 server | `npx -y @modelcontextprotocol/server-filesystem ${ROOT}`，沙箱=项目根 |
| 接入 | `mcp_hub.py`：后台 asyncio 线程保活 stdio Client；同步 `call_tool` |
| 范围 | **仅主 Agent**；Task / AgentTeam 暂不开放 |
| 与内置工具 | 日常读写仍用 `Read`/`Write`/`Grep`/`Glob`；需要 MCP 约定能力（如 `directory_tree`）再用 `mcp__*` |

启动时终端可见：`[MCP] connected filesystem (... tools)`。依赖：`pip install mcp`，本机需有 `npx`。

### 示例提示词

```text
请用 MCP filesystem 工具（名称以 mcp__filesystem__ 开头）完成下面任务，不要用内置 Read/Write/Grep/Glob：

1. 用 mcp__filesystem__directory_tree 查看项目根目录结构（深度适中即可）
2. 用 mcp__filesystem__list_directory 列出 src/bc_code_agent
3. 用 mcp__filesystem__read_text_file 读取 mcp.json
4. 用简短中文告诉我：MCP 配置了哪些 server、工具命名规则是什么
```

终端可见链路：

```text
[MCP] connected `filesystem` (14 tools) via npx
[mcp__filesystem__directory_tree]: {'path': '.../CodeAgent'}
[mcp__filesystem__list_directory]: {'path': '.../CodeAgent/src/bc_code_agent'}
[mcp__filesystem__read_text_file]: {'path': '.../CodeAgent/mcp.json'}
[Agent]: ... 配置了 1 个 server → filesystem；命名 mcp__{server}__{tool} ...
```

要点：

1. **启动先连 MCP**：看到 `connected filesystem` 再发提示词  
2. **强制走 mcp__\***：否则模型可能仍用内置 Read/Write  
3. **读到的 mcp.json**：只有 `filesystem` 一个 server，工具名形如 `mcp__filesystem__list_directory`

## Step 12：Hooks（生命周期拦截）

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

## Step 13：Session 续聊

对话工作记忆写入 `sessions/<id>/working.json`；`--session` 启动时读回 `history`（无快照则从 `transcript.jsonl` 降级重建）。不做进程内 `/resume`。

```bash
python3 src/bc_code_agent/start.py --list-sessions
python3 src/bc_code_agent/start.py --session 20260813-141850
```

同时恢复同目录下的 Todo / 长期记忆；若队伍仍为 active，重新拉起队友线程（进程退出不再 Disband，只停 worker）。

### 示例

先新开一轮，让模型记住暗号后 Ctrl+C：

```text
请记住一个暗号：蓝猫饼干。不要用工具，直接用一句话确认你记住了，喵～
```

再带 `--session` 接上（实测 session `20260813-141850`）：

```text
[Memory] session=20260813-141850 dir=.../sessions/20260813-141850
[Memory] restored 2 working message(s)
Enter a prompt: 暗号是什么
[Agent]: 主人，暗号是「蓝猫饼干」呀，喵～
```

要点：

1. **列表**：`--list-sessions` 不连模型、不连 MCP  
2. **快照**：运行中写入 `working.json`；恢复时读回 `history`  
3. **对照**：不带 `--session` 再开是新目录，不应再记得暗号  

## Step 14：Goal Loop（目标循环）

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

## Step 15：Permission 管道（声明式权限）

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

## Step 16：Background Shell（后台任务）

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

## Step 19：Cron 定时调度

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

## Step 20：Workflow Runtime（固定编排）

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
