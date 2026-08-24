"""Step 02 · 输入循环：用 REPL 让程序持续运行。

默认进入交互模式；--demo 使用离线脚本；--check 做无 API 自检。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable


QUIT_COMMANDS = {"q", "quit", "exit"}


def fake_stream(prompt: str) -> Iterable[str]:
    """以 text_stream 的形状返回离线增量，方便先跑通 REPL。"""
    if prompt == "我刚是谁？":
        reply = "这轮请求只有你当前这句话，所以我不知道上一轮内容。"
    elif prompt == "现在几点？":
        reply = "我看不到宿主进程的时钟；这要由 Harness 提供时间工具。"
    else:
        reply = f"收到：{prompt}"

    for start in range(0, len(reply), 9):
        yield reply[start : start + 9]


def is_quit_command(line: str) -> bool:
    return line.strip().lower() in QUIT_COMMANDS


def run_repl(
    read_line: Callable[[], str],
    render: Callable[..., None] = print,
) -> list[str]:
    """运行最小 REPL，返回本轮程序内产生过的用户输入。"""
    accepted: list[str] = []
    render("Step 02 输入循环（exit/q 退出）")

    while True:
        try:
            line = read_line()
        except (EOFError, KeyboardInterrupt):
            render("")
            break

        if is_quit_command(line):
            break
        if not line.strip():
            continue

        accepted.append(line.strip())
        for chunk in fake_stream(line.strip()):
            render(chunk, end="", flush=True)
        render()

    render("bye")
    return accepted


def run_demo() -> None:
    """无 API 的固定输入演示，重点观察循环和退出。"""
    scripted_input = iter(["你是谁？", "", "我刚是谁？", "exit"])
    run_repl(scripted_input.__next__)


def run_check() -> None:
    """稳定自检：命令识别、空行过滤、EOF 退出和流式输出。"""
    assert is_quit_command(" exit ")
    assert not is_quit_command("")
    assert "".join(fake_stream("我刚是谁？")) == "这轮请求只有你当前这句话，所以我不知道上一轮内容。"

    seen: list[str] = []
    run_repl(
        iter(["你好", "", "exit"]).__next__,
        lambda text="", **_: seen.append(text),
    )
    assert seen[-1] == "bye"
    assert len([line for line in seen if line.startswith("收到：")]) == 1

    print("step-02 check: ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 02 input loop")
    behavior = parser.add_mutually_exclusive_group()
    behavior.add_argument("--demo", action="store_true", help="run scripted offline input")
    behavior.add_argument("--check", action="store_true", help="run stable offline checks")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.check:
        run_check()
    elif args.demo:
        run_demo()
    else:
        run_repl(input)
