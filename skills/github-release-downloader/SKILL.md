---
name: github-release-downloader
description: Download GitHub Release assets with REST API, resume, retries, parallelism and checksum verification / 从 GitHub 下载 Release 资产：REST API、断点续传、重试、并发与校验
metadata:
  author: 邱张华(qiuzhanghua@msn.com)
license: MIT
---

# GitHub Release Downloader / GitHub Release 下载器

## Overview / 概述

Download any release asset (or all of them) from a GitHub repository. The script talks
to the GitHub **REST API** with `requests` and fetches binaries through the asset
endpoint (`Accept: application/octet-stream`), which redirects to
`release-assets.githubusercontent.com` — **not** through `browser_download_url` on
`github.com`.

从 GitHub 仓库下载单个或全部 Release 资产。脚本用 `requests` 直接调 **REST API**，并通过
资产端点（`Accept: application/octet-stream`）下载——它会 302 到
`release-assets.githubusercontent.com`，**不**走 `github.com` 上的 `browser_download_url`。

Why the difference matters / 为什么这点很关键：

- `browser_download_url` 在部分网络环境下连接会间歇性黑洞（本机实测同一 URL 可能一次
  30 秒无响应、下一次 640 KB/s）；API 端点走 `api.github.com`，可用性明显更好。
- API 端点会带上 `Authorization`，私有仓库也能下载；`browser_download_url` 不带 token。
- API 返回的资产元数据里带 `digest`（sha256），可以校验完整性。

## Features / 功能特点

- Latest release, or any release via `--tag` / 最新版本，或用 `--tag` 指定任意版本
- Lists every asset (no extension whitelist — `.deb`、`.rpm`、`.AppImage`、`.msi` 都在) /
  列出全部资产，不再有扩展名白名单
- Filters: `--asset-name`（子串）、`--include` / `--exclude`（glob，可重复）/
  三种过滤方式
- **Resume**: writes `<name>.part` and continues with `Range` after a disconnect /
  断点续传：先写 `.part`，断线后用 Range 续传
- **Retries**: connection/read retries with exponential backoff, per file /
  每文件独立重试，指数退避
- **Verification**: size + sha256 (`digest`) before renaming `.part` /
  完成前校验大小与 sha256
- **Parallel**: `--jobs` (default 3) downloads several assets at once / 并发下载
- Progress with speed, and a non-zero exit code if anything failed /
  带速度的进度显示；任一失败则退出码非 0

## Usage / 使用方法

```bash
# 最新版本的全部资产 → ./origin/<repo>
uv run scripts/download_release.py owner repo

# 指定版本与目录
uv run scripts/download_release.py owner repo --tag v1.1.2 --save-dir ~/space/dl

# 只下 Linux 安装包（glob 可重复）
uv run scripts/download_release.py owner repo -i '*.deb' -i '*.AppImage'

# 排除 rpm；4 路并发；最多重试 8 次
uv run scripts/download_release.py owner repo -x '*.rpm' -j 4 -r 8

# 名称子串（兼容旧用法）
uv run scripts/download_release.py owner repo --asset-name linux
```

### Parameters / 参数

| 参数 | 说明 |
|---|---|
| `owner` `repo` | 仓库坐标 / repository |
| `-s, --save-dir` | 保存目录，默认 `./origin/{repo}` |
| `-g, --tag` | Release 标签；缺省取 latest |
| `-a, --asset-name` | 按名称子串筛选（兼容旧版） |
| `-i, --include` | glob 白名单，可重复，如 `'*.deb'` |
| `-x, --exclude` | glob 黑名单，可重复 |
| `-j, --jobs` | 并发下载数，默认 3 |
| `-r, --retries` | 每个文件的最大尝试次数，默认 5 |
| `--fail-fast` | 任一失败即取消剩余任务 |
| `-q, --quiet` | 不打印进度条 |
| `-t, --token` | GitHub token；缺省读 `GITHUB_TOKEN` |

## Technical Implementation / 技术实现

1. **Metadata**: `GET /repos/{owner}/{repo}/releases/latest` or
   `/releases/tags/{tag}` — one request, no PyGithub dependency.
   元数据：一次 REST 请求，不再依赖 PyGithub。
2. **Download**: `GET /repos/{owner}/{repo}/releases/assets/{id}` with
   `Accept: application/octet-stream`; the 302 target is a signed CDN URL.
   下载：资产端点 + `application/octet-stream`，302 到签名 CDN 地址。
3. **Resume**: keep bytes in `<name>.part`; next attempt sends
   `Range: bytes=<size>-`. A `200` means the server ignored the range and the file is
   restarted from zero; `416` means the partial file is unusable and gets reset.
   续传：`.part` 记录已下字节，下次带 `Range`；`200` 表示服务端不支持 Range（从头来），
   `416` 表示 `.part` 不可用（自动清掉）。
4. **Retry**: `urllib3.Retry` handles connect/read/5xx/429, plus a per-file attempt
   loop so a mid-stream break resumes instead of restarting.
   重试：底层 `Retry` 处理连接/读取/5xx/429，外层再按文件重试，断流后续传。
5. **Verify**: `size` must match, and `sha256` must match `digest` when the API
   provides one; a digest mismatch deletes the `.part` and starts over.
   校验：大小必须一致；API 提供 `digest` 时再比 sha256，不匹配就删掉重下。
6. **Parallel**: `ThreadPoolExecutor(max_workers=jobs)`; each file has its own
   `.part`, retries and progress line.
   并发：每个文件独立的 `.part`、重试与进度行。

### Dependencies / 依赖

- Python 3.12+
- `requests`, `click`（uv 会在首次运行时自动安装 / uv installs them on first run）

## Notes / 注意事项

1. **Rate limits**: unauthenticated API calls are limited (60/hour); pass `--token` or
   set `GITHUB_TOKEN` for private repos or many runs.
   未认证 API 每小时 60 次；私有仓库或频繁调用请传 token。
2. **Interrupted downloads**: `.part` files are kept on purpose. Re-running resumes
   them; delete the `.part` if you want a clean start.
   中断后 `.part` 会保留，重跑即续传；想从头下就删掉它。
3. **Exit code**: `0` only when every selected asset downloaded and verified; any
   failure exits `1` (`--fail-fast` stops early).
   只有全部成功才返回 0；任一失败返回 1。
4. **Proxy**: `requests` honours `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`
   environment variables.
   代理走 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 环境变量。
5. **Network still matters**: the script retries and resumes, but if `api.github.com`
   itself is unreachable nothing can help — check connectivity first.
   脚本能重试与续传，但 `api.github.com` 完全不可达时无能为力。

## Example Output / 示例输出

```
仓库    : qiuzhanghua/terminal-ts
版本    : v1.1.2
保存到  : /home/q/space/dl
资产    : 7/7 个被选中
  terminal_1.1.2_linux_amd64.AppImage     45.2%   37.5/82.9 MB  1.4 MB/s
[OK  ] terminal_1.1.2_linux_amd64.deb  ok
...
完成 7/7 个，用时 96.3s，目录 /home/q/space/dl
```

## Error Handling / 错误处理

- **Repository not found / no release**: check owner/repo/tag; the API returns 404.
- **403 rate limit**: provide `--token`.
- **Checksum mismatch**: the `.part` is deleted and the file is fetched again; repeated
  failures usually mean a proxy/CDN is corrupting the stream.
- **Connection black holes**: retries with backoff + resume handle most of them; if a
  file keeps failing, try a lower `--jobs` or a proxy.
