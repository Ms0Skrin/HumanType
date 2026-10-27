"""Automatic layout switching: a system keyboard hook that follows the words being typed.

The hook runs on a thread of its own and only records keys. When a word ends with a space it
asks the `Session` whether the word (and maybe the short ones before it) belongs to the other
layout. If so the space is held back, a worker erases the word, types it right, switches the
window's layout and lets the space through; keys typed meanwhile are held and replayed after.
A Backspace right after a fix undoes it and remembers the word as an exception.
Enter and Tab end a word too: it is fixed before the key goes through, but cannot be undone.
"""

import ctypes
import logging
import queue
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pythoncom
import win32api
import win32con
import win32gui
import win32process
from PySide6.QtCore import QObject, Signal

from .config import DEFAULT_EXCLUDED
from .detector import SENSITIVITY, Detector, Fix, Session
from .layout import LANG_EN, LANG_RU

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0201, 0x0204, 0x0207
WM_QUIT = 0x0012
WM_REHOOK = 0x8001  # WM_APP + 1: the watchdog asks the hook thread to set the hooks again
WM_INPUTLANGCHANGEREQUEST = 0x0050
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MARKER = 0x48545950  # "HTYP": our own injected keys carry it so the hook can skip them

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.GetTickCount.restype = wintypes.DWORD
user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
oleacc = ctypes.WinDLL("oleacc")
oleacc.AccessibleObjectFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p))
OBJID_CLIENT = 0xFFFFFFFC
STATE_SYSTEM_PROTECTED = 0x20000000
IID_IDISPATCH = uuid.UUID("00020400-0000-0000-C000-000000000046").bytes_le
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "browser.exe", "librewolf.exe"}

# US layout: virtual key -> (plain, with Shift)
US_KEYS: dict[int, tuple[str, str]] = {vk: (chr(vk).lower(), chr(vk)) for vk in range(0x41, 0x5B)}
US_KEYS.update({0x30 + i: (str(i), ")!@#$%^&*("[i]) for i in range(10)})
US_KEYS.update(
    {
        0xBA: (";", ":"),
        0xBB: ("=", "+"),
        0xBC: (",", "<"),
        0xBD: ("-", "_"),
        0xBE: (".", ">"),
        0xBF: ("/", "?"),
        0xC0: ("`", "~"),
        0xDB: ("[", "{"),
        0xDC: ("\\", "|"),
        0xDD: ("]", "}"),
        0xDE: ("'", '"'),
    }
)
RU_LETTER_KEYS = {0xBA, 0xBC, 0xBE, 0xC0, 0xDB, 0xDD, 0xDE}  # ж б ю ё х ъ э: Caps Lock applies to them in ЙЦУКЕН
MODIFIERS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
VK_BACK, VK_TAB, VK_RETURN, VK_SPACE = 0x08, 0x09, 0x0D, 0x20
DELIMITERS = {VK_SPACE, VK_RETURN, VK_TAB}  # keys that end a word and get it judged
LANGS = {LANG_EN: "en", LANG_RU: "ru"}
LANG_IDS = {"en": LANG_EN, "ru": LANG_RU}



def _send(inputs: list[INPUT]) -> None:
    if inputs:
        array = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _key(vk: int, up: bool = False) -> INPUT:
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, MARKER)))


def _char(c: str, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, ord(c), flags, 0, MARKER)))


def type_fix(erase: int, text: str, then_vk: int | None) -> None:
    """Backspaces, the new text as Unicode (independent of any layout), then an optional key."""
    inputs: list[INPUT] = []
    for _ in range(erase):
        inputs += [_key(VK_BACK), _key(VK_BACK, up=True)]
    for c in text:
        inputs += [_char(c), _char(c, up=True)]
    if then_vk is not None:
        inputs += [_key(then_vk), _key(then_vk, up=True)]
    _send(inputs)


def _pressed(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def process_name(hwnd: int) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def is_fullscreen(hwnd: int) -> bool:
    """A borderless window covering its whole monitor: games and full-screen apps."""
    try:
        if win32gui.GetClassName(hwnd) in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        if style & win32con.WS_CAPTION == win32con.WS_CAPTION:
            return False
        monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd))["Monitor"]
        return tuple(win32gui.GetWindowRect(hwnd)) == tuple(monitor)
    except Exception:
        return False


