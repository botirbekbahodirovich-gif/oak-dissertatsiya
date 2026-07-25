"""Shared helpers for the Data Quality CLI scripts (connection + safe output).

Kept tiny and side-effect-free on import so ``python scripts/...`` stays fast.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse


def resolve_connection(database_url: str | None = None):
    """Open a DB connection for a CLI run.

    Precedence: explicit --database-url > DATA_QUALITY_DATABASE_URL >
    TEST_DATABASE_URL > DATABASE_URL. We set the chosen URL into the environment
    and defer to ``data.get_connection`` (same code path the app uses, incl. the
    SSL/normalisation logic), matching scripts/flag_data_quality.py.
    """
    from dotenv import load_dotenv
    load_dotenv()

    chosen = (database_url
              or os.environ.get("DATA_QUALITY_DATABASE_URL")
              or os.environ.get("TEST_DATABASE_URL")
              or os.environ.get("DATABASE_URL", ""))
    if not chosen:
        raise SystemExit("No database URL. Set DATABASE_URL (or pass --database-url).")
    os.environ["DATABASE_URL"] = chosen

    from data import get_connection  # imported late so env is set first
    return get_connection(), _safe_target(chosen)


def _safe_target(url: str) -> str:
    """Host/db label for logging — NEVER the credentials."""
    try:
        p = urlparse(url)
        return f"{p.hostname or '?'}/{(p.path or '').lstrip('/') or '?'}"
    except Exception:
        return "?"


def truncate(value, length: int = 60) -> str:
    """Trim a value for terminal display (never dump long/raw content)."""
    if value is None:
        return ""
    s = str(value).replace("\n", " ").replace("\r", " ")
    return s if len(s) <= length else s[: length - 1] + "…"
