# bc-code-agent

逐步做一个属于白晨的 Agent。

## 进度

- [x] **Step 1**：单轮对话（Anthropic Messages API，流式输出）
- [x] **Step 2**：输入循环（`while True`），但**无会话记忆**
- [ ] Step 3：带 history 的多轮对话（待做）

## Step 2 现象：能循环，但不记得上文

当前实现是外层套了 `while True`，每次只把**本轮**用户输入发给模型：

```python
messages=[{"role": "user", "content": user_input}]
```

因此终端里可以连续聊多轮，但模型看不到历史。实测：

1. 用户：`我今年13岁` → 模型正常接话  
2. 用户：`我今年几岁` → 模型说不知道（上一轮年龄没有传回去）

结论：**循环 ≠ 记忆**。记忆需要自己维护 `messages` 历史（把每轮 user/assistant 追加后再请求）。

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