def focused_is_password(hwnd: int) -> bool:
    """A native password box has the focus (browsers cannot be asked; their fields are skipped elsewhere)."""
    try:
        thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        info = win32gui.GetGUIThreadInfo(thread)
        focus = info["hwndFocus"] if isinstance(info, dict) else info[2]
        if focus and win32gui.GetClassName(focus).lower().startswith("edit"):
            return bool(win32gui.GetWindowLong(focus, win32con.GWL_STYLE) & 0x20)  # ES_PASSWORD
    except Exception:
        pass
    return False


def _msaa_get(obj, name: str, *args):
    dispid = obj.GetIDsOfNames(name)
    return obj.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYGET, True, *args)


def browser_password(hwnd: int) -> bool:
    """Asks a browser through MSAA whether its focused element is a password field.
    Slow-ish and wakes the browser's accessibility, so it is only asked right before a fix."""
    ptr = ctypes.c_void_p()
    try:
        if oleacc.AccessibleObjectFromWindow(hwnd, OBJID_CLIENT, IID_IDISPATCH, ctypes.byref(ptr)) != 0 or not ptr.value:
            return False
        root = pythoncom.ObjectFromAddress(ptr.value, pythoncom.IID_IDispatch)
    except Exception:
        return False
    finally:
        if ptr.value:  # ObjectFromAddress took its own reference
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)
    try:
        focus = _msaa_get(root, "accFocus")
        if isinstance(focus, int):
            state = _msaa_get(root, "accState", focus)
        elif focus is not None:
            state = _msaa_get(focus, "accState", 0)
        else:
            return False
        return bool(int(state) & STATE_SYSTEM_PROTECTED)
    except Exception:
        return False


def is_elevated(hwnd: int) -> bool:
    """A window of an elevated (admin) process: low-level hooks get no input meant for it."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return True
        try:
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
                return True
            kernel32.CloseHandle(token)
            return False
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _idle_ms(since: int) -> int:
    """Milliseconds from `since` (a tick count) to the last input anywhere in the session."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    diff = (info.dwTime - since) & 0xFFFFFFFF
    return diff if diff < 0x80000000 else 0


@dataclass
class _Done:
    fix: Fix
    old_layout: str


