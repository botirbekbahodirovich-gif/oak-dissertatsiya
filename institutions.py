"""Institution helpers — Cyrillic ↔ Latin bridge for dissertations.muassasa.

Pure, dependency-free (no Flask, no DB) so it is unit-testable in isolation and
shared by both the migration (`migrate_institutions.py`) and the runtime
directory route in app.py. The transliteration map mirrors app.KIRILL_TO_LATIN.
"""
import re
from difflib import SequenceMatcher

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


# Latin letters that appear inside otherwise-Cyrillic words (mixed-script typos
# like "унIVерситети") → their Cyrillic equivalents. Applied only when the
# string is majority-Cyrillic, so genuine Latin names are never touched.
_LATIN_TO_CYR = {
    'a': 'а', 'b': 'б', 'c': 'с', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г',
    'h': 'ҳ', 'i': 'и', 'j': 'ж', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н',
    'o': 'о', 'p': 'п', 'q': 'қ', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у',
    'v': 'в', 'w': 'в', 'x': 'х', 'y': 'й', 'z': 'з',
}

_CYR_LETTERS = set('абвгдежзийклмнопрстуфхцчшщъыьэюяёўқғҳ')

# A part must contain one of these to count as a standalone institution when
# splitting multi-institution entries.
_INST_KEYWORDS = ('университет', 'институт', 'академия', 'марказ',
                  'universitet', 'institut', 'akademiya', 'markaz',
                  'university', 'institute', 'academy', 'center', 'centre')


def _has_inst_keyword(s):
    low = (s or '').lower()
    return any(k in low for k in _INST_KEYWORDS)


def _cyr_ratio(s):
    letters = [c for c in (s or '') if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if c.lower() in _CYR_LETTERS)
    return cyr / len(letters)


def clean_name(name):
    """Reduce a raw muassasa value to a single institution name:
    - drop parenthesised remarks ("(олдинги ...)"),
    - multi-institution entries ("A, B" / "A ва B") keep the FIRST institution
      (split only when the parts really look like separate institutions),
    - collapse whitespace."""
    s = (name or '').strip()
    s = re.sub(r'\([^)]*\)', ' ', s)
    # comma-joined: keep the first part when it is a complete institution name
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) > 1 and _has_inst_keyword(parts[0]):
        s = parts[0]
    # " ва "-joined: split only when BOTH sides carry an institution keyword
    # (never split names like "қишлоқ хўжалиги ва агротехнологиялар институти")
    m = re.split(r'\s+(?:ва|va)\s+', s, maxsplit=1)
    if len(m) == 2 and _has_inst_keyword(m[0]) and _has_inst_keyword(m[1]):
        s = m[0]
    return ' '.join(s.split())


def norm_key(name):
    """Aggressive normalization key for grouping variants of the same
    institution: cleans multi-institution/parenthesis noise, fixes
    mixed-script Latin letters, unifies ё/е and й/и, strips apostrophes,
    punctuation, the word 'бажарилган' and trailing locative/genitive
    suffixes (университетиДА, ...НИНГ)."""
    s = clean_name(name).lower()
    # mixed-script repair — only inside majority-Cyrillic strings
    if _cyr_ratio(s) > 0.5:
        s = ''.join(_LATIN_TO_CYR.get(ch, ch) for ch in s)
    s = s.replace('ё', 'е').replace('й', 'и')
    for ch in _APOSTROPHES:
        s = s.replace(ch, '')
    s = re.sub(r'[«»"“”.\-–—]', ' ', s)
    words = [w for w in s.split() if w not in ('бажарилган', 'bajarilgan')]
    s = ' '.join(words)
    # trailing suffixes on the whole name: "...университетида" → "...университети"
    s = re.sub(r'нинг$', '', s)
    s = re.sub(r'да$', '', s)
    return ' '.join(s.split())


def detect_category(name):
    """Classify an institution name into universitet / akademiya / institut /
    markaz from keywords. Falls back to 'universitet' (the table default)."""
    n = (name or '').lower()
    for cat, keys in _CATEGORY_KEYWORDS:
        if any(k in n for k in keys):
            return cat
    return 'universitet'


def _similar_norms(a, b, token_threshold=0.8):
    """Fuzzy match for two normalized keys, token by token. Same word count and
    every differing word pair must be ≥ token_threshold similar. Token-wise
    (not whole-string) so 'техника университети' never merges with
    'транспорт университети' while 'уневерситети' still merges with
    'университети'."""
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if x == y:
            continue
        if SequenceMatcher(None, x, y).ratio() < token_threshold:
            return False
    return True


def build_canonical(variant_counts, fuzzy=True):
    """Given ``{raw_variant: dissertation_count}`` return ``{raw_variant:
    canonical_name}``.

    1. Variants sharing a norm_key form exact groups.
    2. With ``fuzzy``, norm-groups whose keys are token-wise similar (typos:
       уневерситети/унивеситети/университети) are merged via union-find.
    3. Canonical = the cleaned (single-institution, no parenthesis) form of the
       most frequent variant; suffix-carrying forms ('...да', '...нинг') are
       avoided unless the group has nothing else. Deterministic."""
    norm_groups = {}                      # norm key -> {raw: count}
    for name, count in variant_counts.items():
        norm_groups.setdefault(norm_key(name), {})[name] = count or 0

    keys = sorted(norm_groups,
                  key=lambda k: (-sum(norm_groups[k].values()), k))
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    if fuzzy:
        # cheap 4-char prefix gate keeps the pairwise pass fast; typo variants
        # of the same institution virtually always share the leading city word
        by_prefix = {}
        for k in keys:
            by_prefix.setdefault(k[:4], []).append(k)
        for bucket in by_prefix.values():
            for i, a in enumerate(bucket):
                for b in bucket[i + 1:]:
                    if find(a) == find(b):
                        continue
                    if _similar_norms(a, b):
                        parent[find(b)] = find(a)

    merged = {}                           # root -> {raw: count}
    for k in keys:
        merged.setdefault(find(k), {}).update(norm_groups[k])

    mapping = {}
    for members in merged.values():
        # candidate canonical forms: cleaned raw variants weighted by count
        cleaned = {}
        for raw, count in members.items():
            c = clean_name(raw)
            cleaned[c] = cleaned.get(c, 0) + count
        def _suffixed(c):
            low = c.lower()
            return low.endswith('да') or low.endswith('нинг')
        candidates = sorted(
            cleaned.items(),
            key=lambda it: (_suffixed(it[0]), -it[1], len(it[0]), it[0]))
        canonical = candidates[0][0]
        for raw in members:
            mapping[raw] = canonical
    return mapping
