"""Canonical institution IDs and conservative OAK-name resolution."""
from __future__ import annotations

import re
import unicodedata

from institutions import transliterate

_APOSTROPHES = "`´‘’ʻʼʹ"
_ORG_WORD = re.compile(
    r"(?:universitet(?:i|ining)?|university|institut(?:i|ining)?|institute|"
    r"akademiya(?:si|sining)?|academy|markaz(?:i|ining)?|center|centre|"
    r"filial(?:i|ining)?)", re.IGNORECASE)
_SEPARATOR = re.compile(r"\s+(?:va|hamda)\s+|\s*[;,]\s*", re.IGNORECASE)


def normalize_institution_identity(value: str | None) -> str:
    """Conservative exact key: keep every word and never split a name."""
    text = unicodedata.normalize("NFKC", value or "").strip()
    for char in _APOSTROPHES:
        text = text.replace(char, "'")
    text = transliterate(text).casefold()
    text = re.sub(r"[^0-9a-z'&]+", " ", text).replace("'", "")
    return " ".join(text.split())


def looks_like_multiple_institutions(value: str | None) -> bool:
    """Detect clear A-university + B-institute values without splitting official names."""
    latin = transliterate(unicodedata.normalize("NFKC", value or "")).casefold()
    parts = [part.strip() for part in _SEPARATOR.split(latin) if part.strip()]
    return sum(1 for part in parts if _ORG_WORD.search(part)) >= 2


_HAMDA = re.compile(r"\bhamda\b")
_CHILD_MARKER = re.compile(r"\bhuzuridagi\b")
# "... Fanlar akademiyasi <X> instituti/markazi" (also the "OzR FA" short form)
_ACADEMY_CHILD = re.compile(
    r"^(.*?\b(?:fanlar akademiyasi(?:ning)?|ozr fa))\s+(.*\b(?:institut(?:i|ining)?|markaz(?:i|ining)?)\b.*)$")
_SPLIT_KEEP = re.compile(r"(\s+(?:va|hamda)\s+|\s*[;,]\s*)", re.IGNORECASE)


def _org_groups(latin: str) -> list[str]:
    """Split on va/hamda/,/; but glue fragments without an org word onto the
    next part, so "Iqtisodiyot va pedagogika universiteti" stays one name."""
    pieces = _SPLIT_KEEP.split(latin)
    groups, buffer = [], ""
    for i, piece in enumerate(pieces):
        if i % 2 and not buffer:
            continue  # separator right after a finished name
        buffer += piece
        # a full name ENDS with its org word: "… universiteti Biofizika" is
        # the start of "… universiteti Biofizika va biokimyo instituti"
        words = re.findall(r"[0-9a-z'&]+", piece)
        if i % 2 == 0 and words and _ORG_WORD.fullmatch(words[-1]):
            groups.append(buffer)
            buffer = ""
    if buffer.strip() and groups:
        groups[-1] += buffer
    keys = [normalize_institution_identity(g) for g in groups]
    return [k for k in keys if k]


def classify_institution_name(value: str | None) -> dict:
    """Name rule on the normalized Latin text.

    kind='multiple' — two independent organisations ("hamda", or two full
                      names joined by "va"/","); parts = their keys, first is primary.
    kind='child'    — one organisation under a parent ("huzuridagi",
                      "Fanlar akademiyasi ... instituti"); parent = parent key.
    kind='single'   — everything else.
    """
    key = normalize_institution_identity(value)
    out = {'kind': 'single', 'key': key, 'parent': None, 'parts': [key] if key else []}
    if not key:
        return out
    if _HAMDA.search(key):
        parts = [p.strip() for p in _HAMDA.split(key) if p.strip()]
        if len(parts) >= 2 and all(_ORG_WORD.search(p) for p in parts):
            return dict(out, kind='multiple', parts=parts)
    marker = _CHILD_MARKER.search(key)
    if marker:
        return dict(out, kind='child', parent=key[:marker.start()].strip() or None)
    academy = _ACADEMY_CHILD.match(key)
    if academy:
        return dict(out, kind='child', parent=academy.group(1).strip())
    latin = transliterate(unicodedata.normalize("NFKC", value or "")).casefold()
    groups = _org_groups(latin)
    if len(groups) >= 2:
        return dict(out, kind='multiple', parts=groups)
    return out


