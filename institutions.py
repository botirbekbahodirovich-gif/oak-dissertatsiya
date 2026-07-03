"""Institution helpers — Cyrillic ↔ Latin bridge for dissertations.muassasa.

Pure, dependency-free (no Flask, no DB) so it is unit-testable in isolation and
shared by both the migration (`migrate_institutions.py`) and the runtime
directory route in app.py. The transliteration map mirrors app.KIRILL_TO_LATIN.
"""

# Uzbek Cyrillic → Latin (mirror of app.KIRILL_TO_LATIN — keep in sync).
KIRILL_TO_LATIN = {
    'А': 'a', 'Б': 'b', 'В': 'v', 'Г': 'g', 'Д': 'd', 'Е': 'e', 'Ё': 'yo',
    'Ж': 'j', 'З': 'z', 'И': 'i', 'Й': 'y', 'К': 'k', 'Л': 'l', 'М': 'm',
    'Н': 'n', 'О': 'o', 'П': 'p', 'Р': 'r', 'С': 's', 'Т': 't', 'У': 'u',
    'Ф': 'f', 'Х': 'x', 'Ц': 'ts', 'Ч': 'ch', 'Ш': 'sh', 'Щ': 'shch',
    'Ъ': '', 'Ы': 'i', 'Ь': '', 'Э': 'e', 'Ю': 'yu', 'Я': 'ya',
    'Ў': 'o', 'Қ': 'q', 'Ғ': 'g', 'Ҳ': 'h',
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h',
}

# category key → Uzbek plural label used by the directory tabs.
INSTITUTION_CATEGORIES = {
    'universitet': 'Universitetlar',
    'akademiya': 'Akademiyalar',
    'institut': 'Institutlar',
    'markaz': 'Markazlar',
}

# Category keyword sets (Cyrillic + Latin), checked in priority order.
_CATEGORY_KEYWORDS = [
    ('universitet', ('университет', 'universitet', 'university')),
    ('akademiya',   ('академия', 'akademiya', 'academy')),
    ('institut',    ('институт', 'institut', 'institute')),
    ('markaz',      ('марказ', 'markaz', 'center', 'centre', 'центр')),
]

_APOSTROPHES = ("'", "'", "`", "?", "ʼ", "‘", "’", "´")


def transliterate(text):
    """Uzbek Cyrillic → Latin (unknown chars pass through). Lowercases, matching
    app.transliterate — use transliterate_display() for human-facing labels."""
    return "".join(KIRILL_TO_LATIN.get(ch, ch) for ch in (text or ""))


def transliterate_display(text):
    """Case-preserving Cyrillic → Latin for display labels: an uppercase
    Cyrillic letter yields a capitalized Latin (Тошкент → Toshkent)."""
    out = []
    for ch in (text or ""):
        lat = KIRILL_TO_LATIN.get(ch)
        if lat is None:
            out.append(ch)
        elif ch.isupper():
            out.append(lat[:1].upper() + lat[1:])
        else:
            out.append(lat)
    return "".join(out)


def norm_key(name):
    """Case/whitespace/apostrophe-insensitive key used to group name variants
    that are 'the same' institution (mirrors app._seed_norm)."""
    s = (name or '').lower()
    for ch in _APOSTROPHES:
        s = s.replace(ch, '')
    return ' '.join(s.split())


def detect_category(name):
    """Classify an institution name into universitet / akademiya / institut /
    markaz from keywords. Falls back to 'universitet' (the table default)."""
    n = (name or '').lower()
    for cat, keys in _CATEGORY_KEYWORDS:
        if any(k in n for k in keys):
            return cat
    return 'universitet'


def build_canonical(variant_counts):
    """Given ``{raw_variant: dissertation_count}`` return ``{raw_variant:
    canonical_variant}``. Variants sharing a normalized key are one group; the
    canonical is the most common variant (ties → longer, then lexicographic —
    fully deterministic)."""
    groups = {}
    for name, count in variant_counts.items():
        groups.setdefault(norm_key(name), []).append((name, count or 0))
    mapping = {}
    for members in groups.values():
        canonical = sorted(members, key=lambda m: (-m[1], -len(m[0]), m[0]))[0][0]
        for name, _ in members:
            mapping[name] = canonical
    return mapping
