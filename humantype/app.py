"""HumanType: tray icon, hotkeys and the modules wired together."""

import ctypes
import logging
import os
import sys

import win32api
import win32event
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction

from . import theme, winapi
from .autoswitch import AutoSwitcher
from .config import EXCEPTIONS_FILE, Settings, set_autostart
from .detector import Detector
from .fixer import LayoutFixer
from .history import History
from .hotkeys import Hotkeys
from .icons import app_icon, line_icon, tray_icon
from .paths import APP_NAME, ASSETS_DIR, DATA_DIR, IMAGES_DIR, LOG_FILE
from .popup import ClipPopup, corner_at
from .settings_window import SettingsWindow, hotkey_text
from .toast import Toast

ERROR_ALREADY_EXISTS = 183

log = logging.getLogger("humantype")


class MenuHeader(QWidget):
    """The top of the tray menu: icon, name and what the app is doing."""

    def __init__(self) -> None:
        super().__init__()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(34, 34))
        name = QLabel(APP_NAME)
        name.setStyleSheet(f"color: {theme.TEXT}; font-size: 15px; font-weight: 700;")
        self.status = QLabel()
        self.status.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(name)
        text.addWidget(self.status)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 24, 8)
        layout.setSpacing(10)
        layout.addWidget(logo)
        layout.addLayout(text, 1)

    def set_status(self, text: str, ok: bool = True) -> None:
        dot = theme.ACCENT if ok else theme.MUTED
        self.status.setText(f'<span style="color:{dot}">●</span>&nbsp; {text}')


