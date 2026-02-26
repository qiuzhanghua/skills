#!uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pygithub",
#     "requests",
#     "click",
# ]
#
# [[tool.uv.index]]
# url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
# default = true
# ///


"""
GitHub Release 下载器
用于从GitHub仓库下载最新Release资产的Python脚本
"""

import os
import sys
import click
import requests
from pathlib import Path
from typing import Optional, List, Dict


class GitHubReleaseDownloader:
    """GitHub Release下载器类"""

    def __init__(self, token: Optional[str] = None):
        """
        初始化下载器

        Args:
            token: GitHub个人访问令牌（可选，用于提高API限制和访问私有仓库）
                    如果未指定，将自动从环境变量 GITHUB_TOKEN 读取
        """
        # 如果未传入token，自动从环境变量读取
        if not token:
            token = os.environ.get("GITHUB_TOKEN")

        if token:
            self.github = Github(auth=Auth.Token(token))
        else:
            self.github = Github()

    def get_latest_release(self, owner: str, repo: str) -> Optional[GitRelease]:
        """
        获取仓库的最新Release

        Args:
            owner: 仓库所有者
            repo: 仓库名称

        Returns:
            最新Release对象，如果不存在则返回None
        """
        try:
            repository = self.github.get_repo(f"{owner}/{repo}")
            release = repository.get_latest_release()
            return release
        except Exception as e:
            print(f"获取Release失败: {e}")
            return None

    def get_release_by_tag(
        self, owner: str, repo: str, tag: str
    ) -> Optional[GitRelease]:
        """
        获取仓库的指定Tag对应的Release

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            tag: Release标签（如 v1.0.0）

        Returns:
            指定Tag的Release对象，如果不存在则返回None
        """
        try:
            repository = self.github.get_repo(f"{owner}/{repo}")
            release = repository.get_release(tag)
            return release
        except Exception as e:
            print(f"获取Tag '{tag}' 的Release失败: {e}")
            return None

    # 支持的下载文件扩展名
    SUPPORTED_EXTENSIONS = [
        ".exe",
        ".zip",
        ".tar.gz",
        ".tar.zst",
        ".dmg",
        ".tar.bz2",
        ".tar.xz",
    ]

    def list_assets(self, release: GitRelease) -> List[Dict]:
        """
        列出Release的所有资产（只包含支持的文件类型）

        Args:
            release: Release对象

        Returns:
            资产信息列表（只包含支持下载的文件类型）
        """
        assets_info = []
        for asset in release.get_assets():
            # 检查文件扩展名是否在支持列表中
            if any(
                asset.name.lower().endswith(ext) for ext in self.SUPPORTED_EXTENSIONS
            ):
                assets_info.append(
                    {
                        "name": asset.name,
                        "size": asset.size,
                        "size_mb": round(asset.size / (1024 * 1024), 2),
                        "download_url": asset.browser_download_url,
                        "created_at": asset.created_at,
                        "updated_at": asset.updated_at,
                    }
                )
        return assets_info

    def download_asset(
        self, download_url: str, filename: str, save_dir: str, chunk_size: int = 8192
    ) -> bool:
        """
        下载单个资产文件

        Args:
            download_url: 资产的下载URL
            filename: 保存的文件名
            save_dir: 保存目录
            chunk_size: 下载块大小（字节）

        Returns:
            下载是否成功
        """
        # 创建保存目录
        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)

        file_path = save_path / filename

        try:
            print(f"正在下载: {filename}")

            # 使用requests下载，支持大文件流式下载
            response = requests.get(download_url, stream=True, timeout=300)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # 显示下载进度
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            sys.stdout.write(f"\r  进度: {progress:.1f}%")
                            sys.stdout.flush()

            print(f"\n下载完成: {file_path}")
            return True

        except requests.exceptions.RequestException as e:
            print(f"\n下载失败: {e}")
            # 清理不完整的文件
            if file_path.exists():
                file_path.unlink()
            return False
        except IOError as e:
            print(f"\n文件保存失败: {e}")
            return False

    def download_release(
        self,
        owner: str,
        repo: str,
        save_dir: Optional[str] = None,
        asset_name: Optional[str] = None,
        token: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> bool:
        """
        下载Release资产

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            save_dir: 保存目录（默认：origin/{repo}）
            asset_name: 要下载的资产名称（可选，不指定则下载所有）
            token: GitHub访问令牌
            tag: 指定Release标签（可选，不指定则下载最新Release）

        Returns:
            下载是否成功
        """
        # 如果未指定保存目录，使用默认的 ./origin/{repo}
        if save_dir is None:
            save_dir = f"./origin/{repo}"
        print(f"\n{'=' * 50}")
        print(f"GitHub Release 下载器")
        print(f"{'=' * 50}")
        print(f"仓库: {owner}/{repo}")
        print(f"保存目录: {save_dir}")
        if asset_name:
            print(f"筛选资产: {asset_name}")
        if tag:
            print(f"指定Tag: {tag}")
        print(f"{'=' * 50}\n")

        # 根据是否指定tag来获取对应的Release
        if tag:
            release = self.get_release_by_tag(owner, repo, tag)
        else:
            release = self.get_latest_release(owner, repo)

        if not release:
            print("错误：无法获取Release信息")
            return False

        # 显示Release信息
        print(f"Release版本: {release.tag_name}")
        print(f"发布标题: {release.name}")
        print(f"发布说明: {release.body[:100]}..." if release.body else "")
        print()

        # 获取资产列表
        assets = self.list_assets(release)
        if not assets:
            print("该Release没有可下载的资产")
            return False

        print(f"资产数量: {len(assets)}")
        print(f"\n可用资产：")
        for i, asset in enumerate(assets, 1):
            print(f"  {i}. {asset['name']} (大小: {asset['size_mb']} MB)")
        print()

        # 筛选要下载的资产
        if asset_name:
            assets_to_download = [a for a in assets if asset_name in a["name"]]
            if not assets_to_download:
                print(f"未找到包含 '{asset_name}' 的资产")
                return False
        else:
            assets_to_download = assets

        # 下载选中的资产
        success_count = 0
        for asset in assets_to_download:
            if self.download_asset(asset["download_url"], asset["name"], save_dir):
                success_count += 1
            print()

        # 显示下载结果
        print(f"{'=' * 50}")
        print(f"下载完成: {success_count}/{len(assets_to_download)} 个文件")
        print(f"保存位置: {os.path.abspath(save_dir)}")
        print(f"{'=' * 50}")

        return success_count > 0


@click.command()
@click.argument("owner", required=True)
@click.argument("repo", required=True)
@click.option(
    "--save-dir",
    "-s",
    default=None,
    help="保存目录（默认：origin/{repo}）",
    show_default=True,
)
@click.option(
    "--asset-name",
    "-a",
    default=None,
    help="要下载的资产名称（支持模糊匹配，不指定则下载所有）",
)
@click.option(
    "--token", "-t", default=None, help="GitHub个人访问令牌（可选，用于提高API限制）"
)
@click.option(
    "--tag",
    "-g",
    default=None,
    help="指定Release标签（不指定则下载最新Release，如 v1.0.0）",
)
def main(
    owner: str,
    repo: str,
    save_dir: str,
    asset_name: Optional[str],
    token: Optional[str],
    tag: Optional[str],
):
    """
    GitHub Release下载器 - 从GitHub仓库下载Release资产

    使用示例：
      python download_release.py owner repo
      python download_release.py owner repo --save-dir ./myDownloads
      python download_release.py owner repo --asset-name "linux"
      python download_release.py owner repo --tag v1.0.0
      python download_release.py owner repo --token YOUR_GITHUB_TOKEN
    """
    # 创建下载器并执行下载
    downloader = GitHubReleaseDownloader(token=token)
    success = downloader.download_release(
        owner=owner,
        repo=repo,
        save_dir=save_dir,
        asset_name=asset_name,
        token=token,
        tag=tag,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
