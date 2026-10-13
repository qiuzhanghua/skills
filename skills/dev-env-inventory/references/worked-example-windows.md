# 实测示例：一台 Windows + cot 机器（pwsh）

下面是某台 Windows 11 机器 2026-08 的实例，用来示范"Windows 上汇报长什么样"。**版本号一定会过时，路径也会变**——只借格式，不要照抄数据。用户主目录记为 `~`。

配套的 Linux + WSL 示例见 `worked-example.md`，那份是 `c` → zsh alias 的形态。

## 环境入口

`c` → pwsh 里的 **alias**，定义在 `~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1`：

```powershell
function Invoke-CotActivate { param([string]$Argument) & "$HOME/cot/bin/activate.ps1" $Argument }
Set-Alias -Name c -Value Invoke-CotActivate
Set-Alias -Name t -Value Invoke-TdpActivate      # t = tdp 的 activate.ps1
```

`activate.ps1` 激活做的事（读脚本可确认）：

1. 补默认值 `COT_HOME`/`TDP_HOME`；
2. **丢掉继承来的外来会话**（`COT_SESSION_PID` 与当前 `$PID` 不符就删掉 `COT_SESSION_ID`）；
3. 摘掉上次由 `env-cot` 带进来的目录（记在 `$global:__CotManaged`），避免 `cot use` 换版本后旧目录永远留在 PATH；
4. 把 `$COT_HOME\bin` 前置进 PATH；
5. source 会话文件 `env-cot.session.<id>.ps1`（优先）或持久文件 `env-cot.ps1`；
6. 幂等去重 PATH（Windows 路径大小写不敏感）；
7. 注册两个自愈钩子：包装 `prompt` + `Register-EngineEvent PowerShell.OnIdle`，`env-cot` 指纹变了就自动重新 source；
8. 设 `COT_SESSION_ID`/`COT_SESSION_PID`/`COT_HOOK=PowerShell`/`COT_SHELL=PowerShell`，并在退出时删掉自己的会话文件。

激活后 PATH 共 **69** 项。cot 关心的目录顺序（前面的优先）：`cot\lib\go_1.27.1_windows_amd64\bin` → `cot\repo\go\bin` → `cot\lib\python_3.12.13_windows_amd64` → `cot\lib\uv_0.12.9_windows_amd64` → `cot\repo\uv\bin` → `cot\bin` → 其余系统目录。

关键变量（持久文件 `~/cot/bin/env-cot.vars.ps1`）：

| 变量 | 值 |
| --- | --- |
| `GOROOT` / `GOPATH` / `GOPROXY` | `~/cot/lib/go_1.27.1_windows_amd64` / `~/cot/repo/go` / `https://goproxy.cn,direct` |
| `CARGO_HOME` / `RUSTUP_HOME` | `~/cot/repo/cargo` / `~/cot/cache/rustup` |
| `RUSTUP_DIST_SERVER` / `RUSTUP_UPDATE_ROOT` | `https://rsproxy.cn` / `https://rsproxy.cn/rustup` |
| `UV_HOME` / `UV_CACHE_DIR` | `~/cot/lib/uv_0.12.9_windows_amd64` / `~/cot/cache/uv` |
| `UV_DEFAULT_INDEX` | `https://pypi.tuna.tsinghua.edu.cn/simple`（注意与仓库脚本里的 `mirrors.tuna.tsinghua.edu.cn` 不是同一个地址） |
| `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` / `UV_PYTHON_INSTALL_DIR` | `~/cot/repo/uv/{tools,bin,python}` |
| `PYTHON_HOME` | `~/cot/lib/python_3.12.13_windows_amd64` |

profile 里另外设的（**不属于 `c`**）：两个密钥（`DEEPSEEK_API_KEY`、`GITHUB_TOKEN`）、`NATIVE_SDK_PATH`、Chocolatey 的 `ChocolateyInstall`；以及 `docker`→`podman`、`docker-compose`→`podman-compose` 两个 alias。

`~/.gitconfig` 之外还有一处会影响探测：profile 把 `ls`/`cat`/`mv`/`cp`/`rm`/`echo`/`pwd`/`sort`/`tee`/`head`/`tail`/`wc`/`tr`/`mkdir`/`ln` 定义成**函数**，转发到 `C:\Program Files\coreutils\bin\*.exe`。所以这些名字在 `Get-Command` 里是 Function，不是 Application。

## 版本管理器装的（`cot list`）

```
go_1.27.1_windows_amd64
python_3.12.13_windows_amd64
python_3.14.4_windows_amd64
uv_0.12.9_windows_amd64
```

目录形态是 `~/cot/lib/<name>_<ver>_windows_<arch>`（Linux 侧则是 `..._linux_amd64`，同名工具的目录名不同），仓库在 `~/cot/repo/{cargo,go,uv}`，缓存 `~/cot/cache/{rustup,uv}`。

