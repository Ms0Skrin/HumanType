"""What to do with each key: follow the words, fix the ones typed in the wrong layout, undo, flip by hand.

The engine sees every key event of the grabbed keyboards before it goes on to the system and says
whether to swallow it. It knows nothing of devices or D-Bus: all it does to the world goes through
an `Output`, which the daemon implements for real and the tests with a fake text box.

A fix: the delimiter (space, Enter, Tab) is held back, the window is switched to the other layout,
the word is erased with Backspace and the same keys are pressed again, then the delimiter goes on.
Pressing the keys again, instead of typing characters, works in every app and keeps Caps Lock and
Shift exactly as the person meant them.
A word that clearly belongs to the other layout is fixed at its fifth letter, without waiting for
its end. Scroll Lock puts the case of the last word right, Shift+Scroll Lock transliterates it.
"""

import fnmatch
import logging
import time
from dataclasses import dataclass, replace
from typing import Callable, Protocol

from evdev import ecodes as e

from .. import textops
from ..detector import MID_LEN, SENSITIVITY, Detector, Session
from ..layout import EN_TO_RU, LANG_RU, RU_TO_EN
from .keymap import ALTS, CTRLS, DELIMITERS, METAS, MODIFIERS, SHIFTS, US_KEYS, char_of

log = logging.getLogger(__name__)

MAX_LINE = 300
MOUSE_BUTTONS = {e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_TOUCH}
SKIP_PURPOSES = {"password", "pin", "terminal"}
# A terminal asking [y/N]: these answers typed in the Russian layout (н, нуы, т, тщ) are fixed on Enter.
ANSWERS = {"y", "yes", "n", "no"}
TERMINALS = (
    "org.gnome.terminal", "org.gnome.ptyxis", "org.gnome.console", "kgx", "kitty", "alacritty",
    "org.wezfurlong.wezterm", "com.mitchellh.ghostty", "foot", "footclient", "org.kde.konsole",
    "xterm", "com.gexperts.tilix", "terminator", "io.elementary.terminal", "blackbox", "com.raggesilver.blackbox",
)


@dataclass(frozen=True)
class Press:
    code: int
    shift: bool
    caps: bool
    layout: str  # "en" | "ru": the layout the key was pressed in


SPACE = e.KEY_SPACE


@dataclass
class ShellState:
    """What the GNOME Shell extension knows about the focus."""

    layout: str | None  # "en" | "ru" | None for any other layout
    app: str  # app id without .desktop, or ""
    wm_class: str
    purpose: str  # "normal", "password", "pin", "terminal", "url", ...
    private: bool  # the field asked not to be remembered (incognito, password-like)
    fullscreen: bool
    # Asked about an expected text: the shell never hands the text itself out over D-Bus.
    text_known: bool = False  # the app tells what is before the caret (GTK, Qt, Firefox do)
    matches: bool = False  # ... and it ends with the expected text
    at_edge: bool = False  # ... and a word begins there (start of text, a space, a bracket...)
    common: int = 0  # how many of the last characters expected are there in fact
    common_edge: bool = False  # ... and a word begins before them


class Output(Protocol):
    def press(self, code: int) -> None: ...
    def release(self, code: int) -> None: ...
    def tap(self, code: int) -> None: ...
    def shell_state(self, expect: str = "", ignore_case: bool = False) -> ShellState | None: ...
    def set_layout(self, lang: str) -> bool: ...
    def note(self, kind: str, text: str) -> None: ...
    def history(self) -> bool: ...  # opens the clipboard history; False where there is none


@dataclass
class _Undo:
    run: list[Press]  # the keys as they were first typed
    old_layout: str
    original: str
    text: str
    keys: list[str]
    mid: bool = False  # fixed in the middle of the word: no space after it


@dataclass
class Settings:
    autoswitch: bool = True
    sensitivity: str = "normal"
    short_words: bool = True
    mid_word: bool = True  # fix a word at its fifth letter, without waiting for the space
    app_layouts: bool = True  # every program keeps its own layout
    layout_indicator: bool = True  # RU / EN at the caret when the layout changes (GNOME)
    skip_fullscreen: bool = True
    skip_terminals: bool = True  # False: fix words in terminals too (their [y/N] answers still work)
    notify: bool = True
    excluded: tuple[str, ...] = ()
    terminal_answers: bool = True  # y/yes/n/no typed in Russian in a terminal
    history: bool = True  # clipboard history (GNOME)
    history_hotkey: str = "ctrl+shift+v"  # left alone in terminals, where it pastes
    history_limit: int = 100
    history_images: bool = True


