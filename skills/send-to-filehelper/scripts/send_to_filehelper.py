#!uv run
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#     "click",
#     "wxauto4; sys_platform == 'win32'",
# ]
#
# [[tool.uv.index]]
# url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
# default = true
# ///
"""
微信文件发送器（wxauto4）

把本地文件发送到微信「文件传输助手」或指定好友/群聊。
仅支持 Windows + 已登录的微信 PC 客户端 4.x（wxauto4 基于 UI Automation，
不会绕过任何微信限制）。

用法示例：
  uv run scripts/send_to_filehelper.py 报告.pdf
  uv run scripts/send_to_filehelper.py ./dist/*.zip --to 文件传输助手
  uv run scripts/send_to_filehelper.py ./out --recursive --message "构建产物"
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import click

DEFAULT_TARGET = "文件传输助手"
DEFAULT_MAX_SIZE_MB = 100
WILDCARD_CHARS = ("*", "?", "[")
PLATFORM_ESCAPE_ENV = "SEND_TO_FILEHELPER_SKIP_PLATFORM_CHECK"


# --------------------------------------------------------------------------- #
# 输出与格式化
# --------------------------------------------------------------------------- #
def enable_utf8_output() -> None:
    """让中文在 Windows 控制台/重定向中正常输出。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def info(message: str) -> None:
    click.secho(message, fg="cyan")


def ok(message: str) -> None:
    click.secho(message, fg="green")


def warn(message: str) -> None:
    click.secho(message, fg="yellow", err=True)


def fail(message: str) -> None:
    click.secho(f"错误: {message}", fg="red", err=True)


