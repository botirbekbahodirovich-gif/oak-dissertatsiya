"""Gender inference with confidence scoring — the "taxminiy" (estimated)
disclaimer app.py's detect_gender() never surfaces.

app.py:detect_gender() returns a bare 'male'/'female'/'unknown' string with
no confidence and no per-record disclaimer (the aggregate /stats page does
show an unknown bucket; individual profile/cabinet templates don't). This
module keeps the same rule tiers (patronymic > surname suffix > hardcoded
first-name list) but scores each tier, so callers can render a "taxminiy"
label and gate low-confidence guesses instead of asserting them as fact.
"""

_PATRONYMIC_FEMALE = ("овна", "евна", "ёвна", "қизи", "qizi")
_PATRONYMIC_MALE = ("ович", "евич", "ёвич", "ўғли", "o'g'li", "угли", "уғли")
_SURNAME_FEMALE = ("ова", "ева", "ёва", "ская", "цкая", "ная", "яна")
_SURNAME_MALE = ("ов", "ев", "ёв", "ский", "цкий", "ной", "ян")

# Small reference list only — this heuristic tier is inherently the least
# reliable and is documented as such via its confidence score.
_FEMALE_FIRST_NAMES = frozenset({
    "гулнора", "дилноза", "малика", "нигора", "зарина", "мадина",
    "шахноза", "феруза", "гулбахор", "нилуфар", "муниса", "sevara",
    "dilnoza", "malika", "nigora", "zarina", "madina", "feruza",
})

CONFIDENCE_PATRONYMIC = 0.97
CONFIDENCE_SURNAME = 0.75
CONFIDENCE_FIRST_NAME = 0.60
CONFIDENCE_UNKNOWN = 0.0


def detect_gender_scored(full_name):
    """Returns {'gender': 'male'|'female'|'unknown', 'confidence': float,
    'is_estimated': bool, 'matched_rule': str | None}.

    is_estimated is True for every non-'unknown' result — nothing here is
    ever a certainty, only a heuristic guess, however high the confidence."""
    name = (full_name or "").strip().lower()
    if not name:
        return {"gender": "unknown", "confidence": CONFIDENCE_UNKNOWN,
                "is_estimated": False, "matched_rule": None}
    parts = name.split()

    for part in parts:
        if part.endswith(_PATRONYMIC_FEMALE):
            return {"gender": "female", "confidence": CONFIDENCE_PATRONYMIC,
                    "is_estimated": True, "matched_rule": "patronymic_suffix"}
        if part.endswith(_PATRONYMIC_MALE):
            return {"gender": "male", "confidence": CONFIDENCE_PATRONYMIC,
                    "is_estimated": True, "matched_rule": "patronymic_suffix"}

    for part in parts:
        if part.endswith(_SURNAME_FEMALE):
            return {"gender": "female", "confidence": CONFIDENCE_SURNAME,
                    "is_estimated": True, "matched_rule": "surname_suffix"}
        if part.endswith(_SURNAME_MALE) and len(part) > 3:
            return {"gender": "male", "confidence": CONFIDENCE_SURNAME,
                    "is_estimated": True, "matched_rule": "surname_suffix"}

    for part in parts:
        if part in _FEMALE_FIRST_NAMES:
            return {"gender": "female", "confidence": CONFIDENCE_FIRST_NAME,
                    "is_estimated": True, "matched_rule": "first_name_list"}

    return {"gender": "unknown", "confidence": CONFIDENCE_UNKNOWN,
            "is_estimated": False, "matched_rule": None}
