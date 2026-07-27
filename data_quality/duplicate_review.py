"""Presentation logic for the duplicate review queue — pure, no DB, no Flask.

The blueprint does auth/parsing/rendering, the repository does SQL; the rules
about *how a candidate is shown to a reviewer* live here so they can be unit
tested without a database.

Two ideas drive the design, both from the staging analysis of the 1 597
candidates:

* Candidates are not equal. ``exact`` (108), ``strong`` (181) and ``possible``
  with an identical title (~475) are high yield; ``possible`` with a title score
  in the 0.72–0.86 band (~575) is mostly noise. The queue therefore orders by
  match level first and offers a bucket filter so a reviewer can spend their
  attention on the productive pile and leave the noisy one for later.
* A reviewer decides by *comparing fields*. Marking which fields differ — and
  separating a real difference from a cosmetic one — is most of the work, so it
  is done here with the same normalizers the scanner uses.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence

from data_quality import normalizers as N
from data_quality.config import HIGH_YIELD_MIN_SCORE

# Order the queue presents by default. Lower rank is reviewed first.
LEVEL_ORDER = ("exact", "strong", "possible")
LEVEL_RANK = {level: i for i, level in enumerate(LEVEL_ORDER)}

LEVEL_LABEL = {
    "exact": "Aniq",
    "strong": "Kuchli",
    "possible": "Ehtimoliy",
}
STATUS_LABEL = {
    "pending": "Ko‘rilmagan",
    "confirmed": "Tasdiqlangan",
    "rejected": "Rad etilgan",
    "merged": "Birlashtirilgan",
}
# Statuses a reviewer may set from this screen. 'merged' is deliberately absent:
# merging records is a separate, later job — this screen never merges or deletes.
REVIEWABLE_STATUSES = ("confirmed", "rejected", "pending")

# HIGH_YIELD_MIN_SCORE comes from config so the SQL filter uses the same number.
# It only *groups* the queue — nothing is ever hidden outright.
BUCKET_HIGH = "high"
BUCKET_NOISY = "noisy"
BUCKET_LABEL = {
    BUCKET_HIGH: "Yuqori hosilli",
    BUCKET_NOISY: "Shovqinli (past ball)",
}

# Fields shown side by side, in the order a reviewer scans them.
COMPARE_FIELDS = (
    ("mavzu", "Mavzu"),
    ("olim", "Olim"),
    ("ilmiy_rahbar", "Ilmiy rahbar"),
    ("muassasa", "Muassasa"),
    ("ixtisoslik", "Ixtisoslik"),
    ("sana", "Himoya sanasi"),
    ("link", "Havola"),
)

# How each field is normalized before deciding "is this difference real?".
_NORMALIZERS = {
    "mavzu": N.normalize_title,
    "olim": N.normalize_person_name,
    "ilmiy_rahbar": N.normalize_person_name,
    "muassasa": N.normalize_organization_name,
    "sana": lambda v: N.normalize_date(v) or (v or "").strip(),
    "link": N.normalize_url,
}

SAME, MINOR, DIFFERS, ONE_SIDED, BOTH_EMPTY = (
    "same", "minor", "differs", "one_sided", "both_empty")


def bucket_of(match_level: str, score) -> str:
    """Which pile a candidate belongs to. See HIGH_YIELD_MIN_SCORE."""
    if match_level in ("exact", "strong"):
        return BUCKET_HIGH
    try:
        return BUCKET_HIGH if float(score) >= HIGH_YIELD_MIN_SCORE else BUCKET_NOISY
    except (TypeError, ValueError):
        return BUCKET_NOISY


def _normalize(field: str, value) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    normalizer = _NORMALIZERS.get(field)
    if normalizer is not None:
        try:
            text = normalizer(text) or ""
        except Exception:            # a normalizer must never break the page
            pass
    return N.normalize_whitespace(N.normalize_apostrophes(text)).strip().lower()


def _specialties(value) -> str:
    """Specialty codes compare as a set — order and separators are noise."""
    import re
    codes = {N.normalize_specialty_code(t.strip())
             for t in re.split(r"[;,\n]", str(value or "")) if t.strip()}
    return ", ".join(sorted(c for c in codes if c))


def compare_fields(left: Mapping, right: Mapping) -> List[Dict]:
    """Field-by-field comparison of the two records behind a candidate.

    Status per field:
        same        identical once normalized
        minor       differs only cosmetically (apostrophes, spacing, date
                    format, specialty order) — worth seeing, not worth arguing
        differs     a real difference the reviewer must weigh
        one_sided   one record has a value and the other does not
        both_empty  neither has anything
    """
    out: List[Dict] = []
    for field, label in COMPARE_FIELDS:
        raw_l = "" if left.get(field) is None else str(left.get(field))
        raw_r = "" if right.get(field) is None else str(right.get(field))

        if field == "ixtisoslik":
            norm_l, norm_r = _specialties(raw_l), _specialties(raw_r)
        else:
            norm_l, norm_r = _normalize(field, raw_l), _normalize(field, raw_r)

        if not norm_l and not norm_r:
            status = BOTH_EMPTY
        elif not norm_l or not norm_r:
            status = ONE_SIDED
        elif raw_l.strip() == raw_r.strip():
            status = SAME
        elif norm_l == norm_r:
            status = MINOR
        else:
            status = DIFFERS

        out.append({
            "field": field, "label": label, "status": status,
            "left": raw_l.strip(), "right": raw_r.strip(),
        })
    return out


def difference_summary(fields: Sequence[Mapping]) -> Dict[str, int]:
    """Counts per comparison status — drives the 'N ta maydon farq qiladi' line."""
    counts = {SAME: 0, MINOR: 0, DIFFERS: 0, ONE_SIDED: 0, BOTH_EMPTY: 0}
    for f in fields:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return counts


# Detector reasons are short English strings; show them in Uzbek.
_REASON_TEXT = {
    "same source link": "bir xil manba havolasi",
    "identical title + author + defense date": "mavzu + muallif + himoya sanasi bir xil",
    "same author": "bir xil muallif",
    "near author": "muallif ismi yaqin",
    "same/near organization": "tashkilot bir xil yoki yaqin",
    "defense date within 7 days": "himoya sanalari 7 kun ichida",
    "same specialty": "bir xil ixtisoslik",
    "same supervisor": "bir xil ilmiy rahbar",
}


def humanize_reasons(reasons) -> List[str]:
    """Turn the detector's reason list into readable Uzbek phrases.

    Unknown reasons are passed through rather than dropped — a new detector
    reason must never silently disappear from the reviewer's view.
    """
    if not reasons:
        return []
    if isinstance(reasons, str):
        import json
        try:
            reasons = json.loads(reasons)
        except ValueError:
            reasons = [reasons]
    out: List[str] = []
    for reason in reasons:
        text = str(reason).strip()
        if text in _REASON_TEXT:
            out.append(_REASON_TEXT[text])
            continue
        # "title similarity 0.94" -> "mavzu o'xshashligi 0.94"
        if text.startswith("title similarity"):
            out.append("mavzu o‘xshashligi" + text[len("title similarity"):])
            continue
        out.append(text)
    return out


def decorate(candidate: Mapping) -> Dict:
    """Attach everything the template needs to one candidate row."""
    left = {f: candidate.get(f"r1_{f}") for f, _ in COMPARE_FIELDS}
    right = {f: candidate.get(f"r2_{f}") for f, _ in COMPARE_FIELDS}
    fields = compare_fields(left, right)
    data = dict(candidate)
    data.update({
        "fields": fields,
        "diff_counts": difference_summary(fields),
        "reasons_text": humanize_reasons(candidate.get("reasons")),
        "bucket": bucket_of(candidate.get("match_level"), candidate.get("score")),
        "level_label": LEVEL_LABEL.get(candidate.get("match_level"),
                                       candidate.get("match_level")),
        "status_label": STATUS_LABEL.get(candidate.get("status"),
                                         candidate.get("status")),
    })
    return data


def progress_view(rows: Sequence[Mapping]) -> Dict:
    """Build the progress panel from (match_level, status, count) rows.

    Returns totals plus a per-level breakdown in review order, so the reviewer
    can see that e.g. `exact` is finished while `possible` has barely started.
    """
    by_level: Dict[str, Dict[str, int]] = {}
    for row in rows:
        level = row["match_level"]
        bucket = by_level.setdefault(level, {"total": 0, "pending": 0, "reviewed": 0})
        count = int(row["count"])
        bucket["total"] += count
        bucket[row["status"]] = bucket.get(row["status"], 0) + count
        if row["status"] == "pending":
            bucket["pending"] += count
        else:
            bucket["reviewed"] += count

    levels = []
    for level in LEVEL_ORDER:
        if level not in by_level:
            continue
        b = by_level[level]
        levels.append({
            "level": level, "label": LEVEL_LABEL.get(level, level),
            "total": b["total"], "pending": b["pending"], "reviewed": b["reviewed"],
            "percent": round(100.0 * b["reviewed"] / b["total"], 1) if b["total"] else 0.0,
            "confirmed": b.get("confirmed", 0), "rejected": b.get("rejected", 0),
            "merged": b.get("merged", 0),
        })

    total = sum(l["total"] for l in levels)
    reviewed = sum(l["reviewed"] for l in levels)
    return {
        "levels": levels,
        "total": total,
        "reviewed": reviewed,
        "pending": total - reviewed,
        "percent": round(100.0 * reviewed / total, 1) if total else 0.0,
        "confirmed": sum(l["confirmed"] for l in levels),
        "rejected": sum(l["rejected"] for l in levels),
    }