def human_size(num_bytes: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            precision = 0 if unit == "B" else 1
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def display_path(path: Path) -> str:
    return str(path)


# --------------------------------------------------------------------------- #
# 参数展开
# --------------------------------------------------------------------------- #
def _collect_from_dir(directory: Path, recursive: bool) -> List[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(child for child in directory.glob(pattern) if child.is_file())


def _collect(path: Path, recursive: bool) -> Tuple[List[Path], Optional[str]]:
    """返回 (文件列表, 错误原因)。"""
    if path.is_dir():
        found = _collect_from_dir(path, recursive)
        if not found:
            return [], f"目录中没有文件: {path}"
        return found, None
    if path.is_file():
        return [path], None
    return [], f"文件不存在: {path}"


def expand_inputs(
    raw_paths: Sequence[str], recursive: bool
) -> Tuple[List[Path], List[str]]:
    """展开通配符/目录/环境变量，去重并保持输入顺序。

    Returns:
        (files, problems): 待发送文件、无法解析的输入或空目录说明
    """
    files: List[Path] = []
    problems: List[str] = []

    for raw in raw_paths:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if any(char in raw for char in WILDCARD_CHARS):
            matches = sorted(glob.glob(expanded, recursive=recursive))
            if not matches:
                problems.append(f"没有匹配的文件: {raw}")
                continue
            for match in matches:
                found, reason = _collect(Path(match), recursive)
                if reason:
                    problems.append(reason)
                files.extend(found)
            continue

        found, reason = _collect(Path(expanded), recursive)
        if reason:
            problems.append(reason)
        files.extend(found)

    # 去重（保持首次出现的顺序），并转为绝对路径
    unique: List[Path] = []
    seen = set()
    for path in files:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)

    return unique, problems


def check_sizes(files: Sequence[Path], max_size_mb: float) -> List[str]:
    """返回超限提示（微信可能拒收超大文件）。"""
    warnings: List[str] = []
    limit = max_size_mb * 1024 * 1024
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            warnings.append(f"无法读取文件大小: {path} ({exc})")
            continue
        if size == 0:
            warnings.append(f"文件为空: {path}")
        elif size > limit:
            warnings.append(
                f"文件超过 {max_size_mb:g} MB（{human_size(size)}），微信可能拒收: {path}"
            )
    return warnings


# --------------------------------------------------------------------------- #
# wxauto4 结果解析
# --------------------------------------------------------------------------- #
def describe_result(result: object) -> Tuple[Optional[bool], str]:
    """把 wxauto4 的返回值归一化为 (是否显式成功, 说明)。

    wxauto4 各版本 SendFiles 的返回值不完全一致：
    - WxResponse（dict 子类，含 status/message）：可见即按 status 判断；
    - None：接口未返回结果，无法据此判定成败（交给消息校验）。
    """
    if result is None:
        return None, "接口无返回值"

    if isinstance(result, dict):
        status = result.get("status")
        message = str(result.get("message") or result)
        if status is not None:
            return status == "成功", message
        return None, message

    return bool(result), str(result)


def extract_error(result: object) -> str:
    if isinstance(result, dict):
        return str(result.get("message") or result)
    return str(result)


# --------------------------------------------------------------------------- #
# 微信交互
# --------------------------------------------------------------------------- #
def import_wechat_class():
    try:
        from wxauto4 import WeChat  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise RuntimeError(
            "未能导入 wxauto4。请确认在 Windows 上执行，并已用 uv 安装依赖："
            "uv run scripts/send_to_filehelper.py --help"
        ) from exc
    return WeChat


def open_wechat():
    WeChat = import_wechat_class()
    try:
        return WeChat()
    except Exception as exc:  # wxauto4 在未登录/未启动时抛异常
        raise RuntimeError(
            "无法连接微信 PC 客户端。请确认：\n"
            "  1. 已安装并登录 Windows 版微信 4.x（wxauto4 免费版适配 4.1.x）；\n"
            "  2. 微信主窗口已打开且未最小化到托盘；\n"
            "  3. 当前会话已解锁（远程桌面断开会话会导致 UI Automation 取不到控件）。\n"
            f"原始错误: {exc}"
        ) from exc


def switch_to(wx, target: str, exact: bool) -> Dict[str, str]:
    """切换到目标会话并返回 ChatInfo。"""
    try:
        wx.ChatWith(target, exact=exact)
    except TypeError:
        # 兼容早期版本 ChatWith 不支持 exact 关键字的情况
        wx.ChatWith(target)  # type: ignore call-arg
    except Exception as exc:
        raise RuntimeError(f"切换会话失败: {target} ({exc})") from exc

    try:
        chat_info = wx.ChatInfo() or {}
    except Exception as exc:
        raise RuntimeError(f"读取当前会话信息失败: {exc}") from exc
    return dict(chat_info)


def chat_matches(chat_name: str, target: str, exact: bool) -> bool:
    if not chat_name:
        return False
    if exact:
        return chat_name == target
    return target in chat_name


def suggest_sessions(wx, target: str, limit: int = 10) -> List[str]:
    """在切换失败时给出会话列表提示，帮助用户找到正确名称。"""
    try:
        sessions = wx.GetSession() or []
    except Exception:
        return []

    names: List[str] = []
    for session in sessions:
        candidates: List[str] = []
        info_attr = getattr(session, "info", None)
        if isinstance(info_attr, dict):
            candidates.extend(str(value) for value in info_attr.values() if value)
        name_attr = getattr(session, "name", None)
        if isinstance(name_attr, str):
            candidates.append(name_attr)
        for candidate in candidates:
            if candidate and candidate not in names:
                names.append(candidate)
                break

    fuzzy = [name for name in names if target and target in name]
    return (fuzzy or names)[:limit]


def send_batches(
    wx,
    files: Sequence[Path],
    one_by_one: bool,
    delay: float,
    retries: int,
) -> Tuple[List[Path], List[str]]:
    """发送文件，返回 (已调用发送的路径, 错误信息)。"""
    batches: List[Sequence[Path]] = (
        [[path] for path in files] if one_by_one else [list(files)]
    )
    sent: List[Path] = []
    errors: List[str] = []

    for index, batch in enumerate(batches, 1):
        names = "、".join(path.name for path in batch)
        attempt = 0
        while True:
            attempt += 1
            info(f"[{index}/{len(batches)}] 发送 {len(batch)} 个文件: {names}")
            try:
                result = wx.SendFiles([str(path) for path in batch])
            except Exception as exc:
                result = None
                explicit_ok, detail = False, f"{type(exc).__name__}: {exc}"
            else:
                explicit_ok, detail = describe_result(result)

            if explicit_ok is False:
                errors.append(f"发送失败: {names} ({detail})")
                if attempt <= retries:
                    warn(f"第 {attempt} 次发送失败，{delay:g}s 后重试: {detail}")
                    time.sleep(max(delay, 1.0))
                    continue
                break

            sent.extend(batch)
            if explicit_ok is True:
                ok(f"    已提交: {names}")
            else:
                info(f"    已提交（接口未返回结果，稍后校验）: {names}")
            break

        if delay > 0 and index < len(batches):
            time.sleep(delay)

    return sent, errors


def verify_sent(
    wx, files: Sequence[Path]
) -> Tuple[List[str], List[str], Optional[str]]:
    """读取当前会话消息，尽力确认文件消息已出现。

    Returns:
        (confirmed, missing, note): 已确认的文件名、未确认的文件名、不可用原因
    """
    try:
        messages = wx.GetAllMessage() or []
    except Exception as exc:
        return [], [path.name for path in files], f"无法读取会话消息: {exc}"

    haystack: List[str] = []
    for message in messages:
        if getattr(message, "type", "") != "file":
            continue
        haystack.append(str(getattr(message, "content", "") or ""))

    confirmed: List[str] = []
    missing: List[str] = []
    for path in files:
        stem = path.stem
        hit = any(path.name in text or (stem and stem in text) for text in haystack)
        (confirmed if hit else missing).append(path.name)
    return confirmed, missing, None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("files", nargs=-1, required=True, metavar="FILE...")
@click.option(
    "--to",
    "-t",
    "target",
    default=DEFAULT_TARGET,
    show_default=True,
    help="目标会话：文件传输助手 / 好友昵称 / 群聊名称。",
)
@click.option(
    "--exact/--no-exact",
    default=True,
    show_default=True,
    help="是否精确匹配目标名称（默认开启，避免发错人）。",
)
@click.option("-m", "--message", default=None, help="发送文件前先发送一条文本消息。")
@click.option(
    "--one-by-one",
    is_flag=True,
    default=False,
    help="逐个文件发送（默认一次性提交多个文件）。",
)
@click.option(
    "--delay", type=float, default=1.0, show_default=True, help="批次之间的等待秒数。"
)
@click.option(
    "--recursive",
    "-r",
    is_flag=True,
    default=False,
    help="输入为目录时递归收集子目录中的文件。",
)
@click.option(
    "--max-size-mb",
    type=float,
    default=DEFAULT_MAX_SIZE_MB,
    show_default=True,
    help="超过该大小则给出提示（微信客户端可能拒收大文件）。",
)
@click.option(
    "--retries", type=int, default=0, show_default=True, help="发送失败后的重试次数。"
)
@click.option(
    "--no-verify",
    is_flag=True,
    default=False,
    help="跳过发送后的消息校验。",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="只打印将要发送的文件，不操作微信（可在非 Windows 上执行）。",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="以 JSON 输出结果摘要。"
)
def main(
    files: Tuple[str, ...],
    target: str,
    exact: bool,
    message: Optional[str],
    one_by_one: bool,
    delay: float,
    recursive: bool,
    max_size_mb: float,
    retries: int,
    no_verify: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """把本地文件发送到微信文件传输助手或指定会话（Windows + wxauto4）。"""
    enable_utf8_output()

    resolved, problems = expand_inputs(files, recursive)
    if problems:
        for item in problems:
            fail(item)
        fail("输入未能全部解析，为避免漏发已中止。")
        sys.exit(1)

    if not resolved:
        fail("没有可发送的文件")
        sys.exit(1)

    total_bytes = 0
    for path in resolved:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass

    info(f"目标会话: {target}（精确匹配: {'是' if exact else '否'}）")
    info(f"待发送: {len(resolved)} 个文件，共 {human_size(total_bytes)}")
    for path in resolved:
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = "?"
        click.echo(f"  - {display_path(path)}  ({size})")

    for item in check_sizes(resolved, max_size_mb):
        warn(item)

    if dry_run:
        ok("dry-run：未执行任何微信操作")
        sys.exit(0)

    if sys.platform != "win32" and not os.environ.get(PLATFORM_ESCAPE_ENV):
        fail(
            "本 skill 只能在 Windows 上运行：wxauto4 依赖 Windows UI Automation "
            "与已登录的微信 PC 客户端 4.x。"
        )
        sys.exit(1)

    try:
        wx = open_wechat()
        chat_info = switch_to(wx, target, exact)
    except RuntimeError as exc:
        fail(str(exc))
        sys.exit(1)

    chat_name = str(chat_info.get("chat_name") or "")
    if not chat_matches(chat_name, target, exact):
        fail(
            f"当前会话是「{chat_name or '未知'}」，与目标「{target}」不一致，已取消发送。"
        )
        candidates = suggest_sessions(wx, target)
        if candidates:
            info("当前会话列表（可用于修正 --to）: " + "、".join(candidates))
        sys.exit(1)

    ok(f"已切换到会话: {chat_name}")

    if message:
        try:
            msg_result = wx.SendMsg(message)
        except Exception as exc:
            fail(f"发送文本消息失败: {exc}")
            sys.exit(1)
        msg_ok, msg_detail = describe_result(msg_result)
        if msg_ok is False:
            fail(f"发送文本消息失败: {msg_detail}")
            sys.exit(1)
        info(f"已发送文本消息: {message}")
        if delay > 0:
            time.sleep(delay)

    sent, errors = send_batches(wx, resolved, one_by_one, delay, max(retries, 0))
    for item in errors:
        fail(item)

    if not sent:
        fail("没有任何文件发送成功")
        sys.exit(1)

    if delay > 0:
        time.sleep(delay)

    confirmed: List[str] = []
    unconfirmed: List[str] = []
    verify_note: Optional[str] = None
    if not no_verify:
        confirmed, unconfirmed, verify_note = verify_sent(wx, sent)

    # ---- 汇总 ----
    click.echo("")
    click.secho("=" * 52)
    info(f"目标会话 : {chat_name}")
    info(f"已提交   : {len(sent)}/{len(resolved)} 个文件")
    if not no_verify:
        if verify_note:
            warn(f"消息校验不可用：{verify_note}")
        elif unconfirmed:
            warn(f"未在会话中确认到: {'、'.join(unconfirmed)}")
        else:
            ok(f"已确认全部 {len(confirmed)} 个文件出现在会话中")

    result = {
        "target": chat_name,
        "requested": [str(path) for path in resolved],
        "submitted": [str(path) for path in sent],
        "confirmed": confirmed,
        "unconfirmed": unconfirmed,
        "errors": errors,
        "verify_note": verify_note,
    }
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    if errors:
        sys.exit(1)
    # 会话消息里一个都没看到 → 视为失败（除非用户主动关闭校验）
    if not no_verify and not verify_note and not confirmed:
        fail("发送后未在会话中发现任何文件消息，请检查微信窗口状态。")
        sys.exit(1)

    ok("完成")
    sys.exit(0)


if __name__ == "__main__":
    main()
