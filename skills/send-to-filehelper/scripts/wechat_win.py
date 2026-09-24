#!uv run
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#     "pillow",
#     "psutil",
#     "pywin32",
#     "uiautomation",
#     "winrt-Windows.Media.Ocr",
#     "winrt-Windows.Globalization",
#     "winrt-Windows.Graphics.Imaging",
#     "winrt-Windows.Storage",
#     "winrt-Windows.Storage.Streams",
#     "winrt-Windows.Foundation",
#     "winrt-Windows.Foundation.Collections",
# ]
#
# [[tool.uv.index]]
# url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
# default = true
# ///
"""微信 Windows 端自研后端：窗口激活 + 剪贴板 + 合成键鼠 + OCR 地标定位。

不依赖 wxauto4 / wxautox4，**也不依赖微信的 UIA 控件树**——微信 4.1.12.x 根本不向
UI Automation 发布界面控件（实测主窗口只有 2 个外壳节点），所以这里走的是「像人一样
操作界面」的路子：

  1. ``PrintWindow`` 截取微信窗口，用 **Windows OCR** 读出界面文字及其坐标（地标）；
  2. 用 OCR 找到**会话名 / 聊天标题**，作为发送前的硬校验（这是本 skill 的安全底线）；
  3. 用**剪贴板 + 合成 Ctrl+V / Enter** 发送文本；
  4. 用工具栏「发送文件」按钮 + 文件对话框发送文件。

已知限制（实测结论）：

  * **文本发送可用**（端到端验证，消息真实到达）；
  * **文件发送可用**：靠工具栏「发送文件」按钮打开系统文件对话框，写入完整路径并确认，
    微信会把文件挂到聊天输入框；注意**附件挂上后 Enter 无法提交**，必须点界面上的
    「发送」按钮（本模块已这样做）。若文件对话框被遮挡/最小化导致失败，请保持微信窗口
    可见、不要抢占鼠标键盘。
  * OCR 需要 ``winrt`` 系列包（见 SKILL.md 依赖）；缺失时降级为「不做校验」并给出警告。

性能（都是实测标定的，别随手改回去）：

  * ``refresh()`` 只在需要时才真的截图 + OCR：1.2 秒内刚取过、窗口没动、前台状态没变的帧直接
    复用；只需看输入框时用 ``_region_lines()`` 只 OCR 窗口底部那一条（0.3s vs 整窗 0.85s）。
  * 已经在前台就不再抢前台、不再催重绘前的多余等待；固定 sleep 都按"够用就好"标定。
  * 设 ``SEND_TO_FILEHELPER_TIMING=1``（或 CLI ``--timing``）会打印各环节耗时，便于回归验证。
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

WECHAT_CLIENT_EXES = ("weixin.exe", "wechat.exe")
MAIN_WINDOW_CLASS = "Qt51514QWindowIcon"
TRAY_WINDOW_CLASS = "Qt51514WxTrayIconMessageWindowClass"
DIALOG_CLASS = "#32770"

SW_SHOW = 5
SW_RESTORE = 9
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_A = 0x41
VK_DELETE = 0x2E
KEYEVENTF_KEYUP = 0x0002

# 「发送文件」按钮相对「发送」按钮的水平偏移（占窗口宽度比例），实测标定
FILE_BUTTON_OFFSET = -0.192
# 主窗口最小宽度：用于排除微信为文件对话框创建的 131x65 同名属主窗口
MIN_MAIN_WIDTH = 400
# 会话名被截断显示时微信用省略号，但 OCR 对它的识别很不稳定（见过 @ “ "），都算
_TRUNCATION_MARKS = ("…", "...", "・・・", "⋯", "@", "“", "”", "''", '"')
# 地标帧的有效期（秒）：窗口没动、微信又在前台时，1 秒内连读两次没必要重新截图 + OCR。
# 一次 refresh 约 2~3s（截图 0.1s + OCR 0.9s×2 + 置前 0.5s + 催重绘 0.4s），
# 而 OCR 结果在这么短的时间里不会变，缓存能直接砍掉重复读数。
FRAME_TTL = 1.2


class WeChatWindowsError(RuntimeError):
    """自研后端无法继续时的错误（信息面向用户可读）。"""


# --------------------------------------------------------------------------- #
# 计时（排障 / 调优用）
#
# 设 SEND_TO_FILEHELPER_TIMING=1 后，本模块会把每个耗时环节的「调用次数 / 总耗时 /
# 最慢一次」累计起来，由上层在结束时打印。定位"发一条消息为什么要几十秒"就靠它。
# --------------------------------------------------------------------------- #
TIMING_ENV = "SEND_TO_FILEHELPER_TIMING"

_timing_on = bool(os.environ.get(TIMING_ENV))
_timing_stats: Dict[str, List[float]] = {}


def timing_enabled() -> bool:
    return _timing_on


def _record_timing(name: str, seconds: float) -> None:
    entry = _timing_stats.setdefault(name, [0.0, 0.0, 0])   # total, max, calls
    entry[0] += seconds
    entry[1] = max(entry[1], seconds)
    entry[2] += 1


def timed(name: str):
    """给函数/方法加计时（未开启计时时不产生额外开销）。"""
    def decorate(fn):
        def wrapper(*args, **kwargs):
            if not _timing_on:
                return fn(*args, **kwargs)
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                _record_timing(name, time.perf_counter() - start)
        wrapper.__name__ = getattr(fn, "__name__", name)
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorate


def timing_lines() -> List[str]:
    """按总耗时从大到小排好序的计时报告行。"""
    if not _timing_stats:
        return []
    rows = sorted(_timing_stats.items(), key=lambda item: item[1][0], reverse=True)
    lines = ["耗时明细（总数 / 次数 / 单次最慢）:"]
    for name, (total, worst, calls) in rows:
        lines.append(f"  {total:7.2f}s  {calls:3d}×  最慢 {worst:5.2f}s  {name}")
    return lines


def _enable_dpi_awareness() -> None:
    """开启 DPI 感知 —— 必须在任何窗口操作之前完成。

    否则 ``GetWindowRect`` 返回的是被系统虚拟化缩放的坐标，截图区域与 OCR 坐标会
    整体错位：PrintWindow 抓出来的聊天区是空白、也读不到「发送」等控件（实测踩过）。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()


# --------------------------------------------------------------------------- #
# Win32 基础
# --------------------------------------------------------------------------- #
_USER32_TYPED = False


def _user32():
    global _USER32_TYPED
    lib = ctypes.windll.user32
    if not _USER32_TYPED:
        from ctypes import wintypes

        lib.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        lib.GetClassNameW.restype = ctypes.c_int
        lib.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        lib.GetWindowTextW.restype = ctypes.c_int
        lib.IsWindowVisible.argtypes = [wintypes.HWND]
        lib.IsWindowVisible.restype = ctypes.c_int
        lib.IsIconic.argtypes = [wintypes.HWND]
        lib.IsIconic.restype = ctypes.c_int
        lib.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        lib.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        _USER32_TYPED = True
    return lib


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return ""


