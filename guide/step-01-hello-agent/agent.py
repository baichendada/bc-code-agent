"""Step 1 · 最小 Agent：一次单轮对话（流式输出）。

用法（在项目根目录执行）：
    python guide/step-01-hello-agent/agent.py
"""

import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# guide/step-01-hello-agent/agent.py → 项目根
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)

API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("ANTHROPIC_MODEL", "glm-5.2")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "10000"))

if not API_KEY:
    raise SystemExit(
        "缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY："
        f"请在 {ROOT / '.env'} 里配置（参考 guide/README.md）"
    )

client = anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL or None)


def ask(prompt: str) -> str:
    """把一句用户输入发给模型，流式打印并返回完整回答。"""
    chunks: list[str] = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            chunks.append(text)
    print()  # 收尾换行
    return "".join(chunks)


if __name__ == "__main__":
    ask("用两三句话介绍你自己：你是谁，能做什么？")
