"""Data-access layer for the Data Quality subsystem.

This is the ONLY module that writes SQL. Everything is parameterized (no string
interpolation of values), column names used in dynamic statements are validated
against a fixed allowlist, and the caller always supplies the connection so the
same code works against a staging DB in tests (never the production DB unless an
operator explicitly points DATABASE_URL there).

The repository never opens a connection by itself and never commits implicitly
except where documented — transaction control belongs to the caller (scanner /
fixer / request handler).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

try:
    import psycopg2.extras as _extras
except Exception:  # pragma: no cover - import guard
    _extras = None

from data_quality.merge_review import COMPLETENESS_FIELDS, MergePlan
from data_quality.models import DuplicateCandidate, Issue

# Columns read for scanning. Deliberately excludes anything PII-ish; the
# dissertations table has no user/Telegram data, but we still select explicitly.
SCAN_COLUMNS = (
    "id", "sana", "daraja", "olim", "mavzu", "ixtisoslik", "muassasa",
    "ilmiy_rahbar", "link", "data_quality",
)

# Columns the fixer may target (mirrors models.AUTO_FIX_SAFE_COLUMNS). Kept here
# too so a bad column name can never reach an UPDATE statement.
_UPDATABLE_COLUMNS = frozenset({"daraja", "link"})

# Tables the fixer / rollback may write. Same reasoning as _UPDATABLE_COLUMNS:
# data_quality_fix_log stores table_name as data, and that data must never be
# able to steer a statement at another table.
_FIXABLE_TABLES = frozenset({"dissertations"})

DUP_TABLE_TYPE = "dissertation"


# ── Reads over the source table ─────────────────────────────────────────────
def count_dissertations(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM dissertations")
        return int(cur.fetchone()[0])


def fetch_record(conn, record_id: int) -> Optional[Dict]:
    cols = ", ".join(SCAN_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM dissertations WHERE id = %s", (record_id,))
        row = cur.fetchone()
        return dict(zip(SCAN_COLUMNS, row)) if row else None


def iter_dissertations(conn, limit: Optional[int] = None,
                       record_id: Optional[int] = None,
                       batch_size: int = 1000) -> Iterator[Dict]:
    """Stream rows as dicts using a server-side (named) cursor so a full 28k+
    scan never loads the whole table into memory. Read-only."""
    cols = ", ".join(SCAN_COLUMNS)
    if record_id is not None:
        rec = fetch_record(conn, record_id)
        if rec:
            yield rec
        return

    sql = f"SELECT {cols} FROM dissertations ORDER BY id"
    params: Sequence = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)

    name = "dq_scan_cursor"
    cur = conn.cursor(name=name)
    cur.itersize = batch_size
    try:
        cur.execute(sql, params)
        for row in cur:
            yield dict(zip(SCAN_COLUMNS, row))
    finally:
        cur.close()


# ── Run / issue / duplicate persistence ─────────────────────────────────────
def create_run(conn, run_type: str, total_records: int, metadata: Optional[dict] = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO data_quality_runs
                   (run_type, status, total_records, metadata)
               VALUES (%s, 'running', %s, %s::jsonb)
               RETURNING id""",
            (run_type, total_records, _json(metadata or {})),
        )
        return int(cur.fetchone()[0])


