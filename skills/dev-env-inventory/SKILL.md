---
name: dev-env-inventory
description: 现场盘点本机可用的开发工具链、版本号与环境变量来源（每台机器都不同，必须现探而不能套用别处的清单）。先复现用户的环境入口再激活：Linux/macOS/WSL 是登录+交互 zsh 里执行 c（. ~/cot/bin/activate），Windows 是 pwsh 里执行 c（alias → ~/cot/bin/activate.ps1；-NoProfile 会话没有 c，用探测脚本的 -Activate）；然后探测 PATH 上每个工具的真实版本，WSL 下把 /mnt/c 的 *.exe 一并算进去，Windows 下识别 Store 别名（WindowsApps 存根）、alias（如 docker→podman）与 .cmd/.bat/.ps1 shim。Inventory the dev toolchain actually available on this machine — runtimes, compilers, package managers, and where each comes from. Works on both Windows (pwsh) and Linux/macOS/WSL (zsh). 当用户问"这台机器上有哪些开发工具/工具链/编译器/运行时""c 激活后能得到什么""环境变量/PATH 里有什么""列一下本机装了什么""为新机器建环境基线"时必须使用——即使用户只说"运行 c 看看环境""帮我确认某工具在不在"也要触发。
metadata:
  author: 邱张华(qiuzhanghua@icloud.com)
---

# 本机开发工具链盘点

## 为什么必须现场探测

每台机器的工具链都不一样，差别还都藏在细节里：

- 版本管理工具的组件目录名自带版本和平台（Linux 是 `~/cot/lib/node_22.23.2_linux_amd64/`，Windows 是 `~/cot/lib/uv_0.12.9_windows_amd64/`），换机器、换版本、换平台就不同；
- WSL 会把大量 `/mnt/c/...` 塞进 PATH，Windows 程序只能按 `foo.exe` 找到，`command -v foo` 查不到；
- Windows 的 PATH 里混着**应用执行别名**（`%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe`、`kubectl.exe` 这类 Store 存根）：文件在、命令也能跑，但**并没有真装**，执行只会得到一句报错；
- 同一个名字可能是 alias 或函数而不是可执行文件（`docker` → `podman`；有的 profile 把 `ls`/`cat` 包成 coreutils 的函数）；
- 同一个工具可能同时有 Linux 版和 Windows 版（`cargo` 与 `cargo.exe`），来源不同、版本也可能不同。

所以结论只能来自本次探测。凭印象或套用上次的清单，会漏报、多报，版本号也会错。

## 环境入口：先搞清楚 `c` 做了什么

确定性的第一步是复现用户的环境，而不是假设 PATH 就是默认值。`c` 在两种平台上都存在，但**形态不同**：

### Windows（pwsh）

`c` 是 pwsh 里的 **alias**，定义在 `~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1`：

```powershell
function Invoke-CotActivate { param([string]$Argument) & "$HOME/cot/bin/activate.ps1" $Argument }
Set-Alias -Name c -Value Invoke-CotActivate
```

所以 `c` 与 `~` 这类东西**只在加载了 profile 的 pwsh 里存在**：`pwsh -NoLogo -Command 'c ...'` 可以（默认加载 profile），而 `pwsh -NoProfile -Command 'c ...'` 会报找不到 `c`。后者改用探测脚本自带的 `-Activate`（它自己 dot-source `~/cot/bin/activate.ps1`）。

`activate.ps1` 一般会：补默认 `COT_HOME`/`TDP_HOME` → 丢掉继承来的外来 `COT_SESSION_ID`（PID 不符时）→ 摘掉上一代 `env-cot` 带进来的目录 → 前置 `$COT_HOME\bin` → source 会话文件（`env-cot.session.<id>.ps1`）或持久文件（`env-cot.ps1`/`env-cot.vars.ps1`）→ 幂等去重 PATH（**大小写不敏感**）→ 注册 `prompt` + `PowerShell.OnIdle` 自愈钩子。想知道"哪些工具是被这套机制装/管的"：`cot list`。

### Linux / macOS / WSL（zsh）

`c` 是 shell 里的 **alias**（例如 `. ~/cot/bin/activate`）。alias 只在 rc 里定义，所以必须用 `zsh -lic`（登录 + 交互）才能复现用户敲 `c` 之后的环境。

