"""Converting text typed in the wrong keyboard layout (EN <-> RU, ЙЦУКЕН)."""

EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./" + '~QWERTYUIOP{}ASDFGHJKL:"ZXCVBNM<>?@#$^&'
RU = "ёйцукенгшщзхъфывапролджэячсмитьбю." + 'ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,"№;:?'

EN_TO_RU = str.maketrans(EN, RU)
RU_TO_EN = str.maketrans(RU, EN)

LANG_EN = 0x0409
LANG_RU = 0x0419


def _count(text: str) -> tuple[int, int]:
    latin = sum(1 for c in text if "a" <= c.lower() <= "z")
    cyr = sum(1 for c in text if "а" <= c.lower() <= "я" or c in "ёЁ")
    return latin, cyr


def convert(text: str) -> tuple[str, int]:
    """Returns the converted text and the language it now belongs to."""
    latin, cyr = _count(text)
    if latin >= cyr:
        return text.translate(EN_TO_RU), LANG_RU
    return text.translate(RU_TO_EN), LANG_EN
