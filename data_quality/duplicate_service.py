"""DuplicateDetectionService — thin orchestration around the pure detector.

Detection itself (blocking + scoring) lives in ``duplicate_detector`` and needs
no database. This service adds persistence and the optional pg_trgm awareness
(used only when the extension already exists; never created here).
"""

from __future__ import annotations

import logging
from typing import List, Mapping, Optional

from data_quality import duplicate_detector as detector
from data_quality import repository as repo
from data_quality.config import DataQualityConfig, get_config
from data_quality.models import DuplicateCandidate

log = logging.getLogger("data_quality.duplicates")


class DuplicateDetectionService:
    def __init__(self, conn=None, config: Optional[DataQualityConfig] = None):
        self.conn = conn
        self.config = config or get_config()
        self.last_stats = None

    def detect(self, records: List[Mapping]) -> List[DuplicateCandidate]:
        """Return candidate pairs for an in-memory record set. Pure/deterministic.

        The detector logs its own progress every few seconds and leaves the pass
        counters on ``last_stats`` (blocks, pairs compared, filter hit rates) for
        whoever wants to report them.
        """
        log.info("duplicate detection start: %s records", len(records))
        candidates, stats = detector.detect_duplicates_with_stats(
            records, self.config)
        self.last_stats = stats
        log.info("duplicate detection: %s records -> %s candidates in %.1fs",
                 len(records), len(candidates), stats.seconds)
        return candidates

    def persist(self, candidates: List[DuplicateCandidate]) -> int:
        if self.conn is None:
            raise RuntimeError("persist() requires a database connection")
        return repo.insert_duplicates(self.conn, candidates)

    def uses_pg_trgm(self) -> bool:
        """Whether an accelerated pg_trgm path is available (informational).
        Absence just means the Python similarity fallback is used."""
        if not self.config.use_pg_trgm_if_present or self.conn is None:
            return False
        try:
            return repo.has_pg_trgm(self.conn)
        except Exception:  # pragma: no cover - never fatal
            return False