- 如果找不到 `c`，直接读它指向的脚本（`alias c`、`type c`、`~/.zshrc`）再决定怎么激活。
- 这类激活脚本一般会：设置工具链的 home 变量 → 把版本目录前置进 `PATH`（有时还有 `LD_LIBRARY_PATH`）→ 做幂等去重。想知道**持久**配置在哪，找它 source 的文件（例如 `~/cot/bin/env-cot.vars`）；想知道**本次会话**叠加了什么，找带会话 id 的文件。

## 步骤

1. **取环境**。跑两次做对照，才知道 `c` 到底改了什么。

   Windows（pwsh）：

   ```powershell
   pwsh -NoLogo -Command 'Get-ChildItem Env: | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }' > $env:TEMP\env-before-c.txt
   pwsh -NoLogo -Command 'c *> $null; Get-ChildItem Env: | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }' > $env:TEMP\env-after-c.txt
   Compare-Object (Get-Content $env:TEMP\env-before-c.txt) (Get-Content $env:TEMP\env-after-c.txt)
   pwsh -NoLogo -Command 'c *> $null; $Env:PATH -split ";"'      # PATH 逐行，便于看顺序与去重
   ```

   Linux / WSL（zsh）：

   ```bash
   zsh -lic 'env' | sort > /tmp/env-before-c.txt
   zsh -lic 'c >/dev/null 2>&1; env' | sort > /tmp/env-after-c.txt
   diff /tmp/env-before-c.txt /tmp/env-after-c.txt
   zsh -lic 'c >/dev/null 2>&1; print -l $path'      # PATH 逐行，便于看顺序与去重
   ```

   **diff 里有两类噪声**：`COT_SESSION_ID`/`COT_SESSION_PID`（子进程继承父 shell 的会话变量，激活脚本会主动丢弃 PID 不符的那份）和 pwsh 的 `POSH_SESSION_ID`（每会话新生成）。更要注意：如果探测是从**已经被激活过的 shell** 里起的子进程，before 快照本身就带着激活后的 `GOROOT`/`UV_HOME` 等，diff 只会露出 PATH——想要干净的 before，得从不含 cot 环境的地方起。

2. **探工具**。用本技能自带的脚本（默认只打印找到的，避免把上百行 "NOT FOUND" 灌进上下文）：

   Windows（约 30–60 秒；`-Missing` 会到 200 行以上）：

   ```powershell
   pwsh -NoLogo -Command 'c *> $null; & "$HOME\space\skills\skills\dev-env-inventory\scripts\probe-devtools.ps1"'
   #   -Missing    连未安装的一起列（用户问"有没有 X"时用）
   #   -Dirs       列出 PATH 每个目录里的条目（找没被点名的工具）
   #   -Activate   探测前先 dot-source ~/cot/bin/activate.ps1（-NoProfile 会话里没有 c 时用）
   ```

   Linux / WSL（bash）：

   ```bash
   zsh -lic 'c >/dev/null 2>&1; exec bash <skill>/scripts/probe-devtools.sh'
   #   --missing     连未安装的一起列（用户问"有没有 X"时用）
   #   --dirs        列出 PATH 每个目录里的可执行文件（找没被点名的工具）
   #   --no-windows  纯 Linux 机器上跳过 /mnt/c
   ```

   两个脚本输出格式一致：`tool <TAB> path <TAB> version`（三列，制表符分隔）。

   **在 Windows 上只盘 Windows 原生这一侧**：不要为了"补全"再去探 WSL 发行版里的内容（用户不需要，而且每次 `wsl.exe` 调用都要等发行版起来，动辄多花几分钟）。`--no-windows` 是 **WSL 侧专有**的开关，Windows 上没有对应项——在 Windows 上"另一侧"是 WSL，而按约定它不在盘点范围内。

3. **核对来源**。命中路径落在哪里，就说明它从哪来：

   | 命中路径 | 来源 |
   | --- | --- |
   | `~/cot/lib/<name>_<ver>_linux_amd64` / `..._windows_amd64` | 版本管理器（cot）装的，`cot list` 可核对 |
   | `~/cot/repo/*`、`~/cot/bin/*` | cot 的仓库与自带命令（`cot`/`gpm`/`which`） |
   | `~/bin`、`~/.local/bin` | 用户自建 |
   | `/usr/bin`、`/usr/local/bin` | 发行版包管理器或手工安装 |
   | `/mnt/c/...`（WSL 视角） | Windows 侧程序，只能按 `foo.exe` 找到 |
   | `C:\Program Files\*`、`%LOCALAPPDATA%\Programs\*` | Windows 上手工/winget 装的 |
   | `%LOCALAPPDATA%\Microsoft\WindowsApps\*` | **Store 应用执行别名**，可能是没装的应用存根 |
   | `C:\msys64\*` | msys2 里的 POSIX 工具链 |

   版本号要和出处对得上，别把 Windows 版的版本号当成 Linux 版的。Windows 上还要顺手判两件事：命中的是 alias 就**跟到目标再看版本**（`docker` → `podman`）；命中在 `WindowsApps` 就要怀疑它是不是存根。