MOD_GROUPS = {"ctrl": CTRLS, "shift": SHIFTS, "alt": ALTS, "super": METAS}


def parse_hotkey(text: str) -> tuple[frozenset[str], int] | None:
    """"ctrl+shift+v" -> ({"ctrl", "shift"}, KEY_V); None when it does not read."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts or any(m not in MOD_GROUPS for m in parts[:-1]):
        return None
    code = getattr(e, f"KEY_{parts[-1].upper()}", None)
    return (frozenset(parts[:-1]), code) if isinstance(code, int) else None


def other(lang: str) -> str:
    return "ru" if lang == "en" else "en"


def is_terminal(state: ShellState) -> bool:
    names = {n.lower() for n in (state.app, state.wm_class) if n}
    return state.purpose == "terminal" or any(fnmatch.fnmatchcase(n, t) for n in names for t in TERMINALS)


def render(presses: list[Press], layout: str) -> str:
    """The text these keys give in `layout`."""
    out = []
    for p in presses:
        if p.code == SPACE:
            out.append(" ")
            continue
        c = char_of(p.code, p.shift, p.caps, layout) or ""
        out.append(c.translate(EN_TO_RU) if layout == "ru" else c)
    return "".join(out)


def typed(presses: list[Press]) -> str:
    """The text these keys gave, each in the layout it was pressed in."""
    return "".join(render([p], p.layout) for p in presses)


class Engine:
    def __init__(self, detector: Detector, out: Output, settings: Settings | None = None,
                 on_exceptions: Callable[[set[str]], None] | None = None) -> None:
        self.detector = detector
        self.session = Session(detector)
        self.out = out
        self.settings = settings or Settings()
        self.on_exceptions = on_exceptions or (lambda words: None)
        self.on_learned: Callable[[], None] = lambda: None  # the learned words changed: save them
        # For the statistics: "fixed" (by itself), "manual" (by a hotkey), "undone" and the word undone.
        self.count: Callable[..., None] = lambda kind, word="": None
        self._mid_old: str | None = None  # the word being typed was fixed in its middle: the layout it began in
        self._keep = False  # ... and that fix was undone: leave the word alone when it ends
        self.layout: str | None = None  # kept up to date by the shell's signals
        self.caps = False
        self.down: set[int] = set()  # keys held, as the system sees them
        self.line: list[Press] = []  # what was typed since the caret last jumped, spaces included
        self._swallowed: set[int] = set()  # pressed keys whose repeats and release are ours too
        self._undo: _Undo | None = None
        # What came before line[0]: "edge" a word boundary for sure (start, Enter, a new field);
        # "click" most likely one; "unknown" maybe the middle of a word (arrows, a repeat, a paste).
        self._origin = "edge"
        self._history_key = parse_hotkey(self.settings.history_hotkey) if self.settings.history else None
        self.suspended = False  # the clipboard history is open: its keys are none of ours

    # --- configuration ---

    def configure(self, settings: Settings) -> None:
        self.settings = settings
        self.detector.sensitivity = SENSITIVITY.get(settings.sensitivity, SENSITIVITY["normal"])
        self.detector.short_words = settings.short_words
        self._history_key = parse_hotkey(settings.history_hotkey) if settings.history else None
        self.session.reset()
        self._undo = None

    def set_enabled(self, on: bool) -> None:
        self.settings = replace(self.settings, autoswitch=on)
        self.session.reset()  # the caret stays where it is: keep following the line
        self._undo = None

    def reset(self, origin: str = "unknown") -> None:
        """The caret moved or the text changed behind our back: forget what was typed."""
        self.line.clear()
        self.session.reset()
        self._undo = None
        self._mid_old = None
        self._keep = False
        self._origin = origin

    def on_focus(self) -> None:
        self.reset("edge")

    # --- events ---

    def on_button(self) -> None:
        self.reset("click")  # a click or a touch may put the caret anywhere

    def on_key(self, code: int, value: int) -> bool:
        """Returns True to swallow the event. value: 1 press, 0 release, 2 kernel autorepeat."""
        if code in MOUSE_BUTTONS:
            if value == 1:
                self.reset("click")
            return False
        if value == 0:
            self.down.discard(code)
            if code in self._swallowed:
                self._swallowed.discard(code)
                return True
            return False
        if self.suspended:
            if value == 1:
                self.down.add(code)
            return False
        if value == 2:
            if code in self._swallowed:
                return True
            if code not in MODIFIERS:
                self.reset()  # the system repeats the key by itself: we cannot count the characters
            return False

        self.down.add(code)
        if code == e.KEY_CAPSLOCK:
            self.caps = not self.caps  # the LED from the system corrects this right after
            return False
        if code == e.KEY_SCROLLLOCK:
            self._swallowed.add(code)
            self._text_hotkey()
            return True
        if code in MODIFIERS:
            return False
        if code == e.KEY_PAUSE:
            self._swallowed.add(code)
            self._hotkey()
            return True
        if self._is_history_key(code) and self.out.history():
            self._swallowed.add(code)
            self.reset()
            return True
        if self.down & (CTRLS | ALTS | METAS):
            self.reset()  # a shortcut: copy, paste, undo... the text may be anything now
            return False
        if code == e.KEY_BACKSPACE:
            return self._backspace()
        if code in DELIMITERS:
            return self._delimiter(code)
        if code not in US_KEYS or self.layout is None:
            self.reset()  # arrows, Home, Delete, F-keys, numpad...: the caret or the text moved on
            return False
        self._undo = None
        self.line.append(Press(code, bool(self.down & SHIFTS), self.caps, self.layout))
        if len(self.line) > MAX_LINE:
            del self.line[0]
        return self._mid_word(code)

    # --- words ---

    def _mid_word(self, code: int) -> bool:
        """The key just added made the word MID_LEN letters long: if it clearly belongs to the other
        layout, the word so far is typed again in it, this key included. True to swallow the key."""
        if not self.settings.autoswitch or not self.settings.mid_word or self._keep:
            return False
        word = self.line[self._word_start():]
        if len(word) != MID_LEN or len({p.layout for p in word}) != 1:
            return False
        layout = word[0].layout
        fix = self.session.start("".join(char_of(p.code, p.shift, p.caps, layout) or "" for p in word), layout)
        if fix is None:
            return False
        start = self._run_start(len(fix.keys))
        run = self.line[start:]
        if len(run) != fix.erase or render(run, layout) != fix.original:
            self.session.reset()
            return False
        on_screen = fix.original[:-1]  # this key has not reached the window yet
        why = self._refuse(self.out.shell_state(on_screen), layout, on_screen, start == 0 and self._origin != "edge")
        if why:
            log.info("not fixing %r in the middle: %s", fix.original, why)
            self.session.reset()
            return False
        if not self.out.set_layout(fix.layout):
            self.session.reset()
            return False
        self.layout = fix.layout
        self._swallowed.add(code)
        self._retype(len(run) - 1, run)
        self.line[start:] = [replace(p, layout=fix.layout) for p in run]
        self._undo = _Undo(run, layout, fix.original, fix.text, fix.keys, mid=True)
        self._mid_old = layout
        self.count("fixed")
        if self.settings.notify:
            self.out.note("fix", f"{fix.original}…  →  {fix.text}…")
        return True

    def _erased(self) -> None:
        """A character was erased: a word cut below MID_LEN letters is no longer the one fixed in its middle."""
        if len(self.line) - self._word_start() < MID_LEN:
            self._mid_old = None
            self._keep = False

    def _word_start(self) -> int:
        for i in range(len(self.line) - 1, -1, -1):
            if self.line[i].code == SPACE:
                return i + 1
        return 0

    def _run_start(self, words: int) -> int:
        """Where the last `words` words of the line begin; spaces after the last word belong to the run."""
        i = len(self.line)
        while i and self.line[i - 1].code == SPACE:
            i -= 1
        for n in range(words):
            while i and self.line[i - 1].code != SPACE:
                i -= 1
            if n < words - 1:
                if i == 0:
                    break
                i -= 1  # the space between two words
        return i

    def _sync(self) -> None:
        """The app may change the text without a key: a chat clears its field after sending, a
        form is reset, a script fills it in. When it tells what is before the caret, keep only
        the part of the line that is still there."""
        if not self.line:
            return
        expect = typed(self.line)
        state = self.out.shell_state(expect)
        if state is None or not state.text_known or state.matches:
            return
        time.sleep(0.03)  # it may just be late with the last key
        state = self.out.shell_state(expect)
        if state is None or not state.text_known or state.matches:
            return
        log.info("the text changed behind us: keeping the last %d of %d keys", state.common, len(self.line))
        del self.line[:len(self.line) - state.common]
        self.session.reset()
        self._undo = None
        self._origin = "edge" if state.common_edge else "unknown"

    def _answer(self, code: int) -> bool:
        """A terminal asks [y/N] and the answer went in the Russian layout: нуы → yes, н → y, тщ → no."""
        run = self.line
        if (code == SPACE or not self.settings.autoswitch or not self.settings.terminal_answers
                or self._origin != "edge" or not run or len(run) > 3
                or any(p.layout != "ru" or p.code == SPACE for p in run)
                or render(run, "en").lower() not in ANSWERS):
            return False
        state = self.out.shell_state()
        if state is None or state.layout != "ru" or not is_terminal(state):
            return False
        if not self.out.set_layout("en"):
            return False
        self.layout = "en"
        self._retype(len(run), run)
        self.out.press(code)  # Enter goes on; its release follows from the keyboard
        log.info("terminal answer %r", render(run, "en"))
        if self.settings.notify:
            self.out.note("fix", f"{render(run, 'ru')}  →  {render(run, 'en')}")
        self._finish(code)
        return True

    def _is_history_key(self, code: int) -> bool:
        if self._history_key is None or code != self._history_key[1]:
            return False
        if {name for name, keys in MOD_GROUPS.items() if self.down & keys} != self._history_key[0]:
            return False
        state = self.out.shell_state()
        return state is None or not is_terminal(state)  # a terminal pastes with it

    def _delimiter(self, code: int) -> bool:
        if self._answer(code):
            return True
        if self.settings.autoswitch and self.line and self.line[-1].code != SPACE:
            self._sync()
        word = self.line[self._word_start():]
        if not word:
            # A second space or an empty line: nothing to judge, and the words before no longer
            # sit one space apart, so they cannot be fixed together with the next one.
            self.session.reset()
            self._finish(code)
            return False
        layouts = {p.layout for p in word}
        if not self.settings.autoswitch or len(layouts) != 1:
            self.session.reset()
            self._finish(code)
            return False
        layout = layouts.pop()
        keys = "".join(char_of(p.code, p.shift, p.caps, layout) for p in word)
        if self._keep:
            # Its fix in the middle was undone: the same "no" for the whole word, until the service restarts.
            self.detector.soft_exceptions = self.detector.soft_exceptions | {keys.lower()}
            self.session.reset()
            self._finish(code)
            return False
        mid_old = self._mid_old
        fix = self.session.word(keys, layout)
        if fix is None:
            self._finish(code)
            if mid_old and code == SPACE:
                # Fixed in its middle: a Backspace right after the space still takes the whole word back.
                old = [replace(p, layout=mid_old) for p in word]
                old_keys = "".join(char_of(p.code, p.shift, p.caps, mid_old) for p in old)
                self._undo = _Undo(old, mid_old, render(old, mid_old), render(word, layout), [old_keys])
            return False
        start = self._run_start(len(fix.keys))
        run = self.line[start:]
        if len(run) != fix.erase or render(run, layout) != fix.original:
            log.warning("run does not match the fix (%r vs %r), skipped", render(run, layout), fix.original)
            self.session.reset()
            self._finish(code)
            return False
        state = self.out.shell_state(fix.original)
        why = self._refuse(state, layout, fix.original, start == 0 and self._origin != "edge")
        if why:
            log.info("not fixing %r: %s", fix.original, why)
            self.session.reset()
            self._finish(code)
            return False
        if not self.out.set_layout(fix.layout):
            log.warning("could not switch the layout to %s", fix.layout)
            self.session.reset()
            self._finish(code)
            return False
        self.layout = fix.layout
        self._retype(len(run), run)
        self.out.press(code)  # the delimiter goes on; its release follows from the keyboard
        new_run = [replace(p, layout=fix.layout) for p in run]
        self.line[start:] = new_run
        self._finish(code)
        self._undo = _Undo(run, layout, fix.original, fix.text, fix.keys) if code == SPACE else None
        self.count("fixed")
        if self.settings.notify:
            self.out.note("fix", f"{fix.original}  →  {fix.text}")
        return True

    def _finish(self, code: int) -> None:
        self._undo = None
        self._mid_old = None
        self._keep = False
        if code == SPACE:
            self.line.append(Press(SPACE, False, self.caps, self.layout or "en"))
            if len(self.line) > MAX_LINE:
                del self.line[0]
        else:
            self.reset("edge")  # Enter or Tab: a new line, a sent message or another field

    def _refuse(self, state: ShellState | None, layout: str, original: str, check_edge: bool) -> str:
        """Why the fix must not be made here, or "". check_edge: the run starts where we lost track,
        so it must be made sure that a word begins there and we do not hold just its tail."""
        if state is None:
            return "GNOME Shell extension does not answer"
        if state.layout != layout:
            return f"the layout is {state.layout} now"
        skip = SKIP_PURPOSES if self.settings.skip_terminals else SKIP_PURPOSES - {"terminal"}
        if state.purpose in skip or state.private:
            return f"a {state.purpose or 'private'} field"
        if self.settings.skip_fullscreen and state.fullscreen:
            return "full screen"
        names = {n.lower() for n in (state.app, state.wm_class) if n}
        for pattern in self.settings.excluded:
            if any(fnmatch.fnmatchcase(n, pattern.lower()) for n in names):
                return f"excluded app {pattern}"
        if state.text_known and not state.matches:
            # The app tells what is before the caret and it is not our word: autocompletion,
            # a selection typed over, a caret moved by the keyboard of an IME...
            time.sleep(0.03)  # it may just be late with the last key
            again = self.out.shell_state(original)
            if again is None or not again.text_known or not again.matches:
                return "the text before the caret is not what was typed"
            state = again
        if check_edge:
            if state.text_known:
                if not state.at_edge:
                    return "the word began before we started following it"
            elif self._origin != "click":
                return "cannot tell where the word began"
        return ""

    # --- typing ---

    def _retype(self, erase: int, presses: list[Press]) -> None:
        """Erases `erase` characters and presses `presses` again, Shift as each was pressed with."""
        held = sorted(self.down & SHIFTS)
        for code in held:
            self.out.release(code)
        for _ in range(erase):
            self.out.tap(e.KEY_BACKSPACE)
        shift = False
        for p in presses:
            want = p.shift and p.code != SPACE
            if want != shift:
                (self.out.press if want else self.out.release)(e.KEY_LEFTSHIFT)
                shift = want
            self.out.tap(p.code)
        if shift:
            self.out.release(e.KEY_LEFTSHIFT)
        for code in held:
            self.out.press(code)

    def _backspace(self) -> bool:
        undo, self._undo = self._undo, None
        if undo is not None and self.line and (undo.mid or self.line[-1].code == SPACE):
            return self._do_undo(undo)
        if self.line:
            if self.line.pop().code == SPACE:
                self.session.reset()  # editing the word before: the run is broken
            self._erased()
        else:
            self.session.reset()
        return False

    def _do_undo(self, undo: _Undo) -> bool:
        tail = "" if undo.mid else " "
        state = self.out.shell_state(undo.text + tail, ignore_case=True)
        if state is not None and state.text_known and not state.matches:
            log.info("undo skipped: the text before the caret changed")
            return False  # let the Backspace through as an ordinary one
        if not self.out.set_layout(undo.old_layout):
            return False
        self.layout = undo.old_layout
        self._swallowed.add(e.KEY_BACKSPACE)
        self._retype(len(undo.run) + len(tail), undo.run)
        del self.line[-(len(undo.run) + len(tail)):]
        self.line.extend(undo.run)
        self.session.reset()
        self._mid_old = None
        added = {k.lower() for k in undo.keys}
        if undo.mid:
            # Only the beginning of a word: nothing to remember for good. The word itself is left
            # alone when it ends (see _delimiter).
            self._keep = True
            self.detector.soft_exceptions = self.detector.soft_exceptions | added
            self.count("undone")
            if self.settings.notify:
                self.out.note("undo", f"Оставил «{undo.original}…» как есть")
            return True
        self.detector.exceptions = self.detector.exceptions | added
        self.on_exceptions(added)
        if added & self.detector.learned.keys():  # undone: what was learned was wrong
            self.detector.learned = {k: v for k, v in self.detector.learned.items() if k not in added}
            self.on_learned()
        for word in added:
            self.count("undone", "" if " " in undo.original else word)
        if self.settings.notify:
            self.out.note("undo", f"Оставил «{undo.original}» как есть и запомнил")
        return True

    # --- by hand ---

    def _hotkey(self) -> None:
        mods = self.down - {e.KEY_PAUSE}
        if mods & ALTS:
            self.set_enabled(not self.settings.autoswitch)
            self.out.note("toggle", "Автопереключение включено" if self.settings.autoswitch else "Автопереключение выключено")
        elif mods & SHIFTS:
            self._flip(whole_line=True)
        elif not mods & (CTRLS | METAS):
            self._flip(whole_line=False)

    def _flip(self, whole_line: bool) -> None:
        """Pause: the word being typed (or the one just finished) goes into the other layout;
        Shift+Pause: everything typed since the caret last jumped."""
        self._sync()
        if not self.line:
            self.out.note("flip", "Нечего исправлять")
            return
        start = 0 if whole_line else self._run_start(1)
        run = self.line[start:]
        letters = [p for p in run if p.code != SPACE]
        if not letters:
            return
        source = letters[-1].layout
        target = other(source)
        if any(p.layout != source for p in letters):
            log.info("flip skipped: the text mixes layouts")
            return
        if not self.out.set_layout(target):
            return
        self.layout = target
        self._retype(len(run), run)
        self.line[start:] = [replace(p, layout=target) for p in run]
        self.session.reset()
        self._undo = None
        self._mid_old = None
        self.count("manual")
        if not whole_line and len(letters) == len(run):
            self._learn("".join(char_of(p.code, p.shift, p.caps, source) or "" for p in run).lower(), target)
        text = render(run, target).strip()
        self.out.note("flip", f"{'РУ' if target == 'ru' else 'EN'}: {text[:40]}")

    def _learn(self, keys: str, lang: str) -> None:
        """The person flipped this word by hand: from now on it is switched by itself."""
        if not keys or self.detector.learned.get(keys) == lang:
            return
        self.detector.learned = {**self.detector.learned, keys: lang}
        self.detector.soft_exceptions = self.detector.soft_exceptions - {keys}
        self.on_learned()
        if keys in self.detector.exceptions:
            self.detector.exceptions = self.detector.exceptions - {keys}
            self.on_exceptions(set())
        log.info("learned a word to switch to %s", lang)

    def _text_hotkey(self) -> None:
        """Scroll Lock: the case of the last word (пРИВЕТ → Привет, then round: привет → Привет → ПРИВЕТ);
        Shift+Scroll Lock: its transliteration (privet → привет and back)."""
        mods = self.down - {e.KEY_SCROLLLOCK}
        if mods & (CTRLS | ALTS | METAS):
            return
        if mods & SHIFTS:
            self._rewrite(textops.translit, "")
        else:
            self._rewrite(textops.recase, "Aa")

    def _presses(self, text: str, layout: str) -> list[Press] | None:
        """The keys that type `text` in `layout`, Caps Lock as it is now; None when some character has no key."""
        presses = []
        for c in text:
            if c == " ":
                presses.append(Press(SPACE, False, self.caps, layout))
                continue
            key = c if layout == "en" else c.translate(RU_TO_EN)
            found = next((Press(code, shift, self.caps, layout) for code in US_KEYS for shift in (False, True)
                          if char_of(code, shift, self.caps, layout) == key), None)
            if found is None or render([found], layout) != c:
                return None
            presses.append(found)
        return presses

    def _rewrite(self, transform, mark: str) -> None:
        """Types the word being typed (or the one just finished) again as `transform` makes it."""
        self._sync()
        start = self._run_start(1)
        run = self.line[start:]
        letters = [p for p in run if p.code != SPACE]
        if not letters or any(p.layout != letters[-1].layout for p in letters):
            self.out.note("flip", "Нечего менять")
            return
        source = letters[-1].layout
        text = render(run, source)
        new, lang = transform(text)
        target = source if lang is None else ("ru" if lang == LANG_RU else "en")
        presses = self._presses(new, target) if new != text else None
        if presses is None:
            self.out.note("flip", "Нечего менять")
            return
        if target != source and not self.out.set_layout(target):
            return
        self.layout = target
        self._retype(len(run), presses)
        self.line[start:] = presses
        self.session.reset()
        self._undo = None
        self._mid_old = None
        self.out.note("flip", f"{mark or ('РУ' if target == 'ru' else 'EN')}: {new.strip()[:40]}")
