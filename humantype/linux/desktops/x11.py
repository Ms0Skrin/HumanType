"""Any desktop on X11 (Xfce, Cinnamon, MATE, LXQt, i3, KDE on X11...): the layout is the XKB group,
the focused window comes from the EWMH properties, the field from AT-SPI. libX11 through ctypes,
watched by polling every 100 ms: cheap, and no event loop of its own to fit in."""

import ctypes
import ctypes.util
import logging

from gi.repository import GLib

from . import atspi
from .common import Backend, lang_of

log = logging.getLogger(__name__)

XKB_USE_CORE_KBD = 0x0100
ANY_PROPERTY_TYPE = 0
POLL_MS = 100


class XkbStateRec(ctypes.Structure):
    _fields_ = [("group", ctypes.c_ubyte), ("locked_group", ctypes.c_ubyte), ("base_group", ctypes.c_ushort),
                ("latched_group", ctypes.c_ushort), ("mods", ctypes.c_ubyte), ("base_mods", ctypes.c_ubyte),
                ("latched_mods", ctypes.c_ubyte), ("locked_mods", ctypes.c_ubyte), ("compat_state", ctypes.c_ubyte),
                ("grab_mods", ctypes.c_ubyte), ("compat_grab_mods", ctypes.c_ubyte), ("lookup_mods", ctypes.c_ubyte),
                ("compat_lookup_mods", ctypes.c_ubyte), ("ptr_buttons", ctypes.c_ushort)]


def _lib():
    x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XInternAtom.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
                                     ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                     ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
                                     ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
    x.XFree.argtypes = [ctypes.c_void_p]
    x.XkbGetState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(XkbStateRec)]
    x.XkbLockGroup.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    x.XkbLockGroup.restype = ctypes.c_int
    x.XFlush.argtypes = [ctypes.c_void_p]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    return x


# A window may go away between two calls: X errors must not end the process.
_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda _d, _e: 0)


class X11(Backend):
    name = "X11"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.x = _lib()
        self.x.XSetErrorHandler(ctypes.cast(_ERROR_HANDLER, ctypes.c_void_p))
        self.dpy = self.x.XOpenDisplay(None)
        if not self.dpy:
            raise OSError("cannot open the X display")
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self._atoms: dict[str, int] = {}
        self._langs: list[str | None] = []
        self._rules = b""
        self._window = 0
        self.fields = atspi.make()
        self._poll()
        GLib.timeout_add(POLL_MS, self._poll)
        self._appeared()

    def _atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.x.XInternAtom(self.dpy, name.encode(), 0)
        return self._atoms[name]

    def _prop(self, window: int, name: str, length: int = 1024) -> tuple[int, bytes, int]:
        """(format, raw bytes, number of items)"""
        actual_type, fmt = ctypes.c_ulong(), ctypes.c_int()
        nitems, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = self.x.XGetWindowProperty(self.dpy, window, self._atom(name), 0, length, 0, ANY_PROPERTY_TYPE,
                                           ctypes.byref(actual_type), ctypes.byref(fmt), ctypes.byref(nitems),
                                           ctypes.byref(after), ctypes.byref(data))
        if status != 0 or not data.value:
            return 0, b"", 0
        try:
            size = {8: 1, 16: ctypes.sizeof(ctypes.c_short), 32: ctypes.sizeof(ctypes.c_long)}.get(fmt.value, 1)
            return fmt.value, ctypes.string_at(data.value, nitems.value * size), nitems.value
        finally:
            self.x.XFree(data)

    def _longs(self, window: int, name: str) -> list[int]:
        fmt, raw, n = self._prop(window, name)
        if fmt != 32:
            return []
        return list((ctypes.c_ulong * n).from_buffer_copy(raw))

    # --- layouts ---

    def _read_layouts(self) -> None:
        _fmt, raw, _n = self._prop(self.root, "_XKB_RULES_NAMES")
        if raw == self._rules:
            return
        self._rules = raw
        parts = raw.split(b"\0")
        layouts = parts[2].decode(errors="replace") if len(parts) > 2 else "us"
        self._langs = [lang_of(code) for code in layouts.split(",")]
        log.info("X11 layouts: %s", self._langs)

    def _group(self) -> int:
        state = XkbStateRec()
        self.x.XkbGetState(self.dpy, XKB_USE_CORE_KBD, ctypes.byref(state))
        return state.group

    def _current(self) -> str | None:
        group = self._group()
        return self._langs[group] if group < len(self._langs) else None

    def _switch(self, lang: str) -> bool:
        if lang not in self._langs:
            return False
        self.x.XkbLockGroup(self.dpy, XKB_USE_CORE_KBD, self._langs.index(lang))
        self.x.XSync(self.dpy, 0)
        return self._current() == lang

    # --- polling ---

    def _poll(self) -> bool:
        self._read_layouts()
        self._layout_now(self._current())
        active = self._longs(self.root, "_NET_ACTIVE_WINDOW")
        window = active[0] if active else 0
        fullscreen = self._atom("_NET_WM_STATE_FULLSCREEN") in self._longs(window, "_NET_WM_STATE") if window else False
        if window != self._window or fullscreen != self.fullscreen:
            self._window = window
            _fmt, raw, _n = self._prop(window, "WM_CLASS") if window else (0, b"", 0)
            instance, _, cls = raw.rstrip(b"\0").partition(b"\0")
            pid = self._longs(window, "_NET_WM_PID") if window else []
            self._window_now(instance.decode(errors="replace"), cls.decode(errors="replace"), fullscreen,
                             pid[0] if pid else 0)
        return GLib.SOURCE_CONTINUE
