"""What every desktop backend shares: the D-Bus session, the callbacks into the daemon, short
notes as desktop notifications, the text check and the layout names.

A backend tells the daemon four things (see `Backend`): which layout is in force and when it
changes, when the focus moves, how to switch the layout, and what it knows of the focused field.
"""

import logging
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from ..engine import ShellState  # noqa: E402

log = logging.getLogger(__name__)

WORD_EDGE = " \t\n([{«\"'—–-/"
# xkb layout codes and the human names some desktops give instead.
XKB_LANG = {"us": "en", "gb": "en", "ru": "ru"}
NAME_LANG = (("russian", "ru"), ("рус", "ru"), ("english", "en"), ("англ", "en"))


def lang_of(layout: str) -> str | None:
    """'us', 'us(intl)', 'ru', 'Russian', 'English (US)' → 'en' / 'ru' / None."""
    code = layout.strip().split("(")[0].split("+")[0].strip().lower()
    if code in XKB_LANG:
        return XKB_LANG[code]
    low = layout.lower()
    for part, lang in NAME_LANG:
        if part in low:
            return lang
    return None


def text_check(before: str, expect: str, ignore_case: bool) -> dict:
    """The answer about the text before the caret, as the GNOME extension gives it: never the
    text itself, only whether it ends with what was typed and how much of it is there."""
    if ignore_case:
        before, expect = before.lower(), expect.lower()
    matches = before.endswith(expect)
    prev = before[: len(before) - len(expect)][-1:] if matches else ""
    common = 0
    while common < min(len(before), len(expect)) and before[-1 - common] == expect[-1 - common]:
        common += 1
    prev_common = before[: len(before) - common][-1:]
    return {"text_known": True, "matches": matches, "at_edge": matches and (not prev or prev in WORD_EDGE),
            "common": common, "common_edge": not prev_common or prev_common in WORD_EDGE}


class Backend:
    """The daemon talks to this. Subclasses fill in `_layout`, `_switch` and the window facts."""

    name = "desktop"

    def __init__(self, on_layout: Callable[[str | None], None], on_focus: Callable[[], None],
                 on_appeared: Callable[[], None], on_vanished: Callable[[], None] = lambda: None) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.present = False
        self._on_layout = on_layout
        self._on_focus = on_focus
        self._on_appeared = on_appeared
        self._on_vanished = on_vanished
        self._lang: str | None = None
        self._note_id = 0
        # What the window manager says of the focused window; the field comes from AT-SPI.
        self.app = ""
        self.wm_class = ""
        self.fullscreen = False
        self.pid = 0
        self.fields = None  # an atspi.Fields, when accessibility is there

    # --- for subclasses ---

    def _appeared(self) -> None:
        if not self.present:
            self.present = True
            log.info("%s is here", self.name)
            self._on_appeared()

    def _vanished(self) -> None:
        if self.present:
            self.present = False
            log.warning("%s is gone", self.name)
            self._on_layout(None)
            self._on_vanished()

    def _layout_now(self, lang: str | None) -> None:
        if lang != self._lang:
            self._lang = lang
            self._on_layout(lang)

    def _window_now(self, app: str, wm_class: str, fullscreen: bool, pid: int = 0) -> None:
        changed = (app, wm_class, pid) != (self.app, self.wm_class, self.pid)
        self.app, self.wm_class, self.fullscreen, self.pid = app, wm_class, fullscreen, pid
        if changed:
            self._on_focus()

    def _current(self) -> str | None:
        """The layout in force, asked afresh."""
        return self._lang

    def _switch(self, lang: str) -> bool:
        raise NotImplementedError

    # --- for the daemon ---

    def layout(self) -> str | None:
        return self._current()

    def set_layout(self, lang: str) -> bool:
        if not self.present:
            return False
        if self._current() == lang:
            return True
        try:
            ok = self._switch(lang)
        except (GLib.Error, OSError) as error:
            log.warning("switching to %s failed: %s", lang, error)
            return False
        if ok:
            self._layout_now(lang)
        return ok

    def state(self, expect: str = "", ignore_case: bool = False) -> ShellState | None:
        if not self.present:
            return None
        state = ShellState(layout=self._current(), app=self.app, wm_class=self.wm_class, purpose="normal",
                           private=False, fullscreen=self.fullscreen)
        if self.fields is not None:
            field = self.fields.focused(self.pid)
            if field is not None:
                state.purpose = field.purpose
                if field.before is not None:
                    for k, v in text_check(field.before, expect, ignore_case).items():
                        setattr(state, k, v)
        return state

    def note(self, kind: str, text: str) -> None:
        """A short desktop notification in place of the GNOME extension's label at the caret."""
        if not self.present:
            return
        hints = {"transient": GLib.Variant("b", True), "urgency": GLib.Variant("y", 0),
                 "x-canonical-private-synchronous": GLib.Variant("s", "humantype")}
        self.bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("HumanType", self._note_id, "humantype", "HumanType", text,
                                                       [], hints, 1500)),
                      GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000, None, self._noted)

    def _noted(self, bus, result) -> None:
        try:
            self._note_id = bus.call_finish(result).unpack()[0]
        except GLib.Error as error:
            log.debug("notification: %s", error.message)