def complete_run(conn, run_id: int, *, status: str, checked_records: int,
                 issue_count: int, duplicate_count: int,
                 error_message: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE data_quality_runs
                   SET status = %s,
                       completed_at = NOW(),
                       checked_records = %s,
                       issue_count = %s,
                       duplicate_count = %s,
                       error_message = %s
                 WHERE id = %s""",
            (status, checked_records, issue_count, duplicate_count,
             error_message, run_id),
        )


def insert_issues(conn, run_id: Optional[int], issues: Sequence[Issue]) -> int:
    if not issues:
        return 0
    rows = [
        (run_id, i.record_type, i.record_id, i.field_name, i.issue_code,
         i.severity, i.original_value, i.normalized_value, i.description)
        for i in issues
    ]
    with conn.cursor() as cur:
        _extras.execute_values(
            cur,
            """INSERT INTO data_quality_issues
                   (run_id, record_type, record_id, field_name, issue_code,
                    severity, original_value, normalized_value, description)
               VALUES %s""",
            rows,
        )
    return len(rows)


def insert_duplicates(conn, candidates: Sequence[DuplicateCandidate]) -> int:
    if not candidates:
        return 0
    rows = [
        (DUP_TABLE_TYPE, c.record_id_1, c.record_id_2, c.score, c.match_level,
         _json(c.reasons))
        for c in candidates
    ]
    with conn.cursor() as cur:
        # ON CONFLICT keeps re-scans idempotent: an already-reviewed pair is not
        # reset, but its score/level/reasons are refreshed while still pending.
        _extras.execute_values(
            cur,
            """INSERT INTO duplicate_candidates
                   (record_type, record_id_1, record_id_2, score, match_level, reasons)
               VALUES %s
               ON CONFLICT (record_type, record_id_1, record_id_2) DO UPDATE
                   SET score = EXCLUDED.score,
                       match_level = EXCLUDED.match_level,
                       reasons = EXCLUDED.reasons
                 WHERE duplicate_candidates.status = 'pending'""",
            rows,
            template="(%s, %s, %s, %s, %s, %s::jsonb)",
        )
    return len(rows)


# ── Fixer support (writes to the source table) ──────────────────────────────
def update_record_field(conn, record_id: int, column: str, new_value,
                        table_name: str = "dissertations") -> int:
    """UPDATE a single allowlisted column of one record. Returns affected row
    count. Refuses any table/column not on the fixer allowlist."""
    _check_target(table_name, column)
    with conn.cursor() as cur:
        # Both identifiers are validated against fixed allowlists above, so this
        # f-string cannot carry untrusted input; the VALUE is always parameterized.
        cur.execute(
            f"UPDATE {table_name} SET {column} = %s WHERE id = %s",
            (new_value, record_id),
        )
        return cur.rowcount


def iter_fixable_issues(conn, allowed_codes: Sequence[str],
                        issue_code: Optional[str] = None,
                        run_id: Optional[int] = None,
                        record_id: Optional[int] = None,
                        limit: Optional[int] = None) -> List[Dict]:
    """Return OPEN issues whose code is on the fixer allowlist, optionally
    narrowed by a single code / run / record. Read-only."""
    if not allowed_codes:
        return []
    clauses = ["status = 'open'", "issue_code = ANY(%s)"]
    params: List = [list(allowed_codes)]
    if issue_code:
        clauses.append("issue_code = %s")
        params.append(issue_code)
    if run_id is not None:
        clauses.append("run_id = %s")
        params.append(run_id)
    if record_id is not None:
        clauses.append("record_id = %s")
        params.append(record_id)
    sql = ("SELECT id, record_id, field_name, issue_code, original_value, "
           "normalized_value FROM data_quality_issues WHERE "
           + " AND ".join(clauses) + " ORDER BY id")
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        keys = ("id", "record_id", "field_name", "issue_code",
                "original_value", "normalized_value")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


def mark_issue_fixed(conn, issue_id: int, resolved_by: Optional[int] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE data_quality_issues
                   SET status = 'fixed', resolved_by = %s, resolved_at = NOW()
                 WHERE id = %s""",
            (resolved_by, issue_id),
        )


def record_issue_values(conn, issue_id: int, original_value, normalized_value) -> None:
    """Pin the exact before/after the fixer used onto the issue row (the scan
    values may be stale by apply time)."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE data_quality_issues
                   SET original_value = %s, normalized_value = %s
                 WHERE id = %s""",
            (_as_text(original_value), _as_text(normalized_value), issue_id),
        )


# ── Fix log (audit trail + rollback source, migration 005) ──────────────────
FIX_LOG_KEYS = ("id", "run_id", "issue_id", "record_id", "table_name",
                "column_name", "value_before", "value_after", "issue_code",
                "applied_at", "applied_by", "rolled_back_at")


