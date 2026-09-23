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
"""微信 Windows 客户端 UIA 控件树检查工具

wxauto4 / wxautox4 都靠 UI Automation 驱动微信界面。当微信客户端版本过新时，
它可能**完全不向 UIA 暴露控件**，此时任何实现（包括自研）都无从下手。
本工具把真实控件树打印出来，用来判定：

  - 控件树存在（能看到 mmui::MainWindow / mmui::ChatMasterView 等）→ 可以自研或继续用现成后端；
  - 只有 1~2 个壳窗口、没有任何 mmui::* → 该客户端版本不发布控件，必须换客户端。

用法（在普通终端里跑，不要在沙箱/受限环境里跑）：

  uv run scripts/wechat_tree.py              # 检查并打印控件树摘要
  uv run scripts/wechat_tree.py --full       # 打印完整控件树（最多 --depth 层）
  uv run scripts/wechat_tree.py --maximize   # 先把主窗口最大化再检查
  uv run scripts/wechat_tree.py --json       # 机器可读输出

退出码：0=控件树可用；1=环境/客户端有问题。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import click

if sys.platform == "win32":
    from ctypes import wintypes

WECHAT_CLIENT_EXES = ("weixin.exe", "wechat.exe")
MAIN_WINDOW_CLASSES = ("Qt51514QWindowIcon", "WeChatMainWndForPC")
# 真正的控件标识带双冒号；"MMUIRenderSubWindowHW" 这类外壳窗口不含它，
# 不能用裸 "mmui" 子串判断，否则会把空壳误判成可用控件树。
MMUI_MARKER = "mmui::"
SW_RESTORE = 9
SW_MAXIMIZE = 3
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_USER32_TYPED = False


def enable_utf8_output() -> None:
    """让中文在 Windows 控制台/重定向中正常输出（--help 也需要）。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


enable_utf8_output()


def user32():
    global _USER32_TYPED
    lib = ctypes.windll.user32
    if not _USER32_TYPED:
        lib.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        lib.GetClassNameW.restype = ctypes.c_int
        lib.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        lib.GetWindowTextW.restype = ctypes.c_int
        lib.IsWindowVisible.argtypes = [wintypes.HWND]
        lib.IsWindowVisible.restype = wintypes.BOOL
        lib.IsIconic.argtypes = [wintypes.HWND]
        lib.IsIconic.restype = wintypes.BOOL
        lib.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        lib.ShowWindow.restype = wintypes.BOOL
        lib.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        lib.GetWindowThreadProcessId.restype = wintypes.DWORD
        _USER32_TYPED = True
    return lib


def process_image_path(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
        "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
        "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS",
    )]


def file_version(path: str) -> Optional[str]:
    if sys.platform != "win32" or not path:
        return None
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(
            buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
        ):
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        return ".".join(str(part) for part in (
            info.dwFileVersionMS >> 16,
            info.dwFileVersionMS & 0xFFFF,
            info.dwFileVersionLS >> 16,
            info.dwFileVersionLS & 0xFFFF,
        ))
    except Exception:
        return None


@dataclass
class ClientWindow:
    hwnd: int
    pid: int
    exe_path: str
    class_name: str
    title: str
    visible: bool
    minimized: bool

    @property
    def exe_name(self) -> str:
        return os.path.basename(self.exe_path) or "?"

    @property
    def is_main_candidate(self) -> bool:
        return self.class_name in MAIN_WINDOW_CLASSES and self.visible


def enumerate_windows(include_children: bool = True) -> List[ClientWindow]:
    lib = user32()
    rows: List[ClientWindow] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    seen = set()

    def add(hwnd):
        if int(hwnd) in seen:
            return
        seen.add(int(hwnd))
        pid = wintypes.DWORD(0)
        lib.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe_path = process_image_path(pid.value)
        if os.path.basename(exe_path).lower() not in WECHAT_CLIENT_EXES:
            return
        class_buffer = ctypes.create_unicode_buffer(512)
        lib.GetClassNameW(hwnd, class_buffer, 512)
        title_buffer = ctypes.create_unicode_buffer(512)
        lib.GetWindowTextW(hwnd, title_buffer, 512)
        rows.append(ClientWindow(
            hwnd=int(hwnd),
            pid=int(pid.value),
            exe_path=exe_path,
            class_name=class_buffer.value,
            title=title_buffer.value,
            visible=bool(lib.IsWindowVisible(hwnd)),
            minimized=bool(lib.IsIconic(hwnd)),
        ))

    def top_cb(hwnd, _):
        add(hwnd)
        return True

    lib.EnumWindows(enum_proc(top_cb), 0)

    if include_children:
        parents = [row.hwnd for row in rows]
        for parent in parents:
            def child_cb(hwnd, _):
                add(hwnd)
                return True
            try:
                lib.EnumChildWindows(wintypes.HWND(parent), enum_proc(child_cb), 0)
            except Exception:
                pass
    return rows


@dataclass
class TreeReport:
    window: ClientWindow
    nodes: int = 0
    truncated: bool = False
    mmui: List[str] = field(default_factory=list)
    error: Optional[str] = None
    lines: List[str] = field(default_factory=list)