def _window_pid(hwnd: int) -> int:
    pid = ctypes.c_ulong(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _enum_windows() -> List[int]:
    from ctypes import wintypes

    found: List[int] = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        found.append(int(hwnd))
        return 1

    _user32().EnumWindows(enum_proc(_cb), 0)
    return found


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #
@dataclass
class OcrLine:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def cx(self) -> int:
        return (self.x0 + self.x1) // 2

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    def flat(self) -> str:
        return "".join(self.text.split()).lower()


_OCR_ENGINE = None


def ocr_available() -> bool:
    return _ocr_engine() is not None


def _ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            engine = None
            for tag in ("zh-Hans-CN", "zh-Hans", "zh-CN"):
                try:
                    engine = OcrEngine.try_create_from_language(Language(tag))
                except Exception:
                    engine = None
                if engine is not None:
                    break
            if engine is None:
                engine = OcrEngine.try_create_from_user_profile_languages()
            _OCR_ENGINE = engine or False
        except Exception:
            _OCR_ENGINE = False
    return _OCR_ENGINE or None


@timed("ocr_image（把截图写盘 + Windows OCR）")
def ocr_image(image) -> List[OcrLine]:
    """对 PIL 图像做 OCR，返回带坐标的文本行（坐标为图像坐标）。"""
    engine = _ocr_engine()
    if engine is None:
        raise WeChatWindowsError(
            "未安装 Windows OCR 组件（winrt 系列包），无法做界面文字识别与校验。"
        )

    async def _run(path: str) -> List[OcrLine]:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage import FileAccessMode, StorageFile

        handle = await StorageFile.get_file_from_path_async(path)
        stream = await handle.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        lines: List[OcrLine] = []
        for line in result.lines:
            words = [
                (w.text, int(w.bounding_rect.x), int(w.bounding_rect.y),
                 int(w.bounding_rect.width), int(w.bounding_rect.height))
                for w in line.words
            ]
            if not words:
                continue
            lines.append(OcrLine(
                line.text,
                min(w[1] for w in words), min(w[2] for w in words),
                max(w[1] + w[3] for w in words), max(w[2] + w[4] for w in words),
            ))
        return lines

    fd, path = tempfile.mkstemp(suffix=".png", prefix="wxocr_")
    os.close(fd)
    try:
        image.save(path)
        return asyncio.run(_run(path))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 窗口与输入
# --------------------------------------------------------------------------- #
@dataclass
class ChatMessage:
    """一条聊天消息（本后端只用 type/content）。"""

    type: str
    content: str
    sender: str = ""

    def __str__(self) -> str:
        return self.content


@dataclass
class SessionEntry:
    name: str

    def __str__(self) -> str:
        return self.name


def _normalize_ocr_text(text: str) -> str:
    """修掉 OCR 在汉字/中文标点之间插入的空格。

    Windows OCR 会把「文件传输助手」识别成「文 件 传 输 助 手」、把「自检（可忽略）」
    识别成「自检 （ 可忽略 ）」，直接拿去比对必然不相等。这里只删**中日韩字符与中文
    标点之间**的空格，英文/数字之间的空格保留。
    """
    import re

    cjk = r"\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
    return re.sub(rf"(?<=[{cjk}])\s+(?=[{cjk}])", "", text).strip()


# OCR 会把同一个标点读成不同形态（实测 ``【`` -> ``〖``/``〔``、``】`` -> ``〕``）。
# 全角 ``：`` ``，`` 之类 NFKC 能折成半角，但方块括号不在 NFKC 的兼容映射里，单独补。
_PUNCT_FOLD = str.maketrans(
    {
        "【": "[", "〖": "[", "〔": "[", "〈": "[", "《": "[", "「": "[", "『": "[",
        "】": "]", "〗": "]", "〕": "]", "〉": "]", "》": "]", "」": "]", "』": "]",
        "、": ",", "・": ".", "·": ".", "…": ".", "～": "~", "－": "-", "—": "-",
        "“": '"', "”": '"', "‘": "'", "’": "'",
    }
)


# OCR 常见的"数字 ↔ 字母"混读（实测 ``mark1`` → ``markl``、``0`` → ``O``、``5`` → ``S``）。
# 只在严格比对失败、且待查文字里**本来就含数字**时，才用这张表再比一次。
_DIGIT_LETTER_FOLD = str.maketrans(
    {"l": "1", "i": "1", "o": "0", "s": "5", "z": "2", "b": "6", "g": "9"}
)


def _match_key(text: str) -> str:
    """把文字折叠成用于**比对**的规范形式（只用于比对，不用于展示）。

    OCR 会把同一段文字读成不同形态，直接做子串比对必然漏判——实测把
    ``【自动化测试 16:25:27】`` 读成 ``〖自动化测试 16 ： 25 ： 27 〕``：方块括号变体、
    全角冒号、以及数字与标点之间插入的空格。

    这里统一做三件事：

      1. ``NFKC`` 折叠全角/半角（``：`` -> ``:``、``，`` -> ``,``、``（）`` -> ``()``）；
      2. ``_PUNCT_FOLD`` 归并 NFKC 不管的方块括号等异体；
      3. 去掉**所有**空白、下划线并转小写（OCR 常在任意位置插空格，而文件名里的 ``_``
         实测会被读成空格：``final_timing.txt`` → ``final timing.txt``，不去掉就永远比不中）。

    于是 ``【a 16:25】`` 与 ``〖 a 16 ： 25 〕`` 会折叠成同一个 key。
    """
    import unicodedata

    folded = unicodedata.normalize("NFKC", text).translate(_PUNCT_FOLD)
    return "".join(ch for ch in folded if not ch.isspace() and ch != "_").lower()


def _match_any(lines, key: str) -> bool:
    """单行或"按阅读顺序拼成的全文"任一命中即算命中。

    微信会把一条长消息折成多行，OCR 也逐行返回，只比单行的话长消息永远复核不到。

    先按严格 key 比；不比中且 key 里含数字时，再用"数字↔字母"折叠后的 key 比一次——
    OCR 实测会把 ``mark1`` 读成 ``markl``、``0`` 读成 ``O``，这种只差一个字符的误读
    不该被当成"没发出去"。**只用于内容核对**（输入框确认 / 发送后核对），
    会话标题的硬校验另有一套精确比对，不受这里影响。
    """
    def _hit(lines_to_scan: Sequence[OcrLine], needle: str) -> bool:
        if any(needle in _match_key(line.text) for line in lines_to_scan):
            return True
        joined = "".join(line.text for line in sorted(lines_to_scan, key=lambda ln: (ln.cy, ln.cx)))
        return needle in _match_key(joined)

    if _hit(lines, key):
        return True
    if any(char.isdigit() for char in key):
        return _hit(lines, key.translate(_DIGIT_LETTER_FOLD))
    return False


def _is_timestamp(text: str) -> bool:
    """判断 OCR 出来的一行是不是纯时间戳（如 ``10:38`` / ``昨天 19:52`` 里的时间部分）。"""
    core = "".join(text.split()).replace("：", ":").replace(":", "")
    return bool(core) and core.isdigit()


def _restore_if_minimized(hwnd: int) -> bool:
    """窗口最小化时先还原，返回是否真的还原过。

    ``SetForegroundWindow`` / ``BringWindowToTop`` 都不会把最小化的窗口展开，而最小化
    窗口的 ``GetWindowRect`` 约为 (-32000, -32000)：一旦它被当作截图原点，之后所有点击
    都会算到屏幕之外。所以置前之前必须先还原。
    """
    try:
        if not _user32().IsIconic(hwnd):
            return False
        _user32().ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.6)
        return True
    except Exception:
        return False


@timed("_force_foreground（抢前台）")
def _force_foreground(hwnd: int) -> None:
    """把微信窗口拉到最前（最小化时先还原）。

    微信聊天区是硬件渲染的，窗口被别的窗口盖住时 ``PrintWindow`` 往往抓到空白帧
    （表现为"读不到「发送」按钮"）。先按一下 Alt 解除 SetForegroundWindow 的限制，
    再置前，截图才拿得到真实界面。

    **已经在最前时直接返回**：这一步固定睡 0.5s，而一次发送要叫它十几回；
    用户按说明把微信摆在前面时，这些等待全是白花的时间。
    """
    import win32api
    import win32gui

    _restore_if_minimized(hwnd)
    try:
        if int(ctypes.windll.user32.GetForegroundWindow() or 0) == int(hwnd):
            return
    except Exception:
        pass
    VK_MENU = 0x12
    try:
        win32api.keybd_event(VK_MENU, 0, 0, 0)
        win32gui.SetForegroundWindow(hwnd)
        win32api.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        try:
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
    time.sleep(0.5)


@timed("_nudge_repaint（催重绘）")
def _nudge_repaint(hwnd: int) -> None:
    """强制微信窗口重绘。

    微信 4.x 的界面是 Qt 硬件渲染的，``PrintWindow`` 常常抓到**旧帧**——发送其实
    成功了，截到的却还是"输入框空白"的画面，据此判断会得出完全相反的结论。
    先 RedrawWindow + UpdateWindow 催一次重绘，再截图就可靠得多。
    """
    RDW_INVALIDATE = 0x0001
    RDW_UPDATENOW = 0x0100
    RDW_ALLCHILDREN = 0x0080
    RDW_FRAME = 0x0400
    try:
        ctypes.windll.user32.RedrawWindow(
            hwnd, None, None, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN | RDW_FRAME
        )
        ctypes.windll.user32.UpdateWindow(hwnd)
    except Exception:
        pass
    # RDW_UPDATENOW 是同步的，重绘在调用返回前就已经做完；0.25s 只是留给 Qt 合成
    # 最后一帧的余量。原来给 0.4s，一次发送要催七八回，纯属白等。
    time.sleep(0.25)


