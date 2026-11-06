"""HumanType: tray icon, hotkeys and the modules wired together."""

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser

import win32api
import win32event
import win32security
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction,
)

from . import __version__, elevation, theme, updater, winapi
from .autoswitch import AutoSwitcher
from .config import EXCEPTIONS_FILE, LEARNED_FILE, Settings, set_autostart
from .detector import Detector
from .fixer import LayoutFixer
from .history import History
from .hotkeys import Hotkeys
from .icons import app_icon, line_icon, tray_icon
from .journal_window import JournalWindow
from .paths import APP_NAME, ASSETS_DIR, DATA_DIR, IMAGES_DIR, LOG_FILE
from .popup import ClipPopup, corner_at
from .settings_window import SettingsWindow, hotkey_text
from .toast import Toast

ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
MUTEX_NAME = "Local\\HumanTypeTrayApp"
SYNCHRONIZE = 0x00100000

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


class UpdateSignals(QObject):
    """Carries the update check and download results from their thread to the GUI thread."""

    checked = Signal(object, str, bool)  # Release | None, error, quiet
    downloaded = Signal(str, str)  # installer path, error
    progress = Signal(float)


class HumanTypeApp:
    def __init__(self, app: QApplication, elevation_refused: bool = False) -> None:
        self.app = app
        self.update: updater.Release | None = None
        self.updating = False
        self.update_signals = UpdateSignals()
        self.update_signals.checked.connect(self._on_update_checked)
        self.update_signals.downloaded.connect(self._on_update_downloaded)
        self.update_signals.progress.connect(
            lambda part: self.update_action.setText(f"Скачиваю {self.update.version}… {part:.0%}"))
        self.settings = Settings.load()
        self.toast = Toast()
        self.history = History(DATA_DIR / "history.db", IMAGES_DIR)
        self.fixer = LayoutFixer(self.history, self.notify)
        self.popup = ClipPopup(self.history, self.notify)
        self.settings_window: SettingsWindow | None = None
        self.journal_window: JournalWindow | None = None
        self.autoswitcher = AutoSwitcher(Detector(ASSETS_DIR / "dict"), EXCEPTIONS_FILE, LEARNED_FILE)
        self.autoswitcher.corrected.connect(self._on_corrected)
        self.autoswitcher.undone.connect(self._on_undone)
        self.autoswitcher.flipped.connect(self._on_flipped)
        self.autoswitcher.lost_track.connect(self.fixer.fix)
        self.autoswitcher.start()

        self.hotkeys = Hotkeys()
        app.installNativeEventFilter(self.hotkeys)

        self.tray = QSystemTrayIcon(tray_icon())
        self.menu = self._menu()
        # The menu is shown by hand (see _on_tray) so it opens exactly where the history window does.
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        if elevation_refused:
            self.settings.run_as_admin = False  # asked once; not again at every start
            self.settings.save()
        failed = self.apply_settings(self.settings, save=False)
        s = self.settings
        if elevation_refused:
            self.tray.showMessage(
                APP_NAME,
                "Без прав администратора HumanType не видит набор в окнах, запущенных от администратора "
                "(терминал, cmd).\nВключить: Настройки → Поведение → «Работать в окнах администратора».",
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )
        elif failed:
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
        QTimer.singleShot(20_000, lambda: self.check_updates(quiet=True))

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
            set_autostart(settings.autostart, settings.run_as_admin)
        except OSError:
            log.exception("autostart change failed")
        if save and settings.run_as_admin and not elevation.is_admin() and elevation.can_elevate():
            # Switched on in the settings: start again with the rights, this copy makes way.
            if elevation.relaunch_as_admin("--after-restart"):
                QTimer.singleShot(0, self.quit)
            else:
                settings.run_as_admin = False
                settings.save()
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

    def open_journal(self) -> None:
        if self.journal_window is None:
            self.journal_window = JournalWindow(self.autoswitcher)
        self.journal_window.refresh()
        self.journal_window.show()
        self.journal_window.raise_()
        self.journal_window.activateWindow()

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
        menu.addAction(line_icon("info"), "Почему не исправилось?", self.open_journal)
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
        self.update_action = menu.addAction(line_icon("download"), "Проверить обновления", self._update_clicked)
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

    def _on_undone(self, word: str, for_good: bool) -> None:
        if for_good:
            self.toast.show_text(f"Оставил «{word}» как есть и запомнил", 1600)
        else:
            self.toast.show_text(f"Оставил «{word}» как есть", 1300)

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

    # --- updates ---

    def _update_clicked(self) -> None:
        if self.update:
            self.install_update()
        else:
            self.check_updates(quiet=False)

    def check_updates(self, quiet: bool) -> None:
        if self.updating:
            return
        self.update_action.setText("Проверяю обновления…")

        def work() -> None:
            try:
                self.update_signals.checked.emit(updater.newer(), "", quiet)
            except Exception as error:
                log.warning("update check: %s", error)
                self.update_signals.checked.emit(None, str(error), quiet)

        threading.Thread(target=work, daemon=True).start()

    def _on_update_checked(self, release: updater.Release | None, error: str, quiet: bool) -> None:
        self.update = release
        if release:
            self.update_action.setText(f"Обновить до {release.version}")
            self.update_action.setIcon(line_icon("download", theme.ACCENT))
            if quiet:
                self.tray.showMessage(APP_NAME, f"Вышла версия {release.version}. Обновить: меню → «Обновить до {release.version}»",
                                      app_icon(), 6000)
                return
            answer = QMessageBox.question(
                None, APP_NAME,
                f"Вышла версия {release.version} (у тебя {__version__}).\n\n{release.notes[:600]}\n\nОбновить сейчас?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.install_update()
            return
        self.update_action.setText("Проверить обновления")
        if not quiet:
            if error:
                self.tray.showMessage(APP_NAME, f"Не удалось проверить обновления: {error}",
                                      QSystemTrayIcon.MessageIcon.Warning)
            else:
                self.tray.showMessage(APP_NAME, f"Установлена последняя версия ({__version__})", app_icon(), 3000)

    def install_update(self) -> None:
        release = self.update
        if not release or self.updating:
            return
        if not getattr(sys, "frozen", False):
            webbrowser.open(updater.PAGE)  # running from source: nothing to install over
            return
        self.updating = True
        self.update_action.setText(f"Скачиваю {release.version}…")

        def work() -> None:
            try:
                path = updater.download_installer(release, self.update_signals.progress.emit)
                self.update_signals.downloaded.emit(str(path), "")
            except Exception as error:
                log.warning("update download: %s", error)
                self.update_signals.downloaded.emit("", str(error))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_downloaded(self, path: str, error: str) -> None:
        if error:
            self.updating = False
            self.update_action.setText(f"Обновить до {self.update.version}")
            self.tray.showMessage(APP_NAME, f"Не удалось скачать обновление: {error}", QSystemTrayIcon.MessageIcon.Warning)
            return
        # The setup waits until we have quit (it checks our mutex), installs and starts the new version.
        command = f'ping -n 3 127.0.0.1 >nul & start "" "{path}" /SILENT /SUPPRESSMSGBOXES /NORESTART'
        subprocess.Popen(["cmd", "/c", command], creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
        log.info("updating to %s", self.update.version)
        self.quit()

    def quit(self) -> None:
        self.autoswitcher.stop()
        self.hotkeys.unregister_all()
        self.tray.hide()
        self.app.quit()


def _single_instance():
    """The mutex that marks a running copy, or None when one runs already. Anyone may open it
    (and it carries a low integrity label): a copy without admin rights and the installer must
    see the one that has them."""
    attributes = win32security.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:(A;;GA;;;WD)S:(ML;;NW;;;LW)", win32security.SDDL_REVISION_1)
    try:
        mutex = win32event.CreateMutex(attributes, False, MUTEX_NAME)
    except win32event.error as error:
        if error.winerror == ERROR_ACCESS_DENIED:
            return None  # made by a copy with other rights (an old version): it runs
        raise
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return mutex


def _running() -> bool:
    try:
        handle = win32event.OpenMutex(SYNCHRONIZE, False, MUTEX_NAME)
    except win32event.error as error:
        return error.winerror == ERROR_ACCESS_DENIED
    win32api.CloseHandle(handle)
    return True


def _wait(condition, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def _elevate() -> str:
    """Start the copy with admin rights instead of this one: "started", "refused" or "no"."""
    if not elevation.can_elevate():
        return "no"  # not an administrator: nothing to ask for
    if elevation.task_installed() and elevation.run_task():
        if _wait(_running, 8):
            return "started"
        log.warning("the admin task did not start the app, asking UAC")
    return "started" if elevation.relaunch_as_admin("--after-restart") else "refused"


def _cleanup() -> None:
    """For the uninstaller: removes the admin task and stops the running copy."""
    if not elevation.is_admin():
        if elevation.task_installed() and elevation.can_elevate() and elevation.relaunch_as_admin("--cleanup"):
            _wait(lambda: not elevation.task_installed(), 30)
        return
    elevation.remove_task()
    subprocess.run(["taskkill", "/F", "/IM", "HumanType.exe", "/FI", f"PID ne {os.getpid()}"],
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def main() -> None:
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    sys.excepthook = lambda *exc: log.critical("unhandled", exc_info=exc)
    # The keyboard hook must get the GIL back fast: Windows drops a hook that answers late.
    sys.setswitchinterval(0.001)
    args = sys.argv[1:]
    if "--cleanup" in args:
        _cleanup()
        return

    if "--after-restart" in args:
        _wait(lambda: not _running(), 10)  # the copy that started us is still quitting
    elif _running():
        return
    refused = False
    if not elevation.is_admin() and Settings.load().run_as_admin:
        outcome = _elevate()
        if outcome == "started":
            return
        refused = outcome == "refused"

    mutex = _single_instance()
    if mutex is None:
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
    instance = HumanTypeApp(app, elevation_refused=refused)
    log.info("started%s", " with admin rights" if elevation.is_admin() else " without admin rights")
    code = app.exec()
    del instance, mutex
    sys.exit(code)
