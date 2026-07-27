"""Three-tier duplicate detection for dissertation records.

The final decision for every pair is still taken by ``difflib.SequenceMatcher``
— that is the algorithm of record and it has not changed. ``rapidfuzz`` is used
only as an optional *pre-filter* (see Performance below) and the module keeps
working, with identical output, when it is not installed. No database here; the
DB-backed persistence lives in ``duplicate_service.py``.

Tiers
-----
    exact     same source ``link``, OR identical normalized title+author+date.
    strong    high title similarity + same author + same/near org + date ±7 days.
    possible  medium-high title similarity + same specialty + near author or
              supervisor.

Performance
-----------
Never compares every record with every other (that is O(n²) on 28k+ rows).
Three layers keep the pass bounded:

1. *Blocking* — records are grouped by cheap keys (source link, (year,
   specialty), first-author token, title prefix) and only records sharing a
   block are ever considered. Oversized blocks are recursively split by a
   secondary-key ladder (16-char title prefix → author token → year →
   specialty) instead of being dropped, and anything still oversized is
   reported through a WARNING rather than silently skipped.
2. *Cheap bounds* — ``_max_possible_title_sim`` computes a provable upper bound
   on the title similarity from string lengths and token counts alone. Pairs
   that cannot reach the threshold are dropped without any string comparison.
3. *rapidfuzz pre-filter* — one C-level ``process.cdist`` per block scores the
   whole block at once with the Indel ratio. Indel similarity is always ``>=``
   the difflib ratio (difflib's Ratcliff/Obershelp match size can never exceed
   the LCS that Indel uses), so a pair below the cutoff can never pass the
   difflib threshold either. Surviving pairs — and only those — go through the
   unchanged ``_classify``/``SequenceMatcher`` path.

Because every filter is an upper bound on the score that ``_classify`` would
compute, the candidate set is *identical* to the unfiltered one. See
``docs/data_quality_performance.md``.

Nothing here deletes or merges. It only proposes candidates with a score and a
list of human-readable reasons.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from data_quality import normalizers as N
from data_quality.config import DataQualityConfig, get_config
from data_quality.models import DuplicateCandidate

log = logging.getLogger("data_quality.duplicates")

# rapidfuzz (and the numpy it needs for cdist) are optional accelerators. When
# either is missing the cheap bounds still apply and results are unchanged.
try:  # pragma: no cover - trivial import guard
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
except ImportError:  # pragma: no cover
    _rf_fuzz = None
    _rf_process = None
try:  # pragma: no cover
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

RAPIDFUZZ_AVAILABLE = _rf_process is not None and _np is not None

_TOKEN = re.compile(r"\w+", re.UNICODE)

# Blocks larger than this are split by a secondary key before comparison.
_MAX_BLOCK_SIZE = 500
# A block that survives the whole split ladder is still compared up to this
# size (with a WARNING); beyond it the pair count would dominate the run, so it
# is reported as skipped — loudly, never silently.
_HARD_BLOCK_CAP = 5000
# Primary title-prefix block key. Kept at 8 deliberately: Uzbek is suffix-rich
# ("ta'lim tizimi" / "ta'limi tizimida"), so real duplicates routinely diverge
# inside characters 9-16. A 16-char primary key drops those pairs; 16 is used
# as the *secondary* key below, where it shrinks oversized blocks without ever
# removing a pair that is compared today.
_TITLE_PREFIX_LEN = 8
_TITLE_SUBBLOCK_LEN = 16
# Below this size a matrix is not worth building; below _CDIST_MIN_WORKERS it is
# not worth handing to the thread pool either.
_CDIST_MIN_BLOCK = 16
_CDIST_MIN_WORKERS = 200
# The pair memo is keyed at the *classification* gate, so it holds one entry per
# pair that actually reached SequenceMatcher — tens of thousands on a full scan,
# not the ~20M pairs the blocks enumerate. Purely informational threshold.
_SEEN_WARN_AT = 2_000_000
# Pair keys are packed into one int; dissertation ids are Postgres `integer`.
_ID_SHIFT = 1 << 32
# Safety slack (in 0..1 similarity units) subtracted from the pre-filter cutoff
# so float rounding can never drop a pair that difflib would have accepted.
_PREFILTER_MARGIN = 0.005
_PROGRESS_INTERVAL_SEC = 5.0
_TOP_BLOCKS_REPORTED = 10


class _Sig:
    """Precomputed, comparison-ready signature for one record."""
    __slots__ = ("id", "title", "title_tokens", "author", "author_tokens",
                 "org", "supervisor", "specialty", "year", "iso", "link",
                 "title_len", "n_title_tokens")

    def __init__(self, record: Mapping):
        self.id = record.get("id")
        # Transliteration is comparison-only: ``record`` and its DB-backed
        # original values are never mutated. Run it before the ordinary field
        # normalizer so Cyrillic ъ and Latin apostrophe variants canonicalize
        # to the same final representation.
        self.title = _comparison_text(record.get("mavzu"), N.normalize_title)
        self.title_tokens = frozenset(_TOKEN.findall(self.title))
        self.author = _comparison_text(record.get("olim"), N.normalize_person_name)
        self.author_tokens = frozenset(_TOKEN.findall(self.author))
        self.org = _comparison_text(
            record.get("muassasa"), N.normalize_organization_name)
        self.supervisor = _comparison_text(
            record.get("ilmiy_rahbar"), N.normalize_person_name)
        self.specialty = _first_specialty(record.get("ixtisoslik"))
        self.iso = N.normalize_date(record.get("sana"))
        self.year = self.iso[:4] if self.iso else _loose_year(record.get("sana"))
        self.link = _norm_lower(record.get("link"))
        self.title_len = len(self.title)
        self.n_title_tokens = len(self.title_tokens)


def _norm_lower(v) -> str:
    return (v or "").strip().lower() if isinstance(v, str) else (str(v).lower() if v else "")


def _comparison_text(value, normalizer: Callable[[Optional[str]], str]) -> str:
    """Return a clean, lowercase, script-independent in-memory signature."""
    return _norm_lower(normalizer(N.to_latin(value)))


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


def _title_jaccard(a: _Sig, b: _Sig) -> float:
    if not a.title_tokens or not b.title_tokens:
        return 0.0
    union = len(a.title_tokens | b.title_tokens)
    return len(a.title_tokens & b.title_tokens) / union if union else 0.0


def _max_possible_title_sim(a: _Sig, b: _Sig) -> float:
    """Upper bound on ``_title_similarity(a, b)`` from lengths/counts only.

    ``_title_similarity`` is ``max(difflib_ratio, token_jaccard)`` and both
    terms are bounded:

        difflib ratio = 2*M/(La+Lb) with M <= min(La, Lb)  ->  2*Lmin/(Lmin+Lmax)
        jaccard       = |A∩B|/|A∪B|, |A∩B| <= Tmin, |A∪B| >= Tmax -> Tmin/Tmax

    Costs a handful of integer operations and never over-filters.
    """
    la, lb = a.title_len, b.title_len
    if not la or not lb:
        return 0.0
    lo, hi = (la, lb) if la <= lb else (lb, la)
    bound = 2.0 * lo / (lo + hi)
    ta, tb = a.n_title_tokens, b.n_title_tokens
    if ta and tb:
        tlo, thi = (ta, tb) if ta <= tb else (tb, ta)
        jacc_bound = tlo / thi
        if jacc_bound > bound:
            bound = jacc_bound
    return bound


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


# ── blocking ────────────────────────────────────────────────────────────────
def _first_author_token(s: _Sig) -> str:
    return min(s.author_tokens) if s.author_tokens else ""


def _build_blocks(sigs: List[_Sig],
                  title_prefix_len: int = _TITLE_PREFIX_LEN
                  ) -> Dict[Tuple[str, ...], List[int]]:
    blocks: Dict[Tuple[str, ...], List[int]] = {}

    def add(key, idx):
        blocks.setdefault(key, []).append(idx)

    for idx, s in enumerate(sigs):
        # Same source link is an exact-duplicate signal on its own, so it must be
        # a block key even when title/author/year differ (a corrected re-import).
        if s.link:
            add(("ln", s.link), idx)
        if s.year and s.specialty:
            add(("ys", s.year + "|" + s.specialty), idx)
        first_author = _first_author_token(s)
        if first_author:
            add(("au", first_author), idx)
        if s.title_len >= title_prefix_len:
            add(("tt", s.title[:title_prefix_len]), idx)
    return blocks


# Secondary keys, tried in order, that break an oversized block apart.
_SUB_KEYS: Tuple[Tuple[str, Callable[[_Sig], str]], ...] = (
    ("t16", lambda s: s.title[:_TITLE_SUBBLOCK_LEN]),
    ("au", _first_author_token),
    ("yr", lambda s: s.year),
    ("sp", lambda s: s.specialty),
)


def _split_block(key: Tuple[str, ...], idxs: List[int], sigs: List[_Sig],
                 level: int, max_block_size: int, hard_block_cap: int,
                 plan: List[Tuple[Tuple[str, ...], List[int]]],
                 report: "DetectionStats") -> None:
    """Append bounded work units for ``idxs`` to ``plan``. Deterministic."""
    if len(idxs) < 2:
        return
    if len(idxs) <= max_block_size:
        plan.append((key, idxs))
        return
    if level < len(_SUB_KEYS):
        name, keyfn = _SUB_KEYS[level]
        buckets: Dict[str, List[int]] = {}
        for i in idxs:
            buckets.setdefault(keyfn(sigs[i]), []).append(i)
        if len(buckets) > 1:
            for sub, sub_idxs in buckets.items():
                _split_block(key + (f"{name}={sub[:24]}",), sub_idxs, sigs,
                             level + 1, max_block_size, hard_block_cap,
                             plan, report)
        else:
            # This key has no discriminating power here — try the next one.
            _split_block(key, idxs, sigs, level + 1, max_block_size,
                         hard_block_cap, plan, report)
        return

    # Ladder exhausted and still oversized: never drop it quietly.
    pairs = len(idxs) * (len(idxs) - 1) // 2
    if len(idxs) > hard_block_cap:
        log.warning("duplicate blocking: block %s still holds %s records after "
                    "sub-blocking (max %s, hard cap %s) — %s pairs would be "
                    "needed, block NOT compared", key, len(idxs),
                    max_block_size, hard_block_cap, pairs)
        report.blocks_skipped.append((key, len(idxs)))
        return
    log.warning("duplicate blocking: block %s still holds %s records after "
                "sub-blocking (max %s) — comparing anyway (%s pairs)",
                key, len(idxs), max_block_size, pairs)
    report.blocks_oversized.append((key, len(idxs)))
    plan.append((key, idxs))


def _plan_blocks(blocks: Dict[Tuple[str, ...], List[int]], sigs: List[_Sig],
                 max_block_size: int, hard_block_cap: int,
                 report: "DetectionStats"
                 ) -> List[Tuple[Tuple[str, ...], List[int]]]:
    plan: List[Tuple[Tuple[str, ...], List[int]]] = []
    for key, idxs in blocks.items():
        if len(idxs) < 2:
            continue
        _split_block(key, idxs, sigs, 0, max_block_size, hard_block_cap,
                     plan, report)
    return plan


# ── stats / progress ────────────────────────────────────────────────────────
@dataclass
class DetectionStats:
    """Counters for one detection pass (benchmarking + operator visibility)."""
    records: int = 0
    blocks_built: int = 0
    blocks_planned: int = 0
    blocks_done: int = 0
    pairs_seen: int = 0            # pairs examined in the pair loop
    pairs_repeated: int = 0        # skipped: already classified via another key
    pairs_cheap_skipped: int = 0   # dropped by the length/token bound
    pairs_rf_skipped: int = 0      # dropped by the rapidfuzz pre-filter
    pairs_classified: int = 0      # full SequenceMatcher classification
    candidates: int = 0
    largest_blocks: List[Tuple[str, int]] = field(default_factory=list)
    blocks_oversized: List[Tuple[Tuple[str, ...], int]] = field(default_factory=list)
    blocks_skipped: List[Tuple[Tuple[str, ...], int]] = field(default_factory=list)
    seconds: float = 0.0
    rapidfuzz_used: bool = False

    def as_dict(self) -> Dict:
        return {
            "records": self.records,
            "blocks_built": self.blocks_built,
            "blocks_planned": self.blocks_planned,
            "pairs_seen": self.pairs_seen,
            "pairs_repeated": self.pairs_repeated,
            "pairs_cheap_skipped": self.pairs_cheap_skipped,
            "pairs_rf_skipped": self.pairs_rf_skipped,
            "pairs_classified": self.pairs_classified,
            "candidates": self.candidates,
            "largest_blocks": self.largest_blocks,
            "oversized_blocks": len(self.blocks_oversized),
            "skipped_blocks": len(self.blocks_skipped),
            "seconds": round(self.seconds, 3),
            "rapidfuzz_used": self.rapidfuzz_used,
        }


def _pair_key(a_id: int, b_id: int):
    """Canonical, compact key for a pair. Falls back to a tuple if an id is
    somehow wider than 32 bits, so the packing can never collide."""
    lo, hi = (a_id, b_id) if a_id <= b_id else (b_id, a_id)
    if hi < _ID_SHIFT:
        return lo * _ID_SHIFT + hi
    return (lo, hi)


def _block_matrix(idxs: List[int], sigs: List[_Sig], cutoff: float):
    """Score a whole block with rapidfuzz in one C call, or None when the
    accelerator is unavailable / the block is too small to be worth it."""
    if not RAPIDFUZZ_AVAILABLE or len(idxs) < _CDIST_MIN_BLOCK:
        return None
    # One list object passed twice: the matrix is symmetric and rapidfuzz can
    # exploit that. Small blocks stay single-threaded — the pool costs more than
    # the few thousand comparisons it would save.
    titles = [sigs[k].title for k in idxs]
    try:
        return _rf_process.cdist(titles, titles, scorer=_rf_fuzz.ratio,
                                 score_cutoff=max(0.0, cutoff * 100.0),
                                 workers=-1 if len(idxs) >= _CDIST_MIN_WORKERS else 1)
    except Exception:  # pragma: no cover - accelerator must never be fatal
        log.debug("rapidfuzz cdist unavailable for a block; using cheap bounds only",
                  exc_info=True)
        return None


# ── main entry points ───────────────────────────────────────────────────────
def detect_duplicates(records: List[Mapping],
                      config: Optional[DataQualityConfig] = None,
                      max_block_size: int = _MAX_BLOCK_SIZE,
                      *,
                      hard_block_cap: int = _HARD_BLOCK_CAP,
                      title_prefix_len: int = _TITLE_PREFIX_LEN,
                      progress: Optional[Callable[[DetectionStats], None]] = None
                      ) -> List[DuplicateCandidate]:
    """Return de-duplicated candidate pairs across ``records``. Deterministic."""
    candidates, _ = detect_duplicates_with_stats(
        records, config, max_block_size, hard_block_cap=hard_block_cap,
        title_prefix_len=title_prefix_len, progress=progress)
    return candidates


def detect_duplicates_with_stats(
        records: List[Mapping],
        config: Optional[DataQualityConfig] = None,
        max_block_size: int = _MAX_BLOCK_SIZE,
        *,
        hard_block_cap: int = _HARD_BLOCK_CAP,
        title_prefix_len: int = _TITLE_PREFIX_LEN,
        progress: Optional[Callable[[DetectionStats], None]] = None
) -> Tuple[List[DuplicateCandidate], DetectionStats]:
    """Same as :func:`detect_duplicates` but also returns the pass counters."""
    config = config or get_config()
    started = time.monotonic()
    stats = DetectionStats(records=len(records),
                           rapidfuzz_used=RAPIDFUZZ_AVAILABLE)

    sigs = [_Sig(r) for r in records]
    blocks = _build_blocks(sigs, title_prefix_len)
    stats.blocks_built = len(blocks)
    plan = _plan_blocks(blocks, sigs, max_block_size, hard_block_cap, stats)
    stats.blocks_planned = len(plan)
    stats.largest_blocks = [(_key_label(k), len(v)) for k, v in
                            sorted(plan, key=lambda kv: -len(kv[1]))
                            [:_TOP_BLOCKS_REPORTED]]

    # Any pair scoring below this in rapidfuzz/the cheap bound cannot reach
    # either difflib threshold, so it can be dropped without classifying.
    cutoff = min(config.possible_threshold, config.strong_threshold) - _PREFILTER_MARGIN

    # One pair is classified at most once, however many block keys it shares.
    # This set is also what guarantees a candidate is emitted only once.
    seen: set = set()
    out: List[DuplicateCandidate] = []
    last_log = [started]

    def tick() -> None:
        """Heartbeat, called per block and per row so one huge block cannot go
        silent for minutes."""
        now = time.monotonic()
        if now - last_log[0] < _PROGRESS_INTERVAL_SEC:
            return
        last_log[0] = now
        stats.seconds = now - started
        log.info("duplicates: blocks %s/%s | pairs compared: %s | "
                 "classified: %s | candidates: %s | %.0fs",
                 f"{stats.blocks_done:,}", f"{stats.blocks_planned:,}",
                 f"{stats.pairs_seen:,}", f"{stats.pairs_classified:,}",
                 f"{stats.candidates:,}", stats.seconds)
        if progress:
            progress(stats)

    for key, idxs in plan:
        matrix = _block_matrix(idxs, sigs, cutoff)
        _scan_block(idxs, sigs, config, cutoff, matrix, seen, out, stats, tick)
        stats.blocks_done += 1
        tick()

    out.sort(key=lambda c: (-c.score, c.record_id_1, c.record_id_2))
    stats.seconds = time.monotonic() - started
    _log_summary(stats)
    return out, stats


def _scan_block(idxs: List[int], sigs: List[_Sig], config: DataQualityConfig,
                cutoff: float, matrix, seen: set,
                out: List[DuplicateCandidate], stats: DetectionStats,
                tick: Callable[[], None]) -> None:
    n = len(idxs)
    for i in range(n):
        if n >= _CDIST_MIN_WORKERS:
            tick()
        a = sigs[idxs[i]]
        aid = a.id
        if aid is None:
            continue
        row = matrix[i] if matrix is not None else None
        for j in range(i + 1, n):
            b = sigs[idxs[j]]
            bid = b.id
            if bid is None or aid == bid:
                continue
            stats.pairs_seen += 1

            # Filters first: the overwhelming majority of pairs die here, and
            # they are cheaper than building a pair key and probing the memo.
            # An identical source link is an exact match regardless of text, so
            # it must bypass every title-based filter.
            if not (a.link and a.link == b.link):
                if _max_possible_title_sim(a, b) < cutoff:
                    stats.pairs_cheap_skipped += 1
                    continue
                if row is not None and row[j] == 0.0:
                    # Indel ratio is below the cutoff, so the difflib ratio is
                    # too; only a high token overlap can still carry this pair.
                    if _title_jaccard(a, b) < cutoff:
                        stats.pairs_rf_skipped += 1
                        continue

            # Mark the pair as handled BEFORE classifying. The old code only
            # remembered pairs that turned out to *be* candidates, so every
            # near-miss was re-scored once per block key it shared — and this
            # set is what keeps a candidate from being emitted twice.
            pair = _pair_key(aid, bid)
            if pair in seen:
                stats.pairs_repeated += 1
                continue
            seen.add(pair)
            if len(seen) == _SEEN_WARN_AT:
                log.warning("duplicates: %s pairs have reached full "
                            "classification — the blocking keys are weak for "
                            "this dataset", f"{_SEEN_WARN_AT:,}")

            stats.pairs_classified += 1
            cand = _classify(a, b, config)
            if cand is not None:
                out.append(cand.ordered())
                stats.candidates += 1


def _key_label(key: Tuple[str, ...]) -> str:
    return "/".join(str(part)[:24] for part in key)


def _log_summary(stats: DetectionStats) -> None:
    log.info("duplicates done: %s records | %s blocks (%s work units) | "
             "pairs %s (cheap-skip %s, rapidfuzz-skip %s, classified %s) | "
             "candidates %s | %.1fs | rapidfuzz=%s",
             f"{stats.records:,}", f"{stats.blocks_built:,}",
             f"{stats.blocks_planned:,}", f"{stats.pairs_seen:,}",
             f"{stats.pairs_cheap_skipped:,}", f"{stats.pairs_rf_skipped:,}",
             f"{stats.pairs_classified:,}", f"{stats.candidates:,}",
             stats.seconds, stats.rapidfuzz_used)
    for label, size in stats.largest_blocks:
        log.info("  largest block: %-40s %s records (%s pairs)",
                 label, size, size * (size - 1) // 2)
    if stats.blocks_skipped:
        log.warning("duplicates: %s block(s) exceeded the hard cap and were not "
                    "compared", len(stats.blocks_skipped))


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
