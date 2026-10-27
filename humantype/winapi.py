"""Thin wrappers over the Win32 calls the app needs: keys, clipboard, windows."""

import time

import win32api
import win32clipboard
import win32con
import win32gui

VK_C = ord("C")
VK_V = ord("V")
MODIFIERS = (
    win32con.VK_SHIFT,
    win32con.VK_CONTROL,
    win32con.VK_MENU,
    win32con.VK_LWIN,
    win32con.VK_RWIN,
)
EXTENDED = {win32con.VK_LEFT, win32con.VK_RIGHT, win32con.VK_HOME, win32con.VK_END, win32con.VK_DELETE}
WM_INPUTLANGCHANGEREQUEST = 0x0050


def _pressed(vk: int) -> bool:
    return bool(win32api.GetAsyncKeyState(vk) & 0x8000)


def _key(vk: int, up: bool = False) -> None:
    flags = win32con.KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED:
        flags |= win32con.KEYEVENTF_EXTENDEDKEY
    win32api.keybd_event(vk, 0, flags, 0)


def release_modifiers(timeout: float = 0.8) -> None:
    """Waits for the person to let go of Ctrl/Shift/Alt/Win, then lifts any still down,
    so the keys we send are not mixed with the hotkey's own."""
    end = time.monotonic() + timeout
    while any(_pressed(vk) for vk in MODIFIERS) and time.monotonic() < end:
        time.sleep(0.01)
    for vk in MODIFIERS:
        if _pressed(vk):
            _key(vk, up=True)


def combo(*vks: int) -> None:
    for vk in vks:
        _key(vk)
    for vk in reversed(vks):
        _key(vk, up=True)
    time.sleep(0.02)


def clipboard_seq() -> int:
    return win32clipboard.GetClipboardSequenceNumber()


def _open_clipboard() -> bool:
    for _ in range(20):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.02)
    return False


def get_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return None
    finally:
        win32clipboard.CloseClipboard()


def set_text(text: str) -> None:
    if not _open_clipboard():
        return
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def foreground() -> int:
    return win32gui.GetForegroundWindow()


def focus(hwnd: int) -> None:
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def switch_layout(hwnd: int, lang_id: int) -> None:
    """Switches the window's keyboard layout, if that language is installed."""
    for hkl in win32api.GetKeyboardLayoutList():
        if hkl & 0xFFFF == lang_id:
            win32api.PostMessage(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
            return
