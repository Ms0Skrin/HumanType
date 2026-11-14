# Поддержка украинского — черновик (не закончено)

Ветка `ukrainian`. Сессия прервана на этапе исследования: код ещё **не менялся**, ниже — всё, что
выяснено, и план работ по шагам.

## Что сделано

- Прочитан весь код, найдены все места, где зашиты две раскладки (список ниже).
- Проверены реальные раскладки:
  - **XKB `ua`** (`/usr/share/X11/xkb/symbols/ua`, вариант по умолчанию `unicode`, включает `legacy`):
    `s`→і, `]`→ї, `'`→є, `\`→ґ, `` ` ``→`'` (U+0027), `Shift+`` ` ``→ʼ (U+02BC),
    `/`→`.`, `Shift+/`→`,` (как в ru), цифровой ряд с Shift `!"№;%:?*()` — как в `ru(winkeys)`.
    Остальные буквы совпадают с русской ЙЦУКЕН.
  - **Windows «Ukrainian (Enhanced)» (KBDUR1)**: `` ` ``→`'`, `Shift+`` ` ``→₴, `\`→`\`, `/`
    (ґ только на VK_OEM_102 — клавише ISO-клавиатур), `s`→і, `]`→ї, `'`→є, `/`→`.`/`,`.
  - **Windows «Ukrainian» (KBDUR, старая)**: `` ` ``→ё/Ё, остальное как Enhanced.
  - Вывод: таблица `EN_TO_UK` = RU-таблица с заменами ы→і, ъ→ї, э→є, ё→`'`, Ё→ʼ, плюс `\`→ґ, `|`→Ґ
    (по XKB; на Windows `\` остаётся `\`, это терпимо — слово с ґ редкость).
    Windows LANGID украинского: `0x0422`.
- Источник словарей: **hermitdave/FrequencyWords** (OpenSubtitles 2018), данные CC BY-SA 4.0, код MIT.
  Подтверждено, что существующие `ru.words`/`en.words` собраны из него же (первая строка
  `я 5081568` совпадает с `content/2018/ru/ru_50k.txt`). В репозитории источник нигде не указан —
  надо указать в README (раздел «Как это работает» или новый «Словари») и в `tools/build_dict.py`.
  Файл `content/2018/uk/uk_full.txt` (290 325 строк) скачан в `tools/raw/uk_full.txt`
  (папка в .gitignore). Особенности: апостроф встречается как `` ` `` (п`ять), есть комбинируемое
  ударение U+0301, ~6 % токенов — русские слова с ы/э/ъ/ё (мусор из русских субтитров) —
  отсекать регэкспом по украинскому алфавиту `абвгґдеєжзиіїйклмнопрстуфхцчшщьюя` + апостроф.
- Морфология: у pymorphy3 есть `pymorphy3-dicts-uk` (`MorphAnalyzer(path, lang="uk")`); подключить
  так же опционально, как ru (`HumanType.spec`, `linux/install.sh` строка pip, workflow).
- Исходные (baseline) результаты на `main`, до изменений (логи в `tools/raw/*.txt`):
  - `tests/corpus_eval.py`: **fixed when typed wrong: 76/76, left alone when typed right: 113/113**
  - `tests/units.py`: FAILS: 0
  - `tests/linux_engine.py`: FAILS: 0
  - `dbus-run-session -- python3 tests/linux_desktops.py`: FAILS: 0
  - `tests/sim_autoswitch.py`: не запускался — нет PySide6 (нет и python3-venv; ставить
    `pip install --target <dir> PySide6-Essentials pymorphy3 pymorphy3-dicts-ru pymorphy3-dicts-uk`
    и запускать с `PYTHONPATH=<dir>`).

## Где зашиты две раскладки (всё это надо править)

- `humantype/layout.py` — таблицы EN/RU, `LANG_EN/LANG_RU`, `convert()`.
- `humantype/detector.py` — `LETTERS`, `INNER`, `SINGLE`, загрузка словарей `for lang in ("ru","en")`,
  `decide()` (`other = ru if en else en`), `start_switch()`, `_dotted`, `_floor`, `Session.start/word`,
  `keys_of`; морфология только ru.
- `humantype/wordlists.py` — `LANGS = ("ru","en")`.
- `humantype/autoswitch.py` (Windows) — `LANGS`, `LANG_IDS`, `RU_LETTER_KEYS` (Caps Lock), `other()`,
  `char_of/render`, `_answer/_command` (`layout != "ru"`), `_flip` (`other(source)`), `_hkl`,
  `load_learned` (`parts[1] in LANG_IDS`), текст заметки «раскладка окна не русская и не английская».
- `humantype/linux/keymap.py` — `RU_LETTER_KEYS`, `char_of`; `humantype/linux/engine.py` — `other()`,
  `render`, `_answer` (`!= "ru"`), `_flip`, `_rewrite`, `_presses`, заметки «РУ/EN».
- `humantype/linux/daemon.py` — фильтр `app_layouts` `v in ("ru","en")`, метка `lang.upper()`.
- `humantype/linux/config.py` — `load_learned` `in ("ru","en")`, добавить настройку `ukrainian: bool = True`.
- `humantype/linux/desktops/common.py` — `XKB_LANG` (добавить `"ua": "uk"`), `NAME_LANG`
  (`("ukrain","uk"), ("укр","uk")`); остальные бэкенды (kde/x11/hyprland/sway/niri) берут список
  через `lang_of` — у каждого есть `self._langs`, нужен метод `languages()`.
- `linux/gnome-extension/.../extension.js` — `XKB_TO_LANG`/`LANG_TO_XKB` (добавить `ua`↔`uk`),
  новый метод D-Bus `GetLayouts() -> as` (какие языки есть среди источников ввода);
  `humantype/linux/shell.py` — `ShellClient.languages()` (при ошибке — старое расширение — None).
- `humantype/config.py` (Windows) — настройка `autoswitch_ukrainian: bool = True`;
  `humantype/settings_window.py` — строка-переключатель на странице «Автопереключение»,
  `_learned_text`, `_learned()` (`in ("ru","en")`), `_collect/_reset`; `humantype/app.py` — передать в
  `configure()`, `_on_flipped` («РУ/EN»).
- `humantype/fixer.py` — `LANG_NAMES`; `humantype/indicator.py` — `LOOK` (добавить `"uk": ("UA", …)`);
  `humantype/applayout.py` — через `LANG_IDS`, само подхватит.
- `tools/build_dict.py` (добавить uk + нормализация апострофов), `tools/fetch_wiki.py`,
  `tools/eval_detector.py` (ru/en), `tests/corpus_eval.py`, `tests/linux_engine.py`,
  `tests/sim_autoswitch.py`, `tests/units.py`.
- README: таблица возможностей («Умный словарь», индикатор `RU / EN`), настройки Linux в
  комментарии, раздел «Как это работает», тесты; `docs/banner.svg` — текст «ru / en».

## План (ключевые решения)

1. **layout.py**: `EN_TO_UK/UK_TO_EN`, `TO = {"ru":…, "uk":…}`, `text_of(keys, lang)`, `keys_of(text, lang)`,
   `LANG_ID = {"en":0x0409,"ru":0x0419,"uk":0x0422}`, метки `{"ru":"RU","en":"EN","uk":"UA"}`.
   `convert()` для буфера обмена: латиница → предпочитаемый кириллический язык (последняя
   использованная кириллическая раскладка, по умолчанию ru), кириллица с і/ї/є/ґ → из uk.
2. **detector.py**: `Detector(dict_dir, morphology, langs=("ru","en"))`, `set_langs()` подгружает
   словари по требованию; `decide(keys, current, context, prefer)`: читает слово во **всех**
   включённых языках, кроме текущего, выбирает лучшего кандидата `_best()`:
   ранг = (текст отличается от текущего, известно > похоже по n-граммам > невалидно, частота/n-грамма);
   при почти равных (частота в пределах 1.5 log10, n-грамма в пределах 0.5) — сначала язык контекста
   (соседних слов), потом `prefer` (последняя кириллическая раскладка), потом порядок `langs`.
   Дальше — существующая логика «текущее vs другое» без изменений → при `langs=("ru","en")`
   поведение бит-в-бит прежнее (один кандидат). Слова, одинаковые в ru и uk («мама», «так»):
   кандидат с тем же текстом ранжируется ниже → переключения не будет, «туда-сюда» нет.
   `start_switch()` — те же кандидаты, при нескольких прошедших — по частоте начала слова.
   `flip_target(keys, source, prefer)` для Pause: лучший кандидат, при ru+en всегда прежний.
   `SINGLE["uk"] = {і,й,у,в,з,а,о,я}` (без є/ж/б — они на клавишах `' ; ,`, иначе одиночная
   пунктуация в en превращалась бы в букву). `INNER["uk"] = {-, ', ʼ, ’}`, ключ словаря
   нормализует ʼ/’/`` ` `` → `'`. `_floor("uk") = -7.0`.
   `Session.prefer` — последняя кириллическая раскладка, не сбрасывается в `reset()`.
3. **Включение uk**: только когда (а) словарь `assets/dict/uk.*` есть, (б) настройка включена,
   (в) раскладка установлена: Windows — `GetKeyboardLayoutList()` содержит `0x0422`
   (проверять в `configure()` и при смене окна); Linux — `shell.languages()` (бэкенды: `set(_langs)`,
   GNOME — `GetLayouts`), пересчитывать при появлении оболочки, перечитывании настроек и когда
   `_on_layout` приносит язык, которого нет в `detector.langs`. ru и en включены всегда (паритет);
   если ru не установлен, а uk есть — порядок `("uk","en","ru")`, чтобы ничьи шли в uk.
   Раскладка, которой нет в `detector.langs` (uk при выключенной настройке), для движков = `None`,
   т.е. как сейчас (слова в ней не трогаются).
4. **Caps Lock**: `UK_LETTER_KEYS = RU_LETTER_KEYS - {GRAVE} + {BACKSLASH}` (апостроф регистра не имеет).
5. **Ответы в терминале / команды**: условие `layout != "ru"` → `layout == "en"` (любая кириллица).
6. **Словарь**: `tools/build_dict.py` — `"uk": (regex по украинскому алфавиту с апострофом, 300_000)`,
   собрать `assets/dict/uk.words` и `uk.ngram` (`python3 tools/build_dict.py uk`).
7. **Тесты**: `tests/uk_corpus.txt` (свои украинские фразы), `corpus_eval.py` — первая строка вывода
   как прежде (ru/en при `langs=("ru","en")`), затем ru/en с включённым uk и uk-строки, набранные
   в en- и в ru-раскладке; `units.py`, `linux_engine.py`, `sim_autoswitch.py` — случаи
   en→uk (`ghbdsn`→привіт), uk→ru (`місли`→мысли), ru→uk (`привыт`→привіт), одинаковые слова не
   трогаются, апостроф (`g'znm`→п'ять), Pause при трёх раскладках, uk выключен → раскладка uk игнорируется.
8. README (по-русски, в том же стиле), `docs/banner.svg`, упоминание источника словарей (FrequencyWords,
   OpenSubtitles 2018, CC BY-SA 4.0), `HumanType.spec`/`install.sh`/workflow — `pymorphy3-dicts-uk`.

## Открытые вопросы

- Метка индикатора для украинского: «UA» (как имя раскладки xkb) или «UK»/«УКР»? Пока план — «UA».
- На Windows `\` в uk-раскладке даёт `\`, а не ґ (ґ только на VK_OEM_102): таблица по XKB,
  расхождение терпимое, но стоит упомянуть в README как ограничение.
- `tests/linux_e2e.py` и `e2e_autoswitch.py` (живые окружения) не трогались.
