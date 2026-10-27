"""Downloads random Wikipedia article intros as an independent test corpus (tools/raw/wiki_<lang>.txt)."""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
PREFIX = sys.argv[2] if len(sys.argv) > 2 else "wiki"
URL = (
    "https://{lang}.wikipedia.org/w/api.php?action=query&format=json&generator=random&grnnamespace=0"
    "&grnlimit=20&prop=extracts&explaintext=1&exlimit=20&exchars=1200"
)


def fetch(lang: str, rounds: int) -> None:
    out = RAW / f"{PREFIX}_{lang}.txt"
    texts = []
    for _ in range(rounds):
        request = urllib.request.Request(URL.format(lang=lang), headers={"User-Agent": "HumanTypeTest/0.1 (personal test)"})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
                time.sleep(5 * (attempt + 1))
        else:
            continue
        texts += [p.get("extract", "") for p in data.get("query", {}).get("pages", {}).values()]
        time.sleep(2)
    out.write_text("\n".join(texts), encoding="utf-8")
    print(lang, len(" ".join(texts).split()), "words")


if __name__ == "__main__":
    RAW.mkdir(exist_ok=True)
    for lang in ("ru", "en"):
        fetch(lang, int(sys.argv[1]) if len(sys.argv) > 1 else 25)
