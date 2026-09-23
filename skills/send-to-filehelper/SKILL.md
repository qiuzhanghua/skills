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
| Windows | [`wxautox4`](https://pypi.org/project/wxautox4/) (Plus) if installed, else [`wxauto4`](https://pypi.org/project/wxauto4/) (free) | Windows UI Automation |
| macOS | `pyobjc` (Accessibility API) | macOS AX tree + clipboard paste + synthetic keys |

本 skill 把**本地文件和/或文字**发送到微信「文件传输助手」（或指定好友/群聊），两个平台共用同一
个命令行：

| 平台 | 后端 | 原理 |
| --- | --- | --- |
| Windows | 装了 Plus 版就用 `wxautox4`，否则用 `wxauto4` | Windows UI Automation |
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
| Client | Windows 版微信，已登录，主窗口未最小化。**免费版最高只支持客户端 `4.1.8.107`**，见下节 | 微信 Mac 版 **4.x**（`WeChat.app`），已登录 |
| Python | 由 `uv` 自动管理（`>=3.10,<3.13`） | 同左 |
| Dependency | `wxauto4` 免费版（uv 自动安装）；可选 Plus 版 `wxautox4`（付费，见下节） | `pyobjc-framework-Cocoa / Quartz / ApplicationServices`（uv 自动安装） |
| Permission | 无（UI Automation 不需要额外授权） | **必须**授予「辅助功能」权限给运行命令的宿主 App |
| Input mode | 后端自动选择（会话校验 + 发送） | 辅助功能模式（可校验）或键盘模式（微信 4.1.x 实测走这条） |
| Tool | [`uv`](https://docs.astral.sh/uv/) | 同左 |

Linux is not supported. Linux 不支持。

### Windows client compatibility / Windows 客户端版本兼容

**`wxauto4` 免费版官方最高只支持微信客户端 `4.1.8.107`**（见
[wxauto 安装文档](https://docs.wxauto.org/docs/install.html)）。比它更新的客户端不再向 UI Automation
暴露免费版需要的控件树（`mmui::MainWindow` 等），于是 `WeChat()` 抛
`未找到已登录的客户端主窗口` 并白等约 120 秒——**这不是登录、最小化或权限问题，重试也不会成功**。

实测（客户端 `4.1.12.55`）：主窗口 `Qt51514QWindowIcon` 的 UIA 子树只有两个节点
（`Qt51514QWindowIcon`/`Weixin` 与 `MMUIRenderSubWindowHW`），**完全没有 `mmui::*` 控件**。
把窗口最大化到 3872×2072 也一样（微信 4.x 只给可见区域注册控件，但这里不是尺寸问题）。
所以免费版和 PyPI 上的 Plus 版（`wxautox4 41.1.1.post1`）都会报 `未找到已登录的客户端主窗口`。

**排查工具**（建议在换客户端前、以及以后每次微信大版本更新后跑一次）：

```bash
uv run scripts/wechat_tree.py            # 打印主窗口真实控件树并给出结论
uv run scripts/wechat_tree.py --json     # 机器可读
uv run scripts/wechat_tree.py --full     # 打印全部节点
uv run scripts/wechat_tree.py --maximize # 先把主窗口最大化再检查
```

判定 `mmui::*` 控件是否存在（只有带双冒号的才是真控件；`MMUIRenderSubWindowHW` 这种外壳不算）。
控件树可用时退出码 `0`，不可用时 `1`——**不可用就不要再折腾代码或后端了，换客户端版本**。

**必须在普通终端（非受限沙箱）里运行**：wxautox4 的授权状态存在 `~/.wxautox`，两个后端又都依赖
UI Automation 跨进程访问微信。在受限沙箱里运行时会出现两种假故障：

- `~/.wxautox` 不可写 → Plus 版读不到授权，报「未授权设备」（**其实已经激活成功**）；
- 对微信的 UIA 调用阻塞约 60 秒并返回空树 → 任何后端都找不到主窗口。

脚本会在 Plus 版授权目录不可写时提前给出这个结论，而不是让你去排查登录状态。

脚本因此在 Windows 上做了两件事：

1. **前端预检**（纯 stdlib，不依赖后端）：枚举正在运行的 `Weixin.exe` / `WeChat.exe`，读它的文件
   版本，检查主窗口是否存在；最小化的主窗口会被自动还原。版本超出免费版上限时，**在构造客户端
   之前**就中止，并直接给出下面两条出路（不再白等 120 秒再报一个误导性的错误）。
2. **优先使用 Plus 版后端**：装了 `wxautox4` 就自动用 `wxautox4`（Plus 版跟随新版客户端更新），
   没装则回落免费版 `wxauto4`。

### 自研后端（`--wx-backend own`）/ Built-in backend

免费版和 Plus 版都依赖客户端暴露 UIA 控件树，而微信 4.1.12.x 根本不暴露（见上）。为此本 skill
还带了一个**自研后端** `scripts/wechat_win.py`，完全不用 UIA 控件树，改成像人一样操作界面：

| 环节 | 做法 |
| --- | --- |
| 定位界面 | `PrintWindow` 截窗口 + **Windows OCR** 读文字及其坐标（地标） |
| 会话校验 | OCR 读聊天区标题，**发送前硬校验**当前会话（安全底线，与 wxauto4 后端一致） |
| 切换会话 | OCR 找到左侧会话列表里目标名字的行，点它，再复核标题 |
| 发送文本 | 聚焦输入框 → 剪贴板 + `Ctrl+V` → `Enter` |
| 发送文件 | 点工具栏「发送文件」→ 文件对话框填路径 → 回车 |
| 发送校验 | 多次催重绘后 OCR，确认内容出现（微信重绘是异步的，单帧常是旧画面） |

```bash
uv run scripts/send_to_filehelper.py -m "说明" --wx-backend own     # 发文本
uv run scripts/send_to_filehelper.py ./报告.pdf --wx-backend own    # 发文件
```

等价的强制方式是 `SEND_TO_FILEHELPER_WX_BACKEND=own`。

**这个后端的注意事项**（都在实测中踩过）：

- **必须在“已解锁的交互桌面”上运行**。锁屏、RDP 断开时 `SetCursorPos` 会返回「拒绝访问」，
  任何键鼠自动化都无法工作（分辨率也可能被系统降级）。这不是脚本的问题。
- **必须开启 DPI 感知**（脚本内部已调用 `SetProcessDpiAwareness`）。否则 `GetWindowRect`
  返回被虚拟化缩放的坐标，截图区域与 OCR 坐标整体错位，表现为**聊天区一片空白、读不到控件**。
- **微信的界面重绘是异步的**：刚发完消息时截图很可能还是旧画面。脚本会用
  `RedrawWindow` 催重绘 + 多次截图取并集来做校验，不要根据单张截图判断成败。
- 会话列表宽度是**固定像素**（约 740px），聊天区太窄时微信不渲染工具栏；脚本会把窗口收成
  一个完整可见、够宽的尺寸（同 wxauto4 的 `auto_resize`）。
- 发给**非默认会话**时，OCR 名称必须能精确匹配；不确定就用默认的「文件传输助手」。
- **发送后校验是"尽力而为"**：自研后端依赖 OCR 复核，而微信窗口截图经常滞后（实测：
  消息已经到达，截图里却还是上一条）。因此复核不到**不算失败**，技能会打印一条
  `消息校验：…未能复核…` 的提示并以退出码 0 结束。**判定是否送达请看微信界面本身。**
- 机器上可能**同时开着两个微信**（一个已登录、一个停在扫码登录页）。脚本靠 OCR 区分
  （有「搜索/发送」的是主窗口，有「扫码登录/仅传输文件」的是登录页），不会选错。

```bash
# 免费版：把客户端换到受支持的 4.1.8.107
#   https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107

# Plus 版（付费）：装上并激活后，本命令会自动优先使用它
uv run --with wxautox4 scripts/send_to_filehelper.py ./out -m "说明"
wxautox4 auth activate <激活码>        # 首次需要激活（未激活时后端会直接退出）
```

`SEND_TO_FILEHELPER_WX_BACKEND=free|plus` 可强制指定后端（默认 `auto`）；
`SEND_TO_FILEHELPER_SKIP_CLIENT_CHECK=1` 可跳过这套预检强行尝试。

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

# Windows：改用官方 Plus 版后端（需要先激活；装了就会自动优先使用）
uv run --with wxautox4 scripts/send_to_filehelper.py --check

# 发送单个文件到「文件传输助手」
uv run scripts/send_to_filehelper.py "C:\work\报告.pdf"      # Windows
uv run scripts/send_to_filehelper.py ~/work/报告.pdf          # macOS

# 多个文件 / 通配符 / 整个目录
uv run scripts/send_to_filehelper.py ./dist/*.zip
uv run scripts/send_to_filehelper.py ./out --recursive

# 只发文字（可多条，按顺序）
uv run scripts/send_to_filehelper.py -m "构建已完成：https://ci.example.com/1234"
uv run scripts/send_to_filehelper.py -m "第一行" -m "第二行"

# 先发文字说明 → 发文件 → 再补一句
uv run scripts/send_to_filehelper.py ./build/app.exe -m "最新构建产物" -A "发布完成"

# 不切换会话、不弹微信搜索面板（先手动打开目标会话）
uv run scripts/send_to_filehelper.py ./out -m "说明" --no-switch

# 发给指定好友或群聊（发送前请与用户确认名称）
uv run scripts/send_to_filehelper.py ./report.xlsx -m "说明" --to "张三"

# macOS 键盘模式可显式指定（--check 会告知当前是哪种模式）
uv run scripts/send_to_filehelper.py ~/work/报告.pdf --input-mode keystrokes

# 只检查将要发送什么，不操作微信（可在任意平台运行）
uv run scripts/send_to_filehelper.py ./out --dry-run

# 机器可读的结果摘要 / 安静模式（成功时只输出一行）
uv run scripts/send_to_filehelper.py ./out --json
uv run scripts/send_to_filehelper.py ./out -q
```

`-m/--message` 可重复，`-A/--after-message` 同理；长选项写法与短选项等价。

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
| `--no-switch` | 关闭 | 不切换会话，直接发给当前已打开的会话；**完全不触发微信搜索面板** |
| `--search-delay` | `0.5` | macOS 键盘模式：粘贴目标名后等搜索结果再回车的秒数（越小搜索面板显示越短） |
| `--one-by-one` | 关闭 | 逐个文件发送；默认一次性提交多个文件 |
| `--delay` | `1.0` | 消息/批次之间的等待秒数 |
| `-r, --recursive` | 关闭 | 输入是目录时递归收集子目录中的文件 |
| `--max-size-mb` | `100` | 超过此大小给出提示（微信客户端可能拒收大文件） |
| `--retries` | `0` | 发送失败后的重试次数（**仅 Windows**，macOS 后端会忽略并提示） |
| `--no-verify` | 关闭 | 跳过发送后的消息校验 |
| `--dry-run` | 关闭 | 只打印待发送的文本与文件，完全不操作微信，可在任意平台执行 |
| `--check` | 关闭 | 只做环境自检（权限 / 微信 / 窗口 / AX 元素 / 用哪种输入模式），不发送 |
| `--input-mode` | `auto` | macOS 输入方式：`auto` 自动选择；`ax` 只用辅助功能；`keystrokes` 只用键盘（Windows 忽略） |
| `-q, --quiet` | 关闭 | 安静模式：只输出警告/错误和一行结果（适合脚本/定时任务） |
| `--debug` | 关闭 | 把辅助功能（AX）调用的错误码等诊断输出到 stderr |
| `--json` | 关闭 | 以 JSON 输出结果摘要（`submitted` / `messages` / `confirmed` / `errors`） |

至少要给出一个文件或用 `-m/--message` 给出一条文字，否则直接报错退出。

发送顺序固定为：**`-m` 文本（按顺序） → 文件（一个批次或 `--one-by-one`） → `-A` 文本（按顺序）**。

## Technical Implementation / 技术实现

### Common flow / 共同流程

「展开输入 → 校验会话（**硬校验，不一致就中止**）→ 先发文本 → 发文件 → 补发文本 → 校验消息 → 汇总」，
每一步都可能中止，确保宁可不发也不发错人。纯文本发送会跳过文件步骤。

### Windows backend / Windows 后端

0. **预检 + 选后端**（纯 stdlib）：枚举进程与顶层窗口，确认微信在运行、读客户端文件版本、
   必要时还原最小化的主窗口；版本超出免费版上限且没装 Plus 版时直接中止。然后按
   `wxautox4` → `wxauto4` 的顺序导入后端（`SEND_TO_FILEHELPER_WX_BACKEND` 可强制）；
1. `WeChat(ads=False)` 连接已登录的微信客户端（`ads=False` 关掉免费版横幅），失败给出可执行排查提示；
2. `wx.ChatWith(target, exact=True)` 切换会话；
3. `wx.ChatInfo()['chat_name']` 与目标比对，不一致即中止并列出候选会话；
4. 文本用 `wx.SendMsg(text)` 逐条发送（返回值同样兼容 `WxResponse` 与 `None`）；
5. 文件用 `wx.SendFiles([...绝对路径...])`（已确认会话正确，故不重复传 `who`）；
6. `wx.GetAllMessage()` 里按消息类型校验：`type == 'file'` 匹配文件名，`type == 'text'`
   匹配文本内容；`SendFiles`/`SendMsg` 的返回值两种形态都兼容。

#### 关于 wxauto4 免费版的推广输出 / silencing the free-version banner

`wxauto4` 41.1.7 免费版在构造客户端时会打印（新版文案）：

```
====================================================================
当前为免费版wxauto4，如需更多功能可查看plus版本：
https://work.weixin.qq.com/kfid/kfc2576aec57f59362a

wx = WeChat(ads=False) 可取消输出该内容，如有打扰请见谅
====================================================================
```

这段横幅由编译后的扩展直接写底层 stdout，**Python 层过滤器拦不住**（只有早期版本能兜住）。
另外 `WxParam` 里带着远程广告与遥测开关（`AD_API_URL`、`REPORT_API_URL`、
`TELEMETRY_ENABLED=True`）。本 skill 因此做了三件事：

1. **从源头关掉横幅**：构造时传 `ads=False`（旧版本不认这个参数时自动退回无参调用）；
2. **兜底过滤推广文本**：给 `sys.stdout`/`sys.stderr` 装上过滤器，丢弃命中推广标记的写入（连同其
   换行）。过滤器只匹配推广专用片段（如 `wxauto.org/purchase`、`work.weixin.qq.com/kfid`），
   **不会**误伤 `docs.wxauto.org` 这类正常链接；本脚本自己的 `info/ok/warn/fail` 输出走保存下来的
   原始流，永远不会被过滤器吞掉；
3. **关闭广告接口与遥测**：把 `WxParam.TELEMETRY_ENABLED` 置为 `False`，并清空 `AD_API_URL`、
   `REPORT_API_URL`，避免额外的网络请求与设备指纹上报。

需要还原时：`SEND_TO_FILEHELPER_SHOW_ADS=1` 放行推广文本，
`SEND_TO_FILEHELPER_ALLOW_TELEMETRY=1` 放行遥测。

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

**搜索面板无法从脚本侧屏蔽**：`Cmd+F` 后微信会弹出自己的搜索结果面板，其中包含「文件传输助手」
以及并列的介绍/推荐条目（看起来像广告）。这是微信客户端的行为，脚本改不了。要完全不出现它：

1. 手动打开「文件传输助手」（或任何目标会话），然后加 `--no-switch`：
   ```bash
   uv run scripts/send_to_filehelper.py ./out -m "说明" --no-switch
   ```
   脚本不切换会话、不按 `Cmd+F`，直接粘贴发送，全程没有任何搜索面板。AX 模式下仍然会读
   `big_title_line_h_view` 校验当前会话是否为目标；键盘模式下无法校验，会明确提示。
2. 或者调小 `--search-delay`（例如 `0.2`），让搜索面板显示的时间尽量短（太小可能搜索还没出结果，
   回车落空）。

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
| Windows 报 `未找到已登录的客户端主窗口`，或提示「客户端版本超出免费版兼容范围」 | 免费版最高只支持客户端 **4.1.8.107**。换成受支持版本（[4.1.8.107 归档](https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107)），或改用 Plus 版 `uv run --with wxautox4 scripts/send_to_filehelper.py ...`（需激活）。详见「Windows 客户端版本兼容」 |
| Windows `无法连接微信 PC 客户端` | 启动并登录微信并保持主窗口打开（不要只留在托盘）；`--check` 会打印检测到的客户端路径、版本、主窗口状态与当前用的是哪个后端 |
| Windows 提示「没有找到可见的主窗口」 | 微信被关进托盘/系统栏了：点开微信主窗口后重试（脚本只自动还原最小化，不会从托盘唤起） |
| Windows 报「后端直接退出（退出码 1）」 | 用的是 Plus 版且设备未授权：`wxautox4 auth activate <激活码>`；或设 `SEND_TO_FILEHELPER_WX_BACKEND=free` 回到免费版 |
| Plus 版已激活，但仍报「未授权设备」 | 多半是跑在受限沙箱里：`~/.wxautox` 不可写，授权状态读不到。`wxautox4 auth check` 会显示 `active:false`。请在普通终端重跑（脚本也会在授权目录不可写时直接提示） |
| 想确认到底是客户端问题还是代码问题 | 跑 `uv run scripts/wechat_tree.py`：打印主窗口真实控件树。没有 `mmui::*` 就说明客户端不发布界面，和实现无关 |
| `--wx-backend own` 报 `SetCursorPos ... 拒绝访问` | **当前桌面不可交互**（锁屏 / RDP 断开）。解锁屏幕后重试；这时任何键鼠自动化都不工作 |
| `--wx-backend own` 读到的界面"一片空白"、找不到「发送」 | 多半是 DPI 感知没生效（脚本已内置）或窗口被盖住/比屏幕还大。脚本会先把窗口收成完整可见的尺寸并催重绘 |
| 认为自己"发送失败"但对方其实收到了 | 微信界面重绘是异步的，**单张截图可能是旧画面**。以会话列表的预览文字为准，或稍等再截图核对 |
| 认为「窗口太小导致控件树被隐藏」 | 微信 4.x 确实只给可见区域注册控件，但实测把窗口最大化到 3872×2072 后节点数仍是 2，所以本机不是尺寸问题；可用 `wechat_tree.py --maximize` 自行复核 |
| `当前会话是「X」，与目标「Y」不一致` | 名称不精确；用输出的候选列表修正 `--to`（也可用 `--no-exact` 放宽） |
| `发送后未在会话中发现任何文件消息` | 微信被遮挡/弹窗打断；保持窗口在前台、不要同时操作键鼠，然后重试 |
| 微信提示文件过大 | 客户端对大文件有限制（超过 `--max-size-mb` 会提示）；改用其他传输方式 |
| 每次调用弹出微信搜索面板（含「文件传输助手」并列的介绍/推荐条目） | 这是微信客户端自己的搜索界面，脚本无法屏蔽。先手动打开目标会话，再用 `--no-switch` 发送即可完全不触发；也可用 `--search-delay 0.2` 缩短它显示的时间 |
| 每次调用出现 `当前为免费版wxauto4 … 如需更多功能可查看plus版本` | 这是 wxauto4 免费版自带的推广横幅。本 skill 用 `ads=False` 从源头关掉它，并额外过滤推广文本；若仍出现，请把原样输出发出来 |
| 想要更少的输出 | 加 `-q/--quiet`，成功时只打印一行 `完成: …` |
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
9. **测试/高级环境变量**：`SEND_TO_FILEHELPER_BACKEND=windows|macos` 可强制平台后端，
   `SEND_TO_FILEHELPER_SKIP_PLATFORM_CHECK=1` 可忽略平台检查（仅用于模拟/开发验证），
   `SEND_TO_FILEHELPER_WX_BACKEND=free|plus` 可强制 Windows 自动化后端（默认 `auto`：装了 Plus
   就用 Plus），`SEND_TO_FILEHELPER_SKIP_CLIENT_CHECK=1` 可跳过 Windows 客户端版本预检，
   `SEND_TO_FILEHELPER_NO_REOPEN=1` 可禁止脚本自动 `open -b` 重开微信主窗口，
   `SEND_TO_FILEHELPER_SHOW_ADS=1` / `SEND_TO_FILEHELPER_ALLOW_TELEMETRY=1` 可放行
   wxauto4 免费版的推广输出与遥测（默认关闭）。
