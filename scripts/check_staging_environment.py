#!/usr/bin/env python3
"""Validate staging configuration and PostgreSQL using SELECT-only checks."""

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import unquote, urlparse


REQUIRED_TABLES = frozenset({"users", "dissertations", "import_audit_log"})
_STAGING_MARKER = re.compile(r"(^|[._-])(staging|stage|stg)([._-]|$)", re.I)
_PRODUCTION_MARKER = re.compile(r"(^|[._-])(production|prod)([._-]|$)", re.I)
_KNOWN_PRODUCTION_HOSTS = frozenset(
    {"olimlar.uz", "www.olimlar.uz", "db.olimlar.uz", "postgres.olimlar.uz"}
)
_KNOWN_PRODUCTION_DATABASES = frozenset(
    {"olimlar", "olimlar_prod", "olimlar_production", "prod", "production"}
)


class StagingCheckError(RuntimeError):
    """A validation failure whose message is safe to display."""


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


def _print_presence(name: str, environ: dict[str, str]) -> None:
    state = "set" if _present(environ.get(name)) else "not-set"
    print(f"{name}={state}")


def _has_staging_marker(value: str) -> bool:
    return bool(_STAGING_MARKER.search(value or ""))


def validate_configuration(environ: dict[str, str]) -> str:
    """Return the staging DSN after validating it without logging its value."""
    app_env = (environ.get("APP_ENV") or "").strip().lower()
    database_url = (environ.get("DATABASE_URL") or "").strip()
    session_secret = (environ.get("SESSION_SECRET") or "").strip()

    if app_env != "production":
        raise StagingCheckError("APP_ENV must be set to production")
    if not database_url:
        raise StagingCheckError("DATABASE_URL is required")
    if not session_secret:
        raise StagingCheckError("SESSION_SECRET is required")
    if len(session_secret) < 32:
        raise StagingCheckError("SESSION_SECRET must contain at least 32 characters")

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise StagingCheckError("DATABASE_URL must use PostgreSQL")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    database_name = unquote((parsed.path or "").lstrip("/")).split("/", 1)[0]
    database_name_lower = database_name.lower()
    if not hostname or not database_name:
        raise StagingCheckError("DATABASE_URL must include a host and database name")

    if hostname in _KNOWN_PRODUCTION_HOSTS:
        raise StagingCheckError("known production database host rejected")
    if hostname.startswith(("prod.", "production.")) or _PRODUCTION_MARKER.search(hostname):
        raise StagingCheckError("production-like database host rejected")
    if database_name_lower in _KNOWN_PRODUCTION_DATABASES:
        raise StagingCheckError("production database name rejected")
    if _PRODUCTION_MARKER.search(database_name_lower):
        raise StagingCheckError("production-like database name rejected")

    if not (
        _has_staging_marker(hostname)
        or _has_staging_marker(database_name_lower)
    ):
        raise StagingCheckError(
            "database host or database name must contain an explicit staging marker"
        )
    return database_url


def _default_connect(database_url: str):
    try:
        import psycopg2
    except ImportError as exc:
        raise StagingCheckError("psycopg2 is required for the database check") from exc

    return psycopg2.connect(
        database_url,
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
    )


def check_database(database_url: str, connect_factory=None) -> None:
    """Run identity and required-table SELECTs in a read-only session."""
    parsed_database = unquote((urlparse(database_url).path or "").lstrip("/")).split(
        "/", 1
    )[0]
    connector = connect_factory or _default_connect
    try:
        conn = connector(database_url)
        try:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                identity = cur.fetchone()
                if not identity or identity[0] != parsed_database:
                    raise StagingCheckError("connected database identity did not match")

                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = ANY(%s)",
                    ("public", sorted(REQUIRED_TABLES)),
                )
                existing = {row[0] for row in cur.fetchall()}
                missing = REQUIRED_TABLES - existing
                if missing:
                    raise StagingCheckError("one or more required tables are missing")
        finally:
            conn.close()
    except StagingCheckError:
        raise
    except Exception as exc:
        # Database driver messages can contain host/user details. Never surface them.
        raise StagingCheckError("read-only database check failed") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate isolated Olimlar.uz staging configuration."
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="validate environment isolation without opening a DB connection",
    )
    return parser


def main(argv=None, environ=None, connect_factory=None) -> int:
    args = build_parser().parse_args(argv)
    safe_environ = dict(os.environ if environ is None else environ)
    for name in ("APP_ENV", "DATABASE_URL", "SESSION_SECRET"):
        _print_presence(name, safe_environ)

    try:
        database_url = validate_configuration(safe_environ)
        print("staging_configuration=PASS")
        if args.config_only:
            print("database_check=skipped")
            return 0
        check_database(database_url, connect_factory=connect_factory)
        print("database_identity=verified")
        print("required_tables=verified")
        print("database_check=PASS")
        return 0
    except StagingCheckError as exc:
        print(f"staging_check=FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
