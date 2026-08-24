"""Step 04 · 系统提示词：把身份和规则放在顶层 system 字段。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable


Message = dict[str, str]
QUIT_COMMANDS = {"q", "quit", "exit"}
DEFAULT_SYSTEM = (
    "你是 BC Guide，一个终端编程助教。"
    "用中文回答，每轮最多两句话。"
)


def fake_stream(system: str, messages: list[Message]) -> Iterable[str]:
    """根据 system 规则和 messages 历史生成确定性回答。"""
    current_user = messages[-1]["content"]
    remembered_name = None

    for message in messages[:-1]:
        if message["role"] == "user" and message["content"].startswith("我叫"):
            remembered_name = message["content"][2:].strip()

    if "超过 12 个字" in system and current_user == "我是谁？":
        reply = "我还没有你的名字。" if remembered_name is None else f"你是{remembered_name}。"
    elif current_user == "我是谁？":
        reply = f"根据历史，你是{remembered_name}。" if remembered_name else "历史里还没有你的名字。"
    elif current_user == "用一句话解释 system prompt":
        reply = "system 是请求顶层的身份和规则字段，messages 只保存对话历史。"
    elif current_user.startswith("我叫"):
        reply = f"已记住，{current_user[2:].strip()}。"
    else:
        reply = f"收到：{current_user}"

    for start in range(0, len(reply), 9):
        yield reply[start : start + 9]


class Agent:
    """持有 system 和 history，展示两者在每次请求中的分工。"""

    def __init__(self, system: str = DEFAULT_SYSTEM) -> None:
        self.system = system
        self.messages: list[Message] = []

    def ask(
        self,
        user_text: str,
        render: Callable[..., None] = print,
    ) -> str:
        self.messages.append({"role": "user", "content": user_text})

        chunks: list[str] = []
        for text in fake_stream(system=self.system, messages=self.messages):
            render(text, end="", flush=True)
            chunks.append(text)
        render()

        reply = "".join(chunks)
        self.messages.append({"role": "assistant", "content": reply})
        return reply


def run_agent(agent: Agent, read_line: Callable[[], str], render: Callable[..., None] = print) -> None:
    render("Step 04 系统提示词（exit/q 退出）")
    while True:
        try:
            line = read_line()
        except (EOFError, KeyboardInterrupt):
            render("")
            break
        if line.strip().lower() in QUIT_COMMANDS:
            break
        if not line.strip():
            continue
        agent.ask(line.strip(), render)
    render("bye")


def run_demo() -> None:
    default_agent = Agent()
    default_agent.ask("我叫小王")
    default_agent.ask("我是谁？")

    strict_agent = Agent("你是 BC Guide。任何回答不超过 12 个字。")
    strict_agent.ask("我是谁？")


def run_check() -> None:
    agent = Agent()
    agent.ask("我叫小王", lambda *args, **kwargs: None)
    agent.ask("我是谁？", lambda *args, **kwargs: None)
    assert agent.messages[-1]["content"] == "根据历史，你是小王。"
    assert all(message["role"] in {"user", "assistant"} for message in agent.messages)

    strict_agent = Agent("你是 BC Guide。任何回答不超过 12 个字。")
    assert strict_agent.ask("我是谁？", lambda *args, **kwargs: None) == "我还没有你的名字。"
    print("step-04 check: ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 04 system prompt")
    behavior = parser.add_mutually_exclusive_group()
    behavior.add_argument("--demo", action="store_true", help="run offline scripted chat")
    behavior.add_argument("--check", action="store_true", help="run stable offline checks")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.check:
        run_check()
    elif args.demo:
        run_demo()
    else:
        run_agent(Agent(), input)