def ensure_identity_schema(cur) -> None:
    """Create the additive identity schema; raw dissertations.muassasa stays intact."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS institutions (
            id BIGSERIAL PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            latin_name TEXT,
            category VARCHAR(50) NOT NULL DEFAULT 'boshqa',
            region VARCHAR(100),
            parent_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
            merged_into_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS institution_aliases (
            id BIGSERIAL PRIMARY KEY,
            institution_id BIGINT NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
            raw_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            alias_type VARCHAR(30) NOT NULL DEFAULT 'variant',
            source VARCHAR(50) NOT NULL DEFAULT 'admin',
            review_status VARCHAR(20) NOT NULL DEFAULT 'approved'
                CHECK (review_status IN ('approved', 'rejected')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (institution_id, raw_name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS institution_resolution_queue (
            id BIGSERIAL PRIMARY KEY,
            raw_name TEXT NOT NULL UNIQUE,
            normalized_name TEXT NOT NULL,
            reason VARCHAR(30) NOT NULL
                CHECK (reason IN ('unresolved', 'multiple', 'legacy_review')),
            source VARCHAR(50) NOT NULL,
            suggested_institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved', 'ignored')),
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            resolved_by TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS institution_identity_events (
            id BIGSERIAL PRIMARY KEY,
            action VARCHAR(30) NOT NULL,
            source_institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
            target_institution_id BIGINT REFERENCES institutions(id) ON DELETE SET NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            admin_username TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            undone_at TIMESTAMPTZ,
            undone_by TEXT
        )
    """)
    cur.execute("ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS institution_id BIGINT")
    cur.execute("""
        DO $$ BEGIN
            ALTER TABLE dissertations ADD CONSTRAINT dissertations_institution_id_fkey
                FOREIGN KEY (institution_id) REFERENCES institutions(id) ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dissertations_institution_id ON dissertations(institution_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_institution_queue_status ON institution_resolution_queue(status, last_seen_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_institution_aliases_institution ON institution_aliases(institution_id)")


def queue_resolution(cur, raw_name: str, normalized: str, reason: str, source: str,
                     suggested_institution_id: int | None = None) -> None:
    cur.execute("""
        INSERT INTO institution_resolution_queue
            (raw_name, normalized_name, reason, source, suggested_institution_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (raw_name) DO UPDATE SET
            normalized_name = EXCLUDED.normalized_name,
            reason = EXCLUDED.reason,
            source = EXCLUDED.source,
            suggested_institution_id = COALESCE(
                institution_resolution_queue.suggested_institution_id,
                EXCLUDED.suggested_institution_id),
            occurrence_count = institution_resolution_queue.occurrence_count + 1,
            last_seen_at = NOW(),
            status = CASE WHEN institution_resolution_queue.status = 'resolved'
                          THEN 'resolved' ELSE 'pending' END
    """, (raw_name, normalized, reason, source, suggested_institution_id))


def resolve_institution(cur, raw_name: str | None, *, source: str) -> int | None:
    """Resolve approved/exact identities; queue composites and unknown values."""
    raw = " ".join((raw_name or "").strip().split())
    if not raw:
        return None
    normalized = normalize_institution_identity(raw)
    if not normalized:
        return None
    # An explicit admin-approved alias always wins, including a reviewed
    # multi-organisation source string whose single ranking owner was selected.
    cur.execute("""
        SELECT a.institution_id FROM institution_aliases a
        JOIN institutions i ON i.id = a.institution_id
        WHERE a.normalized_name = %s AND a.review_status = 'approved'
          AND i.is_active = TRUE AND i.merged_into_id IS NULL LIMIT 1
    """, (normalized,))
    row = cur.fetchone()
    if row:
        return row[0]
    if looks_like_multiple_institutions(raw):
        queue_resolution(cur, raw, normalized, "multiple", source)
        return None
    cur.execute("""
        SELECT id FROM institutions
        WHERE normalized_name = %s AND is_active = TRUE AND merged_into_id IS NULL LIMIT 1
    """, (normalized,))
    row = cur.fetchone()
    if row:
        institution_id = row[0]
        cur.execute("""
            INSERT INTO institution_aliases
                (institution_id, raw_name, normalized_name, alias_type, source)
            VALUES (%s, %s, %s, 'exact', %s)
            ON CONFLICT (normalized_name) DO NOTHING
        """, (institution_id, raw, normalized, source))
        return institution_id
    queue_resolution(cur, raw, normalized, "unresolved", source)
    return None
