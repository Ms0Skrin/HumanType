"""The humantype service: keyboards in, the engine in between, the twins out.

Runs a GLib main loop in one thread: device reads, D-Bus signals and timers all come through it,
so the engine never needs a lock. A watchdog thread makes sure a stuck loop cannot keep the
keyboards grabbed: if the loop stops answering, the process lets them go and exits.
"""

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

from ..detector import Detector  # noqa: E402
from . import config  # noqa: E402
from .devices import (Keyboard, can_read_any, is_keyboard, is_pointer, is_virtual, list_devices,  # noqa: E402
                      make_twin, open_device, wait_unpressed)
from .engine import Engine, ShellState  # noqa: E402
from .shell import DaemonService, ShellClient  # noqa: E402

log = logging.getLogger("humantype")

# The word lists: next to the package while developing, or where a package put them.
ASSETS_DIR = Path(os.environ.get("HUMANTYPE_ASSETS") or Path(__file__).resolve().parents[2] / "assets")

KEY_DELAY = 0.0015  # between injected events: some apps lose keys that come all at once
LAYOUT_SETTLE = 0.01  # after the shell reports the new layout in force
WATCHDOG_LIMIT = 3.0
FORWARD_TYPES = {e.EV_KEY, e.EV_MSC, e.EV_REL, e.EV_LED, e.EV_SW, e.EV_SND}


class Daemon:
    def __init__(self) -> None:
        self.settings = config.load()
        detector = Detector(ASSETS_DIR / "dict")
        detector.exceptions = config.load_exceptions()
        self.engine = Engine(detector, self, self.settings, self._exceptions_added)
        self.engine.configure(self.settings)
        self.loop = GLib.MainLoop()
        self.keyboards: dict[str, Keyboard] = {}
        self.pointers: dict[str, tuple[evdev.InputDevice, int]] = {}
        self.waiting: set[str] = set()  # keyboards waiting for their keys to be let go
        self.current: Keyboard | None = None
        self.heartbeat = time.monotonic()
        self._stopping = False

        self.shell = ShellClient(self._on_layout, self.engine.on_focus, self._on_shell, self._on_shell_gone)
        self.service = DaemonService(self.shell.bus, lambda: self.engine.settings.autoswitch, self.set_enabled)

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
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._quit)
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()
        log.info("started; %s", "taking the keyboards" if self.shell.present else "waiting for the GNOME Shell extension")
        try:
            self.loop.run()
        finally:
            self.close()
        return 0

    def _quit(self) -> bool:
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def close(self) -> None:
        self._stopping = True
        for path in list(self.keyboards):
            self._drop_keyboard(path)
        for dev, watch in self.pointers.values():
            GLib.source_remove(watch)
            dev.close()
        self.pointers.clear()
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
        self.engine.layout = lang

    def _on_shell(self) -> None:
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
        ok = self.shell.set_layout(lang)
        if ok:
            time.sleep(LAYOUT_SETTLE)
        return ok

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
                self.service.enabled_changed(settings.autoswitch)
        elif name == config.EXCEPTIONS_FILE.name:
            self.engine.detector.exceptions = config.load_exceptions()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    return Daemon().run()
