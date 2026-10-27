"""The settings window: a sidebar of sections, each a column of cards."""

import ctypes
import os
from copy import deepcopy
from typing import Callable

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
import win32gui

from . import theme
from .autoswitch import AutoSwitcher, process_name
from .config import DEFAULT_EXCLUDED, Hotkey, Settings
from .layout import EN_TO_RU
from .history import History
from .hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN
from .icons import app_icon, line_icon
from .paths import APP_NAME, DATA_DIR, VERSION
from .popup import plural

user32 = ctypes.windll.user32

MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F, 0x90}
SPECIAL_NAMES = {0x13: "Pause", 0x14: "Caps Lock", 0x91: "Scroll Lock", 0x2C: "Print Screen", 0x20: "Space"}


def key_name(vk: int) -> str:
    if vk in SPECIAL_NAMES:
        return SPECIAL_NAMES[vk]
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    scan = user32.MapVirtualKeyW(vk, 0)
    lparam = (scan << 16) | ((1 << 24) if vk in EXTENDED_VKS else 0)
    buffer = ctypes.create_unicode_buffer(64)
    if scan and user32.GetKeyNameTextW(lparam, buffer, 64):
        return buffer.value.title()
    return f"VK {vk:#04x}"


def hotkey_text(hotkey: Hotkey) -> str:
    if not hotkey.is_set:
        return "выключено"
    parts = []
    if hotkey.mods & MOD_CONTROL:
        parts.append("Ctrl")
    if hotkey.mods & MOD_SHIFT:
        parts.append("Shift")
    if hotkey.mods & MOD_ALT:
        parts.append("Alt")
    if hotkey.mods & MOD_WIN:
        parts.append("Win")
    parts.append(key_name(hotkey.vk))
    return " + ".join(parts)


# --- controls ---


