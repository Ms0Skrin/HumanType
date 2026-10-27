"""evdev key codes and the characters the US layout gives them (the detector's spelling of keys)."""

from evdev import ecodes as e

# key code -> (plain, with Shift) in the US layout
US_KEYS: dict[int, tuple[str, str]] = {
    getattr(e, f"KEY_{c}"): (c.lower(), c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
}
US_KEYS.update({getattr(e, f"KEY_{i}"): (str(i), ")!@#$%^&*("[i]) for i in range(10)})
US_KEYS.update(
    {
        e.KEY_SEMICOLON: (";", ":"),
        e.KEY_EQUAL: ("=", "+"),
        e.KEY_COMMA: (",", "<"),
        e.KEY_MINUS: ("-", "_"),
        e.KEY_DOT: (".", ">"),
        e.KEY_SLASH: ("/", "?"),
        e.KEY_GRAVE: ("`", "~"),
        e.KEY_LEFTBRACE: ("[", "{"),
        e.KEY_BACKSLASH: ("\\", "|"),
        e.KEY_RIGHTBRACE: ("]", "}"),
        e.KEY_APOSTROPHE: ("'", '"'),
    }
)
# ж б ю ё х ъ э: letters in ЙЦУКЕН, so Caps Lock applies to them there
RU_LETTER_KEYS = {
    e.KEY_SEMICOLON, e.KEY_COMMA, e.KEY_DOT, e.KEY_GRAVE, e.KEY_LEFTBRACE, e.KEY_RIGHTBRACE, e.KEY_APOSTROPHE,
}
LETTER_KEYS = {getattr(e, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}

SHIFTS = {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT}
CTRLS = {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL}
ALTS = {e.KEY_LEFTALT, e.KEY_RIGHTALT}
METAS = {e.KEY_LEFTMETA, e.KEY_RIGHTMETA}
MODIFIERS = SHIFTS | CTRLS | ALTS | METAS | {e.KEY_CAPSLOCK, e.KEY_NUMLOCK, e.KEY_SCROLLLOCK, e.KEY_COMPOSE}

# Ends a word and gets it judged. Interior delimiters of a run are always spaces.
DELIMITERS = {e.KEY_SPACE, e.KEY_ENTER, e.KEY_KPENTER, e.KEY_TAB}


def char_of(code: int, shift: bool, caps: bool, layout: str) -> str | None:
    """The US spelling of a key press, with Caps Lock applied as it would be in `layout`."""
    pair = US_KEYS.get(code)
    if pair is None:
        return None
    is_letter = code in LETTER_KEYS or (layout == "ru" and code in RU_LETTER_KEYS)
    return pair[1] if shift != (caps and is_letter) else pair[0]
