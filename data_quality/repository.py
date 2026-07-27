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

from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

try:
    import psycopg2.extras as _extras
except Exception:  # pragma: no cover - import guard
    _extras = None

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
def update_record_field(conn, record_id: int, column: str, new_value) -> int:
    """UPDATE a single allowlisted column of one dissertation. Returns affected
    row count. Refuses any column not on the fixer allowlist."""
    if column not in _UPDATABLE_COLUMNS:
        raise ValueError(f"column {column!r} is not fixer-updatable")
    with conn.cursor() as cur:
        # column is validated against a fixed allowlist above, so this f-string
        # cannot carry untrusted input; the VALUE is always parameterized.
        cur.execute(
            f"UPDATE dissertations SET {column} = %s WHERE id = %s",
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
