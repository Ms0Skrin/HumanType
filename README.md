<div align="center">

<img src="docs/banner.svg" alt="HumanType — автопереключение раскладки ru / en" width="100%">

<br>

[![Сборка](https://img.shields.io/github/actions/workflow/status/Ms0Skrin/HumanType/build.yml?branch=main&style=for-the-badge&label=%D1%81%D0%B1%D0%BE%D1%80%D0%BA%D0%B0&labelColor=0F172A&color=38BDF8)](https://github.com/Ms0Skrin/HumanType/actions/workflows/build.yml)
[![Релиз](https://img.shields.io/github/v/release/Ms0Skrin/HumanType?style=for-the-badge&label=%D1%80%D0%B5%D0%BB%D0%B8%D0%B7&labelColor=0F172A&color=38BDF8)](https://github.com/Ms0Skrin/HumanType/releases/latest)
![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-38BDF8?style=for-the-badge&logo=windows&logoColor=white&labelColor=0F172A)
![Linux](https://img.shields.io/badge/Linux-GNOME%20%7C%20KDE%20%7C%20Hyprland%20%7C%20Sway%20%7C%20X11-38BDF8?style=for-the-badge&logo=linux&logoColor=white&labelColor=0F172A)
[![MIT](https://img.shields.io/badge/%D0%BB%D0%B8%D1%86%D0%B5%D0%BD%D0%B7%D0%B8%D1%8F-MIT-38BDF8?style=for-the-badge&labelColor=0F172A)](LICENSE)

**Пишешь — и не думаешь о раскладке.**<br>
Набрал `ghbdtn` и пробел — получилось `привет`, а раскладка сама стала русской. Пишешь дальше.

<img src="docs/demo.svg" alt="ghbdtn превращается в привет" width="680">

[Скачать для Windows](#-windows) · [Установить на Linux](#-linux) · [Горячие клавиши](#%EF%B8%8F-горячие-клавиши) · [Как это работает](#-как-это-работает)

</div>

---

## ✨ Возможности

| | |
|---|---|
| ⚡ **Исправляет на лету** | Пробел, Enter или Tab после слова в чужой раскладке — слово перепечатывается, раскладка переключается. Работает и для нескольких слов подряд: `z nt,z k.,k.` → `я тебя люблю`. |
| 🚀 **Не ждёт пробела** | Слово, которое явно из другой раскладки, исправляется уже на пятой букве: набрал `ghbdt` — на экране `приве`, дальше пишешь по-русски. Только Windows; выключается в настройках. |
| ↩️ **Отмена одной клавишей** | Backspace сразу после исправления возвращает слово как было и запоминает его — больше не тронет. |
| 🎯 **Умный словарь** | Частотные словари русского и английского, n-граммы, проверка коротких слов. Ссылки, имена файлов, команды и код не трогает. |
| 🔒 **Бережно к приватному** | Поля паролей, менеджеры паролей, удалённые рабочие столы и игры на весь экран пропускаются. Текст никуда не отправляется. |
| 🖥️ **Везде, и в терминале** | `cmd`, PowerShell, Windows Terminal, редакторы кода — и окна, запущенные от администратора. |
| 🧠 **Видит, что в поле** | Следит за всей строкой: не исправляет слово, начало которого набрано до перехода стрелками, и слово, набранное в двух раскладках. Сверяется с текстом перед курсором (Windows — UI Automation, Linux — AT-SPI): автодополнение и вставки не ломают исправление, а после стрелок слово исправляется, если поле подтверждает, что оно началось там. |
| 💻 **Ответы и команды в терминале** | `н`, `нуы`, `т`, `тщ` по Enter становятся `y`, `yes`, `n`, `no`; команда в начале строки — тоже: `ды` → `ls`, `пше ыефегы` → `git status`. |
| 📚 **Учится на ваших исправлениях** | Слово, исправленное клавишей <kbd>Pause</kbd>, дальше исправляется само. Список — в настройках, «Выученные слова». |
| 🔎 **Почему не исправилось?** | Пункт в меню трея: последние слова и причина решения по каждому. Хранится только в памяти. |
| 🪟 **Своя раскладка у каждой программы** | Запоминает, на каком языке вы пишете в программе, и возвращает его при переходе в неё: в терминале — английский, в мессенджере — русский. Windows. |
| 🏷️ **Индикатор раскладки** | Маленькая метка `RU` / `EN` у текстового курсора, когда раскладка меняется. В играх на весь экран не показывается. Windows. |
| 🔤 **Регистр и транслит** | <kbd>Scroll Lock</kbd> исправляет `пРИВЕТ` → `Привет`, <kbd>Shift</kbd> + <kbd>Scroll Lock</kbd> превращает `privet` в `привет` и обратно. Windows. |
| 📊 **Статистика** | Сколько слов исправлено за день, неделю и всё время, и какие исправления вы чаще всего отменяете — их можно в один клик отправить в исключения. Windows. |
| 🔁 **Перенос слов** | Выученные слова и исключения сохраняются в один файл и загружаются на другом компьютере — с Windows на Linux и обратно. |
| 📋 **История буфера** | <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>V</kbd> — список всего скопированного, текст и картинки: поиск, закрепление, вставка в одно нажатие. Windows и GNOME. Скопированное из менеджеров паролей не запоминается. |

## ⌨️ Горячие клавиши

| Клавиши | Что делает |
|---|---|
| <kbd>Pause</kbd> | Перевернуть последнее слово в другую раскладку |
| <kbd>Shift</kbd> + <kbd>Pause</kbd> | Перевернуть всю набранную строку |
| <kbd>Alt</kbd> + <kbd>Pause</kbd> | Включить / выключить автопереключение |
| <kbd>Scroll Lock</kbd> | Исправить регистр: `пРИВЕТ` → `Привет`; ещё раз — по кругу `привет` → `Привет` → `ПРИВЕТ` (Windows) |
| <kbd>Shift</kbd> + <kbd>Scroll Lock</kbd> | Транслит: `privet` ↔ `привет` (Windows) |
| <kbd>Backspace</kbd> сразу после исправления | Отменить и запомнить слово |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>V</kbd> | История буфера обмена (в терминалах Linux остаётся вставкой) |

Только что набранное <kbd>Pause</kbd> перепечатывает сам, не трогая буфер обмена; выделенный текст и написанное раньше — через буфер обмена. То, что лежало в буфере, возвращается целиком: текст с оформлением, картинки, скопированные файлы.

Все сочетания меняются в настройках.

## 🪟 Windows

Скачайте **HumanType-Setup.exe** со страницы [последнего релиза](https://github.com/Ms0Skrin/HumanType/releases/latest) и запустите.
Программа ставится для текущего пользователя и запускается вместе с Windows. Значок появится в трее.
При первом запуске Windows один раз спросит разрешение: с правами администратора HumanType работает и в окнах, открытых от администратора (без них Windows не даёт видеть там набор). Дальше программа стартует через Планировщик заданий без вопросов. Отказаться можно в настройках: «Поведение» → «Работать в окнах администратора».

## 🐧 Linux

| Оболочка | Раскладка | Окно и полноэкранный режим | Пароли и текст перед курсором | Значок |
|---|---|---|---|---|
| **GNOME** 46–51 (Wayland, X11) | ✅ расширение | ✅ | ✅ расширение | панель сверху |
| **KDE Plasma** 5 / 6 | ✅ D-Bus | ✅ скрипт KWin | ✅ AT-SPI | трей |
| **Hyprland** | ✅ IPC | ✅ | ✅ AT-SPI | waybar |
| **Sway** | ✅ IPC | ✅ | ✅ AT-SPI | waybar |
| **X11**: Xfce, Cinnamon, MATE, LXQt, i3… | ✅ XKB | ✅ EWMH | ✅ AT-SPI | трей |

Установщик сам определит оболочку.

> [!IMPORTANT]
> На живом GNOME всё проверено сквозным тестом. KDE, Hyprland, Sway и X11 пока проверены только на поддельных серверах — если что-то не так, пришлите вывод `journalctl --user -u humantype` в Issues.

```bash
# зависимости — Arch / CachyOS
sudo pacman -S --needed git python-evdev python-gobject
# Fedora:  sudo dnf install git python3-evdev python3-gobject
# Ubuntu:  sudo apt install git python3-evdev python3-gi

git clone https://github.com/Ms0Skrin/HumanType.git ~/.local/share/humantype-app
~/.local/share/humantype-app/linux/install.sh --release
```

Версия у Windows и Linux одна: `--release` ставит последний релиз — тот же, что и установщик для Windows (`--release v0.6` — конкретный). Без `--release` ставится самый свежий код из `main`.

Установщик один раз спросит пароль sudo, поставит пункт в меню приложений и службу, которая стартует при входе.

- **GNOME:** ставится ещё и расширение — **выйдите из сеанса и войдите снова**, значок появится справа вверху.
- **KDE, Xfce, Cinnamon, MATE:** всё запускается сразу, значок — в трее. Клик по значку включает и выключает.
- **Hyprland, Sway:** установщик покажет одну строку для `hyprland.conf` / `sway/config`, чтобы служба стартовала при входе.

<details>
<summary><b>Обновить, удалить, посмотреть журнал</b></summary>

```bash
# обновить до последнего релиза
~/.local/share/humantype-app/linux/install.sh --release
# или до самого свежего кода
git -C ~/.local/share/humantype-app checkout main && git -C ~/.local/share/humantype-app pull && ~/.local/share/humantype-app/linux/install.sh
# удалить
~/.local/share/humantype-app/linux/install.sh --uninstall
# журнал службы
journalctl --user -u humantype -f
# настройки
~/.config/humantype/settings.json      # и exceptions.txt — слова, которые не трогать
# перенос исключений: файл тот же, что сохраняют настройки в Windows
cd ~/.local/share/humantype-app
python3 -m humantype.linux.ctl export-words ~/humantype-words.json
python3 -m humantype.linux.ctl import-words ~/humantype-words.json
#   история буфера: "history", "history_hotkey" ("ctrl+shift+v"), "history_limit", "history_images"
~/.local/share/humantype/history.db    # сама история (картинки — рядом, в images/)
```

</details>

## 🔍 Как это работает

```mermaid
flowchart LR
    K[⌨️ Клавиатура] --> D[Служба HumanType]
    D -- клавиши --> T[Виртуальная клавиатура] --> A[Приложение]
    D <-- раскладка, тип поля --> S[GNOME · KDE · Hyprland · Sway · X11 · Win32]
    D --> E{Слово в чужой<br>раскладке?}
    E -- да --> F[Стереть → переключить<br>раскладку → перепечатать]
```

- **Windows:** низкоуровневый хук клавиатуры, ввод через `SendInput`, поля паролей распознаются через MSAA, текст перед курсором — через UI Automation, и только в момент исправления.
- **Linux:** служба читает клавиатуру через `evdev` и передаёт клавиши дальше через виртуальную клавиатуру `uinput`. На GNOME раскладку, тип поля и текст перед курсором сообщает расширение GNOME Shell; в KDE — D-Bus и скрипт KWin, в Hyprland и Sway — их IPC, в X11 — XKB; тип поля и текст — через AT-SPI. Сам текст никуда не отправляется: служба лишь сверяет, совпадает ли он с набранным.
- Если служба зависнет, через 3 секунды она отпускает клавиатуру и перезапускается.

> [!NOTE]
> На Linux установщик добавляет правило udev, которое даёт программам вашего сеанса право читать клавиатуру. На Wayland без этого программа автопереключения невозможна.

## 🧪 Тесты

```bash
python tests/linux_engine.py     # движок Linux, без устройств
python tests/linux_e2e.py        # живой GNOME: виртуальная клавиатура печатает в тестовое окно
dbus-run-session -- python tests/linux_desktops.py   # KDE, Hyprland, Sway на поддельных серверах
python tests/sim_autoswitch.py   # логика Windows-версии с заглушками Win32
python tests/units.py            # детектор «на ходу», транслит, регистр, файл слов, статистика
python tests/uia_probe.py        # Windows: UI Automation читает текст перед курсором в окне Qt
```

Тесты прогоняются в [GitHub Actions](https://github.com/Ms0Skrin/HumanType/actions) на каждый push; на тег `v*` собирается установщик для Windows и публикуется релиз.

<details>
<summary><b>Собрать Windows-версию самому</b></summary>

Нужны Python 3.12+, `pip install pyside6 pywin32 comtypes pyinstaller` и [Inno Setup 6](https://jrsoftware.org/isdl.php).
Запустите `build.bat` — получится `installer\HumanType-Setup-<версия>.exe`.

</details>

## 📄 Лицензия

[MIT](LICENSE) — можно использовать, менять и распространять, сохраняя упоминание автора.

<div align="center">
<br>
<sub>Сделано с ❤️ для тех, кто пишет на двух языках</sub>
</div>
