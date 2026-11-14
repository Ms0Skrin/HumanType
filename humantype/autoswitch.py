"""Automatic layout switching: a system keyboard hook that follows the words being typed.

The hook runs on a thread of its own and only records keys: the whole line typed since the caret
last jumped, each key with the layout it was pressed in. When a word ends with a space it asks
the `Session` whether the word (and maybe the short ones before it) belongs to the other layout.
If so the space is held back, a worker erases the word, types it right, switches the window's
layout and lets the space through; keys typed meanwhile are held and replayed after.
A word that clearly belongs to the other layout is fixed without waiting for its end, at its fifth letter.
A Backspace right after a fix undoes it and remembers the word as an exception.
Enter and Tab end a word too: it is fixed before the key goes through, but cannot be undone.
In a terminal, y/yes/n/no typed in the Russian layout (н, нуы, т, тщ) are fixed on Enter.
The fix-layout hotkey flips the line it follows by typing it again, without the clipboard.
"""

import ctypes
import logging
import queue
import threading
import time
import uuid
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path

import pythoncom
import win32api
import win32con
import win32gui
import win32process
from PySide6.QtCore import QObject, Signal

from . import elevation, uia
from .config import DEFAULT_EXCLUDED, TERMINAL_COMMANDS, TERMINALS
from .detector import MID_LEN, SENSITIVITY, Detector, Fix, Session
from . import layout as layouts
from .layout import ID_LANGS, LANG_IDS, text_of

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0201, 0x0204, 0x0207
WM_QUIT = 0x0012
WM_REHOOK = 0x8001  # WM_APP + 1: the watchdog asks the hook thread to set the hooks again
WM_INPUTLANGCHANGEREQUEST = 0x0050
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MARKER = 0x48545950  # "HTYP": our own injected keys carry it so the hook can skip them
PROBE_VK = 0xE8  # a key no keyboard has: its release, sent by the watchdog, shows the hook is alive

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
imm32 = ctypes.WinDLL("imm32")
imm32.ImmGetDefaultIMEWnd.argtypes = (wintypes.HWND,)
imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
kernel32.GetCurrentThread.restype = wintypes.HANDLE
kernel32.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
THREAD_PRIORITY_TIME_CRITICAL = 15
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
# The Ukrainian layout: ж б ю х ї є; the key of ё gives an apostrophe there, which has no case.
EXTRA_LETTER_KEYS = {"ru": RU_LETTER_KEYS, "uk": RU_LETTER_KEYS - {0xC0}}
MODIFIERS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
VK_BACK, VK_TAB, VK_RETURN, VK_SPACE = 0x08, 0x09, 0x0D, 0x20
DELIMITERS = {VK_SPACE, VK_RETURN, VK_TAB}  # keys that end a word and get it judged
LANGS = ID_LANGS  # Windows language identifier -> "en" | "ru" | "uk"
MOD_KEYS = ((0x12, 0x0001), (0x11, 0x0002), (0x10, 0x0004), (0x5B, 0x0008), (0x5C, 0x0008))  # vk, MOD_* of RegisterHotKey
HELD_MODS = (0x11, 0x12, 0x5B, 0x5C)  # Ctrl, Alt, Win: a key held with them is a shortcut, not text
MAX_LINE = 300
# A terminal asking [y/N]: these answers typed in the Russian layout (н, нуы, т, тщ) are fixed on Enter.
ANSWERS = {"y", "yes", "n", "no"}
WORD_EDGE = set(" \t\n([{<«\"'„“/")  # what may stand right before a word begins
COMMANDS = {name.lower() for name in TERMINAL_COMMANDS}

# Why a word was left as typed, for the journal ("Почему не исправилось?").
REASONS = {
    "exception": "в словах-исключениях (его исправление когда-то отменили)",
    "digits": "в слове есть цифры",
    "address": "похоже на адрес сайта",
    "other reading is not a word": "в другой раскладке это не слово",
    "mixed case": "смешанный регистр (iPhone, GitHub)",
    "abbreviation": "похоже на аббревиатуру",
    "short words off": "короткие слова выключены в настройках",
    "short word, unsure": "короткое слово, в другой раскладке редкое",
    "short word, waiting for context": "короткое слово: решится по следующему",
    "neither reading is a word": "ни в одной раскладке не слово",
    "both are words": "слово есть в обеих раскладках",
    "same text in both layouts": "в русской и украинской раскладке это одно и то же",
    "other reading is a rare word": "в другой раскладке слишком редкое слово",
    "current reading is a known word": "в этой раскладке это настоящее слово",
    "unsure": "не уверен: буквы похожи на оба языка",
}


@dataclass(frozen=True)
class Press:
    vk: int
    shift: bool
    caps: bool
    layout: str  # "en" | "ru" | "uk": the layout the key was pressed in


