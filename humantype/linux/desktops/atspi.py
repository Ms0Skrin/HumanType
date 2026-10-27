"""The focused text field through AT-SPI, the accessibility bus every desktop has: whether it is a
password or a terminal, and the text before the caret (GTK, Qt and Firefox tell it; Chromium and
Electron only with accessibility forced on).

Turning accessibility on (org.a11y.Status IsEnabled) makes apps started afterwards report to
the bus; it is what screen readers do, and costs the apps a little.
"""

import logging
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

try:  # at-spi2-core's typelib: present on almost every desktop, optional all the same
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi  # noqa: E402
except (ValueError, ImportError):
    Atspi = None

log = logging.getLogger(__name__)

BEFORE_CHARS = 200  # enough for any run of words we fix
TIMEOUT_MS = 150  # a hung app must not hold the keyboard
MAX_NODES = 600  # the lookup in a window gives up beyond this many nodes


@dataclass
class Field:
    purpose: str  # "normal", "password", "terminal"
    before: str | None  # the text before the caret, None when unknown or something is selected


class Fields:
    def __init__(self) -> None:
        self._focused: Atspi.Accessible | None = None
        enable_accessibility()
        Atspi.init()
        Atspi.set_timeout(TIMEOUT_MS, 2000)
        self._listener = Atspi.EventListener.new(self._on_event)
        self._listener.register("object:state-changed:focused")

    def _on_event(self, event) -> None:
        if event.detail1:
            self._focused = event.source
        elif self._focused is not None and event.source == self._focused:
            self._focused = None

    def focused(self, pid: int = 0) -> Field | None:
        """The field with the focus: the one the events named if it still has it, else looked up
        in the active window (some toolkits send no focus events to listeners that came later).
        pid: the process of the focused window, when the window manager tells it."""
        try:
            acc = self._focused
            if acc is None or not acc.get_state_set().contains(Atspi.StateType.FOCUSED):
                acc = self._find(pid)
                self._focused = acc
            return describe(acc) if acc is not None else None
        except GLib.Error as error:
            log.debug("at-spi: %s", error.message)
            self._focused = None
            return None

    def _find(self, pid: int) -> "Atspi.Accessible | None":
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None or (pid and app.get_process_id() != pid):
                continue
            for j in range(app.get_child_count()):
                frame = app.get_child_at_index(j)
                if frame is not None and frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                    found = _walk(frame, [MAX_NODES])
                    if found is not None:
                        return found
        return None


def _walk(acc, budget: list[int]):
    """Depth first through what is showing, for the focused node; at most MAX_NODES nodes."""
    budget[0] -= 1
    if budget[0] < 0:
        return None
    states = acc.get_state_set()
    if states.contains(Atspi.StateType.FOCUSED) and acc.get_role() not in (Atspi.Role.FRAME, Atspi.Role.WINDOW):
        return acc
    for i in range(min(acc.get_child_count(), 200)):
        child = acc.get_child_at_index(i)
        if child is None or not child.get_state_set().contains(Atspi.StateType.SHOWING):
            continue
        found = _walk(child, budget)
        if found is not None:
            return found
    return None


def describe(acc) -> Field:
    role = acc.get_role()
    if role == Atspi.Role.PASSWORD_TEXT:
        return Field("password", None)
    if role == Atspi.Role.TERMINAL:
        return Field("terminal", None)
    if acc.get_text_iface() is None or Atspi.Text.get_n_selections(acc) > 0:
        return Field("normal", None)
    caret = Atspi.Text.get_caret_offset(acc)
    if caret < 0:
        return Field("normal", None)
    return Field("normal", Atspi.Text.get_text(acc, max(0, caret - BEFORE_CHARS), caret))


def enable_accessibility() -> None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties", "Set",
                      GLib.Variant("(ssv)", ("org.a11y.Status", "IsEnabled", GLib.Variant("b", True))),
                      None, Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error as error:
        log.warning("cannot turn accessibility on: %s", error.message)


def make() -> Fields | None:
    if Atspi is None:
        log.warning("AT-SPI (gir Atspi 2.0) is not installed: no password or text checks")
        return None
    try:
        return Fields()
    except Exception:  # an AT-SPI that does not start must not stop the switcher
        log.exception("AT-SPI is not available: no password or text checks")
        return None
