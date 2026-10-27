"""HumanType's look: one palette, shared style sheets, and Windows 11 window effects."""

import ctypes
import winreg

from PySide6.QtWidgets import QWidget

# Palette, taken from the app icon: deep indigo night and a sky-blue accent.
BG = "#0B1020"
SURFACE = "#121A30"
SURFACE_2 = "#1A2442"
SURFACE_3 = "#222E52"
BORDER = "#26314F"
TEXT = "#E7ECF7"
MUTED = "#8B95B5"
ACCENT = "#38BDF8"
ACCENT_HOVER = "#7DD3FC"
ACCENT_DEEP = "#0EA5E9"
ACCENT_SOFT = "rgba(56, 189, 248, 0.14)"
DANGER = "#F87171"
DANGER_SOFT = "rgba(248, 113, 113, 0.12)"

FONT = "'Segoe UI Variable Text', 'Segoe UI', sans-serif"


def system_uses_light_theme() -> bool:
    """Whether the taskbar is light, so the tray icon can pick dark strokes."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


# --- Windows 11 window effects ---

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWCP_ROUND = 2


def _colorref(hex_color: str) -> int:
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return r | (g << 8) | (b << 16)


def _dwm(hwnd: int, attribute: int, value: int) -> None:
    data = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))


def style_window(widget: QWidget, caption: str | None = BG, border: str | None = BORDER) -> None:
    """Dark title bar in the app's colour and rounded corners (Windows 11; ignored elsewhere)."""
    try:
        hwnd = int(widget.winId())
        _dwm(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
        _dwm(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        if caption:
            _dwm(hwnd, DWMWA_CAPTION_COLOR, _colorref(caption))
        if border:
            _dwm(hwnd, DWMWA_BORDER_COLOR, _colorref(border))
    except Exception:
        pass


# --- style sheets ---

MENU_QSS = f"""
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
    padding: 6px;
    font-family: {FONT};
    font-size: 13px;
    color: {TEXT};
}}
QMenu::item {{
    padding: 8px 18px 8px 10px;
    border-radius: 8px;
    margin: 1px 0;
}}
QMenu::item:selected {{ background: {SURFACE_3}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 8px; }}
QMenu::indicator {{ width: 0; height: 0; }}
"""

POPUP_QSS = f"""
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px; }}
QLabel#title {{ color: {TEXT}; font-family: {FONT}; font-weight: 600; font-size: 14px; }}
QLabel#count {{ color: {MUTED}; font-family: {FONT}; font-size: 12px; }}
QLabel#hint {{ color: {MUTED}; font-family: {FONT}; font-size: 11px; }}
QLineEdit {{
    background: {BG}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 8px 12px; font-family: {FONT}; font-size: 13px;
    selection-background-color: {ACCENT_DEEP};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QListWidget {{ background: transparent; color: {TEXT}; border: none; outline: none; font-family: {FONT}; font-size: 13px; }}
QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}
"""

TOAST_QSS = f"""
QLabel {{
    background: rgba(18, 26, 48, 242); color: {TEXT};
    border: 1px solid rgba(56, 189, 248, 0.55); border-radius: 12px;
    padding: 8px 14px; font-family: {FONT}; font-size: 13px;
}}
"""

SETTINGS_QSS = f"""
QDialog {{ background: {BG}; }}
QWidget {{ font-family: {FONT}; font-size: 13px; color: {TEXT}; }}
QFrame#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#appName {{ font-size: 16px; font-weight: 700; }}
QLabel#version {{ color: {MUTED}; font-size: 11px; }}
QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#pageSubtitle {{ color: {MUTED}; }}
QLabel#rowTitle {{ font-weight: 600; }}
QLabel#rowHint, QLabel#muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#bigName {{ font-size: 26px; font-weight: 700; }}
QFrame#row {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#row:hover {{ border-color: {SURFACE_3}; }}
QFrame#bottomBar {{ background: {BG}; border-top: 1px solid {BORDER}; }}

QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 9px 10px; border-radius: 10px; margin: 2px 0; color: {MUTED}; }}
QListWidget#nav::item:hover:!selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#nav::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

QPushButton {{
    background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 8px 16px;
}}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: {BG}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton#ghost:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton#danger {{ background: {DANGER_SOFT}; border: 1px solid rgba(248,113,113,0.35); color: {DANGER}; }}
QPushButton#danger:hover {{ background: rgba(248,113,113,0.22); }}
QPushButton#hotkey {{
    background: {BG}; border: 1px solid {BORDER}; border-bottom: 3px solid {SURFACE_3};
    border-radius: 10px; padding: 7px 14px; min-width: 150px;
    font-family: 'Cascadia Mono', Consolas, monospace; font-weight: 600; color: {TEXT};
}}
QPushButton#hotkey:hover {{ border-color: {ACCENT}; border-bottom-color: {ACCENT_DEEP}; }}
QPushButton#hotkey[capturing="true"] {{ border-color: {ACCENT}; color: {ACCENT}; background: {ACCENT_SOFT}; }}
QPushButton#hotkey[off="true"] {{ color: {MUTED}; font-weight: 400; }}

QSpinBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 6px 10px; min-width: 110px; selection-background-color: {ACCENT_DEEP};
}}
QSpinBox:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QMessageBox {{ background: {SURFACE}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px; }}

QScrollArea, QScrollArea > QWidget > QWidget#pageBody {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

QFrame#segmented {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; }}
QPushButton#segment {{ background: transparent; border: none; border-radius: 8px; padding: 6px 12px; color: {MUTED}; }}
QPushButton#segment:hover {{ color: {TEXT}; }}
QPushButton#segment:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}

QListWidget#plainList {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; outline: none;
}}
QListWidget#plainList::item {{ padding: 6px 8px; border-radius: 6px; color: {TEXT}; }}
QListWidget#plainList::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QListWidget#plainList::item:hover:!selected {{ background: {SURFACE_2}; }}

QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px 10px; min-height: 20px;
}}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_3}; outline: none; padding: 4px;
}}
QLineEdit#field {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px 10px;
}}
QLineEdit#field:focus {{ border-color: {ACCENT}; }}
QLabel#sectionTitle {{ font-weight: 600; margin-top: 8px; }}
"""