def log_fix(conn, *, run_id: int, record_id: int, column_name: str,
            value_before, value_after, table_name: str = "dissertations",
            issue_code: Optional[str] = None, issue_id: Optional[int] = None,
            applied_by: str = "cli") -> int:
    """Record one field rewrite and return the new fix-log id.

    MUST be called inside the same transaction as the UPDATE it describes, and
    before it, so a failed fix can never leave a log row claiming otherwise.
    Values are stored as TEXT; None stays SQL NULL (never the string 'None').
    """
    _check_target(table_name, column_name)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO data_quality_fix_log
                   (run_id, issue_id, record_id, table_name, column_name,
                    value_before, value_after, issue_code, applied_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (run_id, issue_id, record_id, table_name, column_name,
             _as_text(value_before), _as_text(value_after), issue_code,
             (applied_by or "cli")[:128]),
        )
        return int(cur.fetchone()[0])


def fetch_fixes_by_run(conn, run_id: int, include_rolled_back: bool = False,
                       limit: Optional[int] = None) -> List[Dict]:
    """Fixes logged for one fix run, NEWEST FIRST.

    Descending id is deliberate: it is reverse application order, so replaying
    the list restores the original value even when several fixes touched the
    same field. Already-reverted rows are excluded unless asked for.
    """
    sql = ("SELECT " + ", ".join(FIX_LOG_KEYS)
           + " FROM data_quality_fix_log WHERE run_id = %s")
    params: List = [run_id]
    if not include_rolled_back:
        sql += " AND rolled_back_at IS NULL"
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(zip(FIX_LOG_KEYS, r)) for r in cur.fetchall()]


def mark_rolled_back(conn, fix_ids: Sequence[int]) -> int:
    """Stamp rolled_back_at on the given fixes. Returns the number of rows
    actually stamped — already-reverted rows are left untouched, so a double
    rollback can never claim to have reverted the same fix twice."""
    ids = [int(i) for i in fix_ids]
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE data_quality_fix_log
                   SET rolled_back_at = NOW()
                 WHERE id = ANY(%s) AND rolled_back_at IS NULL""",
            (ids,),
        )
        return cur.rowcount


def fetch_current_value(conn, record_id: int, column: str,
                        table_name: str = "dissertations"):
    """Return ``(found, value)`` for one field. ``found`` separates "record is
    gone" from "column is NULL" — the rollback CLI must treat those differently."""
    _check_target(table_name, column)
    with conn.cursor() as cur:
        # Both identifiers are allowlist-validated above; the id is parameterized.
        cur.execute(f"SELECT {column} FROM {table_name} WHERE id = %s", (record_id,))
        row = cur.fetchone()
        return (False, None) if row is None else (True, row[0])


def fix_log_exists(conn) -> bool:
    """True when migration 005 has been applied."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name = 'data_quality_fix_log'""")
        return int(cur.fetchone()[0]) == 1


def _check_target(table_name: str, column: str) -> None:
    if table_name not in _FIXABLE_TABLES:
        raise ValueError(f"table {table_name!r} is not fixer-writable")
    if column not in _UPDATABLE_COLUMNS:
        raise ValueError(f"column {column!r} is not fixer-updatable")


def _as_text(value) -> Optional[str]:
    return None if value is None else str(value)


# ── Dashboard reads / actions ───────────────────────────────────────────────
def latest_run(conn) -> Optional[Dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, run_type, status, started_at, completed_at,
                      total_records, checked_records, issue_count, duplicate_count
                 FROM data_quality_runs ORDER BY started_at DESC LIMIT 1""")
        row = cur.fetchone()
        if not row:
            return None
        keys = ("id", "run_type", "status", "started_at", "completed_at",
                "total_records", "checked_records", "issue_count", "duplicate_count")
        return dict(zip(keys, row))


def issue_breakdown(conn, run_id: Optional[int] = None) -> List[Dict]:
    where, params = ("WHERE run_id = %s", (run_id,)) if run_id else ("", ())
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT issue_code, severity, COUNT(*) AS cnt
                  FROM data_quality_issues {where}
                 GROUP BY issue_code, severity
                 ORDER BY cnt DESC""",
            params,
        )
        return [dict(zip(("issue_code", "severity", "count"), r)) for r in cur.fetchall()]


