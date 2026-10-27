"""System-wide hotkeys through RegisterHotKey, delivered to Qt's event loop."""

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.windll.user32


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, Callable[[], None]] = {}

    def register(self, mods: int, vk: int, callback: Callable[[], None]) -> bool:
        hotkey_id = len(self._callbacks) + 1
        if not user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
            return False
        self._callbacks[hotkey_id] = callback
        return True

    def unregister_all(self) -> None:
        for hotkey_id in self._callbacks:
            user32.UnregisterHotKey(None, hotkey_id)
        self._callbacks.clear()

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._callbacks:
                # Leave the filter at once; do the work on the next loop pass.
                QTimer.singleShot(0, self._callbacks[msg.wParam])
                return True, 0
        return False, 0
