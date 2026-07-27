"""Deterministic, idempotent normalizers for scientific-record fields.

Design contract (enforced by test_data_quality_normalizers.py):
  * Pure functions — no I/O, no DB, no globals mutated.
  * Deterministic — same input always yields the same output.
  * Idempotent — ``f(f(x)) == f(x)`` for every normalizer.
  * Loss-free at the call site — normalizers return the *normalized* string only;
    the ORIGINAL is always kept by the caller (scanner/fixer) for auditing.
  * Script-safe — must not corrupt Uzbek-Latin, Uzbek-Cyrillic, Russian or
    English text, and must preserve acronyms (TATU, OAK, PhD, DSc).
  * Never build SQL. Never emit control characters.

The apostrophe handling matches the rest of the codebase: the Uzbek-Latin
tutuq belgisi (in oʻ / gʻ) is unified to U+02BB (ʻ), consistent with
``utils/transliterate.py``.

``to_latin`` is deliberately opt-in.  The field normalizers below continue to
preserve the stored script; duplicate detection uses the transliterated form
only in its in-memory comparison signature.
"""

from __future__ import annotations

import html as _html
import re
import unicodedata
from typing import Optional


# ── Character classes ──────────────────────────────────────────────────────
# All apostrophe-like variants the source data mixes: straight, curly, turned
# comma, modifier, grave, acute.  (U+0027 ' , U+2018 ‘ , U+2019 ’ , U+02BB ʻ ,
# U+02BC ʼ , U+0060 ` , U+00B4 ´)
_APOS_CLASS = "'‘’ʻʼ`´"
_APOS_ANY = re.compile("[" + _APOS_CLASS + "]")
# The canonical Uzbek-Latin tutuq belgisi.
_UZ_APOS = "ʻ"

# o/g (either case) directly followed by any apostrophe variant -> letter + ʻ
_UZ_APOS_PAIR = re.compile("([oOgG])[" + _APOS_CLASS + "]")

_WS_RUN = re.compile(r"\s+")
# C0 and C1 control characters except the ordinary whitespace we keep.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")

# Uzbek Cyrillic -> Latin. ``е`` is handled contextually in ``to_latin``:
# Uzbek Latin writes it as ``ye`` at the start of a word and after a vowel,
# otherwise as ``e``. Uppercase digraphs use title case because callers that
# compare text case-fold it afterwards.
_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "ʼ", "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
    "ў": "oʻ", "қ": "q", "ғ": "gʻ", "ҳ": "h",
}
_CYRILLIC_VOWELS = frozenset("аеёиоуўыэюяАЕЁИОУЎЫЭЮЯ")
_CYRILLIC_SEPARATORS = frozenset("ъьЪЬ")

_URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def _s(value) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


# ── Primitive normalizers ──────────────────────────────────────────────────
def normalize_unicode(value: Optional[str]) -> str:
    """NFC-normalise so canonically-equivalent sequences compare equal."""
    v = _s(value)
    if not v:
        return v
    return unicodedata.normalize("NFC", v)


def normalize_whitespace(value: Optional[str]) -> str:
    """Convert every whitespace run (incl. NBSP, tabs, newlines) to one space
    and strip the ends."""
    v = _s(value)
    if not v:
        return v
    # NBSP and other Unicode spaces -> plain space before collapsing.
    v = v.replace(" ", " ").replace("​", "")
    v = _WS_RUN.sub(" ", v)
    return v.strip()


def normalize_apostrophes(value: Optional[str]) -> str:
    """Unify apostrophe variants. o/g + apostrophe -> letter + ʻ (Uzbek tutuq
    belgisi); any remaining apostrophe variant -> straight ASCII ' ."""
    v = _s(value)
    if not v:
        return v
    v = _UZ_APOS_PAIR.sub(lambda m: m.group(1) + _UZ_APOS, v)
    # Remaining apostrophe variants that are NOT the canonical uz one -> ASCII '
    v = re.sub("[‘’ʼ`´]", "'", v)
    return v


def to_latin(value: Optional[str]) -> str:
    """Transliterate Uzbek Cyrillic text to Latin for comparison/search.

    Latin text and non-Cyrillic characters pass through unchanged, making the
    function idempotent. The conversion is intentionally not part of any
    storage normalizer: callers opt in when they need a script-independent
    comparison key.

    Ambiguous Cyrillic letters follow Uzbek spelling: ``ж`` -> ``j``,
    ``ё``/``ю``/``я`` -> ``yo``/``yu``/``ya``; ``е`` -> ``ye`` at a word
    boundary or after a vowel/ъ/ь, otherwise ``e``.
    """
    v = normalize_unicode(value)
    if not v:
        return v

    out = []
    for index, char in enumerate(v):
        lower = char.lower()
        if lower == "е":
            previous = v[index - 1] if index else ""
            needs_y = (
                not previous
                or not previous.isalpha()
                or previous in _CYRILLIC_VOWELS
                or previous in _CYRILLIC_SEPARATORS
            )
            replacement = "ye" if needs_y else "e"
        else:
            replacement = _CYRILLIC_TO_LATIN.get(lower)

        if replacement is None:
            out.append(char)
        elif char.isupper() and replacement:
            out.append(replacement[0].upper() + replacement[1:])
        else:
            out.append(replacement)
    return "".join(out)


