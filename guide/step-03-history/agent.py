"""Step 03 · 多轮记忆：显式维护 user/assistant history。"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Iterable


Message = dict[str, str]
QUIT_COMMANDS = {"q", "quit", "exit"}


def fake_stream(messages: list[Message]) -> Iterable[str]:
    """按完整 messages 生成确定性回答，模拟模型读取历史。"""
    current_user = messages[-1]["content"]
    liked_number = None

    for message in messages[:-1]:
        if message["role"] != "user":
            continue
        match = re.search(r"我喜欢的数字是\s*(\d+)", message["content"])
        if match:
            liked_number = match.group(1)

    if "我喜欢的数字是什么" in current_user:
        reply = (
            f"你刚才说你喜欢的数字是 {liked_number}。"
            if liked_number is not None
            else "历史里还没有你喜欢的数字。"
        )
    elif liked_number is not None:
        reply = f"已记住数字 {liked_number}。"
    else:
        reply = f"收到：{current_user}"

    for start in range(0, len(reply), 9):
        yield reply[start : start + 9]


def chat(
    history: list[Message],
    user_text: str,
    render: Callable[..., None] = print,
) -> str:
    """执行一轮对话：append user，流式输出，再 append assistant。"""
    history.append({"role": "user", "content": user_text})

    chunks: list[str] = []
    for text in fake_stream(history):
        render(text, end="", flush=True)
        chunks.append(text)
    render()

    reply = "".join(chunks)
    history.append({"role": "assistant", "content": reply})
    return reply


def run_chat(read_line: Callable[[], str], render: Callable[..., None] = print) -> None:
    render("Step 03 多轮记忆（exit/q 退出）")
    history: list[Message] = []

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
        chat(history, line.strip(), render)

    render("bye")


def run_demo() -> None:
    scripted_input = iter(
        [
            "我喜欢的数字是 7",
            "我喜欢的数字是什么？",
            "exit",
        ]
    )
    run_chat(scripted_input.__next__)


def run_check() -> None:
    """验证多轮追加、历史读取和角色序列。"""
    history: list[Message] = []
    chat(history, "我喜欢的数字是 7", lambda *args, **kwargs: None)
    assert chat(history, "我喜欢的数字是什么？", lambda *args, **kwargs: None) == (
        "你刚才说你喜欢的数字是 7。"
    )
    assert [message["role"] for message in history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert all(set(message) == {"role", "content"} for message in history)
    print("step-03 check: ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 03 history")
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
        run_chat(input)
