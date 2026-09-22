---
name: send-to-filehelper
description: Send local files and text messages to WeChat "文件传输助手" (File Transfer Helper) or a named contact on Windows and macOS / 在 Windows 与 macOS 上把本地文件或文字发送到微信「文件传输助手」或指定好友、群聊。当用户说“把文件发到微信”“发送到文件传输助手”“传到手机”“发我微信上”“把这段文字发到我微信”时使用。
metadata:
  author: 邱张华(qiuzhanghua@msn.com)
license: MIT
---

# Send to FileHelper / 发送文件与文字到微信

## Overview / 概述

This skill sends **local files and/or text messages** to WeChat's **文件传输助手** (File Transfer
Helper) — or to a named contact/group — by driving the **already logged-in WeChat desktop client**
of the user. Two platform backends share one CLI:

| Platform | Backend | Mechanism |
| --- | --- | --- |
| Windows | [`wxauto4`](https://pypi.org/project/wxauto4/) | Windows UI Automation |
| macOS | `pyobjc` (Accessibility API) | macOS AX tree + clipboard paste + synthetic keys |

本 skill 把**本地文件和/或文字**发送到微信「文件传输助手」（或指定好友/群聊），两个平台共用同一
个命令行：

| 平台 | 后端 | 原理 |
| --- | --- | --- |
| Windows | wxauto4 | Windows UI Automation |
| macOS | pyobjc（Accessibility API） | 读取微信 4.x 辅助功能树 + 剪贴板粘贴 + 合成键鼠事件 |

Both backends only operate the user's own logged-in client: no protocol reverse-engineering, no
injection, no bypassing of WeChat limits. 两个后端都只操作用户本人已登录的客户端界面，不做协议
破解、不注入、不绕过微信限制。

Typical use case: a file produced on the computer (build artifact, report, PDF, screenshot, log) or a
piece of text (link, address, note, password) is pushed into WeChat so it can be picked up on the
phone or on another machine.

典型场景：把电脑上生成的文件（构建产物、报告、PDF、截图、日志）或一段文字（链接、地址、备忘）
发进微信，方便在手机或其他设备上取用。

## When to use / 触发场景

- "把这个 PDF 发到文件传输助手" / "把 dist 目录里的文件传到微信" / "发我微信上"
- "把这段文字发到文件传输助手" / "把这条链接发到我微信" / "给我发条微信备忘"
- "send this file to WeChat" / "push the build artifact / this text to my File Transfer Helper"
- "把这几个文件发给我自己"、备份小文件到微信、把截图或一段命令发到微信

Do **not** use this skill for batch marketing, mass messaging, sending to strangers, or any
automated outreach. It is intended only for the user's own account and conversations.

## Requirements / 环境要求

| Item | Windows | macOS |
| --- | --- | --- |
| OS | Windows 10 / 11 | macOS（macOS 15 + 微信 4.1.13 实测） |
| Client | Windows 版微信 **4.x**，已登录，主窗口未最小化 | 微信 Mac 版 **4.x**（`WeChat.app`），已登录 |
| Python | 由 `uv` 自动管理（`>=3.10,<3.13`） | 同左 |
| Dependency | `wxauto4`（uv 自动安装） | `pyobjc-framework-Cocoa / Quartz / ApplicationServices`（uv 自动安装） |
| Permission | 无（UI Automation 不需要额外授权） | **必须**授予「辅助功能」权限给运行命令的宿主 App |
| Input mode | wxauto4（会话校验 + 发送） | 辅助功能模式（可校验）或键盘模式（微信 4.1.x 实测走这条） |
| Tool | [`uv`](https://docs.astral.sh/uv/) | 同左 |

Linux is not supported. Linux 不支持。

### macOS input modes / macOS 两种输入方式

微信 Mac **4.1.x 实测不会通过辅助功能暴露聊天界面**：AX 树里只有标准菜单栏
（Apple / 文件 / 编辑 / 显示 / 窗口 / 帮助），没有 `session_item_*`、`chat_input_field`、
`big_title_line_h_view` 等元素，`AXEnhancedUserInterface`、`AXManualAccessibility` 与坐标
命中测试都返回 `notImplemented`。所以 macOS 上有两条路：

| 模式 | 行为 | 校验能力 |
| --- | --- | --- |
| **辅助功能模式**（`--input-mode ax`） | 读 AX 树切换会话、`AXRaise` 聚焦输入框、剪贴板粘贴 | 发送前校验会话标题、发送后校验消息 |
| **键盘模式**（`--input-mode keystrokes`） | `Esc` → `Cmd+F` → 粘贴名称 → 回车 → 粘贴文件 → 回车 | **无**：发送前后都无法校验 |

`--input-mode auto`（默认）会先探测 AX 内容，可用就用辅助功能模式，否则自动落到键盘模式并
明确提示。键盘模式下目标名称必须完全正确，默认的「文件传输助手」最安全。

### macOS permission setup / macOS 权限设置

macOS 的辅助功能权限是硬性要求（读界面 + 合成键鼠都依赖它）：

1. 打开「系统设置 → 隐私与安全性 → 辅助功能」；
2. 勾选**运行本命令的宿主 App**（Terminal / iTerm2 / Ghostty / VS Code 等，不是 `python`、
   也不是本脚本本身）；
3. 完全退出并重新打开该 App（授权只在启动时生效）；
4. 运行自检：`uv run scripts/send_to_filehelper.py --check`。

`--check` 会输出平台、微信版本、权限、AX 窗口数与窗口状态、AX 探测结果、命中测试，以及最终
判定：辅助功能模式可用，还是只能用键盘模式。加 `--debug` 可看到 AX 错误码。

## Usage / 使用方法

Run from this skill's directory with `uv`（依赖由脚本头部内联元数据自动安装，无需手动 pip）：

```bash
# 环境自检（强烈建议先跑一次，尤其是 macOS 首次使用）
uv run scripts/send_to_filehelper.py --check

# 发送单个文件到「文件传输助手」
uv run scripts/send_to_filehelper.py "C:\work\报告.pdf"      # Windows
uv run scripts/send_to_filehelper.py ~/work/报告.pdf          # macOS

# 只发文字（不传文件）
uv run scripts/send_to_filehelper.py -m "构建已完成：https://ci.example.com/1234"
uv run scripts/send_to_filehelper.py -m "第一行" -m "第二行"        # 多条，按顺序

# 先发说明文字，再发文件，最后补一句
uv run scripts/send_to_filehelper.py ./build/app.exe -m "最新构建产物" -A "发布完成"

# macOS 若为键盘模式（--check 会告知），可显式指定：
uv run scripts/send_to_filehelper.py ~/work/报告.pdf --input-mode keystrokes

# 发送多个文件 / 通配符 / 整个目录
uv run scripts/send_to_filehelper.py ./dist/*.zip
uv run scripts/send_to_filehelper.py ./out --recursive

# 先发一条文字说明，再发文件
uv run scripts/send_to_filehelper.py ./build/app.exe --message "最新构建产物"

# 只发文字 / 先文字后文件 / 文件后再补文字
uv run scripts/send_to_filehelper.py -m "把这段链接发到我微信：https://example.com/x"
uv run scripts/send_to_filehelper.py -m "说明" ./report.xlsx
uv run scripts/send_to_filehelper.py ./report.xlsx -A "查收，有问题找我"

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
| `FILE...` | 可选 | 一个或多个文件路径；支持 `~`、环境变量、`*` / `?` / `[` 通配符和目录。可以与 `-m` 组合，也可以完全不给（只发文字） |
| `-t, --to` | `文件传输助手` | 目标会话：文件传输助手 / 好友昵称 / 群聊名称 |
| `--exact / --no-exact` | `--exact` | 是否精确匹配会话名，默认开启，避免发错人 |
| `-m, --message TEXT` | 无 | 要发送的文本消息，**可重复**，按给出顺序在文件**之前**发送 |
| `-A, --after-message TEXT` | 无 | 文件发送完之后再发的文本消息，可重复 |
| `--one-by-one` | 关闭 | 逐个文件发送；默认一次性提交多个文件 |
| `--delay` | `1.0` | 消息/批次之间的等待秒数 |
| `-r, --recursive` | 关闭 | 输入是目录时递归收集子目录中的文件 |
| `--max-size-mb` | `100` | 超过此大小给出提示（微信客户端可能拒收大文件） |
| `--retries` | `0` | 发送失败后的重试次数（**仅 Windows**，macOS 后端会忽略并提示） |
| `--no-verify` | 关闭 | 跳过发送后的消息校验 |
| `--dry-run` | 关闭 | 只打印待发送的文本与文件，完全不操作微信，可在任意平台执行 |
| `--check` | 关闭 | 只做环境自检（权限 / 微信 / 窗口 / AX 元素 / 用哪种输入模式），不发送 |
| `--input-mode` | `auto` | macOS 输入方式：`auto` 自动选择；`ax` 只用辅助功能；`keystrokes` 只用键盘（Windows 忽略） |
| `--debug` | 关闭 | 把辅助功能（AX）调用的错误码等诊断输出到 stderr |
| `--json` | 关闭 | 以 JSON 输出结果摘要（`submitted` / `messages` / `confirmed` / `errors`） |

至少要给出一个文件或用 `-m/--message` 给出一条文字，否则直接报错退出。

发送顺序固定为：**`-m` 文本（按顺序） → 文件（一个批次或 `--one-by-one`） → `-A` 文本（按顺序）**。

## Technical Implementation / 技术实现

### Common flow / 共同流程

「展开输入 → 校验会话（**硬校验，不一致就中止**）→ 先发文本 → 发文件 → 补发文本 → 校验消息 → 汇总」，
每一步都可能中止，确保宁可不发也不发错人。纯文本发送会跳过文件步骤。

### Windows backend / Windows 后端

1. `WeChat()` 连接已登录的微信客户端，失败给出可执行排查提示；
2. `wx.ChatWith(target, exact=True)` 切换会话；
3. `wx.ChatInfo()['chat_name']` 与目标比对，不一致即中止并列出候选会话；
4. 文本用 `wx.SendMsg(text)` 逐条发送（返回值同样兼容 `WxResponse` 与 `None`）；
5. 文件用 `wx.SendFiles([...绝对路径...])`（已确认会话正确，故不重复传 `who`）；
6. `wx.GetAllMessage()` 里按消息类型校验：`type == 'file'` 匹配文件名，`type == 'text'`
   匹配文本内容；`SendFiles`/`SendMsg` 的返回值两种形态都兼容。

### macOS backend / macOS 后端（`scripts/wechat_mac.py`）

两条路径，`--input-mode auto` 自动选择：

**辅助功能模式**（微信暴露内容时可用）使用这些标识：

| 标识 | 元素 |
| --- | --- |
| `chat_input_field` | 聊天输入框（TextArea） |
| `big_title_line_h_view` | 当前会话标题（StaticText） |
| `chat_message_list` / `chat_bubble_item_view` | 消息列表 / 单条消息气泡 |
| `session_item_<名称>` | 左侧会话列表中的一行 |

1. 用 `NSRunningApplication` 找到微信（bundle id `com.tencent.xinWeChat`）并激活到前台；
2. 若当前会话已是目标（读 `big_title_line_h_view` 校验）→ 直接进入发送；
3. 否则：聚焦搜索框 → 粘贴目标名 → 回车；再读会话标题校验；
4. 校验失败则回退：在左侧会话列表找同名行并在窗口内点击，再次校验；
5. 仍失败 → **中止**（不发送），并输出候选会话名；
6. 文本用 `send_text()`（AX 直接写输入框的值 + 回车）；
7. 文件写入 `NSPasteboard`（`public.file-url`，等价访达拷贝）→ `AXRaise` 聚焦输入框 →
   `Cmd+V` → 回车；
8. 发送前后对比消息列表内容做校验（文件名与文本都能匹配）。

**键盘模式**（微信 4.1.x 实测路径）：`Esc` → `Cmd+F` → `Cmd+V` 粘贴目标名 → 回车 →
`Cmd+V` 粘贴文件/文字 → 回车。只依赖「辅助功能」权限发送合成键鼠事件，不依赖任何 AX 标识；
代价是**无法校验**，因此会打印醒目提示，且 `verify_note` 会写明"不做校验"（退出码仍为 0，
但结果里能看到）。

macOS 端的两点差异：好友身份无法像 Windows 那样二次确认；`--retries` 不适用。

### Exit codes / 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 文件/文本已提交，且消息校验通过（或用户用 `--no-verify` 跳过；或键盘模式/校验不可用但已明确提示） |
| `1` | 输入为空 / 文件不存在 / 平台不支持 / 权限不足 / 微信不可用 / 会话不匹配 / 发送失败 / 校验未发现内容 |

自检模式 `--check`：`0` 表示后端可用（辅助功能模式或键盘模式），`1` 表示有问题并给出修复建议。

## Example Output / 示例输出

```
目标会话: 文件传输助手（精确匹配: 是）
待发送: 1 条文本（文件之前）、2 个文件，共 195.3 KB、1 条文本（文件之后）
  [文本] 最新构建产物
  [文件] /Users/me/work/dist/data.bin  (195.3 KB)
  [文件] /Users/me/work/dist/note.txt  (1 B)
  [文本·后] 发布完成
已切换到会话: 文件传输助手
已发送文本消息: 最新构建产物
粘贴文件并发送: data.bin、note.txt
已发送文本消息（文件之后）: 发布完成

====================================================
目标会话 : 文件传输助手
已提交   : 2/2 个文件
文本消息 : 2/2 条
已确认全部 4 项内容出现在会话中
完成
```

## Troubleshooting / 常见问题

| 现象 | 处理方式 |
| --- | --- |
| macOS 报「需要授予辅助功能权限」 | 系统设置 → 隐私与安全性 → 辅助功能 → 勾选宿主 App → 完全退出并重开 → `--check` |
| macOS `--check` 提示「键盘模式」 | 正常现象：微信 4.1.x 不暴露聊天界面给辅助功能。发送会自动走 `Cmd+F` + 剪贴板，无需额外操作；注意此模式**不做校验**，目标名要写对 |
| macOS `AX 窗口数: 0` | 微信主窗口没打开（只留在 Dock/菜单栏）：点 Dock 图标打开主窗口；发送时脚本会自动 `open -b com.tencent.xinWeChat`（可用 `SEND_TO_FILEHELPER_NO_REOPEN=1` 关闭） |
| macOS 窗口 `minimized=True` | 主窗口被最小化，脚本会激活微信但不会自动还原；先手动展开窗口 |
| macOS 辅助功能模式下会话标题读得到、但发送失败 | 用 `--debug` 看 AX 错误码（`cannotComplete` 多为微信界面正忙），稍后重试 |
| 非 Windows/macOS 平台 | 不支持；可在这些平台上用 `--dry-run` 仅确认清单 |
| Windows `无法连接微信 PC 客户端` | 启动并登录微信 4.x；主窗口不要最小化到托盘；确认微信版本与 wxauto4 兼容 |
| `当前会话是「X」，与目标「Y」不一致` | 名称不精确；用输出的候选列表修正 `--to`（也可用 `--no-exact` 放宽） |
| `发送后未在会话中发现任何文件消息` | 微信被遮挡/弹窗打断；保持窗口在前台、不要同时操作键鼠，然后重试 |
| 微信提示文件过大 | 客户端对大文件有限制（超过 `--max-size-mb` 会提示）；改用其他传输方式 |
| 中文乱码（Windows） | 脚本已强制 UTF-8；仍异常时在 `cmd` 执行 `chcp 65001` |

## Notes / 注意事项

1. **隐私与合规**：只操作本人已登录的账号；默认只发「文件传输助手」。发给他人前必须由用户明确
   指定并确认昵称。禁止用于群发、营销或骚扰。
2. **发送期间不要抢操作鼠标键盘**：两个后端都通过模拟粘贴/点击/回车工作，人为干扰可能导致内容
   发错对象。
3. **文件必须在运行微信的这台机器上可读**，使用绝对路径；Windows 超长路径（>260 字符）可能失败。
4. **纯文本发送**：`-m/--message` 会原样发送（含换行与链接）；单条过长时微信客户端可能拒绝或
   转为文件，长内容建议改用文件发送。
5. **一次只跑一个自动化进程**，避免两个脚本同时操作同一个微信窗口。
6. macOS 首条消息/切换会话依赖微信窗口在前台；脚本会自动激活微信但不会替你关闭其它弹窗。
7. 发送成功只代表消息已提交给微信；手机端接收依赖微信自身的同步。
8. macOS 键盘模式（微信 4.1.x 默认路径）**没有任何校验**：`Esc` → `Cmd+F` → 粘贴名称 → 回车，
   如果名称在微信里查不到，微信不会打开会话，随后粘贴的文件/文字会发给**当前已打开的那个会话**。
   因此键盘模式下务必核对 `--to`，或直接用默认的「文件传输助手」。
9. **测试/高级环境变量**：`SEND_TO_FILEHELPER_BACKEND=windows|macos` 可强制后端，
   `SEND_TO_FILEHELPER_SKIP_PLATFORM_CHECK=1` 可忽略平台检查（仅用于模拟/开发验证），
   `SEND_TO_FILEHELPER_NO_REOPEN=1` 可禁止脚本自动 `open -b` 重开微信主窗口。
