# HumanType

Автопереключение раскладки: набрал «ghbdtn» и пробел — получилось «привет», раскладка сама стала русской.

- Пробел, Enter или Tab после слова в неверной раскладке — слово перепечатывается, раскладка переключается.
- Backspace сразу после исправления — отмена; слово запоминается и больше не трогается.
- Pause — перевернуть последнее слово, Shift+Pause — всю строку, Alt+Pause — включить/выключить.
- Пароли, терминалы, редакторы кода, менеджеры паролей не трогаются.
- В терминале «н», «нуы», «т», «тщ» по Enter становятся y / yes / n / no.

## Linux (GNOME на Wayland, GNOME 46–51)

```bash
sudo pacman -S --needed git python-evdev python-gobject      # Arch / CachyOS
git clone https://github.com/Ms0Skrin/HumanType.git ~/.local/share/humantype-app
~/.local/share/humantype-app/linux/install.sh
```

Установщик один раз спросит пароль sudo (правило udev, чтобы служба могла читать клавиатуру),
поставит расширение GNOME, пункт в меню приложений и службу, которая запускается при входе.
После установки выйдите из сеанса и войдите снова — GNOME загружает новые расширения только при входе.
Значок HumanType появится справа вверху на панели.

Обновить: `git -C ~/.local/share/humantype-app pull && ~/.local/share/humantype-app/linux/install.sh`
Удалить: `~/.local/share/humantype-app/linux/install.sh --uninstall`
Журнал: `journalctl --user -u humantype -f`

Правило udev даёт программам вашего сеанса право читать клавиатуру — без этого на Wayland такая программа невозможна.

## Windows

Запуск из исходников: `pythonw run.pyw` (нужны PySide6 и pywin32).
Установщик: `build.bat` (PyInstaller + Inno Setup) собирает `Output\HumanType-Setup-*.exe`.

## Тесты

```bash
python tests/linux_engine.py     # движок, без устройств
python tests/linux_e2e.py        # на живом GNOME, виртуальная клавиатура в тестовое окно
python tests/sim_autoswitch.py   # логика Windows-версии с заглушками Win32
```
