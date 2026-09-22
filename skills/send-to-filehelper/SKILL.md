---
name: send-to-filehelper
description: Send local files to WeChat "文件传输助手" (File Transfer Helper) or a named contact/group on Windows via wxauto4 / 在 Windows 上通过 wxauto4 把本地文件发送到微信「文件传输助手」或指定好友、群聊。当用户说“把文件发到微信”“发送到文件传输助手”“传到手机”“发给我自己”“把这些文件发到微信里”时使用。
metadata:
  author: 邱张华(qiuzhanghua@msn.com)
license: MIT
---

# Send to FileHelper / 发送文件到微信文件传输助手

## Overview / 概述

This skill sends local files to WeChat's **文件传输助手** (File Transfer Helper) — or to a named
contact/group — on a Windows machine where the WeChat PC client (4.x) is installed and logged in.
It is built on [wxauto4](https://pypi.org/project/wxauto4/), which drives the already-running WeChat
UI through Windows UI Automation.

本 skill 在 Windows 上把本地文件发送到微信「文件传输助手」（或指定好友/群聊）。底层使用
wxauto4，通过 Windows UI Automation 操作用户**已经登录**的微信 PC 客户端界面，不做任何协议
破解、注入或绕过微信限制的行为。

The typical use case: a file produced on the computer (build artifact, report, PDF, image, log) is
pushed into WeChat so it can be picked up on the phone or on another machine.

典型场景：把电脑上生成的文件（构建产物、报告、PDF、截图、日志）发进微信，方便在手机或其他
设备上取用。

## When to use / 触发场景

- "把这个 PDF 发到文件传输助手" / "把 dist 目录里的文件传到微信" / "发我微信上"
- "send this file to WeChat" / "push the build artifact to my File Transfer Helper"
- "把这几个文件发给我自己"、备份小文件到微信、把截图发到微信

Do **not** use this skill for: batch marketing, mass messaging, sending to strangers, or any
automated outreach. It is intended only for the user's own account and conversations.

## Requirements / 环境要求

| Item | Requirement |
| --- | --- |
| OS | Windows 10 / 11 (Windows Server 2016+ also works). **Not supported on macOS / Linux.** |
| WeChat | Windows 版微信客户端 **4.x**，已启动并登录，主窗口处于可交互状态 |
| Python | 由 `uv` 自动管理；脚本声明 `requires-python = ">=3.10,<3.13"` |
| Dependency | `wxauto4`（在 Windows 上由 uv 自动安装；其他平台自动跳过） |
| Tool | 已安装 [`uv`](https://docs.astral.sh/uv/) |

> wxauto4 免费版适配微信 4.1.x；微信客户端版本过新或过旧都可能找不到控件。若失败，先升级
> / 降级微信客户端再试。

## Usage / 使用方法

Run the script from this skill's directory with `uv` (uv installs the dependency automatically
from the inline metadata — no manual `pip install` needed):

在本 skill 目录下用 `uv` 直接运行脚本，依赖由脚本头部的内联元数据自动安装：

```bash
# 发送单个文件到「文件传输助手」
uv run scripts/send_to_filehelper.py "C:\work\报告.pdf"

# 发送多个文件 / 通配符 / 整个目录
uv run scripts/send_to_filehelper.py ./dist/*.zip
uv run scripts/send_to_filehelper.py ./out --recursive

# 先发一条文字说明，再发文件
uv run scripts/send_to_filehelper.py ./build/app.exe --message "最新构建产物"

# 发送给指定好友或群聊（发送前请与用户确认名称）
uv run scripts/send_to_filehelper.py ./report.xlsx --to "张三"

# 只检查将要发送什么，不操作微信（可在任意平台运行）
uv run scripts/send_to_filehelper.py ./out --dry-run

# 机器可读的结果摘要
uv run scripts/send_to_filehelper.py ./out --json
```

### Minimal example / 最简示例

```python
from wxauto4 import WeChat

wx = WeChat()
wx.SendFiles(r'C:\你的文件路径\报告.pdf', '文件传输助手')
```

The script is essentially a hardened version of the snippet above.

### Recommended agent workflow / 推荐的执行流程

1. 确认文件确实存在（必要时先 `ls`/`dir` 或先跑 `--dry-run`），并展开目录/通配符；
2. 默认目标固定为「文件传输助手」；
3. **只有当用户明确要求发给某个人/群时才使用 `--to`，并且先把解析到的昵称回显给用户确认**；
4. 执行脚本，检查退出码与输出的校验结果；
5. 失败时按下方「Troubleshooting」逐条排查，不要盲目重试（重复发送会产生重复文件消息）。

## Parameters / 参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `FILE...` | 必填 | 一个或多个文件路径；支持 `~`、环境变量、`*` / `?` / `[` 通配符和目录 |
| `-t, --to` | `文件传输助手` | 目标会话：文件传输助手 / 好友昵称 / 群聊名称 |
| `--exact / --no-exact` | `--exact` | 是否精确匹配会话名，默认开启，避免发错人 |
| `-m, --message` | 无 | 发送文件前先发一条文本消息（如来源、说明） |
| `--one-by-one` | 关闭 | 逐个文件发送；默认一次性提交多个文件 |
| `--delay` | `1.0` | 批次之间、以及发送前后等待的秒数 |
| `-r, --recursive` | 关闭 | 输入是目录时递归收集子目录中的文件 |
| `--max-size-mb` | `100` | 超过此大小给出提示（微信客户端可能拒收大文件） |
| `--retries` | `0` | 发送失败后的重试次数（重试可能导致重复消息，谨慎使用） |
| `--no-verify` | 关闭 | 跳过发送后的消息校验 |
| `--dry-run` | 关闭 | 只打印待发送清单，完全不操作微信，可在非 Windows 平台执行 |
| `--json` | 关闭 | 以 JSON 输出结果摘要（`requested` / `submitted` / `confirmed` / `errors`） |

## Technical Implementation / 技术实现

脚本对 wxauto4 的调用流程（每一步都带校验，避免“发错人”）：

1. **展开输入**：`~`、环境变量、通配符、目录（可选递归），按绝对路径去重；
2. **连接微信**：`WeChat()`；未启动/未登录时捕获异常并给出可执行的排查提示；
3. **切换会话**：`wx.ChatWith(target, exact=True)`；
4. **校验会话**：`wx.ChatInfo()['chat_name']` 必须与目标一致，否则**中止发送**并列出候选会话名；
5. **发送文本（可选）**：`wx.SendMsg(message)`；
6. **发送文件**：`wx.SendFiles([...绝对路径...])`（此时已确认当前会话正确，因此不重复传 `who`）；
7. **校验结果**：`wx.GetAllMessage()` 中查找 `type == 'file'` 且内容包含文件名的消息；
8. **汇总输出**：人类可读摘要 + `--json`。

`SendFiles` 的返回值在不同 wxauto4 版本中可能是 `WxResponse(status=成功/失败, message=...)`，
也可能是 `None`。脚本对两种情形都做了处理：显式失败 → 失败；返回 `None` → 交给第 7 步的消息
校验判定。

### Exit codes / 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 文件已提交，且消息校验通过（或用户使用 `--no-verify` 主动跳过校验） |
| `1` | 文件不存在 / 非 Windows / 微信不可用 / 会话不匹配 / 发送失败 / 校验未发现文件消息 |

## Example Output / 示例输出

```
目标会话: 文件传输助手（精确匹配: 是）
待发送: 2 个文件，共 195.3 KB
  - C:\work\dist\data.bin  (195.3 KB)
  - C:\work\dist\note.txt  (1 B)
已切换到会话: 文件传输助手
已发送文本消息: 构建产物
[1/1] 发送 2 个文件: data.bin、note.txt
    已提交: data.bin、note.txt

====================================================
目标会话 : 文件传输助手
已提交   : 2/2 个文件
已确认全部 2 个文件出现在会话中
完成
```

## Troubleshooting / 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 非 Windows 平台运行 | 本 skill 不支持；把文件拷到 Windows 机器上执行，或用 `--dry-run` 仅确认清单 |
| `无法连接微信 PC 客户端` | 启动并登录微信 4.x；主窗口不要最小化到托盘；确认微信版本与 wxauto4 兼容 |
| `当前会话是「X」，与目标「Y」不一致` | 会话名不精确；用输出的候选列表修正 `--to`，或使用完整备注名；确认无误后可临时 `--no-exact` |
| `发送后未在会话中发现任何文件消息` | 微信可能弹出了对话框/被遮挡；保持窗口在前台，去掉锁屏与远程桌面断开，然后重试 |
| 微信提示文件过大 | 微信客户端对大文件有限制（默认超过 `--max-size-mb` 会提示）；改用其他传输方式 |
| 找不到控件 / UI 元素已失效 | 关掉微信其他弹出窗口，重新登录微信；远程桌面断开（会话锁屏）时 UI Automation 不可用 |
| 中文乱码 | 脚本已强制 UTF-8 输出；如仍异常，在 `cmd` 中执行 `chcp 65001` 后重试 |

## Notes / 注意事项

1. **隐私与合规**：只操作本人已登录的账号；默认只发「文件传输助手」。发给他人前必须由用户
   明确指定并确认昵称。禁止用于群发、营销或骚扰。
2. **不要在发送过程中抢操作鼠标键盘**：wxauto4 通过模拟剪贴板粘贴 + 点击发送按钮工作，发送
   期间的人为干扰可能导致内容发错。
3. **文件必须在运行微信的这台 Windows 机器上可读**，使用绝对路径；超长路径（>260 字符）可能
   失败。
4. **一次只跑一个 wxauto4 进程**，避免多个脚本同时操作同一个微信窗口。
5. 发送成功只代表消息已提交给微信；真正的手机端接收依赖微信自身的同步。
6. `--dry-run` 可用于在非 Windows 环境（含 macOS/Linux）验证文件解析与清单。