def char_of(p: Press, layout: str) -> str:
    """How the US layout spells this key in `layout`: Caps Lock reaches ж б ю ё х ъ э only in ЙЦУКЕН."""
    if p.vk == VK_SPACE:
        return " "
    pair = US_KEYS[p.vk]
    is_letter = 0x41 <= p.vk <= 0x5A or p.vk in EXTRA_LETTER_KEYS.get(layout, ())
    return pair[1] if p.shift != (p.caps and is_letter) else pair[0]


def render(presses: list[Press], layout: str) -> str:
    """The text these keys give in `layout`."""
    return text_of("".join(char_of(p, layout) for p in presses), layout)



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


_WE_ARE_ADMIN: bool | None = None


def is_elevated(hwnd: int) -> bool:
    """A window of an elevated (admin) process that we, not elevated, cannot reach: low-level
    hooks get no input meant for it and SendInput into it is blocked."""
    global _WE_ARE_ADMIN
    if _WE_ARE_ADMIN is None:
        _WE_ARE_ADMIN = elevation.is_admin()
    if _WE_ARE_ADMIN:
        return False
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return elevation.process_elevated(pid)
    except Exception:
        return False


def layout_thread(hwnd: int) -> int:
    """The thread whose keyboard layout is the window's. A console window (cmd, PowerShell in
    conhost) claims the thread of the shell, which has no layout at all; its IME window belongs
    to the console host's thread, which has the real one."""
    thread = user32.GetWindowThreadProcessId(hwnd, None)
    if LANGS.get((user32.GetKeyboardLayout(thread) or 0) & 0xFFFF):
        return thread
    ime = imm32.ImmGetDefaultIMEWnd(hwnd)
    if ime:
        ime_thread = user32.GetWindowThreadProcessId(ime, None)
        if ime_thread and LANGS.get((user32.GetKeyboardLayout(ime_thread) or 0) & 0xFFFF):
            return ime_thread
    return thread


def thread_layout(thread: int) -> str | None:
    return LANGS.get((user32.GetKeyboardLayout(thread) or 0) & 0xFFFF)


def _idle_ms(since: int) -> int:
    """Milliseconds from `since` (a tick count) to the last input anywhere in the session."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    diff = (info.dwTime - since) & 0xFFFFFFFF
    return diff if diff < 0x80000000 else 0


def _caps() -> bool:
    return bool(user32.GetKeyState(0x14) & 1)


def _mods() -> int:
    """The modifiers held now, as RegisterHotKey spells them."""
    mods = 0
    for vk, flag in MOD_KEYS:
        if _pressed(vk):
            mods |= flag
    return mods


def _let_go_modifiers(timeout: float = 0.8) -> None:
    """Waits for the hotkey's Ctrl/Shift/Alt/Win to be let go, then lifts any still down,
    so the keys we send are not mixed with the hotkey's own."""
    end = time.monotonic() + timeout
    while any(_pressed(vk) for vk, _ in MOD_KEYS) and time.monotonic() < end:
        time.sleep(0.01)
    _send([_key(vk, up=True) for vk, _ in MOD_KEYS if _pressed(vk)])


@dataclass(frozen=True)
class Note:
    """One line of the journal: what happened to a word, or why a window is not followed."""
    when: float
    word: str
    why: str
    fixed: bool = False


@dataclass
class _Done:
    fix: Fix
    old_layout: str
    run: list[Press]  # the keys as they were first typed
    mid: bool = False  # fixed in the middle of the word: no space after it


