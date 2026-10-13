---
name: dev-env-inventory
description: 现场盘点本机可用的开发工具链、版本号与环境变量来源（每台机器都不同，必须现探而不能套用别处的清单）。先进入登录+交互 zsh 执行 c（. ~/cot/bin/activate）激活 cot，再探测 PATH 上每个工具的真实版本，WSL 下把 /mnt/c 的 *.exe 一并算进去。Inventory the dev toolchain actually available on this machine — runtimes, compilers, package managers, and where each comes from. 当用户问"这台机器上有哪些开发工具/工具链/编译器/运行时""c 激活后能得到什么""环境变量/PATH 里有什么""列一下本机装了什么""为新机器建环境基线"时必须使用——即使用户只说"运行 c 看看环境""帮我确认某工具在不在"也要触发。
metadata:
  author: 邱张华(qiuzhanghua@icloud.com)
---

# 本机开发工具链盘点

## 为什么必须现场探测

每台机器的工具链都不一样，差别还都藏在细节里：

- 版本管理工具的组件目录名自带版本（例如 `~/cot/lib/node_22.23.2_linux_amd64/`），换机器、换版本就不同；
- WSL 会把大量 `/mnt/c/...` 塞进 PATH，Windows 程序只能按 `foo.exe` 找到，`command -v foo` 查不到；
- 同一个工具可能同时有 Linux 版和 Windows 版（`cargo` 与 `cargo.exe`），来源不同、版本也可能不同。

所以结论只能来自本次探测。凭印象或套用上次的清单，会漏报、多报，版本号也会错。

## 环境入口：先搞清楚 `c` 做了什么

确定性的第一步是复现用户的环境，而不是假设 PATH 就是默认值。

- `c` 通常是 shell 里的 **alias**（例如 `. ~/cot/bin/activate`）。alias 只在 rc 里定义，所以必须用 `zsh -lic`（登录 + 交互）才能复现用户敲 `c` 之后的环境。
- 如果找不到 `c`，直接读它指向的脚本（`alias c`、`type c`、`~/.zshrc`）再决定怎么激活。
- 这类激活脚本一般会：设置工具链的 home 变量 → 把版本目录前置进 `PATH`（有时还有 `LD_LIBRARY_PATH`）→ 做幂等去重。想知道**持久**配置在哪，找它 source 的文件（例如 `~/cot/bin/env-cot.vars`）；想知道**本次会话**叠加了什么，找带会话 id 的文件。
- 想知道"哪些工具是被这套机制装/管的"：`cot list`（或对应 CLI 的 list 子命令）。

## 步骤

1. **取环境**。跑两次做对照，才知道 `c` 到底改了什么：

   ```bash
   zsh -lic 'env' | sort > /tmp/env-before-c.txt
   zsh -lic 'c >/dev/null 2>&1; env' | sort > /tmp/env-after-c.txt
   diff /tmp/env-before-c.txt /tmp/env-after-c.txt
   zsh -lic 'c >/dev/null 2>&1; print -l $path'      # PATH 逐行，便于看顺序与去重
   ```

2. **探工具**。用本技能自带的脚本（默认只打印找到的，避免把上百行 "NOT FOUND" 灌进上下文）：

   ```bash
   zsh -lic 'c >/dev/null 2>&1; exec bash <skill>/scripts/probe-devtools.sh'
   #   --missing     连未安装的一起列（用户问"有没有 X"时用）
   #   --dirs        列出 PATH 每个目录里的可执行文件（找没被点名的工具）
   #   --no-windows  纯 Linux 机器上跳过 /mnt/c
   ```

3. **核对来源**。命中路径落在哪里，就说明它从哪来：`~/cot/lib/<name>_<ver>`/`~/cot/repo/*` 是被版本管理器装的；`~/bin`、`~/.local/bin` 是用户自建；`/usr/bin`、`/usr/local/bin` 是发行版或手工安装；`/mnt/c/...` 是 Windows 侧。版本号要和出处对得上，别把 Windows 版的版本号当成 Linux 版的。

4. **汇报**。按类别分组：语言运行时 / 包管理器 / 编译构建 / VCS / 容器与云 / CLI 增强 / GPU / 文档排版 / Windows 侧，并给一行"环境入口"小结（激活了什么、关键变量有哪些、PATH 目录数）。用户没问的工具不要铺开；`--missing` 的长列表更不要倒给他。

## 输出模板

```markdown
## 环境入口
`c` → <激活脚本>，PATH 目录 N 个；关键变量：GOROOT/GOPATH/CARGO_HOME/...
## 版本管理器装的
- go 1.27.1 · node 22.23.2 · python 3.10–3.15 · uv 0.13.0 ...
## 发行版 / 系统
- ...
## Windows 侧（WSL 互操作）
- ...
```

## 坑

- **受限环境下包管理器缓存可能只读**：`uv run`/`uvx` 会报 `Could not acquire lock ... Read-only file system`。把 `UV_CACHE_DIR`（必要时还有 `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`/`UV_PYTHON_INSTALL_DIR`）指到可写目录即可。
- 交互式 rc 可能打印无关告警（例如 oh-my-zsh 建不了缓存目录），忽略即可。
- 探测一定加 `timeout … </dev/null`：个别工具或包装脚本会挂在读标准输入上。
- 有的工具不认 `--version`（`go version`、`openssl version`、`ssh -V`、`java -version`），有的对未知参数只回一句报错（`gofmt -version`）——两件事脚本都处理了。
- **运行期错误 ≠ 没安装**：容器/GPU 类工具在受限环境会打印错误（`podman` 的 `/run/user/1000` 只读、`nvidia-smi` 的 NVML 被拦），版本照样能从 `--version` 拿到。
- Windows 的 `*.cmd`/`*.bat`（`npm.cmd`、`tlmgr.bat`）在 bash 下不能直接执行；要看版本就找同名 `.exe`，或用 `cmd.exe /c`。

## 相关文件

- `scripts/probe-devtools.sh` —— 探测脚本本体，改候选清单/加类别时看它。
- `references/worked-example.md` —— 一份完整的实测输出（cot + WSL 机器），需要看"结论长什么样"或写汇报时参考；里面的版本号会过时，只做格式示例。
