---
name: send-to-filehelper
description: Send local files and text messages to WeChat "文件传输助手" (File Transfer Helper) or a named contact on Windows and macOS / 在 Windows 与 macOS 上把本地文件或文字发送到微信「文件传输助手」或指定好友、群聊。当用户说“把文件发到微信”“发送到文件传输助手”“传到手机”“发我微信上”“把这段文字发到我微信”时使用。
metadata:
  author: 邱张华(qiuzhanghua@msn.com)
license: MIT
---

# Send to FileHelper / 发送文件与文字到微信

## Overview / 概述

**实现方式是 RPA（Robotic Process Automation，机器人流程自动化）**：本 skill 不调用微信的任何
接口，也**不依赖任何第三方微信自动化包**，而是**模拟人操作你已经登录的桌面微信客户端**——
激活窗口、模拟点击、剪贴板粘贴、合成按键，并用客户端自带的辅助功能树（macOS）或窗口截图 +
Windows OCR（Windows）来定位与校验。

This skill sends **local files and/or text messages** to WeChat's **文件传输助手** (File Transfer
Helper) — or to a named contact/group — by driving the **already logged-in WeChat desktop client**
of the user. Two platform backends share one CLI, both implemented inside this skill:

| Platform | Backend | Mechanism |
| --- | --- | --- |
| Windows | `scripts/wechat_win.py`（自研 RPA） | 窗口激活 + `PrintWindow` 截图 + Windows OCR 地标 + 剪贴板/合成键鼠 |
| macOS | `scripts/wechat_mac.py`（`pyobjc`，Accessibility API） | macOS AX tree + clipboard paste + synthetic keys |

本 skill 把**本地文件和/或文字**发送到微信「文件传输助手」（或指定好友/群聊），两个平台共用同一
个命令行：

| 平台 | 后端 | 原理 |
| --- | --- | --- |
| Windows | `scripts/wechat_win.py`（自研 RPA，无第三方依赖） | 窗口激活 + 截图 + Windows OCR 定位地标 + 剪贴板/合成键鼠 |
| macOS | `scripts/wechat_mac.py`（pyobjc，Accessibility API） | 读取微信 4.x 辅助功能树 + 剪贴板粘贴 + 合成键鼠事件 |

Both backends only operate the user's own logged-in client: no protocol reverse-engineering, no
injection, no bypassing of WeChat limits. 两个后端都只操作用户本人已登录的客户端界面，不做协议
破解、不注入、不绕过微信限制。

