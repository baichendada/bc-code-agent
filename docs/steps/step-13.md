# Step 13：Session 续聊

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

