#!/bin/bash
# Installs HumanType for the current user on GNOME, KDE Plasma, Hyprland, Sway or any desktop on X11:
#   1. the udev rule that lets you read the keyboard (asks for sudo, once);
#   2. on GNOME, the Shell extension (active after you log out and in again);
#   3. the menu entry and icons;
#   4. the systemd user service that starts with the session.
# Run from anywhere: linux/install.sh        Remove: linux/install.sh --uninstall
# Without the autostarting service (for testing): linux/install.sh --no-service
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="humantype@humantype.local"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
EXT_DIR="$DATA/gnome-shell/extensions/$UUID"
UNIT_DIR="$CONF/systemd/user"
AUTOSTART="$CONF/autostart/humantype.desktop"
RULE=/etc/udev/rules.d/70-humantype.rules
DESKTOP="$DATA/applications/humantype.desktop"
ICONS="$DATA/icons/hicolor/scalable/apps"
# What the service needs from the session; desktops that do not hand it to systemd get it here.
SESSION_VARS="DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE SWAYSOCK HYPRLAND_INSTANCE_SIGNATURE"
START_SH="systemctl --user import-environment $SESSION_VARS; systemctl --user restart humantype.service"
START_CMD="sh -c '$START_SH'"  # for compositor configs; desktop entries quote with " instead

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
    rm -f "$UNIT_DIR/humantype.service" "$AUTOSTART"
    systemctl --user daemon-reload
    if command -v gnome-extensions >/dev/null; then
        gnome-extensions disable "$UUID" 2>/dev/null || true
    fi
    rm -rf "$EXT_DIR"
    rm -f "$DESKTOP" "$ICONS/humantype.svg" "$ICONS/humantype-off.svg"
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
    echo "Need python-evdev and python-gobject:"
    echo "  Arch:   sudo pacman -S python-evdev python-gobject"
    echo "  Fedora: sudo dnf install python3-evdev python3-gobject"
    echo "  Ubuntu: sudo apt install python3-evdev python3-gi"
    exit 1
}
KIND=$(PYTHONPATH="$APP_DIR" python3 -c 'from humantype.linux.desktops import detect; print(detect())')
case "$KIND" in
    gnome) echo "desktop: GNOME" ;;
    kde|hyprland|sway|x11)
        echo "desktop: $KIND"
        python3 -c 'import gi; gi.require_version("Atspi", "2.0")' 2>/dev/null || {
            echo "  (optional) for password and text checks install AT-SPI:"
            echo "  Arch: sudo pacman -S at-spi2-core   Fedora: sudo dnf install at-spi2-core   Ubuntu: sudo apt install gir1.2-atspi-2.0"
        } ;;
    *)
        echo "This desktop is not supported yet (XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-?}, ${XDG_SESSION_TYPE:-?})."
        echo "Supported: GNOME, KDE Plasma, Hyprland, Sway, and any desktop on X11."
        exit 1 ;;
esac

echo "== udev rule (sudo)"
if ! cmp -s "$APP_DIR/linux/70-humantype.rules" "$RULE" 2>/dev/null; then
    sudo install -m 644 "$APP_DIR/linux/70-humantype.rules" "$RULE"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=input --subsystem-match=misc
    sleep 1
fi

if [[ "$KIND" == gnome ]]; then
    echo "== GNOME Shell extension"
    mkdir -p "$(dirname "$EXT_DIR")"
    rm -rf "$EXT_DIR"
    cp -r "$APP_DIR/linux/gnome-extension/$UUID" "$EXT_DIR"
    enabled_add
    gsettings set org.gnome.shell disable-user-extensions false
fi

echo "== app menu entry and icons"
mkdir -p "$(dirname "$DESKTOP")" "$ICONS"
install -m 644 "$APP_DIR/assets/app.svg" "$ICONS/humantype.svg"
install -m 644 "$APP_DIR/assets/app-off.svg" "$ICONS/humantype-off.svg"
sed "s|@APP_DIR@|$APP_DIR|" "$APP_DIR/linux/humantype.desktop" > "$DESKTOP"
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true

if [[ "${1:-}" == "--no-service" ]]; then
    echo "Done without the service (for testing)."
    exit 0
fi

echo "== user service"
mkdir -p "$UNIT_DIR"
sed "s|@APP_DIR@|$APP_DIR|" "$APP_DIR/linux/humantype.service" > "$UNIT_DIR/humantype.service"
systemctl --user daemon-reload
systemctl --user enable humantype.service
if [[ "$KIND" != gnome ]]; then
    # Not every desktop starts graphical-session.target or hands systemd its variables:
    # the autostart entry does both at login (KDE, Xfce, Cinnamon, MATE, LXQt honour it).
    mkdir -p "$(dirname "$AUTOSTART")"
    cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=HumanType
Exec=sh -c "$START_SH"
Icon=humantype
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
    systemctl --user import-environment $SESSION_VARS 2>/dev/null || true
fi
systemctl --user restart humantype.service

echo
case "$KIND" in
    gnome)
        if gdbus introspect --session --dest org.humantype.Shell --object-path /org/humantype/Shell >/dev/null 2>&1; then
            echo "Done: HumanType is running."
        else
            echo "Done. Log out and in again once: GNOME loads a new extension only at login."
        fi ;;
    hyprland)
        echo "Done: HumanType is running. Hyprland does not read autostart entries: add to hyprland.conf"
        echo "  exec-once = $START_CMD" ;;
    sway)
        echo "Done: HumanType is running. Sway does not read autostart entries: add to ~/.config/sway/config"
        echo "  exec $START_CMD" ;;
    *)
        echo "Done: HumanType is running; its icon is in the tray." ;;
esac
echo "Logs: journalctl --user -u humantype -f"
