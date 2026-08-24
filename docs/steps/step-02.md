# Step 2 现象：能循环，但不记得上文


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

