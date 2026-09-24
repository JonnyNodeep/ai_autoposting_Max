"""Safe audio filenames from practice/topic titles."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_MAX_FILENAME_LEN = 120

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _translit_char(ch: str) -> str:
    lower = ch.lower()
    if lower in _TRANSLIT:
        out = _TRANSLIT[lower]
        return out.upper() if ch.isupper() else out
    return ch


def safe_filename_from_topic(topic: str, *, suffix: str = ".mp3") -> str:
    text = (topic or "").strip()
    if not text:
        return f"audio{suffix}"
    chars: list[str] = []
    for ch in text:
        if unicodedata.category(ch).startswith("So"):
            continue
        if ord(ch) < 128 and (ch.isalnum() or ch in (" ", "-", "_")):
            chars.append(ch)
        elif "\u0400" <= ch <= "\u04FF" or ch in _TRANSLIT:
            chars.append(_translit_char(ch))
        elif ch in (" ", "-", "_"):
            chars.append(" ")
    cleaned = "".join(chars)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"[\s_]+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "audio"
    if len(cleaned) > _MAX_FILENAME_LEN:
        cleaned = cleaned[:_MAX_FILENAME_LEN].rstrip()
    return f"{cleaned}{suffix}"


def rename_audio_path(src: str, topic: str) -> str:
    src_path = Path(src)
    if not src_path.is_file():
        return src
    dest = src_path.parent / safe_filename_from_topic(topic)
    if dest.resolve() == src_path.resolve():
        return str(src_path)
    if dest.exists():
        dest.unlink()
    src_path.rename(dest)
    return str(dest)
