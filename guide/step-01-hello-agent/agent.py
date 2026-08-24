"""Step 01 · 最小 Agent：一次单轮对话（流式输出）。

默认运行离线演示；--check 做无 API 自检；--real 调用 Anthropic 兼容接口。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable
from pathlib import Path

# guide/step-01-hello-agent/agent.py -> project root
ROOT = Path(__file__).resolve().parents[2]


def fake_stream(prompt: str) -> Iterable[str]:
    """模拟 messages.stream(...).text_stream 的增量形状。"""
    reply = (
        "我是运行在终端里的教学 Agent。"
        f"你刚才说的是：{prompt}"
    )
    for start in range(0, len(reply), 9):
        yield reply[start : start + 9]


def print_stream(chunks: Iterable[str]) -> str:
    """终端 Agent 的最小渲染：边生成边打印，最后返回完整文本。"""
    collected: list[str] = []
    for chunk in chunks:
        print(chunk, end="", flush=True)
        collected.append(chunk)
    print()
    return "".join(collected)


def real_stream(prompt: str) -> Iterable[str]:
    """真实 Anthropic Messages API 调用；--real 时才加载依赖和密钥。"""
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    model = os.getenv("ANTHROPIC_MODEL", "glm-5.2")
    max_tokens = int(os.getenv("MAX_TOKENS", "10000"))
    if not api_key:
        raise SystemExit("缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        yield from stream.text_stream


def check() -> None:
    chunks = list(fake_stream("你好"))
    assert chunks
    assert "".join(chunks).startswith("我是运行在终端里的教学 Agent。")
    print("step-01 check: ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="run offline assertions")
    parser.add_argument("--real", action="store_true", help="call the real API")
    args = parser.parse_args()

    prompt = "用两三句话介绍你自己：你是谁，能做什么？"
    if args.check:
        check()
    else:
        print_stream(real_stream(prompt) if args.real else fake_stream(prompt))
