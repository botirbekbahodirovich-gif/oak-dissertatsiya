"""Correctness guards for the duplicate-detection performance hotfix.

These tests are pure — no database, no network. They exist to prove that the
pre-filters (cheap length/token bounds + the rapidfuzz Indel pre-filter) and the
new block splitting can only ever make the pass *faster*, never make it find
fewer duplicates.

The load-bearing test is :class:`EquivalenceTests` — it re-implements the
unfiltered "compare every pair inside every block with SequenceMatcher" loop and
asserts the optimized detector returns the byte-identical candidate list.

Run:
    python3 -m unittest tests.test_data_quality_duplicate_perf -v
"""
from __future__ import annotations

import random
import unittest
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

from data_quality import duplicate_detector as D
from data_quality.config import DataQualityConfig

# ── synthetic corpus ────────────────────────────────────────────────────────
# Uzbek-flavoured vocabulary: suffix-rich, so near-duplicates diverge in the
# middle of words the way real OAK titles do.
_WORDS = [
    "sun'iy", "intellekt", "asoslari", "tibbiyotda", "qoʻllash", "iqtisodiy",
    "tahlil", "metodlari", "modellari", "tizimi", "tizimida", "taʼlim",
    "taʼlimi", "raqamli", "texnologiyalar", "boshqaruv", "samaradorligini",
    "oshirish", "yoʻllari", "milliy", "iqtisodiyot", "tarmoqlari",
    "rivojlantirish", "muammolari", "istiqbollari", "axborot", "xavfsizligi",
    "matematik", "modellashtirish", "neyron", "tarmoqlar", "asosida", "tibbiy",
    "tasvir", "tasniflash", "agrar", "sohada", "innovatsion", "faoliyat",
    "huquqiy", "asoslarini", "takomillashtirish", "ijtimoiy", "siyosiy",
]
_SURNAMES = ["Aliyev", "Karimov", "Toshpulatov", "Yusupov", "Rahmonov",
             "Abdullayev", "Ergashev", "Nazarov", "Qodirov", "Saidov"]
_NAMES = ["Vali", "Karim", "Bobur", "Ravshan", "Jasur", "Dilshod", "Aziz"]
_ORGS = ["Toshkent Axborot Texnologiyalari Universiteti",
         "Oʻzbekiston Milliy Universiteti", "TDIU", "Samarqand Davlat Universiteti"]
_SPECIALTIES = ["05.01.02", "05.01.04", "08.00.01", "08.00.13", "13.00.01"]


def _title(rng: random.Random) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(rng.randint(4, 11)))


def _mutate(rng: random.Random, text: str) -> str:
    """Perturb a title the way a re-import or a typo would."""
    words = text.split()
    mode = rng.random()
    if mode < 0.3 and len(words) > 2:                 # word swap (jaccard-high)
        i = rng.randrange(len(words) - 1)
        words[i], words[i + 1] = words[i + 1], words[i]
    elif mode < 0.55:                                  # suffix change
        i = rng.randrange(len(words))
        words[i] = words[i] + rng.choice(["i", "da", "ning", "ni"])
    elif mode < 0.8 and len(words) > 3:                # drop a word
        words.pop(rng.randrange(len(words)))
    else:                                              # character typo
        i = rng.randrange(len(words))
        w = words[i]
        if len(w) > 3:
            p = rng.randrange(len(w))
            words[i] = w[:p] + rng.choice("aeiouq") + w[p + 1:]
    return " ".join(words)