def severity_counts(conn, run_id: Optional[int] = None) -> Dict[str, int]:
    where, params = ("WHERE run_id = %s", (run_id,)) if run_id else ("", ())
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT severity, COUNT(*) FROM data_quality_issues {where} GROUP BY severity",
            params)
        return {sev: int(cnt) for sev, cnt in cur.fetchall()}


# Filters allowed on the issues listing, mapped to safe SQL fragments.
_ISSUE_FILTERS = {
    "issue_code": "i.issue_code = %s",
    "severity": "i.severity = %s",
    "status": "i.status = %s",
    "run_id": "i.run_id = %s",
    "record_id": "i.record_id = %s",
}


def list_issues(conn, filters: Mapping, limit: int, offset: int) -> List[Dict]:
    clauses, params = _build_issue_where(filters)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params = list(params) + [limit, offset]
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT i.id, i.run_id, i.record_type, i.record_id, i.field_name,
                       i.issue_code, i.severity, i.original_value,
                       i.normalized_value, i.description, i.status, i.created_at
                  FROM data_quality_issues i {where}
                 ORDER BY i.id DESC LIMIT %s OFFSET %s""",
            params,
        )
        keys = ("id", "run_id", "record_type", "record_id", "field_name",
                "issue_code", "severity", "original_value", "normalized_value",
                "description", "status", "created_at")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


def count_issues(conn, filters: Mapping) -> int:
    clauses, params = _build_issue_where(filters)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM data_quality_issues i {where}", list(params))
        return int(cur.fetchone()[0])


def iter_issues_for_export(conn, filters: Mapping, hard_cap: int) -> Iterator[Dict]:
    """Server-side cursor stream for CSV export (bounded by hard_cap)."""
    clauses, params = _build_issue_where(filters)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params = list(params) + [hard_cap]
    cur = conn.cursor(name="dq_export_cursor")
    cur.itersize = 1000
    try:
        cur.execute(
            f"""SELECT i.record_id, i.issue_code, i.severity, i.field_name,
                       i.description, i.original_value, i.normalized_value,
                       i.status, i.created_at
                  FROM data_quality_issues i {where}
                 ORDER BY i.id DESC LIMIT %s""",
            params,
        )
        keys = ("record_id", "issue_code", "severity", "field_name", "description",
                "original_value", "normalized_value", "status", "created_at")
        for row in cur:
            yield dict(zip(keys, row))
    finally:
        cur.close()


def _build_issue_where(filters: Mapping):
    clauses, params = [], []
    for key, frag in _ISSUE_FILTERS.items():
        val = filters.get(key)
        if val not in (None, "", "all"):
            clauses.append(frag)
            params.append(val)
    return clauses, params


def resolve_issue(conn, issue_id: int, resolved_by: Optional[int], status: str = "resolved") -> int:
    if status not in ("resolved", "ignored", "open"):
        raise ValueError("invalid issue status")
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE data_quality_issues
                   SET status = %s,
                       resolved_by = %s,
                       resolved_at = CASE WHEN %s = 'open' THEN NULL ELSE NOW() END
                 WHERE id = %s""",
            (status, resolved_by, status, issue_id),
        )
        return cur.rowcount


def list_duplicates(conn, status: Optional[str], limit: int, offset: int) -> List[Dict]:
    where, params = ("WHERE c.status = %s", [status]) if status not in (None, "", "all") else ("", [])
    params = params + [limit, offset]
    with conn.cursor() as cur:
        # LEFT JOIN both sides so the dashboard can render the two records
        # side-by-side without an N+1 per-candidate lookup.
        cur.execute(
            f"""SELECT c.id, c.record_id_1, c.record_id_2, c.score, c.match_level,
                       c.reasons, c.status, c.created_at,
                       d1.mavzu, d1.olim, d1.sana, d1.muassasa,
                       d2.mavzu, d2.olim, d2.sana, d2.muassasa
                  FROM duplicate_candidates c
                  LEFT JOIN dissertations d1 ON d1.id = c.record_id_1
                  LEFT JOIN dissertations d2 ON d2.id = c.record_id_2
                  {where}
                 ORDER BY c.score DESC, c.id ASC LIMIT %s OFFSET %s""",
            params,
        )
        keys = ("id", "record_id_1", "record_id_2", "score", "match_level",
                "reasons", "status", "created_at",
                "r1_mavzu", "r1_olim", "r1_sana", "r1_muassasa",
                "r2_mavzu", "r2_olim", "r2_sana", "r2_muassasa")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


