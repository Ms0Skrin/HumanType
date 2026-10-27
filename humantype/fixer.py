"""Fixing the wrong layout in whatever window has focus: copy, convert, paste back."""

import logging
import time
from typing import Callable

import win32con
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication

from . import winapi
from .history import History
from .layout import LANG_RU, convert

log = logging.getLogger(__name__)


class LayoutFixer(QObject):
    def __init__(self, history: History, notify: Callable[[str], None]) -> None:
        super().__init__()
        self.history = history
        self.notify = notify
        self.switch_layout = True
        self._busy = False

    def fix(self, whole_line: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        hwnd = winapi.foreground()
        self.history.suppress(True)
        saved_text = winapi.get_text()
        saved_image = None if saved_text is not None else QGuiApplication.clipboard().image()
        try:
            winapi.release_modifiers()
            text = self._copy_selection()
            if not text:
                # Nothing selected: take the last word, or with Shift the line up to the cursor.
                if whole_line:
                    winapi.combo(win32con.VK_SHIFT, win32con.VK_HOME)
                else:
                    winapi.combo(win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_LEFT)
                text = self._copy_selection()
            if not text:
                self.notify("Нечего исправлять")
                self._finish(saved_text, saved_image)
                return
            fixed, lang = convert(text)
            winapi.set_text(fixed)
            time.sleep(0.03)
            winapi.combo(win32con.VK_CONTROL, winapi.VK_V)
            if self.switch_layout:
                winapi.switch_layout(hwnd, lang)
            self.notify(f"✓ {'РУ' if lang == LANG_RU else 'EN'}: {fixed.strip()[:40]}")
            # Give the window time to paste before the old clipboard comes back.
            QTimer.singleShot(400, lambda: self._finish(saved_text, saved_image))
        except Exception:
            log.exception("layout fix failed")
            self.notify("Не получилось исправить")
            self._finish(saved_text, saved_image)

    def _copy_selection(self) -> str:
        seq = winapi.clipboard_seq()
        winapi.combo(win32con.VK_CONTROL, winapi.VK_C)
        end = time.monotonic() + 0.4
        while winapi.clipboard_seq() == seq and time.monotonic() < end:
            time.sleep(0.01)
        if winapi.clipboard_seq() == seq:
            return ""
        text = winapi.get_text() or ""
        # VS Code and some editors copy the whole line when nothing is selected.
        if text.endswith("\n") and text.count("\n") == 1:
            return ""
        return text

    def _finish(self, saved_text: str | None, saved_image) -> None:
        try:
            if saved_text is not None:
                winapi.set_text(saved_text)
            elif saved_image is not None and not saved_image.isNull():
                QGuiApplication.clipboard().setImage(saved_image)
        finally:
            # The restore itself raises a clipboard change: stay deaf a moment longer.
            QTimer.singleShot(300, lambda: self.history.suppress(False))
            self._busy = False
