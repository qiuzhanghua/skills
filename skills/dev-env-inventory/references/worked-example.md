# 实测示例：一台 cot + WSL 机器

下面是某个 2026-10 的实例，用来示范"汇报长什么样"。**版本号一定会过时，路径也会变**——只借格式，不要照抄数据。用户主目录记为 `~`，Windows 侧记为 `C:\Users\<user>`。

## 环境入口

`c` → `. ~/cot/bin/activate`（zsh alias）。激活做的事：设 `COT_HOME`/`TDP_HOME` → 摘掉上次由 `__COT_MANAGED` 带进来的目录 → source 会话文件（`$COT_HOME/bin/env-cot.session.$COT_SESSION_ID`，`cot use` 写的）或持久文件 `env-cot`/`env-cot.vars` → 幂等去重 `PATH`/`LD_LIBRARY_PATH`。PATH 共 90 多个目录（含 50 多个 `/mnt/c/...`）。

关键变量：

| 变量 | 值 |
| --- | --- |
| `GOROOT` / `GOPATH` / `GOPROXY` | `~/cot/lib/go_1.27.1_linux_amd64` / `~/cot/repo/go` / `https://goproxy.cn,direct` |
| `CARGO_HOME` / `RUSTUP_HOME` | `~/cot/repo/cargo` / `~/cot/cache/rustup` |
| `NODE_HOME` / `PYTHON_HOME` | `~/cot/lib/node_22.23.2_linux_amd64` / `~/cot/lib/python_3.15.0_linux_amd64_gnu` |
| `UV_HOME` / `UV_DEFAULT_INDEX` | `~/cot/lib/uv_0.13.0_linux_amd64_gnu` / 清华 pypi 镜像 |
| `TYPST_HOME` / `OPENCODE_HOME` | `~/cot/lib/typst_0.15.1_linux_amd64_musl` / `~/cot/lib/opencode_2.0.27_linux_amd64_gnu` |
| `LD_LIBRARY_PATH` | `/usr/local/cuda/lib64` |

## 版本管理器装的（`cot list`）

`go 1.27.1` · `node 22.23.2`（另存 `20.20.2`）· `python 3.10.22 / 3.11.17 / 3.12.15 / 3.13.16 / 3.14.8 / 3.15.0` · `uv 0.13.0` · `typst 0.15.1` · `opencode 2.0.27` · `gh 2.102.0`

`~/bin` 里另有手动放的：`buck2`、`claw`、`dotslash`、`fresh`、`opengrep`。

## 发行版 / 系统

gcc 13.3 · clang 18.1.3 · cmake 3.28.3 · ninja 1.11.1 · make 4.3 · git 2.43.0 + git-lfs 3.4.1 · podman 5.4.1（rootless 在受限环境下会报 `/run/user/1000` 只读）· podman-compose · buildah · sqlite3 · openssl 3.0.13 · vim 9.1 · tmux 3.4 · perl 5.38 · screen · htop · pandoc 3.1.3 · zsh/bash/fish · CUDA 12.8（`nvcc`、`nsys`、`ncu`）· `nvidia-smi`（WSL 下 NVML 可能被拦，看版本用 `--version`）。

## Windows 侧（WSL 互操作，走 `/mnt/c/...`）

`dotnet 8.0.403` · `node v24.16.0` · `python 3.13.13` + pip · `uv` · `cmake 4.3.3`（含 ctest/cpack）· `git 2.47.1.windows.1` · `podman 5.4.1` · `kubectl 1.32.3` · `kind` · `winget` · `choco 2.7.1` · `pwsh 7.6.6` + Windows PowerShell 5.1 · `nu 0.112.2` · msys2 `gcc/g++ 15.2.0` · TeX Live 2024（`xelatex`/`pdflatex`/`tlmgr`）· `rustc`/`cargo 1.96.1` + `cargo-generate` · `ollama 0.40.2` · llama.cpp 一整套 `llama-*` · `nvidia-smi 596.58` · Nsight（`ncu`/`nsys`）· Microsoft MPI 10.1 · `code` · `zed` · `oh-my-posh`。

注意点：`docker` 可能是 `podman` 的 alias；`java` 存在但 `javac` 常常没有（只装了运行时而没装 JDK）；`npm.cmd`/`tlmgr.bat` 在 bash 下不能直接跑。

## 这台机器上常用的对照命令

```bash
# 激活后到底多了什么
diff <(zsh -lic 'env' | sort) <(zsh -lic 'c >/dev/null 2>&1; env' | sort)

# 只想知道某个工具在不在、什么版本
zsh -lic 'c >/dev/null 2>&1; command -v gh && gh --version'

# WSL 里 Windows 程序的版本
timeout 20 cargo.exe --version
```
