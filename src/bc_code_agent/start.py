import os

from dotenv import load_dotenv

# 从项目根加载 .env（无论从哪启动）
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_PATH = os.path.join(_ROOT, ".env")
if not os.path.isfile(_ENV_PATH):
    raise SystemExit(
        f"未找到 {_ENV_PATH}\n"
        "请在项目根创建 .env（可参考 README），至少包含：\n"
        "  LLM_PROVIDER=anthropic\n"
        "  ANTHROPIC_AUTH_TOKEN=...\n"
        "  ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic\n"
        "  ANTHROPIC_MODEL=glm-5.2"
    )
load_dotenv(_ENV_PATH, override=True)

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "10000"))


def run_anthropic(user_input: str) -> None:
    import anthropic

    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    model = os.getenv("ANTHROPIC_MODEL")

    if not api_key:
        raise SystemExit(f"缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY（检查 {_ENV_PATH}）")
    if not model:
        raise SystemExit(f"缺少 ANTHROPIC_MODEL（检查 {_ENV_PATH}）")

    client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None)

    print("[Agent]: ", end="", flush=True)
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": user_input}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
    print()


def run_openai(user_input: str) -> None:
    from openai import OpenAI

    api_key = os.getenv("OPENCODE_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")
    model = os.getenv("OPENCODE_MODEL", "deepseek-v4-flash")

    if not api_key:
        raise SystemExit("缺少 OPENCODE_API_KEY / OPENAI_API_KEY")

    client = OpenAI(api_key=api_key, base_url=base_url)

    stream = client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": user_input}],
        stream=True,
    )

    print("[Agent]: ", end="", flush=True)
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end="", flush=True)
    print()


def main() -> None:
    user_input = input("Enter a prompt: ")
    if PROVIDER in {"anthropic", "a", "claude"}:
        run_anthropic(user_input)
    elif PROVIDER in {"openai", "opencode", "o"}:
        run_openai(user_input)
    else:
        raise SystemExit(f"未知 LLM_PROVIDER={PROVIDER!r}，请用 anthropic 或 openai")


if __name__ == "__main__":
    main()
