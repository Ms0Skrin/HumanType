"""Runs anywhere (Linux too): simulation of humantype.autoswitch: Win32 replaced by fakes, a fake text box records what lands on screen."""

import ctypes
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeDLL:
    def __getattr__(self, name):
        f = mock.MagicMock(name=name, return_value=0)
        setattr(self, name, f)
        return f


ctypes.WinDLL = lambda *a, **k: FakeDLL()
ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
ctypes.windll = types.SimpleNamespace(user32=FakeDLL(), shell32=FakeDLL())
for name in ("win32api", "win32con", "win32gui", "win32process", "pythoncom", "winreg"):
    sys.modules[name] = mock.MagicMock(name=name)

from humantype import autoswitch as A  # noqa: E402
from humantype.detector import Detector  # noqa: E402
from humantype.layout import EN_TO_RU  # noqa: E402

HKL = {"en": 0x0409, "ru": 0x0419}


class Box:
    def __init__(self, layout="en", slow=0.0):
        self.text = []
        self.layout = layout
        self.slow = slow
        self.pressed = set()
        self.lock = threading.Lock()

    def put(self, vk, shift):
        if vk == A.VK_BACK:
            if self.text:
                self.text.pop()
        elif vk == A.VK_SPACE:
            self.text.append(" ")
        elif vk == A.VK_RETURN:
            self.text.append("\n")
        elif vk == A.VK_TAB:
            self.text.append("\t")
        else:
            c = A.US_KEYS[vk][1 if shift else 0]
            self.text.append(c.translate(EN_TO_RU) if self.layout == "ru" else c)

    def s(self):
        return "".join(self.text)


box = Box()
CHAR = {}
for vk, (p, s) in A.US_KEYS.items():
    CHAR.setdefault(p, (vk, False))
    CHAR.setdefault(s, (vk, True))
CHAR[" "] = (A.VK_SPACE, False)
CHAR["\n"] = (A.VK_RETURN, False)
CHAR["\t"] = (A.VK_TAB, False)
CHAR["\b"] = (A.VK_BACK, False)


def fake_type_fix(erase, text, then_vk):
    time.sleep(box.slow)
    with box.lock:
        for _ in range(erase):
            box.text.pop()
        box.text.extend(text)
        if then_vk is not None:
            box.put(then_vk, False)


def fake_send(inputs):
    shift = False
    with box.lock:
        for i in inputs:
            ki = i.u.ki
            if ki.wVk == 0x10:
                shift = not (ki.dwFlags & A.KEYEVENTF_KEYUP)
            elif not ki.dwFlags & A.KEYEVENTF_KEYUP:
                box.put(ki.wVk, shift or 0x10 in box.pressed)


def post_message(hwnd, msg, w, hkl):
    def later():
        time.sleep(box.switch_delay)
        box.layout = {v: k for k, v in HKL.items()}[hkl]
    threading.Thread(target=later).start()


box.switch_delay = 0.0
A.type_fix = fake_type_fix
A._send = fake_send
A._pressed = lambda vk: vk in box.pressed
A.focused_is_password = lambda h: False
A.process_name = lambda h: "notepad.exe"
A.win32api.PostMessage = post_message
A.AutoSwitcher._layout = staticmethod(lambda hwnd: box.layout)
A.AutoSwitcher._hkl = staticmethod(lambda lang: HKL[lang])
A.AutoSwitcher._window_allowed = lambda self, h: True
A.user32.GetForegroundWindow.return_value = 1
A.user32.GetKeyState.return_value = 0

detector = Detector(ROOT / "assets" / "dict")
tmp = Path(tempfile.mkdtemp()) / "exc.txt"
tmp.unlink(missing_ok=True)
sw = A.AutoSwitcher(detector, tmp)
threading.Thread(target=sw._worker, daemon=True).start()


def idle():
    for _ in range(400):
        if not sw._busy and sw._jobs.empty():
            time.sleep(0.03)
            if not sw._busy and sw._jobs.empty():
                return
        time.sleep(0.005)


def key(ch, wait=True):
    vk, shift = CHAR[ch]
    if shift:
        box.pressed.add(0x10)
    kb = A.KBDLLHOOKSTRUCT(vk, 0, 0, 0, 0)
    if not sw._handle(A.WM_KEYDOWN, kb):
        with box.lock:
            box.put(vk, shift)
    box.pressed.discard(0x10)
    if wait:
        idle()


def type_(s, wait=True):
    for ch in s:
        key(ch, wait)
    idle()


def reset(layout="en", slow=0.0, switch_delay=0.0):
    idle()
    box.text.clear()
    box.layout = layout
    box.slow = slow
    box.switch_delay = switch_delay
    sw.reset()
    sw.detector.exceptions = set()


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("OK  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


reset(); type_("ghbdtn rfr ltkf "); check("basic", box.s(), "привет как дела ")
reset(); type_("z nt,z k.,k. "); check("short words", box.s(), "я тебя люблю ")
reset("ru"); type_("hello world "); check("ru->en", box.s(), "hello world ")
reset(); type_("hello world "); check("correct en untouched", box.s(), "hello world ")
reset(); type_("ghbdtn \b"); check("undo after fix", box.s(), "ghbdtn"); check("undo layout back", box.layout, "en")
check("undo saved exception", tmp.read_text(), "ghbdtn")
type_(" ghbdtn "); check("exception respected", box.s(), "ghbdtn ghbdtn ")
reset(); type_("ghbdtn\n"); check("Enter fixes", box.s(), "привет\n"); check("no undo after Enter", sw._last, None)
reset(); type_("ghbdtn\t"); check("Tab fixes", box.s(), "привет\t")
reset(); type_("ghbdtnn\b "); check("backspace inside word", box.s(), "привет ")
reset(); type_("z  nt,z "); check("double space breaks the run", box.s(), "z  тебя ")
# fast typing while a fix is typed and the window switches layout slowly (bug 1)
reset(slow=0.05, switch_delay=0.03); type_("ghbdtn", True); key(" ", False); type_("rfr ", False)
check("held keys after slow switch", box.s(), "привет как ")
# Backspace pressed while the fix is being typed (bug 3)
reset(slow=0.05); type_("ghbdtn"); key(" ", False); key("\b", False); idle()
check("backspace during fix = undo", box.s(), "ghbdtn"); check("layout back", box.layout, "en")
# held backspace inside the held word (bug 3b): buffer must stay in step
reset(slow=0.05); type_("ghbdtn"); key(" ", False); type_("rfrr\b ", False)
check("held backspace keeps buffer", box.s(), "привет как ")
# exceptions edited in settings while an undo adds one (bug 6)
reset(); sw.update_exceptions({"abc"}, set()); initial = set(sw.detector.exceptions)
type_("ghbdtn \b")
words = initial | {"xyz"}
sw.update_exceptions(words - initial, initial - words)
check("settings merge keeps undo word", sorted(sw.detector.exceptions), ["abc", "ghbdtn", "xyz"])
# watchdog maths
A.user32.GetLastInputInfo.side_effect = lambda p: (setattr(p._obj, "dwTime", 10_000), 1)[1]
check("idle: input after hook", A._idle_ms(5_000), 5_000)
check("idle: hook newer", A._idle_ms(12_000), 0)
A.user32.GetLastInputInfo.side_effect = lambda p: (setattr(p._obj, "dwTime", 100), 1)[1]
check("idle: tick wrap", A._idle_ms(0xFFFFFF00), 356)

print("FAILS:", fails)
sys.exit(1 if fails else 0)
