"""Service layer — orchestrates a data-quality scan.

DataQualityService  : runs a scan (report / persist / normalize-preview) and
                      returns a RunResult. Batches issue inserts, uses one
                      transaction boundary for a persisted run, and turns any
                      failure into a 'failed' run status instead of a crash.
QualityReportService: builds the aggregate snapshot the admin dashboard shows,
                      reusing the SAME scoring function as the CLI.

No business logic lives in the routes/scripts — they call these services.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from data_quality import repository as repo
from data_quality import scoring
from data_quality.config import DataQualityConfig, get_config
from data_quality.duplicate_service import DuplicateDetectionService
from data_quality.models import Issue, RunResult, ScanSummary
from data_quality.validators import validate_record

log = logging.getLogger("data_quality.scan")

MODE_REPORT = "report"
MODE_PERSIST = "persist"
MODE_PREVIEW = "normalize-preview"


class DataQualityService:
    def __init__(self, conn, config: Optional[DataQualityConfig] = None):
        self.conn = conn
        self.config = config or get_config()

    # -- public API ----------------------------------------------------------
    def scan(self, *, mode: str = MODE_REPORT, limit: Optional[int] = None,
             record_id: Optional[int] = None, duplicates_only: bool = False,
             sample_cap: int = 50,
             progress: Optional[Callable[[int], None]] = None) -> RunResult:
        if mode not in (MODE_REPORT, MODE_PERSIST, MODE_PREVIEW):
            raise ValueError(f"unknown scan mode: {mode}")

        persist = mode == MODE_PERSIST
        if persist and not repo.dq_tables_exist(self.conn):
            raise RuntimeError(
                "Data Quality tables are missing — apply migration 004 before "
                "running a persist scan.")

        total = repo.count_dissertations(self.conn)
        summary = ScanSummary(total_records=total)
        run_id: Optional[int] = None

        log.info("scan start mode=%s total=%s limit=%s duplicates_only=%s",
                 mode, total, limit, duplicates_only)

        if persist:
            run_id = repo.create_run(
                self.conn, "scan", total,
                metadata={"mode": mode, "limit": limit,
                          "duplicates_only": duplicates_only})

        try:
            records = self._run_checks(
                summary, mode, limit, record_id, duplicates_only,
                run_id, sample_cap, progress)

            # Duplicate detection over the (in-memory) scanned records.
            dup_service = DuplicateDetectionService(self.conn, self.config)
            candidates = dup_service.detect(records)
            summary.duplicate_count = len(candidates)
            if persist and candidates:
                dup_service.persist(candidates)

            summary.quality_score = scoring.compute_score(
                summary, total, self.config)["overall"]

            if persist:
                repo.complete_run(
                    self.conn, run_id, status="completed",
                    checked_records=summary.checked_records,
                    issue_count=summary.issue_count,
                    duplicate_count=summary.duplicate_count)
                self.conn.commit()

            log.info("scan done mode=%s checked=%s issues=%s duplicates=%s score=%s",
                     mode, summary.checked_records, summary.issue_count,
                     summary.duplicate_count, summary.quality_score)
            return RunResult(run_id=run_id, run_type="scan", status="completed",
                             summary=summary, metadata={"mode": mode})
        except Exception as exc:  # noqa: BLE001 - deliberately broad; recorded below
            log.error("scan failed mode=%s: %s", mode, exc.__class__.__name__)
            if persist and run_id is not None:
                try:
                    self.conn.rollback()
                    repo.complete_run(
                        self.conn, run_id, status="failed",
                        checked_records=summary.checked_records,
                        issue_count=summary.issue_count,
                        duplicate_count=summary.duplicate_count,
                        error_message=str(exc)[:500])
                    self.conn.commit()
                except Exception:  # pragma: no cover
                    self.conn.rollback()
            return RunResult(run_id=run_id, run_type="scan", status="failed",
                             summary=summary, metadata={"mode": mode},
                             error_message=str(exc))

    # -- internals -----------------------------------------------------------
    def _run_checks(self, summary, mode, limit, record_id, duplicates_only,
                    run_id, sample_cap, progress) -> List[Dict]:
        records: List[Dict] = []
        buffer: List[Issue] = []
        persist = mode == MODE_PERSIST
        batch = max(1, self.config.batch_size)

        for rec in repo.iter_dissertations(
                self.conn, limit=limit, record_id=record_id, batch_size=batch):
            records.append(rec)
            summary.checked_records += 1

            if not duplicates_only:
                for issue in validate_record(rec):
                    if mode == MODE_PREVIEW and not issue.issue_code.startswith("INCONSISTENT"):
                        # preview only cares about proposed normalizations
                        continue
                    summary.add_issue(issue, sample_cap=sample_cap)
                    if persist:
                        buffer.append(issue)
                        if len(buffer) >= batch:
                            repo.insert_issues(self.conn, run_id, buffer)
                            buffer.clear()

            if progress and summary.checked_records % batch == 0:
                progress(summary.checked_records)

        if persist and buffer:
            repo.insert_issues(self.conn, run_id, buffer)
        return records


class QualityReportService:
    """Read-side aggregation for the admin dashboard."""

    def __init__(self, conn, config: Optional[DataQualityConfig] = None):
        self.conn = conn
        self.config = config or get_config()

    def dashboard_summary(self, run_id: Optional[int] = None) -> Dict:
        run = repo.latest_run(self.conn)
        effective_run = run_id or (run["id"] if run else None)
        breakdown = repo.issue_breakdown(self.conn, effective_run)
        sev = repo.severity_counts(self.conn, effective_run)
        total = run["total_records"] if run else repo.count_dissertations(self.conn)

        # Rebuild a ScanSummary from persisted aggregates so the dashboard score
        # comes from the exact same scoring function the CLI uses.
        summary = ScanSummary(total_records=total,
                              checked_records=(run["checked_records"] if run else 0))
        for b in breakdown:
            summary.issues_by_code[b["issue_code"]] = (
                summary.issues_by_code.get(b["issue_code"], 0) + b["count"])
            summary.issue_count += b["count"]
        summary.issues_by_severity = dict(sev)
        summary.duplicate_count = run["duplicate_count"] if run else 0

        score = scoring.compute_score(summary, total, self.config)
        return {
            "run": run,
            "score": score,
            "breakdown": breakdown,
            "severity": sev,
            "total_records": total,
            "issue_count": summary.issue_count,
            "duplicate_count": summary.duplicate_count,
        }