# ── duplicate review queue ──────────────────────────────────────────────────
# Full field set for the side-by-side comparison. One LEFT JOIN per side keeps
# the whole page to a single query — no N+1 over 1 597 candidates.
_REVIEW_FIELDS = ("mavzu", "olim", "ilmiy_rahbar", "muassasa", "ixtisoslik",
                  "sana", "link")
# exact -> strong -> possible, then by score. Written as a CASE so the ordering
# lives with the query and cannot drift from the UI's stated default.
_LEVEL_ORDER_SQL = ("CASE c.match_level WHEN 'exact' THEN 0 "
                    "WHEN 'strong' THEN 1 WHEN 'possible' THEN 2 ELSE 3 END")


def _duplicate_filter_sql(filters: Optional[Dict]) -> Tuple[str, list]:
    """Build the WHERE clause for the review queue. Parameterized only."""
    filters = filters or {}
    clauses, params = [], []

    status = filters.get("status")
    if status and status != "all":
        if status not in ("pending", "confirmed", "rejected", "merged"):
            raise ValueError("invalid duplicate status filter")
        clauses.append("c.status = %s")
        params.append(status)

    level = filters.get("match_level")
    if level and level != "all":
        if level not in ("exact", "strong", "possible"):
            raise ValueError("invalid match_level filter")
        clauses.append("c.match_level = %s")
        params.append(level)

    # Bucket splits the productive pile from the noisy low-score tail. The
    # threshold is config.HIGH_YIELD_MIN_SCORE so the query and the UI label
    # cannot drift apart.
    bucket = filters.get("bucket")
    if bucket in ("high", "noisy"):
        from data_quality.config import HIGH_YIELD_MIN_SCORE
        if bucket == "high":
            clauses.append("(c.match_level IN ('exact', 'strong') OR c.score >= %s)")
        else:
            clauses.append("(c.match_level = 'possible' AND c.score < %s)")
        params.append(HIGH_YIELD_MIN_SCORE)
    elif bucket and bucket != "all":
        raise ValueError("invalid bucket filter")

    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def count_duplicates(conn, filters: Optional[Dict] = None) -> int:
    where, params = _duplicate_filter_sql(filters)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM duplicate_candidates c {where}", params)
        return cur.fetchone()[0]


def list_duplicates_for_review(conn, filters: Optional[Dict], limit: int,
                               offset: int) -> List[Dict]:
    """Candidates with both records' comparable fields, in review order."""
    where, params = _duplicate_filter_sql(filters)
    cols = ", ".join(f"d1.{f}" for f in _REVIEW_FIELDS)
    cols2 = ", ".join(f"d2.{f}" for f in _REVIEW_FIELDS)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT c.id, c.record_id_1, c.record_id_2, c.score, c.match_level,
                       c.reasons, c.status, c.created_at, c.reviewed_at,
                       {cols}, {cols2}
                  FROM duplicate_candidates c
                  LEFT JOIN dissertations d1 ON d1.id = c.record_id_1
                  LEFT JOIN dissertations d2 ON d2.id = c.record_id_2
                  {where}
                 ORDER BY {_LEVEL_ORDER_SQL}, c.score DESC, c.id ASC
                 LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        keys = (("id", "record_id_1", "record_id_2", "score", "match_level",
                 "reasons", "status", "created_at", "reviewed_at")
                + tuple(f"r1_{f}" for f in _REVIEW_FIELDS)
                + tuple(f"r2_{f}" for f in _REVIEW_FIELDS))
        return [dict(zip(keys, r)) for r in cur.fetchall()]