@timed("_capture（窗口截图）")
def _capture(hwnd: int, from_screen: bool = False):
    """截取窗口内容。

    ``PrintWindow`` 对 Qt/硬件渲染的窗口偶尔会抓到"聊天区空白"的半成品帧，
    此时 OCR 读不到「发送」按钮与聊天标题；``from_screen=True`` 改成直接抓
    屏幕上该窗口所在区域（要求窗口在最前）。
    """
    from PIL import Image
    import win32gui
    import win32ui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top

    if from_screen:
        # 把区域夹到虚拟屏幕内：窗口比屏幕大时 BitBlt 会直接失败
        virt_x = ctypes.windll.user32.GetSystemMetrics(76)
        virt_y = ctypes.windll.user32.GetSystemMetrics(77)
        virt_w = ctypes.windll.user32.GetSystemMetrics(78)
        virt_h = ctypes.windll.user32.GetSystemMetrics(79)
        grab_x = max(left, virt_x)
        grab_y = max(top, virt_y)
        grab_w = max(1, min(right, virt_x + virt_w) - grab_x)
        grab_h = max(1, min(bottom, virt_y + virt_h) - grab_y)

        ddc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        mem = ddc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(ddc, grab_w, grab_h)
        mem.SelectObject(bmp)
        mem.BitBlt((0, 0), (grab_w, grab_h), ddc, (grab_x, grab_y), 0x00CC0020)  # SRCCOPY
        info = bmp.GetInfo()
        image = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                 bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        ddc.DeleteDC()
        # 截到的坐标系原点变成 grab_x/grab_y
        return image, (grab_x, grab_y, grab_w, grab_h)

    dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, width, height)
    mem.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    image = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                             bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, dc)
    return image, (left, top, width, height)


def _is_foreground(hwnd: int) -> bool:
    """微信窗口（或它的子窗口）当前是不是前台窗口。

    已是前台时就不必再按 Alt + ``SetForegroundWindow`` 抢一次（那一步固定睡 0.5s），
    这在一次发送里要发生好几次。
    """
    try:
        u = ctypes.windll.user32
        foreground = int(u.GetForegroundWindow() or 0)
    except Exception:
        return False
    if not foreground:
        return False
    if foreground == int(hwnd):
        return True
    try:
        return int(u.GetAncestor(foreground, 2)) == int(hwnd)      # GA_ROOT
    except Exception:
        return False


def _desktop_locked() -> bool:
    """探测当前桌面是否不可交互（锁屏 / RDP 断开）。

    做法是"把鼠标移到它现在所在的位置"——不可交互的桌面上 ``SetCursorPos`` 会直接
    返回 0（实测报「拒绝访问」）。这一步只花几微秒，却能让脚本在**任何 OCR 之前**
    就给出结论，而不是折腾二十几秒再失败。
    """
    try:
        u = ctypes.windll.user32
        point = ctypes.wintypes.POINT()
        if not u.GetCursorPos(ctypes.byref(point)):
            return True
        return not u.SetCursorPos(point.x, point.y)
    except Exception:
        return False


def _virtual_screen_rect() -> Tuple[int, int, int, int]:
    """整个虚拟桌面的 (left, top, right, bottom)。

    用虚拟桌面而不是主屏尺寸：多显示器（含主屏左侧的副屏）下负坐标本来就是合法的。
    """
    u = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    left = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
    return (
        left,
        top,
        left + u.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        top + u.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


@timed("_click（合成点击）")
def _click(x: int, y: int) -> None:
    """在屏幕坐标 (x, y) 处左键单击；坐标不在屏幕内就报错，**不再夹回屏幕**。

    以前这里会把越界坐标夹进屏幕再点。窗口最小化时 ``GetWindowRect`` 约为
    (-32000, -32000)，按它换算出的绝对坐标远在屏幕之外，于是被静默夹到屏幕角落点一下，
    脚本还当成点成功了——表现出来就是"点击全都不对"。宁可报错让上层重新取帧，也不盲点。
    """
    xi, yi = int(x), int(y)
    left, top, right, bottom = _virtual_screen_rect()
    if not (left <= xi < right and top <= yi < bottom):
        raise WeChatWindowsError(
            f"点击坐标 ({xi}, {yi}) 不在屏幕范围 ({left}, {top})-({right}, {bottom}) 内；"
            "微信窗口可能已最小化或被移出屏幕，已放弃本次点击以免点错位置。"
        )

    # 边界检查通过后才加载输入库：越界时先报错，不依赖 pywin32 是否可用
    import win32api
    import win32con

    win32api.SetCursorPos((xi, yi))
    time.sleep(0.25)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _key(vk: int, ctrl: bool = False) -> None:
    import win32api

    if ctrl:
        win32api.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.05)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.08)
    win32api.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    if ctrl:
        time.sleep(0.05)
        win32api.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def _set_clipboard_text(text: str) -> None:
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _release_modifiers() -> None:
    import win32api

    for vk in (VK_CONTROL, 0x12, 0x10, 0xA2, 0xA3, 0xA0, 0xA1):
        win32api.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)


