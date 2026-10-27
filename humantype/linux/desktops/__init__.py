"""The desktop the service runs in: who switches the layout and tells about the focus.

GNOME has its own extension (humantype.linux.shell); KDE Plasma, Hyprland, Sway and any desktop
on X11 have a backend here. HUMANTYPE_DESKTOP=gnome|kde|hyprland|sway|x11 overrides the guess.
"""

import os


def detect() -> str:
    forced = os.environ.get("HUMANTYPE_DESKTOP", "").lower()
    if forced:
        return forced
    names = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    if "gnome" in names or "ubuntu" in names:
        return "gnome"
    if "kde" in names:
        return "kde"
    if "hyprland" in names or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if "sway" in names or os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("XDG_SESSION_TYPE") == "x11" or (os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")):
        return "x11"
    return ""


def connect(kind: str, on_layout, on_focus, on_appeared, on_vanished):
    if kind == "gnome":
        from ..shell import ShellClient
        return ShellClient(on_layout, on_focus, on_appeared, on_vanished)
    if kind == "kde":
        from .kde import Kde as cls
    elif kind == "hyprland":
        from .hyprland import Hyprland as cls
    elif kind == "sway":
        from .sway import Sway as cls
    elif kind == "x11":
        from .x11 import X11 as cls
    else:
        raise ValueError(f"unsupported desktop {kind!r}")
    return cls(on_layout, on_focus, on_appeared, on_vanished)