class Toggle(QAbstractButton):
    """An iOS/Windows 11 style switch with a sliding knob."""

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._slide)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _slide(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        off, on = QColor(theme.SURFACE_3), QColor(theme.ACCENT)
        t = self._pos
        color = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        d = self.height() - 6
        x = 3 + (self.width() - 6 - d) * t
        p.setBrush(QColor(theme.BG) if t > 0.5 else QColor(theme.TEXT))
        p.drawEllipse(QRectF(x, 3, d, d))


class HotkeyButton(QPushButton):
    """Click, then press the combination. Esc cancels, Backspace switches it off."""

    changed = Signal()

    def __init__(self, hotkey: Hotkey) -> None:
        super().__init__()
        self.setObjectName("hotkey")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hotkey = deepcopy(hotkey)
        self._capturing = False
        self.clicked.connect(self._start)
        self._show()

    def set_hotkey(self, hotkey: Hotkey) -> None:
        self.hotkey = deepcopy(hotkey)
        self._show()

    def _show(self) -> None:
        self.setProperty("capturing", self._capturing)
        self.setProperty("off", not self.hotkey.is_set and not self._capturing)
        self.setText("Нажми сочетание…" if self._capturing else hotkey_text(self.hotkey))
        self.style().unpolish(self)
        self.style().polish(self)

    def _start(self) -> None:
        self._capturing = True
        self._show()
        self.grabKeyboard()

    def _stop(self) -> None:
        self._capturing = False
        self.releaseKeyboard()
        self._show()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            return super().keyPressEvent(event)
        vk = event.nativeVirtualKey()
        if event.key() == Qt.Key.Key_Escape:
            self._stop()
            return
        if event.key() == Qt.Key.Key_Backspace:
            self.hotkey = Hotkey(0, 0)
            self._stop()
            self.changed.emit()
            return
        if vk in MODIFIER_VKS or vk == 0:
            return  # wait for the main key
        qmods = event.modifiers()
        mods = 0
        if qmods & Qt.KeyboardModifier.ControlModifier:
            mods |= MOD_CONTROL
        if qmods & Qt.KeyboardModifier.ShiftModifier:
            mods |= MOD_SHIFT
        if qmods & Qt.KeyboardModifier.AltModifier:
            mods |= MOD_ALT
        if qmods & Qt.KeyboardModifier.MetaModifier:
            mods |= MOD_WIN
        self.hotkey = Hotkey(mods, vk)
        self._stop()
        self.changed.emit()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._stop()
        super().focusOutEvent(event)


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def row(title: str, hint: str, control: QWidget) -> QFrame:
    """A card: title and hint on the left, the control on the right."""
    card = QFrame()
    card.setObjectName("row")
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(_label(title, "rowTitle"))
    if hint:
        text.addWidget(_label(hint, "rowHint", wrap=True))
    layout = QHBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(16)
    layout.addLayout(text, 1)
    layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return card


def page(title: str, subtitle: str, *cards: QWidget) -> QWidget:
    body = QWidget()
    body.setObjectName("pageBody")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(28, 24, 22, 16)
    layout.setSpacing(10)
    layout.addWidget(_label(title, "pageTitle"))
    layout.addWidget(_label(subtitle, "pageSubtitle", wrap=True))
    layout.addSpacing(8)
    for card in cards:
        layout.addWidget(card)
    layout.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    return scroll


class Segmented(QFrame):
    """A row of mutually exclusive options, like a Windows 11 segmented control."""

    def __init__(self, options: list[tuple[str, str]], value: str) -> None:
        super().__init__()
        self.setObjectName("segmented")
        self.group = QButtonGroup(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._buttons: dict[str, QPushButton] = {}
        for key, label in options:
            button = QPushButton(label)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button)
            layout.addWidget(button)
            self._buttons[key] = button
        self.set_value(value)

    def value(self) -> str:
        return next(k for k, b in self._buttons.items() if b.isChecked())

    def set_value(self, value: str) -> None:
        self._buttons.get(value, next(iter(self._buttons.values()))).setChecked(True)


def running_apps() -> list[str]:
    """Process names of the windows open right now, for the exclusion picker."""
    names: set[str] = set()

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            name = process_name(hwnd)
            if name:
                names.add(name)
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        pass
    return sorted(names, key=str.lower)


class ListEditor(QFrame):
    """A card holding a list with remove, and optionally an input with add."""

    def __init__(self, title: str, hint: str, items: list[str], picker: QComboBox | None = None, display=None) -> None:
        super().__init__()
        self.setObjectName("row")
        self.display = display or (lambda s: s)
        self.list = QListWidget()
        self.list.setObjectName("plainList")
        self.list.setMinimumHeight(150)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.set_items(items)
        remove = QPushButton("Удалить выбранное")
        remove.setObjectName("ghost")
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(self._remove)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(_label(title, "rowTitle"))
        layout.addWidget(_label(hint, "rowHint", wrap=True))
        if picker is not None:
            self.picker = picker
            add = QPushButton("Добавить")
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.clicked.connect(self._add)
            picker.lineEdit().returnPressed.connect(self._add)
            line = QHBoxLayout()
            line.addWidget(picker, 1)
            line.addWidget(add)
            layout.addLayout(line)
        layout.addWidget(self.list)
        layout.addWidget(remove, 0, Qt.AlignmentFlag.AlignRight)

    def set_items(self, items: list[str]) -> None:
        self.list.clear()
        for value in items:
            item = QListWidgetItem(self.display(value))
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.list.addItem(item)

    def items(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _add(self) -> None:
        value = self.picker.currentText().strip()
        if value and value.lower() not in {v.lower() for v in self.items()}:
            self.set_items(self.items() + [value])
        self.picker.setEditText("")

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))


# --- the window ---


