# bc-code-agent

逐步做一个属于白晨的 Agent。

## 进度

- [x] **Step 1**：单轮对话（流式输出）
  - 支持 `anthropic`（Messages API）与 `openai`（Chat Completions）双协议
  - 通过项目根目录 `.env` 的 `LLM_PROVIDER` 切换
- [ ] Step 2：多轮会话 / Agent 循环（待做）

## 快速开始

```bash
pip3 install -r requirements.txt
```

在项目根创建 `.env`（已 gitignore，勿提交密钥）：

```bash
# anthropic | openai
LLM_PROVIDER=anthropic
MAX_TOKENS=10000

# Anthropic 兼容（如智谱 BigModel）
ANTHROPIC_AUTH_TOKEN=your-token
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_MODEL=glm-5.2

# OpenAI 兼容（如 OpenCode Go；切到 openai 时填）
OPENCODE_API_KEY=
OPENCODE_MODEL=deepseek-v4-flash
OPENCODE_BASE_URL=https://opencode.ai/zen/go/v1
```

```bash
python3 src/bc_code_agent/start.py
```

说明：

- `.env` 会以 `override=True` 加载，覆盖 shell / `~/.zshrc` 里同名的 `ANTHROPIC_*`
- 智谱 Anthropic 接口模型名用 `glm-5.2`（不要用 Claude Code 别名 `glm-5.2[1M]`）
- 切到 OpenAI：设 `LLM_PROVIDER=openai` 并填好 `OPENCODE_API_KEY`
