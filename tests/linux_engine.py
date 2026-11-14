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
from humantype.layout import text_of  # noqa: E402
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
            self.text.append(text_of(c, self.layout))

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
# The fix at the fifth letter has its own checks at the end; the ones before them are for the fix at the space.
engine = Engine(detector, box, Settings(excluded=("org.gnome.ptyxis", "jetbrains-*"), mid_word=False), saved.update)
counts: list[tuple[str, str]] = []
engine.count = lambda kind, word="": counts.append((kind, word))


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
    detector.soft_exceptions = set()
    detector.learned = {}
    counts.clear()
    engine.session.prefer = None
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

# a word flipped by hand is learned and switched by itself after
reset(); type_("qwzx "); check("learn: an unknown word is left", box.s(), "qwzx ")
reset(); type_("qwzx"); event(e.KEY_PAUSE, 1); event(e.KEY_PAUSE, 0); check("learn: flipped", box.s(), "йцяч")
check("learn: remembered", detector.learned.get("qwzx"), "ru"); check("learn: counted", counts, [("manual", "")])
learned = dict(detector.learned)
reset(); detector.learned = learned; type_("qwzx "); check("learn: now switched by itself", box.s(), "йцяч ")
type_(""); check("learn: Backspace unlearns", detector.learned.get("qwzx"), None)
check("learn: the undo is counted with its word", counts, [("fixed", ""), ("undone", "qwzx")])

# Scroll Lock: the case of the last word; Shift+Scroll Lock: its transliteration
scroll = lambda: (event(e.KEY_SCROLLLOCK, 1), event(e.KEY_SCROLLLOCK, 0))  # noqa: E731
reset("ru"); combo(e.KEY_CAPSLOCK); engine.caps = True; type_("Ghbdtn"); check("case: typed with Caps Lock", box.s(), "пРИВЕТ")
combo(e.KEY_CAPSLOCK); engine.caps = False; scroll(); check("case: пРИВЕТ → Привет", box.s(), "Привет")
scroll(); check("case: → ПРИВЕТ", box.s(), "ПРИВЕТ"); scroll(); check("case: → привет", box.s(), "привет")
type_(" vbh "); check("case: typing goes on", box.s(), "привет мир ")
reset(); type_("hello "); scroll(); check("case: the word just finished", box.s(), "Hello ")
reset(); type_("privet"); combo(e.KEY_LEFTSHIFT, e.KEY_SCROLLLOCK); check("translit: privet → привет", box.s(), "привет")
check("translit: layout", box.layout, "ru")
combo(e.KEY_LEFTSHIFT, e.KEY_SCROLLLOCK); check("translit: and back", box.s(), "privet"); check("translit: layout back", box.layout, "en")
reset(); type_("shchuka"); combo(e.KEY_LEFTSHIFT, e.KEY_SCROLLLOCK); check("translit: shchuka → щука", box.s(), "щука")
type_(" b "); check("translit: typing goes on", box.s(), "щука и ")
reset(); scroll(); check("case: nothing typed, nothing done", box.s(), "")

# a word that clearly belongs to the other layout is fixed at its fifth letter, without waiting for the space
engine.settings = Settings(excluded=("org.gnome.ptyxis", "jetbrains-*"))
reset(); type_("ghbdt"); check("mid: fixed at the fifth letter", box.s(), "приве"); check("mid: layout", box.layout, "ru")
type_("n rfr "); check("mid: the rest goes on in the new layout", box.s(), "привет как ")
check("mid: counted once", counts, [("fixed", "")])
reset(); type_("ghbdtn "); type_(""); check("mid: Backspace after the space takes the word back", box.s(), "ghbdtn")
check("mid: layout back", box.layout, "en"); check("mid: the word is remembered", "ghbdtn" in detector.exceptions, True)
type_(" ghbdtn "); check("mid: the undone word is left alone", box.s(), "ghbdtn ghbdtn ")
reset(); type_("ntcnb"); check("mid: another word", box.s(), "тести"); type_("")
check("mid: Backspace right after undoes", box.s(), "ntcnb"); check("mid: undo, layout back", box.layout, "en")
type_("hjdfnm "); check("mid: the undone word stays when it ends", box.s(), "ntcnbhjdfnm ")
check("mid: ... and is not remembered for good", detector.exceptions, set())
type_("ntcnbhjdfnm "); check("mid: ... but is left alone the next time", box.s(), "ntcnbhjdfnm ntcnbhjdfnm ")
reset(); type_("z nt,z k.,k."); check("mid: the short words before it are fixed too", box.s(), "я тебя люблю")
reset(); type_("hello world "); check("mid: the right layout is untouched", box.s(), "hello world ")
reset("ru"); type_("hello"); check("mid: ru → en", box.s(), "hello"); check("mid: ru → en layout", box.layout, "en")
reset(); type_("Ghbdtn "); check("mid: capital letter", box.s(), "Привет ")
reset(); type_("GHBDTN "); check("mid: all capitals wait for the space", box.s(), "ПРИВЕТ ")
reset(); type_("ghbdtt"); type_("dtn "); check("mid: erased below five and typed again", box.s(), "привет ")
type_(""); check("mid: no undo for a word typed again in the new layout", box.s(), "привет")
reset(purpose="password"); type_("ghbdtn "); check("mid: password field", box.s(), "ghbdtn ")
reset(app="jetbrains-pycharm"); type_("ghbdtn "); check("mid: excluded app", box.s(), "ghbdtn ")
reset(switch_ok=False); type_("ghbdtn "); check("mid: layout switch fails: untouched", box.s(), "ghbdtn ")
reset(); type_("abc"); event(e.KEY_LEFT, 1); event(e.KEY_LEFT, 0); box.text[:] = list("abc"); type_("ghbdtn ")
check("mid: not in the middle of a word", box.s(), "abcghbdtn ")
reset(); type_("ghb"); box.text.append("X"); type_("dtn "); check("mid: text changed behind us", box.s(), "ghbXdtn ")
reset(); type_("ghbdtn"); event(e.KEY_PAUSE, 1); event(e.KEY_PAUSE, 0); check("mid: Pause flips the fixed word back", box.s(), "ghbdtn")
engine.settings = Settings(excluded=("org.gnome.ptyxis", "jetbrains-*"), mid_word=False)
reset(); type_("ghbdt"); check("mid: off in the settings", box.s(), "ghbdt")