def build_corpus(n: int = 1200, seed: int = 20260726,
                 dup_rate: float = 0.12) -> List[Dict]:
    """Deterministic record set with a realistic sprinkling of near-duplicates."""
    rng = random.Random(seed)
    records: List[Dict] = []
    next_id = 1
    while len(records) < n:
        title = _title(rng)
        author = f"{rng.choice(_SURNAMES)} {rng.choice(_NAMES)}"
        org = rng.choice(_ORGS)
        spec = rng.choice(_SPECIALTIES)
        year = rng.randint(2015, 2024)
        day, month = rng.randint(1, 28), rng.randint(1, 12)
        base = {
            "id": next_id, "mavzu": title, "olim": author, "muassasa": org,
            "ixtisoslik": spec, "sana": f"{day:02d}.{month:02d}.{year}",
            "ilmiy_rahbar": f"{rng.choice(_SURNAMES)} {rng.choice(_NAMES)}",
            "link": f"https://vak.uz/diss/{next_id}" if rng.random() < 0.5 else "",
        }
        records.append(base)
        next_id += 1

        if rng.random() < dup_rate and len(records) < n:
            twin = dict(base)
            twin["id"] = next_id
            next_id += 1
            kind = rng.random()
            if kind < 0.15:                    # exact re-import, same link
                pass
            elif kind < 0.3:                   # same link, everything else drifts
                twin["mavzu"] = _title(rng)
                twin["olim"] = f"{rng.choice(_SURNAMES)} {rng.choice(_NAMES)}"
            else:
                twin["mavzu"] = _mutate(rng, title)
                twin["link"] = ""
                if kind > 0.7:
                    twin["sana"] = f"{min(day + 3, 28):02d}.{month:02d}.{year}"
                if kind > 0.9:
                    twin["muassasa"] = org + " (filiali)"
            records.append(twin)
    return records[:n]


# ── reference implementation ────────────────────────────────────────────────
def reference_detect(records, config, max_block_size: int,
                     title_prefix_len: int = D._TITLE_PREFIX_LEN
                     ) -> List[Tuple]:
    """The unfiltered algorithm: every pair inside every block goes through
    ``_classify`` (SequenceMatcher). Blocks over ``max_block_size`` are skipped,
    which is exactly what the code did before the hotfix."""
    sigs = [D._Sig(r) for r in records]
    blocks = D._build_blocks(sigs, title_prefix_len)
    seen = set()
    out = []
    for _key, idxs in blocks.items():
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
                cand = D._classify(a, b, config)
                if cand is not None:
                    seen.add(pair)
                    out.append(cand.ordered())
    out.sort(key=lambda c: (-c.score, c.record_id_1, c.record_id_2))
    return [_shape(c) for c in out]


def _shape(c) -> Tuple:
    return (c.record_id_1, c.record_id_2, c.score, c.match_level, tuple(c.reasons))


_HUGE = 10 ** 9
CORPUS = build_corpus(800)
CONFIG = DataQualityConfig()

# The reference pass is deliberately O(pairs) with three SequenceMatcher calls
# each, so memoise it — several tests compare against the same baseline.
_REF_CACHE: Dict[Tuple, List[Tuple]] = {}


def _reference(config: DataQualityConfig, max_block_size: int) -> List[Tuple]:
    key = (config.possible_threshold, config.strong_threshold, max_block_size)
    if key not in _REF_CACHE:
        _REF_CACHE[key] = reference_detect(CORPUS, config, max_block_size)
    return _REF_CACHE[key]


class EquivalenceTests(unittest.TestCase):
    """The pre-filters must not change the result — only the runtime."""

    def test_identical_to_unfiltered_reference(self):
        expected = _reference(CONFIG, _HUGE)
        got = [_shape(c) for c in D.detect_duplicates(
            CORPUS, CONFIG, _HUGE, hard_block_cap=_HUGE)]
        self.assertEqual(got, expected)
        self.assertTrue(expected, "corpus must actually contain duplicates")

    def test_identical_without_rapidfuzz(self):
        """Same answer on a host where rapidfuzz is not installed."""
        expected = _reference(CONFIG, _HUGE)
        original = D.RAPIDFUZZ_AVAILABLE
        try:
            D.RAPIDFUZZ_AVAILABLE = False
            got = [_shape(c) for c in D.detect_duplicates(
                CORPUS, CONFIG, _HUGE, hard_block_cap=_HUGE)]
        finally:
            D.RAPIDFUZZ_AVAILABLE = original
        self.assertEqual(got, expected)

    def test_equivalence_at_strict_thresholds(self):
        strict = DataQualityConfig(possible_threshold=0.95, strong_threshold=0.97)
        expected = _reference(strict, _HUGE)
        got = [_shape(c) for c in D.detect_duplicates(
            CORPUS, strict, _HUGE, hard_block_cap=_HUGE)]
        self.assertEqual(got, expected)

    def test_equivalence_at_loose_thresholds(self):
        loose = DataQualityConfig(possible_threshold=0.55, strong_threshold=0.70)
        expected = _reference(loose, _HUGE)
        got = [_shape(c) for c in D.detect_duplicates(
            CORPUS, loose, _HUGE, hard_block_cap=_HUGE)]
        self.assertEqual(got, expected)

    def test_block_splitting_never_loses_candidates(self):
        """Splitting oversized blocks replaces the old silent skip, so the
        candidate set can only grow."""
        before = set(_reference(CONFIG, 400))                 # old behaviour
        after = {_shape(c) for c in D.detect_duplicates(CORPUS, CONFIG, 500)}
        missing = before - after
        self.assertEqual(missing, set(),
                         f"{len(missing)} candidate(s) lost by the new blocking")