class HumanTypeApp:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.settings = Settings.load()
        self.toast = Toast()
        self.history = History(DATA_DIR / "history.db", IMAGES_DIR)
        self.fixer = LayoutFixer(self.history, self.notify)
        self.popup = ClipPopup(self.history, self.notify)
        self.settings_window: SettingsWindow | None = None
        self.autoswitcher = AutoSwitcher(Detector(ASSETS_DIR / "dict"), EXCEPTIONS_FILE)
        self.autoswitcher.corrected.connect(self._on_corrected)
        self.autoswitcher.undone.connect(self._on_undone)
        self.autoswitcher.flipped.connect(self._on_flipped)
        self.autoswitcher.start()

        self.hotkeys = Hotkeys()
        app.installNativeEventFilter(self.hotkeys)

        self.tray = QSystemTrayIcon(tray_icon())
        self.menu = self._menu()
        # The menu is shown by hand (see _on_tray) so it opens exactly where the history window does.
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        failed = self.apply_settings(self.settings, save=False)
        s = self.settings
        if failed:
            self.tray.showMessage(
                APP_NAME,
                "Заняты другой программой: " + ", ".join(failed) + "\nПоменяй их в настройках.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        else:
            self.tray.showMessage(
                f"{APP_NAME} запущен",
                f"{hotkey_text(s.fix_word)} — исправить раскладку\n"
                f"{hotkey_text(s.fix_line)} — вся строка\n"
                f"{hotkey_text(s.history_popup)} — история буфера",
                app_icon(),
                4000,
            )

    def notify(self, text: str) -> None:
        if self.settings.show_toasts:
            self.toast.show_text(text)

    # --- settings ---

    def _register_hotkeys(self) -> list[str]:
        self.hotkeys.unregister_all()
        s = self.settings
        failed = []
        for hotkey, action in (
            (s.fix_word, self.fix_word),
            (s.fix_line, self.fix_line),
            (s.history_popup, self.open_history),
            (s.toggle_autoswitch, self.toggle_autoswitch),
        ):
            if hotkey.is_set and not self.hotkeys.register(hotkey.mods, hotkey.vk, action):
                failed.append(hotkey_text(hotkey))
        return failed

    def apply_settings(self, settings: Settings, save: bool = True) -> list[str]:
        """Puts settings in force; returns the hotkeys another program holds."""
        self.settings = settings
        if save:
            settings.save()
        self.history.save_images = settings.save_images
        self.history.set_limit(settings.history_limit)
        self.fixer.switch_layout = settings.switch_layout
        self.autoswitcher.configure(
            settings.autoswitch,
            settings.autoswitch_sensitivity,
            settings.autoswitch_short_words,
            settings.autoswitch_skip_fullscreen,
            settings.autoswitch_excluded,
            settings.autoswitch_terminal_answers,
        )
        self.autoswitcher.set_flip_hotkeys(
            [(hotkey.mods, hotkey.vk) for hotkey in (settings.fix_word, settings.fix_line) if hotkey.is_set]
        )
        self._update_autoswitch_action()
        try:
            set_autostart(settings.autostart)
        except OSError:
            log.exception("autostart change failed")
        s = settings
        self.tray.setToolTip(f"{APP_NAME}\n{hotkey_text(s.fix_word)} — раскладка\n{hotkey_text(s.history_popup)} — буфер")
        self.fix_action.setText(f"Исправить раскладку\t{hotkey_text(s.fix_word)}")
        self.history_action.setText(f"История буфера\t{hotkey_text(s.history_popup)}")
        return self._register_hotkeys()

    def open_settings(self) -> None:
        if self.settings_window and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        # Free the hotkeys so the window can catch them while one is being changed.
        self.hotkeys.unregister_all()
        window = SettingsWindow(
            self.settings, self.apply_settings, self.hotkeys.unregister_all, self.history, self.autoswitcher
        )
        window.finished.connect(lambda _: self._register_hotkeys())
        self.settings_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    # --- tray ---

    def _menu(self) -> QMenu:
        menu = QMenu()
        # Rounded corners need a frameless, see-through menu window.
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(theme.MENU_QSS)

        self.header = MenuHeader()
        header_action = QWidgetAction(menu)
        header_action.setDefaultWidget(self.header)
        menu.addAction(header_action)
        menu.addSeparator()

        self.autoswitch_action = menu.addAction(line_icon("swap", theme.ACCENT), "Автопереключение", self.toggle_autoswitch)
        menu.addSeparator()
        self.fix_action = menu.addAction(line_icon("keyboard", theme.ACCENT), "Исправить раскладку", self.fix_word)
        self.history_action = menu.addAction(
            line_icon("clipboard", theme.ACCENT), "История буфера", lambda: self.popup.open_at_cursor(None)
        )
        menu.addSeparator()
        self.pause_action = menu.addAction(line_icon("pause"), "Не запоминать буфер", self._toggle_pause)
        menu.addAction(line_icon("trash"), "Очистить историю", self.history.clear)
        menu.addAction(line_icon("folder"), "Папка с данными", lambda: os.startfile(DATA_DIR))
        menu.addSeparator()
        menu.addAction(line_icon("settings"), "Настройки", self.open_settings)
        menu.addAction(line_icon("power"), "Выход", self.quit)

        menu.aboutToShow.connect(self._update_header)
        return menu

    def _update_header(self) -> None:
        if self.history.paused:
            self.header.set_status("буфер на паузе", ok=False)
        else:
            auto = "авто вкл" if self.settings.autoswitch else "авто выкл"
            self.header.set_status(f"работает · {auto} · в истории {len(self.history.items())}")

    # --- layout ---

    def fix_word(self) -> None:
        # The word just typed is typed again from the keys the hook saw; anything else (a selection,
        # text typed before the caret last jumped) goes through the clipboard.
        if not self.autoswitcher.flip(False, self.settings.switch_layout):
            self.autoswitcher.reset()
            self.fixer.fix()

    def fix_line(self) -> None:
        if not self.autoswitcher.flip(True, self.settings.switch_layout):
            self.autoswitcher.reset()
            self.fixer.fix(whole_line=True)

    def toggle_autoswitch(self) -> None:
        self.settings.autoswitch = not self.settings.autoswitch
        self.settings.save()
        self.apply_settings(self.settings, save=False)
        self.toast.show_text("Автопереключение включено" if self.settings.autoswitch else "Автопереключение выключено")

    def _update_autoswitch_action(self) -> None:
        on = self.settings.autoswitch
        hotkey = hotkey_text(self.settings.toggle_autoswitch) if self.settings.toggle_autoswitch.is_set else ""
        self.autoswitch_action.setText(f"Автопереключение: {'вкл' if on else 'выкл'}	{hotkey}")
        self.autoswitch_action.setIcon(line_icon("swap", theme.ACCENT if on else theme.MUTED))
        self.tray.setIcon(tray_icon(on))  # grey while switched off

    def _on_corrected(self, before: str, after: str) -> None:
        if self.settings.autoswitch_notify:
            self.toast.show_text(f"{before}  →  {after}", 1100)

    def _on_flipped(self, text: str, lang: str) -> None:
        self.notify(f"✓ {'РУ' if lang == 'ru' else 'EN'}: {text.strip()[:40]}")

    def _on_undone(self, word: str) -> None:
        self.toast.show_text(f"Оставил «{word}» как есть и запомнил", 1600)

    def open_history(self) -> None:
        self.popup.open_at_cursor(winapi.foreground())

    def _on_tray(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.menu.hide()
            self.popup.open_at_cursor(None)
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self.popup.hide()
            self._update_header()
            self.menu.ensurePolished()
            self.menu.adjustSize()
            self.menu.popup(corner_at(QCursor.pos(), self.menu.sizeHint()))
            self.menu.activateWindow()  # so a click elsewhere closes it, as with a native tray menu

    def _toggle_pause(self) -> None:
        paused = not self.history.paused
        self.history.paused = paused
        self.pause_action.setText("Снова запоминать буфер" if paused else "Не запоминать буфер")
        self.pause_action.setIcon(line_icon("play", theme.ACCENT) if paused else line_icon("pause"))
        self.toast.show_text("Буфер не запоминается" if paused else "Буфер снова запоминается")

    def quit(self) -> None:
        self.autoswitcher.stop()
        self.hotkeys.unregister_all()
        self.tray.hide()
        self.app.quit()


def main() -> None:
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    sys.excepthook = lambda *exc: log.critical("unhandled", exc_info=exc)

    mutex = win32event.CreateMutex(None, False, "Local\\HumanTypeTrayApp")
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    # Taskbar groups our windows under our own icon instead of python's.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HumanType.App")
    except Exception:
        pass
    instance = HumanTypeApp(app)
    log.info("started")
    code = app.exec()
    del instance, mutex
    sys.exit(code)
