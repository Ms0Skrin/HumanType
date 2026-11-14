"""Runs anywhere: the parts with no window and no keyboard in them: the detector's judgement of a
word still being typed, transliteration and letter case, the word-list file, the statistics."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from humantype import textops, wordlists  # noqa: E402
from humantype import layout as layouts  # noqa: E402
from humantype.detector import Detector, Session, keys_of  # noqa: E402
from humantype.stats import Stats  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("OK  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


# --- a word still being typed ---
d = Detector(ROOT / "assets" / "dict")
for keys, layout, want in [
    ("ghbdt", "en", "приве"), ("Ghbdt", "en", "Приве"), ("rjvgm", "en", "компь"), ("k.,jd", "en", "любов"),
    ("hello", "en", None), ("pytes", "en", None), ("kubec", "en", None), ("GHBDT", "en", None), ("Duke,", "en", None),
    ("https", "en", None), ("user1", "en", None), ("ghbd", "en", None),
    (keys_of("руддщ", "ru"), "ru", "hello"), (keys_of("приве", "ru"), "ru", None), (keys_of("котор", "ru"), "ru", None),
]:
    check(f"start: {keys} in {layout}", d.start_switch(keys, layout), want)
d.exceptions = {"ghbdtn"}
check("start: the beginning of an exception", d.start_switch("ghbdt", "en"), None)
d.exceptions = set()
d.learned = {"ghbdtn": "en"}
check("start: the beginning of a word taught to stay", d.start_switch("ghbdt", "en"), None)
d.learned = {}

# --- transliteration ---
for text, want in [
    ("privet", "привет"), ("Privet kak dela", "Привет как дела"), ("PRIVET", "ПРИВЕТ"), ("zhizn'", "жизнь"),
    ("shchuka", "щука"), ("moy kray", "мой край"), ("krasnyy siniy", "красный синий"), ("yabloko", "яблоко"),
    ("привет", "privet"), ("Щука и Ёж", "Shchuka i Yozh"), ("ЖИЗНЬ", "ZHIZN"), ("Жук", "Zhuk"), ("123", "123"),
]:
    check(f"translit: {text}", textops.translit(text)[0], want)

# --- letter case ---
for text, want in [
    ("пРИВЕТ", "Привет"), ("hELLO wORLD", "Hello World"), ("пРИВЕТ КАК ДЕЛА", "Привет как дела"),
    ("привет", "Привет"), ("Привет", "ПРИВЕТ"), ("ПРИВЕТ", "привет"), ("iPhone", "iphone"),
    ("привет мир", "Привет мир"), ("(привет)", "(Привет)"), ("a b", "A b"), ("123", "123"),
]:
    check(f"case: {text}", textops.recase(text)[0], want)

# --- the word-list file ---
text = wordlists.dump({"abc", "ghbdtn"}, {"qwzx": "ru"})
check("words: there and back", wordlists.parse(text), ({"abc", "ghbdtn"}, {"qwzx": "ru"}))
check("words: a plain list", wordlists.parse("﻿abc\nGhbdtn\n\nqwzx ru\n"), ({"abc", "ghbdtn"}, {"qwzx": "ru"}))
check("words: an empty file", wordlists.parse(""), (set(), {}))
for bad in ("a b c d", '{"exceptions": 5}', "{not json"):
    try:
        wordlists.parse(bad)
        check(f"words: {bad!r} refused", False, True)
    except ValueError:
        check(f"words: {bad!r} refused", True, True)

check("words: a Ukrainian learned word", wordlists.parse("ghbdsn uk\n"), (set(), {"ghbdsn": "uk"}))

# --- the Ukrainian layout: the keys that differ from the Russian one ---
check("layout: uk text", layouts.text_of("ghbdsn g`znm ]] 't \\fyjr", "uk"), "привіт п'ять її єе ґанок")
check("layout: uk keys", layouts.keys_of("привіт п'ять її є ґанок, так.", "uk"), "ghbdsn g`znm ]] ' \\fyjr? nfr/")
check("layout: uk capitals", layouts.text_of("}S\"|~", "uk"), "ЇІЄҐʼ")
check("layout: ru as ever", (layouts.text_of("ghbdtn ]s'`", "ru"), layouts.keys_of("привет ъыэё", "ru")), ("привет ъыэё", "ghbdtn ]s'`"))
check("layout: convert, Latin", layouts.convert("ghbdtn"), ("привет", layouts.LANG_RU))
check("layout: convert, Russian", layouts.convert("руддщ"), ("hello", layouts.LANG_EN))
check("layout: convert, Ukrainian", layouts.convert("руддщ шіті"), ("hello isns", layouts.LANG_EN))
layouts.cyrillic = "uk"
check("layout: convert, Latin after the Ukrainian layout", layouts.convert("ghbdsn"), ("привіт", layouts.LANG_UK))
layouts.cyrillic = "ru"

# --- three languages ---
check("uk: two languages unless told", d.langs, ("ru", "en"))
d.set_langs(("ru", "en", "uk"))


def decided(text, lang, typed_in, context=None, prefer=None):
    r = d.decide(keys_of(text, lang), typed_in, context, prefer)
    return (r.target, r.text) if r.switch else None


for text, lang, typed_in, want in [
    ("привіт", "uk", "en", ("uk", "привіт")), ("привет", "ru", "en", ("ru", "привет")),
    ("сьогодні", "uk", "en", ("uk", "сьогодні")), ("сегодня", "ru", "en", ("ru", "сегодня")),
    ("п'ять", "uk", "en", ("uk", "п'ять")), ("їжак", "uk", "en", ("uk", "їжак")), ("объект", "ru", "en", ("ru", "объект")),
    # between the Cyrillic layouts: only where the keys differ
    ("привіт", "uk", "ru", ("uk", "привіт")), ("тільки", "uk", "ru", ("uk", "тільки")), ("її", "uk", "ru", ("uk", "її")),
    ("мысли", "ru", "uk", ("ru", "мысли")), ("это", "ru", "uk", ("ru", "это")), ("объект", "ru", "uk", ("ru", "объект")),
    # spelled alike in both: nothing to fix, no going back and forth
    ("мама", "ru", "uk", None), ("мама", "uk", "ru", None), ("так", "ru", "uk", None), ("так", "uk", "ru", None),
    ("вона", "uk", "uk", None), ("привіт", "uk", "uk", None), ("привет", "ru", "ru", None), ("ты", "ru", "ru", None),
    ("ти", "uk", "uk", None), ("ті", "uk", "uk", None), ("сир", "uk", "uk", None), ("сыр", "ru", "ru", None),
    # a word only the other language has, typed alike in both: the layout follows, the text stays
    ("привет", "ru", "uk", ("ru", "привет")), ("дякую", "uk", "ru", ("uk", "дякую")),
    # English
    ("hello", "en", "uk", ("en", "hello")), ("hello", "en", "ru", ("en", "hello")), ("hello", "en", "en", None),
    ("system", "en", "uk", ("en", "system")), ("the", "en", "en", None), ("is", "en", "en", None), ("it's", "en", "en", None),
]:
    check(f"uk: {text} typed in {typed_in}", decided(text, lang, typed_in), want)
check("uk: a shared word from Latin goes to Russian first", decided("мама", "ru", "en"), ("ru", "мама"))
check("uk: ... to Ukrainian after Ukrainian neighbours", decided("мама", "ru", "en", context="uk"), ("uk", "мама"))
check("uk: ... or when Ukrainian was typed in last", decided("мама", "ru", "en", prefer="uk"), ("uk", "мама"))
check("uk: a Russian word is Russian all the same", decided("привет", "ru", "en", prefer="uk"), ("ru", "привет"))
check("uk: a Ukrainian word is Ukrainian all the same", decided("привіт", "uk", "en", prefer="ru"), ("uk", "привіт"))
for keys, typed_in, want in [("ghbds", "en", ("приві", "uk")), ("ghbdt", "en", ("приве", "ru")), ("lzre.", "en", ("дякую", "uk")),
                             ("hello", "en", None), (keys_of("руддщ", "uk"), "uk", ("hello", "en")), ("ghbds", "uk", None)]:
    check(f"uk: start {keys} in {typed_in}", d.start_target(keys, typed_in), want)
check("uk: flip, a Ukrainian word", d.flip_target("ghbdsn", "en"), "uk")
check("uk: flip, a Russian word", d.flip_target("ghbdtn", "en"), "ru")
check("uk: flip, Cyrillic back to Latin", (d.flip_target("ghbdtn", "ru"), d.flip_target("ghbdsn", "uk")), ("en", "en"))
check("uk: flip, between the Cyrillic layouts", (d.flip_target("ghbdsn", "ru"), d.flip_target("vsckb", "uk")), ("uk", "ru"))
# a line typed word by word: the short words wait for a neighbour and go where it goes
session = Session(d)


def typed(keys, typed_in):
    session.reset()
    session.prefer = None
    screen = ""
    for word in keys.split(" "):
        screen += layouts.text_of(word, typed_in)
        fix = session.word(word, typed_in)
        if fix:
            screen, typed_in = screen[: len(screen) - fix.erase] + fix.text, fix.layout
        screen += " "
    return screen.rstrip(), typed_in


check("uk: line en → uk", typed(keys_of("я не розумію, що це таке", "uk"), "en"), ("я не розумію, що це таке", "uk"))
check("uk: line en → ru", typed(keys_of("я не знаю, что это такое", "ru"), "en"), ("я не знаю, что это такое", "ru"))
check("uk: line ru → uk", typed(keys_of("він сказав, що прийде сьогодні", "uk"), "ru"), ("він сказав, що прийде сьогодні", "uk"))
check("uk: line uk → ru", typed(keys_of("мы были там вчера вечером", "ru"), "uk"), ("мы были там вчера вечером", "ru"))
check("uk: line of shared words stays", typed(keys_of("мама там на так", "uk"), "uk"), ("мама там на так", "uk"))
d.set_langs(("ru", "en"))
check("uk: back to two languages", (d.langs, d.decide("ghbdsn", "en").target != "uk", d.decide("ghbdsn", "ru").switch),
      (("ru", "en"), True, False))
check("uk: a list with no word list is left out", (d.set_langs(("ru", "en", "xx")), d.langs)[1], ("ru", "en"))

# --- statistics ---
path = Path(tempfile.mkdtemp()) / "stats.json"
stats = Stats(path)
for _ in range(3):
    stats.add("fixed")
stats.add("manual")
stats.add("undone", "ghbdtn")
stats.add("undone", "ghbdtn")
stats.add("undone", "yt")
stats.save()
again = Stats(path)
check("stats: today", again.count("fixed", 1), 3)
check("stats: all time", again.count("undone"), 3)
check("stats: the most undone first", again.top_undone(), [("ghbdtn", 2), ("yt", 1)])
check("stats: the last days end today", again.last_days(3)[-1][1], 3)
again.clear()
again.save()
check("stats: cleared", Stats(path).count("fixed"), 0)
path.write_text("{broken", encoding="utf-8")
check("stats: a broken file starts anew", Stats(path).count("fixed"), 0)

print("FAILS:", fails)
sys.exit(1 if fails else 0)