def duplicate_progress(conn) -> List[Dict]:
    """(match_level, status, count) for the progress panel — one grouped query."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT match_level, status, COUNT(*)
                 FROM duplicate_candidates
                GROUP BY match_level, status""")
        return [{"match_level": r[0], "status": r[1], "count": r[2]}
                for r in cur.fetchall()]


def review_duplicate(conn, dup_id: int, status: str, reviewed_by: Optional[int]) -> int:
    if status not in ("confirmed", "rejected", "pending"):
        raise ValueError("invalid duplicate status")
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE duplicate_candidates
                   SET status = %s, reviewed_by = %s, reviewed_at = NOW()
                 WHERE id = %s""",
            (status, reviewed_by, dup_id),
        )
        return cur.rowcount


def has_pg_trgm(conn) -> bool:
    """True only if the pg_trgm extension is ALREADY installed. We never create
    it (spec: no auto extension creation in production)."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        return cur.fetchone() is not None


def dq_tables_exist(conn) -> bool:
    """True when migration 004 has been applied (all three tables present)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM information_schema.tables
                WHERE table_name IN
                    ('data_quality_runs','data_quality_issues','duplicate_candidates')""")
        return int(cur.fetchone()[0]) == 3


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ── Duplicate merge execution ────────────────────────────────────────────────
# Hard foreign keys whose target is dissertations(id). Mirrors
# docs/data_quality_dissertation_fk_inventory.md §1. Kept as the single source
# of truth that merge_dissertations() repoints AND that
# assert_no_unknown_fk_references() checks the live schema against — if this
# tuple and the database ever disagree, the merge refuses to run rather than
# silently missing a reference.
KNOWN_FK_REFERENCES_TO_DISSERTATIONS: Tuple[Tuple[str, str], ...] = (
    ("user_bookmarks", "dissertation_id"),
    ("specialty_notifications_log", "dissertation_id"),
)

# Columns fetched for a merge: every field the winner might need filled, plus
# the merge-state columns migration 006 adds.
_MERGE_ROW_COLUMNS: Tuple[str, ...] = (
    ("id",) + COMPLETENESS_FIELDS + ("merged_into_id", "merged_at"))


def discover_fk_references_to_dissertations(conn) -> List[Tuple[str, str]]:
    """Live discovery of every foreign key targeting dissertations(id), across
    the whole schema, EXCLUDING dissertations' own self-referencing
    merged_into_id. This is the runtime half of the safety net described in
    docs/data_quality_dissertation_fk_inventory.md §5 — the static inventory
    is what a human reads; this is what protects the data if that inventory
    is ever out of date."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT tc.table_name, kcu.column_name
                 FROM information_schema.table_constraints tc
                 JOIN information_schema.key_column_usage kcu
                   ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                 JOIN information_schema.constraint_column_usage ccu
                   ON tc.constraint_name = ccu.constraint_name
                  AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND ccu.table_name = 'dissertations'
                  AND ccu.column_name = 'id'
                  AND tc.table_name <> 'dissertations'""")
        return [(r[0], r[1]) for r in cur.fetchall()]


def assert_no_unknown_fk_references(conn) -> None:
    """Raises if the live database has a foreign key into dissertations(id)
    that merge_dissertations() does not know how to repoint. Call once before
    a merge run, never mid-merge (it is a schema check, not a per-row check)."""
    found = set(discover_fk_references_to_dissertations(conn))
    known = set(KNOWN_FK_REFERENCES_TO_DISSERTATIONS)
    unknown = found - known
    if unknown:
        raise RuntimeError(
            "merge refused: found foreign key(s) into dissertations(id) that "
            f"this script does not know how to repoint: {sorted(unknown)}. "
            "Update KNOWN_FK_REFERENCES_TO_DISSERTATIONS and "
            "merge_dissertations() in data_quality/repository.py (and "
            "docs/data_quality_dissertation_fk_inventory.md) before merging "
            "anything else.")


