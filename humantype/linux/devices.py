"""The keyboards: found, grabbed, mirrored through uinput; mice and touchpads only listened to.

Each grabbed keyboard gets a virtual twin with the same keys and LEDs. Everything the keyboard
sends goes on through the twin unless the engine swallows it; the twin is also what types the
fixes. The system sets the Caps Lock LED on the twin, and we pass it to the real keyboard.
"""

import errno
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

import evdev
from evdev import ecodes as e

log = logging.getLogger(__name__)

TWIN_PREFIX = "HumanType "
KEYBOARD_KEYS = {e.KEY_A, e.KEY_Z, e.KEY_SPACE, e.KEY_ENTER}
POINTER_BUTTONS = {e.BTN_LEFT, e.BTN_TOUCH}


@dataclass
class Keyboard:
    dev: evdev.InputDevice
    twin: evdev.UInput | None = None
    grabbed: bool = False
    watches: list[int] = field(default_factory=list)


def is_keyboard(dev: evdev.InputDevice) -> bool:
    keys = set(dev.capabilities().get(e.EV_KEY, []))
    return KEYBOARD_KEYS <= keys and not dev.name.startswith(TWIN_PREFIX)


def is_virtual(dev: evdev.InputDevice) -> bool:
    """Made through uinput by some program (dictation, remote desktop, ydotool): what it types is
    already right, so it is not ours to fix."""
    name = os.path.basename(dev.path)
    return "/devices/virtual/" in os.path.realpath(f"/sys/class/input/{name}")


def is_pointer(dev: evdev.InputDevice) -> bool:
    keys = set(dev.capabilities().get(e.EV_KEY, []))
    return bool(keys & POINTER_BUTTONS) and not dev.name.startswith(TWIN_PREFIX)


def make_twin(dev: evdev.InputDevice) -> evdev.UInput:
    caps = dev.capabilities()
    # No EV_REP: the kernel would autorepeat the twin on its own; the system repeats keys itself anyway.
    events = {t: codes for t, codes in caps.items() if t not in (e.EV_SYN, e.EV_FF, e.EV_ABS, e.EV_REP)}
    events.setdefault(e.EV_KEY, [])
    # The fixes press these whatever the keyboard has.
    events[e.EV_KEY] = sorted(set(events[e.EV_KEY]) | {e.KEY_BACKSPACE, e.KEY_LEFTSHIFT, e.KEY_SPACE})
    return evdev.UInput(events, name=f"{TWIN_PREFIX}{dev.name}"[:79], vendor=dev.info.vendor,
                        product=dev.info.product, version=dev.info.version, bustype=dev.info.bustype)


def wait_unpressed(dev: evdev.InputDevice) -> bool:
    """Grabbing a keyboard while a key is down leaves that key stuck for the system."""
    try:
        return not dev.active_keys()
    except OSError:
        return False


def list_devices() -> list[str]:
    try:
        return sorted(os.path.join("/dev/input", n) for n in os.listdir("/dev/input") if n.startswith("event"))
    except OSError:
        return []


def open_device(path: str) -> evdev.InputDevice | None:
    try:
        return evdev.InputDevice(path)
    except PermissionError:
        return None
    except OSError as error:
        if error.errno not in (errno.ENOENT, errno.ENODEV):
            log.warning("cannot open %s: %s", path, error)
        return None


def can_read_any() -> bool:
    """Whether this user may read input devices at all (the udev rule or the input group)."""
    paths = list_devices()
    return any(os.access(p, os.R_OK) for p in paths)


Handler = Callable[[Keyboard, list[evdev.InputEvent]], None]
