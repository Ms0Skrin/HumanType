"""The keyboard layout of every program: remembered while its window is in front and brought back
when it comes to the front again (a terminal stays English, a messenger Russian). Also tells when
the layout of the window in front changes, for the indicator at the caret.

Looks at the window in front a few times a second, on the GUI thread: two cheap calls each time."""

import ctypes
import json
import logging
from ctypes import wintypes
from pathlib import Path

import win32api
import win32gui
import win32process
from PySide6.QtCore import QObject, QTimer, Signal

from .autoswitch import LANG_IDS, WM_INPUTLANGCHANGEREQUEST, imm32, is_fullscreen, layout_thread, process_name, thread_layout

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND

# The shell's own windows (the taskbar, the desktop, the Alt+Tab list): they come to the front in
# passing and nothing is typed there.
SHELL = {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "XamlExplorerHostIslandWindow",
    "MultitaskingViewFrame", "TaskSwitcherWnd", "ForegroundStaging", "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland",
}
INTERVAL_MS = 120
SAVE_MS = 10_000
GIVE_UP = 6  # looks after which a window that does not take the layout is left alone


class AppLayouts(QObject):
    changed = Signal(str, int)  # the layout the window in front has now ("ru" | "en" | "uk"), the window

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.remember = True
        self.layouts: dict[str, str] = {}  # process name, lowercase -> "ru" | "en" | "uk"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.layouts = {str(name).lower(): lang for name, lang in raw.items() if lang in LANG_IDS}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("app_layouts.json is unreadable, starting anew")
        self._own_pid = win32api.GetCurrentProcessId()
        self._hwnd = 0
        self._name = ""  # the program in front, "" when it is not followed
        self._layout: str | None = None
        self._want: str | None = None  # the layout asked of the window in front, until it takes it
        self._looks = 0
        self._dirty = False
        self._failed = False
        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._saver = QTimer(self)
        self._saver.setInterval(SAVE_MS)
        self._saver.timeout.connect(self.save)

    def set_active(self, on: bool) -> None:
        if on == self._timer.isActive():
            return
        if on:
            self._hwnd = 0
            self._timer.start()
            self._saver.start()
        else:
            self._timer.stop()
            self._saver.stop()
            self.save()

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(sorted(self.layouts.items())), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
        except OSError:
            log.exception("app_layouts.json not saved")

    def _tick(self) -> None:
        try:
            self._look()
        except Exception:
            if not self._failed:  # once: this runs eight times a second
                log.exception("following the layout of the window in front")
                self._failed = True

    def _look(self) -> None:
        hwnd = user32.GetForegroundWindow() or 0
        if not hwnd:
            return
        layout = thread_layout(layout_thread(hwnd))
        if hwnd != self._hwnd:
            self._hwnd, self._layout, self._want = hwnd, layout, None
            self._name = self._followed(hwnd)
            want = self.layouts.get(self._name) if self.remember and self._name else None
            if want and layout and want != layout:
                self._want, self._looks = want, 0
                self._ask(hwnd, want, deeper=False)
            return
        if self._want:
            if layout != self._want:
                self._looks += 1
                if self._looks in (2, 4):
                    self._ask(hwnd, self._want, deeper=True)
                elif self._looks > GIVE_UP:
                    self._want = None
                return
            self._want = None
        if layout is None:
            return
        if layout != self._layout:
            self._layout = layout
            if self._name:
                self.changed.emit(layout, hwnd)
        if self._name and self.layouts.get(self._name) != layout:
            self.layouts[self._name] = layout
            self._dirty = True

    def _followed(self, hwnd: int) -> str:
        """The program's name, or "" for a window whose layout is neither kept nor shown."""
        try:
            if win32gui.GetClassName(hwnd) in SHELL:
                return ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return ""
        if pid == self._own_pid or is_fullscreen(hwnd):
            return ""  # our own windows; games and full-screen video are left alone
        return process_name(hwnd).lower()

    @staticmethod
    def _ask(hwnd: int, lang: str, deeper: bool) -> None:
        """Asks the window for the layout; `deeper`, its focused control and its IME window instead."""
        hkl = next((h for h in win32api.GetKeyboardLayoutList() if h & 0xFFFF == LANG_IDS[lang]), 0)
        if not hkl:
            return
        targets = [hwnd]
        if deeper:
            targets = []
            try:
                thread, _ = win32process.GetWindowThreadProcessId(hwnd)
                info = win32gui.GetGUIThreadInfo(thread)
                focus = info["hwndFocus"] if isinstance(info, dict) else info[2]
                if focus and focus != hwnd:
                    targets.append(focus)
            except Exception:
                pass
            ime = imm32.ImmGetDefaultIMEWnd(hwnd)
            if ime:
                targets.append(ime)
        for target in targets:
            try:
                win32api.PostMessage(target, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
            except Exception:
                pass