def fetch_confirmed_candidates(conn, candidate_id: Optional[int] = None,
                               limit: Optional[int] = None) -> List[Dict]:
    """Merge-eligible candidates: status='confirmed', or one specific id
    regardless of status (so the CLI can print a clear per-candidate refusal
    reason instead of silently skipping it)."""
    keys = ("id", "record_id_1", "record_id_2", "score", "match_level", "status")
    cols = ", ".join(keys)
    params: List = []
    if candidate_id is not None:
        where = "WHERE id = %s"
        params.append(candidate_id)
    else:
        where = "WHERE status = 'confirmed'"
    sql = f"SELECT {cols} FROM duplicate_candidates {where} ORDER BY id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(zip(keys, r)) for r in cur.fetchall()]


def fetch_dissertation_rows(conn, ids: Sequence[int], *,
                            for_update: bool = False) -> Dict[int, Dict]:
    """The columns a merge needs for the given ids. `for_update` row-locks
    them (ascending id order — callers must pass ids already sorted, or rely on
    the ORDER BY here, to keep lock order consistent across concurrent merges
    and avoid deadlocks)."""
    ids = sorted(set(ids))
    if not ids:
        return {}
    cols = ", ".join(_MERGE_ROW_COLUMNS)
    lock = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {cols} FROM dissertations WHERE id = ANY(%s) "
            f"ORDER BY id{lock}", (ids,))
        return {row[0]: dict(zip(_MERGE_ROW_COLUMNS, row)) for row in cur.fetchall()}


