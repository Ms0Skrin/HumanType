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
LEARNED_FILE = DATA_DIR / "autoswitch_learned.txt"  # words flipped by hand: switched by themselves
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HumanType"
VK_PAUSE = 0x13
VK_SCROLL = 0x91  # Scroll Lock: the key beside Pause

# Terminals and consoles: words are fixed there like anywhere else, and y/yes/n/no typed in
# Russian are fixed on Enter even when the terminal is excluded.
TERMINAL_APPS = [
    "cmd.exe", "powershell.exe", "pwsh.exe", "WindowsTerminal.exe", "OpenConsole.exe", "conhost.exe",
    "mintty.exe", "putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe",
]
TERMINALS = {name.lower() for name in TERMINAL_APPS}

# Commands typed in the Russian layout at the start of a terminal line are fixed: ды → ls, пше → git.
# Left out: those whose Russian reading is a word someone may well type there (uv = «гм», ps = «зы»).
TERMINAL_COMMANDS = """
ls cd git dir cls cat npm npx pip pip3 ssh exit clear code python python3 py node docker kubectl cargo go make
sudo vim nvim nano claude winget choco scoop wsl ping ipconfig tasklist taskkill echo mkdir rm rmdir cp mv touch
grep curl wget yarn pnpm poetry pytest gh pwd whoami tree start explorer notepad cmake rustc rustup dotnet java
javac mvn gradle php composer ruby gem bundle deno bun terraform helm ansible systemctl journalctl apt pacman dnf
yay paru brew top htop btop du kill man less head tail chmod chown tar zip unzip scp rsync history alias export
setx where which sfc dism shutdown netstat nslookup tracert powershell pwsh bash zsh fish tmux ollama ffmpeg
""".split()

# Programs where automatic switching stays off: typing a fix there is wrong or unsafe.
DEFAULT_EXCLUDED = [
    # password managers
    "KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe",
    # remote desktops and virtual machines: the keys belong to another computer
    "mstsc.exe", "VirtualBoxVM.exe", "vmware.exe", "vmplayer.exe",
]

# Excluded by default until settings version 2; dropped from saved settings once, so switching
# works in terminals and code editors for those who never changed the list by hand.
_OLD_DEFAULTS = {name.lower() for name in [
    *TERMINAL_APPS,
    "Code.exe", "Cursor.exe", "devenv.exe", "sublime_text.exe", "idea64.exe", "pycharm64.exe",
    "webstorm64.exe", "rider64.exe", "clion64.exe", "goland64.exe", "phpstorm64.exe", "studio64.exe",
]}
SETTINGS_VERSION = 2


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
    fix_case: Hotkey = field(default_factory=lambda: Hotkey(0, VK_SCROLL))  # пРИВЕТ → Привет
    fix_translit: Hotkey = field(default_factory=lambda: Hotkey(MOD_SHIFT, VK_SCROLL))  # privet → привет
    history_popup: Hotkey = field(default_factory=lambda: Hotkey(MOD_CONTROL | MOD_SHIFT, ord("V")))
    history_limit: int = 100
    save_images: bool = True
    switch_layout: bool = True
    show_toasts: bool = True
    autostart: bool = field(default_factory=lambda: getattr(sys, "frozen", False))  # on by default once installed
    run_as_admin: bool = True  # so admin windows (an elevated terminal) are reached too
    app_layouts: bool = True  # every program keeps its own layout
    layout_indicator: bool = True  # RU / EN at the caret when the layout changes
    # automatic switching
    autoswitch: bool = True
    toggle_autoswitch: Hotkey = field(default_factory=lambda: Hotkey(MOD_ALT, VK_PAUSE))
    autoswitch_sensitivity: str = "normal"  # careful | normal | bold
    autoswitch_short_words: bool = True
    autoswitch_mid_word: bool = True  # fix a word at its fifth letter, without waiting for the space
    autoswitch_skip_fullscreen: bool = True
    autoswitch_notify: bool = True
    autoswitch_terminal_answers: bool = True  # н, нуы, т, тщ in a terminal → y, yes, n, no on Enter
    autoswitch_ukrainian: bool = True  # Ukrainian too, when its layout is installed
    autoswitch_excluded: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED))
    version: int = SETTINGS_VERSION

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
        if int(raw.get("version", 1)) < 2:
            settings.autoswitch_excluded = [n for n in settings.autoswitch_excluded if n.lower() not in _OLD_DEFAULTS]
            settings.version = SETTINGS_VERSION
            try:
                settings.save()
            except OSError:
                log.exception("settings.json could not be updated")
        return settings

    def save(self) -> None:
        # Through a temporary file: a crash or power cut mid-write must not leave half a file,
        # which would silently put every setting back to its default.
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)


def _launch_command() -> str:
    from .elevation import launch_parts

    program, arguments = launch_parts()
    return f'"{program}" {arguments}'.strip()


def _set_run_key(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


_TASK_STATE = DATA_DIR / "task.json"


def set_autostart(on: bool, run_as_admin: bool = True) -> None:
    """Running elevated, autostart goes through the admin task (it also starts later launches
    elevated); otherwise through the Run key."""
    from . import elevation

    if not elevation.is_admin():
        _set_run_key(on)
        return
    if not run_as_admin:
        if elevation.task_installed():
            elevation.remove_task()
        _TASK_STATE.unlink(missing_ok=True)
        _set_run_key(on)
        return
    want = {"command": _launch_command(), "logon": on}
    try:
        have = json.loads(_TASK_STATE.read_text(encoding="utf-8"))
    except Exception:
        have = None
    if have != want or not elevation.task_installed():
        if not elevation.install_task(on):
            _set_run_key(on)  # the task failed: at least start with Windows
            return
        _TASK_STATE.write_text(json.dumps(want), encoding="utf-8")
    _set_run_key(False)  # the task starts it; a Run entry would start a second, non-elevated copy