def walk_tree(control, max_depth: int, max_nodes: int, lines: List[str],
              mmui: List[str]) -> Tuple[int, bool]:
    count = 0
    truncated = False

    def visit(node, depth):
        nonlocal count, truncated
        if depth > max_depth or count >= max_nodes:
            truncated = truncated or count >= max_nodes
            return
        for child in node.GetChildren():
            count += 1
            try:
                class_name = child.ClassName
                automation_id = child.AutomationId
                name = child.Name
                control_type = child.ControlTypeName
            except Exception:
                continue
            lines.append(
                f"{'  ' * depth}{control_type} ClassName={class_name!r} "
                f"AutomationId={automation_id!r} Name={name[:60]!r}"
            )
            blob = f"{class_name} {automation_id} {name}"
            if MMUI_MARKER in blob.lower():
                mmui.append(f"{class_name} | {automation_id} | {name[:50]}")
            visit(child, depth + 1)

    visit(control, 0)
    return count, truncated


def inspect(window: ClientWindow, max_depth: int, max_nodes: int) -> TreeReport:
    report = TreeReport(window=window)
    try:
        import wxauto4.uia as uia
    except Exception as exc:
        report.error = (
            f"无法导入 UIA 库（{type(exc).__name__}: {exc}）。请在 Windows 上运行，"
            "并确认 uv 能安装依赖。"
        )
        return report
    try:
        root = uia.ControlFromHandle(window.hwnd)
        report.nodes, report.truncated = walk_tree(
            root, max_depth, max_nodes, report.lines, report.mmui
        )
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
    return report


def main_on_windows_only() -> int:
    if sys.platform != "win32":
        click.secho(f"不支持的平台: {sys.platform}（本工具仅用于 Windows）", fg="red", err=True)
        return 1
    return 0


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--full", is_flag=True, default=False,
              help="打印完整控件树（默认只打印摘要与少量样本）。")
@click.option("--depth", type=int, default=6, show_default=True,
              help="遍历的最大层数。")
@click.option("--max-nodes", type=int, default=400, show_default=True,
              help="每个窗口最多输出多少节点（防止超大窗口刷屏）。")
@click.option("--maximize", is_flag=True, default=False,
              help="先把主窗口最大化再检查（微信 4.x 只注册可见区域的控件）。")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="以 JSON 输出结果。")
def main(full: bool, depth: int, max_nodes: int, maximize: bool, as_json: bool) -> None:
    """检查微信 Windows 客户端是否向 UI Automation 暴露控件树。"""
    if main_on_windows_only():
        sys.exit(1)

    windows = enumerate_windows()
    if not windows:
        click.secho(
            "没有检测到正在运行的微信客户端。请先启动并登录微信（主窗口不要只留在托盘）。",
            fg="red", err=True,
        )
        sys.exit(1)

    mains = [w for w in windows if w.is_main_candidate]
    target = mains[0] if mains else windows[0]
    exe_version = file_version(target.exe_path)

    if maximize and not target.minimized:
        try:
            user32().ShowWindow(wintypes.HWND(target.hwnd), SW_MAXIMIZE)
            time.sleep(2.5)
        except Exception:
            pass
    elif target.minimized:
        try:
            user32().ShowWindow(wintypes.HWND(target.hwnd), SW_RESTORE)
            time.sleep(1.5)
        except Exception:
            pass

    if not as_json:
        click.secho("平台: Windows（UI Automation 检查）", fg="cyan")
        click.secho(
            f"微信客户端: {target.exe_path}（版本 {exe_version or '未知'}，"
            f"进程 {target.exe_name} pid={target.pid}）",
            fg="cyan",
        )
        click.secho(
            f"主窗口: {target.class_name} {target.title!r} "
            f"(visible={int(target.visible)} minimized={int(target.minimized)})",
            fg="cyan",
        )
        other = [w for w in windows if w.hwnd != target.hwnd]
        if other:
            click.secho(f"另检测到 {len(other)} 个微信相关窗口/子窗口", fg="cyan")
        click.echo("")

    report = inspect(target, depth, max_nodes)
    if report.error:
        click.secho(f"读取控件树失败: {report.error}", fg="red", err=True)
        sys.exit(1)

    result = {
        "client": target.exe_path,
        "version": exe_version,
        "window": {"hwnd": hex(target.hwnd), "class": target.class_name,
                   "title": target.title, "minimized": target.minimized},
        "nodes": report.nodes,
        "mmui_controls": report.mmui,
        "usable": bool(report.mmui),
    }

    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        click.secho(f"主窗口控件树节点数: {report.nodes}", fg="cyan")
        for line in (report.lines if full else report.lines[:15]):
            click.echo("  " + line)
        if not full and len(report.lines) > 15:
            click.echo(f"  …（共 {len(report.lines)} 行，用 --full 看全部）")
        click.echo("")

    if report.mmui:
        click.secho(
            f"控件树可用：发现 {len(report.mmui)} 个 mmui::* 控件，"
            "可以基于它做自动化（自研或现成后端都行）。",
            fg="green",
        )
        sys.exit(0)

    click.secho(
        "控件树**不可用**：主窗口只暴露了外壳窗口，没有任何 mmui::* 控件。\n"
        "  说明当前微信客户端版本不向 UI Automation 发布界面（常见于过新的客户端）。\n"
        "  这种情况下 wxauto4 / wxautox4 / 自研实现都无法工作——不是代码问题。\n"
        "  处理办法：换用受支持的客户端版本，例如 4.1.8.107\n"
        "  https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107",
        fg="yellow", err=True,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
