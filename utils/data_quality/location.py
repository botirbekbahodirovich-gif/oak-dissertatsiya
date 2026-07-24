"""Region detection for institutions — consolidates three independent,
diverging keyword tables found in the codebase:

  - migrate_institutions.py:detect_region()      13 regions, returns None on
                                                  no match (safe default).
  - app.py:detect_uni_city_region()               near-duplicate table, but
                                                  falls back to ('Toshkent',
                                                  'Toshkent') on no match.
  - app.py:detect_region() (heatmap UZ_REGIONS)   a third, differently
                                                  structured dict, also
                                                  defaults to 'Toshkent'.

None of the three recognizes "Denov" as a keyword, so any institution whose
name doesn't literally contain a recognized city word is silently routed to
Toshkent by the two implementations that default rather than return None —
this is the confirmed, reproducible root cause of "Urganch, Andijon, Denov
kabi muassasalar noto'g'ri Toshkent bo'lib qolgan" for the Denov case
specifically. This module never defaults to a region: no match means
(None, 0.0), always.
"""
import re

REGION_RULES = (
    (("buxoro", "bukhara"), "Buxoro"),
    (("andijon", "andijan"), "Andijon"),
    (("farg", "fargona", "qoqon", "kokand"), "Fargona"),
    (("samarqand", "samarkand"), "Samarqand"),
    (("namangan",), "Namangan"),
    (("nukus", "qoraqalpoq", "ajiniyoz", "berdaq"), "Qoraqalpogiston"),
    (("termiz", "surxon", "denov"), "Surxondaryo"),
    (("qarshi", "shahrisabz"), "Qashqadaryo"),
    (("jizzax",), "Jizzax"),
    (("navoiy",), "Navoiy"),
    (("urganch", "xorazm"), "Xorazm"),
    (("guliston", "sirdaryo"), "Sirdaryo"),
    (("chirchiq", "toshkent", "tashkent"), "Toshkent"),
)

_FILIAL_SPLIT_RE = re.compile(r"\bfilial\w*\b|\bфилиал\w*\b", re.IGNORECASE)


def _find_region(name_lower):
    for keywords, region in REGION_RULES:
        for kw in keywords:
            if kw in name_lower:
                return region, kw
    return None, None


def detect_region_scored(name):
    """(region, confidence, matched_keyword). No match -> (None, 0.0, None) —
    never guesses Toshkent as a fallback."""
    n = (name or "").lower()
    if not n:
        return None, 0.0, None
    region, kw = _find_region(n)
    if region is None:
        return None, 0.0, None
    return region, 1.0, kw


def flag_location_mismatch(institution_name, assigned_region):
    """None if consistent; otherwise a dict describing the mismatch, for use
    as a data_quality_issues.detail payload. `assigned_region` is whatever a
    caller currently has stored (e.g. institution_map.region or
    universities.region) — this never rewrites it, only reports."""
    detected, confidence, keyword = detect_region_scored(institution_name)
    if detected is None:
        return None  # nothing to compare against; not a mismatch, just unknown
    if assigned_region and assigned_region == detected:
        return None
    return {
        "institution_name": institution_name,
        "assigned_region": assigned_region,
        "detected_region": detected,
        "confidence": confidence,
        "matched_keyword": keyword,
    }


def detect_filial_region_conflict(name):
    """For branch/filial institutions whose parent-name keyword and
    branch-city keyword disagree — typical phrasing is "<Parent university,
    named after its home city> <Branch city> filiali", e.g. a Tashkent-based
    university's "... Urganch filiali". Finds every region keyword occurring
    in the name (in order) and compares the one immediately BEFORE "filial/
    филиал" (the branch's real city) against the first keyword found overall
    (the parent's implied city). Returns a mismatch dict, or None. This is
    the likely mechanism behind Urganch/Andijon branch campuses being
    mislabeled even though those cities ARE present in REGION_RULES."""
    if not name:
        return None
    filial_match = _FILIAL_SPLIT_RE.search(name)
    if not filial_match:
        return None
    n = name.lower()
    hits = []  # (start_pos, region, keyword)
    for keywords, region in REGION_RULES:
        for kw in keywords:
            pos = n.find(kw)
            if pos != -1:
                hits.append((pos, region, kw))
    if len(hits) < 2:
        return None
    hits.sort(key=lambda h: h[0])
    filial_pos = filial_match.start()
    before_filial = [h for h in hits if h[0] < filial_pos]
    if not before_filial:
        return None
    branch_pos, branch_region, branch_kw = before_filial[-1]
    parent_candidates = [h for h in hits if h[0] != branch_pos]
    if not parent_candidates:
        return None
    parent_pos, parent_region, parent_kw = parent_candidates[0]
    if parent_region != branch_region:
        return {
            "institution_name": name,
            "parent_region": parent_region,
            "parent_keyword": parent_kw,
            "branch_region": branch_region,
            "branch_keyword": branch_kw,
        }
    return None
