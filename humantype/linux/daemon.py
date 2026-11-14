"""The humantype service: keyboards in, the engine in between, the twins out.

Runs a GLib main loop in one thread: device reads, D-Bus signals and timers all come through it,
so the engine never needs a lock. A watchdog thread makes sure a stuck loop cannot keep the
keyboards grabbed: if the loop stops answering, the process lets them go and exits.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import evdev
import gi
from evdev import ecodes as e

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .. import __version__  # noqa: E402
from ..clipstore import ClipStore  # noqa: E402
from ..detector import Detector  # noqa: E402
from ..layout import LABELS, LANGS  # noqa: E402
from ..stats import Stats  # noqa: E402
from . import config  # noqa: E402
from .devices import (Keyboard, can_read_any, is_keyboard, is_pointer, is_virtual, list_devices,  # noqa: E402
                      make_twin, open_device, wait_unpressed)
from .engine import Engine, ShellState  # noqa: E402
from . import desktops  # noqa: E402
from .shell import DaemonService  # noqa: E402
from .tray import Tray  # noqa: E402

log = logging.getLogger("humantype")

# The word lists: next to the package while developing, or where a package put them.
ASSETS_DIR = Path(os.environ.get("HUMANTYPE_ASSETS") or Path(__file__).resolve().parents[2] / "assets")
DATA_DIR = config.DATA_DIR
APP_LAYOUTS_FILE = DATA_DIR / "app_layouts.json"
HISTORY_OPEN_LIMIT = 120  # seconds: the engine takes the keys back even if the list never says it closed

KEY_DELAY = 0.0015  # between injected events: some apps lose keys that come all at once
LAYOUT_SETTLE = 0.01  # after the shell reports the new layout in force
WATCHDOG_LIMIT = 3.0
FORWARD_TYPES = {e.EV_KEY, e.EV_MSC, e.EV_REL, e.EV_LED, e.EV_SW, e.EV_SND}


class Daemon:
    def __init__(self) -> None:
        self.settings = config.load()
        detector = Detector(ASSETS_DIR / "dict")
        detector.exceptions = config.load_exceptions()
        detector.learned = config.load_learned()
        self.engine = Engine(detector, self, self.settings, self._exceptions_added)
        self.engine.configure(self.settings)
        self.engine.on_learned = lambda: config.save_learned(self.engine.detector.learned)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.stats = Stats(DATA_DIR / "stats.json")
        self.engine.count = self.stats.add
        # Every program keeps its own layout: remembered while its window has the focus, brought
        # back when it gets the focus again.
        self.app = ""  # the program in front, "" when it is not followed
        self.app_layouts: dict[str, str] = {}
        self._layouts_dirty = False
        self._switching = False  # the engine is switching the layout for a fix: no indicator for that
        try:
            raw = json.loads(APP_LAYOUTS_FILE.read_text(encoding="utf-8"))
            self.app_layouts = {str(k): v for k, v in raw.items() if v in LANGS}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("app_layouts.json is unreadable, starting anew")
        self.loop = GLib.MainLoop()
        self.keyboards: dict[str, Keyboard] = {}
        self.pointers: dict[str, tuple[evdev.InputDevice, int]] = {}
        self.waiting: set[str] = set()  # keyboards waiting for their keys to be let go
        self.current: Keyboard | None = None
        self.heartbeat = time.monotonic()
        self._stopping = False

        self.desktop = desktops.detect()
        self.shell = desktops.connect(self.desktop, self._on_layout, self._on_focus, self._on_shell,
                                      self._on_shell_gone)
        self.service = DaemonService(self.shell.bus, lambda: self.engine.settings.autoswitch, self.set_enabled,
                                     getattr(self.shell, "window_changed", None), self._history_call)
        self.store: ClipStore | None = None  # opened at the first copy
        self._history_timer = 0
        # Outside GNOME (which has the extension's icon) a tray icon; the installer puts its images
        # in the icon theme as humantype and humantype-off.
        self.tray = None
        if self.desktop != "gnome":
            self.tray = Tray(self.shell.bus, lambda: self.set_enabled(not self.engine.settings.autoswitch), "")
            self.tray.show(self.settings.autoswitch)

    # --- life cycle ---

    def run(self) -> int:
        if not can_read_any():
            log.error("no access to /dev/input: install the udev rule (linux/install.sh) and log in again")
            return 2
        if not os.access("/dev/uinput", os.W_OK):
            log.error("no access to /dev/uinput: install the udev rule (linux/install.sh)")
            return 2
        self.scan()
        monitor = Gio.File.new_for_path("/dev/input").monitor_directory(Gio.FileMonitorFlags.NONE, None)
        monitor.connect("changed", self._on_dev_changed)
        self._monitor = monitor
        conf_monitor = Gio.File.new_for_path(str(config.CONFIG_DIR)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        conf_monitor.connect("changed", self._on_config_changed)
        self._conf_monitor = conf_monitor
        GLib.timeout_add(3000, self._rescan)
        GLib.timeout_add(250, self._beat)
        GLib.timeout_add_seconds(30, self._save_data)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._quit)
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()
        log.info("HumanType %s started on %s; %s", __version__, self.shell.name,
                 "taking the keyboards" if self.shell.present else "waiting for it to answer")
        try:
            self.loop.run()
        finally:
            self.close()
        return 0

    def _quit(self) -> bool:
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _save_data(self) -> bool:
        self.stats.save()
        if self._layouts_dirty:
            try:
                tmp = APP_LAYOUTS_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(dict(sorted(self.app_layouts.items())), ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(APP_LAYOUTS_FILE)
                self._layouts_dirty = False
            except OSError:
                log.exception("app_layouts.json not saved")
        return GLib.SOURCE_CONTINUE

    def close(self) -> None:
        self._stopping = True
        self._save_data()
        for path in list(self.keyboards):
            self._drop_keyboard(path)
        for dev, watch in self.pointers.values():
            GLib.source_remove(watch)
            dev.close()
        self.pointers.clear()
        if self.tray:
            self.tray.close()
        self.service.close()

    def _beat(self) -> bool:
        self.heartbeat = time.monotonic()
        return GLib.SOURCE_CONTINUE

    def _watchdog(self) -> None:
        while not self._stopping:
            time.sleep(0.5)
            if time.monotonic() - self.heartbeat > WATCHDOG_LIMIT and not self._stopping:
                log.critical("main loop stuck for %.1f s: letting the keyboards go", time.monotonic() - self.heartbeat)
                for kb in list(self.keyboards.values()):
                    try:
                        kb.dev.ungrab()
                    except OSError:
                        pass
                logging.shutdown()
                os._exit(1)  # systemd starts us again

    # --- devices ---

    def scan(self) -> None:
        # Without the extension nothing can be fixed: keep our hands off the keyboards then.
        if not self.shell.present:
            return
        for path in list_devices():
            if path not in self.keyboards and path not in self.pointers and path not in self.waiting:
                self._add(path)

    def _rescan(self) -> bool:
        self.scan()
        return GLib.SOURCE_CONTINUE

    def _on_dev_changed(self, _monitor, file, _other, event) -> None:
        name = file.get_basename() or ""
        if not name.startswith("event"):
            return
        path = file.get_path()
        if event == Gio.FileMonitorEvent.CREATED:
            # udev sets the permissions a moment after the node appears.
            for delay in (200, 600, 1500):
                GLib.timeout_add(delay, self._add_later, path)
        elif event == Gio.FileMonitorEvent.DELETED:
            if path in self.keyboards:
                self._drop_keyboard(path)
            if path in self.pointers:
                dev, watch = self.pointers.pop(path)
                GLib.source_remove(watch)
                dev.close()

    def _add_later(self, path: str) -> bool:
        if self.shell.present and path not in self.keyboards and path not in self.pointers and path not in self.waiting:
            self._add(path)
        return GLib.SOURCE_REMOVE

    def _add(self, path: str) -> None:
        dev = open_device(path)
        if dev is None:
            return
        try:
            only = os.environ.get("HUMANTYPE_ONLY")  # tests: take only the keyboards with this in the name
            if is_keyboard(dev) and (only in dev.name if only else not is_virtual(dev)):
                self.waiting.add(path)
                self._try_grab(path, dev, 0)
            elif is_pointer(dev):
                watch = GLib.io_add_watch(dev.fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
                                          self._on_pointer, path)
                self.pointers[path] = (dev, watch)
            else:
                dev.close()
        except OSError:
            log.exception("cannot use %s", path)
            dev.close()

    def _try_grab(self, path: str, dev: evdev.InputDevice, tries: int) -> bool:
        if self._stopping or path not in self.waiting:  # stopping, or the extension went away meanwhile
            dev.close()
            return GLib.SOURCE_REMOVE
        if not wait_unpressed(dev):
            if tries == 0:
                log.info("waiting for the keys of %s to be let go", dev.name)
            GLib.timeout_add(30, self._try_grab, path, dev, tries + 1)
            return GLib.SOURCE_REMOVE
        self.waiting.discard(path)
        kb = Keyboard(dev)
        try:
            kb.twin = make_twin(dev)
            dev.grab()
            kb.grabbed = True
        except OSError as error:
            log.error("cannot take %s (%s): %s", dev.name, path, error)
            if kb.twin:
                kb.twin.close()
            dev.close()
            return GLib.SOURCE_REMOVE
        kb.watches = [
            GLib.io_add_watch(dev.fd, GLib.PRIORITY_HIGH, GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP, self._on_keyboard, path),
            GLib.io_add_watch(kb.twin.fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_twin, path),
        ]
        self.keyboards[path] = kb
        log.info("keyboard %s (%s)", dev.name, path)
        return GLib.SOURCE_REMOVE

    def _drop_keyboard(self, path: str) -> None:
        kb = self.keyboards.pop(path, None)
        if kb is None:
            return
        for watch in kb.watches:
            GLib.source_remove(watch)
        try:
            if kb.grabbed:
                kb.dev.ungrab()
        except OSError:
            pass
        kb.dev.close()
        if kb.twin:
            kb.twin.close()
        if self.current is kb:
            self.current = None
        self.engine.reset()
        log.info("keyboard gone: %s", path)

    # --- events ---

    def _on_keyboard(self, _fd, condition, path: str) -> bool:
        kb = self.keyboards.get(path)
        if kb is None:
            return GLib.SOURCE_REMOVE
        self.heartbeat = time.monotonic()
        try:
            events = list(kb.dev.read())
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            self._drop_keyboard(path)
            return GLib.SOURCE_REMOVE
        self.current = kb
        twin = kb.twin
        for ev in events:
            if ev.type == e.EV_KEY:
                try:
                    swallow = self.engine.on_key(ev.code, ev.value)
                except Exception:
                    log.exception("engine")
                    self.engine.reset()
                    swallow = False
                if not swallow:
                    twin.write(e.EV_KEY, ev.code, ev.value)
            elif ev.type == e.EV_SYN:
                twin.syn()
            elif ev.type in FORWARD_TYPES:
                twin.write(ev.type, ev.code, ev.value)
        return GLib.SOURCE_CONTINUE

    def _on_twin(self, _fd, _condition, path: str) -> bool:
        """The system sets the LEDs on the twin: pass them to the real keyboard."""
        kb = self.keyboards.get(path)
        if kb is None:
            return GLib.SOURCE_REMOVE
        try:
            events = list(kb.twin.read())
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            return GLib.SOURCE_REMOVE
        for ev in events:
            if ev.type == e.EV_LED:
                try:
                    kb.dev.set_led(ev.code, ev.value)
                except OSError:
                    pass
                if ev.code == e.LED_CAPSL:
                    self.engine.caps = bool(ev.value)
        return GLib.SOURCE_CONTINUE

    def _on_pointer(self, _fd, _condition, path: str) -> bool:
        entry = self.pointers.get(path)
        if entry is None:
            return GLib.SOURCE_REMOVE
        dev, _ = entry
        try:
            events = list(dev.read())
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            self.pointers.pop(path, None)
            dev.close()
            return GLib.SOURCE_REMOVE
        if any(ev.type == e.EV_KEY and ev.value == 1 and ev.code in (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_TOUCH)
               for ev in events):
            self.engine.on_button()
        return GLib.SOURCE_CONTINUE

    # --- the shell ---

    def _on_layout(self, lang: str | None) -> None:
        by_us = self._switching or lang == self.engine.layout
        self.engine.layout = lang
        if lang is None:
            return
        if lang not in self.engine.detector.langs:
            self._languages()  # a layout added since we last asked
        self._remember(lang)
        # A fix says what it did in its own note; the mark is for a layout changed by hand or
        # brought back for the program. Only GNOME can show it at the caret: elsewhere a note is
        # a desktop notification, too much for every switch.
        shell = getattr(self, "shell", None)  # a backend may tell its first layout while it is being made
        if shell is not None and not by_us and self.settings.layout_indicator and self.desktop == "gnome":
            shell.note("layout", LABELS.get(lang, lang.upper()))

    def _remember(self, lang: str) -> None:
        if self.settings.app_layouts and self.app and self.app_layouts.get(self.app) != lang:
            self.app_layouts[self.app] = lang
            self._layouts_dirty = True

    def _on_focus(self) -> None:
        self.engine.on_focus()
        if self.settings.app_layouts:
            GLib.idle_add(self._focus_layout)

    def _focus_layout(self) -> bool:
        """Another window has the focus: give its program the layout it had."""
        try:
            state = self.shell.state()
            if state is None:
                return GLib.SOURCE_REMOVE
            self._languages()
            # Games and full-screen video are left alone.
            self.app = "" if state.fullscreen else (state.app or state.wm_class).lower()
            want = self.app_layouts.get(self.app)
            if self.app and want and state.layout and want != state.layout:
                self.shell.set_layout(want)
            elif state.layout:
                self._remember(state.layout)
        except Exception:
            log.exception("the layout of the program in front")
        return GLib.SOURCE_REMOVE

    def _languages(self) -> None:
        """Asks the desktop which layouts there are: Ukrainian is followed only when it is one of them."""
        shell = getattr(self, "shell", None)  # a backend may tell its first layout while it is being made
        if shell is None:
            return
        try:
            self.engine.set_available(shell.languages())
        except Exception:
            log.exception("the layouts of the system")

    def _on_shell(self) -> None:
        self._languages()
        self.engine.layout = self.shell.layout()
        self.engine.reset()
        self.scan()

    def _on_shell_gone(self) -> None:
        for path in list(self.keyboards):
            self._drop_keyboard(path)
        self.waiting.clear()

    # --- Output, for the engine ---

    def _write(self, code: int, value: int) -> None:
        if self.current is None or self.current.twin is None:
            return
        self.current.twin.write(e.EV_KEY, code, value)
        self.current.twin.syn()
        time.sleep(KEY_DELAY)

    def press(self, code: int) -> None:
        self._write(code, 1)

    def release(self, code: int) -> None:
        self._write(code, 0)

    def tap(self, code: int) -> None:
        self._write(code, 1)
        self._write(code, 0)

    def shell_state(self, expect: str = "", ignore_case: bool = False) -> ShellState | None:
        return self.shell.state(expect, ignore_case)

    def set_layout(self, lang: str) -> bool:
        self._switching = True
        try:
            ok = self.shell.set_layout(lang)
        finally:
            self._switching = False
        if ok:
            time.sleep(LAYOUT_SETTLE)
        return ok

    def history(self) -> bool:
        show = getattr(self.shell, "show_history", None)  # GNOME only, for now
        if show is None or not self.settings.history:
            return False
        items = [(i.id, i.kind, " ".join(i.text.split())[:300], i.image or "", i.pinned) for i in self._store().items()]
        if not show(items):
            return False
        self.engine.suspended = True  # the keys go to the list now
        if self._history_timer:
            GLib.source_remove(self._history_timer)
        self._history_timer = GLib.timeout_add_seconds(HISTORY_OPEN_LIMIT, self._history_closed)
        return True

    def note(self, kind: str, text: str) -> None:
        if kind == "toggle":
            self._enabled_changed()
        self.shell.note(kind, text)

    # --- settings ---

    def set_enabled(self, on: bool) -> None:
        if on != self.engine.settings.autoswitch:
            self.engine.set_enabled(on)
            self._enabled_changed()
            self.shell.note("toggle", "Автопереключение включено" if on else "Автопереключение выключено")

    def _enabled_changed(self) -> None:
        self.settings = self.engine.settings
        config.save(self.settings)
        self.service.enabled_changed(self.settings.autoswitch)
        if self.tray:
            self.tray.show(self.settings.autoswitch)

    # --- clipboard history ---

    def _store(self) -> ClipStore:
        if self.store is None:
            self.store = ClipStore(DATA_DIR / "history.db", DATA_DIR / "images", self.settings.history_limit)
        return self.store

    def _history_call(self, method: str, args: tuple) -> tuple | None:
        if method == "ClipboardAdd":
            text, png = args
            try:
                if self.settings.history:
                    if text.strip():
                        self._store().add_text(text)
                    elif png and self.settings.history_images:
                        data = Path(png).read_bytes()
                        self._store().add_png(data, png_size(data))
            finally:
                if png:
                    Path(png).unlink(missing_ok=True)
        elif method == "HistoryGet":
            item = next((i for i in self._store().items() if i.id == args[0]), None)
            if item is None:
                return ("", "", "")
            return (item.kind, item.text if item.kind == "text" else "", item.image or "")
        elif method == "HistoryPin":
            self._store().toggle_pin(args[0])
        elif method == "HistoryDelete":
            self._store().delete(args[0])
        elif method == "HistoryDone":
            self._history_closed()
            if args[0]:
                # The extension has put the item on the clipboard and the list is gone: paste it
                # once the window the list came over has the keyboard again.
                GLib.timeout_add(60, self._paste)
        return None

    def _history_closed(self) -> bool:
        self.engine.suspended = False
        self.engine.reset()
        if self._history_timer:
            GLib.source_remove(self._history_timer)
            self._history_timer = 0
        return GLib.SOURCE_REMOVE

    def _paste(self) -> bool:
        self.press(e.KEY_LEFTCTRL)
        self.tap(e.KEY_V)
        self.release(e.KEY_LEFTCTRL)
        return GLib.SOURCE_REMOVE

    def _exceptions_added(self, _words: set[str]) -> None:
        config.save_exceptions(self.engine.detector.exceptions)

    def _on_config_changed(self, _monitor, file, _other, event) -> None:
        if event not in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.RENAMED,
                         Gio.FileMonitorEvent.MOVED_IN):
            return
        name = file.get_basename()
        if name == config.SETTINGS_FILE.name:
            settings = config.load()
            if settings != self.engine.settings:
                log.info("settings reloaded")
                self.settings = settings
                self.engine.configure(settings)
                if self.store is not None:
                    self.store.set_limit(settings.history_limit)
                self.service.enabled_changed(settings.autoswitch)
                if self.tray:
                    self.tray.show(settings.autoswitch)
        elif name == config.EXCEPTIONS_FILE.name:
            self.engine.detector.exceptions = config.load_exceptions()
        elif name == config.LEARNED_FILE.name:
            self.engine.detector.learned = config.load_learned()


def png_size(data: bytes) -> str:
    """'1920×1080' from a PNG's header, '' when it is not one."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return ""
    return f"{int.from_bytes(data[16:20], 'big')}×{int.from_bytes(data[20:24], 'big')}"


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    if not desktops.detect():
        log.error("this desktop is not supported: GNOME, KDE Plasma, Hyprland, Sway, niri or any desktop on X11 "
                  "(XDG_CURRENT_DESKTOP=%r); HUMANTYPE_DESKTOP=... picks one by hand",
                  os.environ.get("XDG_CURRENT_DESKTOP", ""))
        return 2
    return Daemon().run()
