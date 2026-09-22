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

import os
import plistlib
import re
import subprocess
import sys
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
F_KEYCODE = 3
ESC_KEYCODE = 53

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
        AXUIElementCopyElementAtPosition,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        AXUIElementSetMessagingTimeout,
        AXValueGetType,
        AXValueGetValue,
        kAXChildrenAttribute,
        kAXFocusedWindowAttribute,
        kAXIdentifierAttribute,
        kAXPositionAttribute,
        kAXRaiseAction,
        kAXRoleAttribute,
        kAXSizeAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
        kAXValueCGPointType,
        kAXValueCGSizeType,
        kAXWindowRole,
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
DEBUG = False


def set_debug(enabled: bool) -> None:
    global DEBUG
    DEBUG = bool(enabled)


def _debug(message: str) -> None:
    if not DEBUG:
        return
    try:
        sys.stderr.write(f"[ax] {message}\n")
        sys.stderr.flush()
    except Exception:
        pass


AX_ERROR_NAMES = {
    0: "success",
    -25200: "failure",
    -25201: "illegalArgument",
    -25202: "invalidUIElement",
    -25203: "invalidUIElementObserver",
    -25204: "cannotComplete",
    -25205: "attributeUnsupported",
    -25206: "actionUnsupported",
    -25207: "notificationUnsupported",
    -25208: "notImplemented",
    -25209: "notificationAlreadyRegistered",
    -25210: "notificationNotRegistered",
    -25211: "apiDisabled(辅助功能未授权?)",
    -25212: "noValue",
    -25213: "parameterizedAttributeUnsupported",
    -25214: "notEnoughPrecision",
}


def ax_error_name(err: int) -> str:
    return AX_ERROR_NAMES.get(err, f"error {err}")


def _count(value: Any) -> Optional[int]:
    """AX 数组类属性的元素个数；pyobjc 返回的不一定是 list/tuple。"""
    if value is None:
        return None
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        pass
    try:
        return sum(1 for _ in value)
    except TypeError:
        return None


def ax_copy(element: Any, attribute: str) -> Tuple[int, Any]:
    """返回 (AX 错误码, 值)，便于诊断。"""
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception as exc:  # pyobjc 在元素失效时可能直接抛异常
        _debug(f"copy {attribute} 异常: {exc}")
        return -25202, None
    # attributeUnsupported / noValue 是遍历树时的常态，不刷屏
    if err not in (0, -25205, -25212):
        _debug(f"copy {attribute} -> {ax_error_name(err)}")
    return err, value


