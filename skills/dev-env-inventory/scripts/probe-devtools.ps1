<#
.SYNOPSIS
  探测本机开发工具链（Windows / PowerShell 版，等价于 probe-devtools.sh）。

.DESCRIPTION
  请在"已激活的环境"里运行：`c`（= ~/cot/bin/activate.ps1）会带入 cot 的 PATH 与变量。
  输出三列，制表符分隔：tool <TAB> path <TAB> version

  与 bash 版的对应关系：
    bash 的 `</dev/null` + `timeout 6`  → 这里用 .NET Process（关闭 stdin + 超时杀进程）
    bash 的 `command -v`               → Get-Command（会一并认出 alias / function）
    bash 的 `--no-windows`（跳过 /mnt/c）→ Windows 上不存在"另一侧"，无对应开关；
                                          要看 WSL 侧就进 WSL 跑 probe-devtools.sh。

.PARAMETER Missing
  连未安装的一起打印（用户问"有没有 X"时用）。

.PARAMETER Dirs
  列出 PATH 每个目录里的条目（找没被点名的工具）。Windows 系统目录会跳过。

.PARAMETER Activate
  探测前先执行 $HOME\cot\bin\activate.ps1。用于 -NoProfile 的会话（那里没有 c 别名）。

.PARAMETER TimeoutSeconds
  单个工具的版本探测超时，默认 6 秒。

.EXAMPLE
  pwsh -NoLogo -Command 'c *> $null; & "$HOME\space\skills\skills\dev-env-inventory\scripts\probe-devtools.ps1"'

.EXAMPLE
  pwsh -NoLogo -File probe-devtools.ps1 -Activate -Missing
#>
[CmdletBinding()]
param(
    [switch]$Missing,
    [switch]$Dirs,
    [switch]$Activate,
    [int]$TimeoutSeconds = 6
)

$ErrorActionPreference = 'Continue'

if ($Activate) {
    # 注意：变量名是大小写不敏感的，别用 $activate 存路径（那是 SwitchParameter 参数本身）
    $activateScript = Join-Path $HOME 'cot\bin\activate.ps1'
    if (Test-Path $activateScript) {
        & $activateScript *> $null
    } else {
        Write-Warning "找不到 $activateScript，未激活 cot 环境"
    }
}

# 版本探针参数：先试这个工具特有的写法，再退回到通用链。
# 有些工具（go/openssl/ssh/tmux/java）根本不认 --version，会回一句错误，所以必须兜底。
function Get-VersionArgs {
    param([Parameter(Mandatory)][string]$Name)
    $n = [System.IO.Path]::GetFileNameWithoutExtension($Name).ToLowerInvariant()
    switch ($n) {
        'go'        { return @('version') }
        'openssl'   { return @('version') }
        'ssh'       { return @('-V') }
        'tmux'      { return @('-V') }
        'java'      { return @('-version') }
        'javac'     { return @('-version') }
        'podman'    { return @('--version') }
        'buildah'   { return @('--version') }
        'podman-compose' { return @('--version') }
        default     { return @('--version', '-version', '-V', 'version', '-v') }
    }
}

# 子进程输出解码：先按 UTF-8 严格解（Go/Rust 等直接写 UTF-8），
# 解不动再退回系统 ANSI 代码页（中文环境里本地化的报错多为 CP936）。
function Convert-BytesToText {
    param([byte[]]$Bytes)
    if (-not $Bytes -or $Bytes.Length -eq 0) { return '' }
    $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
    try {
        return $strictUtf8.GetString($Bytes)
    } catch {
        try {
            $cp = [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage
            return [System.Text.Encoding]::GetEncoding($cp).GetString($Bytes)
        } catch {
            return [System.Text.Encoding]::UTF8.GetString($Bytes)
        }
    }
}

# 跑一个外部程序并抓输出：关闭 stdin（等价 bash 的 </dev/null，防读标准输入挂住），
# 超时则杀进程树。返回合并后的 stdout+stderr（进程级重定向，不受 PowerShell 管道影响）。
function Invoke-External {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 6
    )

    $exe = $FilePath
    $argv = @($Arguments)
    $ext = [System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()
    if ($ext -eq '.cmd' -or $ext -eq '.bat') {
        # .cmd/.bat 要走 cmd.exe /c（CreateProcess 不保证能直接跑批处理）
        $argv = @('/c', $FilePath) + $argv
        $exe = $env:ComSpec
    } elseif ($ext -eq '.ps1') {
        # .ps1 shim 用当前 pwsh 跑
        $pwshPath = (Get-Process -Id $PID -ErrorAction SilentlyContinue).Path
        if (-not $pwshPath) { $pwshPath = Join-Path $PSHOME 'pwsh.exe' }
        $argv = @('-NoLogo', '-NoProfile', '-File', $FilePath) + $argv
        $exe = $pwshPath
    }

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $exe
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    # ArgumentList 是 .NET Core+ 才有的；5.1 退回到手工拼串
    if ($psi.PSObject.Properties.Name -contains 'ArgumentList') {
        foreach ($a in $argv) { [void]$psi.ArgumentList.Add($a) }
    } else {
        $psi.Arguments = (($argv | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
        }) -join ' ')
    }

    $proc = $null
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        $proc.StandardInput.Close()
        # 按字节读、自己解码，避免 .NET 固定单一编码造成的乱码（见 Convert-BytesToText）
        $outMs = [System.IO.MemoryStream]::new()
        $errMs = [System.IO.MemoryStream]::new()
        $outTask = $proc.StandardOutput.BaseStream.CopyToAsync($outMs)
        $errTask = $proc.StandardError.BaseStream.CopyToAsync($errMs)
        if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
            try { $proc.Kill($true) } catch { try { $proc.Kill() } catch { } }
            return ''
        }
        try {
            [void]$outTask.Wait(2000)
            [void]$errTask.Wait(2000)
        } catch { }
        $raw = (Convert-BytesToText $outMs.ToArray()) + "`n" + (Convert-BytesToText $errMs.ToArray())
    } catch {
        return ''
    } finally {
        if ($proc) { $proc.Dispose() }
    }

    $lines = ($raw -replace "`r", '') -split "`n" |
        Where-Object { $_.Trim() -ne '' } |
        Select-Object -First 2
    $text = ($lines -join ' | ').Trim()

    # 有的工具对未知参数只回一句报错（gofmt 甚至会把 "version" 当成文件名去打开），
    # 别把报错当成版本。
    foreach ($pattern in @(
            'flag provided but not defined', 'unknown option', 'unrecognized option',
            'invalid option', 'bad option', 'Unsupported option', 'Usage:', 'usage:',
            "Try '", 'GetFileAttributesEx', 'cannot find the file',
            'No such file or directory',
            'is not recognized as an internal or external command')) {
        if ($text -like "*$pattern*") { return '' }
    }
    if ($text.Length -gt 140) { $text = $text.Substring(0, 140) }
    return $text
}