class BoundTests(unittest.TestCase):
    """Every filter must be a true upper bound on the score _classify computes."""

    def test_cheap_bound_never_under_estimates(self):
        rng = random.Random(11)
        sigs = [D._Sig(r) for r in CORPUS]
        for _ in range(8000):
            a, b = rng.choice(sigs), rng.choice(sigs)
            bound = D._max_possible_title_sim(a, b)
            actual = D._title_similarity(a, b)
            self.assertGreaterEqual(
                bound + 1e-12, actual,
                f"bound {bound} < actual {actual} for {a.title!r} / {b.title!r}")

    @unittest.skipUnless(D.RAPIDFUZZ_AVAILABLE, "rapidfuzz not installed")
    def test_rapidfuzz_dominates_difflib_on_corpus_pairs(self):
        """Indel similarity >= difflib ratio — the premise of the pre-filter.

        Sampled from staging titles when TEST_DATABASE_URL is set, otherwise
        from the synthetic corpus.
        """
        from rapidfuzz import fuzz

        titles = [D._Sig(r).title for r in _sample_titles()]
        rng = random.Random(4242)
        violations = []
        for _ in range(2000):
            a, b = rng.choice(titles), rng.choice(titles)
            if not a or not b:
                continue
            difflib_ratio = SequenceMatcher(None, a, b).ratio()
            indel_ratio = fuzz.ratio(a, b) / 100.0
            if indel_ratio < difflib_ratio - 1e-9:
                violations.append((a, b, difflib_ratio, indel_ratio))
        self.assertEqual(violations[:3], [],
                         f"{len(violations)} pair(s) where rapidfuzz < difflib — "
                         "the pre-filter premise is broken, STOP and review")


