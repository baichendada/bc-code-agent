# Step 1 · 最小 Agent：一次对话

## 本步目标

写一个 40 行左右的 Python 程序，它只做一件事：**把一句话发给大模型 API，把回答流式打到终端，然后退出**。

跑起来是这样：

```text
$ python guide/step-01-hello-agent/agent.py
我是由Z.ai训练的大型语言模型，也就是你们常说的AI助手。
我可以回答你的问题、帮你写东西、翻译语言，也可以和你一起头脑风暴、出谋划策。

今天你想聊点什么呢？
```

（这是本教程实跑的输出，逐字打出；你那边的内容会略有不同。）

这是 Agent 的"受精卵"：还没有循环、没有记忆、没有工具、没有人设，但它已经具备 Agent 最重要的两件事——

- 一个会推理的**大脑**（远端的大模型）
- 一个你亲手写的**宿主程序**（本地 Python，负责输入输出）

后面 19 步，都是往这个骨架上加器官。

## 准备

- Python 3.10+
- 项目根目录执行过 `pip install -r requirements.txt`（本章实际只用到 `anthropic` 和 `python-dotenv`）
- `.env` 已按 [guide/README.md](../README.md) 配好

## 原理

### 一次请求的最小形态

所有 Agent 和模型的交互，都从这样一次调用开始：

```python
client.messages.create(
    model="glm-5.2",
    max_tokens=10000,
    messages=[{"role": "user", "content": "你好"}],
)
```

三个必填件：

| 参数 | 含义 | 在本教程后面的演化 |
|---|---|---|
| `model` | 用哪个模型 | 基本不变 |
| `max_tokens` | 回答最多多长 | 基本不变 |
| `messages` | 对话"剧本" | **整个教程的主角** |

`messages` 是一个列表，每个元素是一条 `{"role": ..., "content": ...}`。现在剧本里只有一句台词；Step 3 的记忆、Step 5 的工具结果、Step 4 的人设，最终都会变成对这一个列表的增删改。记住这句话：**Agent 的"状态"，绝大部分就活在这个列表里**。

### 为什么要流式

SDK 提供两种拿回答的方式：

- `client.messages.create(...)`：阻塞，等模型把整段回答生成完，一次性返回
- `client.messages.stream(...)`：边生成边给，你的 `for` 循环里每个 token 都能立刻拿到

对终端 Agent 来说流式几乎是必选——没人愿意盯着空白屏幕等半分钟，然后"啪"地蹦出一大段。Claude Code 那种逐字打出来的效果，用的就是流式。

### 为什么要 `.env`

密钥（API Key）绝不能写进代码。`.env` 文件放本地、进 `.gitignore`，程序用 `python-dotenv` 读进来。这样代码可以放心开源，密钥永远留在你自己机器上。

## 动手实现

### 1. 装依赖、建客户端

```python
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

client = anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL or None)
```

几个细节：

- `Path(__file__).resolve().parents[2]`：从当前文件往上走两级到项目根，这样**在任意目录执行都能找到 `.env`**
- `base_url=BASE_URL or None`：没配就传 `None`，SDK 自动用官方地址；配了就走你的兼容端点（比如智谱）
- 支持 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` 两个名字，兼容官方和兼容网关两种习惯

### 2. 流式调用

```python
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
    print()
    return "".join(chunks)
```

三件事：

1. `messages` 里只有一条 user 消息——所以叫"单轮"
2. `stream.text_stream` 每次吐一小段文字增量，`print(..., end="", flush=True)` 让终端像打字机一样逐字显示
3. 一边打印一边攒进 `chunks`，最后拼成完整回答返回——这个返回值在 Step 3 会变成记忆

## 完整代码

[agent.py](agent.py)，全文 40 行左右。

## 运行与验收

在项目根目录执行：

```bash
python guide/step-01-hello-agent/agent.py
```

**验收标准**（三条全过才算学会）：

1. 终端**逐字**打出一段自我介绍，不是憋一屏后一次性蹦出
2. 程序正常退出，没有报错
3. 把最后一行 `ask(...)` 里的提问换掉，回答内容会跟着变

## 常见坑

| 现象 | 原因 | 解法 |
|---|---|---|
| `401` / 认证失败 | token 没读到或填错 | 检查 `.env` 的 `ANTHROPIC_AUTH_TOKEN`；确认执行目录、确认没有多余引号 |
| `404` / `model not found` | 模型名不对 | `ANTHROPIC_MODEL` 用网关要求的名字，别带 `[1M]` 这种客户端别名 |
| 连接超时 | `base_url` 配错或网络不通 | 核对 `ANTHROPIC_BASE_URL`，本地测试可以先 `curl` 一下 |
| `python3: command not found` | Windows PowerShell 下没有 `python3` | 用 `python` |
| 报错缺 `anthropic` / `dotenv` | 依赖没装 | 回项目根执行 `pip install -r requirements.txt` |

## 你学到了什么

- Agent = 你写的宿主程序 + 远端模型，靠 Messages API 连接
- `messages` 列表是 Agent 状态的容器，后面所有功能都在操作它
- 流式输出 (`messages.stream` + `text_stream`) 是终端 Agent 的标配
- 密钥走 `.env`，代码保持可发布

## 下一步预告

现在程序一问一答就退出了，一点"Agent 味"都没有。Step 2 会加一个 `while True`，让它变成可以一直聊的终端程序。

但你会发现一个反直觉的现象：明明是同一个进程、连续聊了好几轮，它却**完全不记得**你上一句说了什么。

为什么？Step 3 揭晓。
