# Step 5：工具调用

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