4. **汇报**。按类别分组：语言运行时 / 包管理器 / 编译构建 / VCS / 容器与云 / CLI 增强 / GPU / 文档排版 / Windows 侧（WSL 视角）或系统与手工安装（Windows 视角），并给一行"环境入口"小结（激活了什么、关键变量有哪些、PATH 目录数）。用户没问的工具不要铺开；`-Missing`/`--missing` 的长列表更不要倒给他。

## 输出模板

```markdown
## 环境入口
`c` → <激活脚本>（<平台/形态>），PATH 目录 N 个；关键变量：GOROOT/GOPATH/CARGO_HOME/...
## 版本管理器装的（`cot list`）
- go 1.27.1 · node 22.23.2 · python 3.10–3.15 · uv 0.13.0 ...
## 发行版 / 系统 / 手工安装
- ...
## Windows 侧 / Windows 原生视角（按运行位置二选一，不要两边都盘）
- 在 WSL 里跑时：`/mnt/c/...` 里的 *.exe（dotnet、winget、TeX Live、CUDA ...）
- 在 Windows 上跑时：`C:\msys64` 的 gcc、`Program Files` 下的 cmake/git、Store 别名（python3、kubectl …）；**不要再去盘 WSL 发行版**
```

## 坑

- **受限环境下包管理器缓存可能只读**：`uv run`/`uvx` 会报 `Could not acquire lock ... Read-only file system`。把 `UV_CACHE_DIR`（必要时还有 `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`/`UV_PYTHON_INSTALL_DIR`）指到可写目录即可。
- 交互式 rc 可能打印无关告警（例如 oh-my-posh 建不了缓存目录），忽略即可。
- 探测一定加超时并把标准输入关掉（bash 用 `timeout … </dev/null`，Windows 脚本用 .NET Process + 关闭 stdin）：个别工具或包装脚本会挂在读标准输入上。
- 有的工具不认 `--version`（`go version`、`openssl version`、`ssh -V`、`java -version`），要按工具换写法——两个脚本都做了特例表。
- **"运行期错误"和"没安装"是两回事**：容器/GPU 类工具在受限环境会打印错误（`podman` 的 `/run/user/1000` 只读、`nvidia-smi` 的 NVML 被拦），版本照样能从 `--version` 拿到；反过来 `rust-analyzer.exe` 存在也可能因为组件没装而只回 rustup proxy 的报错。
- **别把报错当版本**：有的工具对未知参数只回一句报错，甚至把参数当文件名去打开（`gofmt -version` 报 flag 错，裸参数 `version` 变成"找不到文件"）。这类输出要过滤掉，宁可记 `(无版本输出)`。
- Windows 的 `*.cmd`/`*.bat`（`npm.cmd`、`tlmgr.bat`）在 bash 下不能直接执行；要看版本就找同名 `.exe`，或用 `cmd.exe /c`。反过来在 pwsh 下走 `cmd /c` 是可靠的。
- **pwsh 的 `Get-Command` 会认 alias 和函数**：`-Dirs` 列的是目录内容，函数不占目录，所以 profile 里包成函数的 `ls`/`cat`/`rm`（coreutils 转发）不会出现在目录清单里，要单独看 `Get-Command <name>`。
- **中文环境下子进程输出可能不是 UTF-8**：Windows PowerShell 5.1、dotnet 之类会按 CP936 写。探测脚本按"UTF-8 严格解 → 失败回退系统 ANSI 代码页"处理；自己写探测时别硬按单一编码解码。
- **PATH 里可能有只差尾反斜杠的重复目录**（`C:\Program Files\PowerShell\7` 与 `...\7\`），去重不规范化就看不出来。

## 相关文件

- `scripts/probe-devtools.ps1` —— Windows / pwsh 探测脚本（改候选清单、加类别、调超时看它）。
- `scripts/probe-devtools.sh` —— Linux / macOS / WSL 的 bash 探测脚本。
- `references/worked-example-windows.md` —— Windows + cot 机器的完整实测输出（含 Store 别名、alias、编码等本机实证）。
- `references/worked-example.md` —— Linux + WSL 机器的完整实测输出。两份示例的版本号都会过时，只做格式示例。
