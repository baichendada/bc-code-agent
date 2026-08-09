import os

from dotenv import load_dotenv
import anthropic

# 从项目根加载 .env（无论从哪启动）
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ENV_PATH = os.path.join(_ROOT, ".env")
if not os.path.isfile(_ENV_PATH):
    raise SystemExit(
        f"未找到 {_ENV_PATH}\n"
        "请在项目根创建 .env（可参考 README），至少包含：\n"
        "  ANTHROPIC_AUTH_TOKEN=...\n"
        "  ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic\n"
        "  ANTHROPIC_MODEL=glm-5.2"
    )
# override=True：让项目 .env 覆盖 shell 里的 ANTHROPIC_*（如 ~/.zshrc 的 mcli）
load_dotenv(_ENV_PATH, override=True)

API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("ANTHROPIC_MODEL")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "10000"))
THINKING_TYPE = os.getenv("THINKING_TYPE", "enabled")
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "high")

if not API_KEY:
    raise SystemExit(f"缺少 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY（检查 {_ENV_PATH}）")
if not MODEL:
    raise SystemExit(f"缺少 ANTHROPIC_MODEL（检查 {_ENV_PATH}）")

client = anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL or None)

SYSTEM_PROMPT = """
    你是一只猫娘，侍奉主人多年，忠心耿耿
    说话风格必须符合猫娘，语气要温柔，要符合猫娘的性格
    你必须尊称用户为主人
    每次回复后必须有固定后缀"喵～"
    使用中文回复
"""

history = []

while True:
    user_input = input("Enter a prompt: ")
    if not user_input.strip():
        continue

    history.append({"role": "user", "content": user_input})

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=history,
        system=SYSTEM_PROMPT,
        extra_body={
            "thinking": {"type": THINKING_TYPE},
            "reasoning_effort": REASONING_EFFORT,
        },
    )

    reply = next((b.text for b in message.content if b.type == "text"), "")
    print(f"[Agent]: {reply}\n")
    history.append({"role": "assistant", "content": reply})
