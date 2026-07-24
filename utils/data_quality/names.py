"""Person-name normalization and broken-name detection for scholars (olim)
and advisors (ilmiy_rahbar). Pure functions — order-tolerant, script-tolerant
matching key for candidate duplicate detection, plus a broken-name detector
generalizing data.py's single `'нинг' in olim` sanity check.

Not a replacement for olim_profiles.olim_name (the live unique key) — this is
a *candidate-generation* key: two different real people can share it (common
names), which is fine, because nothing here auto-merges (see confidence.py).
"""
import re

_APOSTROPHES = ("'", "‘", "’", "`", "ʼ", "´")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_SPECIALTY_CODE_RE = re.compile(r"\d{2}\.\d{2}\.\d{2}")

# Known ingest placeholders that leak into the name field when the scraper's
# name-extraction regex fails to find a real name (data.py _extract_olim's
# last-resort fallback can produce these verbatim).
_PLACEHOLDER_PATTERNS = (
    re.compile(r"ф\.?\s*и\.?\s*ш", re.IGNORECASE),   # Ф.И.Ш / ФИШ
    re.compile(r"f\.?\s*i\.?\s*sh", re.IGNORECASE),  # F.I.Sh
    re.compile(r"ининг\s*$", re.IGNORECASE),
)


def _strip_apostrophes(s):
    for ch in _APOSTROPHES:
        s = s.replace(ch, "")
    return s


def normalize_person_name(name):
    """Order-tolerant, script-lightly-normalized candidate key.

    Lowercases, unifies ё->е and й->и, strips apostrophes/periods/commas,
    collapses whitespace, then sorts tokens alphabetically so "Familiya Ism
    Otasining" and "Ism Otasining Familiya" produce the same key (addresses
    "ism-familya tartibi" — name-order duplicates)."""
    s = (name or "").strip().lower()
    s = s.replace("ё", "е").replace("й", "и")  # ё->е, й->и
    s = _strip_apostrophes(s)
    s = re.sub(r"[.,;:«»\"“”\-–—()]", " ", s)
    tokens = [t for t in s.split() if t]
    return " ".join(sorted(tokens))


def initials_key(name):
    """Like normalize_person_name, but every token longer than one character
    is collapsed to its first letter — lets a full name be compared against
    an F.I.Sh.-style initials form via initials_match()."""
    s = (name or "").strip().lower()
    s = s.replace("ё", "е").replace("й", "и")
    s = _strip_apostrophes(s)
    s = re.sub(r"[.,;:«»\"“”\-–—()]", " ", s)
    tokens = [t[0] for t in s.split() if t]
    return "".join(sorted(tokens))


def initials_match(full_name, short_name):
    """True if short_name plausibly abbreviates full_name (same multiset of
    first letters, same token count) — e.g. "Каримов Азиз Ботирович" vs
    "Каримов А.Б." Loose on purpose: feeds candidate detection, never an
    auto-merge decision."""
    full_tokens = [t for t in re.sub(r"[.,]", " ", (full_name or "").lower()).split() if t]
    short_tokens = [t for t in re.sub(r"[.,]", " ", (short_name or "").lower()).split() if t]
    if not full_tokens or not short_tokens or len(full_tokens) != len(short_tokens):
        return False
    for f, s in zip(full_tokens, short_tokens):
        s_clean = s.rstrip(".")
        if len(s_clean) == 1:
            if f[0] != s_clean:
                return False
        elif f != s_clean:
            return False
    return True


def is_broken_name(name):
    """Detect garbled/placeholder scholar names before they enter rankings,
    catalogs, or clustering. Returns (is_broken: bool, reason: str | None).

    reasons: 'empty' | 'bracket_residue' | 'html_residue' | 'placeholder' |
             'no_letters' | 'too_short'
    """
    if name is None:
        return True, "empty"
    s = name.strip()
    if not s:
        return True, "empty"
    if "[" in s or "]" in s:
        return True, "bracket_residue"
    if _HTML_TAG_RE.search(s) or _HTML_ENTITY_RE.search(s):
        return True, "html_residue"
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(s):
            return True, "placeholder"
    if not _LETTER_RE.search(s):
        return True, "no_letters"
    letters_only = _LETTER_RE.findall(s)
    if len(letters_only) < 3:
        return True, "too_short"
    return False, None
