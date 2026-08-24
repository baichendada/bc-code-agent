# Step 10：AgentTeam（长期协作）

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

