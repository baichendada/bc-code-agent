# bc-code-agent

逐步做一个属于白晨的 Agent。

## 进度

- [x] **Step 1**：单轮对话（Anthropic Messages API，流式输出）
- [x] **Step 2**：输入循环（`while True`），但**无会话记忆**
- [x] **Step 3**：用 `history` 维护多轮对话（有记忆）
- [x] **Step 4**：`system` 提示词（人设 / 行为约束）
- [x] **Step 5**：工具调用（`tools` + `tool_use` / `tool_result` 循环）
- [x] **Step 6**：Skill 系统（渐进式披露 + `LoadSkill` + 示例 `web-search`）
- [x] **Step 7**：三层记忆 + 压缩（history≥20 触发，保留近 6 条）+ Token 计量
- [x] **Step 8**：Todo 任务清单（`TodoWrite` / `TodoRead`，防复杂任务迷路）
- [ ] **TODO（重要，稍后做）**：工具权限管理  
  - Bash 等危险工具需要 `allow` / `ask` / `deny`，否则模型可直接改文件、跑命令  
  - 当前为学习阶段故意先不做；**不要上生产 / 别对重要目录裸跑**

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
  start.py                # LoadSkill + WebSearch + Bash
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

1. `len(history) >= 20` 触发压缩（按 messages 条数）  
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

## 快速开始

```bash
pip3 install -r requirements.txt   # 含 anthropic / python-dotenv / ddgs
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
```

```bash
python3 src/bc_code_agent/start.py
```

说明：

- `.env` 会覆盖 shell / `~/.zshrc` 里同名的 `ANTHROPIC_*`
- 智谱 Anthropic 接口模型名用 `glm-5.2`（不要用 Claude Code 别名 `glm-5.2[1M]`）
