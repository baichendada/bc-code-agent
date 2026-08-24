# Step 7：三层记忆与压缩

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

