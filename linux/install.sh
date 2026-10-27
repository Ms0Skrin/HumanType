#!/bin/bash
# Installs HumanType for the current user on GNOME (Wayland):
#   1. the udev rule that lets you read the keyboard (asks for sudo, once);
#   2. the GNOME Shell extension (active after you log out and in again);
#   3. the systemd user service that starts with the session.
# Run from anywhere: linux/install.sh        Remove: linux/install.sh --uninstall
# Only the rule, the extension and the menu entry, no autostarting service: linux/install.sh --no-service
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="humantype@humantype.local"
EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
RULE=/etc/udev/rules.d/70-humantype.rules
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP="$DATA/applications/humantype.desktop"
ICON="$DATA/icons/hicolor/scalable/apps/humantype.svg"

enabled_add() {
    local list
    list=$(gsettings get org.gnome.shell enabled-extensions)
    if [[ "$list" != *"'$UUID'"* ]]; then
        if [[ "$list" == "@as []" || "$list" == "[]" ]]; then
            gsettings set org.gnome.shell enabled-extensions "['$UUID']"
        else
            gsettings set org.gnome.shell enabled-extensions "${list%]*}, '$UUID']"
        fi
    fi
}

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now humantype.service 2>/dev/null || true
    rm -f "$UNIT_DIR/humantype.service"
    systemctl --user daemon-reload
    gnome-extensions disable "$UUID" 2>/dev/null || true
    rm -rf "$EXT_DIR"
    rm -f "$DESKTOP" "$ICON"
    if [[ -f "$RULE" ]]; then
        echo "Removing $RULE (sudo)"
        sudo rm -f "$RULE"
        sudo udevadm control --reload
        sudo udevadm trigger --subsystem-match=input --subsystem-match=misc
    fi
    echo "HumanType removed. Settings stay in ~/.config/humantype."
    exit 0
fi

echo "== checking dependencies"
python3 -c 'import evdev, gi; gi.require_version("Gio", "2.0")' 2>/dev/null || {
    echo "Need python-evdev and python-gobject:  sudo pacman -S python-evdev python-gobject"
    exit 1
}

echo "== udev rule (sudo)"
if ! cmp -s "$APP_DIR/linux/70-humantype.rules" "$RULE" 2>/dev/null; then
    sudo install -m 644 "$APP_DIR/linux/70-humantype.rules" "$RULE"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=input --subsystem-match=misc
    sleep 1
fi

echo "== GNOME Shell extension"
mkdir -p "$(dirname "$EXT_DIR")"
rm -rf "$EXT_DIR"
cp -r "$APP_DIR/linux/gnome-extension/$UUID" "$EXT_DIR"
enabled_add
gsettings set org.gnome.shell disable-user-extensions false

echo "== app menu entry"
mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
install -m 644 "$APP_DIR/assets/app.svg" "$ICON"
sed "s|@APP_DIR@|$APP_DIR|" "$APP_DIR/linux/humantype.desktop" > "$DESKTOP"
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true

if [[ "${1:-}" == "--no-service" ]]; then
    echo "Done without the service (for testing). Log out and in again once to load the extension."
    exit 0
fi

echo "== user service"
mkdir -p "$UNIT_DIR"
sed "s|@APP_DIR@|$APP_DIR|" "$APP_DIR/linux/humantype.service" > "$UNIT_DIR/humantype.service"
systemctl --user daemon-reload
systemctl --user enable humantype.service
systemctl --user restart humantype.service

echo
if gdbus introspect --session --dest org.humantype.Shell --object-path /org/humantype/Shell >/dev/null 2>&1; then
    echo "Done: HumanType is running."
else
    echo "Done. Log out and in again once: GNOME loads a new extension only at login."
fi
echo "Logs: journalctl --user -u humantype -f"
