"""End-to-end test of automatic switching with real system key presses.

Starts tests/e2e_target.py (a text box in another process), runs the AutoSwitcher hook here,
types into the box with SendInput as a keyboard would, and compares what the box shows.
Keep hands off the keyboard and mouse for the ~15 seconds it runs.
"""

import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import win32api  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

from humantype.autoswitch import US_KEYS, AutoSwitcher  # noqa: E402
from humantype.detector import Detector  # noqa: E402

user32 = ctypes.windll.user32
CHAR_TO_KEY = {}
for vk, (plain, shifted) in US_KEYS.items():
    CHAR_TO_KEY.setdefault(plain, (vk, False))
    CHAR_TO_KEY.setdefault(shifted, (vk, True))
CHAR_TO_KEY[" "] = (0x20, False)

tmp = Path(tempfile.mkdtemp())
out_file = tmp / "text.txt"


def press(vk: int, up: bool = False) -> None:
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP if up else 0, 0)


def type_keys(keys: str, delay: float = 0.03) -> None:
    for c in keys:
        if c == "\b":
            vk, shift = 0x08, False
        else:
            vk, shift = CHAR_TO_KEY[c]
        if shift:
            press(0x10)
        press(vk)
        press(vk, up=True)
        if shift:
            press(0x10, up=True)
        time.sleep(delay)


def text() -> str:
    time.sleep(0.5)
    return out_file.read_text(encoding="utf-8") if out_file.exists() else ""


def set_layout(hwnd: int, lang: int) -> None:
    for hkl in win32api.GetKeyboardLayoutList():
        if hkl & 0xFFFF == lang:
            win32api.PostMessage(hwnd, 0x0050, 0, hkl)
    time.sleep(0.3)


def clear(hwnd: int) -> None:
    press(0x11)
    type_keys("a", 0.02)
    press(0x11, up=True)
    press(0x2E)
    press(0x2E, up=True)
    time.sleep(0.3)


def main() -> int:
    app = QCoreApplication(sys.argv)
    switcher = AutoSwitcher(Detector(ROOT / "assets" / "dict"), tmp / "exceptions.txt")
    events = []
    switcher.corrected.connect(lambda a, b: events.append(f"{a}->{b}"))
    switcher.start()

    target = subprocess.Popen([sys.executable, str(ROOT / "tests" / "e2e_target.py"), str(out_file)])
    hwnd = 0
    for _ in range(50):
        hwnd = win32gui.FindWindow(None, "HumanType E2E target")
        if hwnd:
            break
        time.sleep(0.1)
    time.sleep(0.8)
    if not hwnd:
        print("target window did not appear")
        return 1
    press(0x12)
    press(0x12, up=True)  # an Alt tap lets this process hand the foreground over
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.4)

    cases = [
        # (name, starting layout, keys, expected text)
        ("phrase in wrong layout", 0x0409, "ghbdtn rfr ltkf ", "привет как дела "),
        ("short words first", 0x0409, "z nt,z k.,k. ", "я тебя люблю "),
        ("english typed in russian", 0x0419, "ok i will be there soon ", "ok i will be there soon "),
        ("english in russian layout", 0x0419, "hello my friend ", "hello my friend "),
        ("russian typed right", 0x0419, "ghbdtn vbh ", "привет мир "),
        ("english typed right", 0x0409, "this is fine ", "this is fine "),
        ("fast typing", 0x0409, "cgfcb,j pf gjvjom ", "спасибо за помощь "),
        ("punctuation", 0x0409, "ghbdtn? rfr ltkf& ", "привет, как дела? "),
        ("undo with backspace", 0x0409, "ghbdtn \b", "ghbdtn"),
        ("remembered after undo", 0x0409, "ghbdtn ", "ghbdtn "),
    ]
    failed = 0
    try:
        for name, layout, keys, expected in cases:
            if win32gui.GetForegroundWindow() != hwnd:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            if win32gui.GetForegroundWindow() != hwnd:
                print("lost the foreground, stopping")
                return 1
            clear(hwnd)
            set_layout(hwnd, layout)
            switcher.reset()
            type_keys(keys, 0.0 if name == "fast typing" else 0.03)
            app.processEvents()
            got = text()
            ok = got == expected
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name:28} {got!r}" + ("" if ok else f"   expected {expected!r}"))
    finally:
        switcher.stop()
        target.terminate()
    print(f"\n{len(cases) - failed}/{len(cases)} passed; exceptions now: {sorted(switcher.detector.exceptions)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
