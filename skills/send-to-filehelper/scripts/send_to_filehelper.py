#!uv run
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#     "click",
#     "wxauto4; sys_platform == 'win32'",
#     "pillow; sys_platform == 'win32'",
#     "psutil; sys_platform == 'win32'",
#     "pywin32; sys_platform == 'win32'",
#     "winrt-Windows.Media.Ocr; sys_platform == 'win32'",
#     "winrt-Windows.Globalization; sys_platform == 'win32'",
#     "winrt-Windows.Graphics.Imaging; sys_platform == 'win32'",
#     "winrt-Windows.Storage; sys_platform == 'win32'",
#     "winrt-Windows.Storage.Streams; sys_platform == 'win32'",
#     "winrt-Windows.Foundation; sys_platform == 'win32'",
#     "winrt-Windows.Foundation.Collections; sys_platform == 'win32'",
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

import ctypes
import glob
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import click

if sys.platform == "win32":  # ctypes.wintypes 只在 Windows 上存在
    from ctypes import wintypes

DEFAULT_TARGET = "文件传输助手"
DEFAULT_MAX_SIZE_MB = 100
WILDCARD_CHARS = ("*", "?", "[")
PLATFORM_ESCAPE_ENV = "SEND_TO_FILEHELPER_SKIP_PLATFORM_CHECK"
# 仅用于测试/模拟：强制使用某个后端（windows / macos）
FORCE_BACKEND_ENV = "SEND_TO_FILEHELPER_BACKEND"
# 需要看 wxauto4 免费版原样输出（含推广）时设置
SHOW_ADS_ENV = "SEND_TO_FILEHELPER_SHOW_ADS"
# 允许 wxauto4 上报遥测时设置
ALLOW_TELEMETRY_ENV = "SEND_TO_FILEHELPER_ALLOW_TELEMETRY"
# 强制选择免费版 / Plus 版：auto（默认，装了 Plus 就优先用）/ free / plus
WX_BACKEND_ENV = "SEND_TO_FILEHELPER_WX_BACKEND"
# 跳过 Windows 客户端预检（即使版本超出免费版上限也强行尝试）
SKIP_CLIENT_CHECK_ENV = "SEND_TO_FILEHELPER_SKIP_CLIENT_CHECK"

# wxauto4 免费版官方兼容的微信客户端上限（见 docs.wxauto.org 安装文档）。
# 比它更新的客户端不再向 UIA 暴露免费版需要的控件树，WeChat() 会抛
# 「未找到已登录的客户端主窗口」——这不是登录/最小化问题，重试也没用。
WXAUTO4_FREE_MAX_CLIENT = (4, 1, 8, 107)
# 免费版可用客户端的官方版本归档
WXAUTO4_FREE_CLIENT_URL = (
    "https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107"
)
# Plus 版（wxautox4）安装与激活文档
WXAUTO4_PLUS_DOCS_URL = "https://docs.wxauto.org/docs/install.html"

# wxauto4 免费版会打印的推广内容（命中即丢弃）。
# 注意只匹配推广专用的 URL 片段，不要用裸域名 wxauto.org：
# 官方文档链接 docs.wxauto.org 会出现在本脚本自己的提示里。
WXAUTO_AD_MARKERS = (
    "当前为免费版",
    "如需更多功能",
    "wxauto.org/purchase",
    "plus版本",
    "plus版",
    "Plus版",
    "可取消输出该内容",
    "如有打扰请见谅",
    "work.weixin.qq.com/kfid",
)

# Windows 上正在运行的微信客户端进程名（4.x 为 Weixin.exe，3.x 为 WeChat.exe）
WECHAT_CLIENT_EXES = ("weixin.exe", "wechat.exe")
# 微信主窗口的顶层类名（4.x / 3.x）
WECHAT_MAIN_WINDOW_CLASSES = ("Qt51514QWindowIcon", "WeChatMainWndForPC")
# 托盘/登录窗口的类名特征：这些不是主窗口
WECHAT_TRAY_MARKERS = ("WxTrayIcon", "WeChatLoginWnd")

QUIET = False

# 安装推广过滤器之前的原始流：本脚本自己的输出走它们，永不被过滤器吞掉
_REAL_STDOUT = None
_REAL_STDERR = None


def set_quiet(enabled: bool) -> None:
    global QUIET
    QUIET = bool(enabled)


@dataclass
class Report:
    """一次发送尝试的结果，供 main() 统一汇总/退出。"""

    target: str = ""
    submitted: List[Path] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
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


def _own_stream(err: bool = False):
    """本脚本自身输出所用的流（绕开推广过滤器）。"""
    stream = _REAL_STDERR if err else _REAL_STDOUT
    if stream is not None:
        return stream
    return sys.stderr if err else sys.stdout


def info(message: str) -> None:
    if not QUIET:
        click.secho(message, fg="cyan", file=_own_stream())


def ok(message: str) -> None:
    if not QUIET:
        click.secho(message, fg="green", file=_own_stream())


def warn(message: str) -> None:
    click.secho(message, fg="yellow", file=_own_stream(err=True))


def fail(message: str) -> None:
    click.secho(f"错误: {message}", fg="red", file=_own_stream(err=True))