class SettingsWindow(QDialog):
    SECTIONS = [
        ("keyboard", "Горячие клавиши"),
        ("swap", "Автопереключение"),
        ("clipboard", "История буфера"),
        ("sliders", "Поведение"),
        ("info", "О программе"),
    ]

    def __init__(
        self,
        settings: Settings,
        apply: Callable[[Settings], list[str]],
        suspend: Callable[[], None],
        history: History | None = None,
        autoswitcher: AutoSwitcher | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.apply = apply
        self.suspend = suspend
        self.history = history
        self.autoswitcher = autoswitcher
        self.setWindowTitle(f"{APP_NAME} — настройки")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(theme.SETTINGS_QSS)
        self.resize(820, 560)
        self.setMinimumSize(720, 500)

        # --- controls ---
        self.hk_word = HotkeyButton(settings.fix_word)
        self.hk_line = HotkeyButton(settings.fix_line)
        self.hk_popup = HotkeyButton(settings.history_popup)
        self.hk_toggle = HotkeyButton(settings.toggle_autoswitch)
        self.limit = QSpinBox()
        self.limit.setRange(10, 2000)
        self.limit.setSingleStep(50)
        self.limit.setValue(settings.history_limit)
        self.limit.setSuffix("  записей")
        self.limit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.save_images = Toggle(settings.save_images)
        self.switch_layout = Toggle(settings.switch_layout)
        self.show_toasts = Toggle(settings.show_toasts)
        self.autostart = Toggle(settings.autostart)

        # --- pages ---
        self.stack = QStackedWidget()
        self.stack.addWidget(
            page(
                "Горячие клавиши",
                "Кликни по клавише и нажми новое сочетание. Esc — отмена, Backspace — выключить.",
                row("Исправить раскладку", "Выделенный текст, а если ничего не выделено — последнее слово", self.hk_word),
                row("Исправить всю строку", "Всё, что набрано от начала строки до курсора", self.hk_line),
                row("История буфера", "Открыть список скопированного у курсора", self.hk_popup),
                row("Автопереключение вкл/выкл", "Быстро включить или выключить автоматическое исправление", self.hk_toggle),
            )
        )
        self.stack.addWidget(self._autoswitch_page(settings))
        clear = QPushButton("Очистить")
        clear.setObjectName("danger")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self._clear_history)
        self.history_stats = _label("", "muted")
        self.stack.addWidget(
            page(
                "История буфера",
                "Всё, что ты копируешь, хранится только на этом компьютере.",
                row("Хранить последние", "Закреплённые записи хранятся всегда и в лимит не входят", self.limit),
                row("Запоминать картинки", "Скриншоты и скопированные изображения с превью", self.save_images),
                row("Очистить историю", "Удалит всё, кроме закреплённого", clear),
                self.history_stats,
            )
        )
        self.stack.addWidget(
            page(
                "Поведение",
                "Как HumanType ведёт себя в работе.",
                row("Переключать раскладку", "После исправления включить язык, на котором текст теперь написан", self.switch_layout),
                row("Подсказки у курсора", "Короткие уведомления вроде «✓ РУ: привет»", self.show_toasts),
                row("Запуск вместе с Windows", "HumanType будет в трее сразу после входа", self.autostart),
            )
        )
        self.stack.addWidget(self._about_page())

        # --- sidebar ---
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(36, 36))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_text.addWidget(_label(APP_NAME, "appName"))
        brand_text.addWidget(_label(f"версия {VERSION}", "version"))
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(logo)
        brand.addLayout(brand_text)
        brand.addStretch(1)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        for icon, name in self.SECTIONS:
            item = QListWidgetItem(line_icon(icon, theme.MUTED), "  " + name)
            item.setData(Qt.ItemDataRole.UserRole, icon)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._go)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 20, 12, 16)
        side.setSpacing(18)
        side.addLayout(brand)
        side.addWidget(self.nav, 1)

        # --- bottom bar ---
        reset = QPushButton("Сбросить по умолчанию")
        reset.setObjectName("ghost")
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        for button in (reset, cancel, save):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        bar = QFrame()
        bar.setObjectName("bottomBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 12, 20, 12)
        bar_layout.addWidget(reset)
        bar_layout.addStretch(1)
        bar_layout.addWidget(cancel)
        bar_layout.addWidget(save)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self.stack, 1)
        content.addWidget(bar)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addLayout(content, 1)

        self.nav.setCurrentRow(0)
        self._update_stats()

    def showEvent(self, event) -> None:
        theme.style_window(self)
        super().showEvent(event)

    def _autoswitch_page(self, settings: Settings) -> QWidget:
        self.autoswitch = Toggle(settings.autoswitch)
        self.sensitivity = Segmented(
            [("careful", "Осторожно"), ("normal", "Нормально"), ("bold", "Смело")], settings.autoswitch_sensitivity
        )
        self.short_words = Toggle(settings.autoswitch_short_words)
        self.skip_fullscreen = Toggle(settings.autoswitch_skip_fullscreen)
        self.autoswitch_notify = Toggle(settings.autoswitch_notify)
        self.terminal_answers = Toggle(settings.autoswitch_terminal_answers)

        picker = QComboBox()
        picker.setEditable(True)
        picker.addItems(running_apps())
        picker.setEditText("")
        picker.lineEdit().setPlaceholderText("Выбери из запущенных или впиши, например game.exe")
        self.excluded = ListEditor(
            "Программы-исключения",
            "Здесь автопереключение не работает. По умолчанию: терминалы, редакторы кода, менеджеры паролей, удалённые рабочие столы.",
            settings.autoswitch_excluded,
            picker,
        )
        words = sorted(self.autoswitcher.detector.exceptions) if self.autoswitcher else []
        self._initial_words = set(words)
        self.exception_words = ListEditor(
            "Слова-исключения",
            "Эти слова никогда не исправляются. Слово попадает сюда само, если сразу после исправления нажать Backspace.",
            words,
            display=lambda keys: f"{keys}   ·   {keys.translate(EN_TO_RU)}",
        )
        return page(
            "Автопереключение",
            "HumanType следит за набором и сам исправляет слова, набранные не в той раскладке: "
            "ghbdtn → привет. Сразу после исправления Backspace вернёт как было.",
            row("Автопереключение раскладки", "Исправлять слова автоматически после пробела", self.autoswitch),
            row(
                "Чувствительность",
                "Осторожно — только явные случаи. Смело — исправляет больше, в том числе редкие слова.",
                self.sensitivity,
            ),
            row("Короткие слова", "«я», «в», «на», «не»… — решает по соседним словам", self.short_words),
            row("Не работать в полноэкранных приложениях", "Игры и полноэкранные программы", self.skip_fullscreen),
            row("Подсказка при исправлении", "Короткое уведомление у курсора: ghbdtn → привет", self.autoswitch_notify),
            row(
                "Ответы в терминале",
                "н, нуы, т, тщ по Enter становятся y, yes, n, no — даже там, где автопереключение выключено",
                self.terminal_answers,
            ),
            self.excluded,
            self.exception_words,
        )

    def _about_page(self) -> QWidget:
        big = QLabel()
        big.setPixmap(app_icon().pixmap(96, 96))
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name = _label(APP_NAME, "bigName")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version = _label(f"Версия {VERSION}", "muted")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about = _label(
            "Печатай по-человечески: исправляет текст, набранный не в той раскладке, "
            "и помнит всё, что ты копируешь — текст и картинки.",
            "pageSubtitle",
            wrap=True,
        )
        about.setAlignment(Qt.AlignmentFlag.AlignCenter)
        folder = QPushButton("  Открыть папку с данными")
        folder.setIcon(line_icon("folder", theme.TEXT))
        folder.setCursor(Qt.CursorShape.PointingHandCursor)
        folder.clicked.connect(lambda: os.startfile(DATA_DIR))

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(48, 40, 48, 24)
        layout.setSpacing(8)
        layout.addStretch(1)
        layout.addWidget(big)
        layout.addWidget(name)
        layout.addWidget(version)
        layout.addSpacing(10)
        layout.addWidget(about)
        layout.addSpacing(16)
        layout.addWidget(folder, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(2)
        return widget

    def _go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            color = theme.ACCENT if i == index else theme.MUTED
            item.setIcon(line_icon(item.data(Qt.ItemDataRole.UserRole), color))

    def _update_stats(self) -> None:
        if not self.history:
            return
        items = self.history.items()
        images = sum(1 for i in items if i.kind == "image")
        pinned = sum(1 for i in items if i.pinned)
        self.history_stats.setText(
            f"Сейчас в истории: {plural(len(items), 'запись', 'записи', 'записей')} · "
            f"картинок: {images} · закреплено: {pinned}"
        )

    def _clear_history(self) -> None:
        if self.history:
            self.history.clear()
            self._update_stats()

    def _collect(self) -> Settings:
        return Settings(
            fix_word=self.hk_word.hotkey,
            fix_line=self.hk_line.hotkey,
            history_popup=self.hk_popup.hotkey,
            history_limit=self.limit.value(),
            save_images=self.save_images.isChecked(),
            switch_layout=self.switch_layout.isChecked(),
            show_toasts=self.show_toasts.isChecked(),
            autostart=self.autostart.isChecked(),
            autoswitch=self.autoswitch.isChecked(),
            toggle_autoswitch=self.hk_toggle.hotkey,
            autoswitch_sensitivity=self.sensitivity.value(),
            autoswitch_short_words=self.short_words.isChecked(),
            autoswitch_skip_fullscreen=self.skip_fullscreen.isChecked(),
            autoswitch_notify=self.autoswitch_notify.isChecked(),
            autoswitch_terminal_answers=self.terminal_answers.isChecked(),
            autoswitch_excluded=self.excluded.items(),
        )

    def _reset(self) -> None:
        defaults = Settings()
        self.hk_word.set_hotkey(defaults.fix_word)
        self.hk_line.set_hotkey(defaults.fix_line)
        self.hk_popup.set_hotkey(defaults.history_popup)
        self.hk_toggle.set_hotkey(defaults.toggle_autoswitch)
        self.autoswitch.setChecked(defaults.autoswitch)
        self.sensitivity.set_value(defaults.autoswitch_sensitivity)
        self.short_words.setChecked(defaults.autoswitch_short_words)
        self.skip_fullscreen.setChecked(defaults.autoswitch_skip_fullscreen)
        self.autoswitch_notify.setChecked(defaults.autoswitch_notify)
        self.terminal_answers.setChecked(defaults.autoswitch_terminal_answers)
        self.excluded.set_items(list(DEFAULT_EXCLUDED))
        self.limit.setValue(defaults.history_limit)
        self.save_images.setChecked(defaults.save_images)
        self.switch_layout.setChecked(defaults.switch_layout)
        self.show_toasts.setChecked(defaults.show_toasts)
        self.autostart.setChecked(defaults.autostart)

    def _save(self) -> None:
        new = self._collect()
        hotkeys = (new.fix_word, new.fix_line, new.history_popup, new.toggle_autoswitch)
        combos = [(h.mods, h.vk) for h in hotkeys if h.is_set]
        if len(combos) != len(set(combos)):
            QMessageBox.warning(self, APP_NAME, "Одно и то же сочетание назначено на два действия.")
            return
        if self.autoswitcher:
            # Only what was changed here: a Backspace undo may have added words while the window was open.
            words = {w.lower() for w in self.exception_words.items()}
            self.autoswitcher.update_exceptions(words - self._initial_words, self._initial_words - words)
            self._initial_words = words
        failed = self.apply(new)
        if failed:
            self.suspend()  # keep the hotkeys free while the window is still open
            QMessageBox.warning(
                self,
                APP_NAME,
                "Эти сочетания заняты другой программой и не работают:\n" + "\n".join(failed),
            )
            return
        self.accept()
