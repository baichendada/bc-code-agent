# Step 3：带 history 的多轮对话

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

