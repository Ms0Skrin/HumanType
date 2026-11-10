"""A small RU / EN mark that shows for a moment at the text caret when the layout changes.
It never takes the focus and lets clicks through. The caret is asked of Windows first, then of the
field itself through UI Automation (browsers, terminals and most modern apps draw their own);
where neither tells, the mark shows at the mouse cursor, like the other notes."""

import ctypes
from ctypes import wintypes

import win32api
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QLabel

from . import theme, uia

user32 = ctypes.WinDLL("user32", use_last_error=True)

HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOACTIVATE = 0x0001, 0x0010
MONITOR_DEFAULTTONEAREST = 2


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)

QSS = """
QLabel {{
    background: rgba(18, 26, 48, 242); color: {color};
    border: 1px solid {border}; border-radius: 9px;
    padding: 3px 9px; font-family: {font}; font-size: 12px; font-weight: 700;
}}
"""
LOOK = {
    "ru": ("RU", theme.ACCENT, "rgba(56, 189, 248, 0.6)"),
    "en": ("EN", theme.TEXT, "rgba(139, 149, 181, 0.6)"),
}


def caret_point(hwnd: int) -> tuple[int, int] | None:
    """The bottom of the text caret of the window's thread, in screen pixels; None when it has none."""
    thread = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not thread or not user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.hwndCaret:
        return None
    rect = info.rcCaret
    if rect.bottom <= rect.top:
        return None
    point = wintypes.POINT(rect.right, rect.bottom)
    if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
        return None
    return point.x, point.y


def place(point: tuple[int, int], size: tuple[int, int], area: tuple[int, int, int, int]) -> tuple[int, int]:
    """Where a mark of `size` goes for the caret at `point`: just below and to the right, kept inside `area`
    (left, top, right, bottom); above the caret when there is no room below."""
    x, y = point[0] + 8, point[1] + 6
    left, top, right, bottom = area
    if y + size[1] > bottom:
        y = point[1] - size[1] - 28
    return max(left, min(x, right - size[0])), max(top, min(y, bottom - size[1]))


class Indicator(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._animate(0.0, 220))
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(lambda: self.windowOpacity() < 0.05 and self.hide())
        # UI Automation is slow the first time it is asked on a thread: ask before it is needed.
        QTimer.singleShot(3000, uia.warm_up)

    def show_layout(self, lang: str, hwnd: int, ms: int = 750) -> None:
        if lang not in LOOK:
            return
        text, color, border = LOOK[lang]
        self.setStyleSheet(QSS.format(color=color, border=border, font=theme.FONT))
        self.setText(text)
        self.adjustSize()
        point = caret_point(hwnd) or uia.caret_point()
        if point is None:
            cursor = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            point = (cursor.x + 8, cursor.y + 12)
        if not self.isVisible():
            self.setWindowOpacity(0.0)
        self.show()
        # Placed through Win32, in screen pixels: the caret is given in them, whatever the display scale.
        ratio = self.devicePixelRatioF()
        size = (round(self.width() * ratio), round(self.height() * ratio))
        try:
            area = win32api.GetMonitorInfo(win32api.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST))["Work"]
        except Exception:
            area = (point[0] - 4000, point[1] - 4000, point[0] + 4000, point[1] + 4000)
        x, y = place(point, size, tuple(area))
        user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        self._animate(1.0, 110)
        self._timer.start(ms)

    def _animate(self, to: float, ms: int) -> None:
        self._fade.stop()
        self._fade.setDuration(ms)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(to)
        self._fade.start()