class AutoSwitcher(QObject):
    corrected = Signal(str, str)  # before, after
    undone = Signal(str)  # the word now kept as typed

    def __init__(self, detector: Detector, exceptions_file: Path) -> None:
        super().__init__()
        self.detector = detector
        self.session = Session(detector)
        self.exceptions_file = exceptions_file
        self.enabled = True
        self.skip_fullscreen = True
        self.excluded: set[str] = {name.lower() for name in DEFAULT_EXCLUDED}
        self._own_pid = win32api.GetCurrentProcessId()
        self._lock = threading.RLock()
        self._buffer: list[str] = []  # US-layout spelling of the word being typed
        self._hwnd = 0
        self._window_ok = True
        self._last: _Done | None = None
        self._busy = False
        self._held: list[tuple[int, bool]] = []  # keys typed while a fix was being made
        self._jobs: queue.Queue = queue.Queue()
        self._thread_id = 0
        self._hooks: list[int] = []
        self._procs = (HOOKPROC(self._on_key), HOOKPROC(self._on_mouse))
        self._seen = 0  # tick count of the last event a hook received
        self._stopped = threading.Event()
        self._file_lock = threading.Lock()
        self.load_exceptions()

    # --- settings ---

    def configure(self, enabled: bool, sensitivity: str, short_words: bool, skip_fullscreen: bool, excluded: list[str]) -> None:
        with self._lock:
            self.enabled = enabled
            self.detector.sensitivity = SENSITIVITY.get(sensitivity, SENSITIVITY["normal"])
            self.detector.short_words = short_words
            self.skip_fullscreen = skip_fullscreen
            self.excluded = {name.strip().lower() for name in excluded if name.strip()}
            self._reset()

    def load_exceptions(self) -> None:
        try:
            words = self.exceptions_file.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            words = []
        self.detector.exceptions = {w.lower() for w in words}

    def save_exceptions(self) -> None:
        # Never called under self._lock: a hook waiting on a slow disk would be dropped by Windows.
        with self._file_lock:
            words = sorted(self.detector.exceptions)
            self.exceptions_file.write_text("\n".join(words), encoding="utf-8")

    def add_exception(self, keys: str) -> None:
        self.update_exceptions({keys.lower()}, set())

    def update_exceptions(self, added: set[str], removed: set[str]) -> None:
        self.detector.exceptions = (self.detector.exceptions - removed) | added
        self.save_exceptions()

    # --- life cycle ---

    def start(self) -> None:
        threading.Thread(target=self._hook_thread, name="humantype-hook", daemon=True).start()
        threading.Thread(target=self._worker, name="humantype-typer", daemon=True).start()
        threading.Thread(target=self._watchdog, name="humantype-watchdog", daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._jobs.put(None)

    def reset(self) -> None:
        """Forget the words typed so far (the app changed the text itself, e.g. the manual fixer)."""
        with self._lock:
            self._reset()

    def _reset(self) -> None:
        self._buffer.clear()
        self.session.reset()
        self._last = None

    def _set_hooks(self) -> None:
        for hook in self._hooks:
            if hook:
                user32.UnhookWindowsHookEx(hook)
        self._hooks = []
        module = kernel32.GetModuleHandleW(None)
        for kind, proc in ((WH_KEYBOARD_LL, self._procs[0]), (WH_MOUSE_LL, self._procs[1])):
            hook = user32.SetWindowsHookExW(kind, proc, module, 0)
            if not hook:
                log.error("hook %s failed: %s", kind, ctypes.get_last_error())
            self._hooks.append(hook)
        self._seen = kernel32.GetTickCount()

    def _hook_thread(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._set_hooks()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_REHOOK and not msg.hWnd:
                self._set_hooks()
                with self._lock:
                    self._reset()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for hook in self._hooks:
            if hook:
                user32.UnhookWindowsHookEx(hook)

    def _watchdog(self) -> None:
        """Windows silently removes a low-level hook that once answered too slowly (a busy GIL is
        enough). Input the session saw but our hooks did not means they are gone: set them again."""
        while not self._stopped.wait(5):
            if not self._thread_id or _idle_ms(self._seen) < 3000:
                continue
            hwnd = user32.GetForegroundWindow() or 0
            if not hwnd or is_elevated(hwnd):
                continue  # the lock screen or an admin window: hooks get nothing there anyway
            log.warning("keyboard hook went silent, setting it again")
            self._seen = kernel32.GetTickCount()
            user32.PostThreadMessageW(self._thread_id, WM_REHOOK, 0, 0)

    # --- the hooks (keep them quick: Windows drops a hook that stalls) ---

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        self._seen = kernel32.GetTickCount()
        if code >= 0 and wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            with self._lock:
                self._reset()  # a click may move the caret anywhere
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        self._seen = kernel32.GetTickCount()
        try:
            if code >= 0 and self._handle(wparam, ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents):
                return 1  # swallowed
        except Exception:
            log.exception("keyboard hook")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle(self, wparam: int, kb: KBDLLHOOKSTRUCT) -> bool:
        """Returns True to swallow the key."""
        if kb.dwExtraInfo == MARKER or wparam not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return False
        vk = kb.vkCode
        if vk in MODIFIERS:
            return False
        with self._lock:
            if self._busy:  # a fix is being typed: hold this key and replay it after
                self._held.append((vk, _pressed(0x10)))
                return True
            if not self.enabled:
                return False
            hwnd = user32.GetForegroundWindow() or 0
            if hwnd != self._hwnd:
                self._hwnd = hwnd
                self._reset()
                self._window_ok = self._window_allowed(hwnd)
            if not self._window_ok or _pressed(0x11) or _pressed(0x12) or _pressed(0x5B) or _pressed(0x5C):
                self._reset()  # shortcuts, excluded apps
                return False

            if vk == VK_BACK:
                if self._last is not None and not self._buffer:
                    done, self._last = self._last, None
                    self._busy = True
                    self._jobs.put(("undo", done, hwnd))
                    return True
                if self._buffer:
                    self._buffer.pop()
                else:
                    self.session.reset()  # editing the previous word: the run is broken
                return False

            layout = self._layout(hwnd)
            if layout is None:
                self._reset()
                return False

            if vk in DELIMITERS:
                keys = "".join(self._buffer)
                self._buffer.clear()
                self._last = None
                if not keys:
                    # A second space or an empty line: nothing to judge, and the words before no
                    # longer sit one space apart, so they cannot be fixed together with the next.
                    self.session.reset()
                    return False
                if focused_is_password(hwnd):
                    self.session.reset()
                    return False
                fix = self.session.word(keys, layout)
                if vk != VK_SPACE:
                    self.session.reset()  # a new line or another field: the next words start afresh
                if fix is None:
                    return False
                self._busy = True
                self._jobs.put(("fix", fix, hwnd, layout, vk))
                return True

            char = self._char(vk, layout)
            if char is None:
                self._reset()  # arrows, Enter, Tab, F-keys, numpad...: the caret or the text moved on
                return False
            if len(self._buffer) < 48:
                self._buffer.append(char)
            self._last = None
            return False

    # --- helpers for the hook ---

    def _window_allowed(self, hwnd: int) -> bool:
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return False
        if pid == self._own_pid:
            return False
        if process_name(hwnd).lower() in self.excluded:
            return False
        return not (self.skip_fullscreen and is_fullscreen(hwnd))

    @staticmethod
    def _layout(hwnd: int) -> str | None:
        thread = user32.GetWindowThreadProcessId(hwnd, None)
        hkl = user32.GetKeyboardLayout(thread) or 0
        return LANGS.get(hkl & 0xFFFF)

    @staticmethod
    def _char(vk: int, layout: str, shift: bool | None = None) -> str | None:
        pair = US_KEYS.get(vk)
        if pair is None:
            return None
        if shift is None:
            shift = _pressed(0x10)
        caps = bool(user32.GetKeyState(0x14) & 1)
        is_letter = 0x41 <= vk <= 0x5A or (layout == "ru" and vk in RU_LETTER_KEYS)
        return pair[1] if shift != (caps and is_letter) else pair[0]

    # --- the worker: types fixes outside the hook ---

    def _worker(self) -> None:
        pythoncom.CoInitialize()  # for asking browsers about password fields
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                if job[0] == "fix":
                    self._apply(*job[1:])
                    # A Backspace pressed while the fix was being typed is the same "no" as one after it.
                    if self._take_held_backspace():
                        with self._lock:
                            done, self._last = self._last, None
                        if done is not None:
                            self._undo(done, job[2])
                else:
                    self._undo(*job[1:])
            except Exception:
                log.exception("autoswitch worker")
            finally:
                self._release_held()

    def _apply(self, fix: Fix, hwnd: int, layout: str, delimiter: int = VK_SPACE) -> None:
        if process_name(hwnd).lower() in BROWSERS and browser_password(hwnd):
            type_fix(0, "", delimiter)  # a password field: leave the text alone
            with self._lock:
                self.session.reset()
            return
        type_fix(fix.erase, fix.text, delimiter)
        self._switch(hwnd, fix.layout)
        with self._lock:
            # After Enter or Tab the text may already be sent or left behind: no undo then.
            self._last = _Done(fix, layout) if delimiter == VK_SPACE else None
        self.corrected.emit(fix.original, fix.text)

    def _undo(self, done: _Done, hwnd: int) -> None:
        """Takes the fix and its space back, returns the old layout and remembers the words."""
        fix = done.fix
        type_fix(len(fix.text) + 1, fix.original, None)
        self._switch(hwnd, done.old_layout)
        with self._lock:
            self.session.reset()
            self._buffer = list(fix.keys[-1])  # the caret is back at the end of that word
        self.update_exceptions({keys.lower() for keys in fix.keys}, set())
        self.undone.emit(fix.original)

    def _switch(self, hwnd: int, lang: str) -> None:
        """Asks the window for another layout and waits until it takes it, so that the keys held
        meanwhile are replayed in the new layout, not the old one."""
        hkl = self._hkl(lang)
        if not hkl:
            return
        win32api.PostMessage(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
        end = time.monotonic() + 0.2
        while self._layout(hwnd) != lang and time.monotonic() < end:
            time.sleep(0.005)

    def _take_held_backspace(self) -> bool:
        with self._lock:
            if self._held and self._held[0][0] == VK_BACK:
                del self._held[0]
                return True
            return False

    def _release_held(self) -> None:
        with self._lock:
            held, self._held = self._held, []
            self._busy = False
        if not held:
            return
        time.sleep(0.01)
        inputs: list[INPUT] = []
        for vk, shift in held:
            if shift and not _pressed(0x10):
                inputs += [_key(0x10), _key(vk), _key(vk, up=True), _key(0x10, up=True)]
            else:
                inputs += [_key(vk), _key(vk, up=True)]
            with self._lock:
                # Replayed keys carry MARKER and skip the hook: keep the word buffer in step here.
                layout = self._layout(self._hwnd) or "en"
                char = self._char(vk, layout, shift) if vk not in DELIMITERS else None
                if vk == VK_BACK:
                    if self._buffer:
                        self._buffer.pop()
                    else:
                        self.session.reset()
                elif char is None:
                    self._buffer.clear()
                    self.session.reset()
                else:
                    self._buffer.append(char)
        _send(inputs)

    @staticmethod
    def _hkl(lang: str) -> int:
        for hkl in win32api.GetKeyboardLayoutList():
            if hkl & 0xFFFF == LANG_IDS[lang]:
                return hkl
        return 0
