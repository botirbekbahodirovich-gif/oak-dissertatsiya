"""Field validators — turn a raw ``dissertations`` row into a list of Issues.

Pure logic: no DB, no Flask. A "record" is a mapping whose keys are the
dissertation column names (``id, olim, mavzu, muassasa, ixtisoslik, sana,
ilmiy_rahbar, link, daraja, ...``). ``today`` is injectable so future-date
checks are deterministic in tests.

Three kinds of finding are produced:
  * completeness  -> a required field is empty
  * validity      -> a present value is malformed / impossible
  * consistency   -> the normalized value differs from the original (a safe,
                     previewable rewrite), surfaced as INCONSISTENT_* codes
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable, List, Mapping, Optional

from data_quality import models as M
from data_quality import normalizers as N

RECORD_TYPE = "dissertation"

# Supervisor placeholder templates left over from the OAK source (mirrors
# scripts/flag_data_quality.py). A supervisor value that is only a template is
# effectively missing.
_SUPERVISOR_PLACEHOLDERS = (
    "ф.и.ш", "f.i.sh", "ф.и.о", "фиш",
    "ининг ф", "ининг f", "нинг ф.и", "нинг f.i",
)

# ilmiy_rahbar sometimes holds a DEGREE PHRASE instead of (or alongside) a
# name — e.g. "педагогика фанлари доктори (DSc)". A real person's name never
# contains these words, so the false-positive risk is low.
_DEGREE_TEXT_HINT = re.compile(
    r"\b(PhD|DSc)\b"
    r"|фан(?:лари)?\s+доктор"
    r"|фалсафа\s+доктор"
    r"|фанлари\s+номзод"
    r"|доктори\b"
    r"|номзоди\b"
    r"|doktori\b"
    r"|nomzodi\b",
    re.IGNORECASE)

# Uzbek surname / patronymic endings, used to tell "this token is a name"
# apart from "this token is degree prose" when both may be present on one
# line. Cyrillic and Latin script both occur in the data.
_SURNAME_SUFFIXES = ("ов", "ева", "ова", "ев", "ий", "ийна",
                     "ov", "ova", "eva", "ev")
_PATRONYMIC_WORDS = frozenset({
    "огли", "ўғли", "қизи", "ogli", "o'g'li", "qizi",
})
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

_MIN_TITLE_LEN = 12          # a real dissertation title is never this short
# Some OAK rows carry the registry code in the title column instead of the
# title, e.g. "В2026.2.PhD/Iqt4897". Those are long enough to slip past
# _MIN_TITLE_LEN, so they need their own check. Optional leading letter is
# Cyrillic or Latin; IGNORECASE also covers the lowercase variants.
_TITLE_CODE = re.compile(r"^[А-ЯЁа-яёA-Za-z]?\d{4}\.\d{1,2}\.(?:PhD|DSc)/",
                         re.IGNORECASE)
_HTML_HINT = re.compile(r"<[^>]+>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")
_CONTROL_HINT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPECIALTY_OK = re.compile(r"^\d{2}\.\d{2}\.\d{2}$")
_URL_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def _is_placeholder_supervisor(v: str) -> bool:
    low = v.lower()
    return any(p in low for p in _SUPERVISOR_PLACEHOLDERS)


def _has_degree_text(v: str) -> bool:
    return bool(_DEGREE_TEXT_HINT.search(v))


def _looks_like_name(v: str) -> bool:
    """True when at least one token in ``v`` looks like an Uzbek surname or
    patronymic — i.e. a real name is present, not just degree prose."""
    for tok in _WORD.findall(v):
        low = tok.lower()
        if low in _PATRONYMIC_WORDS or low.endswith(_SURNAME_SUFFIXES):
            return True
    return False


def _mk(record_id, field, code, severity, desc, original=None, normalized=None):
    return M.Issue(
        record_type=RECORD_TYPE, record_id=record_id, field_name=field,
        issue_code=code, severity=severity, description=desc,
        original_value=original, normalized_value=normalized,
    )


def _consistency(record_id, field, code, original, normalized) -> Optional[M.Issue]:
    """Emit an INCONSISTENT_* issue only when normalisation actually changed the
    value (and both are non-empty)."""
    if _blank(original):
        return None
    if normalized is not None and normalized != original:
        return _mk(record_id, field, code, M.SEVERITY_INFO,
                   "Normalizatsiya qilinganda qiymat o‘zgaradi (xavfsiz tuzatish)",
                   original=str(original), normalized=str(normalized))
    return None


def validate_record(record: Mapping, today: Optional[date] = None) -> List[M.Issue]:
    today = today or date.today()
    rid = record.get("id")
    issues: List[M.Issue] = []

    olim = record.get("olim")
    mavzu = record.get("mavzu")
    muassasa = record.get("muassasa")
    ixt = record.get("ixtisoslik")
    sana = record.get("sana")
    rahbar = record.get("ilmiy_rahbar")
    link = record.get("link")
    daraja = record.get("daraja")

    # ── Completeness ───────────────────────────────────────────────────────
    if _blank(olim):
        issues.append(_mk(rid, "olim", M.MISSING_AUTHOR, M.SEVERITY_CRITICAL,
                          "Muallif (olim) bo‘sh"))
    if _blank(mavzu):
        issues.append(_mk(rid, "mavzu", M.MISSING_TITLE, M.SEVERITY_CRITICAL,
                          "Mavzu bo‘sh"))
    if _blank(muassasa):
        issues.append(_mk(rid, "muassasa", M.MISSING_ORG, M.SEVERITY_WARNING,
                          "Tashkilot (muassasa) bo‘sh"))
    if _blank(ixt):
        issues.append(_mk(rid, "ixtisoslik", M.MISSING_SPECIALTY, M.SEVERITY_WARNING,
                          "Ixtisoslik bo‘sh"))
    if _blank(sana):
        issues.append(_mk(rid, "sana", M.MISSING_DATE, M.SEVERITY_WARNING,
                          "Himoya sanasi bo‘sh"))
    if _blank(rahbar):
        issues.append(_mk(rid, "ilmiy_rahbar", M.MISSING_SUPERVISOR, M.SEVERITY_WARNING,
                          "Ilmiy rahbar bo‘sh"))
    else:
        rahbar_s = str(rahbar)
        if _is_placeholder_supervisor(rahbar_s):
            issues.append(_mk(rid, "ilmiy_rahbar", M.PLACEHOLDER_SUPERVISOR, M.SEVERITY_WARNING,
                              "Ilmiy rahbar — to‘ldirilmagan shablon (F.I.Sh)",
                              original=rahbar_s))
        elif _has_degree_text(rahbar_s):
            if _looks_like_name(rahbar_s):
                issues.append(_mk(rid, "ilmiy_rahbar", M.SUPERVISOR_WITH_DEGREE,
                                  M.SEVERITY_INFO,
                                  "Ilmiy rahbar maydonida ism va ilmiy daraja bitta "
                                  "satrda aralashgan",
                                  original=rahbar_s))
            else:
                issues.append(_mk(rid, "ilmiy_rahbar", M.PLACEHOLDER_SUPERVISOR,
                                  M.SEVERITY_WARNING,
                                  "Ilmiy rahbar o‘rniga faqat ilmiy daraja matni "
                                  "yozilgan (ism yo‘q)",
                                  original=rahbar_s))
    if _blank(link):
        issues.append(_mk(rid, "link", M.MISSING_URL, M.SEVERITY_INFO,
                          "Manba havolasi (link) bo‘sh"))

    # ── Validity ───────────────────────────────────────────────────────────
    if not _blank(mavzu):
        title = str(mavzu)
        if len(title.strip()) < _MIN_TITLE_LEN:
            issues.append(_mk(rid, "mavzu", M.SHORT_TITLE, M.SEVERITY_WARNING,
                              f"Mavzu juda qisqa (< {_MIN_TITLE_LEN} belgi)",
                              original=title))
        if _TITLE_CODE.match(title.strip()):
            issues.append(_mk(rid, "mavzu", M.TITLE_IS_CODE, M.SEVERITY_CRITICAL,
                              "Mavzu o‘rniga OAK ro‘yxat kodi yozilgan "
                              "(masalan В2026.2.PhD/Iqt4897)",
                              original=title))
        if _HTML_HINT.search(title):
            issues.append(_mk(rid, "mavzu", M.HTML_RESIDUE, M.SEVERITY_WARNING,
                              "Mavzuda HTML/entity qoldig‘i bor", original=title))
        if _CONTROL_HINT.search(title):
            issues.append(_mk(rid, "mavzu", M.CONTROL_CHARS, M.SEVERITY_WARNING,
                              "Mavzuda control belgilar bor", original=title))

    if not _blank(sana):
        iso = N.normalize_date(sana)
        if iso is None:
            issues.append(_mk(rid, "sana", M.INVALID_DATE, M.SEVERITY_WARNING,
                              "Himoya sanasi tan olinmadi (noto‘g‘ri format)",
                              original=str(sana)))
        else:
            try:
                y, m, d = (int(x) for x in iso.split("-"))
                if date(y, m, d) > today:
                    issues.append(_mk(rid, "sana", M.FUTURE_DATE, M.SEVERITY_WARNING,
                                      "Himoya sanasi kelajakda (mantiqsiz)",
                                      original=str(sana), normalized=iso))
            except ValueError:
                pass

    if not _blank(daraja):
        canon = N.normalize_degree(daraja)
        if canon not in (N.DEGREE_PHD, N.DEGREE_DSC):
            issues.append(_mk(rid, "daraja", M.INVALID_DEGREE, M.SEVERITY_WARNING,
                              "Daraja tan olinmadi (PhD/DSc emas)",
                              original=str(daraja)))

    if not _blank(ixt):
        # A record may carry several codes; check each non-empty token.
        for token in re.split(r"[;,\n]", str(ixt)):
            token = token.strip()
            if not token:
                continue
            code = N.normalize_specialty_code(token)
            if re.search(r"\d", token) and not _SPECIALTY_OK.match(code):
                issues.append(_mk(rid, "ixtisoslik", M.INVALID_SPECIALTY_CODE,
                                  M.SEVERITY_INFO,
                                  "Ixtisoslik kodi formati shubhali (NN.NN.NN emas)",
                                  original=token, normalized=code))

    if not _blank(link):
        norm_url = N.normalize_url(link)
        if not _URL_SCHEME.match(norm_url):
            issues.append(_mk(rid, "link", M.MALFORMED_URL, M.SEVERITY_INFO,
                              "Havola URL formatiga o‘xshamaydi", original=str(link)))

    # ── Consistency (safe, previewable rewrites) ───────────────────────────
    _add(issues, _consistency(rid, "daraja", M.INCONSISTENT_DEGREE,
                              daraja, N.normalize_degree(daraja) if daraja else None))
    for fld, val in (("olim", olim), ("mavzu", mavzu), ("muassasa", muassasa),
                     ("ilmiy_rahbar", rahbar)):
        if _blank(val):
            continue
        # apostrophe-only change is the safest auto-fixable rewrite
        _add(issues, _consistency(rid, fld, M.INCONSISTENT_APOSTROPHE,
                                  val, N.normalize_apostrophes(str(val))))
        # whitespace collapse
        _add(issues, _consistency(rid, fld, M.INCONSISTENT_WHITESPACE,
                                  val, N.normalize_whitespace(str(val))))
    if not _blank(link):
        _add(issues, _consistency(rid, "link", M.INCONSISTENT_URL,
                                  link, N.normalize_url(link)))

    return issues


def _add(issues: List[M.Issue], maybe: Optional[M.Issue]) -> None:
    if maybe is not None:
        issues.append(maybe)


def validate_many(records: Iterable[Mapping], today: Optional[date] = None):
    """Yield (record, issues) for a batch, streaming-friendly."""
    for rec in records:
        yield rec, validate_record(rec, today=today)