**因为是 RPA，所以有硬性前提**：**微信必须已经打开并登录、主窗口保持可见**（不能最小化、不能只留
托盘）；Windows 端建议**先手动打开「文件传输助手」会话**；桌面必须解锁；**运行期间不要动
鼠标键盘**。详见「[Windows 后端](#windows-后端自研-rpa--built-in-rpa-backend)」一节。

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
| Client | Windows 版微信，已登录，主窗口未最小化（**RPA 需要真实可见窗口**） | 微信 Mac 版 **4.x**（`WeChat.app`），已登录 |
| Python | 由 `uv` 自动管理（`>=3.10,<3.13`） | 同左 |
| Dependency | `pywin32` + `pillow` + `uiautomation` + `winrt-Windows.*`（OCR），全部由 uv 自动安装；**不需要 wxauto4 / wxautox4** | `pyobjc-framework-Cocoa / Quartz / ApplicationServices`（uv 自动安装） |
| Permission | 无额外授权（**桌面必须已解锁且可交互**） | **必须**授予「辅助功能」权限给运行命令的宿主 App |
| Input mode | 剪贴板 + 合成键鼠（会话标题 OCR 硬校验） | 辅助功能模式（可校验）或键盘模式（微信 4.1.x 实测走这条） |
| Tool | [`uv`](https://docs.astral.sh/uv/) | 同左 |

Linux is not supported. Linux 不支持。

> **实现方式是 RPA（模拟人操作界面）**，两个平台都一样：脚本驱动的是**你已经登录、已经打开的
> 微信窗口**，不做协议破解也不注入。所以运行前请把**微信打开并登录、主窗口保持可见**；
> Windows 端尤其建议**先手动打开「文件传输助手」会话**，并让桌面保持解锁、运行期间不要动鼠标
> 键盘。详见下文「[Windows 后端](#windows-后端自研-rpa--built-in-rpa-backend)」一节。

### Windows: why RPA and not UI Automation / 为什么 Windows 端走 RPA

**微信 4.x 的 Windows 客户端几乎不向 UI Automation 暴露界面控件。** 实测（客户端 `4.1.12.55` /
`4.1.13.65`）：主窗口 `Qt51514QWindowIcon`（进程 `Weixin.exe`）的 UIA 子树只有两个节点
（`Qt51514QWindowIcon`/`Weixin` 与 `MMUIRenderSubWindowHW`），**完全没有 `mmui::*` 控件**；
把窗口最大化到 3872×2072 也一样（微信 4.x 只给可见区域注册控件，但这里不是尺寸问题）。

这正是第三方包（`wxauto4` / `wxautox4`）在本机报 `未找到已登录的客户端主窗口` 并白等约 120 秒
的原因——**不是登录、最小化或权限问题，重试也不会成功**。本 skill 因此**删除了对这类包的依赖**，
改为自研 RPA 后端：不读控件树，而是截图 + OCR 找地标，再合成键鼠操作。

**排查工具**（微信大版本更新后建议跑一次；只读，不发送）：

```bash
uv run scripts/wechat_win.py                # 打印主窗口、当前会话标题、输入框/工具栏/「发送」按钮地标
uv run scripts/wechat_win.py --shot wx.png  # 同时存一张窗口截图，人工核对 OCR 是否读对了
uv run scripts/wechat_win.py --session 文件传输助手   # 只测试切换会话（不发送）
```

**必须在普通终端（非受限沙箱）里运行**：本后端要截取微信窗口、把鼠标移到屏幕上并注入按键，
受限沙箱会让 UIA/OCR 调用阻塞、`SetCursorPos` 报「拒绝访问」，看起来像代码坏了。

脚本因此在 Windows 上做了两件事：

1. **前端预检**（纯 stdlib，不依赖后端）：枚举正在运行的 `Weixin.exe` / `WeChat.exe`，读它的文件
   版本，检查主窗口是否存在；最小化的主窗口会被自动还原。没有微信 / 主窗口在托盘时**直接给出结论**，
   不再让你等一次超时。
2. **自研 RPA 后端**：窗口激活 + 截图 + OCR 地标 + 剪贴板/合成键鼠（见下节）。

### Windows 后端：自研 RPA / Built-in RPA backend

**实现方式是 RPA（Robotic Process Automation，机器人流程自动化）——这点必须先讲清楚。**

它不给微信装插件、不注入进程、不调私有协议、不碰账号凭证，而是**像人一样操作你已经登录的
微信界面**：激活窗口、模拟鼠标点击、剪贴板粘贴、合成按键，再用 Windows OCR 读屏确认。
所以它的能力边界和"一个人坐在电脑前点鼠标"完全一样——**界面挡住了、窗口不在、输入被抢，
它就做不了**。

#### 使用前必须满足（缺一不可）

1. **微信必须已经打开并登录**，且**主窗口完整可见**——不能最小化、不能只留在托盘/系统栏。
   窗口太窄也不行（会话名会显示成「文件传...」或被挤没，脚本会先把窗口拉宽再读，但仍建议留够宽度）。
2. **请先把「文件传输助手」会话打开、停在聊天界面**。脚本会自动去左侧会话列表里找目标并点击，
   点击后再用 OCR **硬校验**会话标题，所以技术上不要求你预先打开；但 RPA 最稳、最省事的用法就是
   **目标会话已经打开**。想完全不触发切换动作（也就不会碰到搜索/会话列表），加 `--no-switch`，
   它只发当前会话，命令行报出来的 `当前会话` 会告诉你是不是对的。
3. **桌面必须已解锁且可交互**。锁屏 / RDP 断开时 `SetCursorPos` 会返回「拒绝访问」，
   任何键鼠自动化都不工作（分辨率还可能被系统降级）。
4. **运行期间不要动鼠标键盘**。你和脚本会互相抢输入，可能把内容发到别的地方；
   自动化期间请让微信窗口保持在前台不要手动切换。

**为什么要有它**：微信 4.x 根本不暴露 UIA 控件树，任何"读控件"的方案都失效（见上）。
`scripts/wechat_win.py` 就是为此写的——完全不用 UIA 控件树，走的就是上面这套 RPA 动作：

| 环节 | 做法 |
| --- | --- |
| 定位界面 | `PrintWindow` 截窗口 + **Windows OCR** 读文字及其坐标（地标） |
| 窗口尺寸 | 会话栏是**固定像素宽**（可拖动分隔条、不按比例缩放），所以不能按窗口宽度算比例；脚本用 OCR 行量出会话栏右边界（=聊天区左边界），读不到目标名就自动把窗口拉宽 |
| 会话校验 | OCR 读聊天区标题，**发送前硬校验**当前会话（安全底线） |
| 切换会话 | 先用 OCR 精确匹配会话列表里的行，匹配不到再退化为「唯一前缀匹配」，以适配「文件传...」这种被截断的显示 |
| 发送文本 | 聚焦输入框 → 剪贴板 + `Ctrl+V` → `Enter` |
| 发送文件 | 用像素分析量出工具栏第 3 个图标（「发送文件」）→ 点它 → 文件对话框填路径 → 回车确认 → **再点界面上的「发送」按钮**（附件挂上后回车提交不了） |
| 发送校验 | 多次催重绘后 OCR，确认内容出现（微信重绘是异步的，单帧常是旧画面） |

```bash
# 推荐带上 --no-verify：跳过**发送后**的 OCR 结果复核。发送前的会话校验与输入框确认都保留。
uv run scripts/send_to_filehelper.py -m "说明" --no-verify     # 发文本
uv run scripts/send_to_filehelper.py ./报告.pdf --no-verify    # 发文件
```

### 耗时（实测）/ Speed

前提就是上面那 4 条**都满足**（微信已打开并停在目标会话、窗口在前台、桌面已解锁、没人抢键鼠）。
在这台机器上（Windows 11 + 微信 4.1.13.65，1877 × 1491 的窗口，Release 版脚本 + uv 启动）：

| 操作 | 默认（带发送后核对） | 加 `--no-verify` | 加 `--blind` |
| --- | --- | --- | --- |
| 发 1 条文本 | **约 7 s** | 约 6 s | 约 5 s |
| 发 1 个文件 | **约 8.5 s** | 约 7 s | — |
| 发 2 个文件（一批） | 约 16 s | — | — |

时间去向（`--timing` 会打印这张表）：发文本 ≈ 2.4s 固定等待（点输入框/粘贴/回车）+
1~2 帧"输入框那一条"的 OCR（每帧约 0.65s）+ 0.7s 会话校验 + 0.4s 合成点击；
发文件 ≈ 4s 花在系统文件对话框（等它弹出、填路径、等附件挂上、点「发送」）+ 4s 地标 OCR。

对照：做这些优化之前，同一台机器上发一条文本约 **35 s**、发一个文件约 **46 s**。

**用户越"按要求来"，脚本做的事越少**（这一版专门为此做了优化）：

| 条件满足时 | 脚本会怎么做 |
| --- | --- |
| 微信**已经在前台** | 不再抢前台（原来每次都 Alt + `SetForegroundWindow` + 睡 0.5s，一次发送要抢十几回） |
| 微信**已经在目标会话** | `ChatWith` 直接返回，不点会话列表、不触发重绘 |
| 1.2 秒内**刚取过地标帧** | 直接复用那一帧（窗口没动、前台状态没变时），省掉整次"截图 + OCR" |
| 只需要判断**输入框**里的内容 | 只 OCR 窗口底部那一条（约 0.3s），不做整窗 OCR（约 0.85s） |
| 附件**已经挂上** | 不再傻等固定秒数，立刻点「发送」 |
| 切换会话后**已核对上** | 不再多等；核对不上才继续重试 |

**条件不满足时，脚本立刻说清楚，不再硬撑**。启动时先花几微秒检查这几项，命中就直接报错退出
（不会先去截图/OCR 折腾二十几秒再报一个含糊的错）：

| 情况 | 立刻给出的结论 |
| --- | --- |
| 微信没启动 | `没有检测到正在运行的微信客户端（检测到安装位置: …）` |
| 主窗口关进了托盘 | `微信在运行，但没有找到可见的主窗口` |
| 主窗口被最小化 | 先尝试自动还原；还原不了才报 `微信主窗口被最小化或隐藏了` |
| 主窗口被拖到屏幕外 | `微信主窗口不在屏幕可见区域内（左,上-右,下）` |
| 锁屏 / RDP 会话断开 | `当前桌面不可交互（锁屏 / 远程桌面会话已断开）` |
| 缺少 OCR 依赖 | `未安装 Windows OCR 组件`（或 `--check` 里的 `Windows OCR: 不可用`） |

要自己看时间分布：`uv run scripts/send_to_filehelper.py ./报告.pdf --timing`
（也支持 `SEND_TO_FILEHELPER_TIMING=1`，`--check` 同样适用）。

**这个后端的注意事项**（都在实测中踩过）：

- **必须在“已解锁的交互桌面”上运行**。锁屏、RDP 断开时 `SetCursorPos` 会返回「拒绝访问」，
  任何键鼠自动化都无法工作（分辨率也可能被系统降级）。这不是脚本的问题。
- **必须开启 DPI 感知**（脚本内部已调用 `SetProcessDpiAwareness`）。否则 `GetWindowRect`
  返回被虚拟化缩放的坐标，截图区域与 OCR 坐标整体错位，表现为**聊天区一片空白、读不到控件**。
- **发送期间不要让微信最小化**。最小化后 `GetWindowRect` 约为 (-32000, -32000)，按它换算出的点击坐标远在屏幕外。脚本会先还原窗口并在每次点击前校验位置，越界就放弃该次点击并报错（不再夹到屏幕角落静默点错）。
- **微信的界面重绘是异步的**：刚发完消息时截图很可能还是旧画面。脚本会用
  `RedrawWindow` 催重绘 + 多次截图取并集来做校验，不要根据单张截图判断成败。
- **工具栏图标位置是量出来的，不是猜的**。图标间距随窗口宽度变化，早先"猜偏移"时第一下经常点到
  旁边的「收藏」图标（表现为"每次都先去点发送收藏"）。现在按行的"墨量"切出图标簇，第 3 个即
  「发送文件」，一次命中。
- **文件对话框是系统对话框**：点开「发送文件」后它会抢前台，脚本填完路径回车后会把主窗口重新
  置前再点「发送」。若此时你手动切走了窗口，可能失败。
- 发给**非默认会话**时，OCR 名称必须能精确匹配；不确定就用默认的「文件传输助手」。
- **发送后复核用的是后端自己的结论**：后端在发送当场就逐条核对（`_text_present`，带 OCR 标点
  容错），复核不到**不算失败**，只打印 `消息校验：…未能复核…` 并以退出码 0 结束
  （早先上层还会再做一遍整窗 OCR，既多花约 5 秒、又因为不认识 `16:25:27` → `16 ： 25 ： 27`
  这种 OCR 变体而把**已经发出去**的消息判成失败并以 1 退出）。**判定是否送达请看微信界面本身。**
- **建议加 `--no-verify`**：跳过发送后的核对（省约 1~2 秒）。OCR 除了滞后还会**认错字**
  （实测 `性`->`陸`、`3`->`引`）。发送**前**的两道 OCR 都保留：会话识别与硬校验
  （用不存在的会话名测过，仍 exit=1 拒绝发送）、以及输入框确认。发错人的安全底线不变。
- **要最快就用 `--blind`（盲发）**：连发送前的输入框确认也跳过，消息内容 OCR 一次都不做。代价是**不再确认文字是否真的粘进了输入框**，粘贴失败时回车会空按（不会发错人，但可能什么都没发）。会话识别与硬校验仍然保留。
- 机器上可能**同时开着两个微信**（一个已登录、一个停在扫码登录页）。脚本靠 OCR 区分
  （有「搜索/发送」的是主窗口，有「扫码登录/仅传输文件」的是登录页），不会选错。
- 排障用 `uv run scripts/wechat_win.py`：它把窗口尺寸、当前会话标题、输入框/工具栏/「发送」
  按钮的地标全部打印出来，并可用 `--shot wx.png` 存一张截图人工核对。

`SEND_TO_FILEHELPER_SKIP_CLIENT_CHECK=1` 可跳过 Windows 客户端预检强行尝试。

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

# Windows 排障：打印 RPA 地标（当前会话标题、输入框/工具栏/「发送」按钮位置）
uv run scripts/wechat_win.py

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
# Windows：本 skill 自己的 RPA 后端（scripts/wechat_win.py），无第三方依赖
import sys; sys.path.insert(0, "scripts")
from wechat_win import WinWeChat
wx = WinWeChat()
wx.ChatWith("文件传输助手", exact=True)   # OCR 硬校验会话名
wx.SendFiles([r'C:\你的文件路径\报告.pdf'])
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
| `--no-verify` | 关闭 | 跳过**发送后**的结果复核（自研后端为 `_text_present` + 上层 `verify_sent`）。发送前的会话校验与输入框确认**不受影响** |
| `--blind` | 关闭 | **盲发**：跳过**发送前**的输入框确认 + **发送后**的结果复核（隐含 `--no-verify`）。自研后端的消息内容 OCR 全部不做，最快。**不影响**会话识别与硬校验——发错人的安全底线仍在。仅 Windows 自研后端生效 |
| `--dry-run` | 关闭 | 只打印待发送的文本与文件，完全不操作微信，可在任意平台执行 |
| `--check` | 关闭 | 只做环境自检（权限 / 微信 / 窗口 / AX 元素 / 用哪种输入模式），不发送 |
| `--timing` | 关闭 | 打印后端各环节耗时（截图 / OCR / 合成点击…），用于排障与提速（Windows，等价 `SEND_TO_FILEHELPER_TIMING=1`） |
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

### Windows backend / Windows 后端（`scripts/wechat_win.py`）

0. **前置条件门禁**（几微秒，先于任何截图/OCR）：窗口存在且可见、没被最小化、落在屏幕内、
   桌面可交互（`SetCursorPos` 探一下）——任一不满足**立刻**带着结论报错退出；
1. `WinWeChat()` 找到主窗口（按面积挑最大的 Qt 窗口，并用 OCR 打分排除停在扫码登录页的第二个
   微信进程）；已在目标会话、窗口已在前台时，后续步骤会走快速通道（见上「耗时」一节）；
2. `wx.ChatWith(target, exact=True)`：OCR 读会话列表 → 精确匹配 → 否则唯一前缀匹配（适配被截断的
   「文件传...」）→ 点击 → **重新取帧**复核聊天区标题（拿缓存帧等于没校验）；
3. `wx.ChatInfo()['chat_name']` 与目标比对，不一致即中止并列出候选会话；
4. 文本用 `wx.SendMsg(text)`：聚焦输入框 → 剪贴板 + `Ctrl+V` → **只 OCR 输入框那一条**确认文字
   进去了 → `Enter`；
5. 文件用 `wx.SendFiles([...绝对路径...])`：像素分析定位工具栏「发送文件」图标 → 系统文件对话框
   填完整路径 → 回车 → **轮询等附件挂上**（文件名出现在输入框）→ 把主窗口置前 →
   **点界面上的「发送」按钮**（附件挂上后回车提交不了）；
6. 逐条核对（`_text_present` / `_wait_attachment`，带 OCR 标点容错），结果记在
   `self_confirmed` / `self_missing` 交给上层；上层不再重复做整窗 OCR。

后端的返回值与「显式成功/无法判定」的归一化处理见 `describe_result()`；核对不到**不算失败**
（异步重绘 + OCR 认错字），会打印 `消息校验：…未能复核…` 并以退出码 0 结束。

性能相关的三个设施：`FRAME_TTL` 帧缓存、`_region_lines()` 局部 OCR、`--timing` 计时报告
（`SEND_TO_FILEHELPER_TIMING=1`）。

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
| Windows 报 `未找到已登录的客户端主窗口` | 那是第三方包（wxauto4/wxautox4）的错误，本 skill 已不再使用它们。若你在别处见到，原因见「[Windows: why RPA and not UI Automation](#windows-why-rpa-and-not-ui-automation--为什么-windows-端走-rpa)」 |
| Windows `无法连接微信 PC 客户端` | 启动并登录微信并保持主窗口打开（不要只留在托盘）；`--check` 会打印检测到的客户端路径、版本与主窗口状态 |
| Windows 提示「没有找到可见的主窗口」 | 微信被关进托盘/系统栏了：点开微信主窗口后重试（脚本只自动还原最小化，不会从托盘唤起） |
| 想确认当前界面地标是否被正确识别 | 跑 `uv run scripts/wechat_win.py --shot wx.png`：打印会话标题、输入框/工具栏/「发送」按钮坐标，并存一张截图供人工核对 |
| Windows 报 `SetCursorPos ... 拒绝访问` | **当前桌面不可交互**（锁屏 / RDP 断开）。解锁屏幕后重试；这时任何键鼠自动化都不工作 |
| Windows 报「会话列表里没有找到「X」」 | RPA 只能操作**界面上已经显示出来的东西**：先手动打开微信主窗口并停在目标会话（最省事就是直接打开「文件传输助手」），或用 `--no-switch` 只发给当前已打开的会话 |
| Windows 报「当前会话是「文件传...」，与目标不一致」 | 窗口太窄，会话名被截断显示了。脚本会自动把窗口拉宽重读，也会退化为「唯一前缀匹配」；仍不行就手动把窗口拉宽、或把左右栏的分隔条往右拖一点。**左右栏宽度是固定像素、不是按比例缩放的**，所以拖一次就稳定了 |
| Windows 读到的界面"一片空白"、找不到「发送」 | 多半是 DPI 感知没生效（脚本已内置）或窗口被盖住/比屏幕还大。脚本会先把窗口收成完整可见的尺寸并催重绘 |
| Windows 点击「全都不对」（点到屏幕角落或别的窗口） | 微信窗口在发送过程中被**最小化**或被挪走了。脚本会先还原最小化窗口、每次点击前校对窗口位置，坐标越界时**直接报错**而不是夹回屏幕盲点。保持微信主窗口打开可见即可避免 |
| 每次先去点了「收藏」才点到「发送文件」 | 旧版用「猜偏移」定位工具栏图标；现在改为按像素墨量切出图标簇（第 3 个 = 发送文件），一次命中。若再现，请用 `wechat_win.py` 打印图标 x 列表并反馈 |
| 认为自己"发送失败"但对方其实收到了 | 微信界面重绘是异步的，**单张截图可能是旧画面**。以会话列表的预览文字为准，或稍等再截图核对 |
| 消息含标点/数字时总报「未能复核」，但其实已经发出 | 复核现在用后端自己的判定（NFKC + 标点归并 + 去空格后比对全文），这类误报已基本消除；仍复核不到时以微信界面为准 |
| 发送完成后还要等十几秒才结束 | 那是发送后的 OCR 结果复核在空转。现在复核由后端在发送当场完成（带标点容错），不再做整窗二次 OCR；要更快就加 `--no-verify` |
| 发送很慢（几十秒） | 先看前置条件是否真的满足（微信在前台、已停在目标会话、桌面没锁）。用 `--timing` 看时间花在哪儿：正常应是"发文本约 7s / 发文件约 8.5s"。若 `refresh` 次数远多于 3 次，多半是窗口不在前台或被遮挡，脚本在反复重试 |
| 附件已经挂到输入框、但一直没发出去 | 微信里**挂上附件后回车提交不了**，必须点「发送」按钮。脚本已改为点按钮；若仍卡住，检查「发送」按钮是否被遮挡 |
| `当前会话是「X」，与目标「Y」不一致` | 名称不精确；用输出的候选列表修正 `--to`（也可用 `--no-exact` 放宽） |
| `发送后未在会话中发现任何文件消息` | 微信被遮挡/弹窗打断；保持窗口在前台、不要同时操作键鼠，然后重试 |
| 微信提示文件过大 | 客户端对大文件有限制（超过 `--max-size-mb` 会提示）；改用其他传输方式 |
| 每次调用弹出微信搜索面板（含「文件传输助手」并列的介绍/推荐条目） | 这是 macOS 键盘模式的行为（`Cmd+F`），脚本无法屏蔽。先手动打开目标会话，再用 `--no-switch` 发送即可完全不触发；也可用 `--search-delay 0.2` 缩短它显示的时间 |
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
   `SEND_TO_FILEHELPER_SKIP_CLIENT_CHECK=1` 可跳过 Windows 客户端预检，
   `SEND_TO_FILEHELPER_NO_REOPEN=1` 可禁止脚本自动 `open -b` 重开微信主窗口。
10. **第三方包已移除**：本 skill 曾支持用 `wxauto4` / `wxautox4` 作为 Windows 后端，但微信 4.1.12+
    不再向 UI Automation 暴露界面控件，这两个包必然失败，因此依赖与相关代码已全部删除。现在
    Windows 端只有本 skill 自研的 RPA 后端（`scripts/wechat_win.py`），无需安装任何微信自动化包。
