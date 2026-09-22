#!uv run
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#     "click",
#     "wxauto4; sys_platform == 'win32'",
#     "pyobjc-framework-Cocoa; sys_platform == 'darwin'",
#     "pyobjc-framework-Quartz; sys_platform == 'darwin'",
#     "pyobjc-framework-ApplicationServices; sys_platform == 'darwin'",
# ]
#
# [[tool.uv.index]]
# url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
# default = true
# ///
"""
微信文件发送器

把本地文件发送到微信「文件传输助手」或指定好友/群聊。两个平台后端：
  - Windows：wxauto4（Windows UI Automation）
  - macOS：Accessibility API（驱动微信 Mac 4.x 界面，见 wechat_mac.py）

两者都只操作用户本人已登录的客户端界面，不做协议破解、不注入、不绕过限制。

用法示例：
  uv run scripts/send_to_filehelper.py 报告.pdf
  uv run scripts/send_to_filehelper.py ./dist/*.zip --to 文件传输助手
  uv run scripts/send_to_filehelper.py ./out --recursive --message "构建产物"
  uv run scripts/send_to_filehelper.py --check
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import click

DEFAULT_TARGET = "文件传输助手"
DEFAULT_MAX_SIZE_MB = 100
WILDCARD_CHARS = ("*", "?", "[")
PLATFORM_ESCAPE_ENV = "SEND_TO_FILEHELPER_SKIP_PLATFORM_CHECK"
# 仅用于测试/模拟：强制使用某个后端（windows / macos）
FORCE_BACKEND_ENV = "SEND_TO_FILEHELPER_BACKEND"


@dataclass
class Report:
    """一次发送尝试的结果，供 main() 统一汇总/退出。"""

    target: str = ""
    submitted: List[Path] = field(default_factory=list)
    confirmed: List[str] = field(default_factory=list)
    unconfirmed: List[str] = field(default_factory=list)
    verify_note: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    abort: Optional[str] = None
    candidates: List[str] = field(default_factory=list)


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
# 平台后端：Windows（wxauto4）
# --------------------------------------------------------------------------- #
def run_windows_backend(
    files: Sequence[Path],
    target: str,
    exact: bool,
    message: Optional[str],
    one_by_one: bool,
    delay: float,
    retries: int,
    no_verify: bool,
) -> Report:
    report = Report()
    try:
        wx = open_wechat()
        chat_info = switch_to(wx, target, exact)
    except RuntimeError as exc:
        report.abort = str(exc)
        return report

    chat_name = str(chat_info.get("chat_name") or "")
    if not chat_matches(chat_name, target, exact):
        report.abort = f"当前会话是「{chat_name or '未知'}」，与目标「{target}」不一致，已取消发送。"
        report.candidates = suggest_sessions(wx, target)
        return report

    report.target = chat_name
    ok(f"已切换到会话: {chat_name}")

    if message:
        try:
            msg_result = wx.SendMsg(message)
        except Exception as exc:
            report.abort = f"发送文本消息失败: {exc}"
            return report
        msg_ok, msg_detail = describe_result(msg_result)
        if msg_ok is False:
            report.abort = f"发送文本消息失败: {msg_detail}"
            return report
        info(f"已发送文本消息: {message}")
        if delay > 0:
            time.sleep(delay)

    sent, errors = send_batches(wx, files, one_by_one, delay, max(retries, 0))
    report.errors = errors
    report.submitted = sent
    if not sent:
        report.abort = "没有任何文件发送成功"
        return report

    if delay > 0:
        time.sleep(delay)

    if not no_verify:
        report.confirmed, report.unconfirmed, report.verify_note = verify_sent(wx, sent)
    return report


# --------------------------------------------------------------------------- #
# 平台后端：macOS（Accessibility API）
# --------------------------------------------------------------------------- #
def _match_by_filename(
    texts: Sequence[str], files: Sequence[Path]
) -> Tuple[List[str], List[str]]:
    confirmed: List[str] = []
    missing: List[str] = []
    for path in files:
        stem = path.stem
        hit = any(path.name in text or (stem and stem in text) for text in texts)
        (confirmed if hit else missing).append(path.name)
    return confirmed, missing


def run_macos_backend(
    files: Sequence[Path],
    target: str,
    exact: bool,
    message: Optional[str],
    one_by_one: bool,
    delay: float,
    retries: int,
    no_verify: bool,
) -> Report:
    from wechat_mac import MacBackendError, MacWeChat

    report = Report()
    if retries:
        warn("macOS 后端不支持 --retries，已忽略。")

    try:
        wx = MacWeChat()
        wx.activate()
    except MacBackendError as exc:
        report.abort = str(exc)
        return report

    if target != DEFAULT_TARGET:
        warn(
            f"macOS 端无法像 Windows 那样二次确认好友身份；即将发送给「{target}」，"
            "请确认名称准确无误。"
        )

    try:
        opened, current, candidates = wx.open_chat(target, exact=exact)
    except MacBackendError as exc:
        report.abort = str(exc)
        return report
    report.candidates = candidates

    if not opened:
        report.abort = f"未能切换到会话「{target}」（当前会话：「{current or '未知'}」），已取消发送。"
        return report

    report.target = current or target
    ok(f"已切换到会话: {report.target}")

    before: Optional[List[str]] = None
    if not no_verify:
        try:
            before = wx.message_texts()
        except Exception:
            before = None

    try:
        if message:
            wx.send_text(message)
            info(f"已发送文本消息: {message}")
            if delay > 0:
                time.sleep(delay)

        info(f"粘贴文件并发送: {'、'.join(path.name for path in files)}")
        wx.send_files(
            [str(path) for path in files], batch=not one_by_one, interval=delay
        )
    except MacBackendError as exc:
        report.abort = str(exc)
        return report

    report.submitted = list(files)

    if no_verify:
        return report

    try:
        after = wx.message_texts()
    except Exception as exc:
        after = None
        report.verify_note = f"无法读取会话消息: {exc}"

    if after is None:
        report.verify_note = (
            report.verify_note or "无法读取会话消息列表（微信可能未渲染或辅助功能受限）"
        )
        return report

    confirmed, missing = _match_by_filename(after, files)
    if not confirmed and before is not None and len(after) > len(before):
        # 没有匹配到文件名，但确实多出了新消息 —— 大概率已发送成功
        report.confirmed = [path.name for path in files]
        report.verify_note = "检测到新消息，但未在消息文本中匹配到文件名"
        return report

    report.confirmed = confirmed
    report.unconfirmed = missing
    return report


# --------------------------------------------------------------------------- #
# 环境自检
# --------------------------------------------------------------------------- #
def check_environment(debug: bool = False) -> int:
    """检查当前平台的后端是否可用，返回退出码。"""
    platform = sys.platform
    problems: List[str] = []

    if platform == "win32":
        info("平台: Windows（后端: wxauto4 / UI Automation）")
        try:
            wx = open_wechat()
        except RuntimeError as exc:
            fail(str(exc))
            return 1
        try:
            chat_info = wx.ChatInfo()
        except Exception as exc:
            fail(f"读取当前会话信息失败: {exc}")
            return 1
        ok("wxauto4 可用")
        ok(f"当前会话: {chat_info.get('chat_name') or '未知'}")
        try:
            from wxauto4 import __version__ as wxauto_version  # type: ignore

            if wxauto_version:
                info(f"wxauto4 版本: {wxauto_version}")
        except Exception:
            pass
        return 0

    if platform == "darwin":
        import wechat_mac
        from wechat_mac import MacBackendError, MacWeChat

        wechat_mac.set_debug(debug)

        info("平台: macOS（后端: Accessibility API）")
        try:
            running = MacWeChat.is_running()
        except Exception as exc:  # pyobjc 缺失
            fail(str(exc))
            return 1

        if running:
            ok(f"微信 Mac 客户端正在运行（版本 {MacWeChat.version() or '未知'}）")
        else:
            problems.append("未检测到正在运行的微信 Mac 客户端，请先打开并登录。")

        if MacWeChat.permission_granted():
            ok("辅助功能权限: 已授予")
        else:
            problems.append(
                "辅助功能权限: 未授予。请到「系统设置 → 隐私与安全性 → 辅助功能」"
                "勾选运行本命令的宿主 App（Terminal / iTerm / VS Code / dsh 等），"
                "然后完全退出并重开该 App。"
            )

        if not (running and MacWeChat.permission_granted()):
            for item in problems:
                fail(item)
            return 1

        try:
            wx = MacWeChat()
            wx.activate()
        except MacBackendError as exc:
            fail(str(exc))
            return 1

        windows = wx.windows()
        info(f"AX 窗口数: {len(windows)}")
        for summary in wx.window_summaries():
            info(f"  - {summary}")
        if not windows:
            fail(
                "微信主窗口没有打开：AX 树里没有任何窗口。\n"
                "  处理：点一下 Dock 栏里的微信图标（或菜单栏微信 → 打开微信），"
                "让主窗口显示出来后再运行 --check。"
            )
            return 1

        def probe() -> Tuple[Optional[str], int, bool]:
            current = wx.current_chat()
            rows = wx.sidebar_rows()
            try:
                wx.focus_input()
                input_ok = True
            except MacBackendError:
                input_ok = False
            return current, len(rows), input_ok

        current, row_count, input_ok = probe()
        if not current or row_count == 0 or not input_ok:
            info("首次读取不完整，尝试启用微信完整辅助功能树后重试...")
            wx.try_enable_enhanced_ui()
            time.sleep(0.5)
            current, row_count, input_ok = probe()

        if current:
            ok(f"会话标题可读，当前会话: {current}")
        else:
            problems.append(
                "无法读取会话标题（big_title_line_h_view / big_line_h_view）。"
            )
        info(f"会话列表可见行数: {row_count}")
        if row_count:
            info("会话示例: " + "、".join(row.name for row in wx.sidebar_rows()[:5]))
        if input_ok:
            ok("聊天输入框可定位（chat_input_field）")
        else:
            problems.append("未能定位聊天输入框（chat_input_field）。")

        if problems:
            for item in problems:
                fail(item)
            click.echo("")
            info("当前 AX 树片段（角色 / 标识 / 标题），可用于适配你的微信版本：")
            for line in wx.debug_dump(limit=40):
                click.echo("  " + line)
            info(
                "若窗口数为 1 但仍找不到会话/输入框元素，说明该微信版本未暴露这些标识；"
                "请把上面的片段反馈给 skill 维护者（或用 --debug 查看 AX 错误码）。"
            )
            return 1

        ok("macOS 后端自检通过")
        return 0

    fail(f"不支持的平台: {platform}（仅支持 Windows 与 macOS）")
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("files", nargs=-1, required=False, metavar="[FILE...]")
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
    "--retries",
    type=int,
    default=0,
    show_default=True,
    help="发送失败后的重试次数（仅 Windows）。",
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
    help="只打印将要发送的文件，不操作微信（可在任意平台执行）。",
)
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    default=False,
    help="只做环境自检（权限/微信/后端可用性）。",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="把辅助功能（AX）调用诊断输出到 stderr。",
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
    check_only: bool,
    debug: bool,
    as_json: bool,
) -> None:
    """把本地文件发送到微信文件传输助手或指定会话（Windows / macOS）。"""
    enable_utf8_output()

    if check_only:
        sys.exit(check_environment(debug=debug))

    if debug and sys.platform == "darwin":
        import wechat_mac

        wechat_mac.set_debug(True)

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

    platform = sys.platform
    forced = os.environ.get(FORCE_BACKEND_ENV, "").strip().lower()
    if forced == "windows":
        platform = "win32"
    elif forced == "macos":
        platform = "darwin"

    if platform not in ("win32", "darwin") and not os.environ.get(PLATFORM_ESCAPE_ENV):
        fail(
            f"不支持的平台: {platform}。仅支持 Windows（wxauto4）与 macOS（辅助功能）。"
        )
        sys.exit(1)

    if platform == "darwin":
        report = run_macos_backend(
            resolved, target, exact, message, one_by_one, delay, retries, no_verify
        )
    else:
        report = run_windows_backend(
            resolved, target, exact, message, one_by_one, delay, retries, no_verify
        )

    if report.abort:
        fail(report.abort)
        if report.candidates:
            info("候选会话（可用于修正 --to）: " + "、".join(report.candidates[:10]))
        sys.exit(1)

    # ---- 汇总 ----
    click.echo("")
    click.secho("=" * 52)
    info(f"目标会话 : {report.target}")
    info(f"已提交   : {len(report.submitted)}/{len(resolved)} 个文件")
    if not no_verify:
        if report.verify_note:
            warn(f"消息校验：{report.verify_note}")
        if report.unconfirmed:
            warn(f"未在会话中确认到: {'、'.join(report.unconfirmed)}")
        elif not report.verify_note:
            ok(f"已确认全部 {len(report.confirmed)} 个文件出现在会话中")

    result = {
        "platform": "macos" if platform == "darwin" else "windows",
        "target": report.target,
        "requested": [str(path) for path in resolved],
        "submitted": [str(path) for path in report.submitted],
        "confirmed": report.confirmed,
        "unconfirmed": report.unconfirmed,
        "errors": report.errors,
        "verify_note": report.verify_note,
    }
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    if report.errors:
        sys.exit(1)
    # 会话消息里一个都没看到 → 视为失败（除非用户主动关闭校验）
    if not no_verify and not report.verify_note and not report.confirmed:
        fail("发送后未在会话中发现任何文件消息，请检查微信窗口状态。")
        sys.exit(1)

    ok("完成")
    sys.exit(0)


if __name__ == "__main__":
    main()
