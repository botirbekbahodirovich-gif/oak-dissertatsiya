"""Three-tier duplicate detection for dissertation records.

Pure logic, standard library only (``difflib``) — no external similarity
package and no database. The DB-backed candidate generation/persistence lives
in ``duplicate_service.py``; this module is the algorithm.

Tiers
-----
    exact     same source ``link``, OR identical normalized title+author+date.
    strong    high title similarity + same author + same/near org + date ±7 days.
    possible  medium-high title similarity + same specialty + near author or
              supervisor.

Performance
-----------
Never compares every record with every other (that is O(n²) on 28k+ rows).
Records are grouped into *blocks* by cheap keys — (year, specialty),
first-author-token, and a title-token prefix — and only records that share a
block are ever compared. Oversized blocks (e.g. thousands of records with an
empty year) are skipped with a warning to keep the pass bounded. See
``docs/data_quality_performance.md``.

Nothing here deletes or merges. It only proposes candidates with a score and a
list of human-readable reasons.
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Dict, List, Mapping, Optional, Tuple

from data_quality import normalizers as N
from data_quality.config import DataQualityConfig, get_config
from data_quality.models import DuplicateCandidate

_TOKEN = re.compile(r"\w+", re.UNICODE)
# Blocks larger than this are skipped (they carry no discriminating power and
# would dominate the runtime). Tunable via the caller if ever needed.
_MAX_BLOCK_SIZE = 400


class _Sig:
    """Precomputed, comparison-ready signature for one record."""
    __slots__ = ("id", "title", "title_tokens", "author", "author_tokens",
                 "org", "supervisor", "specialty", "year", "iso", "link")

    def __init__(self, record: Mapping):
        self.id = record.get("id")
        self.title = _norm_lower(N.normalize_title(record.get("mavzu")))
        self.title_tokens = frozenset(_TOKEN.findall(self.title))
        self.author = _norm_lower(N.normalize_person_name(record.get("olim")))
        self.author_tokens = frozenset(_TOKEN.findall(self.author))
        self.org = _norm_lower(N.normalize_organization_name(record.get("muassasa")))
        self.supervisor = _norm_lower(N.normalize_person_name(record.get("ilmiy_rahbar")))
        self.specialty = _first_specialty(record.get("ixtisoslik"))
        self.iso = N.normalize_date(record.get("sana"))
        self.year = self.iso[:4] if self.iso else _loose_year(record.get("sana"))
        self.link = _norm_lower(record.get("link"))


def _norm_lower(v) -> str:
    return (v or "").strip().lower() if isinstance(v, str) else (str(v).lower() if v else "")


def _first_specialty(v) -> str:
    if not v:
        return ""
    token = re.split(r"[;,\n]", str(v))[0]
    return N.normalize_specialty_code(token)


_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _loose_year(v) -> str:
    if not v:
        return ""
    m = _YEAR_RE.search(str(v))
    return m.group(0) if m else ""


def _title_similarity(a: _Sig, b: _Sig) -> float:
    if not a.title or not b.title:
        return 0.0
    seq = SequenceMatcher(None, a.title, b.title).ratio()
    if a.title_tokens and b.title_tokens:
        inter = len(a.title_tokens & b.title_tokens)
        union = len(a.title_tokens | b.title_tokens)
        jacc = inter / union if union else 0.0
    else:
        jacc = 0.0
    return max(seq, jacc)


def _author_match(a: _Sig, b: _Sig) -> bool:
    if not a.author or not b.author:
        return False
    if a.author == b.author:
        return True
    if a.author_tokens and b.author_tokens:
        inter = len(a.author_tokens & b.author_tokens)
        smaller = min(len(a.author_tokens), len(b.author_tokens))
        if smaller and inter / smaller >= 0.6:
            return True
    return SequenceMatcher(None, a.author, b.author).ratio() >= 0.9


def _org_near(a: _Sig, b: _Sig) -> bool:
    if not a.org or not b.org:
        return False
    return a.org == b.org or SequenceMatcher(None, a.org, b.org).ratio() >= 0.85


def _within_days(a: _Sig, b: _Sig, days: int) -> Optional[bool]:
    """True/False when both dates are known, None when we cannot tell."""
    if not a.iso or not b.iso:
        return None
    try:
        da = date(*(int(x) for x in a.iso.split("-")))
        db = date(*(int(x) for x in b.iso.split("-")))
    except ValueError:
        return None
    return abs((da - db).days) <= days


def _build_blocks(sigs: List[_Sig]) -> Dict[Tuple[str, str], List[int]]:
    blocks: Dict[Tuple[str, str], List[int]] = {}

    def add(key, idx):
        blocks.setdefault(key, []).append(idx)

    for idx, s in enumerate(sigs):
        # Same source link is an exact-duplicate signal on its own, so it must be
        # a block key even when title/author/year differ (a corrected re-import).
        if s.link:
            add(("ln", s.link), idx)
        if s.year and s.specialty:
            add(("ys", s.year + "|" + s.specialty), idx)
        first_author = next(iter(sorted(s.author_tokens)), "") if s.author_tokens else ""
        if first_author:
            add(("au", first_author), idx)
        if len(s.title) >= 8:
            add(("tt", s.title[:8]), idx)
    return blocks


def detect_duplicates(records: List[Mapping],
                      config: Optional[DataQualityConfig] = None,
                      max_block_size: int = _MAX_BLOCK_SIZE) -> List[DuplicateCandidate]:
    """Return de-duplicated candidate pairs across ``records``. Deterministic."""
    config = config or get_config()
    sigs = [_Sig(r) for r in records]

    blocks = _build_blocks(sigs)
    seen: set = set()
    out: List[DuplicateCandidate] = []

    for key, idxs in blocks.items():
        if len(idxs) < 2 or len(idxs) > max_block_size:
            continue
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                a, b = sigs[idxs[i]], sigs[idxs[j]]
                if a.id is None or b.id is None or a.id == b.id:
                    continue
                pair = (a.id, b.id) if a.id <= b.id else (b.id, a.id)
                if pair in seen:
                    continue
                cand = _classify(a, b, config)
                if cand is not None:
                    seen.add(pair)
                    out.append(cand.ordered())
    out.sort(key=lambda c: (-c.score, c.record_id_1, c.record_id_2))
    return out


def _classify(a: _Sig, b: _Sig,
              config: DataQualityConfig) -> Optional[DuplicateCandidate]:
    reasons: List[str] = []

    # ── L1 EXACT ───────────────────────────────────────────────────────────
    if a.link and b.link and a.link == b.link:
        return DuplicateCandidate(a.id, b.id, 1.0, "exact", ["same source link"])
    if (a.title and a.title == b.title and a.author and a.author == b.author
            and a.iso and a.iso == b.iso):
        return DuplicateCandidate(a.id, b.id, 1.0, "exact",
                                  ["identical title + author + defense date"])

    title_sim = _title_similarity(a, b)
    author_ok = _author_match(a, b)

    # ── L2 STRONG ──────────────────────────────────────────────────────────
    if title_sim >= config.strong_threshold and author_ok:
        near_date = _within_days(a, b, 7)
        org_ok = _org_near(a, b)
        # Require at least one strong corroborating signal beyond title+author,
        # and never contradict a *known* far-apart date.
        if near_date is not False and (org_ok or near_date is True):
            reasons.append(f"title similarity {title_sim:.2f}")
            reasons.append("same author")
            if org_ok:
                reasons.append("same/near organization")
            if near_date is True:
                reasons.append("defense date within 7 days")
            return DuplicateCandidate(a.id, b.id, round(title_sim, 4), "strong", reasons)

    # ── L3 POSSIBLE ────────────────────────────────────────────────────────
    if title_sim >= config.possible_threshold:
        specialty_ok = bool(a.specialty) and a.specialty == b.specialty
        supervisor_near = bool(a.supervisor) and a.supervisor == b.supervisor
        if specialty_ok and (author_ok or supervisor_near):
            reasons.append(f"title similarity {title_sim:.2f}")
            reasons.append("same specialty")
            if author_ok:
                reasons.append("near author")
            if supervisor_near:
                reasons.append("same supervisor")
            return DuplicateCandidate(a.id, b.id, round(title_sim, 4), "possible", reasons)

    return None


def compare_pair(record_a: Mapping, record_b: Mapping,
                 config: Optional[DataQualityConfig] = None) -> Optional[DuplicateCandidate]:
    """Convenience: classify a single pair (used by the import pre-check to test
    an incoming row against an existing one)."""
    config = config or get_config()
    return _classify(_Sig(record_a), _Sig(record_b), config)