# 逐个候选参数试着拿版本，第一个有输出的就用（等价 bash 版里 for flag in "$@" 的循环）。
# 不能把候选参数一次性全传进去：git --version -version -V version -v 会被工具当成非法参数。
function Get-ToolVersion {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string]$Name,
        [int]$TimeoutSeconds = 6
    )
    foreach ($flag in (Get-VersionArgs $Name)) {
        $out = Invoke-External -FilePath $FilePath -Arguments @($flag) -TimeoutSeconds $TimeoutSeconds
        if ($out) { return $out }
    }
    return ''
}

# 解析 + 探测一个名字，按调用方的开关决定是否打印。
function Probe-Tool {
    param([Parameter(Mandatory)][string]$Name)

    $c = Get-Command -Name $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $c) {
        if ($Missing) { "{0}`t{1}`t{2}" -f $Name, '-', 'NOT FOUND' }
        return
    }

    if ($c.CommandType -eq 'Alias') {
        $target = $c.Definition
        $tc = Get-Command -Name $target -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandType -eq 'Application' } | Select-Object -First 1
        if ($tc) {
            $out = Get-ToolVersion -FilePath $tc.Source -Name $target -TimeoutSeconds $TimeoutSeconds
            if (-not $out) { $out = '(无版本输出)' }
            "{0}`t{1}`t{2}" -f $Name, "alias -> $target ($($tc.Source))", $out
        } else {
            "{0}`t{1}`t{2}" -f $Name, "alias -> $target", '(别名，目标不在 PATH)'
        }
        return
    }

    if ($c.CommandType -eq 'Function' -or $c.CommandType -eq 'Filter') {
        "{0}`t{1}`t{2}" -f $Name, 'function', '(函数/包装，跳过版本探测)'
        return
    }

    $path = $c.Source
    $out = Get-ToolVersion -FilePath $path -Name $Name -TimeoutSeconds $TimeoutSeconds
    if (-not $out) { $out = '(无版本输出)' }
    # WindowsApps 下的是应用执行别名（Store 存根），可能并没有真装
    if ($path -like '*\Microsoft\WindowsApps\*') { $out = "$out  [Store 别名]" }
    "{0}`t{1}`t{2}" -f $Name, $path, $out
}

# 列出 PATH 每个目录里的条目
function Show-PathDirs {
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($dir in ($Env:PATH -split ';')) {
        if (-not $dir) { continue }
        if (-not $seen.Add($dir.TrimEnd('\'))) { continue }
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        # Windows 系统目录有几百个 exe，对"有哪些开发工具"没有信息量。
        # 注意只跳真正的 C:\Windows（盘根下的那个），别用 "\Windows$" —— 那会把
        # C:\texlive\2024\bin\windows 这种合法工具目录也误杀。
        if ($dir -match '(?i)^[a-z]:\\Windows(\\|$)' -or $dir -match '(?i)\\Microsoft\\WindowsApps(\\|$)') {
            '### {0}  (已跳过：Windows 系统目录)' -f $dir
            ''
            continue
        }
        $items = @(Get-ChildItem -LiteralPath $dir -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty Name)
        if ($items.Count -eq 0) { continue }
        '### {0}  ({1} 个条目)' -f $dir, $items.Count
        ($items -join '  ')
        ''
    }
}

if ($Dirs) {
    Show-PathDirs
    exit 0
}

# 候选清单：按类别排，方便读。缺什么直接用 -Missing 看。
$CANDIDATES = @'
python python3 py pip pip3 uv uvx pipx poetry virtualenv conda mamba
node npm npx pnpm yarn bun deno corepack tsx tsc esbuild vite
go gofmt golangci-lint
cargo rustc rustup rustfmt clippy-driver cargo-clippy cargo-fmt rustdoc rust-analyzer
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
openssl gpg ssh rsync tar zstd xz 7z aria2c yt-dlp ffmpeg pandoc typst tectonic latexmk xelatex pdflatex quarto tlmgr
opencode dsh mise asdf direnv starship zsh bash fish nu pwsh powershell ollama nvidia-smi nvcc nsys ncu htop btop
uvicorn gunicorn pytest ruff black mypy ipython jupyter
cot tdp gpm which winget choco scoop shim
nu oh-my-posh llama-cli llama-server perry oz mpiexec
'@ -split '\s+' | Where-Object { $_ }

# 去重后逐个探测（同名工具在两个类别里都列了也只探一次，Windows 下大小写不敏感）
$seenNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($tool in $CANDIDATES) {
    if ($seenNames.Add($tool)) { Probe-Tool -Name $tool }
}