class _AdFilterStream:
    """把 wxauto4 免费版打印的推广内容挡在终端之外。

    只做逐次写入的整段匹配：包含推广标记的片段直接丢弃，其余原样透传，
    因此不会缓冲、不会影响进度输出或其它第三方输出。
    """

    def __init__(self, stream, markers: Sequence[str]) -> None:
        self._stream = stream
        self._markers = markers
        self._swallow_newline = False

    def write(self, text) -> int:
        # click 会先用 bytes 探测流，这里两种类型都要能处理
        if isinstance(text, (bytes, bytearray)):
            raw = bytes(text)
            probe = raw.decode("utf-8", "ignore")
        else:
            raw = None
            probe = text
        if not probe:
            return 0
        if any(marker in probe for marker in self._markers):
            # print() 会先写内容再单独写 "\n"，这里标记一下把随后的换行也吃掉
            self._swallow_newline = True
            return len(probe)
        if self._swallow_newline:
            self._swallow_newline = False
            if not probe.strip():
                return len(probe)
        if raw is not None:
            buffer = getattr(self._stream, "buffer", None)
            if buffer is not None:
                buffer.write(raw)
                return len(raw)
            return self._stream.write(probe)
        return self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, item: str):
        return getattr(self._stream, item)


def silence_wxauto_ads() -> None:
    """在 import wxauto4 之前安装过滤器（推广可能在 import 或实例化时打印）。

    同时记下原始流：本脚本自己的输出（info/ok/warn/fail）不经过过滤器，
    否则提示里的 wxauto 官方链接会被当成推广一起吞掉。
    """
    global _REAL_STDOUT, _REAL_STDERR
    if _REAL_STDOUT is None:
        _REAL_STDOUT = sys.stdout
    if _REAL_STDERR is None:
        _REAL_STDERR = sys.stderr
    if os.environ.get(SHOW_ADS_ENV):
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or isinstance(stream, _AdFilterStream):
            continue
        setattr(sys, name, _AdFilterStream(stream, WXAUTO_AD_MARKERS))


def configure_wxauto_privacy(module_name: str = "wxauto4") -> None:
    """关掉后端自带的远程广告接口与遥测上报（需要时可用环境变量放行）。

    Args:
        module_name: 实际使用的后端包名（``wxauto4`` 或 ``wxautox4``）。
    """
    if os.environ.get(ALLOW_TELEMETRY_ENV):
        return
    try:
        param_module = importlib.import_module(f"{module_name}.param")
        WxParam = getattr(param_module, "WxParam")  # type: ignore import-not-found
    except Exception:
        return
    for attribute, value in (
        ("TELEMETRY_ENABLED", False),
        ("AD_API_URL", ""),
        ("REPORT_API_URL", ""),
    ):
        try:
            setattr(WxParam, attribute, value)
        except Exception:
            pass


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
# Windows 客户端预检（纯 stdlib，不依赖后端，所以后端坏掉时也能给出结论）
# --------------------------------------------------------------------------- #
SW_RESTORE = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 只用到 c_uint32，保证 macOS 上也能定义（wintypes 只在 Windows 存在）
class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


_USER32_TYPED = False


def _user32():
    """取 user32，并把用到的函数签名声明好（64 位下句柄必须是 HWND 而不是 int）。"""
    global _USER32_TYPED
    user32 = ctypes.windll.user32
    if not _USER32_TYPED:
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _USER32_TYPED = True
    return user32


def _process_image_path(pid: int) -> str:
    """返回进程的可执行文件完整路径（拿不到就返回空串）。"""
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


def _window_facts(hwnd) -> Tuple[int, str, str, str, bool, bool]:
    """返回 (pid, 进程路径, 类名, 标题, 是否可见, 是否最小化)。"""
    user32 = _user32()
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    class_buffer = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(hwnd, class_buffer, 512)
    title_buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buffer, 512)

    return (
        int(pid.value),
        _process_image_path(pid.value),
        class_buffer.value,
        title_buffer.value,
        bool(user32.IsWindowVisible(hwnd)),
        bool(user32.IsIconic(hwnd)),
    )


def _enumerate_top_level_windows() -> List[Tuple[int, int, str, str, str, bool, bool]]:
    """枚举所有顶层窗口，返回 (hwnd, pid, 路径, 类名, 标题, 可见, 最小化)。"""
    user32 = _user32()
    results: List[Tuple[int, int, str, str, str, bool, bool]] = []
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        try:
            pid, exe_path, class_name, title, visible, minimized = _window_facts(hwnd)
            results.append(
                (int(hwnd), pid, exe_path, class_name, title, visible, minimized)
            )
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc_type(_callback), 0)
    return results


def file_version_tuple(path: str) -> Optional[Tuple[int, int, int, int]]:
    """读取可执行文件的版本资源，返回 (major, minor, build, revision)。"""
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
        return (
            info.dwFileVersionMS >> 16,
            info.dwFileVersionMS & 0xFFFF,
            info.dwFileVersionLS >> 16,
            info.dwFileVersionLS & 0xFFFF,
        )
    except Exception:
        return None


def format_version(version: Optional[Sequence[int]]) -> str:
    if not version:
        return "未知"
    return ".".join(str(part) for part in version)


