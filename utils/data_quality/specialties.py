"""Ixtisoslik (specialty) code/name parsing and validation.

data.py's live _extract_ixtisoslik() keeps only the FIRST NN.NN.NN code found
in a dissertation's combined "topic + code(s)" text, even though scraper.py
already extracts the full list — all_specialty_codes() restores that. These
are pure functions; nothing here writes to the database.
"""
import re

CODE_PATTERN = re.compile(r"\b(\d{2}\.\d{2}\.\d{2})\b")
_CODE_FORMAT_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}$")
_LEADING_SEPARATORS_RE = re.compile(r"^[\s\-–—:.)]+")
_TRAILING_SEPARATORS_RE = re.compile(r"[\s\-–—:.(]+$")


def validate_code_format(code):
    """True iff code is exactly NN.NN.NN (no surrounding text)."""
    return bool(code) and bool(_CODE_FORMAT_RE.match(code.strip()))


def extract_specialty_code(text):
    """First NN.NN.NN code found in free text, or None. Mirrors the current
    (lossy) single-code behavior in data.py, kept for callers that only need
    one code."""
    if not text:
        return None
    m = CODE_PATTERN.search(text)
    return m.group(1) if m else None


def all_specialty_codes(text):
    """All distinct NN.NN.NN codes found in free text, in first-seen order.
    Use this instead of extract_specialty_code() wherever a dissertation may
    carry more than one specialty (interdisciplinary defenses)."""
    if not text:
        return []
    seen = []
    for m in CODE_PATTERN.finditer(text):
        code = m.group(1)
        if code not in seen:
            seen.append(code)
    return seen


def split_specialty_text(raw):
    """Split a combined "topic + code" or "code - topic" string into
    (topic_text, code). Handles both orderings seen in OAK data:
    - "... dissertation topic ... 05.01.01"   (code trailing — the common case)
    - "05.01.01 - Dissertation topic ..."     (code leading)

    Returns (topic_text, code) with code=None and topic_text=raw.strip() if
    no code is found."""
    if not raw:
        return (raw or "").strip(), None
    matches = list(CODE_PATTERN.finditer(raw))
    if not matches:
        return raw.strip(), None
    first = matches[0]
    # Code leading: the first code appears in the opening few characters,
    # before any real topic text has had a chance to start.
    if first.start() <= 12:
        code = first.group(1)
        rest = raw[first.end():]
        rest = _LEADING_SEPARATORS_RE.sub("", rest)
        return rest.strip(), code
    # Code trailing (default): take the last code as the specialty, topic is
    # everything before its first occurrence.
    last = matches[-1]
    code = last.group(1)
    topic = raw[:first.start()]
    topic = _TRAILING_SEPARATORS_RE.sub("", topic)
    return topic.strip(), code


def branch_of(code):
    """Top-level 2-digit class of a specialty code, e.g. '05.01.01' -> '05.00.00'."""
    if not validate_code_format(code):
        return None
    major = code.split(".")[0]
    return f"{major}.00.00"