def _log_fix(cur, run_id: int, record_id: int, table_name: str, column_name: str,
            value_before, value_after, applied_by: str) -> None:
    cur.execute(
        """INSERT INTO data_quality_fix_log
               (run_id, record_id, table_name, column_name,
                value_before, value_after, applied_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (run_id, record_id, table_name, column_name,
         None if value_before is None else str(value_before),
         None if value_after is None else str(value_after), applied_by))


@dataclass
class MergeResult:
    candidate_id: int
    winner_id: int
    loser_id: int
    fields_filled: int = 0
    bookmarks_repointed: int = 0
    bookmarks_dropped: int = 0
    subscriptions_repointed: int = 0
    duplicate_rows_repointed: int = 0
    duplicate_rows_dropped: int = 0


def merge_dissertations(conn, run_id: int, plan: MergePlan, applied_by: str,
                        reviewed_by: Optional[int] = None) -> MergeResult:
    """Executes one merge plan. Every write in here belongs to ONE transaction
    — this function never calls commit() or rollback(); the caller does,
    around the whole call, so all six steps below are atomic: either every one
    of them lands, or none does.

    Assumes the caller already validated the plan with
    data_quality.merge_review.can_merge() and holds a row lock on both records
    (fetch_dissertation_rows(..., for_update=True)) taken before this call, in
    the same transaction.
    """
    winner_id, loser_id = plan.winner_id, plan.loser_id
    result = MergeResult(candidate_id=plan.candidate_id, winner_id=winner_id,
                         loser_id=loser_id)

    with conn.cursor() as cur:
        # 1. Fill blank winner fields from the loser. One UPDATE + one fix_log
        #    row per field, so the log is a precise, replayable before/after.
        for fill in plan.fills:
            cur.execute(
                f"UPDATE dissertations SET {fill.field} = %s WHERE id = %s",
                (fill.new_value, winner_id))
            _log_fix(cur, run_id, winner_id, "dissertations", fill.field,
                     fill.old_value, fill.new_value, applied_by)
        result.fields_filled = len(plan.fills)

        # 2. user_bookmarks: UNIQUE(user_id, dissertation_id), ON DELETE CASCADE.
        #    A user may have bookmarked both records; repointing the loser's
        #    bookmark onto the winner would then collide with their existing
        #    winner bookmark. Drop the now-redundant loser-side row first (its
        #    personal note, if any, is lost — an accepted, minor side effect of
        #    two bookmarks resolving to one canonical record), then repoint
        #    what remains, one row at a time so each gets its own audit entry.
        cur.execute(
            """DELETE FROM user_bookmarks b1
                WHERE b1.dissertation_id = %s
                  AND EXISTS (SELECT 1 FROM user_bookmarks b2
                               WHERE b2.user_id = b1.user_id
                                 AND b2.dissertation_id = %s)
              RETURNING b1.id""",
            (loser_id, winner_id))
        result.bookmarks_dropped = len(cur.fetchall())

        cur.execute(
            """UPDATE user_bookmarks SET dissertation_id = %s
                WHERE dissertation_id = %s
              RETURNING id""",
            (winner_id, loser_id))
        bookmark_ids = [r[0] for r in cur.fetchall()]
        result.bookmarks_repointed = len(bookmark_ids)
        for bookmark_id in bookmark_ids:
            _log_fix(cur, run_id, bookmark_id, "user_bookmarks", "dissertation_id",
                     loser_id, winner_id, applied_by)

        # 3. specialty_notifications_log: no uniqueness, plain repoint.
        cur.execute(
            """UPDATE specialty_notifications_log SET dissertation_id = %s
                WHERE dissertation_id = %s
              RETURNING id""",
            (winner_id, loser_id))
        log_ids = [r[0] for r in cur.fetchall()]
        result.subscriptions_repointed = len(log_ids)
        for log_id in log_ids:
            _log_fix(cur, run_id, log_id, "specialty_notifications_log",
                     "dissertation_id", loser_id, winner_id, applied_by)

        # 4. duplicate_candidates: soft reference, UNIQUE(record_type,
        #    record_id_1, record_id_2), canonical id1 < id2. Any OTHER row
        #    pairing the loser with a third record is repointed to the winner;
        #    a row that already represents exactly (winner, loser) — i.e. this
        #    candidate itself, or a stray duplicate of it — is retired here
        #    too. A collision with an existing (winner, third) row means that
        #    pairing is already recorded, so the now-redundant row is dropped
        #    rather than erroring.
        cur.execute(
            """SELECT id, record_id_1, record_id_2 FROM duplicate_candidates
                WHERE record_type = %s AND (record_id_1 = %s OR record_id_2 = %s)
                  AND id <> %s""",
            (DUP_TABLE_TYPE, loser_id, loser_id, plan.candidate_id))
        for row_id, id1, id2 in cur.fetchall():
            third = id2 if id1 == loser_id else id1
            if third == winner_id:
                cur.execute(
                    "UPDATE duplicate_candidates SET status = 'merged', "
                    "reviewed_at = NOW() WHERE id = %s", (row_id,))
                result.duplicate_rows_repointed += 1
                continue
            new1, new2 = (winner_id, third) if winner_id < third else (third, winner_id)
            cur.execute(
                """SELECT 1 FROM duplicate_candidates
                    WHERE record_type = %s AND record_id_1 = %s AND record_id_2 = %s
                      AND id <> %s""",
                (DUP_TABLE_TYPE, new1, new2, row_id))
            if cur.fetchone():
                cur.execute("DELETE FROM duplicate_candidates WHERE id = %s", (row_id,))
                result.duplicate_rows_dropped += 1
            else:
                _log_fix(cur, run_id, row_id, "duplicate_candidates",
                         "record_id_1/record_id_2", f"{id1},{id2}",
                         f"{new1},{new2}", applied_by)
                cur.execute(
                    "UPDATE duplicate_candidates SET record_id_1 = %s, "
                    "record_id_2 = %s WHERE id = %s", (new1, new2, row_id))
                result.duplicate_rows_repointed += 1

        # 5. Retire the loser. NEVER deleted — merged_into_id is how a reader
        #    (or a future rollback) finds out this row was superseded.
        _log_fix(cur, run_id, loser_id, "dissertations", "merged_into_id",
                 None, winner_id, applied_by)
        cur.execute(
            "UPDATE dissertations SET merged_into_id = %s, merged_at = NOW() "
            "WHERE id = %s", (winner_id, loser_id))

        # 6. Close out the reviewed candidate itself.
        cur.execute(
            """UPDATE duplicate_candidates
                   SET status = 'merged', reviewed_by = %s, reviewed_at = NOW()
                 WHERE id = %s""",
            (reviewed_by, plan.candidate_id))

    return result
