"""Pure data-quality helpers for the public scholars catalogue.

The helpers never mutate source records.  Invalid or suspicious values are
returned to the caller as audit reasons so production data can be reviewed
separately.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from institutions import transliterate


SPECIALTY_CODE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}$")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_LEADING_NOISE_RE = re.compile(r"^[\s.,;:!?/\\|_+=*#@~`'\"«»()[\]{}-]+")

_REGION_ALIASES = {
    "fargona": "Farg'ona",
    "farg'ona": "Farg'ona",
    "фаргона": "Farg'ona",
    "фарғона": "Farg'ona",
    "qoraqalpogiston": "Qoraqalpog'iston",
    "qoraqalpog'iston": "Qoraqalpog'iston",
    "қорақалпоғистон": "Qoraqalpog'iston",
    "каракалпакстан": "Qoraqalpog'iston",
}

# Visually identical characters that commonly leak between Latin and Cyrillic.
_LATIN_TO_CYR_HOMOGLYPH = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
    "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
    "A": "А", "C": "С", "E": "Е", "O": "О", "P": "Р", "X": "Х",
    "Y": "У", "K": "К", "M": "М", "T": "Т", "B": "В", "H": "Н",
})
_CYR_TO_LATIN_HOMOGLYPH = str.maketrans({
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x",
    "у": "y", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h",
    "А": "A", "С": "C", "Е": "E", "О": "O", "Р": "P", "Х": "X",
    "У": "Y", "К": "K", "М": "M", "Т": "T", "В": "B", "Н": "H",
})


def normalize_unicode(value):
    """Return a stable NFKC value with invisible characters removed."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _ZERO_WIDTH_RE.sub("", text)
    return " ".join(text.split())


def normalize_region(value):
    text = normalize_unicode(value)
    if not text:
        return ""
    return _REGION_ALIASES.get(text.casefold(), text)


def normalize_scholar_name(value):
    text = normalize_unicode(value)
    text = _LEADING_NOISE_RE.sub("", text)
    return text.strip(" \t\r\n,;:")


def scholar_name_issue(value):
    """Return an audit reason or an empty string for a catalogue-safe name."""
    text = normalize_scholar_name(value)
    if not text:
        return "empty_or_punctuation_only"
    tokens = [token for token in _WORD_RE.findall(text) if len(token) > 1]
    all_tokens = _WORD_RE.findall(text)
    # "Karimov A.A." and "A. B. Karimov" are accepted verified-name formats.
    has_initials_format = (
        len(all_tokens) >= 2
        and any(len(token) >= 3 for token in all_tokens)
        and re.search(r"(?:^|\s)[^\W\d_]\.", text, re.UNICODE) is not None
    )
    if len(tokens) < 2 and not has_initials_format:
        return "too_few_meaningful_tokens"
    if not any(ch.isalpha() for ch in text):
        return "empty_or_punctuation_only"
    return ""


def normalize_specialty_code(value):
    text = normalize_unicode(value)
    return text if SPECIALTY_CODE_RE.fullmatch(text) else ""


def repair_mixed_script_homoglyphs(value):
    """Repair obvious homoglyphs only when one script clearly dominates."""
    text = normalize_unicode(value)
    latin = sum("LATIN" in unicodedata.name(ch, "") for ch in text if ch.isalpha())
    cyrillic = sum("CYRILLIC" in unicodedata.name(ch, "") for ch in text if ch.isalpha())
    if cyrillic >= 3 and latin and cyrillic > latin * 2:
        return text.translate(_LATIN_TO_CYR_HOMOGLYPH)
    if latin >= 3 and cyrillic and latin > cyrillic * 2:
        return text.translate(_CYR_TO_LATIN_HOMOGLYPH)
    return text


def normalize_specialty_name(value):
    text = repair_mixed_script_homoglyphs(value)
    text = _LEADING_NOISE_RE.sub("", text)
    return text.strip()


def _comparison_key(value):
    text = transliterate(normalize_specialty_name(value)).casefold()
    return re.sub(r"[^a-zа-яёўқғҳ0-9]+", " ", text).strip()


def specialty_name_matches(source_name, canonical_name, threshold=0.58):
    """Loose comparison used only to flag likely classifier mismatches."""
    source = _comparison_key(source_name)
    canonical = _comparison_key(canonical_name)
    if not source or not canonical:
        return True
    if source in canonical or canonical in source:
        return True
    return SequenceMatcher(None, source, canonical).ratio() >= threshold


def canonical_specialty(code, source_name, canonical_names):
    """Return ``(code, display_name, audit_reasons)`` without mutating input."""
    reasons = []
    clean_code = normalize_specialty_code(code)
    if not clean_code:
        if normalize_unicode(code):
            reasons.append("invalid_specialty_code")
        return "", "", reasons

    source = normalize_specialty_name(source_name)
    canonical = normalize_specialty_name((canonical_names or {}).get(clean_code, ""))
    if canonical:
        if source and not specialty_name_matches(source, canonical):
            reasons.append("specialty_name_classifier_mismatch")
        return clean_code, canonical, reasons
    return clean_code, source, reasons
