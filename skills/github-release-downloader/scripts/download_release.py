#!uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
#     "requests",
# ]
#
# [[tool.uv.index]]
# url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
# default = true
# ///

"""
GitHub Release 下载器（REST + 断点续传 + 并发 + 校验）。

与旧版的区别：
- 下载走 `GET /repos/{owner}/{repo}/releases/assets/{id}`（Accept:
  application/octet-stream），由 api.github.com 302 到 release-assets；
  **不再**用 browser_download_url 直连 github.com —— 后者在国内经常连接超时，
  且私有仓库的请求不带 token 会 404。
- 每个文件写 `<name>.part`，断线后用 `Range` 续传；完成后校验大小与
  sha256（GitHub API 的 `digest` 字段），通过才改名。
- 连接/读取有指数退避重试；多文件并发（默认 3）。
- 默认下载全部资产，用 `--include/--exclude`（glob）或 `--asset-name`（子串）
  过滤；不再有扩展名白名单（旧版会漏掉 deb/rpm/AppImage/msi）。
"""

import fnmatch
import hashlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.github.com"
CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class Asset:
    id: int
    name: str
    size: int
    digest: Optional[str]  # sha256 hex，旧版 API 可能没有


class Downloader:
    def __init__(self, token: Optional[str], retries: int, jobs: int):
        self.token = token
        self.retries = retries
        # 单文件时用 \r 原地刷新；并发时改成整行输出，避免互相覆盖。
        self.interactive = jobs == 1
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "qcode-release-downloader/2"
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=max(jobs, 2) * 2,
            pool_maxsize=max(jobs, 2) * 2,
        )
        self.session.mount("https://", adapter)

    # ---------- metadata ----------

    def release(self, owner: str, repo: str, tag: Optional[str]) -> dict:
        if tag:
            url = f"{API}/repos/{owner}/{repo}/releases/tags/{tag}"
        else:
            url = f"{API}/repos/{owner}/{repo}/releases/latest"
        resp = self.session.get(url, timeout=(10, 30))
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def assets(release: dict) -> list[Asset]:
        out = []
        for item in release.get("assets", []):
            digest = (item.get("digest") or "").removeprefix("sha256:") or None
            out.append(
                Asset(
                    id=item["id"],
                    name=item["name"],
                    size=int(item.get("size") or 0),
                    digest=digest,
                )
            )
        return out

    # ---------- download ----------

    def download_asset(
        self,
        owner: str,
        repo: str,
        asset: Asset,
        save_dir: Path,
        quiet: bool,
    ) -> tuple[bool, str]:
        target = save_dir / asset.name
        part = save_dir / f"{asset.name}.part"

        if target.exists():
            if self._verify(target, asset):
                return True, "已存在且校验通过"
            target.unlink()
        if part.exists() and asset.size and part.stat().st_size == asset.size:
            if self._verify(part, asset):
                part.replace(target)
                return True, "续传文件已完整，校验通过"
            part.unlink()

        last_error = "unknown"
        for attempt in range(1, self.retries + 1):
            try:
                self._stream(owner, repo, asset, part, quiet)
                size = part.stat().st_size
                if asset.size and size > asset.size:
                    part.unlink(missing_ok=True)
                    raise RuntimeError(f"文件偏大（{size} > {asset.size}），已重置")
                if asset.size and size < asset.size:
                    raise RuntimeError(f"文件不完整（{size} < {asset.size}），继续续传")
                if not self._verify(part, asset):
                    part.unlink(missing_ok=True)
                    raise RuntimeError("sha256 校验失败，已删除 .part 重下")
                part.replace(target)
                return True, "ok"
            except Exception as exc:  # noqa: BLE001 - 逐次重试，最后一次报错
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    delay = min(2 ** attempt, 10)
                    if not quiet:
                        click.echo(
                            f"  {asset.name}: 第 {attempt} 次失败（{last_error}），"
                            f"{delay}s 后续传",
                            err=True,
                        )
                    time.sleep(delay)

        return False, last_error

    def _stream(self, owner: str, repo: str, asset: Asset, part: Path, quiet: bool) -> None:
        url = f"{API}/repos/{owner}/{repo}/releases/assets/{asset.id}"
        headers = {"Accept": "application/octet-stream"}
        pos = part.stat().st_size if part.exists() else 0
        if pos:
            headers["Range"] = f"bytes={pos}-"

        with self.session.get(
            url, headers=headers, stream=True, timeout=(10, 60)
        ) as resp:
            if pos and resp.status_code == 200:
                # 服务器忽略了 Range：从头来。
                pos = 0
                part.unlink(missing_ok=True)
            elif pos and resp.status_code == 416:
                part.unlink(missing_ok=True)
                raise RuntimeError("Range 越界（.part 不可用），已重置")
            elif pos and resp.status_code != 206:
                resp.raise_for_status()
            resp.raise_for_status()

            total = asset.size or (pos + int(resp.headers.get("Content-Length") or 0))
            mode = "ab" if pos else "wb"
            interval = 0.5 if self.interactive else 2.0
            last_time = time.monotonic()
            last_pos = pos
            with open(part, mode) as fh:
                for chunk in resp.iter_content(chunk_size=CHUNK):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    pos += len(chunk)
                    if quiet:
                        continue
                    now = time.monotonic()
                    if now - last_time < interval:
                        continue
                    # 区间速度，而不是从 0 开始的平均速度
                    speed = (pos - last_pos) / max(now - last_time, 1e-6) / (1 << 20)
                    last_time, last_pos = now, pos
                    pct = f"{(pos / total * 100):5.1f}%" if total else "  ?  "
                    line = (
                        f"  {asset.name[:40]:<40} {pct} "
                        f"{pos / (1 << 20):7.1f}/{(total / (1 << 20)) if total else 0:7.1f} MiB "
                        f"{speed:6.1f} MiB/s"
                    )
                    sys.stdout.write(("\r" if self.interactive else "\n") + line)
                    sys.stdout.flush()
            if not quiet:
                sys.stdout.write("\n")
                sys.stdout.flush()

    @staticmethod
    def _verify(path: Path, asset: Asset) -> bool:
        if asset.size and path.stat().st_size != asset.size:
            return False
        if not asset.digest:
            return True  # 老 API 没有 digest，只能信大小
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
        return digest.hexdigest() == asset.digest


