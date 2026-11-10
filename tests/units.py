"""Runs anywhere: the parts with no window and no keyboard in them: the detector's judgement of a
word still being typed, transliteration and letter case, the word-list file, the statistics."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from humantype import textops, wordlists  # noqa: E402
from humantype.detector import Detector, keys_of  # noqa: E402
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
