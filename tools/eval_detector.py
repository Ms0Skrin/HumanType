"""Measures the layout detector on real text it was not built from.

Every word of the test texts is "typed" twice: in the right layout (the detector must leave it)
and in the wrong one (it must switch it back to exactly the original). Prints accuracy per
sensitivity, per word length, and samples of the mistakes.

    python tools/eval_detector.py [sensitivity] [--errors N]
"""

import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humantype.detector import SENSITIVITY, Detector, Session, keys_of  # noqa: E402
from humantype.layout import EN_TO_RU  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "tools" / "raw"
TOKEN = re.compile(r"\S+")
RU = re.compile(r"[а-яёА-ЯЁ]")
EN = re.compile(r"[a-zA-Z]")
TYPEABLE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789`~!@#$%^&*()-_=+[]{};:'\",.<>/?\\| ")


def tokens(path: Path) -> list[tuple[str, str]]:
    """(token, its language) for every token one could type on a ЙЦУКЕН/QWERTY keyboard."""
    out = []
    for token in TOKEN.findall(path.read_text(encoding="utf-8", errors="ignore")):
        has_ru, has_en = bool(RU.search(token)), bool(EN.search(token))
        if has_ru == has_en:
            continue
        lang = "ru" if has_ru else "en"
        keys = keys_of(token, lang)
        if all(c in TYPEABLE for c in keys):
            out.append((token, lang))
    return out


def letters(token: str) -> int:
    return sum(c.isalpha() for c in token)


def evaluate(detector: Detector, data: list[tuple[str, str]], show: int) -> dict:
    stats = Counter()
    by_len = defaultdict(Counter)
    misses, false = [], []
    for token, lang in data:
        keys = keys_of(token, lang)
        wrong = "en" if lang == "ru" else "ru"
        bucket = min(letters(token), 7)

        right = detector.decide(keys, lang)  # typed correctly: must stay
        stats["right"] += 1
        by_len[bucket]["right"] += 1
        if right.switch:
            stats["false"] += 1
            by_len[bucket]["false"] += 1
            false.append((token, lang, right.text, right.reason))

        typo = detector.decide(keys, wrong)  # typed in the wrong layout: must come back
        stats["wrong"] += 1
        by_len[bucket]["wrong"] += 1
        if typo.switch and typo.text == token:
            stats["fixed"] += 1
            by_len[bucket]["fixed"] += 1
        else:
            misses.append((token, lang, typo.reason))

    stats_out = {
        "recall": stats["fixed"] / stats["wrong"],
        "false_rate": stats["false"] / stats["right"],
        "words": stats["right"],
        "by_len": by_len,
        "misses": misses,
        "false": false,
    }
    return stats_out


def main() -> None:
    args = sys.argv[1:]
    show = 25
    if "--errors" in args:
        show = int(args[args.index("--errors") + 1])
    levels = [a for a in args if a in SENSITIVITY] or list(SENSITIVITY)

    started = time.perf_counter()
    detector = Detector(ROOT / "assets" / "dict")
    print(f"loaded in {time.perf_counter() - started:.2f}s")

    prefix = "holdout" if "--holdout" in args else "wiki"
    corpora = {name: tokens(RAW / f"{prefix}_{name}.txt") for name in ("ru", "en") if (RAW / f"{prefix}_{name}.txt").exists()}
    extra = ROOT / "tests" / "chat_corpus.txt"
    if extra.exists() and prefix == "wiki":
        corpora["chat"] = tokens(extra)
    data = [t for c in corpora.values() for t in c]
    print("tokens:", {k: len(v) for k, v in corpora.items()})
    phrases = [p for name in ("ru", "en") if (RAW / f"{prefix}_{name}.txt").exists() for p in phrases_of(RAW / f"{prefix}_{name}.txt")]
    if extra.exists() and prefix == "wiki":
        phrases += phrases_of(extra)

    for level in levels:
        detector.sensitivity = SENSITIVITY[level]
        started = time.perf_counter()
        r = evaluate(detector, data, show)
        per_word = (time.perf_counter() - started) / (2 * r["words"]) * 1e6
        print(f"\n=== {level}: fixed {r['recall']:.2%} of wrong-layout words, "
              f"false switches {r['false_rate']:.3%} of correct words ({len(r['false'])}), {per_word:.0f} µs/word")
        print("  letters  fixed   false")
        for n in sorted(r["by_len"]):
            b = r["by_len"][n]
            print(f"  {n if n < 7 else '7+':>7}  {b['fixed'] / max(1, b['wrong']):6.1%}  {b['false'] / max(1, b['right']):6.2%}")
        if show:
            print("  false switches:", ", ".join(f"{t}→{x} ({why})" for t, _, x, why in r["false"][:show]))
            sample = [m for m in r["misses"] if letters(m[0]) >= 3][:show]
            print("  missed (3+ letters):", ", ".join(f"{t} ({why})" for t, _, why in sample))

        hard = ROOT / "tests" / "hard_corpus.txt"
        if hard.exists():
            spoiled = evaluate_hard(detector, hard)
            print(f"  HARD (code, links, slang, brands — typed correctly): {len(spoiled)} spoiled",
                  "; ".join(spoiled[:12]))

        s = evaluate_phrases(detector, phrases)
        print(f"  PHRASES ({s['phrases']}): typed in the wrong layout -> fully restored {s['restored']:.1%}, "
              f"words right {s['words_ok']:.2%}; typed correctly -> left untouched {s['untouched']:.2%}")
        for bad in s["bad"][:min(show, 8)]:
            print("    ✗", bad)


def evaluate_hard(detector: Detector, path: Path) -> list[str]:
    """Every token typed in its own layout, a session per line: nothing may be switched."""
    spoiled = []
    for line in path.read_text(encoding="utf-8").splitlines():
        session = Session(detector)
        for token in line.split():
            lang = "ru" if RU.search(token) else "en"
            keys = keys_of(token, lang)
            if not all(c in TYPEABLE for c in keys):
                session.reset()
                continue
            fix = session.word(keys, lang)
            if fix:
                spoiled.append(f"{token}→{fix.text}")
    return spoiled


def phrases_of(path: Path) -> list[tuple[str, str]]:
    """Phrases of 3+ words, all in one script and typeable, from a text file."""
    out = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for chunk in re.split(r"[\n.!?;]+", text):
        words = chunk.split()
        if len(words) < 3:
            continue
        langs = {("ru" if RU.search(w) else "en" if EN.search(w) else None) for w in words}
        if len(langs) != 1 or None in langs:
            continue
        lang = langs.pop()
        if all(all(c in TYPEABLE for c in keys_of(w, lang)) for w in words):
            out.append((" ".join(words), lang))
    return out


def type_phrase(detector: Detector, phrase: str, lang: str, layout: str) -> str:
    """Simulates typing the phrase word by word, applying the session's fixes like the app does."""
    session = Session(detector)
    buffer = ""
    for word in phrase.split():
        keys = keys_of(word, lang)
        buffer += keys if layout == "en" else keys.translate(EN_TO_RU)
        fix = session.word(keys, layout)
        if fix:
            buffer = buffer[: len(buffer) - fix.erase] + fix.text
            layout = fix.layout
        buffer += " "
    return buffer.rstrip()


def evaluate_phrases(detector: Detector, phrases: list[tuple[str, str]]) -> dict:
    restored = untouched = words_ok = words = 0
    bad = []
    for phrase, lang in phrases:
        wrong = "en" if lang == "ru" else "ru"
        got = type_phrase(detector, phrase, lang, wrong)
        restored += got == phrase
        pairs = list(zip(got.split(), phrase.split()))
        words_ok += sum(a == b for a, b in pairs)
        words += len(phrase.split())
        if got != phrase:
            bad.append(f"{got}   ≠   {phrase}")
        kept = type_phrase(detector, phrase, lang, lang)
        untouched += kept == phrase
        if kept != phrase:
            bad.append(f"[spoiled] {kept}   ≠   {phrase}")
    n = max(1, len(phrases))
    return {"phrases": len(phrases), "restored": restored / n, "untouched": untouched / n, "words_ok": words_ok / max(1, words), "bad": bad}


if __name__ == "__main__":
    main()