def _sample_titles() -> List[Dict]:
    """Real staging rows when a staging DB is reachable, else the corpus."""
    import os
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return CORPUS
    try:  # pragma: no cover - only on the staging box
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, mavzu FROM dissertations "
                            "WHERE mavzu IS NOT NULL ORDER BY id LIMIT 4000")
                return [{"id": r[0], "mavzu": r[1]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return CORPUS


class SeenPairTests(unittest.TestCase):
    def test_pair_classified_exactly_once(self):
        """A pair sharing several block keys must reach _classify once — the
        bug this hotfix fixes recomputed non-candidates in every shared block."""
        recs = [
            {"id": 1, "mavzu": "Iqtisodiy tahlil metodlari va ularning modellari",
             "olim": "Karimov Karim", "muassasa": "TDIU", "ixtisoslik": "08.00.01",
             "sana": "10.03.2020", "ilmiy_rahbar": "Rahbarov R", "link": "http://x/1"},
            # shares: link, (year, specialty), author token, title prefix …
            # … but is not similar enough to become a candidate.
            {"id": 2, "mavzu": "Iqtisodiy siyosat huquqiy asoslarini isloh qilish",
             "olim": "Karimov Karim", "muassasa": "TDIU", "ixtisoslik": "08.00.01",
             "sana": "11.03.2020", "ilmiy_rahbar": "Rahbarov R", "link": ""},
        ]
        _cands, stats = D.detect_duplicates_with_stats(recs, CONFIG)
        self.assertEqual(stats.pairs_classified, 1,
                         "a non-candidate pair must not be re-scored per block")
        self.assertGreaterEqual(stats.pairs_repeated, 1,
                                "the pair should be recognised as already handled")

    def test_no_duplicate_rows_for_multi_block_pair(self):
        recs = [
            {"id": 1, "mavzu": "Sun'iy intellekt asoslari va uni tibbiyotda qoʻllash",
             "olim": "Aliyev Vali", "muassasa": "TATU", "ixtisoslik": "05.01.02",
             "sana": "10.03.2020", "ilmiy_rahbar": "Yusupov R", "link": "http://x/7"},
            {"id": 2, "mavzu": "Sun'iy intellekt asoslari va uni tibbiyotda qoʻllashi",
             "olim": "Aliyev Vali", "muassasa": "TATU", "ixtisoslik": "05.01.02",
             "sana": "12.03.2020", "ilmiy_rahbar": "Yusupov R", "link": "http://x/7"},
        ]
        cands = D.detect_duplicates(recs, CONFIG)
        self.assertEqual(len(cands), 1)


class DeterminismTests(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        first = [_shape(c) for c in D.detect_duplicates(CORPUS, CONFIG)]
        second = [_shape(c) for c in D.detect_duplicates(CORPUS, CONFIG)]
        self.assertEqual(first, second)

    def test_stats_are_stable(self):
        _c1, s1 = D.detect_duplicates_with_stats(CORPUS, CONFIG)
        _c2, s2 = D.detect_duplicates_with_stats(CORPUS, CONFIG)
        self.assertEqual(s1.pairs_seen, s2.pairs_seen)
        self.assertEqual(s1.candidates, s2.candidates)
        self.assertEqual(s1.blocks_planned, s2.blocks_planned)


class BlockingTests(unittest.TestCase):
    def _same_title_corpus(self, n=1400):
        """1400 records that all land in one 8-char-prefix block. The 16-char
        prefix does not separate them either, so the split ladder has to fall
        through to the author key."""
        return [{"id": i,
                 "mavzu": "Raqamli iqtisodiyot tarmoqlarini rivojlantirish "
                          f"muammolari {i % 7}",
                 "olim": f"{_SURNAMES[i % 10]} {_NAMES[i % 7]}",
                 "muassasa": "TDIU", "ixtisoslik": "08.00.01",
                 "sana": "10.03.2020", "ilmiy_rahbar": "", "link": ""}
                for i in range(1, n + 1)]

    def test_oversized_block_is_split_not_skipped(self):
        recs = self._same_title_corpus()
        _cands, stats = D.detect_duplicates_with_stats(recs, CONFIG,
                                                       max_block_size=200)
        self.assertGreater(stats.blocks_planned, 1,
                           "an oversized block should produce several work units")
        self.assertEqual(stats.blocks_skipped, [],
                         "nothing should be dropped at this size")
        for _label, size in stats.largest_blocks:
            self.assertLessEqual(size, 200, "work units must respect the cap")

    def test_work_units_are_bounded_or_reported(self):
        recs = self._same_title_corpus()
        _cands, stats = D.detect_duplicates_with_stats(recs, CONFIG,
                                                       max_block_size=200)
        reported = {label for label, _ in
                    [(D._key_label(k), n) for k, n in stats.blocks_oversized]}
        for label, size in stats.largest_blocks:
            if size > 200:
                self.assertIn(label, reported,
                              "an oversized work unit must be logged, not silent")

    def test_hard_cap_skips_are_recorded(self):
        recs = [{"id": i, "mavzu": "Bir xil mavzu nomi butunlay",
                 "olim": "Aliyev Vali", "muassasa": "TDIU",
                 "ixtisoslik": "08.00.01", "sana": "10.03.2020",
                 "ilmiy_rahbar": "", "link": ""} for i in range(1, 601)]
        _cands, stats = D.detect_duplicates_with_stats(
            recs, CONFIG, max_block_size=100, hard_block_cap=200)
        self.assertTrue(stats.blocks_skipped,
                        "unsplittable oversized blocks must be recorded")

    def test_largest_blocks_are_reported(self):
        _cands, stats = D.detect_duplicates_with_stats(CORPUS, CONFIG)
        self.assertTrue(stats.largest_blocks)
        self.assertLessEqual(len(stats.largest_blocks), 10)
        sizes = [n for _label, n in stats.largest_blocks]
        self.assertEqual(sizes, sorted(sizes, reverse=True))


class FilterEffectivenessTests(unittest.TestCase):
    """Not a correctness guard — a canary that the filters are actually doing
    work, so a future refactor that disables them is noticed."""

    def test_most_pairs_are_filtered_before_sequencematcher(self):
        _cands, stats = D.detect_duplicates_with_stats(CORPUS, CONFIG)
        filtered = stats.pairs_cheap_skipped + stats.pairs_rf_skipped
        self.assertGreater(filtered, 0)
        self.assertLess(stats.pairs_classified, stats.pairs_seen,
                        "pre-filters should remove a share of the pairs")


if __name__ == "__main__":
    unittest.main()