def strip_html(value: Optional[str]) -> str:
    """Remove HTML tags and decode entities, safely (no markup passes through)."""
    v = _s(value)
    if not v:
        return v
    v = _HTML_TAG.sub(" ", v)
    v = _html.unescape(v)
    # A second tag pass in case an entity decoded into a stray angle bracket.
    v = _HTML_TAG.sub(" ", v)
    return v


def strip_control_chars(value: Optional[str]) -> str:
    v = _s(value)
    if not v:
        return v
    return _CONTROL.sub("", v)


# ── Field normalizers ──────────────────────────────────────────────────────
def _base_clean(value: Optional[str]) -> str:
    """Shared safe pipeline: unicode -> strip control/HTML -> apostrophe ->
    whitespace. Order matters and keeps the result idempotent."""
    v = normalize_unicode(value)
    v = strip_control_chars(v)
    v = strip_html(v)
    v = normalize_apostrophes(v)
    v = normalize_whitespace(v)
    return v


def normalize_title(value: Optional[str]) -> str:
    """Dissertation title / mavzu — clean but never change wording or case."""
    return _base_clean(value)


def normalize_person_name(value: Optional[str]) -> str:
    """Person name — CONSERVATIVE: clean whitespace/apostrophe/unicode only,
    and tidy initials spacing ("A.  B .Familiya" -> "A.B. Familiya"). Never
    force letter case (real names/acronyms must survive untouched)."""
    v = _base_clean(value)
    if not v:
        return v
    # Tidy spacing around dotted initials without altering the letters.
    v = re.sub(r"\s*\.\s*", ".", v)          # "A . B" -> "A.B"
    v = re.sub(r"\.(?=[^\s.])", ". ", v)     # "A.B.Familiya" -> "A. B. Familiya"
    v = normalize_whitespace(v)
    return v


# Acronyms that must never be case-folded by organisation normalisation.
_ORG_ACRONYMS = ("TATU", "OAK", "PhD", "DSc", "AKT", "AAJ", "MChJ")


def normalize_organization_name(value: Optional[str]) -> str:
    """Organisation / muassasa — clean and drop a trailing separator; preserve
    acronyms and existing casing (Cyrillic/Latin variants are NOT merged here —
    that is a consistency finding, not an automatic rewrite)."""
    v = _base_clean(value)
    if not v:
        return v
    v = v.rstrip(" ,;")
    return v


# Degree canonical forms.
DEGREE_PHD = "PhD"
DEGREE_DSC = "DSc"

_DEGREE_PHD_PAT = re.compile(
    r"(ph\.?\s*d)|(falsafa\s+doktor)|(фалсафа\s+доктор)", re.IGNORECASE)
_DEGREE_DSC_PAT = re.compile(
    r"(d\.?\s*sc)|(fan(lari)?\s+doktor)|(фан(лари)?\s+доктор)", re.IGNORECASE)


def normalize_degree(value: Optional[str]) -> Optional[str]:
    """Map the many degree spellings to canonical 'PhD' / 'DSc'. Returns the
    canonical string, or the cleaned original if it is unrecognised (so the
    validator can flag INVALID_DEGREE rather than the fixer guessing)."""
    v = normalize_whitespace(normalize_unicode(value))
    if not v:
        return v
    # DSc is checked first: "fan doktori" must not be shadowed by a loose PhD rule.
    if _DEGREE_DSC_PAT.search(v):
        return DEGREE_DSC
    if _DEGREE_PHD_PAT.search(v):
        return DEGREE_PHD
    return v


_SPECIALTY_RE = re.compile(r"\d+")


def normalize_specialty_code(value: Optional[str]) -> str:
    """Specialty code -> dot-joined digit groups, e.g. '01 . 01-01' -> '01.01.01'.
    Non-code text is returned cleaned but otherwise unchanged."""
    v = normalize_whitespace(normalize_unicode(value))
    if not v:
        return v
    groups = _SPECIALTY_RE.findall(v)
    # Treat it as a code only when it is *made of* digit groups + separators.
    stripped = re.sub(r"[\s.\-–—]", "", v)
    if groups and stripped.isdigit() and len(groups) >= 2:
        return ".".join(groups)
    return v


def normalize_url(value: Optional[str]) -> str:
    """Trim, normalise unicode, and add https:// when a bare host is given.
    Leaves an already-schemed URL untouched apart from trimming."""
    v = normalize_whitespace(normalize_unicode(value))
    if not v:
        return v
    if _URL_SCHEME.match(v):
        return v
    # Don't invent a scheme for obvious non-URLs.
    if "." not in v or " " in v:
        return v
    return "https://" + v


# Date formats we accept confidently. Ambiguity (e.g. bare 2-digit year) is
# left unparsed on purpose — a wrong date is worse than a missing one.
_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), ("d", "m", "y")),
)


def normalize_date(value: Optional[str]) -> Optional[str]:
    """Free-text ``sana`` -> ISO 'YYYY-MM-DD' when confidently parseable, else
    None. Deliberately refuses ambiguous input (no locale guessing)."""
    v = normalize_whitespace(normalize_unicode(value))
    if not v:
        return None
    for pat, order in _DATE_PATTERNS:
        m = pat.match(v)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        try:
            y, mo, d = int(parts["y"]), int(parts["m"]), int(parts["d"])
        except (KeyError, ValueError):
            return None
        if not (1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
            return None
        # Reject impossible day/month combos cheaply.
        import calendar
        if d > calendar.monthrange(y, mo)[1]:
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None
