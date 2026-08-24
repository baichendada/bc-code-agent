# Step 11：MCP Host（filesystem，仅主 Agent）

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

