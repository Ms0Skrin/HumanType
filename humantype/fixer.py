"""Fixing the wrong layout in whatever window has focus: copy, convert, paste back."""

import logging
import time
from typing import Callable

import win32con
from PySide6.QtCore import QObject, QTimer

from . import winapi
from .history import History
from .layout import LANG_IDS, NOTE_LABELS, convert

LANG_NAMES = {LANG_IDS[lang]: label for lang, label in NOTE_LABELS.items()}
# What a fix does to the text: the new text and the layout it now belongs to (None: leave the layout).
Transform = Callable[[str], tuple[str, int | None]]

log = logging.getLogger(__name__)


class LayoutFixer(QObject):
    def __init__(self, history: History, notify: Callable[[str], None]) -> None:
        super().__init__()
        self.history = history
        self.notify = notify
        self.switch_layout = True
        self.on_layout_fixed: Callable[[], None] | None = None  # told of every layout fix made, for the statistics
        self._busy = False

    def fix(self, whole_line: bool = False, transform: Transform = convert, mark: str = "") -> None:
        """Copies the selection (or the last word, or the line up to the caret), changes it with
        `transform` and pastes it back. The clipboard is put back as it was, whatever was on it."""
        if self._busy:
            return
        self._busy = True
        hwnd = winapi.foreground()
        self.history.suppress(True)
        saved = winapi.snapshot()
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
                self._finish(saved)
                return
            fixed, lang = transform(text)
            if fixed == text:
                self.notify("Нечего менять")
                self._finish(saved)
                return
            winapi.set_text(fixed)
            time.sleep(0.03)
            winapi.combo(win32con.VK_CONTROL, winapi.VK_V)
            if self.switch_layout and lang is not None:
                winapi.switch_layout(hwnd, lang)
            self.notify(f"✓ {mark or LANG_NAMES.get(lang, '')}: {fixed.strip()[:40]}")
            if transform is convert and self.on_layout_fixed:
                self.on_layout_fixed()
            # Give the window time to paste before the old clipboard comes back.
            QTimer.singleShot(400, lambda: self._finish(saved))
        except Exception:
            log.exception("layout fix failed")
            self.notify("Не получилось исправить")
            self._finish(saved)

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

    def _finish(self, saved: winapi.Snapshot | None) -> None:
        try:
            winapi.restore(saved)
        except Exception:
            log.exception("the clipboard was not put back")
        finally:
            # The restore itself raises a clipboard change: stay deaf a moment longer.
            QTimer.singleShot(300, lambda: self.history.suppress(False))
            self._busy = False
