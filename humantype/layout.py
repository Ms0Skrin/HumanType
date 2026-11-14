"""Converting text typed in the wrong keyboard layout: English (QWERTY), Russian and Ukrainian (ЙЦУКЕН).

Every language is described by what its layout gives for the keys of the US layout. The Ukrainian
layout is the Russian one but for a few keys: і for ы, ї for ъ, є for э, an apostrophe for ё
(ʼ with Shift, as XKB `ua` has it; Windows gives ₴ there) and ґ on the backslash key (XKB; on
Windows that key stays a backslash and ґ sits on the extra key of ISO keyboards)."""

EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./" + '~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?@#$^&'
RU = "ёйцукенгшщзхъфывапролджэячсмитьбю." + 'ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,"№;:?'
UK = "'йцукенгшщзхїфівапролджєячсмитьбю." + 'ʼЙЦУКЕНГШЩЗХЇФІВАПРОЛДЖЄЯЧСМИТЬБЮ,"№;:?'

EN_TO_RU = str.maketrans(EN, RU)
RU_TO_EN = str.maketrans(RU, EN)
EN_TO_UK = str.maketrans(EN + "\\|", UK + "ґҐ")
UK_TO_EN = str.maketrans(UK + "ґҐ’", EN + "\\|~")

LANG_EN = 0x0409
LANG_RU = 0x0419
LANG_UK = 0x0422

LANGS = ("ru", "en", "uk")  # every language there is; which are in use is the detector's to say
CYRILLIC = ("ru", "uk")
LANG_IDS = {"en": LANG_EN, "ru": LANG_RU, "uk": LANG_UK}  # Windows language identifiers
ID_LANGS = {v: k for k, v in LANG_IDS.items()}
LABELS = {"en": "EN", "ru": "RU", "uk": "UA"}  # the mark of a layout, as the system's own indicator has it
NOTE_LABELS = {"en": "EN", "ru": "РУ", "uk": "УКР"}  # in the short notes: «✓ РУ: привет»
_FROM_EN = {"ru": EN_TO_RU, "uk": EN_TO_UK}
_TO_EN = {"ru": RU_TO_EN, "uk": UK_TO_EN}
UK_ONLY = set("іїєґІЇЄҐ")  # letters no Russian text has

# The Cyrillic layout a Latin text is turned into by the hotkey when nothing else tells: the one
# last typed in (set by the automatic switcher), Russian until then.
cyrillic = "ru"


def text_of(keys: str, lang: str) -> str:
    """What the keys, spelled as the US layout prints them, give in the layout of `lang`."""
    table = _FROM_EN.get(lang)
    return keys.translate(table) if table else keys


def keys_of(text: str, lang: str) -> str:
    """How the US layout spells the keys that type `text` in the layout of `lang`."""
    table = _TO_EN.get(lang)
    return text.translate(table) if table else text


def _count(text: str) -> tuple[int, int]:
    latin = sum(1 for c in text if "a" <= c.lower() <= "z")
    cyr = sum(1 for c in text if "а" <= c.lower() <= "я" or c in "ёЁ" or c in UK_ONLY)
    return latin, cyr


def convert(text: str) -> tuple[str, int]:
    """Returns the converted text and the language it now belongs to."""
    latin, cyr = _count(text)
    if latin >= cyr:
        return text_of(text, cyrillic), LANG_IDS[cyrillic]
    source = "uk" if any(c in UK_ONLY for c in text) else "ru"
    return keys_of(text, source), LANG_EN
