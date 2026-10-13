#!/usr/bin/env bash
# 探测本机开发工具链。请在"已激活的环境"里运行（见 SKILL.md 第 1 步），
# 因为工具是否可见完全取决于 zsh rc + c / activate 之后的 PATH。
#
#   bash probe-devtools.sh             # 只打印探测到的工具
#   bash probe-devtools.sh --missing   # 连未安装的一起打印（看缺口用）
#   bash probe-devtools.sh --dirs      # 列出 PATH 每个目录里的可执行文件
#   bash probe-devtools.sh --no-windows  # 不探 /mnt/c 下的 *.exe（纯 Linux 机器上没必要）
#
# 输出：tool <TAB> path <TAB> version
set -u

# --- 兼容层：BSD/macOS 没有 GNU timeout（macOS 连 gtimeout 也没有）---
if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_BIN=timeout
elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_BIN=gtimeout
else
    TIMEOUT_BIN=""
fi

# 带超时地跑一条命令：优先后台守卫 + kill -9，macOS 上不需要 brew install coreutils。
# 守卫子壳的 fd 必须挪出管道，否则它会替管道多活满 secs 秒（实测：不加会从 44 秒劣化到 5 分钟以上）。
_t() {
    local secs="$1"; shift
    if [ -n "$TIMEOUT_BIN" ]; then
        "$TIMEOUT_BIN" "$secs" "$@"
        return
    fi
    "$@" </dev/null &
    local p=$! w
    ( sleep "$secs"; kill -9 "$p" 2>/dev/null ) >/dev/null 2>&1 & w=$!
    wait "$p" 2>/dev/null
    kill "$w" 2>/dev/null
    wait "$w" 2>/dev/null
    return 0
}

SHOW_MISSING=0
SHOW_DIRS=0
PROBE_WINDOWS=1
for arg in "$@"; do
    case "$arg" in
        --missing) SHOW_MISSING=1 ;;
        --dirs) SHOW_DIRS=1 ;;
        --no-windows) PROBE_WINDOWS=0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

# 版本探针：先试这个工具特有的写法，再退回到通用 --version/-V/version 链。
# 有些工具（go/openssl/ssh/tmux）根本不认 --version，会回一句错误，所以必须兜底。
version_of() {
    local cmd="$1" out="" flag
    case "$cmd" in
        go)      set -- version ;;
        openssl) set -- version ;;
        ssh)     set -- -V ;;
        tmux)    set -- -V ;;
        java|javac) set -- -version ;;
        podman|buildah) set -- --version ;;
        *)       set -- --version -version -V version -v ;;
    esac
    for flag in "$@"; do
        out="$(_t 6 "$cmd" "$flag" </dev/null 2>&1 \
               | tr -d '\r' | grep -v '^$' | head -n 2 | paste -sd' | ' -)"
        [ -n "$out" ] && break
    done
    # 有的工具对未知参数只回一句报错（gofmt -version），别把报错当成版本。
    case "$out" in
        *"flag provided but not defined"*|*"unknown option"*|*"unrecognized option"*|\
        *"invalid option"*|Usage:*|usage:*|*"Try '"*)
            out="" ;;
    esac
    printf '%s' "${out:0:140}"
}

probe() {
    local cmd="$1" path=""
    path="$(command -v "$cmd" 2>/dev/null)"
    if [ -z "$path" ] && [ "$PROBE_WINDOWS" = 1 ] && [ -d /mnt/c ]; then
        # WSL：/mnt/c 上的 Windows 程序只能按 foo.exe 找到，
        # command -v foo 不会替你补后缀。
        case "$cmd" in
            *.exe) ;;
            *) path="$(command -v "${cmd}.exe" 2>/dev/null)" ;;
        esac
    fi
    if [ -z "$path" ]; then
        [ "$SHOW_MISSING" = 1 ] && printf '%s\t%s\t%s\n' "$cmd" "-" "NOT FOUND"
        return
    fi
    local out
    out="$(version_of "$cmd")"
    [ -z "$out" ] && out="(无版本输出)"
    printf '%s\t%s\t%s\n' "$cmd" "$path" "$out"
}

show_dirs() {
    local dir name
    while IFS= read -r dir; do
        [ -d "$dir" ] || continue
        case "$dir" in
            # Windows 的系统目录有几百个 exe，对"有哪些开发工具"没有信息量。
            /mnt/c/Windows*|/mnt/c/WINDOWS*|"/mnt/c/Program Files/coreutils"*)
                printf '### %s  (已跳过：Windows 系统目录)\n\n' "$dir"; continue ;;
        esac
        name="$(ls -A "$dir" 2>/dev/null | grep -c . )"
        [ "${name:-0}" = 0 ] && continue
        printf '### %s  (%s 个条目)\n' "$dir" "$name"
        ls -A "$dir" 2>/dev/null | paste -sd' ' - | fold -s -w 165
        echo
    done < <(printf '%s\n' "$PATH" | tr ':' '\n' | awk '!seen[$0]++')
}

if [ "$SHOW_DIRS" = 1 ]; then
    show_dirs
    exit 0
fi

# 候选清单：按类别排，方便读。缺什么直接用 --missing 看。
CANDIDATES="
python python3 pip pip3 uv uvx pipx poetry virtualenv
node npm npx pnpm yarn bun deno corepack tsx tsc esbuild vite
go gofmt golangci-lint
cargo rustc rustup rustfmt clippy-driver
gcc g++ clang clang++ cc c++ ld nm objdump make cmake ninja meson autoconf automake pkg-config gdb lldb valgrind strace hyperfine just
git git-lfs gh glab svn hg jj
jq yq curl wget rg fd fzf bat eza zoxide tmux screen vim nvim emacs helix micro code cursor zed
docker podman docker-compose podman-compose kubectl helm kind minikube k9s skopeo buildah
terraform tofu ansible aws az gcloud vagrant packer
java javac mvn gradle kotlin kotlinc scala sbt groovy
dotnet msbuild nuget
ruby gem bundle rake php composer
lua luajit perl Rscript julia swift zig clang-format clang-tidy
ghc cabal stack clojure lein bb erl elixir mix dart flutter
protoc buf grpcurl sqlite3 psql mysql redis-cli mongosh duckdb
openssl gpg ssh rsync tar zstd xz 7z aria2c yt-dlp ffmpeg pandoc typst tectonic latexmk xelatex pdflatex quarto
opencode dsh mise asdf direnv starship zsh bash fish nu pwsh ollama nvidia-smi nvcc nsys ncu htop btop
uvicorn gunicorn pytest ruff black mypy ipython jupyter
"
# Windows 侧（WSL 互操作）常用工具，非 WSL 机器上会全部 NOT FOUND，用 --no-windows 跳过。
WIN_CANDIDATES="
winget choco scoop dotnet pwsh powershell py conda mamba uv
nu oh-my-posh llama-cli llama-server ollama perry oz mpiexec
rust-analyzer cargo-clippy cargo-fmt rustdoc
"

for c in $CANDIDATES; do probe "$c"; done
if [ "$PROBE_WINDOWS" = 1 ] && [ -d /mnt/c ]; then
    for c in $WIN_CANDIDATES; do probe "$c"; done
fi