def ax_get(element: Any, attribute: str) -> Any:
    err, value = ax_copy(element, attribute)
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
        """优先用运行中的应用信息，取不到就读 App 包的 Info.plist。"""
        if _IMPORT_ERROR is not None:
            return None
        try:
            apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                BUNDLE_ID
            )
        except Exception as exc:
            _debug(f"枚举微信进程失败: {exc}")
            return None
        if not apps:
            return None
        app = apps[0]
        try:
            getter = getattr(app, "bundleVersion", None)
            if callable(getter):
                version = getter()
                if version:
                    return str(version)
        except Exception as exc:
            _debug(f"bundleVersion 不可用: {exc}")
        try:
            bundle_url = app.bundleURL()
            if bundle_url is not None:
                plist_path = bundle_url.path() + "/Contents/Info.plist"
                with open(plist_path, "rb") as handle:
                    info = plistlib.load(handle)
                for key in ("CFBundleShortVersionString", "CFBundleVersion"):
                    if info.get(key):
                        return str(info[key])
        except Exception as exc:
            _debug(f"读取 Info.plist 失败: {exc}")
        return None

    @property
    def ax_app(self) -> Any:
        if self._ax_app is None:
            self._ax_app = AXUIElementCreateApplication(
                self._ns_app.processIdentifier()
            )
            try:
                AXUIElementSetMessagingTimeout(self._ax_app, 2.0)
            except Exception as exc:  # pragma: no cover - 老系统可能不支持
                _debug(f"设置 AX 超时失败: {exc}")
        return self._ax_app

    def _check_permission(self) -> None:
        if not AXIsProcessTrusted():
            raise MacPermissionError(PERMISSION_HINT)

    def activate(self, reopen_if_needed: bool = True) -> None:
        was_active = bool(self._ns_app.isActive())
        self._ns_app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        if not was_active:
            time.sleep(ACTIVATE_SETTLE)
        if reopen_if_needed and not self.windows():
            self.reopen_main_window()

    def reopen_main_window(self, timeout: float = 5.0) -> bool:
        """微信主窗口被关闭（只留在 Dock/菜单栏）时重新打开它。"""
        if os.environ.get("SEND_TO_FILEHELPER_NO_REOPEN"):
            _debug("SEND_TO_FILEHELPER_NO_REOPEN 已设置，跳过自动重开微信窗口")
            return bool(self.windows())
        _debug("未发现 AX 窗口，尝试 open -b 重新打开微信主窗口")
        try:
            subprocess.run(
                ["open", "-b", BUNDLE_ID],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except Exception as exc:
            _debug(f"open -b {BUNDLE_ID} 失败: {exc}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.windows():
                time.sleep(ACTIVATE_SETTLE)
                return True
            time.sleep(0.25)
        return bool(self.windows())

    # -- 诊断 ------------------------------------------------------------- #
    def window_summaries(self, limit: int = 3) -> List[str]:
        """窗口标题/是否最小化/位置尺寸，用于诊断。"""
        summaries: List[str] = []
        for window in self.windows()[:limit]:
            title = ax_get(window, kAXTitleAttribute) or "?"
            minimized = ax_get(window, "AXMinimized")
            point = _point_of(window)
            size = _size_of(window)
            summaries.append(
                f"{title!r} minimized={bool(minimized)} pos={point} size={size}"
            )
        return summaries

    def windows(self) -> List[Any]:
        """当前存在的 AX 窗口（主窗口被关闭时为 0）。"""
        err, value = ax_copy(self.ax_app, kAXWindowsAttribute)
        candidates = list(value or []) if err == 0 else []
        if not candidates:
            # 某些版本/状态下 AXWindows 取不到，退回按角色扫描直接子元素
            candidates = [
                child
                for child in ax_children(self.ax_app)
                if ax_get(child, kAXRoleAttribute) == kAXWindowRole
            ]
        result: List[Any] = []
        for window in candidates:
            size = _size_of(window)
            if size is None or (size[0] >= 80 and size[1] >= 80):
                result.append(window)
        return result

    @staticmethod
    def related_processes() -> List[str]:
        """列出所有微信相关进程及其 AX 窗口数。

        微信 4.x 是多进程架构，界面有可能不属于 `com.tencent.xinWeChat`
        主进程；当主进程读不到窗口时用这个列表确认界面到底在谁那里。
        """
        _require_frameworks()
        lines: List[str] = []
        try:
            apps = AppKit.NSWorkspace.sharedWorkspace().runningApplications()
        except Exception as exc:
            _debug(f"枚举进程失败: {exc}")
            return lines

        for app in apps:
            try:
                bundle_id = str(app.bundleIdentifier() or "")
                url = app.bundleURL()
                path = str(url.path() or "") if url is not None else ""
            except Exception:
                continue
            if "com.tencent.xinWeChat" not in bundle_id and "WeChat" not in path:
                continue
            count = -1
            try:
                element = AXUIElementCreateApplication(app.processIdentifier())
                try:
                    AXUIElementSetMessagingTimeout(element, 1.0)
                except Exception:
                    pass
                err, value = ax_copy(element, kAXWindowsAttribute)
                if err == 0 and isinstance(value, (list, tuple)):
                    count = len(value)
                else:
                    count = -1
            except Exception as exc:
                _debug(f"探测 pid={app.processIdentifier()} 失败: {exc}")
            name = str(app.localizedName() or "?")
            lines.append(
                f"pid={int(app.processIdentifier())} {name!r} "
                f"bundle={bundle_id or '-'} AX窗口={count if count >= 0 else '读取失败'} "
                f"path={path or '-'}"
            )
        return lines

    def element_at(self, x: float, y: float) -> Any:
        """屏幕坐标命中测试：即使应用不暴露子元素树也能拿到该点的元素。"""
        try:
            err, element = AXUIElementCopyElementAtPosition(
                self.ax_app, float(x), float(y), None
            )
        except Exception as exc:
            _debug(f"命中测试 ({x:.0f},{y:.0f}) 异常: {exc}")
            return None
        if err != 0:
            _debug(f"命中测试 ({x:.0f},{y:.0f}) -> {ax_error_name(err)}")
            return None
        return element

    # 命中测试采样点：窗口内的相对坐标
    HIT_SAMPLES = (
        ("标题栏", 0.5, 0.02),
        ("会话列表", 0.12, 0.30),
        ("聊天区", 0.60, 0.40),
        ("输入区", 0.60, 0.93),
    )

    def hit_test_lines(self) -> List[str]:
        frame = self._window_frame()
        if frame is None:
            return ["命中测试: 无可用窗口"]
        wx, wy, ww, wh = frame
        lines: List[str] = []
        for label, fx, fy in self.HIT_SAMPLES:
            element = self.element_at(wx + ww * fx, wy + wh * fy)
            if element is None:
                lines.append(f"命中测试 {label}: 无元素")
                continue
            role = ax_get(element, kAXRoleAttribute) or "?"
            identifier = ax_get(element, kAXIdentifierAttribute) or ""
            title = ax_get(element, kAXTitleAttribute) or ""
            value = ax_get(element, kAXValueAttribute) or ""
            extra = f" title={str(title)[:30]!r}" if title else ""
            if not extra and value:
                extra = f" value={str(value)[:30]!r}"
            lines.append(f"命中测试 {label}: {role} #{identifier or '-'}{extra}")
        return lines

    def probe(self) -> List[str]:
        """对应用元素做最小 AX 探测，输出人类可读的诊断行。

        用来区分三类失败：AX 被拦截（返回错误码）、应用没有窗口、
        或窗口在但内容树为空（微信未暴露，需要键盘兜底）。
        """
        lines: List[str] = []
        role_err, role = ax_copy(self.ax_app, kAXRoleAttribute)
        lines.append(f"应用元素 AXRole -> {role!r} ({ax_error_name(role_err)})")

        win_err, win_value = ax_copy(self.ax_app, kAXWindowsAttribute)
        lines.append(
            f"AXWindows -> {_count(win_value)} 个 "
            f"[{type(win_value).__name__}] ({ax_error_name(win_err)})"
        )

        children_err, children = ax_copy(self.ax_app, kAXChildrenAttribute)
        lines.append(
            f"AXChildren -> {_count(children)} 个 "
            f"[{type(children).__name__}] ({ax_error_name(children_err)})"
        )

        focused_err, focused = ax_copy(self.ax_app, kAXFocusedWindowAttribute)
        lines.append(
            f"AXFocusedWindow -> {'有' if focused is not None else '无'} "
            f"({ax_error_name(focused_err)})"
        )

        for window in self.windows()[:1]:
            window_children = ax_get(window, kAXChildrenAttribute)
            lines.append(f"主窗口子元素 -> {_count(window_children)} 个")

        lines.extend(self.hit_test_lines())
        return lines

    def try_enable_enhanced_ui(self) -> List[Tuple[str, int]]:
        """尝试打开微信的完整辅助功能树（部分应用需要这个握手）。"""
        results: List[Tuple[str, int]] = []
        for attribute in ("AXEnhancedUserInterface", "AXManualAccessibility"):
            err = AXUIElementSetAttributeValue(self.ax_app, attribute, True)
            results.append((attribute, err))
            _debug(f"set {attribute}=True -> {ax_error_name(err)}")
        return results

    def debug_dump(self, limit: int = 40) -> List[str]:
        """把当前 AX 树（角色/标识/标题）打印成行，便于适配微信版本。"""
        lines: List[str] = []
        queue: List[Tuple[Any, int]] = [(self.ax_app, 0)]
        while queue and len(lines) < limit:
            element, depth = queue.pop(0)
            role = ax_get(element, kAXRoleAttribute) or "?"
            identifier = ax_get(element, kAXIdentifierAttribute) or ""
            title = ax_get(element, kAXTitleAttribute) or ""
            value = ax_get(element, kAXValueAttribute) or ""
            summary = f"{'  ' * depth}{role}"
            if identifier:
                summary += f"  #{identifier}"
            if title:
                summary += f"  title={str(title)[:40]!r}"
            elif value:
                summary += f"  value={str(value)[:40]!r}"
            lines.append(summary)
            for child in ax_children(element):
                queue.append((child, depth + 1))
        if len(lines) >= limit:
            lines.append(f"...（仅显示前 {limit} 个元素）")
        return lines

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
        for window in self.windows():
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
        if field is not None:
            return field

        # 回退：应用没有暴露子元素树时，用命中测试找输入区
        frame = self._window_frame()
        if frame is not None:
            wx, wy, ww, wh = frame
            for fx, fy in ((0.60, 0.93), (0.75, 0.93), (0.60, 0.88)):
                element = self.element_at(wx + ww * fx, wy + wh * fy)
                if element is not None and ax_get(element, kAXRoleAttribute) in (
                    kAXTextAreaRole,
                    "AXTextField",
                ):
                    return element

        raise MacBackendError(
            "未能定位微信输入框（chat_input_field）。请确认已打开某个会话窗口，"
            "或改用 --input-mode keystrokes。"
        )

    def ax_content_usable(self) -> bool:
        """微信是否真的把界面内容暴露给了辅助功能。"""
        try:
            if self.current_chat():
                return True
        except Exception:
            pass
        try:
            if self.sidebar_rows():
                return True
        except Exception:
            pass
        try:
            self._input_field()
            return True
        except MacBackendError:
            return False

    # -- 键盘兜底（不依赖 AX 内容树） ------------------------------------- #
    def focus_search_by_keys(self) -> None:
        _keyboard(ESC_KEYCODE)
        time.sleep(0.2)
        _keyboard(F_KEYCODE, command=True)
        time.sleep(0.5)

    def open_chat_by_keys(self, target: str) -> None:
        """Esc → Cmd+F → 粘贴目标名 → 回车。

        微信 Mac 的搜索框与输入框都能接受 Cmd+V，这条路径不依赖任何 AX
        标识，但因此也无法在发送前读取会话标题做校验。
        """
        self.focus_search_by_keys()
        self.set_text_clipboard(target)
        time.sleep(0.15)
        _keyboard(V_KEYCODE, command=True)
        time.sleep(0.7)
        _keyboard(RETURN_KEYCODE)
        time.sleep(0.9)

    def send_text_by_keys(self, text: str) -> None:
        self.set_text_clipboard(text)
        time.sleep(0.15)
        self.paste()
        time.sleep(0.4)
        self.press_return()
        time.sleep(0.6)

    def send_files_by_keys(
        self, paths: Sequence[str], batch: bool = True, interval: float = 0.8
    ) -> None:
        batches: List[Sequence[str]] = (
            [list(paths)] if batch else [[path] for path in paths]
        )
        for index, chunk in enumerate(batches):
            self.set_files_clipboard(chunk)
            time.sleep(0.2)
            self.paste()
            time.sleep(PASTE_SETTLE)
            self.press_return()
            time.sleep(SEND_SETTLE)
            if index + 1 < len(batches):
                time.sleep(max(interval, 0.3))

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
