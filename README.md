# bc-code-agent

逐步做一个属于白晨的 Agent。

## 进度

- [x] **Step 1**：单轮对话（Anthropic Messages API，流式输出）
- [x] **Step 2**：输入循环（`while True`），但**无会话记忆**
- [x] **Step 3**：用 `history` 维护多轮对话（有记忆）
- [ ] Step 4：工具调用（待做）

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

## 快速开始

```bash
pip3 install -r requirements.txt
```

在项目根创建 `.env`（已 gitignore，勿提交密钥）：

```bash
ANTHROPIC_AUTH_TOKEN=your-token
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_MODEL=glm-5.2
MAX_TOKENS=10000
```

```bash
python3 src/bc_code_agent/start.py
```

说明：

- `.env` 会覆盖 shell / `~/.zshrc` 里同名的 `ANTHROPIC_*`
- 智谱 Anthropic 接口模型名用 `glm-5.2`（不要用 Claude Code 别名 `glm-5.2[1M]`）