# --- Ukrainian: a third layout, followed only when the system has it ---
pause = lambda: (event(e.KEY_PAUSE, 1), event(e.KEY_PAUSE, 0))  # noqa: E731
check("uk: not in use until its layout is there", detector.langs, ("ru", "en"))
reset("uk"); type_("hello "); check("uk: without it the Ukrainian layout is not followed", box.s(), "руддщ ")
engine.set_available({"en", "ru"}); check("uk: no Ukrainian layout, two languages", detector.langs, ("ru", "en"))
engine.set_available({"en", "ru", "uk"}); check("uk: its layout is there, three languages", detector.langs, ("ru", "en", "uk"))
reset(); type_("ghbdsn zr cghfdb "); check("uk: en → uk", box.s(), "привіт як справи "); check("uk: en → uk layout", box.layout, "uk")
reset(); type_("ghbdtn rfr ltkf "); check("uk: en → ru as before", box.s(), "привет как дела "); check("uk: en → ru layout", box.layout, "ru")
reset(); type_("z nt,z k.,k. "); check("uk: Russian short words as before", box.s(), "я тебя люблю "); check("uk: ... layout", box.layout, "ru")
reset(); type_("z nt,t le;t k.,k. "); check("uk: Ukrainian short words", box.s(), "я тебе дуже люблю "); check("uk: ... layout", box.layout, "uk")
reset(); type_("hello world, this is fine "); check("uk: english untouched", box.s(), "hello world, this is fine ")
reset("ru"); type_("ghbdsn "); check("uk: ru → uk", box.s(), "привіт "); check("uk: ru → uk layout", box.layout, "uk")
reset("uk"); type_("vsckb "); check("uk: uk → ru", box.s(), "мысли "); check("uk: uk → ru layout", box.layout, "ru")
reset("uk"); type_("hello world "); check("uk: uk → en", box.s(), "hello world "); check("uk: uk → en layout", box.layout, "en")
reset("ru"); type_("hello world "); check("uk: ru → en as before", box.s(), "hello world ")
reset("uk"); type_("ghbdsn zr cghfdb "); check("uk: Ukrainian untouched", box.s(), "привіт як справи "); check("uk: ... layout", box.layout, "uk")
reset("ru"); type_("ghbdtn rfr ltkf "); check("uk: Russian untouched", box.s(), "привет как дела "); check("uk: ... layout", box.layout, "ru")
# words Russian and Ukrainian spell alike move nothing, whichever of the two layouts is on
reset("uk"); type_("vfvf nfr yf "); check("uk: shared words in the Ukrainian layout", (box.s(), box.layout), ("мама так на ", "uk"))
reset("ru"); type_("vfvf nfr yf "); check("uk: shared words in the Russian layout", (box.s(), box.layout), ("мама так на ", "ru"))
check("uk: shared words, nothing counted", counts, [])
# a word only the other Cyrillic language has, typed alike in both: the text stays, the layout follows
reset("uk"); type_("ghbdtn "); check("uk: a Russian word in the Ukrainian layout", (box.s(), box.layout), ("привет ", "ru"))
type_("vs "); check("uk: ... and the next word comes out right", box.s(), "привет мы ")
reset("uk"); type_("ghbdtn "); type_("\b"); check("uk: ... Backspace takes the layout back", (box.s(), box.layout), ("привет", "uk"))
reset("ru"); type_("lzre. "); check("uk: a Ukrainian word in the Russian layout", (box.s(), box.layout), ("дякую ", "uk"))
# the keys that differ
reset(); type_("g`znm [dbkby "); check("uk: the apostrophe", box.s(), "п'ять хвилин ")
reset(); type_("]]? \\fyjr "); check("uk: ї and ґ", box.s(), "її, ґанок ")
reset("uk"); combo(e.KEY_CAPSLOCK); engine.caps = True; type_("]s'\\`"); check("uk: Caps Lock on its letters", box.s(), "ЇІЄҐ'")
combo(e.KEY_CAPSLOCK); engine.caps = False
reset(); type_("' gbnfyyz "); check("uk: «є» is fixed with its neighbour", box.s(), "є питання ")
reset(); type_("it's "); check("uk: an apostrophe in English stays", box.s(), "it's ")
# by hand
reset(); engine.set_enabled(False); type_("ghbdsn"); pause(); check("uk: Pause, a Ukrainian word", (box.s(), box.layout), ("привіт", "uk"))
pause(); check("uk: Pause again, back to Latin", (box.s(), box.layout), ("ghbdsn", "en"))
reset(); engine.set_enabled(False); type_("ghbdtn"); pause(); check("uk: Pause, a Russian word", (box.s(), box.layout), ("привет", "ru"))
reset("ru"); engine.set_enabled(False); type_("ghbdsn"); pause(); check("uk: Pause, Russian layout → Ukrainian", (box.s(), box.layout), ("привіт", "uk"))
reset(); type_("qwzx"); pause(); check("uk: Pause, no word anywhere: Russian first", (box.s(), detector.learned.get("qwzx")), ("йцяч", "ru"))
reset(); engine.session.prefer = "uk"; type_("qwzx"); pause()
check("uk: Pause, no word anywhere: the Cyrillic layout last typed in", (box.layout, detector.learned.get("qwzx")), ("uk", "uk"))
learned = dict(detector.learned)
reset(); detector.learned = learned; type_("qwzx "); check("uk: a learned word goes where it was sent", (box.s(), box.layout), ("йцяч ", "uk"))
# the layout last typed in decides for words spelled alike
reset(); type_("vfvf "); check("uk: a shared word from Latin: Russian first", (box.s(), box.layout), ("мама ", "ru"))
reset(); engine.session.prefer = "uk"; type_("vfvf "); check("uk: ... Ukrainian when it was typed in last", (box.s(), box.layout), ("мама ", "uk"))
# terminal answers from the Ukrainian layout
reset("uk", purpose="terminal"); type_("yes\n"); check("uk: terminal: нуі -> yes", (box.s(), box.layout), ("yes\n", "en"))
# at the fifth letter
engine.configure(Settings(excluded=("org.gnome.ptyxis", "jetbrains-*")))
reset(); type_("ghbds"); check("uk: mid: fixed at the fifth letter", (box.s(), box.layout), ("приві", "uk"))
type_("n zr "); check("uk: mid: the rest goes on", box.s(), "привіт як ")
reset(); type_("ghbdt"); check("uk: mid: Russian as before", (box.s(), box.layout), ("приве", "ru"))
reset("uk"); type_("hello"); check("uk: mid: uk → en", (box.s(), box.layout), ("hello", "en"))
reset("uk"); type_("ghbdsn "); check("uk: mid: the right layout is untouched", box.s(), "привіт ")
# switched off in the settings: the Ukrainian layout is any other layout again
engine.configure(Settings(excluded=("org.gnome.ptyxis", "jetbrains-*"), mid_word=False, ukrainian=False))
check("uk: off in the settings", detector.langs, ("ru", "en"))
reset("uk"); type_("hello "); check("uk: off: its layout is not followed", box.s(), "руддщ ")
reset(); type_("ghbdtn "); check("uk: off: Russian as ever", (box.s(), box.layout), ("привет ", "ru"))
# a Ukrainian layout and no Russian one: words spelled alike go to the Ukrainian
engine.configure(Settings(excluded=("org.gnome.ptyxis", "jetbrains-*"), mid_word=False))
engine.set_available({"en", "uk"}); check("uk: no Russian layout", detector.langs, ("uk", "en", "ru"))
reset(); type_("vfvf "); check("uk: no Russian layout: a shared word", (box.s(), box.layout), ("мама ", "uk"))
engine.set_available(None); check("uk: the desktop does not tell its layouts", detector.langs, ("ru", "en"))

print("FAILS:", fails)
sys.exit(1 if fails else 0)
