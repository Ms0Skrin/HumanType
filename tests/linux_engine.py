"""The Linux engine against a fake text box: keys go through the engine as from a keyboard, what
is not swallowed is typed into the box in its layout, and the engine's own key presses too.

    python tests/linux_engine.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evdev import ecodes as e  # noqa: E402

from humantype.detector import Detector  # noqa: E402
from humantype.layout import EN_TO_RU  # noqa: E402
from humantype.linux.engine import Engine, Settings, ShellState  # noqa: E402
from humantype.linux.keymap import SHIFTS, US_KEYS, char_of  # noqa: E402

CHAR = {}
for code, (plain, shifted) in US_KEYS.items():
    CHAR.setdefault(plain, (code, False))
    CHAR.setdefault(shifted, (code, True))
CHAR[" "] = (e.KEY_SPACE, False)
CHAR["\n"] = (e.KEY_ENTER, False)
CHAR["\t"] = (e.KEY_TAB, False)
CHAR["\b"] = (e.KEY_BACKSPACE, False)


class Box:
    """A text field with the caret at the end, as the system would fill it."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.layout = "en"
        self.down: set[int] = set()  # keys held, physical and injected together
        self.caps = False
        self.surrounding = True  # does the app tell the text before the caret
        self.purpose = "normal"
        self.app = "org.gnome.TextEditor"
        self.switch_ok = True
        self.notes: list[str] = []
        self.histories = 0  # times the clipboard history was opened
        self.history_ok = True

    def key(self, code: int, value: int) -> None:
        if value == 0:
            self.down.discard(code)
            return
        self.down.add(code)
        if code == e.KEY_BACKSPACE:
            if self.text:
                self.text.pop()
        elif code == e.KEY_SPACE:
            self.text.append(" ")
        elif code == e.KEY_ENTER:
            self.text.append("\n")
        elif code == e.KEY_TAB:
            self.text.append("\t")
        elif code == e.KEY_CAPSLOCK:
            self.caps = not self.caps
        elif code in US_KEYS:
            c = char_of(code, bool(self.down & SHIFTS), self.caps, self.layout)
            self.text.append(c.translate(EN_TO_RU) if self.layout == "ru" else c)

    # Output
    def press(self, code):
        self.key(code, 1)

    def release(self, code):
        self.key(code, 0)

    def tap(self, code):
        self.key(code, 1)
        self.key(code, 0)

    def shell_state(self, expect="", ignore_case=False):
        text = "".join(self.text)
        if not self.surrounding:
            return ShellState(self.layout, self.app, "", self.purpose, False, False)
        t, x = (text.lower(), expect.lower()) if ignore_case else (text, expect)
        matches = t.endswith(x)
        edge = " \t\n([{«\"'—–-/"
        prev = text[: len(text) - len(expect)][-1:]
        common = 0
        while common < min(len(t), len(x)) and t[-1 - common] == x[-1 - common]:
            common += 1
        before = t[: len(t) - common][-1:]
        return ShellState(self.layout, self.app, "", self.purpose, False, False, True, matches,
                          matches and (not prev or prev in edge), common, not before or before in edge)

    def set_layout(self, lang):
        if self.switch_ok:
            self.layout = lang
            engine.layout = lang
        return self.switch_ok

    def note(self, kind, text):
        self.notes.append(text)

    def history(self):
        self.histories += 1
        return self.history_ok

    def s(self):
        return "".join(self.text)


detector = Detector(ROOT / "assets" / "dict")
box = Box()
saved: set[str] = set()
engine = Engine(detector, box, Settings(excluded=("org.gnome.ptyxis", "jetbrains-*")), saved.update)


def event(code, value):
    if not engine.on_key(code, value):
        box.key(code, value)


def type_(s: str) -> None:
    for ch in s:
        code, shift = CHAR[ch]
        if shift:
            event(e.KEY_LEFTSHIFT, 1)
        event(code, 1)
        event(code, 0)
        if shift:
            event(e.KEY_LEFTSHIFT, 0)


def combo(*codes):
    for c in codes:
        event(c, 1)
    for c in reversed(codes):
        event(c, 0)