def select_assets(
    assets: list[Asset],
    asset_name: Optional[str],
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> list[Asset]:
    selected = []
    for asset in assets:
        if asset_name and asset_name not in asset.name:
            continue
        if include and not any(fnmatch.fnmatch(asset.name, pat) for pat in include):
            continue
        if exclude and any(fnmatch.fnmatch(asset.name, pat) for pat in exclude):
            continue
        selected.append(asset)
    return selected


@click.command()
@click.argument("owner", required=True)
@click.argument("repo", required=True)
@click.option("--save-dir", "-s", default=None, help="保存目录（默认：origin/{repo}）")
@click.option("--asset-name", "-a", default=None, help="按名称子串筛选资产")
@click.option("--include", "-i", multiple=True, help="只下载匹配的资产（glob，可多次）")
@click.option("--exclude", "-x", multiple=True, help="排除匹配的资产（glob，可多次）")
@click.option("--tag", "-g", default=None, help="指定 Release 标签（默认最新，如 v1.0.0）")
@click.option("--token", "-t", default=None, help="GitHub token（默认读 GITHUB_TOKEN）")
@click.option("--jobs", "-j", default=3, show_default=True, help="并发下载数")
@click.option("--retries", "-r", default=5, show_default=True, help="每个文件的最大重试次数")
@click.option("--fail-fast", is_flag=True, help="任一文件失败就停止剩余下载")
@click.option("--quiet", "-q", is_flag=True, help="不打印进度条")
def main(
    owner: str,
    repo: str,
    save_dir: Optional[str],
    asset_name: Optional[str],
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    tag: Optional[str],
    token: Optional[str],
    jobs: int,
    retries: int,
    fail_fast: bool,
    quiet: bool,
) -> None:
    """从 GitHub Release 下载资产（续传 / 并发 / 校验）。

    示例：
      download_release.py owner repo
      download_release.py owner repo --tag v1.1.2 -a linux
      download_release.py owner repo -i '*.deb' -i '*.AppImage'
      download_release.py owner repo -x '*.rpm' -j 4
    """
    token = token or os.environ.get("GITHUB_TOKEN")
    save = Path(save_dir or f"./origin/{repo}")
    save.mkdir(parents=True, exist_ok=True)

    downloader = Downloader(token=token, retries=retries, jobs=jobs)
    try:
        release = downloader.release(owner, repo, tag)
    except requests.HTTPError as exc:
        click.echo(f"获取 Release 失败：{exc}", err=True)
        sys.exit(1)

    assets = downloader.assets(release)
    selected = select_assets(assets, asset_name, include, exclude)

    click.echo(f"仓库    : {owner}/{repo}")
    click.echo(f"版本    : {release.get('tag_name')}")
    click.echo(f"保存到  : {save.resolve()}")
    click.echo(f"资产    : {len(selected)}/{len(assets)} 个被选中")
    if not selected:
        click.echo("没有匹配的资产（检查 --asset-name / --include / --exclude）", err=True)
        sys.exit(1)

    started = time.monotonic()
    results: list[tuple[Asset, bool, str]] = []
    failed_fast = False

    with ThreadPoolExecutor(max_workers=max(jobs, 1)) as pool:
        futures = {
            pool.submit(
                downloader.download_asset, owner, repo, asset, save, quiet
            ): asset
            for asset in selected
        }
        try:
            for future in as_completed(futures):
                asset = futures[future]
                ok, detail = future.result()
                results.append((asset, ok, detail))
                status = "OK  " if ok else "FAIL"
                click.echo(f"[{status}] {asset.name}  {detail}")
                if not ok and fail_fast:
                    failed_fast = True
                    for pending in futures:
                        pending.cancel()
                    break
        except KeyboardInterrupt:
            for pending in futures:
                pending.cancel()
            click.echo("\n已中断；已下载部分保留在 .part 文件里，重跑即可续传", err=True)
            sys.exit(130)

    elapsed = time.monotonic() - started
    ok_count = sum(1 for _, ok, _ in results if ok)
    click.echo(
        f"\n完成 {ok_count}/{len(selected)} 个，用时 {elapsed:.1f}s，目录 {save.resolve()}"
    )
    if failed_fast or ok_count != len(selected):
        sys.exit(1)


if __name__ == "__main__":
    main()