@dataclass
class ClientWindow:
    hwnd: int
    class_name: str
    title: str
    visible: bool
    minimized: bool

    @property
    def is_main_candidate(self) -> bool:
        return self.class_name in WECHAT_MAIN_WINDOW_CLASSES and self.visible

    @property
    def is_tray(self) -> bool:
        return any(marker in self.class_name for marker in WECHAT_TRAY_MARKERS)

    def describe(self) -> str:
        state = []
        if self.minimized:
            state.append("最小化")
        elif not self.visible:
            state.append("不可见")
        suffix = f"（{'、'.join(state)}）" if state else ""
        return f"{self.class_name} {self.title!r}{suffix}"


@dataclass
class WindowsClient:
    pid: int
    exe_path: str
    windows: List[ClientWindow] = field(default_factory=list)

    @property
    def exe_name(self) -> str:
        return os.path.basename(self.exe_path) or "?"

    @property
    def version(self) -> Optional[Tuple[int, int, int, int]]:
        return file_version_tuple(self.exe_path)

    @property
    def main_window(self) -> Optional[ClientWindow]:
        for window in self.windows:
            if window.is_main_candidate:
                return window
        return None


def find_windows_clients() -> List[WindowsClient]:
    """找出正在运行的微信客户端进程，以及它们各自的顶层窗口。"""
    clients: Dict[int, WindowsClient] = {}
    for hwnd, pid, exe_path, class_name, title, visible, minimized in (
        _enumerate_top_level_windows()
    ):
        if not exe_path:
            continue
        if os.path.basename(exe_path).lower() not in WECHAT_CLIENT_EXES:
            continue
        client = clients.get(pid)
        if client is None:
            client = WindowsClient(pid=pid, exe_path=exe_path)
            clients[pid] = client
        client.windows.append(
            ClientWindow(
                hwnd=hwnd,
                class_name=class_name,
                title=title,
                visible=visible,
                minimized=minimized,
            )
        )
    return list(clients.values())