class AutoSwitcher(QObject):
    corrected = Signal(str, str)  # before, after
    undone = Signal(str, bool)  # the word now kept as typed; whether for good
    flipped = Signal(str, str)  # the text now typed, its layout
    lost_track = Signal(bool)  # a flip found other text before the caret: the clipboard fixer takes it, whole line or not

    def __init__(self, detector: Detector, exceptions_file: Path, learned_file: Path | None = None) -> None:
        super().__init__()
        self.detector = detector
        self.session = Session(detector)
        self.exceptions_file = exceptions_file
        self.learned_file = learned_file
        # Words undone once (kept until restart); a second undo, after a restart, keeps them for good.
        self.struck_file = exceptions_file.with_name("autoswitch_undone.txt")
        self._struck: set[str] = set()
        self.journal: deque[Note] = deque(maxlen=60)  # in memory only: it holds what was typed
        self.enabled = True
        self.skip_fullscreen = True
        self.terminal_answers = True  # y/yes/n/no typed in Russian in a terminal
        self.mid_word = True  # fix a word at its fifth letter, without waiting for the space
        self.ukrainian = True  # Ukrainian too, when its layout is installed
        self.excluded: set[str] = {name.lower() for name in DEFAULT_EXCLUDED}
        self.flip_hotkeys: set[tuple[int, int]] = set()  # (MOD_*, vk) of the hotkeys that flip the line
        self._own_pid = win32api.GetCurrentProcessId()
        self._lock = threading.RLock()
        self._line: list[Press] = []  # what was typed since the caret last jumped, spaces included
        # What came before line[0]: "edge" a word boundary for sure (Enter, a new window);
        # "click" most likely one; "unknown" maybe the middle of a word (arrows, a repeat, a shortcut).
        self._origin = "edge"
        self._down: set[int] = set()  # keys held, to tell the system's autorepeat from a new press
        self._hwnd = 0
        self._kbd_thread = 0  # the thread whose layout is the window's (see layout_thread)
        self._window = "ok"  # "ok"; "answers": only terminal answers are fixed; "off": not followed
        self._terminal = False
        self._last: _Done | None = None
        self._mid_old: str | None = None  # the word being typed was fixed in its middle: the layout it began in
        self._keep = False  # ... and that fix was undone: leave the word alone when it ends
        self._busy = False
        # Keys typed while a fix was being made: vk, Shift, and the Ctrl/Alt/Win held with it.
        self._held: list[tuple[int, bool, tuple[int, ...]]] = []
        self._jobs: queue.Queue = queue.Queue()
        self._thread_id = 0
        self._hooks: list[int] = []
        self._procs = (HOOKPROC(self._on_key), HOOKPROC(self._on_mouse))
        self._seen = 0  # tick count of the last event a hook received
        self._probe = threading.Event()  # the hook saw the watchdog's probe key
        self._stopped = threading.Event()
        self._file_lock = threading.Lock()
        self._window_why = ""  # why the focused window is not followed
        self._loading = False  # a word list is being read (see _set_langs)
        self.load_exceptions()
        self.load_learned()
        try:
            self._struck = set(self.struck_file.read_text(encoding="utf-8").split())
        except (FileNotFoundError, OSError):
            self._struck = set()

    # --- settings ---

    def configure(self, enabled: bool, sensitivity: str, short_words: bool, skip_fullscreen: bool, excluded: list[str],
                  terminal_answers: bool = True, mid_word: bool = True, ukrainian: bool = True) -> None:
        with self._lock:
            self.mid_word = mid_word
            self.ukrainian = ukrainian
            self.enabled = enabled
            self.detector.sensitivity = SENSITIVITY.get(sensitivity, SENSITIVITY["normal"])
            self.detector.short_words = short_words
            self.skip_fullscreen = skip_fullscreen
            self.excluded = {name.strip().lower() for name in excluded if name.strip()}
            self.terminal_answers = terminal_answers
            self._hwnd = 0  # judge the window again by the new settings
            self._reset()
            self._set_langs()

    def _set_langs(self, wait: bool = True) -> None:
        """Russian and English always, as ever; Ukrainian when its layout is installed and the
        setting is on. With a Ukrainian layout and no Russian one, a word that reads the same in
        both goes to the Ukrainian. wait=False (from the hook, which must answer at once): a word
        list not yet read is read on a thread of its own and comes into use when it is there."""
        langs = ["ru", "en"]
        if self.ukrainian and self.detector.available("uk") and self._hkl("uk"):
            langs = ["ru", "en", "uk"] if self._hkl("ru") else ["uk", "en", "ru"]
        if tuple(langs) == self.detector.langs:
            return
        if not wait and any(lang not in self.detector.freq for lang in langs):
            if not self._loading:
                self._loading = True
                threading.Thread(target=self._load_langs, args=(langs,), name="humantype-words", daemon=True).start()
            return
        self.detector.set_langs(langs)
        log.info("languages: %s", ", ".join(self.detector.langs))
        self._reset()

    def _load_langs(self, langs: list[str]) -> None:
        try:
            for lang in langs:
                if lang not in self.detector.freq:
                    self.detector._load(lang)
            with self._lock:
                self._set_langs()
        except Exception:
            log.exception("word lists")
        finally:
            self._loading = False

    def set_flip_hotkeys(self, hotkeys: list[tuple[int, int]]) -> None:
        with self._lock:
            self.flip_hotkeys = set(hotkeys)

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
        if added & self.detector.learned.keys():  # undone: what was learned was wrong
            self.detector.learned = {k: v for k, v in self.detector.learned.items() if k not in added}
            self.save_learned()

    def load_learned(self) -> None:
        learned: dict[str, str] = {}
        if self.learned_file:
            try:
                for line in self.learned_file.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] in LANG_IDS:
                        learned[parts[0].lower()] = parts[1]
            except FileNotFoundError:
                pass
        self.detector.learned = learned

    def save_learned(self) -> None:
        if not self.learned_file:
            return
        with self._file_lock:
            lines = [f"{keys} {lang}" for keys, lang in sorted(self.detector.learned.items())]
            self.learned_file.write_text("\n".join(lines), encoding="utf-8")

    def set_learned(self, learned: dict[str, str]) -> None:
        self.detector.learned = dict(learned)
        self.save_learned()

    def learn(self, keys: str, lang: str) -> None:
        """The person flipped this word by hand: from now on it is switched by itself."""
        keys = keys.lower()
        if not keys or self.detector.learned.get(keys) == lang:
            return
        self.detector.learned = {**self.detector.learned, keys: lang}
        self.save_learned()
        self.detector.soft_exceptions = self.detector.soft_exceptions - {keys}
        self._struck.discard(keys)
        if keys in self.detector.exceptions:
            self.detector.exceptions = self.detector.exceptions - {keys}
            self.save_exceptions()
        log.info("learned a word to switch to %s", lang)

    @property
    def fixing(self) -> bool:
        """A fix is being typed right now (it may be switching the layout)."""
        return self._busy

    def note(self, word: str, why: str, fixed: bool = False) -> None:
        self.journal.append(Note(time.time(), word, why, fixed))

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

    def _reset(self, origin: str = "unknown") -> None:
        """The caret moved or the text changed behind our back: forget what was typed."""
        self._line.clear()
        self.session.reset()
        self._last = None
        self._mid_old = None
        self._keep = False
        self._origin = origin

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
        # Windows drops a hook that answers late; never let the hook wait behind other work.
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL)
        self._set_hooks()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_REHOOK and not msg.hWnd:
                self._set_hooks()
                with self._lock:
                    self._reset()
                    self._down.clear()
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
            # Input the hooks did not see is not always a dead hook (some of it never reaches
            # low-level hooks at all), and setting them again forgets the word being typed.
            # Ask the hook itself first.
            if self._hook_alive():
                self._seen = kernel32.GetTickCount()
                continue
            log.warning("keyboard hook went silent, setting it again")
            self._seen = kernel32.GetTickCount()
            user32.PostThreadMessageW(self._thread_id, WM_REHOOK, 0, 0)

    def _hook_alive(self) -> bool:
        """Sends the release of a key that does not exist, marked as ours: a live hook sees it at once."""
        self._probe.clear()
        _send([_key(PROBE_VK, up=True)])
        return self._probe.wait(0.5)

    # --- the hooks (keep them quick: Windows drops a hook that stalls) ---

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        self._seen = kernel32.GetTickCount()
        if code >= 0 and wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            with self._lock:
                self._reset("click")  # a click may move the caret anywhere
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
        if kb.dwExtraInfo == MARKER:
            if kb.vkCode == PROBE_VK:
                self._probe.set()
            return False
        vk = kb.vkCode
        if wparam in (WM_KEYUP, WM_SYSKEYUP):
            self._down.discard(vk)
            return False
        if wparam not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return False
        # A key still down that is pressed again is the system's autorepeat. The hook runs before
        # the key state changes, so a key whose release went missing reads as up here and is dropped.
        self._down = {k for k in self._down if _pressed(k)}
        repeat = vk in self._down
        self._down.add(vk)
        if vk in MODIFIERS:
            return False
        with self._lock:
            if self._busy:  # a fix is being typed: hold this key and replay it after
                self._held.append((vk, _pressed(0x10), tuple(m for m in HELD_MODS if _pressed(m))))
                return True
            hwnd = user32.GetForegroundWindow() or 0
            if hwnd != self._hwnd:
                self._hwnd = hwnd
                self._set_langs(wait=False)  # a layout may have been added or removed meanwhile
                self._reset("edge")
                self._window = self._window_kind(hwnd)
                self._kbd_thread = layout_thread(hwnd)
                if self._window != "ok" and self._window_why:
                    self.note("", self._window_why)
            if self._window == "off":
                self._reset()
                return False
            if (_mods(), vk) in self.flip_hotkeys:
                return False  # the hotkey flips the line we follow: keep it
            if repeat:
                self._reset()  # the system repeats the key by itself: we cannot count the characters
                return False
            if _pressed(0x11) or _pressed(0x12) or _pressed(0x5B) or _pressed(0x5C):
                self._reset()  # a shortcut: copy, paste, undo... the text may be anything now
                return False
            if vk == VK_BACK:
                return self._backspace(hwnd)
            layout = self._fast_layout(hwnd)
            if layout not in self.detector.langs:
                layout = None  # Ukrainian while it is switched off: as any layout we do not follow
            if layout is None:
                if vk in DELIMITERS and self._line:
                    self.note("", "раскладка окна не из тех, за которыми слежу (русская, английская, украинская)")
                self._reset()
                return False
            if layout in layouts.CYRILLIC:
                layouts.cyrillic = layout  # where the fix-layout hotkey sends Latin text
            if vk in DELIMITERS:
                return self._delimiter(vk, hwnd, layout)
            if vk not in US_KEYS:
                self._reset()  # arrows, Home, Delete, F-keys, numpad...: the caret or the text moved on
                return False
            self._last = None
            self._append(Press(vk, _pressed(0x10), _caps(), layout))
            return self._mid_word(hwnd, layout)

    # --- the line ---

    def _append(self, press: Press) -> None:
        self._line.append(press)
        if len(self._line) > MAX_LINE:
            del self._line[0]

    def _word_start(self) -> int:
        for i in range(len(self._line) - 1, -1, -1):
            if self._line[i].vk == VK_SPACE:
                return i + 1
        return 0

    def _run_start(self, words: int) -> int:
        """Where the last `words` words of the line begin; spaces after the last word belong to the run."""
        i = len(self._line)
        while i and self._line[i - 1].vk == VK_SPACE:
            i -= 1
        for n in range(words):
            while i and self._line[i - 1].vk != VK_SPACE:
                i -= 1
            if n < words - 1:
                if i == 0:
                    break
                i -= 1  # the space between two words
        return i

    def _mid_word(self, hwnd: int, layout: str) -> bool:
        """The key just added made the word MID_LEN letters long: if it clearly belongs to the other
        layout, the key is held back and the worker types the word so far again. True to swallow it."""
        if not (self.enabled and self.mid_word and self._window == "ok") or self._keep:
            return False
        word = self._line[self._word_start():]
        if len(word) != MID_LEN or any(p.layout != layout for p in word):
            return False
        fix = self.session.start("".join(char_of(p, layout) for p in word), layout)
        if fix is None:
            return False
        start = self._run_start(len(fix.keys))
        run = self._line[start:]
        if len(run) != fix.erase or render(run, layout) != fix.original or focused_is_password(hwnd):
            self.session.reset()
            return False
        origin = self._origin if start == 0 else "edge"
        self._line[start:] = [replace(p, layout=fix.layout) for p in run]
        self._busy = True
        self._jobs.put(("mid", fix, hwnd, layout, run, origin))
        return True

    def _finish(self, vk: int) -> None:
        self._last = None
        self._mid_old = None
        self._keep = False
        if vk == VK_SPACE:
            self._append(Press(VK_SPACE, False, False, "en"))
        else:
            self._reset("edge")  # Enter or Tab: a new line, a sent message or another field

    def _backspace(self, hwnd: int) -> bool:
        done, self._last = self._last, None
        if done is not None and self._line and (done.mid or self._line[-1].vk == VK_SPACE):
            self._busy = True
            self._jobs.put(("undo", done, hwnd))
            return True
        if self._line:
            if self._line.pop().vk == VK_SPACE:
                self.session.reset()  # editing the word before: the run is broken
            self._erased()
        else:
            self._reset()  # erasing what came before the line: maybe into the middle of a word
        return False

    def _erased(self) -> None:
        """A character was erased: a word cut below MID_LEN letters is no longer the one fixed in its middle."""
        if len(self._line) - self._word_start() < MID_LEN:
            self._mid_old = None
            self._keep = False

    def _delimiter(self, vk: int, hwnd: int, layout: str) -> bool:
        if self._answer(vk, hwnd, layout):
            return True
        word = self._line[self._word_start():]
        if word and self.enabled and self._window == "ok" and {p.layout for p in word} != {layout}:
            self.note(render(word, layout), "слово набрано в двух раскладках: переключали вручную")
        if not word or not self.enabled or self._window != "ok" or {p.layout for p in word} != {layout}:
            # A second space or an empty line: nothing to judge, and the words before no longer sit
            # one space apart, so they cannot be fixed together with the next. Or a word typed in
            # two layouts: the person switched by hand in the middle of it.
            self.session.reset()
            self._finish(vk)
            return False
        if focused_is_password(hwnd):
            self.note("•••", "поле пароля")
            self.session.reset()
            self._finish(vk)
            return False
        keys = "".join(char_of(p, layout) for p in word)
        if self._keep:
            # Its fix in the middle was undone: the same "no" for the whole word, until the app restarts.
            self.detector.soft_exceptions = self.detector.soft_exceptions | {keys.lower()}
            self.note(render(word, layout), "исправление на ходу отменено: не трогаю до перезапуска")
            self.session.reset()
            self._finish(vk)
            return False
        mid_old = self._mid_old
        fix = self._command(word, keys, layout) if vk == VK_SPACE else None
        if fix is None:
            fix = self.session.word(keys, layout)
            if fix is None and self.session.last is not None:
                self.note(render(word, layout), REASONS.get(self.session.last.reason, self.session.last.reason))
        else:
            self.session.reset()
            self.session.context = "en"
        if vk != VK_SPACE:
            self.session.reset()  # a new line or another field: the next words start afresh
        if fix is None:
            self._finish(vk)
            if mid_old and vk == VK_SPACE:
                # Fixed in its middle: a Backspace right after the space still takes the whole word back.
                old = [replace(p, layout=mid_old) for p in word]
                old_keys = "".join(char_of(p, mid_old) for p in old)
                self._last = _Done(Fix(len(word), render(word, layout), layout, render(old, mid_old), [old_keys]), mid_old, old)
            return False
        start = self._run_start(len(fix.keys))
        run = self._line[start:]
        if len(run) != fix.erase or render(run, layout) != fix.original:
            log.warning("run does not match the fix, skipped")
            self.note(render(run, layout), "сбился счёт набранного (автоповтор, вставка)")
            self.session.reset()
            self._finish(vk)
            return False
        # Where the run starts the line, what came before it decides whether a word begins there;
        # unless that is sure, the worker asks the field.
        origin = self._origin if start == 0 else "edge"
        self._line[start:] = [replace(p, layout=fix.layout) for p in run]
        self._finish(vk)
        self._busy = True
        self._jobs.put(("fix", fix, hwnd, layout, vk, run, origin))
        return True

    def _command(self, word: list[Press], keys: str, layout: str) -> Fix | None:
        """The first word of a terminal line, typed in Russian, is a command: ды → ls, пше → git."""
        if (not self._terminal or not self.terminal_answers or self._origin != "edge" or layout == "en"
                or self._word_start() != 0 or self.detector.is_exception(keys)):
            return None
        text = render(word, "en")
        if text.lower() not in COMMANDS:
            return None
        return Fix(len(word), text, "en", render(word, layout), [keys])

    def _answer(self, vk: int, hwnd: int, layout: str) -> bool:
        """A terminal asks [y/N] and the answer went in the Russian layout: нуы → yes, н → y, тщ → no.
        A command alone on its line is fixed the same way: ды → ls, сды → cls."""
        run = self._line
        if (vk == VK_SPACE or not self.enabled or not self.terminal_answers or not self._terminal
                or self._origin != "edge" or layout == "en" or not run
                or any(p.layout != layout or p.vk == VK_SPACE for p in run)):
            return False
        typed = render(run, "en").lower()
        if not ((len(run) <= 3 and typed in ANSWERS) or typed in COMMANDS) or self.detector.is_exception(render(run, "en")):
            return False
        job = ("answer", len(run), render(run, layout), render(run, "en"), hwnd, vk)
        self._reset("edge")
        self._busy = True
        self._jobs.put(job)
        return True

    # --- by hand ---

    def flip(self, whole_line: bool, switch_layout: bool = True) -> bool:
        """The fix-layout hotkey: types the word just typed (or the whole line followed) again in
        the other layout. False when the line is not known; the clipboard fixer takes over then."""
        with self._lock:
            hwnd = user32.GetForegroundWindow() or 0
            if self._busy or hwnd != self._hwnd or self._window == "off" or not self._line:
                return False
            start = 0 if whole_line else self._run_start(1)
            if start == 0 and self._origin == "unknown":
                return False  # the text goes on before what we saw: let the clipboard take it whole
            run = self._line[start:]
            letters = [p for p in run if p.vk != VK_SPACE]
            if not letters or any(p.layout != letters[-1].layout for p in letters):
                return False
            source = letters[-1].layout
            # The last word says where to: with three layouts, the one it reads as a word in.
            last = self._line[self._run_start(1):]
            target = self.detector.flip_target("".join(char_of(p, source) for p in last if p.vk != VK_SPACE), source,
                                               self.session.prefer)
            # One word flipped by hand is a word to switch by itself next time.
            learn = "" if whole_line or len(letters) != len(run) else "".join(char_of(p, source) for p in letters)
            self._line[start:] = [replace(p, layout=target) for p in run]
            self.session.reset()
            self._last = None
            self._mid_old = None
            self._busy = True
            self._jobs.put(("flip", render(run, source), render(run, target), target, switch_layout, hwnd, whole_line,
                            learn))
            return True

    # --- helpers for the hook ---

    def _window_kind(self, hwnd: int) -> str:
        self._window_why = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            self._terminal = False
            return "off"
        if pid == self._own_pid:
            self._terminal = False
            return "off"
        name = process_name(hwnd).lower()
        self._terminal = name in TERMINALS
        if name in self.excluded:
            self._window_why = f"{name or 'программа'} в программах-исключениях"
            return "answers" if self._terminal and self.terminal_answers else "off"
        if self.skip_fullscreen and is_fullscreen(hwnd):
            self._window_why = f"{name or 'окно'} на весь экран (игры пропускаются, меняется в настройках)"
            return "off"
        return "ok"

    def _fast_layout(self, hwnd: int) -> str | None:
        """The window's layout through the thread found when it got the focus: one call, cheap
        enough for every key."""
        layout = thread_layout(self._kbd_thread)
        if layout is None:
            self._kbd_thread = layout_thread(hwnd)  # a console may get its host thread late
            layout = thread_layout(self._kbd_thread)
        return layout

    @staticmethod
    def _layout(hwnd: int) -> str | None:
        return thread_layout(layout_thread(hwnd))

    # --- the worker: types fixes outside the hook ---

    def _worker(self) -> None:
        pythoncom.CoInitialize()  # for asking browsers about password fields
        uia.warm_up()  # COM objects belong to this thread: make them here, before the first fix
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
                elif job[0] == "mid":
                    self._apply_mid(*job[1:])
                elif job[0] == "answer":
                    self._type_answer(*job[1:])
                elif job[0] == "flip":
                    self._flip(*job[1:])
                else:
                    self._undo(*job[1:])
            except Exception:
                log.exception("autoswitch worker")
            finally:
                self._release_held()

    def _field_says(self, expect: str, origin: str = "edge") -> str:
        """Why the text before the caret forbids typing over `expect`, or "": "changed" when it is
        not what was typed (autocompletion, a selection typed over, the app changed it), "middle"
        when a word may begin before it. origin: what came before `expect` in the line we follow."""
        before = uia.text_before_caret(len(expect) + 1)
        if before is not None and not before.endswith(expect):
            time.sleep(0.03)  # it may just be late with the last key
            before = uia.text_before_caret(len(expect) + 1)
            if before is not None and not before.endswith(expect):
                return "changed"
        if origin == "edge":
            return ""
        if before is None:
            return "" if origin == "click" else "middle"  # a click most likely lands between words
        return "middle" if len(before) > len(expect) and before[-len(expect) - 1] not in WORD_EDGE else ""

    def _apply(self, fix: Fix, hwnd: int, layout: str, delimiter: int, run: list[Press], origin: str = "edge") -> None:
        why = self._field_says(fix.original, origin)
        if not why and process_name(hwnd).lower() in BROWSERS and browser_password(hwnd):
            why = "password"
        if why:
            log.info("not fixing %r: %s", fix.original, {
                "changed": "the text before the caret is not what was typed",
                "middle": "the word began before we started following it",
                "password": "a password field",
            }[why])
            self.note(fix.original, {
                "changed": "текст в поле не совпал с набранным (автодополнение, вставка)",
                "middle": "слово начато до того, как курсор сюда попал",
                "password": "поле пароля",
            }[why])
            type_fix(0, "", delimiter)  # leave the text alone, let the delimiter through
            with self._lock:
                self.session.reset()
                n = len(run) + 1
                if why == "middle" and delimiter == VK_SPACE and len(self._line) >= n:
                    self._line[-n:-1] = run  # the line is right, only the word stays as typed
                elif delimiter == VK_SPACE:
                    self._reset()
            return
        moved = fix.text == fix.original  # Russian and Ukrainian type it alike: only the layout moves
        if moved:
            type_fix(0, "", delimiter)
        else:
            type_fix(fix.erase, fix.text, delimiter)
        self._switch(hwnd, fix.layout)
        self.note(f"{fix.original} → {fix.text}", f"раскладка: {layouts.LABELS[fix.layout]}" if moved else "исправлено",
                  fixed=True)
        with self._lock:
            # After Enter or Tab the text may already be sent or left behind: no undo then.
            self._last = _Done(fix, layout, run) if delimiter == VK_SPACE else None
        self.corrected.emit(fix.original, layouts.NOTE_LABELS[fix.layout] if moved else fix.text)

    def _apply_mid(self, fix: Fix, hwnd: int, layout: str, run: list[Press], origin: str) -> None:
        """A word fixed at its MID_LEN-th letter. That letter was held back: the field has one less."""
        last = run[-1]
        why = self._field_says(fix.original[:-1], origin)
        if not why and process_name(hwnd).lower() in BROWSERS and browser_password(hwnd):
            why = "password"
        if why:
            self.note(fix.original + "…", {
                "changed": "текст в поле не совпал с набранным (автодополнение, вставка)",
                "middle": "слово начато до того, как курсор сюда попал",
                "password": "поле пароля",
            }[why])
            shift = [0x10] if last.shift and not _pressed(0x10) else []
            _send([_key(m) for m in shift] + [_key(last.vk), _key(last.vk, up=True)] + [_key(m, up=True) for m in shift])
            with self._lock:
                self.session.reset()
                if why == "middle" and len(self._line) >= len(run):
                    self._line[-len(run):] = run  # the line is right, only the word stays as typed
                else:
                    self._reset()
            return
        type_fix(fix.erase - 1, fix.text, None)
        self._switch(hwnd, fix.layout)
        self.note(f"{fix.original}… → {fix.text}…", "исправлено на ходу", fixed=True)
        with self._lock:
            self._last = _Done(fix, layout, run, mid=True)
            self._mid_old = layout
        self.corrected.emit(fix.original + "…", fix.text + "…")

    def _type_answer(self, erase: int, original: str, text: str, hwnd: int, delimiter: int) -> None:
        type_fix(erase, text, delimiter)
        self._switch(hwnd, "en")
        self.note(f"{original} → {text}", "исправлено в терминале", fixed=True)
        self.corrected.emit(original, text)

    def _flip(self, original: str, text: str, target: str, switch_layout: bool, hwnd: int, whole_line: bool,
              learn: str = "") -> None:
        _let_go_modifiers()
        if self._field_says(original) == "changed":
            log.info("flip: the text before the caret is not what was typed, the clipboard takes over")
            with self._lock:
                self._reset()
            self.lost_track.emit(whole_line)
            return
        type_fix(len(original), text, None)
        if switch_layout:
            self._switch(hwnd, target)
        if learn:
            self.learn(learn, target)
            self.note(text, "исправлено вручную: дальше буду исправлять сам", fixed=True)
        self.flipped.emit(text, target)

    def _undo(self, done: _Done, hwnd: int) -> None:
        """Takes the fix and its space back, returns the old layout and remembers the words."""
        fix = done.fix
        tail = "" if done.mid else " "
        before = uia.text_before_caret(len(fix.text) + 1)
        if before is not None and not before.lower().endswith((fix.text + tail).lower()):
            log.info("undo skipped: the text before the caret changed")
            type_fix(0, "", VK_BACK)  # the Backspace goes through as an ordinary one
            with self._lock:
                self._reset()
            return
        type_fix(len(fix.text) + len(tail), fix.original, None)
        self._switch(hwnd, done.old_layout)
        with self._lock:
            self.session.reset()
            n = len(done.run) + len(tail)
            if len(self._line) >= n and (done.mid or self._line[-1].vk == VK_SPACE):
                self._line[-n:] = done.run  # the caret is back at the end of that word
                self._mid_old = None
                self._keep = done.mid
            else:
                self._reset()
        keys = {k.lower() for k in fix.keys}
        if done.mid:
            # Only the beginning of a word: nothing to remember for good. The word itself is
            # left alone when it ends (see _delimiter).
            self.detector.soft_exceptions = self.detector.soft_exceptions | keys
            self.note(fix.original + "…", "исправление на ходу отменено")
            self.undone.emit(fix.original + "…", False)
            return
        for_good = keys & self._struck
        # An accidental Backspace must not switch a word off for ever: the first undo keeps it
        # until the app restarts, a second one (after that) for good.
        self.detector.soft_exceptions = self.detector.soft_exceptions | keys
        if self.detector.learned.keys() & keys:
            self.detector.learned = {k: v for k, v in self.detector.learned.items() if k not in keys}
            self.save_learned()
        if for_good:
            self.update_exceptions(for_good, set())
        self._struck |= keys
        try:
            with self._file_lock:
                self.struck_file.write_text("\n".join(sorted(self._struck)), encoding="utf-8")
        except OSError:
            log.exception("undone words not saved")
        self.note(fix.original, "отменено второй раз: больше не трогаю (вернуть — Pause)" if for_good
                  else "отменено: не трогаю до перезапуска; ещё одна отмена потом — навсегда")
        self.undone.emit(fix.original, bool(for_good))

    def _switch(self, hwnd: int, lang: str) -> None:
        """Asks the window for another layout and waits until it takes it, so that the keys held
        meanwhile are replayed in the new layout, not the old one. A window that ignores the
        request is asked again through its focused control and its IME window."""
        hkl = self._hkl(lang)
        if not hkl:
            log.warning("no %s layout installed", lang)
            return
        targets = [hwnd]
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
        for attempt, target in enumerate(targets):
            try:
                win32api.PostMessage(target, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
            except Exception:
                continue
            end = time.monotonic() + (0.12 if attempt < len(targets) - 1 else 0.25)
            while time.monotonic() < end:
                if self._layout(hwnd) == lang:
                    return
                time.sleep(0.002)
        log.warning("the window did not switch to %s (%s)", lang, process_name(hwnd))

    def _take_held_backspace(self) -> bool:
        with self._lock:
            if self._held and self._held[0][0] == VK_BACK and not self._held[0][2]:
                del self._held[0]
                return True
            return False

    def _release_held(self) -> None:
        """Replays the keys held while a fix was typed. The line stays busy until none are left:
        a key pressed during the replay is held too, so it cannot get ahead of the replayed ones."""
        first = True
        while True:
            with self._lock:
                held, self._held = self._held, []
                if not held:
                    self._busy = False
                    return
            if first:
                time.sleep(0.01)
                first = False
            self._replay(held)

    def _replay(self, held: list[tuple[int, bool, tuple[int, ...]]]) -> None:
        inputs: list[INPUT] = []
        for vk, shift, combo in held:
            # A shortcut (Ctrl+S right after a word) must stay a shortcut even if its modifier
            # was let go while the fix was being typed: press what is no longer down around the key.
            mods = [m for m in ((0x10,) if shift else ()) + combo if not _pressed(m)]
            inputs += [_key(m) for m in mods] + [_key(vk), _key(vk, up=True)] + [_key(m, up=True) for m in reversed(mods)]
            with self._lock:
                # Replayed keys carry MARKER and skip the hook: keep the line in step here.
                layout = self._fast_layout(self._hwnd)
                self._last = None  # a key typed after the fix: Backspace no longer undoes it
                if combo:
                    self._reset()  # a shortcut: the text may be anything now
                elif vk == VK_BACK:
                    if self._line:
                        if self._line.pop().vk == VK_SPACE:
                            self.session.reset()
                        self._erased()
                    else:
                        self._reset()
                elif vk in (VK_RETURN, VK_TAB):
                    self._reset("edge")
                elif layout not in self.detector.langs or self._window == "off":
                    self._reset()
                elif vk == VK_SPACE:
                    self.session.reset()  # the word before it went unjudged
                    self._mid_old = None
                    self._keep = False
                    self._append(Press(VK_SPACE, False, False, layout))
                elif vk in US_KEYS:
                    self._append(Press(vk, shift, _caps(), layout))
                else:
                    self._reset()
        _send(inputs)

    @staticmethod
    def _hkl(lang: str) -> int:
        for hkl in win32api.GetKeyboardLayoutList():
            if hkl & 0xFFFF == LANG_IDS[lang]:
                return hkl
        return 0
