"""微信 macOS 后端（微信 4.x / 仅界面自动化）

通过 macOS Accessibility (AX) API 驱动**用户本人已登录**的微信 Mac 客户端：
激活窗口 → 打开目标会话 → 校验会话标题 → 剪贴板粘贴文件 → 回车发送。

不做协议破解、不注入、不读取数据库；只操作 VoiceOver 读取的同一棵
辅助功能树，以及合成键鼠事件。

依赖（由主脚本的内联元数据在 macOS 上自动安装）：
    pyobjc-framework-Cocoa
    pyobjc-framework-Quartz
    pyobjc-framework-ApplicationServices

用到的微信 4.x 辅助功能标识（参考社区实现 wechat-mcp 的实测结论）：
    chat_input_field        聊天输入框（TextArea）
    big_title_line_h_view   当前会话标题（StaticText）
    chat_message_list       会话消息列表（List）
    chat_bubble_item_view   单条消息气泡
    session_item_<名称>      左侧会话列表中的一行（StaticText）
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

BUNDLE_ID = "com.tencent.xinWeChat"
INPUT_FIELD_ID = "chat_input_field"
CHAT_TITLE_ID = "big_title_line_h_view"
MESSAGE_LIST_ID = "chat_message_list"
MESSAGE_BUBBLE_ID = "chat_bubble_item_view"
SESSION_ITEM_PREFIX = "session_item_"
SEARCH_TITLES = ("Search", "搜索")

MAX_AX_DEPTH = 40
ACTIVATE_SETTLE = 0.35
PASTE_SETTLE = 0.8
SEND_SETTLE = 1.0

RETURN_KEYCODE = 36
V_KEYCODE = 9
A_KEYCODE = 0

PERMISSION_HINT = (
    "需要给「运行本命令的宿主 App」授予辅助功能权限：\n"
    "  系统设置 → 隐私与安全性 → 辅助功能 → 勾选你的终端（Terminal / iTerm / VS Code / dsh 等）\n"
    "授权后请完全退出并重新打开该 App，然后运行：\n"
    "  uv run scripts/send_to_filehelper.py --check"
)

_IMPORT_ERROR: Optional[BaseException] = None
try:  # pragma: no cover - 依赖导入
    import AppKit
    from ApplicationServices import (  # type: ignore import-not-found
        AXIsProcessTrusted,
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        AXValueGetType,
        AXValueGetValue,
        kAXChildrenAttribute,
        kAXIdentifierAttribute,
        kAXPositionAttribute,
        kAXRaiseAction,
        kAXRoleAttribute,
        kAXSizeAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
        kAXValueCGPointType,
        kAXValueCGSizeType,
        kAXWindowsAttribute,
        kAXStaticTextRole,
        kAXTextAreaRole,
    )
    from Quartz import (  # type: ignore import-not-found
        CGEventCreateKeyboardEvent,
        CGEventCreateMouseEvent,
        CGEventPost,
        CGEventSetFlags,
        CGPoint,
        kCGEventFlagMaskCommand,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGHIDEventTap,
    )
except ImportError as exc:  # pragma: no cover - 仅在缺少依赖时触发
    _IMPORT_ERROR = exc


class MacBackendError(RuntimeError):
    """macOS 后端无法继续执行（含权限、未登录、找不到控件等）。"""


class MacPermissionError(MacBackendError):
    """缺少辅助功能权限。"""


@dataclass
class SessionRow:
    """左侧会话列表中的一行。"""

    name: str
    element: Any
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Tuple[float, float]:
        return self.x + self.width / 2.0, self.y + self.height / 2.0


def _require_frameworks() -> None:
    if _IMPORT_ERROR is not None:
        raise MacBackendError(
            "缺少 pyobjc 依赖（应已由 uv 自动安装）。请用 uv 运行脚本：\n"
            "  uv run scripts/send_to_filehelper.py --check"
        ) from _IMPORT_ERROR


# --------------------------------------------------------------------------- #
# AX 基础操作
# --------------------------------------------------------------------------- #
def ax_get(element: Any, attribute: str) -> Any:
    err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    if err != 0:
        return None
    return value


def ax_children(element: Any) -> List[Any]:
    return list(ax_get(element, kAXChildrenAttribute) or [])


def dfs(
    element: Any,
    predicate: Callable[[Any, Any, Any, Any], bool],
    depth: int = 0,
) -> Any:
    """深度优先查找满足条件的 AX 元素。"""
    if element is None or depth > MAX_AX_DEPTH:
        return None
    role = ax_get(element, kAXRoleAttribute)
    title = ax_get(element, kAXTitleAttribute)
    identifier = ax_get(element, kAXIdentifierAttribute)
    if predicate(element, role, title, identifier):
        return element
    for child in ax_children(element):
        found = dfs(child, predicate, depth + 1)
        if found is not None:
            return found
    return None


def _point_of(element: Any) -> Optional[Tuple[float, float]]:
    ref = ax_get(element, kAXPositionAttribute)
    if ref is None or AXValueGetType(ref) != kAXValueCGPointType:
        return None
    ok, point = AXValueGetValue(ref, kAXValueCGPointType, None)
    if not ok:
        return None
    return float(point.x), float(point.y)


def _size_of(element: Any) -> Optional[Tuple[float, float]]:
    ref = ax_get(element, kAXSizeAttribute)
    if ref is None or AXValueGetType(ref) != kAXValueCGSizeType:
        return None
    ok, size = AXValueGetValue(ref, kAXValueCGSizeType, None)
    if not ok:
        return None
    return float(size.width), float(size.height)


def normalize_chat_name(name: Optional[str]) -> str:
    """去掉群聊标题尾部的成员数后缀，如「工作群(23)」→「工作群」。"""
    if not name:
        return ""
    return re.sub(r"\(\d+\)$", "", name.strip()).strip()


def _keyboard(keycode: int, command: bool = False) -> None:
    flags = kCGEventFlagMaskCommand if command else 0
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    CGEventSetFlags(down, flags)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    CGEventSetFlags(up, flags)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


def _click(x: float, y: float) -> None:
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, CGPoint(x, y), 0)
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, CGPoint(x, y), 0)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


# --------------------------------------------------------------------------- #
# 微信客户端
# --------------------------------------------------------------------------- #
class MacWeChat:
    """封装 macOS 微信 4.x 的最小操作集。"""

    def __init__(self) -> None:
        _require_frameworks()
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            BUNDLE_ID
        )
        if not apps:
            raise MacBackendError(
                "未检测到正在运行的微信 Mac 客户端。请先打开并登录微信 4.x。"
            )
        self._ns_app = apps[0]
        self._ax_app = None

    # -- 环境 ------------------------------------------------------------- #
    @staticmethod
    def is_running() -> bool:
        if _IMPORT_ERROR is not None:
            return False
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            BUNDLE_ID
        )
        return bool(apps)

    @staticmethod
    def permission_granted() -> bool:
        if _IMPORT_ERROR is not None:
            return False
        return bool(AXIsProcessTrusted())

    @staticmethod
    def version() -> Optional[str]:
        if _IMPORT_ERROR is not None:
            return None
        try:
            apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                BUNDLE_ID
            )
            return apps[0].bundleVersion() if apps else None
        except Exception:
            return None

    @property
    def ax_app(self) -> Any:
        if self._ax_app is None:
            self._ax_app = AXUIElementCreateApplication(
                self._ns_app.processIdentifier()
            )
        return self._ax_app

    def _check_permission(self) -> None:
        if not AXIsProcessTrusted():
            raise MacPermissionError(PERMISSION_HINT)

    def activate(self) -> None:
        was_active = bool(self._ns_app.isActive())
        self._ns_app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        if not was_active:
            time.sleep(ACTIVATE_SETTLE)

    # -- 会话 ------------------------------------------------------------- #
    def current_chat(self) -> Optional[str]:
        """当前打开的会话名称；读不到返回 None。"""

        def is_title(el: Any, role: Any, title: Any, identifier: Any) -> bool:
            return role == kAXStaticTextRole and identifier == CHAT_TITLE_ID

        element = dfs(self.ax_app, is_title)
        if element is None:
            return None
        value = ax_get(element, kAXValueAttribute)
        if isinstance(value, str) and value.strip():
            return normalize_chat_name(value)
        title = ax_get(element, kAXTitleAttribute)
        if isinstance(title, str) and title.strip():
            return normalize_chat_name(title)
        return None

    def _window_frame(self) -> Optional[Tuple[float, float, float, float]]:
        windows = ax_get(self.ax_app, kAXWindowsAttribute) or []
        for window in windows:
            point = _point_of(window)
            size = _size_of(window)
            if point and size and size[0] > 100 and size[1] > 100:
                return point[0], point[1], size[0], size[1]
        return None

    def sidebar_rows(self) -> List[SessionRow]:
        """左侧会话列表中当前渲染出来的行。"""
        rows: List[SessionRow] = []

        def walk(element: Any, depth: int = 0) -> None:
            if depth > 12:
                return
            identifier = ax_get(element, kAXIdentifierAttribute)
            if isinstance(identifier, str) and identifier.startswith(
                SESSION_ITEM_PREFIX
            ):
                name = identifier[len(SESSION_ITEM_PREFIX) :]
                point = _point_of(element)
                size = _size_of(element)
                if name and point and size:
                    rows.append(
                        SessionRow(
                            name=name,
                            element=element,
                            x=point[0],
                            y=point[1],
                            width=size[0],
                            height=size[1],
                        )
                    )
            for child in ax_children(element):
                walk(child, depth + 1)

        walk(self.ax_app)
        rows.sort(key=lambda row: row.y)
        return rows

    def _find_search_field(self) -> Any:
        text_areas: List[Any] = []

        def walk(element: Any, depth: int = 0) -> None:
            if depth > MAX_AX_DEPTH:
                return
            if ax_get(element, kAXRoleAttribute) == kAXTextAreaRole:
                identifier = ax_get(element, kAXIdentifierAttribute)
                if identifier != INPUT_FIELD_ID:
                    text_areas.append(element)
            for child in ax_children(element):
                walk(child, depth + 1)

        walk(self.ax_app)
        for area in text_areas:
            title = ax_get(area, kAXTitleAttribute)
            if isinstance(title, str) and title in SEARCH_TITLES:
                return area
        if text_areas:
            return text_areas[0]
        raise MacBackendError(
            "未能定位微信搜索框。请确认微信主窗口已打开（未最小化）。"
        )

    def _type_into_search(self, text: str) -> None:
        field = self._find_search_field()
        AXUIElementPerformAction(field, kAXRaiseAction)
        AXUIElementSetAttributeValue(field, kAXValueAttribute, "")
        self.set_text_clipboard(text)
        time.sleep(0.1)
        _keyboard(A_KEYCODE, command=True)
        time.sleep(0.05)
        _keyboard(V_KEYCODE, command=True)

    def _click_row(self, row: SessionRow) -> bool:
        frame = self._window_frame()
        if frame is None:
            return False
        wx, wy, ww, wh = frame
        cx, cy = row.center
        # 只点击确实落在窗口内的行，避免点到桌面上其它位置
        if not (wx <= cx <= wx + ww and wy <= cy <= wy + wh):
            return False
        _click(cx, cy)
        return True

    def open_chat(
        self, target: str, exact: bool = True
    ) -> Tuple[bool, Optional[str], List[str]]:
        """打开目标会话并校验。

        Returns:
            (是否成功, 当前会话名, 候选会话名列表)
        """
        self._check_permission()
        wanted = normalize_chat_name(target)

        current = self.current_chat()
        if self._name_matches(current, wanted, exact):
            return True, current, []

        # 1) 搜索并回车（对不在会话列表里的联系人也有效）
        try:
            self._type_into_search(target)
            time.sleep(0.4)
            _keyboard(RETURN_KEYCODE)
            time.sleep(0.6)
        except MacBackendError:
            pass
        current = self.current_chat()
        if self._name_matches(current, wanted, exact):
            return True, current, []

        # 2) 回退：点击左侧会话列表中的同名行
        rows = self.sidebar_rows()
        candidates = [row.name for row in rows]
        for row in rows:
            if self._name_matches(normalize_chat_name(row.name), wanted, exact):
                if self._click_row(row):
                    time.sleep(0.6)
                    current = self.current_chat()
                    if self._name_matches(current, wanted, exact):
                        return True, current, candidates
                break

        return False, self.current_chat(), candidates

    @staticmethod
    def _name_matches(current: Optional[str], wanted: str, exact: bool) -> bool:
        if not current or not wanted:
            return False
        if exact:
            return current == wanted
        return wanted in current

    # -- 输入框 / 剪贴板 -------------------------------------------------- #
    def _input_field(self) -> Any:
        def is_input(el: Any, role: Any, title: Any, identifier: Any) -> bool:
            return role == kAXTextAreaRole and identifier == INPUT_FIELD_ID

        field = dfs(self.ax_app, is_input)
        if field is None:
            raise MacBackendError(
                "未能定位微信输入框（chat_input_field）。请确认已打开某个会话窗口。"
            )
        return field

    def focus_input(self) -> None:
        AXUIElementPerformAction(self._input_field(), kAXRaiseAction)

    def set_files_clipboard(self, paths: Sequence[str]) -> None:
        """把多个文件放到系统剪贴板（等价于访达中拷贝这些文件）。"""
        from AppKit import NSURL

        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        urls = [NSURL.fileURLWithPath_(str(path)) for path in paths]
        if not pasteboard.writeObjects_(urls):
            raise MacBackendError("写入剪贴板失败，无法复制文件。")

    def set_text_clipboard(self, text: str) -> None:
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)

    def paste(self) -> None:
        _keyboard(V_KEYCODE, command=True)

    def press_return(self) -> None:
        _keyboard(RETURN_KEYCODE)

    def send_text(self, text: str) -> None:
        """在当前会话发送一条文本（直接写输入框内容再回车）。"""
        field = self._input_field()
        AXUIElementPerformAction(field, kAXRaiseAction)
        err = AXUIElementSetAttributeValue(field, kAXValueAttribute, text)
        if err != 0:
            raise MacBackendError(f"写入输入框失败（AX 错误 {err}）")
        time.sleep(0.2)
        self.press_return()

    def send_files(
        self, paths: Sequence[str], batch: bool = True, interval: float = 0.8
    ) -> None:
        """粘贴文件并回车发送。batch=False 时逐个文件发送。"""
        batches: List[Sequence[str]] = (
            [list(paths)] if batch else [[path] for path in paths]
        )
        for index, chunk in enumerate(batches):
            self.set_files_clipboard(chunk)
            self.focus_input()
            time.sleep(0.15)
            self.paste()
            time.sleep(PASTE_SETTLE)
            self.press_return()
            time.sleep(SEND_SETTLE)
            if index + 1 < len(batches):
                time.sleep(max(interval, 0.3))

    # -- 消息读取（尽力而为） --------------------------------------------- #
    def message_texts(self) -> Optional[List[str]]:
        """读取当前会话中已渲染消息的文本；无法读取时返回 None。"""

        def is_list(el: Any, role: Any, title: Any, identifier: Any) -> bool:
            return identifier == MESSAGE_LIST_ID or title == "Messages"

        message_list = dfs(self.ax_app, is_list)
        if message_list is None:
            return None

        texts: List[str] = []
        for child in ax_children(message_list):
            title = ax_get(child, kAXTitleAttribute)
            value = ax_get(child, kAXValueAttribute)
            for candidate in (title, value):
                if isinstance(candidate, str) and candidate.strip():
                    texts.append(candidate.strip())
                    break
        return texts
