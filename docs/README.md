# bc-code-agent 步骤详解

逐步实现笔记（每步含设计说明 + 真实会话实录）。主 README 只保留概览与快速开始，详细内容都在这里。

## 步骤索引

| 步骤 | 文件 | 一句话 |
|---|---|---|
| Step 1 | （见 step-02/03） | 单轮对话 + 流式输出（Messages API 第一版，无独立章节） |
| Step 2–3 | [`step-02`](steps/step-02.md) · [`step-03`](steps/step-03.md) | 输入循环 → 带 history 的多轮记忆（循环 ≠ 记忆） |
| Step 4 | [`step-04`](steps/step-04.md) | system 提示词（人设 / 行为约束） |
| Step 5 | [`step-05`](steps/step-05.md) | 工具调用（tool_use / tool_result 循环） |
| Step 6 | [`step-06`](steps/step-06.md) | Skill 系统（渐进式披露 + LoadSkill + web-search 示例） |
| Step 7 | [`step-07`](steps/step-07.md) | 三层记忆 + 压缩 + Token 计量 |
| Step 8 | [`step-08`](steps/step-08.md) | Todo 任务清单 |
| Step 9 | [`step-09`](steps/step-09.md) | 语义化工具 + Task 子 Agent（explore/general/review/research） |
| Step 10 | [`step-10`](steps/step-10.md) | AgentTeam（Spawn 队友 + mailbox + 消息工具） |
| Step 11 | [`step-11`](steps/step-11.md) | MCP Host（filesystem，仅主 Agent） |
| Step 12 | [`step-12`](steps/step-12.md) | Hooks（Event→Matcher→Handler→Decision） |
| Step 13 | [`step-13`](steps/step-13.md) | Session 续聊（--session / --list-sessions） |
| Step 14 | [`step-14`](steps/step-14.md) | Goal Loop（可验证终点 + 独立评估器） |
| Step 15 | [`step-15`](steps/step-15.md) | Permission 管道（allow/ask/deny + YOLO） |
| Step 16 | [`step-16`](steps/step-16.md) | Background Shell（后台任务 + 完成注入 + Goal defer） |
| Step 17/18 | （暂缓，见主 README） | Task 图 + Team v2 —— 多执行者协作方向，将来想做再补 |
| Step 19 | [`step-19`](steps/step-19.md) | Cron 定时调度（三线程模型 + 定时轮权限 fail-closed） |
| Step 20 | [`step-20`](steps/step-20.md) | Workflow Runtime（YAML 编排 + parallel/pipeline + schema + journal 审计） |