## 系统 / 手动安装

**msys2 / ucrt64**：`gcc`/`g++`/`cc`/`c++` 15.2.0 · `ld`/`nm`/`objdump`（binutils 2.46）· `zstd` 1.5.7。

**系统自带**：`curl` 8.21.0（`C:\Windows\system32`）· `tar` = bsdtar 3.8.8 · `ssh` OpenSSH_for_Windows_9.5p2。

**独立安装**：`cmake` 4.3.3 · `git` 2.47.1.windows.1 + `git-lfs` 3.6.0 · `podman` 5.4.1（`docker` 是它的 alias）· `dotnet` 8.0.403 · `choco` 2.7.1 · `winget` 1.29.380 · `pwsh` 7.6.6 + Windows PowerShell 5.1 · `nu` 0.112.2 · `oh-my-posh` 24.6.5 · TeX Live 2024（`xelatex`/`pdflatex`/`latexmk`/`tlmgr`）· `pandoc` 3.6 · `ollama` 0.40.2 · llama.cpp（`llama-cli`/`llama-server` build 9553，winget 装的）· `perry` 0.5.345 · `mpiexec`（Microsoft MPI）· `code` 1.140.0 · `zed` 1.16.1。

**GPU**：CUDA 13.1（`nvcc`；PATH 里还留着 12.8、12.4 的 bin）· Nsight Compute 2025.4.1（`ncu.bat`）· `nvidia-smi` 596.58。

**cot 自己的**：`cot` 2.0.1 · `gpm` 1.0.0 · `which` 1.1.1。

## 注意点（都在本机实测到）

- **Store 别名 ≠ 已安装**：`python3`、`kubectl`、`kind`、`winget` 都落在 `%LOCALAPPDATA%\Microsoft\WindowsApps\`。脚本给这类命中打 `[Store 别名]` 标记。典型表现：`kubectl --version` 报 `error: unknown flag`（存根不是真 kubectl），`python3` 无输出。
- **`docker` 是 alias，`docker-compose` 的目标不在 PATH**：脚本对 alias 会跟到目标再取版本（`docker` → `podman 5.4.1`），目标缺失就报"别名，目标不在 PATH"。
- **`gofmt` 没有版本可报**：它对 `-version` 只回 `flag provided but not defined`，而裸参数 `version` 会被当成**待格式化的文件名**（`GetFileAttributesEx version: ...`）。这类输出已被过滤，结果记 `(无版本输出)`，别当成版本号。
- **`rust-analyzer.exe` 在 `~/cot/repo/cargo/bin` 里，但组件没装**：输出是 rustup proxy 的 `error: Unknown binary 'rust-analyzer.exe' in official toolchain ...`。文件存在 ≠ 工具可用。
- **`mpiexec` 不支持 `--version`**：它会把 `version` 当程序名去 launch，回 `failed to launch 'version'`。
- **Windows PowerShell 5.1 的报错是中文**：子进程按 CP936 写、脚本按 UTF-8 严格解失败后回退 CP936，所以看到的是 `所在位置 行:1 字符: 3` 这类正常中文，不是乱码。
- **PATH 里有"只差一个尾反斜杠"的重复目录**：如 `C:\Program Files\PowerShell\7` 与 `C:\Program Files\PowerShell\7\` 同时存在（cot 按整串去重，不规范化尾部分隔符）。

## 这台机器上常用的对照命令（pwsh）

```powershell
# 激活后到底多了什么
Compare-Object `
  (pwsh -NoLogo -Command 'Get-ChildItem Env: | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }') `
  (pwsh -NoLogo -Command 'c *> $null; Get-ChildItem Env: | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }')

# 只想知道某个工具在不在、什么版本
pwsh -NoLogo -Command 'c *> $null; Get-Command gh | Select-Object Source; gh --version'

# 探测（本技能自带脚本）
pwsh -NoLogo -Command 'c *> $null; & "$HOME\space\skills\skills\dev-env-inventory\scripts\probe-devtools.ps1"'
```

**diff 的两个噪声源**：`COT_SESSION_ID`/`COT_SESSION_PID` 在 before 快照里就存在（子进程**继承**了父 shell 的会话变量，`activate.ps1` 会主动丢弃 PID 不符的那份），`POSH_SESSION_ID` 则是每个 pwsh 会话各自生成。这两组不是 `c` 的贡献。

**更要注意**：如果探测是从"已经被激活过的 shell"里起的子进程（例如 agent 的宿主进程本身就继承了 `GOROOT`/`UV_HOME`/`COT_SESSION_ID`），那么 before 快照**已经带着激活后的变量**，diff 只会露出 PATH 和会话变量。想要干净的 before，得从不含 cot 环境的地方起（explorer 启动的 pwsh 最干净，或先显式 `Remove-Item Env:GOROOT,Env:UV_HOME,...`）。