# --------------------------------------------------------------------------- #
# 主后端
# --------------------------------------------------------------------------- #
class WinWeChat:
    """自研 Windows RPA 后端（供 skill 的 ``send_to_filehelper.py`` 直接使用）。"""

    def __init__(self, target_hint: str = "") -> None:
        if sys.platform != "win32":
            raise WeChatWindowsError("自研后端只支持 Windows。")
        self.hwnd = self._find_main_window()
        self._lines: List[OcrLine] = []
        self._size: Tuple[int, int] = (0, 0)
        self._origin: Tuple[int, int] = (0, 0)
        # 最近一帧的截图：工具栏图标靠像素分析定位，比猜偏移稳
        self._image = None
        # 帧缓存（见 refresh / FRAME_TTL）
        self._frame_at = 0.0
        self._frame_rect: Optional[Tuple[int, int, int, int]] = None
        self._frame_foreground = False
        # 自研后端的"发送后校验"依赖 OCR，而微信界面重绘是异步的，读不到新消息是常态。
        # 这种情况下**不判失败**（消息其实已经发出），只把这个说明交给上层展示。
        self.verify_note: Optional[str] = None
        # 后端自己在发送时逐条核对的结果（原始内容：文本原文 / 文件名）。
        # 上层据此生成"已确认/未确认"，不必再用整窗 OCR 复核一遍——那既慢又会因为
        # 不认识 OCR 的标点变体而误报"未发现内容"（实测：后端已确认，上层却判失败）。
        self.self_confirmed: List[str] = []
        self.self_missing: List[str] = []
        self.best_effort_verify = True
        # 由 send_to_filehelper.py 设置（客户端认错字时两处校验都永远不可能通过）：
        #   skip_verify      --no-verify：跳过**发送后**的结果复核
        #   skip_input_check --blind     ：连**发送前**的输入框确认也跳过（盲发）
        self.skip_verify = False
        self.skip_input_check = False
        self._check_preconditions()
        self.activate()
        if not ocr_available():
            raise WeChatWindowsError(
                "未安装 Windows OCR 组件，自研后端无法做界面识别与发送前校验。\n"
                "  请用 uv 安装依赖：uv run scripts/send_to_filehelper.py --help\n"
                "  （依赖见脚本头部内联元数据：winrt-Windows.Media.Ocr 等）"
            )

    def _check_preconditions(self) -> None:
        """条件不满足就**立刻**给结论，不要先折腾二十几秒再报错。

        RPA 的前提是"微信主窗口开着、桌面能交互"。这两条不成立时，后面的截图与
        合成键鼠一定失败，而且失败方式很绕（读到空白帧→判定"找不到发送按钮"→重试），
        所以这里先花几微秒探一下，把话说清楚。
        """
        import win32gui

        u = _user32()
        try:
            visible = bool(u.IsWindowVisible(self.hwnd))
            iconic = bool(u.IsIconic(self.hwnd))
        except Exception:
            visible, iconic = True, False

        if not visible or iconic:
            raise WeChatWindowsError(
                "微信主窗口被最小化或隐藏了。\n"
                "  本后端是 RPA，必须有一个真实可见的窗口才能操作：请点开微信主窗口"
                "（不要只留在托盘），然后重试。"
            )

        try:
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        except Exception:
            left = top = right = bottom = 0
        virt = _virtual_screen_rect()
        if right <= left or bottom <= top or right <= virt[0] or bottom <= virt[1] \
                or left >= virt[2] or top >= virt[3]:
            raise WeChatWindowsError(
                f"微信主窗口不在屏幕可见区域内（{left},{top}-{right},{bottom}）。\n"
                "  请把微信窗口拖回屏幕内再重试。"
            )

        if _desktop_locked():
            raise WeChatWindowsError(
                "当前桌面不可交互（锁屏 / 远程桌面会话已断开）。\n"
                "  RPA 需要合成鼠标键盘事件，锁屏时 Windows 会拒绝（SetCursorPos → 拒绝访问）。"
                "请解锁桌面后重试。"
            )

    # ---- 窗口 ----
    @staticmethod
    @timed("_find_main_window（找主窗口）")
    def _find_main_window() -> int:
        """找到已登录的微信主窗口。

        机器上可能同时开着**两个微信**：一个是已登录的主窗口，另一个停在扫码登录页
        （`扫码登录` / `仅传输文件`）。两者类名和标题都完全一样，只能靠界面内容区分——
        候选多于一个时用 OCR 认一下，别靠"谁更大"赌运气。
        """
        import win32gui

        candidates: List[Tuple[int, int]] = []
        too_small: List[str] = []
        for hwnd in _enum_windows():
            try:
                if win32gui.GetClassName(hwnd) != MAIN_WINDOW_CLASS:
                    continue
                name = _process_name(_window_pid(hwnd)).lower()
                # 读不到进程名（缺 psutil 等）时不要武断排除：类名已经足够窄了
                if name and not name.startswith(WECHAT_CLIENT_EXES[:2]):
                    continue
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width, height = right - left, bottom - top
                # 微信为文件对话框创建的同名小属主窗口要排除
                if width < MIN_MAIN_WIDTH:
                    if width > 150:      # 明显是被折叠/缩小的主窗口，值得提示
                        too_small.append(f"{width}x{height}")
                    continue
                area = max(0, width) * max(0, height)
                candidates.append((hwnd, area))
            except Exception:
                continue

        if not candidates:
            if too_small:
                raise WeChatWindowsError(
                    "微信主窗口尺寸异常（" + "、".join(too_small) + "），看起来被折叠或缩得很小。\n"
                    "  请把微信窗口恢复成正常大小（或双击标题栏最大化）后重试。"
                )
            raise WeChatWindowsError(
                "没有找到微信主窗口。请确认微信已启动并登录，且主窗口没有关到托盘。"
            )
        if len(candidates) == 1 or not ocr_available():
            return max(candidates, key=lambda item: item[1])[0]

        best_hwnd, best_score = candidates[0][0], -(10 ** 9)
        for hwnd, area in candidates:
            score = min(area // 100000, 20)      # 面积只作轻微权重
            try:
                image, _ = _capture(hwnd)
                flat = "".join(line.flat() for line in ocr_image(image))
            except Exception:
                flat = ""
            if "搜索" in flat or "发送" in flat:
                score += 100                      # 有聊天界面 = 已登录
            if "扫码登录" in flat or "仅传输文件" in flat or "登录" in flat:
                score -= 100                      # 登录页 = 排除
            if score > best_score:
                best_hwnd, best_score = hwnd, score
        return best_hwnd

    def activate(self) -> None:
        """把微信摆成可操作状态。

        已经是前台、未最小化、几何也正常时**什么都不做**——原来无条件
        ``ShowWindow`` + ``SetForegroundWindow`` 加三个 sleep（合计约 1.4s），
        而用户按使用说明把微信摆在前面时，这些动作纯属浪费。
        """
        import win32gui

        if _is_foreground(self.hwnd) and not _restore_if_minimized(self.hwnd):
            self._ensure_usable_geometry()
            _release_modifiers()
            return

        _user32().ShowWindow(self.hwnd, SW_SHOW)
        time.sleep(0.3)
        _user32().ShowWindow(self.hwnd, SW_RESTORE)
        time.sleep(0.3)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        time.sleep(0.8)
        self._ensure_usable_geometry()
        _release_modifiers()

    def _ensure_usable_geometry(self) -> None:
        """把窗口摆成一个稳定、完整可见的尺寸。

        窗口最大化/贴边/比屏幕还大时，截图会越界、点击坐标也会越界
        （实测：显示器从 3840x2160 变成 1536x864 后整个流程就崩了）。
        这里按**当前屏幕**尺寸收一下，并校验结果确实落在屏幕内。
        """
        import win32gui

        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
            width, height = right - left, bottom - top
            # 目标尺寸必须**完整落在屏幕内**：屏幕分辨率会变（实测从 3840x2160 掉到
            # 1536x864），窗口一旦比屏幕高，工具栏那一行就跑到屏幕外，所有点击都会被
            # `_click` 的越界检查拒绝——表现出来就是"文件一直发不出去"。
            # 同时不能太窄：会话列表是固定宽度，窗口窄了会话名会显示成「文件传...」，
            # 发送前的会话校验就没法做（实测宽度 ≤1200 时连标题都读不出来）。
            target_w = min(1800, max(1350, int(screen_w * 0.9)))
            target_h = min(1000, max(560, int(screen_h * 0.86)))
            target_w = min(target_w, screen_w - 8)
            target_h = min(target_h, screen_h - 8)
            off_screen = (
                right > screen_w or bottom > screen_h or left < 0 or top < 0
            )
            need = (
                off_screen
                or width > screen_w - 20
                or height > screen_h - 20
                or width < 1300
                or height < 560
            )
            if need:
                win32gui.MoveWindow(self.hwnd, 4, 4, target_w, target_h, True)
                time.sleep(1.2)
                _nudge_repaint(self.hwnd)
        except Exception:
            pass

    @timed("refresh（催重绘+截图+OCR 整个循环）")
    def refresh(
        self,
        need: Sequence[str] = ("send", "title"),
        max_rounds: int = 2,
        force: bool = False,
    ) -> None:
        """催重绘 → 截图 → OCR，刷新地标。

        提速的关键都在这里（一次 refresh 原本要 2~11 秒）：

        * **复用刚取过的帧**（``FRAME_TTL``）。窗口没动、微信也在前台时，1 秒内
          连续读数（``ChatWith`` 之后紧跟 ``ChatInfo()`` 这种）直接用缓存，不再截图 OCR。
          需要"一定是这一瞬间的画面"的调用（粘贴之后的输入框确认、发送后的复核）
          传 ``force=True`` 绕开缓存。
        * **已在前台就不再抢前台**：``_force_foreground`` 固定睡 0.5s，一次发送要叫它好几回。
        * **最多 2 轮**（原来是 3 轮）：每轮失败都要重新截图 + OCR + 睡 1s 以上，
          而"地标读不到"通常是窗口状态问题，多熬一轮并不能变好。
        """
        if not force and self._frame_is_fresh(need):
            return

        foreground = _is_foreground(self.hwnd)
        best_lines: Optional[List[OcrLine]] = None
        best_rect: Optional[Tuple[int, int, int, int]] = None
        best_image = None
        best_score = -1
        for attempt in range(max_rounds):
            # 微信窗口不在前台时 PrintWindow 会返回**旧帧**（"刚发出去的消息"永远读不到），
            # 所以只有不在前台才需要抢一次；已经在最前就别浪费那 0.5s。
            if attempt or not foreground:
                _force_foreground(self.hwnd)
            _nudge_repaint(self.hwnd)
            for from_screen in (False, True):
                try:
                    image, rect = _capture(self.hwnd, from_screen=from_screen)
                except Exception:
                    continue      # 这种抓法不可用（例如窗口比屏幕大）就换另一种
                lines = ocr_image(image)
                self._origin = (rect[0], rect[1])
                self._size = (rect[2], rect[3])
                self._lines = lines
                self._image = image
                has_send = self._send_button() is not None
                has_title = self._chat_title() is not None
                # 优先"地标齐全"的帧，其次行数多；避免拿到微信重绘中途的半成品
                score = (200 if has_send else 0) + (100 if has_title else 0) + min(len(lines), 90)
                if score > best_score:
                    best_score, best_lines, best_rect, best_image = score, lines, rect, image
                if all(self._has_landmarks(need, lines, has_send, has_title)):
                    self._stamp_frame(rect)
                    return
            time.sleep(0.6)
        if best_lines is not None and best_rect is not None:
            self._lines = best_lines
            self._origin = (best_rect[0], best_rect[1])
            self._size = (best_rect[2], best_rect[3])
            self._image = best_image
            self._stamp_frame(best_rect)

    # ---- 帧缓存 ----
    @staticmethod
    def _has_landmarks(
        need: Sequence[str], lines: Sequence[OcrLine], has_send: bool, has_title: bool
    ) -> List[bool]:
        flags = []
        for item in need:
            if item == "send":
                flags.append(has_send)
            elif item == "title":
                flags.append(has_title)
            elif item == "lines":
                flags.append(bool(lines))
        return flags

    def _stamp_frame(self, rect: Tuple[int, int, int, int]) -> None:
        self._frame_at = time.monotonic()
        self._frame_rect = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        self._frame_foreground = _is_foreground(self.hwnd)

    def _frame_is_fresh(self, need: Sequence[str] = ()) -> bool:
        """上一帧还能不能用：时间够近、窗口没动、前台状态没变、要的地标都在。"""
        if not self._lines or not self._frame_rect:
            return False
        if time.monotonic() - self._frame_at > FRAME_TTL:
            return False
        if self._frame_rect != (self._origin[0], self._origin[1], self._size[0], self._size[1]):
            return False
        if self._window_origin() != self._origin:
            return False
        # 前台状态变了说明有人（或别的程序）动过窗口，缓存不再可信
        if _is_foreground(self.hwnd) != self._frame_foreground:
            return False
        for item in need:
            if item == "send" and self._send_button() is None:
                return False
            if item == "title" and self._chat_title() is None:
                return False
        return True

    # ---- 地标 ----
    def _abs(self, x: int, y: int) -> Tuple[int, int]:
        return self._origin[0] + x, self._origin[1] + y

    def _toolbar_icons(self, band: int = 24) -> List[int]:
        """用像素分析量出工具栏图标中心的窗口坐标 x（从左到右）。

        以前是"猜偏移"（聊天区左边 +200/+259…），但**图标间距随窗口宽度变化**
        （实测同一台机器上 +201 和 +259 都出现过），于是第一下经常先点到邻居图标——
        用户看到的就是"每次都先去点发送收藏"。这里改成量：工具栏那一行图标是深色
        字形、底色浅，按列的"墨量"切出一个个图标簇即可。

        返回空列表表示这一帧不适合做像素分析，调用方退回偏移试探。
        """
        img = getattr(self, "_image", None)
        if img is None:
            return []
        ty = self._toolbar_y()
        x_start = self._session_left()
        send = self._send_button()
        x_end = (send.x0 - 24) if send is not None else (self._size[0] - 24)
        top = max(0, ty - band)
        bottom = min(self._size[1], ty + band)
        if bottom - top < 8 or x_end - x_start < 60:
            return []
        try:
            gray = img.crop((x_start, top, x_end, bottom)).convert("L")
        except Exception:
            return []
        width, height = gray.size
        pixels = gray.load()
        ink = []
        for x in range(width):
            n = 0
            for y in range(height):
                if pixels[x, y] < 120:
                    n += 1
            ink.append(n)

        clusters: List[Tuple[int, int]] = []
        start = None
        gap = 0
        for x, n in enumerate(ink):
            if n >= 2:
                if start is None:
                    start = x
                gap = 0
            elif start is not None:
                gap += 1
                if gap > 6:                 # 连续空白 = 图标边界
                    clusters.append((start, x - gap))
                    start = None
                    gap = 0
        if start is not None:
            clusters.append((start, width - 1))

        centers = [
            x_start + (a + b) // 2
            for a, b in clusters
            if 8 <= (b - a) <= 70           # 图标宽度合理，排除噪点与细长滑块
        ]
        return centers

    def _window_origin(self) -> Optional[Tuple[int, int]]:
        """当前窗口左上角；窗口没了/取不到时返回 None。"""
        import win32gui

        try:
            left, top, _, _ = win32gui.GetWindowRect(self.hwnd)
        except Exception:
            return None
        return int(left), int(top)

    def _ensure_clickable(self) -> bool:
        """点击前确认窗口可见、未最小化，且位置与当前地标帧一致。

        窗口最小化或被挪动后 ``self._origin`` 就作废了，继续按它换算绝对坐标会把点击
        送到错误位置。这里先还原窗口；位置对不上就重新取帧，让地标与原点重新对齐。
        返回 False 表示当前无法安全点击，调用方应当放弃本次点击。
        """
        u = _user32()

        if _restore_if_minimized(self.hwnd):
            _force_foreground(self.hwnd)
            self.refresh()
        else:
            # 屏幕分辨率会变。窗口一旦比屏幕大，工具栏就跑到屏幕外，之后所有点击都会被
            # `_click` 的越界检查拒绝（表现为"文件一直发不出去"），所以点击前先把窗口收进屏幕。
            self._ensure_usable_geometry()

        try:
            if not u.IsWindowVisible(self.hwnd) or u.IsIconic(self.hwnd):
                return False
        except Exception:
            return False

        if self._lines and self._window_origin() == self._origin:
            return True

        # 窗口被移动/还原过，或还没有任何地标帧：重新取一帧再核对
        self.refresh()
        return bool(self._lines) and self._window_origin() == self._origin

    def _click_window(self, x: int, y: int) -> bool:
        """点击窗口内相对坐标 (x, y)；返回 False 表示本次点击已被安全放弃。"""
        if not self._ensure_clickable():
            return False
        try:
            _click(*self._abs(x, y))
        except WeChatWindowsError:
            return False
        return True

    def _send_button(self, lines: Optional[Sequence[OcrLine]] = None) -> Optional[OcrLine]:
        """定位「发送」按钮。

        OCR 会把这两个字认错（实测见过 ``发 法``），所以只要是以「发」开头的两三个字
        就认——**认不出来会退化成比例估算，纵坐标能差近 20px，工具栏图标就点不中了**。
        取最靠右、最靠下的那个候选（发送按钮在输入区右下角）。

        ``lines`` 可以传入"只 OCR 了窗口底部一条"的结果，省下整窗 OCR。
        """
        candidates = []
        for line in (self._lines if lines is None else lines):
            flat = line.flat()
            if flat == "发送" or (flat.startswith("发") and 1 < len(flat) <= 3):
                candidates.append(line)
        if not candidates:
            return None
        return max(candidates, key=lambda line: (line.cx, line.cy))

    def _session_left(self) -> int:
        """会话列表的右边界（= 聊天区左边界）。会话列表宽度是固定的，别用比例猜。"""
        rows = self._session_rows()
        return max((line.x1 for line in rows), default=int(self._size[0] * 0.24))

    def _toolbar_y(self) -> int:
        """工具栏（表情/文件/截图…）所在的纵坐标。

        优先用 OCR 找到的「发送」按钮；找不到就按"输入区在窗口底部"估一个——
        灰色禁用态的「发送」二字 OCR 有时读不出来，不能因此断定界面没渲染。
        """
        send = self._send_button()
        if send is not None:
            return send.cy
        return int(self._size[1] * 0.93)

    def _chat_title(self) -> Optional[OcrLine]:
        """聊天标题 = 窗口上方、位于左侧会话列表右侧的那行文字。

        会话列表宽度是**固定像素**（不随窗口缩放），所以不能用比例当边界；这里改用
        「搜索」占位文字的位置作为左边界，再用"最靠上"定位标题，并排掉几种干扰：
        搜索框、纯时间戳（会话列表里的 10:38）、以及单字符（会话列表的 + 按钮）。
        """
        search = next((line for line in self._lines if "搜索" in line.flat()), None)
        threshold = (search.x1 + 40) if search else int(self._size[0] * 0.28)
        # 标题恒在顶部标题栏（设备像素约 120~160），与会话列表里的时间/摘要行拉开距离
        top_bound = max(220, int(self._size[1] * 0.08))
        candidates = [
            line for line in self._lines
            if line.x0 > threshold
            and line.cy < top_bound            and "搜索" not in line.flat()
            and len(line.flat()) >= 2
            and not _is_timestamp(line.text)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda line: line.cy)

    def _input_point(self) -> Tuple[int, int]:
        """聊天输入框**文字区**里的一个点。

        注意别取到工具栏那一行（y 太高会点到表情/文件图标，太低会落到输入框下边缘），
        取工具栏往上约 13% 窗口高度的位置最稳。
        """
        x = self._session_left() + int(self._size[0] * 0.20)
        y = self._toolbar_y() - int(self._size[1] * 0.13)
        return x, y

    def _chat_area(self) -> Tuple[int, int, int, int]:
        """返回 (左边, 上边, 下边, 右边)，用于圈定聊天消息区。"""
        left = self._session_left()
        title = self._chat_title()
        right = title.x1 + int(self._size[0] * 0.4) if title else self._size[0]
        top = int(self._size[1] * 0.12)
        bottom = self._toolbar_y() - int(self._size[1] * 0.04)
        return left, top, bottom, right

    def _session_rows(self) -> List[OcrLine]:
        """左侧会话列表里的文字行（标题左边的一切）。"""
        title = self._chat_title()
        limit = title.x0 if title else int(self._size[0] * 0.5)
        rows = [
            line for line in self._lines
            if line.cy > self._size[1] * 0.12
            and line.x1 < limit
            and "搜索" not in line.flat()
        ]
        rows.sort(key=lambda line: line.cy)
        return rows

    # ---- 对外能力 ----
    @staticmethod
    def _title_looks_truncated(line: OcrLine) -> bool:
        """标题是不是被"截断显示"了。

        窗口窄时微信会把会话名截成「文件传...」。OCR 对那个省略号的识别很不稳定，
        实测见过认成 ``@``、``“``、``"``，所以这里按"结尾出现省略号类字符"来判断。
        """
        raw = line.text.rstrip()
        if not raw:
            return False
        return any(raw.endswith(mark) for mark in _TRUNCATION_MARKS)

    def _widen_for_title(self) -> bool:
        """把窗口拉宽再重读标题：名字被截断只是**窗口太窄的显示问题**，不是会话不存在。"""
        import win32gui

        try:
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
            width, height = right - left, bottom - top
            target_w = min(1800, max(1350, screen_w - 20))
            if target_w <= width + 60:
                return False
            win32gui.MoveWindow(self.hwnd, left, top, target_w, height, True)
            time.sleep(1.2)
            _nudge_repaint(self.hwnd)
            self.refresh()
            return True
        except Exception:
            return False

    def current_chat(self, fresh: bool = False) -> str:
        """当前聊天标题。

        ``fresh=True`` 强制重新取帧（切换会话后、发送后这种"必须看新画面"的场合）；
        默认允许复用 ``FRAME_TTL`` 内的帧——连续读数时能省掉整次截图 + OCR。
        """
        self.refresh(force=fresh)
        title = self._chat_title()
        # 两种情况都先试着把窗口拉宽再读一次：
        #   1) 标题被截断显示成「文件传...」——名字只是显示不下，会话本身没问题；
        #   2) 标题整行都读不到——窗口太窄时聊天区被挤没，标题根本不渲染。
        # 拉宽不成功（窗口已经够宽）时 _widen_for_title 会直接返回 False，不浪费一次重读。
        if title is None or self._title_looks_truncated(title):
            if self._widen_for_title():
                title = self._chat_title() or title
        return _normalize_ocr_text(title.text) if title else ""

    def ChatInfo(self) -> Dict[str, str]:
        return {"chat_name": self.current_chat()}

    def GetSession(self) -> List[SessionEntry]:
        self.refresh()
        entries: List[SessionEntry] = []
        seen = set()
        for row in self._session_rows():
            name = _normalize_ocr_text(row.text)
            if name and name not in seen:
                seen.add(name)
                entries.append(SessionEntry(name))
        return entries

    @timed("ChatWith（切换会话）")
    def ChatWith(self, who: str, exact: bool = True, **_: object) -> bool:
        """切换到目标会话：已在目标会话则不点击（避免触发重绘），否则点会话列表并校验。"""
        want = "".join(who.split()).lower()
        current = self.current_chat()
        got = "".join(current.split()).lower()
        if (got == want) if exact else (want in got):
            return True

        self.refresh()
        target = None
        for row in self._session_rows():
            flat = row.flat()
            if not flat:
                continue
            if (flat == want) if exact else (want in flat):
                target = row
                break
        if target is None:
            # 退一步：会话列表文字可能把"名字+摘要"识别成一行
            for row in self._session_rows():
                if want and want in row.flat():
                    target = row
                    break
        if target is None:
            # 再退一步：**会话行被截断显示**（列表窄时是「2028届1班家校通知…」）。
            # 截断行是目标名的前缀，所以反过来判断"目标名以前缀开头"；为避免撞名，
            # 只在唯一命中时才用它——而且它只负责"找到行"，真正的安全校验仍然是
            # 点击之后的标题比对（标题被截断时 current_chat 会先拉宽窗口再读）。
            prefixes = [
                row for row in self._session_rows()
                if len(row.flat()) >= 3 and want.startswith(row.flat())
            ]
            if len(prefixes) == 1:
                target = prefixes[0]
        if target is None:
            raise WeChatWindowsError(
                f"会话列表里没有找到「{who}」。"
                "请确认名称，或先把目标会话打开（可用 --no-switch）。"
            )
        if not self._click_window(target.x0 + 10, target.cy):
            raise WeChatWindowsError(
                "微信窗口当前不可点击（可能刚被最小化或移出屏幕），已放弃切换会话。"
                "请保持微信主窗口打开可见后重试。"
            )
        time.sleep(0.8)
        # 点击后微信会重绘聊天区，**必须重新取帧**（fresh=True）再下结论：
        # 拿点之前的缓存帧等于没校验。这里读到不一致就中止，是"防发错人"的安全底线。
        for attempt in range(3):
            current = self.current_chat(fresh=True)
            got = "".join(current.split()).lower()
            if (got == want) if exact else (want in got):
                return True
            time.sleep(0.6)
        raise WeChatWindowsError(
            f"点击后当前会话是「{current or '未知'}」，与目标「{who}」不一致，已停止。"
        )

    def _input_box_top(self) -> int:
        """聊天输入框区域的顶边。

        输入框比工具栏高得多（约占窗口下方 28%），用 ``toolbar_y - 20`` 当边界会把
        输入框里的文字误判成聊天消息（实测踩过：探针文字明明进去了却报"没聚焦"）。
        输入框为空时里面有一行占位文字「按住鼠标 语音输入文字」，那是最好用的地标。
        """
        for line in self._lines:
            flat = line.flat()
            if "语音输入文字" in flat or "输入文字" in flat:
                return max(0, line.cy - 60)
        return self._toolbar_y() - int(self._size[1] * 0.28)

    # ---- 局部 OCR（只认一小块，比整窗快 2~3 倍）----
    def _region_lines(self, top: int, bottom: Optional[int] = None) -> List[OcrLine]:
        """重新截一帧，但**只 OCR 窗口的某一条横带**，坐标系仍是窗口坐标。

        整窗 OCR 一次约 0.85s，而"文字有没有进输入框""附件有没有挂上"这类判断只关心
        窗口底部那一条；裁出来 OCR 约 0.3s。代价是这一帧不写入地标缓存（``_lines``
        仍是上一次整窗帧），所以调用方不要用它做全窗口的判断。
        """
        try:
            _nudge_repaint(self.hwnd)
            image, rect = _capture(self.hwnd)
        except Exception:
            return []
        top = max(0, min(int(top), rect[3] - 1))
        bottom = rect[3] if bottom is None else max(top + 1, min(int(bottom), rect[3]))
        self._origin = (rect[0], rect[1])
        self._size = (rect[2], rect[3])
        try:
            crop = image.crop((0, top, rect[2], bottom))
        except Exception:
            return []
        lines = ocr_image(crop)
        # 裁切后 y 从 0 开始，补回偏移量，好让调用方沿用窗口坐标
        for line in lines:
            line.y0 += top
            line.y1 += top
        return lines

    def _input_contains(self, needle: str) -> bool:
        """检查这段文字是不是**已经在输入框里**（回车前的关键确认）。

        必须多轮 + 多帧：微信重绘是异步的，刚 ``Ctrl+V`` 完那一下截到的往往还是旧画面，
        读不到文字就会误报「未能确认文字进入输入框」。所以这里最多看 **2 帧**，
        每帧都强制重新取（``force=True``）——但每帧只取一次（``max_rounds=1``），
        原实现一轮里还要重试 3 遍，等于把同一个画面反复读三遍。
        """
        key = _match_key(needle)
        if not key:
            return False
        # 最多看 3 帧（每帧约 0.65s，命中就立刻返回）：微信重绘比我们截图慢的时候，
        # 只看 2 帧偶尔会读不到刚粘进去的文字（实测约 1/3 次），多看一眼比自己猜更划算。
        for i in range(3):
            if i:
                time.sleep(0.35)
            # 只看输入框那一条：整窗 OCR 要 0.85s，这一条约 0.3s
            if _match_any(self._region_lines(self._input_box_top()), key):
                return True
        return False

    @timed("SendMsg（发文本）")
    def SendMsg(self, text: str, **_: object) -> Dict[str, str]:
        """发送文本：聚焦输入框 → 粘贴**一次** → 确认 → 回车。

        合成键鼠只送给**前台窗口**，所以每一步之前都要确保微信在最前
        （机器上可能还开着第二个微信的登录窗口，很容易把焦点抢走）。

        **粘贴只做一次**。原实现每次重试都重新粘贴，而 OCR 复核在客户端认错字时
        永远不通过，于是输入框被粘贴 2~3 遍、最后一次性发出，**消息内容成倍重复**
        （实测同一条文字被发成 3 遍）。这里只在"点不到输入框"时重试粘贴——那种
        情况下什么都没粘进去，重试是安全的；校验本身失败只在"看"上重试。
        """
        typed = False
        for _ in range(3):
            _force_foreground(self.hwnd)
            if not self._click_window(*self._input_point()):
                time.sleep(0.5)
                continue
            # 输入框聚焦到能接受粘贴只需要几十毫秒；下面的确认环节会重新截图核对，
            # 所以这里不必再为"保险"多睡——原来 0.6+0.3+0.9 合计 1.8s。
            time.sleep(0.35)
            _set_clipboard_text(text)
            time.sleep(0.15)
            _key(VK_V, ctrl=True)
            time.sleep(0.6)
            typed = True
            break

        # 发送**前**的输入框确认：只有 --blind（盲发）才跳过，--no-verify 不跳过它。
        if self.skip_input_check:
            entered = typed
        else:
            entered = typed and self._input_contains(text)

        # 无论确认与否都按一次回车：确认失败也可能是截图滞后造成的误判，
        # 此时回车无害；若文本确实没进去，回车同样无害。
        _key(VK_RETURN)
        # 回车是同步注入的，微信的事件队列立刻收到；这里 1s 是留给"消息渲染出来"的，
        # 而随后的复核本来就会多帧重试，不需要再等更久。
        time.sleep(1.0)
        # --no-verify 只跳过**发送后**的结果复核（_text_present + 上层 verify_sent）
        if self.skip_verify:
            self.verify_note = (
                None
                if entered
                else "发送前未能确认文字进入输入框；已按 --no-verify 跳过发送后复核"
            )
            return {"status": "成功", "message": "已提交（已跳过发送后复核）"}
        if self._text_present(text):
            self.self_confirmed.append(text)
            return {"status": "成功", "message": "已发送"}
        self.self_missing.append(text)
        self.verify_note = (
            ("未能确认文字进入输入框；" if not entered else "")
            + "自研后端依赖 OCR 复核，微信界面重绘较慢时读不到刚发出的消息；"
            "本次未能复核，请自行确认会话中是否收到"
        )
        return {"status": "成功", "message": "已提交（未能 OCR 复核）"}

    def SendFiles(self, filepath, who: Optional[str] = None, **_: object) -> Dict[str, str]:
        """发送文件：点工具栏「发送文件」→ 文件对话框填路径 → 回车提交。

        对话框正常走完（弹出 → 填好路径 → 确认）就算提交成功；随后的 OCR 复核只是
        锦上添花——微信重绘慢时读不到文件名并不代表没发出去，所以那种情况**不判失败**，
        而是记一条 verify_note 交给上层提示。
        """
        paths = [filepath] if isinstance(filepath, (str, os.PathLike)) else list(filepath)
        paths = [str(Path(p).resolve()) for p in paths]
        for path in paths:
            if not os.path.isfile(path):
                return {"status": "失败", "message": f"文件不存在: {path}"}
        for path in paths:
            self._send_one_file(path)
            time.sleep(0.6)
        if self.skip_verify:
            self.self_confirmed.extend(Path(p).name for p in paths)
            self.verify_note = "已按 --no-verify 跳过发送后复核"
            return {"status": "成功", "message": "已提交（已跳过发送后复核）"}
        missing = [p for p in paths if not self._text_present(Path(p).name)]
        for path in paths:
            name = Path(path).name
            if path in missing:
                self.self_missing.append(name)
            else:
                self.self_confirmed.append(name)
        if missing:
            self.verify_note = (
                "文件已通过对话框提交，但未能 OCR 复核到文件名（微信重绘较慢）："
                + "、".join(Path(p).name for p in missing)
            )
            return {"status": "成功", "message": "已提交（未能 OCR 复核）"}
        return {"status": "成功", "message": "已发送"}

    def _restore_main_window(self) -> bool:
        """主窗口被最小化或被隐藏时把它弄回来；返回是否做过还原。

        误点到工具栏的**截图**或**小程序**图标时，微信会把自己的主窗口藏起来/最小化
        （截图会接管整个屏幕）。此时缓存的窗口原点和地标全部失效，后续点击都会落空，
        表现为"发送失败"。所以每次试探之后都要检查并恢复一次。
        """
        u = _user32()
        try:
            hidden = not u.IsWindowVisible(self.hwnd)
            minimized = bool(u.IsIconic(self.hwnd))
        except Exception:
            return False
        if not (hidden or minimized):
            return False
        try:
            _restore_if_minimized(self.hwnd)
            if hidden:
                u.ShowWindow(self.hwnd, SW_SHOW)
            _force_foreground(self.hwnd)
            time.sleep(0.6)
            return True
        except Exception:
            return False

    @timed("_send_one_file（发一个文件）")
    def _send_one_file(self, path: str) -> None:
        # 重新取一次地标：窗口可能刚被最小化/还原或改过尺寸，旧坐标会点偏
        _force_foreground(self.hwnd)
        self._restore_main_window()
        self.refresh()

        def candidates() -> List[int]:
            """给出要尝试的窗口内 x 坐标：**先量出来的图标，再退化为猜偏移。**

            工具栏左起依次是 表情 / 收藏(小程序) / **发送文件** / 截图 / 语音 / 喇叭，
            所以第 3 个图标就是目标。量不出来（像素分析失败）才用偏移兜底——
            猜偏移会先点到邻居图标，用户看到的就是"每次都先去点发送收藏"。
            """
            icons = self._toolbar_icons()
            ordered: List[int] = []
            if len(icons) >= 3:
                ordered.append(icons[2])                    # 量出来的「发送文件」
                ordered.extend(x for i, x in enumerate(icons) if i != 2)
            chat_left = self._session_left()
            span = max(120, min(460, self._size[0] - chat_left - 60))
            offsets = [259, 200, 232, 300, 168, 336, 136, 376, 104, 416, 72, 456]
            for off in offsets:
                if 0 < off < span:
                    ordered.append(chat_left + off)
            # 去重保序
            seen = set()
            return [x for x in ordered if not (x in seen or seen.add(x))]

        dialog = None
        tried = 0
        xs = candidates()
        for index, x in enumerate(xs):
            # 每次点击前重算 y：_click_window 可能因窗口刚被还原/移动而重新取帧
            toolbar_y = self._toolbar_y()
            tried += 1
            if not self._click_window(x, toolbar_y):
                continue
            dialog = self._wait_dialog(timeout=1.4)
            if dialog:
                break
            _key(VK_ESCAPE)          # 关掉误开的表情/收藏/小程序等弹层
            time.sleep(0.3)
            # 误点到「截图」会接管屏幕并把主窗口藏起来，这里必须检查并恢复，
            # 否则后面的候选位置全部按失效坐标点击，文件永远发不出去。
            if self._restore_main_window():
                self.refresh()
                # 窗口动过之后，剩下的"量出来的图标"也可能失效，重算一次候选
                rest = candidates()
                xs = xs[: index + 1] + [c for c in rest if c not in xs[: index + 1]]
        if not dialog:
            raise WeChatWindowsError(
                "点了工具栏但没弹出文件选择对话框（「发送文件」按钮没命中）。"
                f"已试过 {tried} 个位置（含像素分析量出的图标）。"
            )
        self._fill_dialog(dialog, path)
        _key(VK_RETURN)          # 确认选择，关闭文件对话框
        # 等附件挂到输入框：**轮询文件名出现**，通常 1 秒左右就能继续，
        # 比无条件睡 2.5 秒既快又可靠（附件没挂上时点「发送」是空点）。
        self._wait_attachment(path)
        # 关掉文件对话框后主窗口可能没回到前台（甚至被藏起来），先弄回来再提交附件
        self._restore_main_window()
        # 附件这时已经挂在输入框里（「发送」按钮变绿）。
        # 关键：**回车提交不了已挂的附件**（实测文件会一直停在输入框里等发送），
        # 必须点「发送」按钮。
        self._click_send_button()
        # 点完「发送」不必等太久：附件是否真的提交由 SendFiles 末尾的复核负责，
        # 它会自己多帧重试（原来这里 1.5s + _click_send_button 里 1.5s 共 3s）。
        time.sleep(0.8)

    def _wait_attachment(self, path: str, timeout: float = 4.0) -> bool:
        """等附件真正挂到输入框（以文件名出现在界面上为准），最多等 ``timeout`` 秒。

        返回是否在超时前看到。看不到也**不抛错**：微信重绘慢或 OCR 认错字都可能读不到，
        而点「发送」本身是安全的（附件没挂上时它什么也不做）。
        """
        name = _match_key(Path(path).name)
        if not name:
            time.sleep(1.5)
            return False
        deadline = time.monotonic() + timeout
        while True:
            # 只看输入框那一条（附件就挂在那里），别为等它整窗 OCR
            if _match_any(self._region_lines(self._input_box_top()), name):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.4)

    def _send_button_point(self, lines: Optional[Sequence[OcrLine]] = None) -> Tuple[int, int]:
        """「发送」按钮的坐标。

        OCR 有时读不出这颗按钮（灰色/绿色的小字），所以读不到就按比例兜底——
        实测它稳定落在窗口右下角约 (0.915w, 0.924h)。
        """
        send = self._send_button(lines)
        if send is not None:
            return send.cx, send.cy
        return int(self._size[0] * 0.915), int(self._size[1] * 0.924)

    def _click_send_button(self) -> bool:
        """点聊天输入区右下角的「发送」按钮（附件发送必须走这里）。"""
        # 刚挂上附件，要的是这一瞬间的画面；而「发送」按钮只在窗口底部那一条，
        # 所以只截一帧 + 只 OCR 那一条，比整窗 refresh 快一倍多。
        lines = self._region_lines(max(0, self._toolbar_y() - 30))
        _click(*self._abs(*self._send_button_point(lines or None)))
        time.sleep(1.0)
        return True

    @staticmethod
    def _wait_dialog(timeout: float) -> Optional[int]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for hwnd in _enum_windows():
                try:
                    import win32gui

                    if win32gui.GetClassName(hwnd) != DIALOG_CLASS:
                        continue
                except Exception:
                    continue
                name = _process_name(_window_pid(hwnd)).lower()
                if name.startswith(WECHAT_CLIENT_EXES[:2]):
                    return hwnd
            time.sleep(0.3)
        return None

    @staticmethod
    def _find_filename_edit(dialog: int) -> int:
        """在标准文件对话框里找「文件名(N)」输入框（纯 Win32，不需要 UIA）。"""
        import win32gui

        top = win32gui.GetWindowRect(dialog)[1]
        height = win32gui.GetWindowRect(dialog)[3] - top
        best_hwnd, best_width = 0, -1
        found = []

        def _cb(hwnd, _):
            try:
                if win32gui.GetClassName(hwnd) != "Edit":
                    return True
                rect = win32gui.GetWindowRect(hwnd)
            except Exception:
                return True
            found.append((hwnd, rect))
            return True

        win32gui.EnumChildWindows(dialog, _cb, None)
        for hwnd, rect in found:
            if (rect[1] + rect[3]) / 2 > top + height * 0.7:
                width = rect[2] - rect[0]
                if width > best_width:
                    best_hwnd, best_width = hwnd, width
        return best_hwnd

    @staticmethod
    def _fill_dialog(dialog: int, path: str) -> None:
        """把完整路径写进「文件名(N)」框。

        首选**纯 Win32**（``WM_SETTEXT``）——不需要 UI Automation，在受限环境里也能用；
        失败时才回退到 UIA 的 ValuePattern。
        """
        import win32con
        import win32gui

        edit = WinWeChat._find_filename_edit(dialog)
        if edit:
            try:
                win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, path)
                time.sleep(0.4)
                if win32gui.GetWindowText(edit) == path:
                    return
                # 有些对话框的 Edit 不回报文本，这里也认为已写入
                return
            except Exception:
                pass

        try:
            import uiautomation as uia
        except Exception:
            raise WeChatWindowsError(
                "没有找到文件对话框的「文件名」输入框，且 uiautomation 不可用。"
            ) from None
        control = uia.ControlFromHandle(dialog)
        stack = [control]
        edit = None
        while stack:
            node = stack.pop()
            try:
                kids = node.GetChildren()
            except Exception:
                continue
            for kid in kids:
                stack.append(kid)
                try:
                    if kid.ControlTypeName == "EditControl" and (kid.AutomationId or "") == "1148":
                        edit = kid
                except Exception:
                    continue
        if edit is None:
            raise WeChatWindowsError("文件对话框里没有找到「文件名」输入框。")
        try:
            edit.GetValuePattern().SetValue(path)
        except Exception as exc:
            raise WeChatWindowsError(f"写入文件路径失败: {exc}") from exc

    # ---- 校验 ----
    @timed("_lines_union（多帧并集）")
    def _lines_union(self, times: int = 3) -> List[OcrLine]:
        """多次催重绘+截图，把识别到的文本行取并集。

        微信重绘是异步的，单帧很可能还是"发送前"的画面；取并集能避免把已成功的内容
        判成失败。**只用于"某段文字是否存在"这类判断，不用于会话标题**（并集里可能
        混着切换会话前的旧标题）。

        每次都重新取帧（``force=True``），但每轮只取一帧（``max_rounds=1``）：
        这里要的是"多看几眼"，不是"对同一眼反复看"——后者只是把同一个画面重读三遍。
        """
        merged: Dict[Tuple[str, int, int], OcrLine] = {}
        for i in range(times):
            if i:
                _nudge_repaint(self.hwnd)
                time.sleep(0.4)
            self.refresh(force=True, max_rounds=1)
            for line in self._lines:
                merged[(line.flat(), line.cx // 20, line.cy // 20)] = line
        return list(merged.values())

    def _text_present(self, needle: str) -> bool:
        """判断这段文字是否**已经发出**（出现在聊天区，而不是还躺在输入框里）。"""
        key = _match_key(needle)
        if not key:
            return False
        # 轮数压到 2：这条校验发生在**回车之后**，多轮重试就是"消息已经发出去了还在等"。
        for i in range(2):
            if i:
                _force_foreground(self.hwnd)
                _nudge_repaint(self.hwnd)
                time.sleep(0.5)
            # 忽略仍在输入框里的行：那说明回车没生效，不能算发送成功
            input_top = self._input_box_top()
            self.refresh(force=True, max_rounds=1)
            lines = [line for line in self._lines if line.cy < input_top]
            if _match_any(lines, key):
                return True
        return False

    def GetAllMessage(self) -> List[ChatMessage]:
        """返回窗口内识别到的文本行，供 skill 做发送后校验。

        注意：这里**不做聊天区裁剪**。微信的消息列表经常不跟着重绘（实测：新发出的
        消息只在会话列表的预览里先出现），一旦按聊天区裁剪就会把已成功的发送判成失败。
        宁可放宽范围用于"内容是否出现"的判断，也不要误报失败。
        """
        messages: List[ChatMessage] = []
        seen = set()
        for line in self._lines_union(3):
            text = _normalize_ocr_text(line.text)
            if not text or len(text) < 2:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            kind = "file" if ("." in text and " " not in text) else "text"
            messages.append(ChatMessage(type=kind, content=text))
        return messages

    def message_texts(self) -> List[str]:
        return [m.content for m in self.GetAllMessage()]


# --------------------------------------------------------------------------- #
# 自检（排障用）
# --------------------------------------------------------------------------- #
def _dump(params) -> int:
    """打印窗口信息与本后端依赖的全部 OCR 地标，用于定位"点偏了/找不到"。

    只读：不会发送任何消息、不会点击任何按钮（``--activate`` 只把窗口置前）。
    """
    if not ocr_available():
        print("OCR 不可用：缺少 winrt 系列包，本后端无法定位界面地标。")
        print("请在 SKILL.md 的依赖列表里确认 winrt-Windows.* 已安装。")
        return 1

    try:
        wx = WinWeChat()
    except WeChatWindowsError as exc:
        print(f"失败: {exc}")
        return 1

    if params.activate:
        wx.activate()
    wx.refresh()

    print(f"主窗口 hwnd={wx.hwnd}  origin={wx._origin}  size={wx._size}")
    if getattr(wx, "_image", None) is not None and params.shot:
        try:
            wx._image.save(params.shot)
            print(f"窗口截图: {params.shot}")
        except Exception as exc:
            print(f"截图保存失败: {exc}")

    chat = wx.current_chat()
    print(f"当前聊天标题: {chat!r}")
    print(f"会话栏左边界 x={wx._session_left()}")
    print(f"输入框顶边 y={wx._input_box_top()}   输入点={wx._input_point()}")
    send = wx._send_button()
    print(
        "「发送」按钮: "
        + (f"({send.cx}, {send.cy}) 文本={send.text!r}" if send else "未识别（会按比例兜底）")
    )

    icons = wx._toolbar_icons()
    print(f"工具栏图标 x（像素分析）: {icons}")
    if len(icons) > 2:
        point = wx._abs(icons[2], wx._toolbar_y())
        print(f"「发送文件」按钮推算点击点: {point}")
    else:
        print("「发送文件」按钮: 像素分析未切出图标（会退回偏移试探）")

    rows = wx._session_rows()
    print(f"会话列表识别到 {len(rows)} 行:")
    for row in rows[: params.limit]:
        print(f"  ({row.cx}, {row.cy}) {row.text!r}")

    if params.messages:
        messages = wx.GetAllMessage()
        print(f"全窗口识别到 {len(messages)} 行（含聊天区与会话列表）:")
        for message in messages:
            print(f"  [{message.type}] {message.content!r}")

    if params.session:
        try:
            hit = wx.ChatWith(params.session, exact=False)
        except Exception as exc:
            print(f"切换会话失败: {exc}")
            return 1
        print(f"切换到 {params.session!r}: {'成功' if hit else '未命中'}")
        print(f"切换后标题: {wx.current_chat()!r}")

    for line in timing_lines():
        print(line)
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="自研 RPA 后端自检：打印主窗口与 OCR 地标（只读，不发送）"
    )
    parser.add_argument("--shot", metavar="PNG", help="把当前窗口截图存到该路径，便于人工核对")
    parser.add_argument("--session", help="顺带测试切换到该会话（只读，不发送）")
    parser.add_argument("--messages", action="store_true", help="打印全窗口 OCR 出来的每一行")
    parser.add_argument("--activate", action="store_true", help="先把微信主窗口置前")
    parser.add_argument("--limit", type=int, default=10, help="最多打印多少行会话列表")
    sys.exit(_dump(parser.parse_args()))
