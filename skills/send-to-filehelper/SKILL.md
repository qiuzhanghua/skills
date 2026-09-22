---
name: send-to-filehelper
description: Send local files to WeChat "文件传输助手" (File Transfer Helper) or a named contact on Windows and macOS / 在 Windows 与 macOS 上把本地文件发送到微信「文件传输助手」或指定好友、群聊。当用户说“把文件发到微信”“发送到文件传输助手”“传到手机”“发给我自己”“把这些文件发到微信里”时使用。
metadata:
  author: 邱张华(qiuzhanghua@msn.com)
license: MIT
---

# Send to FileHelper / 发送文件到微信文件传输助手

## Overview / 概述

This skill sends local files to WeChat's **文件传输助手** (File Transfer Helper) — or to a named
contact/group — by driving the **already logged-in WeChat desktop client** of the user. Two
platform backends share one CLI:

| Platform | Backend | Mechanism |
| --- | --- | --- |
| Windows | [`wxauto4`](https://pypi.org/project/wxauto4/) | Windows UI Automation |
| macOS | `pyobjc` (Accessibility API) | macOS AX tree + clipboard paste + synthetic keys |

本 skill 把本地文件发送到微信「文件传输助手」（或指定好友/群聊），两个平台共用同一个命令行：

| 平台 | 后端 | 原理 |
| --- | --- | --- |
| Windows | wxauto4 | Windows UI Automation |
| macOS | pyobjc（Accessibility API） | 读取微信 4.x 辅助功能树 + 剪贴板粘贴 + 合成键鼠事件 |

Both backends only operate the user's own logged-in client: no protocol reverse-engineering, no
injection, no bypassing of WeChat limits. 两个后端都只操作用户本人已登录的客户端界面，不做协议
破解、不注入、不绕过微信限制。

Typical use case: a file produced on the computer (build artifact, report, PDF, screenshot, log) is
pushed into WeChat so it can be picked up on the phone or on another machine.

典型场景：把电脑上生成的文件（构建产物、报告、PDF、截图、日志）发进微信，方便在手机或其他设备
上取用。

## When to use / 触发场景

- "把这个 PDF 发到文件传输助手" / "把 dist 目录里的文件传到微信" / "发我微信上"
- "send this file to WeChat" / "push the build artifact to my File Transfer Helper"
- "把这几个文件发给我自己"、备份小文件到微信、把截图发到微信

Do **not** use this skill for batch marketing, mass messaging, sending to strangers, or any
automated outreach. It is intended only for the user's own account and conversations.

## Requirements / 环境要求

| Item | Windows | macOS |
| --- | --- | --- |
| OS | Windows 10 / 11 | macOS（针对 macOS 15 + 微信 4.1.13 编写；需授予辅助功能权限后自测） |
| Client | Windows 版微信 **4.x**，已登录，主窗口未最小化 | 微信 Mac 版 **4.x**（`WeChat.app`），已登录 |
| Python | 由 `uv` 自动管理（`>=3.10,<3.13`） | 同左 |
| Dependency | `wxauto4`（uv 自动安装） | `pyobjc-framework-Cocoa / Quartz / ApplicationServices`（uv 自动安装） |
| Permission | 无（UI Automation 不需要额外授权） | **必须**授予「辅助功能」权限给运行命令的宿主 App |
| Tool | [`uv`](https://docs.astral.sh/uv/) | 同左 |

Linux is not supported. Linux 不支持。

### macOS permission setup / macOS 权限设置

macOS 的辅助功能权限是硬性要求（读取微信界面 + 合成键鼠都依赖它）：

1. 打开「系统设置 → 隐私与安全性 → 辅助功能」；
2. 勾选**运行本命令的宿主 App**（Terminal / iTerm2 / VS Code / dsh 等，不是 `python`、也不是
   本脚本本身）；
3. 完全退出并重新打开该 App（授权只在启动时生效）；
4. 运行自检：`uv run scripts/send_to_filehelper.py --check`。

`--check` 会输出平台、微信是否运行、权限是否授予、会话标题是否可读、会话列表与输入框是否可定位。

## Usage / 使用方法

Run from this skill's directory with `uv`（依赖由脚本头部内联元数据自动安装，无需手动 pip）：

```bash
# 环境自检（强烈建议先跑一次，尤其是 macOS 首次使用）
uv run scripts/send_to_filehelper.py --check

# 发送单个文件到「文件传输助手」
uv run scripts/send_to_filehelper.py "C:\work\报告.pdf"      # Windows
uv run scripts/send_to_filehelper.py ~/work/报告.pdf          # macOS

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

### Minimal examples / 最简示例

```python
# Windows (wxauto4)
from wxauto4 import WeChat
wx = WeChat()
wx.SendFiles(r'C:\你的文件路径\报告.pdf', '文件传输助手')
```

```applescript
-- macOS：本脚本做的事就是把文件放到剪贴板，再粘贴进微信输入框回车
-- （需要「辅助功能」权限；脚本内部用 Accessibility API 校验会话后再粘贴）
```

### Recommended agent workflow / 推荐的执行流程

1. 先跑 `--check`（尤其 macOS 首次），确认后端起作用；
2. 确认文件存在（必要时 `--dry-run` 先展开清单），目录/通配符会自动展开；
3. 默认目标固定为「文件传输助手」；
4. **只有用户明确要求发给某个人/群时才用 `--to`，并先把解析到的名称回显给用户确认**；
5. 执行后检查退出码与校验结果；
6. 失败时按「Troubleshooting」排查，不要盲目重试（可能产生重复文件消息）。

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
| `--retries` | `0` | 发送失败后的重试次数（**仅 Windows**，macOS 后端会忽略并提示） |
| `--no-verify` | 关闭 | 跳过发送后的消息校验 |
| `--dry-run` | 关闭 | 只打印待发送清单，完全不操作微信，可在任意平台执行 |
| `--check` | 关闭 | 只做环境自检（权限 / 微信 / 后端可用性），不发送 |
| `--json` | 关闭 | 以 JSON 输出结果摘要（`platform` / `submitted` / `confirmed` / `errors`） |

## Technical Implementation / 技术实现

### Common flow / 共同流程

「展开输入 → 校验会话（**硬校验，不一致就中止**）→ 发送 → 校验消息 → 汇总」，
每一步都可能中止，确保宁可不发也不发错人。

### Windows backend / Windows 后端

1. `WeChat()` 连接已登录的微信客户端，失败给出可执行排查提示；
2. `wx.ChatWith(target, exact=True)` 切换会话；
3. `wx.ChatInfo()['chat_name']` 与目标比对，不一致即中止并列出候选会话；
4. `wx.SendFiles([...绝对路径...])`（已确认会话正确，故不重复传 `who`）；
5. `wx.GetAllMessage()` 中查找 `type == 'file'` 且内容含文件名的消息；
6. `SendFiles` 返回值兼容 `WxResponse`（含 status/message）与 `None` 两种情形。

### macOS backend / macOS 后端（`scripts/wechat_mac.py`）

使用微信 4.x 稳定的辅助功能标识：

| 标识 | 元素 |
| --- | --- |
| `chat_input_field` | 聊天输入框（TextArea） |
| `big_title_line_h_view` | 当前会话标题（StaticText） |
| `chat_message_list` / `chat_bubble_item_view` | 消息列表 / 单条消息气泡 |
| `session_item_<名称>` | 左侧会话列表中的一行 |

流程：

1. 用 `NSRunningApplication` 找到微信（bundle id `com.tencent.xinWeChat`）并激活到前台；
2. 若当前会话已是目标（读 `big_title_line_h_view` 校验）→ 直接进入发送；
3. 否则：聚焦搜索框 → 粘贴目标名 → 回车；再读会话标题校验；
4. 校验失败则回退：在左侧会话列表找同名行并在窗口内点击，再次校验；
5. 仍失败 → **中止**（不发送），并输出候选会话名；
6. 把文件写入系统剪贴板（`NSPasteboard` 的 `public.file-url`，等价于访达拷贝），
   用 `AXRaise` 聚焦输入框 → `Cmd+V` → 回车；
7. 发送前后对比消息列表内容，做发送后校验。

macOS 端的两点差异：好友身份无法像 Windows 那样二次确认，因此发送前只认「会话标题精确匹配」
（`--exact` 默认开启）；`--retries` 不适用。

### Exit codes / 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 文件已提交，且消息校验通过（或用户用 `--no-verify` 主动跳过校验；或校验不可用但有明确提示） |
| `1` | 文件不存在 / 平台不支持 / 权限不足 / 微信不可用 / 会话不匹配 / 发送失败 / 校验未发现文件消息 |

自检模式 `--check` 也使用同样的退出码：`0` 表示后端起作用，`1` 表示有问题并给出修复建议。

## Example Output / 示例输出

```
目标会话: 文件传输助手（精确匹配: 是）
待发送: 2 个文件，共 195.3 KB
  - /Users/me/work/dist/data.bin  (195.3 KB)
  - /Users/me/work/dist/note.txt  (1 B)
已切换到会话: 文件传输助手
已发送文本消息: 构建产物
粘贴文件并发送: data.bin、note.txt

====================================================
目标会话 : 文件传输助手
已提交   : 2/2 个文件
已确认全部 2 个文件出现在会话中
完成
```

## Troubleshooting / 常见问题

| 现象 | 处理方式 |
| --- | --- |
| macOS 报「需要授予辅助功能权限」 | 系统设置 → 隐私与安全性 → 辅助功能 → 勾选宿主 App → 完全退出并重开 → `--check` |
| macOS 会话标题读不到 | 微信主窗口被最小化/遮挡，先切到前台；`--check` 会检查这一项 |
| 非 Windows/macOS 平台 | 不支持；可在这些平台上用 `--dry-run` 仅确认清单 |
| Windows `无法连接微信 PC 客户端` | 启动并登录微信 4.x；主窗口不要最小化到托盘；确认微信版本与 wxauto4 兼容 |
| `当前会话是「X」，与目标「Y」不一致` | 名称不精确；用输出的候选列表修正 `--to`（macOS 也可用 `--no-exact` 放宽） |
| `发送后未在会话中发现任何文件消息` | 微信被遮挡/弹窗打断；保持窗口在前台、不要同时操作键鼠，然后重试 |
| 微信提示文件过大 | 客户端对大文件有限制（超过 `--max-size-mb` 会提示）；改用其他传输方式 |
| 中文乱码（Windows） | 脚本已强制 UTF-8；仍异常时在 `cmd` 执行 `chcp 65001` |

## Notes / 注意事项

1. **隐私与合规**：只操作本人已登录的账号；默认只发「文件传输助手」。发给他人前必须由用户明确
   指定并确认昵称。禁止用于群发、营销或骚扰。
2. **发送期间不要抢操作鼠标键盘**：两个后端都通过模拟粘贴/点击/回车工作，人为干扰可能导致内容
   发错对象。
3. **文件必须在运行微信的这台机器上可读**，使用绝对路径；Windows 超长路径（>260 字符）可能失败。
4. **一次只跑一个自动化进程**，避免两个脚本同时操作同一个微信窗口。
5. macOS 首条消息/切换会话依赖微信窗口在前台；脚本会自动激活微信但不会替你关闭其它弹窗。
6. 发送成功只代表消息已提交给微信；手机端接收依赖微信自身的同步。
7. **测试用环境变量**（一般不需要）：`SEND_TO_FILEHELPER_BACKEND=windows|macos` 可强制后端，
   `SEND_TO_FILEHELPER_SKIP_PLATFORM_CHECK=1` 可忽略平台检查 —— 仅用于模拟/开发验证。
