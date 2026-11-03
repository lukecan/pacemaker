"""Running-pun playlist names, identical to the website's (site/lib/names.js).

The word lists live in site/lib/names.json so both sides stay in step.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

NAMES_FILE = Path(__file__).resolve().parents[2] / "site" / "lib" / "names.json"


def _fnv1a(text: str) -> int:
    h = 0x811C9DC5
    for ch in text:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _fmix32(h: int) -> int:
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def _cadence(value: float) -> str:
    return f"{value:g}"


def playlist_seed(keys, cadence: float) -> str:
    return "\n".join(sorted(keys)) + f"\n{_cadence(cadence)}"


@lru_cache(maxsize=1)
def _lists() -> dict | None:
    try:
        return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except OSError:
        return None


def playlist_name(seed: str, lang: str = "en") -> str | None:
    names = _lists()
    if not names:
        return None
    lists = names.get(lang) or names["en"]
    x = _fmix32(_fnv1a(seed))
    pun = lists["puns"][x % len(lists["puns"])]
    x //= len(lists["puns"])
    edition = lists["editions"][x % len(lists["editions"])]
    x //= len(lists["editions"])
    number = 1 + x % 99
    return lists["format"].replace("{pun}", pun).replace("{edition}", edition).replace("{n}", str(number))