def installed_client_paths() -> List[str]:
    """微信没在运行时，尽力找出已安装的位置，让提示更具体。"""
    paths: List[str] = []
    if sys.platform == "win32":
        try:
            import winreg

            locations = (
                (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\Weixin"),
                (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat"),
            )
            for root, sub_key in locations:
                try:
                    with winreg.OpenKey(root, sub_key) as key:
                        install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                except OSError:
                    continue
                for exe_name in ("Weixin.exe", "WeChat.exe"):
                    candidate = os.path.join(str(install_path), exe_name)
                    if os.path.isfile(candidate):
                        paths.append(candidate)
        except Exception:
            pass

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        for relative in (r"Tencent\Weixin\Weixin.exe", r"Tencent\WeChat\WeChat.exe"):
            candidate = os.path.join(base, relative)
            if os.path.isfile(candidate):
                paths.append(candidate)

    unique: List[str] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def restore_window(hwnd: int) -> bool:
    try:
        _user32().ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        return True
    except Exception:
        return False


def free_backend_version_problem(version: Optional[Sequence[int]]) -> Optional[str]:
    """免费版 + 客户端版本超范围时，返回可直接展示的结论；否则返回 None。"""
    if version is None:
        return None
    if tuple(version) <= WXAUTO4_FREE_MAX_CLIENT:
        return None
    supported = format_version(WXAUTO4_FREE_MAX_CLIENT)
    return (
        f"当前微信客户端版本 {format_version(version)} 超出了 wxauto4 免费版的官方兼容范围"
        f"（免费版最高支持 {supported}）。\n"
        "  因此后端找不到「已登录的客户端主窗口」——这不是登录、最小化或权限问题，"
        "重试也不会成功。\n"
        "解决办法（任选其一）：\n"
        f"  1. 换用受支持的客户端 {supported}：\n"
        f"     {WXAUTO4_FREE_CLIENT_URL}\n"
        "  2. 使用官方 Plus 版（付费，跟随新版客户端更新），装好后本命令会自动优先使用它：\n"
        "     uv run --with wxautox4 scripts/send_to_filehelper.py ...\n"
        f"     激活: wxautox4 auth activate <激活码>   文档: {WXAUTO4_PLUS_DOCS_URL}\n"
        f"  若确认要继续尝试（例如已切到 Plus 后端），设 {SKIP_CLIENT_CHECK_ENV}=1 跳过本检查。"
    )


def plus_license_dir_problem() -> Optional[str]:
    """Plus 版授权目录不可写时给出提示（沙箱/受限环境的典型症状）。

    wxautox4 把授权状态放在 ``~/.wxautox``；该目录不可写时它读不到授权，
    只会报「未授权设备」，看起来像没激活过。
    """
    directory = Path.home() / ".wxautox"
    if not directory.is_dir():
        return None
    probe = directory / f".send-to-filehelper-probe-{os.getpid()}"
    try:
        probe.write_text("probe", encoding="utf-8")
    except OSError:
        return (
            f"Plus 版的授权目录不可写: {directory}\n"
            "  当前进程很可能运行在沙箱/受限环境里：wxautox4 读不到授权状态，"
            "会报「未授权设备」（即使已经激活成功过）。\n"
            "  请在普通终端（不要经过沙箱包装）里重新运行本命令。"
        )
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return None


def windows_client_preflight(backend: "WxBackend") -> Tuple[List[str], Optional[str]]:
    """检查 Windows 微信客户端状态。

    Returns:
        (notes, blocked): 需要回显的信息行；blocked 不为 None 时表示应中止发送。
    """
    notes: List[str] = []
    clients = find_windows_clients()

    if not clients:
        installed = installed_client_paths()
        location = f"（检测到安装位置: {installed[0]}）" if installed else ""
        return notes, (
            f"没有检测到正在运行的微信客户端{location}。\n"
            "  请先启动并登录 Windows 版微信，并保持主窗口打开（不要只留在托盘）。"
        )

    # 优先挑真正有主窗口的那个进程
    client = next((item for item in clients if item.main_window), clients[0])
    version = client.version
    normalized = tuple(version) if version else None

    notes.append(
        f"微信客户端: {client.exe_path}"
        f"（版本 {format_version(version)}，进程 {client.exe_name} pid={client.pid}）"
    )

    main_window = client.main_window
    if main_window is None:
        states = "、".join(window.describe() for window in client.windows) or "无"
        return notes, (
            "微信在运行，但没有找到可见的主窗口（可能关进了托盘/系统栏）。\n"
            "  请点开微信主窗口后重试。\n"
            f"  当前进程的顶层窗口: {states}"
        )

    notes.append(f"主窗口: {main_window.describe()}")

    if main_window.minimized:
        if restore_window(main_window.hwnd):
            notes.append("主窗口此前是最小化的，已自动还原。")
        else:
            notes.append("主窗口处于最小化状态（自动还原失败，请手动展开）。")

    if backend.kind == "own":
        # 自研后端不使用客户端的 UIA 控件树，因此不受"免费版客户端版本上限"约束，
        # 也不需要 Plus 授权；上面这些客户端信息只作为诊断输出。
        return notes, None

    if backend.is_plus:
        license_problem = plus_license_dir_problem()
        if license_problem:
            return notes, license_problem
        return notes, None

    problem = free_backend_version_problem(normalized)
    if problem:
        return notes, problem
    return notes, None


# --------------------------------------------------------------------------- #
# 后端选择与微信交互
# --------------------------------------------------------------------------- #
@dataclass
class WxBackend:
    """实际使用的微信自动化后端。"""

    module_name: str
    label: str
    is_plus: bool
    wechat_class: object
    kind: str = "wxauto"     # wxauto（免费/Plus）| own（自研：窗口+键鼠+OCR）


_BACKEND: Optional[WxBackend] = None


def load_wx_backend() -> WxBackend:
    """选择并导入后端：默认优先 Plus 版（wxautox4），没装则回落免费版。

    Plus 版跟随新版微信客户端更新，免费版的客户端兼容上限明显更低，
    所以「装了 Plus 就用 Plus」是最不容易失败的顺序。
    可用 ``SEND_TO_FILEHELPER_WX_BACKEND=free|plus|own`` 强制指定；
    ``own`` 是自研后端（不使用微信的 UIA 控件树，见 wechat_win.py）。
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    # 必须在 import 后端之前装好推广过滤与原始流记录：
    # 免费版在 import / 构造时都可能打印推广。
    silence_wxauto_ads()

    forced = os.environ.get(WX_BACKEND_ENV, "auto").strip().lower()

    if forced in ("own", "self", "builtin"):
        try:
            from wechat_win import WinWeChat  # type: ignore import-not-found
        except ImportError as exc:
            raise RuntimeError(
                f"未能导入自研后端 wechat_win（{exc}）。请在 skill 目录下运行，"
                "并确认 scripts/wechat_win.py 存在。"
            ) from exc
        _BACKEND = WxBackend("wechat_win", "自研（窗口+键鼠+OCR）", False, WinWeChat, "own")
        return _BACKEND

    candidates = [
        ("wxautox4", "wxautox4（Plus 版）", True),
        ("wxauto4", "wxauto4（免费版）", False),
    ]
    if forced in ("free", "wxauto4"):
        candidates = [candidates[1]]
    elif forced in ("plus", "wxautox4"):
        candidates = [candidates[0]]

    problems: List[str] = []
    for module_name, label, is_plus in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # 未安装 / 激活失败等
            problems.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        wechat_class = getattr(module, "WeChat", None)
        if wechat_class is None:
            problems.append(f"{label}: 模块中没有 WeChat")
            continue
        _BACKEND = WxBackend(module_name, label, is_plus, wechat_class)
        return _BACKEND

    detail = "".join(f"  - {item}\n" for item in problems)
    raise RuntimeError(
        "未能导入微信自动化后端。请确认在 Windows 上执行，并已安装依赖：\n"
        "  免费版：uv run scripts/send_to_filehelper.py --help\n"
        "  Plus 版：uv run --with wxautox4 scripts/send_to_filehelper.py ...\n"
        f"尝试过的后端：\n{detail}"
    )


def _construct_client(backend: WxBackend):
    """构造客户端。

    ``ads=False`` 关闭 wxauto4 免费版打印的推广横幅；该横幅由编译后的扩展
    直接写底层 stdout，Python 层的过滤器拦不住，只有这个开关有效。
    旧版本不认识这个参数时退回无参调用。自研后端不需要该参数。
    """
    if backend.kind == "own":
        return backend.wechat_class()
    try:
        return backend.wechat_class(ads=False)
    except TypeError:
        return backend.wechat_class()


def _connect_error_message(exc: object, backend: WxBackend) -> str:
    if isinstance(exc, SystemExit):
        # Plus 版未激活时会直接 sys.exit()，其退出码本身没有信息量
        text = f"后端直接退出（退出码 {exc.code}）"
    else:
        text = str(exc)
    lines = [
        "无法连接微信 PC 客户端。请确认：",
        f"  1. 已安装并登录 Windows 版微信（当前后端: {backend.label}）；",
        "  2. 微信主窗口已打开且未最小化到托盘；",
        "  3. 当前会话已解锁（远程桌面断开会话会导致 UI Automation 取不到控件）。",
    ]

    hints: List[str] = []
    if backend.is_plus:
        hints.append(
            "Plus 版需要先激活: wxautox4 auth activate <激活码>"
            f"（文档: {WXAUTO4_PLUS_DOCS_URL}）；"
            "若在沙箱/受限环境里运行，授权状态读不到，也会表现为未激活"
        )
    if "未找到已登录的客户端主窗口" in text:
        hints.append(
            "该报错表示后端拿不到微信的 UIA 控件树（mmui::*），通常是客户端版本不受支持："
            f"免费版官方上限 {format_version(WXAUTO4_FREE_MAX_CLIENT)}；"
            "实测客户端 4.1.12.55 连 Plus 版也找不到主窗口。\n"
            f"     请换用受支持的客户端: {WXAUTO4_FREE_CLIENT_URL}"
        )
    for index, hint in enumerate(hints, start=4):
        lines.append(f"  {index}. {hint}")

    lines.append(f"原始错误: {text}")
    return "\n".join(lines)


def open_wechat():
    silence_wxauto_ads()
    backend = load_wx_backend()
    if backend.kind != "own":
        configure_wxauto_privacy(backend.module_name)

    if sys.platform == "win32":
        if os.environ.get(SKIP_CLIENT_CHECK_ENV):
            info(f"已跳过客户端预检（{SKIP_CLIENT_CHECK_ENV} 已设置）")
        else:
            # 预检要在构造客户端之前做完：客户端版本不受支持时，
            # WeChat() 会白等约 120 秒再抛一个误导性的错误。
            notes, blocked = windows_client_preflight(backend)
            for line in notes:
                info(line)
            if blocked:
                raise RuntimeError(blocked)

    try:
        return _construct_client(backend)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit) as exc:
        # 未登录 / 未启动 / 版本不兼容都会走到这里；
        # Plus 版未激活时是直接 sys.exit()，所以 SystemExit 也要接住。
        raise RuntimeError(_connect_error_message(exc, backend)) from exc


def switch_to(wx, target: str, exact: bool) -> Dict[str, str]:
    """切换到目标会话并返回 ChatInfo。"""
    try:
        wx.ChatWith(target, exact=exact)
    except TypeError:
        # 兼容早期版本 ChatWith 不支持 exact 关键字的情况
        wx.ChatWith(target)  # type: ignore call-arg
    except Exception as exc:
        raise RuntimeError(f"切换会话失败: {target} ({exc})") from exc

    return read_chat_info(wx)


def read_chat_info(wx) -> Dict[str, str]:
    """读取当前会话信息（不切换）。"""
    try:
        return dict(wx.ChatInfo() or {})
    except Exception as exc:
        raise RuntimeError(f"读取当前会话信息失败: {exc}") from exc


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
    wx, files: Sequence[Path], texts: Sequence[str] = ()
) -> Tuple[List[str], List[str], Optional[str]]:
    """读取当前会话消息，尽力确认文件/文本消息已出现。

    Returns:
        (confirmed, missing, note): 已确认项、未确认项、不可用原因。
        条目既可能是文件名，也可能是 ``文本: 摘要``。
    """
    backend_note = getattr(wx, "verify_note", None)
    try:
        messages = wx.GetAllMessage() or []
    except Exception as exc:
        return [], _expected_labels(files, texts), f"无法读取会话消息: {exc}"

    file_haystack: List[str] = []
    text_haystack: List[str] = []
    for message in messages:
        kind = getattr(message, "type", "")
        content = str(getattr(message, "content", "") or "")
        if kind == "file":
            file_haystack.append(content)
        elif kind == "text":
            text_haystack.append(content)

    confirmed: List[str] = []
    missing: List[str] = []
    for path in files:
        stem = path.stem
        hit = any(
            path.name in text or (stem and stem in text) for text in file_haystack
        )
        (confirmed if hit else missing).append(path.name)
    for text in texts:
        # 用「去掉所有空白」做比对：OCR / 客户端回显都可能在中文与标点之间插空格
        flat_needle = "".join(text.split())
        hit = bool(flat_needle) and any(
            flat_needle in "".join(content.split()) for content in text_haystack
        )
        label = _text_label(text)
        (confirmed if hit else missing).append(label)
    if backend_note and not confirmed:
        # 自研后端（--wx-backend own）的复核依赖 OCR，读不到新消息是常态而不是失败；
        # 返回 note 让上层只提示、不判失败（消息其实已经发出）。
        return [], [], backend_note
    return confirmed, missing, None


def _text_label(text: str, width: int = 20) -> str:
    flat = " ".join(text.split())
    if len(flat) > width:
        flat = flat[:width] + "…"
    return f"文本: {flat}"


def _expected_labels(files: Sequence[Path], texts: Sequence[str]) -> List[str]:
    return [path.name for path in files] + [_text_label(text) for text in texts]


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
    blind: bool = False,
    texts: Sequence[str] = (),
    after_texts: Sequence[str] = (),
    switch: bool = True,
) -> Report:
    report = Report()
    try:
        wx = open_wechat()
        chat_info = switch_to(wx, target, exact) if switch else read_chat_info(wx)
    except RuntimeError as exc:
        report.abort = str(exc)
        return report

    # 自研后端把校验做在 SendMsg 内部，必须穿透进去才省得掉那些等待。
    # 用属性而不是关键字：wxauto4/wxautox4 的 SendMsg 签名不认 verify，传了会 TypeError。
    #   --no-verify -> 只跳过发送后的结果复核
    #   --blind     -> 盲发：连发送前的输入框确认也跳过（并隐含 --no-verify）
    # 会话识别与硬校验（防发错人）两者都不受影响。
    if blind:
        no_verify = True
    if hasattr(wx, "skip_verify"):
        wx.skip_verify = no_verify
    if hasattr(wx, "skip_input_check"):
        wx.skip_input_check = blind

    chat_name = str(chat_info.get("chat_name") or "")
    if not chat_matches(chat_name, target, exact):
        report.abort = f"当前会话是「{chat_name or '未知'}」，与目标「{target}」不一致，已取消发送。"
        report.candidates = suggest_sessions(wx, target)
        return report

    report.target = chat_name
    if switch:
        ok(f"已切换到会话: {chat_name}")
    else:
        ok(f"--no-switch：使用当前会话: {chat_name}")

    def send_one_text(text: str, position: str) -> bool:
        try:
            result = wx.SendMsg(text)
        except Exception as exc:
            report.abort = f"发送{position}失败: {exc}"
            return False
        msg_ok, msg_detail = describe_result(result)
        if msg_ok is False:
            report.abort = f"发送{position}失败: {msg_detail}"
            return False
        report.messages.append(text)
        info(f"已发送{position}: {text}")
        if delay > 0:
            time.sleep(delay)
        return True

    for text in texts:
        if not send_one_text(text, "文本消息"):
            return report

    sent: List[Path] = []
    if files:
        sent, errors = send_batches(wx, files, one_by_one, delay, max(retries, 0))
        report.errors = errors
        report.submitted = sent
        if not sent:
            report.abort = "没有任何文件发送成功"
            return report
        if delay > 0:
            time.sleep(delay)

    for text in after_texts:
        if not send_one_text(text, "文本消息（文件之后）"):
            return report

    if not sent and not report.messages:
        report.abort = "没有任何内容发送成功"
        return report

    if not no_verify:
        report.confirmed, report.unconfirmed, report.verify_note = verify_sent(
            wx, sent, [*texts, *after_texts]
        )
    return report


# --------------------------------------------------------------------------- #
# 平台后端：macOS（Accessibility API）
# --------------------------------------------------------------------------- #
def _match_by_filename(
    texts: Sequence[str], files: Sequence[Path], messages: Sequence[str] = ()
) -> Tuple[List[str], List[str]]:
    confirmed: List[str] = []
    missing: List[str] = []
    for path in files:
        stem = path.stem
        hit = any(path.name in text or (stem and stem in text) for text in texts)
        (confirmed if hit else missing).append(path.name)
    for text in messages:
        needle = text.strip()
        hit = bool(needle) and any(needle in content for content in texts)
        (confirmed if hit else missing).append(_text_label(text))
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
    input_mode: str = "auto",
    texts: Sequence[str] = (),
    after_texts: Sequence[str] = (),
    switch: bool = True,
    search_delay: float = 0.5,
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

    use_keys = input_mode == "keystrokes"
    if input_mode == "auto":
        try:
            usable = wx.ax_content_usable()
        except Exception as exc:
            usable = False
            warn(f"读取辅助功能内容失败: {exc}")
        if not usable:
            use_keys = True
            warn(
                "微信未向辅助功能暴露界面内容，已自动切换到键盘模式"
                "（Cmd+F 搜索 + 剪贴板粘贴）。"
            )

    if use_keys:
        if switch:
            warn(
                f"键盘模式会打开微信搜索面板（微信自身会显示并列的介绍条目），"
                f"且不做会话/消息校验，直接发送给「{target}」——请确认名称正确；"
                "不想看到搜索面板就加 --no-switch。"
            )
        else:
            warn(
                f"--no-switch：不会切换会话，也不触发微信搜索面板，"
                f"内容将直接发到当前已打开的会话，请确认它就是「{target}」。"
            )
        report.target = target
        report.verify_note = "键盘模式（AX 树为空）不做会话/消息校验"
        try:
            if switch:
                wx.open_chat_by_keys(target, search_delay=search_delay)
            for text in texts:
                wx.send_text_by_keys(text)
                report.messages.append(text)
                info(f"已发送文本消息: {text}")
                if delay > 0:
                    time.sleep(delay)
            if files:
                info(f"粘贴文件并发送: {'、'.join(path.name for path in files)}")
                wx.send_files_by_keys(
                    [str(path) for path in files], batch=not one_by_one, interval=delay
                )
                report.submitted = list(files)
            for text in after_texts:
                wx.send_text_by_keys(text)
                report.messages.append(text)
                info(f"已发送文本消息（文件之后）: {text}")
                if delay > 0:
                    time.sleep(delay)
        except MacBackendError as exc:
            report.abort = str(exc)
            return report
        if not report.submitted and not report.messages:
            report.abort = "没有任何内容发送成功"
        return report

    if switch:
        try:
            opened, current, candidates = wx.open_chat(target, exact=exact)
        except MacBackendError as exc:
            report.abort = str(exc)
            return report
        report.candidates = candidates
        if not opened:
            report.abort = f"未能切换到会话「{target}」（当前会话：「{current or '未知'}」），已取消发送。"
            return report
    else:
        # 不切换，但仍然读当前会话标题做校验（AX 模式可读）
        matched, current = wx.matches_target(target, exact=exact)
        if not matched:
            report.abort = (
                f"--no-switch：当前会话是「{current or '未知'}」，与目标「{target}」不一致，"
                "已取消发送。"
            )
            return report

    report.target = current or target
    if switch:
        ok(f"已切换到会话: {report.target}")
    else:
        ok(f"--no-switch：使用当前会话: {report.target}")

    before: Optional[List[str]] = None
    if not no_verify:
        try:
            before = wx.message_texts()
        except Exception:
            before = None

    try:
        for text in texts:
            wx.send_text(text)
            report.messages.append(text)
            info(f"已发送文本消息: {text}")
            if delay > 0:
                time.sleep(delay)

        if files:
            info(f"粘贴文件并发送: {'、'.join(path.name for path in files)}")
            wx.send_files(
                [str(path) for path in files], batch=not one_by_one, interval=delay
            )
            report.submitted = list(files)

        for text in after_texts:
            wx.send_text(text)
            report.messages.append(text)
            info(f"已发送文本消息（文件之后）: {text}")
            if delay > 0:
                time.sleep(delay)
    except MacBackendError as exc:
        report.abort = str(exc)
        return report

    if not report.submitted and not report.messages:
        report.abort = "没有任何内容发送成功"
        return report

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

    confirmed, missing = _match_by_filename(after, files, [*texts, *after_texts])
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
        info("平台: Windows（后端: 微信 UI Automation）")
        try:
            backend = load_wx_backend()
        except RuntimeError as exc:
            fail(str(exc))
            return 1
        info(f"自动化后端: {backend.label}")

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
        ok(f"{backend.label} 可用")
        ok(f"当前会话: {chat_info.get('chat_name') or '未知'}")
        try:
            module = importlib.import_module(backend.module_name)
            version = getattr(module, "__version__", None)

            if version:
                info(f"{backend.module_name} 版本: {version}")
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
        for line in wx.probe():
            info(f"  {line}")
        if not windows:
            fail(
                "AX 树里没有任何窗口。两种可能：\n"
                "  1. 微信主窗口确实没打开 → 点 Dock 栏的微信图标打开主窗口；\n"
                "  2. 当前进程的辅助功能调用被拦截 → 确认授予辅助功能的宿主 App "
                "就是运行本命令的终端（Terminal / iTerm / Ghostty 等）。"
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
        info(f"会话列表可见行数: {row_count}")
        if row_count:
            info("会话示例: " + "、".join(row.name for row in wx.sidebar_rows()[:5]))
        if input_ok:
            ok("聊天输入框可定位（chat_input_field）")

        if current or row_count or input_ok:
            ok("macOS 后端自检通过（辅助功能模式：可在发送前校验会话）")
            return 0

        # 窗口在，但微信完全不暴露聊天界面（微信 4.1.x 实测如此）
        warn(
            "微信窗口存在，但客户端没有通过辅助功能暴露聊天界面：\n"
            "  只暴露了菜单栏（Apple/文件/编辑/…），没有 session_item_*、"
            "chat_input_field 等元素；\n"
            "  AXEnhancedUserInterface / AXManualAccessibility 与命中测试都返回 "
            "notImplemented —— 这是微信客户端自身的行为，不是权限或终端问题。"
        )
        click.echo("")
        info("AX 树片段（角色 / 标识 / 标题）：")
        for line in wx.debug_dump(limit=12):
            click.echo("  " + line)
        click.echo("")
        ok(
            "macOS 后端自检通过（键盘模式）：\n"
            "  发送时会自动改用 Cmd+F 搜索 + 剪贴板粘贴（--input-mode auto 的默认行为）。\n"
            "  代价：无法在发送前校验会话、也无法在发送后校验消息 —— 目标名称必须完全正确\n"
            "  （默认的「文件传输助手」最安全）。"
        )
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
@click.option(
    "-m",
    "--message",
    "messages",
    multiple=True,
    help="要发送的文本消息（可重复，按顺序在文件之前发送）。",
)
@click.option(
    "-A",
    "--after-message",
    "after_messages",
    multiple=True,
    help="文件发送完之后再发的文本消息（可重复）。",
)
@click.option(
    "--no-switch",
    "no_switch",
    is_flag=True,
    default=False,
    help="不切换会话，直接发送到当前已打开的会话（可避免微信搜索面板弹出）。",
)
@click.option(
    "--search-delay",
    type=float,
    default=0.5,
    show_default=True,
    help="macOS 键盘模式：粘贴目标名后等待搜索结果再回车的秒数。",
)
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
    "--blind",
    is_flag=True,
    default=False,
    help="盲发：连发送前的输入框确认也跳过（隐含 --no-verify）。仅自研后端生效。",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="只打印将要发送的文本与文件，不操作微信（可在任意平台执行）。",
)
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    default=False,
    help="只做环境自检（权限/微信/后端可用性）。",
)
@click.option(
    "--input-mode",
    type=click.Choice(["auto", "ax", "keystrokes"]),
    default="auto",
    show_default=True,
    help="macOS 输入方式：auto 自动选择；ax 只用辅助功能；keystrokes 只用键盘（Windows 忽略）。",
)
@click.option(
    "--wx-backend",
    "wx_backend",
    type=click.Choice(["auto", "free", "plus", "own"]),
    default="auto",
    show_default=True,
    help="Windows 后端：auto 自动（优先 Plus）/ free=wxauto4 / plus=wxautox4 / "
         "own=自研（窗口+键鼠+OCR，不依赖微信 UIA 控件树）。",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    default=False,
    help="安静模式：只输出警告/错误和一行结果。",
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
    messages: Tuple[str, ...],
    after_messages: Tuple[str, ...],
    no_switch: bool,
    search_delay: float,
    one_by_one: bool,
    delay: float,
    recursive: bool,
    max_size_mb: float,
    retries: int,
    no_verify: bool,
    blind: bool,
    dry_run: bool,
    check_only: bool,
    input_mode: str,
    wx_backend: str,
    quiet: bool,
    debug: bool,
    as_json: bool,
) -> None:
    """把本地文件 / 文本发送到微信文件传输助手或指定会话（Windows / macOS）。"""
    enable_utf8_output()
    set_quiet(quiet)
    if wx_backend and wx_backend != "auto":
        os.environ[WX_BACKEND_ENV] = wx_backend

    if check_only:
        sys.exit(check_environment(debug=debug))

    if debug and sys.platform == "darwin":
        import wechat_mac

        wechat_mac.set_debug(True)

    texts = [text for text in messages if text]
    after_texts = [text for text in after_messages if text]

    if not files and not texts and not after_texts:
        fail("至少要指定一个文件，或用 -m/--message 指定要发送的文本。")
        sys.exit(1)

    resolved, problems = expand_inputs(files, recursive)
    if problems:
        for item in problems:
            fail(item)
        fail("输入未能全部解析，为避免漏发已中止。")
        sys.exit(1)

    total_bytes = 0
    for path in resolved:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass

    info(f"目标会话: {target}（精确匹配: {'是' if exact else '否'}）")
    parts = []
    if texts:
        parts.append(f"{len(texts)} 条文本（文件之前）")
    if resolved:
        parts.append(f"{len(resolved)} 个文件，共 {human_size(total_bytes)}")
    if after_texts:
        parts.append(f"{len(after_texts)} 条文本（文件之后）")
    info("待发送: " + "、".join(parts) if parts else "待发送: 无")
    if not QUIET:
        for text in texts:
            click.echo(f"  [文本] {text}")
        for path in resolved:
            try:
                size = human_size(path.stat().st_size)
            except OSError:
                size = "?"
            click.echo(f"  [文件] {display_path(path)}  ({size})")
        for text in after_texts:
            click.echo(f"  [文本·后] {text}")

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

    # --blind 只对 Windows 自研后端生效。命令层的"汇总 / 退出码"判断必须自己同步：
    # run_windows_backend 内部对 no_verify 的赋值是那个函数的局部变量、传不出来，
    # 不同步的话"发送成功但未做校验"会被后面的失败判定误判成失败并以 1 退出。
    verify_off = no_verify or (blind and platform == "win32")

    if platform == "darwin":
        report = run_macos_backend(
            resolved,
            target,
            exact,
            None,
            one_by_one,
            delay,
            retries,
            no_verify,
            input_mode=input_mode,
            texts=texts,
            after_texts=after_texts,
            switch=not no_switch,
            search_delay=search_delay,
        )
    else:
        report = run_windows_backend(
            resolved,
            target,
            exact,
            None,
            one_by_one,
            delay,
            retries,
            no_verify,
            blind=blind,
            texts=texts,
            after_texts=after_texts,
            switch=not no_switch,
        )

    if report.abort:
        fail(report.abort)
        if report.candidates:
            info("候选会话（可用于修正 --to）: " + "、".join(report.candidates[:10]))
        sys.exit(1)

    # ---- 汇总 ----
    if not QUIET:
        click.echo("")
        click.secho("=" * 52)
        info(f"目标会话 : {report.target}")
        if resolved:
            info(f"已提交   : {len(report.submitted)}/{len(resolved)} 个文件")
        if texts or after_texts:
            sent_texts = len(report.messages)
            info(f"文本消息 : {sent_texts}/{len(texts) + len(after_texts)} 条")
        if not verify_off:
            if report.verify_note:
                warn(f"消息校验：{report.verify_note}")
            if report.unconfirmed:
                warn(f"未在会话中确认到: {'、'.join(report.unconfirmed)}")
            elif not report.verify_note:
                ok(f"已确认全部 {len(report.confirmed)} 项内容出现在会话中")

    result = {
        "platform": "macos" if platform == "darwin" else "windows",
        "target": report.target,
        "requested": [str(path) for path in resolved],
        "submitted": [str(path) for path in report.submitted],
        "messages": report.messages,
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
    if not verify_off and not report.verify_note and not report.confirmed:
        fail("发送后未在会话中发现任何内容，请检查微信窗口状态。")
        sys.exit(1)

    summary = f"完成: {report.target}"
    if resolved:
        summary += f" · {len(report.submitted)}/{len(resolved)} 个文件"
    if texts or after_texts:
        summary += f" · {len(report.messages)}/{len(texts) + len(after_texts)} 条文本"
    if QUIET:
        click.echo(summary)
    else:
        ok(summary)
    sys.exit(0)


if __name__ == "__main__":
    main()