def reset(layout="en", **kw):
    box.text.clear()
    box.histories, box.history_ok = 0, True
    box.layout = engine.layout = layout
    box.surrounding, box.purpose, box.app, box.switch_ok = True, "normal", "org.gnome.TextEditor", True
    for k, v in kw.items():
        setattr(box, k, v)
    box.notes.clear()
    engine.suspended = False
    box.caps = engine.caps = False
    engine.reset("edge")
    detector.exceptions = set()
    engine.set_enabled(True)


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("OK  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f"  (want {want!r})"))


reset(); type_("ghbdtn rfr ltkf "); check("basic", box.s(), "привет как дела ")
check("layout after", box.layout, "ru")
reset(); type_("z nt,z k.,k. "); check("short words together", box.s(), "я тебя люблю ")
reset("ru"); type_("hello world "); check("ru -> en", box.s(), "hello world ")
reset(); type_("hello world, this is fine "); check("english untouched", box.s(), "hello world, this is fine ")
reset("ru"); type_("ghbdtn rfr "); check("russian untouched", box.s(), "привет как ")
reset(); type_("Ghbdtn "); check("capital letter", box.s(), "Привет ")
reset(); type_("GHBDTN "); check("all caps word", box.s(), "ПРИВЕТ ")
reset(); combo(e.KEY_CAPSLOCK); engine.caps = True; type_("ghbdtn "); check("caps lock", box.s(), "ПРИВЕТ ")
reset(); type_("ghbdtn \b"); check("undo", box.s(), "ghbdtn"); check("undo layout", box.layout, "en")
check("undo remembered", "ghbdtn" in saved and "ghbdtn" in detector.exceptions, True)
type_(" ghbdtn "); check("exception kept", box.s(), "ghbdtn ghbdtn ")
reset(); type_("ghbdtn\n"); check("Enter", box.s(), "привет\n")
type_("\b"); check("no undo after Enter", box.s(), "привет")
reset(); type_("ghbdtn\t"); check("Tab", box.s(), "привет\t")
reset(); type_("ghbdtnn\b "); check("Backspace inside a word", box.s(), "привет ")
reset(); type_("z  nt,z "); check("double space breaks the run", box.s(), "z  тебя ")
reset(); type_("ghbdtn"); event(e.KEY_PAUSE, 1); event(e.KEY_PAUSE, 0); check("Pause flips the word", box.s(), "привет")
type_(" vbh "); check("typing goes on after flip", box.s(), "привет мир ")
reset(); type_("ghbdtn "); check("…", box.s(), "привет "); event(e.KEY_PAUSE, 1); event(e.KEY_PAUSE, 0)
check("Pause flips back a fixed word", box.s(), "ghbdtn "); check("layout", box.layout, "en")
reset(); type_("hello"); combo(e.KEY_LEFTSHIFT, e.KEY_PAUSE); check("Shift+Pause line", box.s(), "руддщ")
reset(); combo(e.KEY_LEFTALT, e.KEY_PAUSE); type_("ghbdtn "); check("Alt+Pause turns it off", box.s(), "ghbdtn ")
combo(e.KEY_LEFTALT, e.KEY_PAUSE); type_("ghbdtn "); check("… and on", box.s(), "ghbdtn привет ")
reset(purpose="password"); type_("ghbdtn "); check("password field", box.s(), "ghbdtn ")
reset(purpose="terminal"); type_("ghbdtn "); check("terminal", box.s(), "ghbdtn ")
reset(app="org.gnome.Ptyxis"); type_("ghbdtn "); check("excluded app", box.s(), "ghbdtn ")
reset("ru", purpose="terminal"); type_("yes\n"); check("terminal: нуы -> yes", box.s(), "yes\n")
reset("ru", app="kitty"); type_("Y\n"); check("terminal by app: Н -> Y", box.s(), "Y\n")
reset("ru", purpose="terminal"); type_("no\n"); check("terminal: тщ -> no", box.s(), "no\n")
reset("ru", purpose="terminal"); type_("ls yes\n"); check("terminal: not alone on the line", box.s(), "ды нуы\n")
reset("ru"); type_("y\n"); check("not a terminal: н stays", box.s(), "н\n")
reset("ru", purpose="terminal"); type_("yes "); check("terminal: only on Enter", box.s(), "нуы ")
reset(app="jetbrains-pycharm"); type_("ghbdtn "); check("excluded glob", box.s(), "ghbdtn ")
reset(); type_("ghb"); box.text.append("X"); type_("dtn "); check("text changed behind us (autocomplete)", box.s(), "ghbXdtn ")
reset(surrounding=False); type_("ghbdtn "); check("no surrounding text: still fixes", box.s(), "привет ")
reset(); type_("hello"); box.text.clear(); type_("ghbdtn "); check("field cleared by the app", box.s(), "привет ")
reset(); type_("ntcn \b"); box.text.clear(); type_("ghbdtn"); event(e.KEY_PAUSE, 1); event(e.KEY_PAUSE, 0)
check("field cleared by the app, then Pause", box.s(), "привет")
reset(); type_("abc"); box.text[:] = list("xyz "); type_("ghbdtn "); check("text replaced by the app", box.s(), "xyz привет ")
reset(switch_ok=False); type_("ghbdtn "); check("layout switch fails: untouched", box.s(), "ghbdtn ")
reset(); type_("ghbdtn"); event(e.KEY_LEFTCTRL, 1); event(e.KEY_A, 1); event(e.KEY_A, 0); event(e.KEY_LEFTCTRL, 0)
type_(" "); check("Ctrl shortcut resets", box.s(), "ghbdtna ")
reset(); type_("ghbd"); engine.on_button(); type_("tn "); check("click resets", box.s(), "ghbdtn ")
reset(); type_("gh"); event(e.KEY_B, 1); event(e.KEY_B, 2); event(e.KEY_B, 0)
type_("dtn "); check("autorepeat: the tail of a word is not fixed", box.s(), "ghbbdtn ")
reset(surrounding=False); type_("gh"); event(e.KEY_LEFT, 1); event(e.KEY_LEFT, 0); type_("dtn ")
check("arrow mid-word, no surrounding: tail not fixed", box.s(), "ghdtn ")
reset(surrounding=False); type_("abc"); engine.on_button(); type_("ghbdtn "); check("click, no surrounding: fixed", box.s(), "abcпривет ")
reset(); type_("abc"); engine.on_button(); type_(" ghbdtn "); check("click then word: fixed", box.s(), "abc привет ")
reset(); type_("ghbdtn"); event(e.KEY_LEFTSHIFT, 1); event(e.KEY_SPACE, 1); event(e.KEY_SPACE, 0); event(e.KEY_LEFTSHIFT, 0)
check("Shift+Space with Shift held", box.s(), "привет ")
reset(); type_("ns ult? "); check("punctuation inside (ты где?)", box.s(), "ты где, ")
reset(); type_("file.txt ")
check("file name untouched", box.s(), "file.txt ")
reset(); type_("https://github.com "); check("url untouched", box.s(), "https://github.com ")

# clipboard history: Ctrl+Shift+V opens it, the keys go to the list while it is open
reset(); type_("abc"); combo(e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_V)
check("history: Ctrl+Shift+V opens it", box.histories, 1); check("history: V swallowed", box.s(), "abc")
engine.suspended = True; type_("ghbdtn "); check("history open: keys are not ours", box.s(), "abcghbdtn ")
engine.suspended = False; engine.reset(); type_("ghbdtn "); check("history closed: fixing again", box.s(), "abcghbdtn привет ")
reset(purpose="terminal"); combo(e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_V)
check("history: a terminal pastes with it", (box.histories, box.s()), (0, "V"))
reset(history_ok=False); combo(e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_V)
check("history: none here, the key goes on", box.s(), "V")
reset(); combo(e.KEY_LEFTCTRL, e.KEY_V); check("history: Ctrl+V is a paste", (box.histories, box.s()), (0, "v"))
from humantype.linux.engine import parse_hotkey  # noqa: E402
check("hotkey parse", parse_hotkey("Super + V"), (frozenset({"super"}), e.KEY_V))
check("hotkey parse: nonsense", parse_hotkey("hyper+v"), None)
# the store the history keeps
import tempfile  # noqa: E402
from humantype.clipstore import ClipStore  # noqa: E402
d = Path(tempfile.mkdtemp())
store = ClipStore(d / "h.db", d / "img", limit=2)
store.add_text("один"); store.add_text("два"); store.add_text("один"); store.add_text("три")
check("store: newest first, no repeats, limit", [i.text for i in store.items()], ["три", "один"])
store.toggle_pin(store.items()[1].id); store.add_text("четыре"); store.add_text("пять")
check("store: pinned stays", [i.text for i in store.items()], ["один", "пять", "четыре"])

print("FAILS:", fails)
sys.exit(1 if fails else 0)
