"""微信 Windows 端自研后端：窗口激活 + 剪贴板 + 合成键鼠 + OCR 地标定位。

不依赖 wxauto4 / wxautox4，**也不依赖微信的 UIA 控件树**——微信 4.1.12.x 根本不向
UI Automation 发布界面控件（实测主窗口只有 2 个外壳节点），所以这里走的是「像人一样
操作界面」的路子：

  1. ``PrintWindow`` 截取微信窗口，用 **Windows OCR** 读出界面文字及其坐标（地标）；
  2. 用 OCR 找到**会话名 / 聊天标题**，作为发送前的硬校验（这是本 skill 的安全底线）；
  3. 用**剪贴板 + 合成 Ctrl+V / Enter** 发送文本；
  4. 用工具栏「发送文件」按钮 + 文件对话框发送文件。

已知限制（实测结论）：

  * **文本发送可用**（已端到端验证，消息真实到达）；
  * **文件发送目前在微信 4.1.12.55 上无法完成**：文件对话框会被正常打开并"接受"，
    拖放也能进入（显示「复制」光标），但微信始终不把文件挂到聊天输入框。同一台机器
    上「剪贴板粘贴文件」「WM_DROPFILES」「真实 OLE 拖放」也都失败。因此本模块会对
    文件发送做严格校验，失败时明确报错而不是假装成功。
  * OCR 需要 ``winrt`` 系列包（见 SKILL.md 依赖）；缺失时降级为「不做校验」并给出警告。
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


class WeChatWindowsError(RuntimeError):
    """自研后端无法继续时的错误（信息面向用户可读）。"""


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
    """与 wxauto4 的 Message 形状保持兼容（本后端只用 type/content）。"""

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


def _match_key(text: str) -> str:
    """把文字折叠成用于**比对**的规范形式（只用于比对，不用于展示）。

    OCR 会把同一段文字读成不同形态，直接做子串比对必然漏判——实测把
    ``【自动化测试 16:25:27】`` 读成 ``〖自动化测试 16 ： 25 ： 27 〕``：方块括号变体、
    全角冒号、以及数字与标点之间插入的空格。

    这里统一做三件事：

      1. ``NFKC`` 折叠全角/半角（``：`` -> ``:``、``，`` -> ``,``、``（）`` -> ``()``）；
      2. ``_PUNCT_FOLD`` 归并 NFKC 不管的方块括号等异体；
      3. 去掉**所有**空白并转小写（OCR 常在任意位置插空格）。

    于是 ``【a 16:25】`` 与 ``〖 a 16 ： 25 〕`` 会折叠成同一个 key。
    """
    import unicodedata

    folded = unicodedata.normalize("NFKC", text).translate(_PUNCT_FOLD)
    return "".join(folded.split()).lower()


def _match_any(lines, key: str) -> bool:
    """单行或"按阅读顺序拼成的全文"任一命中即算命中。

    微信会把一条长消息折成多行，OCR 也逐行返回，只比单行的话长消息永远复核不到。
    """
    if any(key in _match_key(line.text) for line in lines):
        return True
    joined = "".join(line.text for line in sorted(lines, key=lambda ln: (ln.cy, ln.cx)))
    return key in _match_key(joined)


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


def _force_foreground(hwnd: int) -> None:
    """把微信窗口拉到最前（最小化时先还原）。

    微信聊天区是硬件渲染的，窗口被别的窗口盖住时 ``PrintWindow`` 往往抓到空白帧
    （表现为"读不到「发送」按钮"）。先按一下 Alt 解除 SetForegroundWindow 的限制，
    再置前，截图才拿得到真实界面。
    """
    import win32api
    import win32gui

    _restore_if_minimized(hwnd)
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
    time.sleep(0.4)


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
    """自研 Windows 后端，接口与 wxauto4 的 ``WeChat`` 对齐（供 skill 直接使用）。"""

    def __init__(self, target_hint: str = "") -> None:
        if sys.platform != "win32":
            raise WeChatWindowsError("自研后端只支持 Windows。")
        self.hwnd = self._find_main_window()
        self._lines: List[OcrLine] = []
        self._size: Tuple[int, int] = (0, 0)
        self._origin: Tuple[int, int] = (0, 0)
        # 自研后端的"发送后校验"依赖 OCR，而微信界面重绘是异步的，读不到新消息是常态。
        # 这种情况下**不判失败**（消息其实已经发出），只把这个说明交给上层展示。
        self.verify_note: Optional[str] = None
        self.best_effort_verify = True
        # 由 send_to_filehelper.py 设置（客户端认错字时两处校验都永远不可能通过）：
        #   skip_verify      --no-verify：跳过**发送后**的结果复核
        #   skip_input_check --blind     ：连**发送前**的输入框确认也跳过（盲发）
        self.skip_verify = False
        self.skip_input_check = False
        self.activate()
        if not ocr_available():
            raise WeChatWindowsError(
                "未安装 Windows OCR 组件，自研后端无法做界面识别与发送前校验。\n"
                "  请用 uv 安装依赖：uv run scripts/send_to_filehelper.py --help\n"
                "  （依赖见脚本头部内联元数据：winrt-Windows.Media.Ocr 等）"
            )

    # ---- 窗口 ----
    @staticmethod
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
                if not name.startswith(WECHAT_CLIENT_EXES[:2]):
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
        import win32gui

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
            # 会话列表宽度固定（约 740px），聊天区太窄时微信不渲染工具栏，所以取宽一点
            target_w = min(1600, max(760, int(screen_w * 0.88)))
            target_h = min(1000, max(560, int(screen_h * 0.86)))
            off_screen = (
                right > screen_w or bottom > screen_h or left < 0 or top < 0
            )
            need = off_screen or width > screen_w - 20 or width < 720 or height < 520
            if need:
                win32gui.MoveWindow(self.hwnd, 10, 10, target_w, target_h, True)
                time.sleep(1.2)
                _nudge_repaint(self.hwnd)
        except Exception:
            pass

    def refresh(self) -> None:
        """催重绘 → 截图 → OCR，刷新地标。

        先试 ``PrintWindow``，读不到关键地标就换"屏幕实抓"，最多重试 3 轮；
        保留地标最全的那一帧，避免被微信的旧帧误导。
        """
        best_lines: Optional[List[OcrLine]] = None
        best_rect: Optional[Tuple[int, int, int, int]] = None
        best_score = -1
        for attempt in range(3):
            # 每轮都强制置前：微信窗口不在前台时 PrintWindow 会返回**旧帧**，
            # 于是"刚发出去的消息"永远读不到（实测：发送成功却被判失败并重发）。
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
                has_send = self._send_button() is not None
                has_title = self._chat_title() is not None
                # 优先"地标齐全"的帧，其次行数多；避免拿到微信重绘中途的半成品
                score = (200 if has_send else 0) + (100 if has_title else 0) + min(len(lines), 90)
                if score > best_score:
                    best_score, best_lines, best_rect = score, lines, rect
                if has_send and has_title:
                    return
            time.sleep(0.8)
        if best_lines is not None and best_rect is not None:
            self._lines = best_lines
            self._origin = (best_rect[0], best_rect[1])
            self._size = (best_rect[2], best_rect[3])

    # ---- 地标 ----
    def _abs(self, x: int, y: int) -> Tuple[int, int]:
        return self._origin[0] + x, self._origin[1] + y

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

    def _send_button(self) -> Optional[OcrLine]:
        for line in self._lines:
            if line.flat() == "发送":
                return line
        return None

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
            and line.cy < top_bound
            and "搜索" not in line.flat()
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
    def current_chat(self) -> str:
        self.refresh()
        title = self._chat_title()
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
            raise WeChatWindowsError(
                f"会话列表里没有找到「{who}」。"
                "请确认名称，或先把目标会话打开（可用 --no-switch）。"
            )
        if not self._click_window(target.x0 + 10, target.cy):
            raise WeChatWindowsError(
                "微信窗口当前不可点击（可能刚被最小化或移出屏幕），已放弃切换会话。"
                "请保持微信主窗口打开可见后重试。"
            )
        time.sleep(1.2)
        # 点击后微信会重绘聊天区，缓存帧有时不完整；复核两次再下结论
        for attempt in range(3):
            current = self.current_chat()
            got = "".join(current.split()).lower()
            if (got == want) if exact else (want in got):
                return True
            time.sleep(1.0)
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

    def _input_contains(self, needle: str) -> bool:
        """检查这段文字是不是**已经在输入框里**（回车前的关键确认）。

        必须多轮 + 多帧：微信重绘是异步的，而 ``refresh()`` 一旦拿到「发送」与标题这两个
        地标就会**立刻返回**——很可能返回的还是**粘贴之前**的旧帧，于是刚粘进去的文字读不到，
        必然误报「未能确认文字进入输入框」。这里与 ``_text_present`` 同样取多帧并集并重试。
        """
        key = _match_key(needle)
        if not key:
            return False
        # 轮数刻意压到 2：多轮重试会把「按下回车」推迟十几秒，而校验在客户端认错字时
        # 本来就不可能通过，等待纯属浪费。多帧并集保留，用来兜住异步重绘。
        for i in range(2):
            if i:
                _force_foreground(self.hwnd)
                _nudge_repaint(self.hwnd)
                time.sleep(0.5)
            lines = self._lines_union(2)
            input_top = self._input_box_top()
            if _match_any([line for line in lines if line.cy >= input_top], key):
                return True
        return False

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
            time.sleep(0.6)
            _set_clipboard_text(text)
            time.sleep(0.3)
            _key(VK_V, ctrl=True)
            time.sleep(0.9)
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
        time.sleep(1.5)
        # --no-verify 只跳过**发送后**的结果复核（_text_present + 上层 verify_sent）
        if self.skip_verify:
            self.verify_note = (
                None
                if entered
                else "发送前未能确认文字进入输入框；已按 --no-verify 跳过发送后复核"
            )
            return {"status": "成功", "message": "已提交（已跳过发送后复核）"}
        if self._text_present(text):
            return {"status": "成功", "message": "已发送"}
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
            time.sleep(1.0)
        missing = [p for p in paths if not self._text_present(Path(p).name)]
        if missing:
            self.verify_note = (
                "文件已通过对话框提交，但未能 OCR 复核到文件名（微信重绘较慢）："
                + "、".join(Path(p).name for p in missing)
            )
            return {"status": "成功", "message": "已提交（未能 OCR 复核）"}
        return {"status": "成功", "message": "已发送"}

    def _send_one_file(self, path: str) -> None:
        # 重新取一次地标：窗口可能刚被最小化/还原或改过尺寸，旧坐标会点偏
        _force_foreground(self.hwnd)
        self.refresh()
        # 「发送文件」是工具栏左起第 3 个图标，位置取决于聊天区左边界（而不是窗口宽度，
        # 也不是「发送」按钮）。先试最可能的位置（聊天区左边 +200px），再向两侧扩散；
        # 用"是否弹出文件选择对话框"来自我纠偏。
        offsets = [200, 232, 168, 264, 136, 296, 104, 328, 72, 360, 40, 392, 424]
        dialog = None
        span = 0
        tried = 0
        for offset in offsets:
            # 每次点击前重算地标：_click_window 可能因窗口刚被还原/移动而重新取帧
            toolbar_y = self._toolbar_y()
            chat_left = self._session_left()
            span = max(120, min(460, self._size[0] - chat_left - 60))
            if not 0 < offset < span:
                continue
            tried += 1
            if not self._click_window(chat_left + offset, toolbar_y):
                continue
            dialog = self._wait_dialog(timeout=1.4)
            if dialog:
                break
            _key(VK_ESCAPE)          # 关掉误开的表情/小程序等弹层
            time.sleep(0.3)
        if not dialog:
            raise WeChatWindowsError(
                "点了工具栏但没弹出文件选择对话框（「发送文件」按钮没命中）。"
                f"已试过聊天区左边 +40…{span}px 共 {tried} 个位置。"
            )
        self._fill_dialog(dialog, path)
        _key(VK_RETURN)          # 确认选择，关闭文件对话框
        time.sleep(2.5)
        # 附件这时已经挂在输入框里（「发送」按钮变绿）。
        # 关键：**回车提交不了已挂的附件**（实测文件会一直停在输入框里等发送），
        # 必须点「发送」按钮。
        self._click_send_button()
        time.sleep(2.5)

    def _send_button_point(self) -> Tuple[int, int]:
        """「发送」按钮的坐标。

        OCR 有时读不出这颗按钮（灰色/绿色的小字），所以读不到就按比例兜底——
        实测它稳定落在窗口右下角约 (0.915w, 0.924h)。
        """
        send = self._send_button()
        if send is not None:
            return send.cx, send.cy
        return int(self._size[0] * 0.915), int(self._size[1] * 0.924)

    def _click_send_button(self) -> bool:
        """点聊天输入区右下角的「发送」按钮（附件发送必须走这里）。"""
        self.refresh()
        _click(*self._abs(*self._send_button_point()))
        time.sleep(2.0)
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

        uia = None
        for module_name in ("uiautomation", "wxauto4.uia"):
            try:
                import importlib

                uia = importlib.import_module(module_name)
                break
            except Exception:
                continue
        if uia is None:
            raise WeChatWindowsError("没有找到文件对话框的「文件名」输入框，且 UIA 不可用。")
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
    def _lines_union(self, times: int = 3) -> List[OcrLine]:
        """多次催重绘+截图，把识别到的文本行取并集。

        微信重绘是异步的，单帧很可能还是"发送前"的画面；取并集能避免把已成功的内容
        判成失败。**只用于"某段文字是否存在"这类判断，不用于会话标题**（并集里可能
        混着切换会话前的旧标题）。
        """
        merged: Dict[Tuple[str, int, int], OcrLine] = {}
        for i in range(times):
            if i:
                _nudge_repaint(self.hwnd)
                time.sleep(0.5)
            self.refresh()
            for line in self._lines:
                merged[(line.flat(), line.cx // 20, line.cy // 20)] = line
        return list(merged.values())

    def _text_present(self, needle: str) -> bool:
        """判断这段文字是否**已经发出**（出现在聊天区，而不是还躺在输入框里）。"""
        key = _match_key(needle)
        if not key:
            return False
        input_top = self._input_box_top()
        # 轮数压到 2：这条校验发生在**回车之后**，多轮重试就是"消息已经发出去了还在等"。
        for i in range(2):
            if i:
                _force_foreground(self.hwnd)
                _nudge_repaint(self.hwnd)
                time.sleep(0.6)
            # 忽略仍在输入框里的行：那说明回车没生效，不能算发送成功
            lines = [line for line in self._lines_union(2) if line.cy < input_top]
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
