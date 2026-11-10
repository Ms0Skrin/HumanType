"""Thin wrappers over the Win32 calls the app needs: keys, clipboard, windows."""

import ctypes
import time
from ctypes import wintypes

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


# --- the whole clipboard, whatever is on it ---

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.EnumClipboardFormats.argtypes = (wintypes.UINT,)
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.GetClipboardData.argtypes = (wintypes.UINT,)
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
_user32.SetClipboardData.restype = wintypes.HANDLE
_kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalFree.restype = wintypes.HGLOBAL
GMEM_MOVEABLE = 0x0002
# Formats whose handle is not a block of memory (bitmaps, metafiles, palettes; the system makes
# the bitmap again from CF_DIB) or belongs to its owner alone: they cannot be copied and put back.
_NOT_MEMORY = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
# OLE's own bookkeeping: it points at the program that copied, and is wrong once we put the data back.
_OLE_PRIVATE = {"DataObject", "Ole Private Data"}
MAX_SNAPSHOT = 64 * 1024 * 1024  # more than this is not kept: the copy would take too long

Snapshot = list[tuple[int, bytes]]


def _format_name(fmt: int) -> str:
    if fmt < 0xC000:
        return ""  # a standard format: it has no name
    try:
        return win32clipboard.GetClipboardFormatName(fmt)
    except Exception:
        return ""


def snapshot() -> Snapshot | None:
    """Everything on the clipboard, format by format: text with its formatting, pictures, copied
    files. None when the clipboard cannot be opened; what cannot be copied is left out."""
    if not _open_clipboard():
        return None
    saved: Snapshot = []
    total = 0
    try:
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _NOT_MEMORY and not 0x200 <= fmt <= 0x3FF and _format_name(fmt) not in _OLE_PRIVATE:
                handle = _user32.GetClipboardData(fmt)
                size = _kernel32.GlobalSize(handle) if handle else 0
                if size and total + size <= MAX_SNAPSHOT:
                    address = _kernel32.GlobalLock(handle)
                    if address:
                        try:
                            saved.append((fmt, ctypes.string_at(address, size)))
                            total += size
                        finally:
                            _kernel32.GlobalUnlock(handle)
            fmt = _user32.EnumClipboardFormats(fmt)
    finally:
        win32clipboard.CloseClipboard()
    return saved


def restore(saved: Snapshot | None) -> None:
    """Puts back what `snapshot` took. An empty snapshot empties the clipboard: it was empty."""
    if saved is None or not _open_clipboard():
        return
    try:
        win32clipboard.EmptyClipboard()
        for fmt, data in saved:
            handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                continue
            address = _kernel32.GlobalLock(handle)
            if not address:
                _kernel32.GlobalFree(handle)
                continue
            ctypes.memmove(address, data, len(data))
            _kernel32.GlobalUnlock(handle)
            if not _user32.SetClipboardData(fmt, handle):
                _kernel32.GlobalFree(handle)  # the clipboard did not take it: it is still ours
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
