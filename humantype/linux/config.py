"""Settings in ~/.config/humantype: settings.json, the words never to switch (exceptions.txt) and
the words always to switch (learned.txt); what the service gathers itself is in ~/.local/share/humantype."""

import json
import logging
import os
from dataclasses import asdict, fields
from pathlib import Path

from .engine import TERMINALS, Settings

log = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "humantype"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
EXCEPTIONS_FILE = CONFIG_DIR / "exceptions.txt"
LEARNED_FILE = CONFIG_DIR / "learned.txt"  # words flipped by hand with Pause: "keys ru" or "keys en" a line
# The clipboard history (history.db and its pictures), the statistics, the layouts of the programs.
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "humantype"

# Apps where automatic switching stays off: app ids and window classes, lowercase, * allowed.
# Terminals mostly say so themselves (the field's purpose is "terminal"); these are for the rest.
DEFAULT_EXCLUDED = (
    *TERMINALS,
    # code editors and IDEs
    "code", "code-oss", "codium", "vscodium", "com.visualstudio.code", "cursor", "dev.zed.zed", "zed",
    "jetbrains-*", "sublime_text", "com.sublimetext.three", "org.gnome.builder", "nvim-qt", "neovide",
    # password managers
    "org.keepassxc.keepassxc", "keepassxc", "1password", "bitwarden", "com.bitwarden.desktop",
    # remote desktops and virtual machines
    "org.gnome.connections", "org.remmina.remmina", "virt-manager", "org.gnome.boxes", "virtualbox machine",
    "vmware",
)


def load() -> Settings:
    settings = Settings(excluded=DEFAULT_EXCLUDED)
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        save(settings)
        return settings
    except Exception:
        log.exception("settings.json is unreadable, using defaults")
        return settings
    known = {f.name for f in fields(Settings)}
    values = {k: v for k, v in raw.items() if k in known}
    if "excluded" in values:
        values["excluded"] = tuple(str(x) for x in values["excluded"])
    return Settings(**{**asdict(settings), **values})


def save(settings: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    data["excluded"] = list(settings.excluded)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def load_exceptions() -> set[str]:
    try:
        return {w.lower() for w in EXCEPTIONS_FILE.read_text(encoding="utf-8").split()}
    except FileNotFoundError:
        return set()


def save_exceptions(words: set[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EXCEPTIONS_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    tmp.replace(EXCEPTIONS_FILE)


def load_learned() -> dict[str, str]:
    learned: dict[str, str] = {}
    try:
        for line in LEARNED_FILE.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] in ("ru", "en"):
                learned[parts[0].lower()] = parts[1]
    except FileNotFoundError:
        pass
    return learned


def save_learned(learned: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED_FILE.with_suffix(".tmp")
    tmp.write_text("".join(f"{keys} {lang}\n" for keys, lang in sorted(learned.items())), encoding="utf-8")
    tmp.replace(LEARNED_FILE)
