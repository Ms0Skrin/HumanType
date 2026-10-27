"""Settings kept in data/settings.json, and autostart through the registry's Run key."""

import json
import logging
import sys
import winreg
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT
from .paths import DATA_DIR, app_dir

log = logging.getLogger(__name__)

SETTINGS_FILE = DATA_DIR / "settings.json"
EXCEPTIONS_FILE = DATA_DIR / "autoswitch_exceptions.txt"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HumanType"
VK_PAUSE = 0x13

# Programs where automatic switching stays off.
DEFAULT_EXCLUDED = [
    # terminals and consoles
    "cmd.exe", "powershell.exe", "pwsh.exe", "WindowsTerminal.exe", "OpenConsole.exe", "conhost.exe",
    "mintty.exe", "putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe",
    # code editors and IDEs
    "Code.exe", "Cursor.exe", "devenv.exe", "sublime_text.exe", "idea64.exe", "pycharm64.exe",
    "webstorm64.exe", "rider64.exe", "clion64.exe", "goland64.exe", "phpstorm64.exe", "studio64.exe",
    # password managers
    "KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe",
    # remote desktops and virtual machines
    "mstsc.exe", "VirtualBoxVM.exe", "vmware.exe", "vmplayer.exe",
]


@dataclass
class Hotkey:
    mods: int = 0
    vk: int = 0  # 0 means switched off

    @property
    def is_set(self) -> bool:
        return self.vk != 0


@dataclass
class Settings:
    fix_word: Hotkey = field(default_factory=lambda: Hotkey(0, VK_PAUSE))
    fix_line: Hotkey = field(default_factory=lambda: Hotkey(MOD_SHIFT, VK_PAUSE))
    history_popup: Hotkey = field(default_factory=lambda: Hotkey(MOD_CONTROL | MOD_SHIFT, ord("V")))
    history_limit: int = 100
    save_images: bool = True
    switch_layout: bool = True
    show_toasts: bool = True
    autostart: bool = field(default_factory=lambda: getattr(sys, "frozen", False))  # on by default once installed
    # automatic switching
    autoswitch: bool = True
    toggle_autoswitch: Hotkey = field(default_factory=lambda: Hotkey(MOD_ALT, VK_PAUSE))
    autoswitch_sensitivity: str = "normal"  # careful | normal | bold
    autoswitch_short_words: bool = True
    autoswitch_skip_fullscreen: bool = True
    autoswitch_notify: bool = True
    autoswitch_excluded: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED))

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return settings
        except Exception:
            log.exception("settings.json is unreadable, using defaults")
            return settings
        for name, value in raw.items():
            if not hasattr(settings, name):
                continue
            if isinstance(getattr(settings, name), Hotkey):
                value = Hotkey(int(value.get("mods", 0)), int(value.get("vk", 0)))
            setattr(settings, name, value)
        return settings

    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{app_dir() / "run.pyw"}"'


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
