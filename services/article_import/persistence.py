# -*- coding: utf-8 -*-
"""DB qatlami — `article_import_batches` / `article_import_items` (Phase 2C.2).

Konvensiya (grants/conferences/saved/articles bilan bir xil, CLAUDE.md):
  * Sxema lazy + idempotent (`ensure_schema`) — migrations/010 bilan AYNAN
    bir xil DDL.
  * DB — xom psycopg2 kursor, ORM yo'q.
  * Har bir status o'tishi `services.article_import.state` orqali TEKSHIRILADI
    — bu yerda status qo'lda string bilan yozilmaydi.
  * `scientific_articles`ga yozish — `create_article_from_article_columns`
    orqali, `blueprints/articles.py:ARTICLE_WRITE_COLUMNS`dagi bilan BIR XIL
    ustun to'plamining kichik qismi (import candidate PDF/forma-o'ziga xos
    maydonlarni bilmaydi). Bu servis qatlami Flask'ga BOG'LIQ EMAS (§77) —
    shuning uchun blueprint modulidan import QILINMAYDI, faqat bir xil ustun
    nomlaridan foydalanadi.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import psycopg2

from services.article_import.state import (ALLOWED_TRANSITIONS, BATCH_DRAFT,
                                            REVIEW_BUCKET_STATUSES,
                                            derive_batch_status)

_schema_ready = False


# ── Sxema ────────────────────────────────────────────────────────────────────

def ensure_schema(cur):
    """migrations/010 bilan bir xil DDL, idempotent."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_import_batches (
            id                  SERIAL PRIMARY KEY,
            created_by          INTEGER      NOT NULL,
            source_type         VARCHAR(16)  NOT NULL DEFAULT 'doi_list',
            original_filename   TEXT,
            status              VARCHAR(24)  NOT NULL DEFAULT 'draft',
            total_items         INTEGER      NOT NULL DEFAULT 0,
            pending_count       INTEGER      NOT NULL DEFAULT 0,
            processing_count    INTEGER      NOT NULL DEFAULT 0,
            review_count        INTEGER      NOT NULL DEFAULT 0,
            approved_count      INTEGER      NOT NULL DEFAULT 0,
            rejected_count      INTEGER      NOT NULL DEFAULT 0,
            failed_count        INTEGER      NOT NULL DEFAULT 0,
            duplicate_count     INTEGER      NOT NULL DEFAULT 0,
            skipped_count       INTEGER      NOT NULL DEFAULT 0,
            cancel_requested    BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
            started_at          TIMESTAMP,
            completed_at        TIMESTAMP,
            updated_at          TIMESTAMP    NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_batches_created_by "
                "ON article_import_batches (created_by, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_batches_status "
                "ON article_import_batches (status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_batches_created_at "
                "ON article_import_batches (created_at DESC)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_import_items (
            id                     SERIAL PRIMARY KEY,
            batch_id               INTEGER      NOT NULL REFERENCES article_import_batches(id) ON DELETE CASCADE,
            row_number             INTEGER      NOT NULL,
            raw_input               TEXT         NOT NULL,
            normalized_doi          VARCHAR(255),
            status                  VARCHAR(24)  NOT NULL DEFAULT 'pending',
            duplicate_of_item_id    INTEGER      REFERENCES article_import_items(id) ON DELETE SET NULL,
            existing_article_id     INTEGER      REFERENCES scientific_articles(id) ON DELETE SET NULL,
            created_article_id      INTEGER      REFERENCES scientific_articles(id) ON DELETE SET NULL,
            metadata_candidate      JSONB,
            provider_status_summary JSONB,
            conflicts                JSONB,
            warnings                 JSONB,
            is_retracted             BOOLEAN      NOT NULL DEFAULT FALSE,
            oa_status                VARCHAR(24),
            candidate_pdf_url        TEXT,
            attempt_count            INTEGER      NOT NULL DEFAULT 0,
            last_error_code          VARCHAR(64),
            last_error_message       TEXT,
            created_at               TIMESTAMP    NOT NULL DEFAULT NOW(),
            started_at               TIMESTAMP,
            processed_at             TIMESTAMP,
            reviewed_at              TIMESTAMP,
            reviewed_by              INTEGER
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_batch "
                "ON article_import_items (batch_id, row_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_doi "
                "ON article_import_items (normalized_doi)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_status "
                "ON article_import_items (status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_created_at "
                "ON article_import_items (created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_existing_article "
                "ON article_import_items (existing_article_id) WHERE existing_article_id IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_created_article "
                "ON article_import_items (created_article_id) WHERE created_article_id IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_batch_status "
                "ON article_import_items (batch_id, status, row_number)")

    # migrations/014 bilan AYNAN bir xil — Phase 2D.1 discovery provenance.
    cur.execute("""
        ALTER TABLE article_import_batches
            ADD COLUMN IF NOT EXISTS source_provider      VARCHAR(24),
            ADD COLUMN IF NOT EXISTS source_query         JSONB,
            ADD COLUMN IF NOT EXISTS source_oak_field_id  INTEGER
                REFERENCES scientific_fields(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS source_result_count  INTEGER,
            ADD COLUMN IF NOT EXISTS excluded_no_doi_count INTEGER
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_batches_source_oak_field "
                "ON article_import_batches (source_oak_field_id) "
                "WHERE source_oak_field_id IS NOT NULL")
    cur.execute("ALTER TABLE article_import_items "
                "ADD COLUMN IF NOT EXISTS external_source_id VARCHAR(64)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_import_items_external_source "
                "ON article_import_items (external_source_id) "
                "WHERE external_source_id IS NOT NULL")
    # ACTIVE_DOI_ITEM_STATUSES — state.py yagona haqiqat manbai (parity test).
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_article_import_items_active_doi
            ON article_import_items (normalized_doi)
            WHERE normalized_doi IS NOT NULL AND status IN (
                'pending', 'processing', 'ready_for_review', 'partial_metadata',
                'retracted', 'provider_failed', 'failed', 'metadata_not_found'
            )
    """)

    # migrations/015 bilan AYNAN bir xil — Phase 2D.1.1 discovery enqueue
    # idempotency (bitta preview -> bitta batch, concurrent double-submit'da
    # ham). `source_request_id` — raw preview token EMAS, uning hash'i.
    cur.execute("ALTER TABLE article_import_batches "
                "ADD COLUMN IF NOT EXISTS source_request_id VARCHAR(64)")
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_article_import_batches_discovery_request
            ON article_import_batches (source_request_id)
            WHERE source_type = 'discovery' AND source_request_id IS NOT NULL
    """)
    _schema_ready = True


def _dict_rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _dict_row(cur):
    if cur.description is None:
        return None
    row = cur.fetchone()
    if row is None:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


# ── Batch CRUD ───────────────────────────────────────────────────────────────

def create_batch(cur, created_by, source_type, original_filename=None):
    cur.execute(
        """INSERT INTO article_import_batches
              (created_by, source_type, original_filename, status)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (created_by, source_type, original_filename, BATCH_DRAFT))
    return cur.fetchone()[0]


def get_or_create_discovery_batch(cur, created_by, *, source_provider, source_query,
                                  source_oak_field_id, source_result_count,
                                  excluded_no_doi_count, source_request_id=None):
    """Phase 2D.1.1 — `source_type='discovery'` partiyasi, provenance bilan
    (migrations/014) VA enqueue idempotency bilan (migrations/015).

    `source_request_id` berilgan bo'lsa: `ON CONFLICT ... DO NOTHING` —
    `uq_article_import_batches_discovery_request` bilan AYNAN bir xil
    predicate. Ikki (ketma-ket YOKI PARALLEL) so'rov bir xil request_id bilan
    kelsa, faqat BITTASI qator yozadi; ikkinchisi mavjud batch'ni o'qib
    qaytaradi — hech qanday bo'sh "orphan" ikkinchi batch YO'Q, 500 YO'Q.

    Qaytaradi: `(batch_id, created)` — `created=False` bo'lsa chaqiruvchi
    itemlarni QAYTA yozmasligi kerak (ular allaqachon bor).
    """
    cur.execute(
        """INSERT INTO article_import_batches
              (created_by, source_type, status, source_provider, source_query,
               source_oak_field_id, source_result_count, excluded_no_doi_count,
               source_request_id)
           VALUES (%s, 'discovery', %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (source_request_id) WHERE source_type = 'discovery'
               AND source_request_id IS NOT NULL
           DO NOTHING
           RETURNING id""",
        (created_by, BATCH_DRAFT, source_provider, _json(source_query),
         source_oak_field_id, source_result_count, excluded_no_doi_count,
         source_request_id))
    row = cur.fetchone()
    if row is not None:
        return row[0], True

    # DO NOTHING ishladi — request_id albatta berilgan va boshqa
    # (parallel/oldingi) so'rov ULGURGAN edi. Endi o'sha batch'ni o'qiymiz.
    existing_id = find_discovery_batch_by_request_id(cur, source_request_id)
    return existing_id, False


def find_discovery_batch_by_request_id(cur, source_request_id):
    if not source_request_id:
        return None
    cur.execute(
        """SELECT id FROM article_import_batches
           WHERE source_type = 'discovery' AND source_request_id = %s""",
        (source_request_id,))
    row = cur.fetchone()
    return row[0] if row else None


def insert_discovery_item(cur, batch_id, row_number, normalized_doi,
                          external_source_id):
    """Bitta discovery nomzodini `pending` sifatida yozadi.

    `ON CONFLICT ... DO NOTHING` — `uq_article_import_items_active_doi`
    (migrations/014) bilan AYNAN bir xil predicate. Bu YAKUNIY himoya (§16):
    ikki admin bir xil DOI'ni bir vaqtda navbatga qo'ysa, faqat BITTASI qator
    yozadi — ikkinchisi `None` oladi (chaqiruvchi buni "allaqachon navbatda"
    deb talqin qiladi, 500 EMAS).
    """
    cur.execute(
        """INSERT INTO article_import_items
              (batch_id, row_number, raw_input, normalized_doi, status,
               external_source_id)
           VALUES (%s, %s, %s, %s, 'pending', %s)
           ON CONFLICT (normalized_doi) WHERE normalized_doi IS NOT NULL AND status IN (
               'pending', 'processing', 'ready_for_review', 'partial_metadata',
               'retracted', 'provider_failed', 'failed', 'metadata_not_found'
           )
           DO NOTHING
           RETURNING id""",
        (batch_id, row_number, normalized_doi, normalized_doi, external_source_id))
    row = cur.fetchone()
    return row[0] if row else None


def get_batch(cur, batch_id):
    cur.execute("SELECT * FROM article_import_batches WHERE id = %s", (batch_id,))
    return _dict_row(cur)


def list_batches(cur, limit=25, offset=0):
    cur.execute("SELECT COUNT(*) FROM article_import_batches")
    total = cur.fetchone()[0]
    cur.execute(
        """SELECT * FROM article_import_batches
           ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s""",
        (limit, offset))
    return _dict_rows(cur), total


def mark_batch_started(cur, batch_id):
    cur.execute(
        """UPDATE article_import_batches SET started_at = COALESCE(started_at, NOW()),
              updated_at = NOW() WHERE id = %s""", (batch_id,))


def set_batch_cancel_requested(cur, batch_id, requested=True):
    cur.execute(
        """UPDATE article_import_batches SET cancel_requested = %s, updated_at = NOW()
           WHERE id = %s""", (requested, batch_id))


def recompute_batch_counts(cur, batch_id):
    """Hisoblagichlarni ELEMENTLAR holatidan qayta hisoblaydi (§7 — drift yo'q).

    Har bir item status o'tishidan keyin, SHU tranzaksiya ichida chaqiriladi.
    """
    cur.execute(
        "SELECT status, COUNT(*) FROM article_import_items WHERE batch_id = %s "
        "GROUP BY status", (batch_id,))
    counts = dict(cur.fetchall())
    total = sum(counts.values())
    review = sum(counts.get(s, 0) for s in REVIEW_BUCKET_STATUSES)

    cur.execute("SELECT started_at, cancel_requested FROM article_import_batches "
                "WHERE id = %s", (batch_id,))
    row = cur.fetchone()
    started, cancel_requested = (row if row else (None, False))
    status = derive_batch_status(counts, cancel_requested, started)

    from services.article_import.state import BATCH_TERMINAL_STATUSES
    completed_clause = ('completed_at = NOW()' if status in BATCH_TERMINAL_STATUSES
                        else 'completed_at = completed_at')

    cur.execute(
        """UPDATE article_import_batches SET
              total_items = %s, pending_count = %s, processing_count = %s,
              review_count = %s, approved_count = %s, rejected_count = %s,
              failed_count = %s, duplicate_count = %s, skipped_count = %s,
              status = %s, updated_at = NOW(), """ + completed_clause + """
           WHERE id = %s""",
        (total, counts.get('pending', 0), counts.get('processing', 0), review,
         counts.get('approved', 0), counts.get('rejected', 0),
         counts.get('failed', 0) + counts.get('provider_failed', 0),
         counts.get('duplicate_in_batch', 0) + counts.get('already_exists', 0),
         counts.get('skipped', 0), status, batch_id))
    return status, counts


# ── Item CRUD ────────────────────────────────────────────────────────────────

def insert_items(cur, batch_id, normalized_rows, in_batch_dup_map, existing_map):
    """Barcha elementlarni bitta partiyada yozadi va boshlang'ich statusni
    belgilaydi (§16, §17). `duplicate_of_item_id` ikkinchi bosqichda
    to'ldiriladi — chunki original qatorning DB id'si shu yerda hosil bo'ladi.
    """
    from services.article_import.state import (ALREADY_EXISTS,
                                                DUPLICATE_IN_BATCH,
                                                INVALID_DOI, PENDING)
    row_to_item_id = {}
    for row in normalized_rows:
        if not row.is_valid:
            status = INVALID_DOI
        elif row.row_number in in_batch_dup_map:
            status = DUPLICATE_IN_BATCH
        elif row.normalized_doi in existing_map:
            status = ALREADY_EXISTS
        else:
            status = PENDING
        existing = existing_map.get(row.normalized_doi) if row.normalized_doi else None
        cur.execute(
            """INSERT INTO article_import_items
                  (batch_id, row_number, raw_input, normalized_doi, status,
                   existing_article_id, last_error_message)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (batch_id, row.row_number, row.raw_input, row.normalized_doi, status,
             existing['id'] if existing else None,
             row.error_message if not row.is_valid else None))
        row_to_item_id[row.row_number] = cur.fetchone()[0]

    for dup_row, original_row in in_batch_dup_map.items():
        cur.execute(
            "UPDATE article_import_items SET duplicate_of_item_id = %s WHERE id = %s",
            (row_to_item_id[original_row], row_to_item_id[dup_row]))
    return row_to_item_id


def get_item(cur, item_id):
    cur.execute("SELECT * FROM article_import_items WHERE id = %s", (item_id,))
    return _dict_row(cur)


def list_items(cur, batch_id, status=None, limit=50, offset=0):
    where, params = ['batch_id = %s'], [batch_id]
    if status:
        where.append('status = %s')
        params.append(status)
    where_sql = ' AND '.join(where)
    cur.execute(f"SELECT COUNT(*) FROM article_import_items WHERE {where_sql}", params)
    total = cur.fetchone()[0]
    cur.execute(
        f"SELECT * FROM article_import_items WHERE {where_sql} "
        f"ORDER BY row_number LIMIT %s OFFSET %s", params + [limit, offset])
    return _dict_rows(cur), total


def fetch_pending_chunk(cur, batch_id, chunk_size):
    """Keyingi ishlanadigan elementlar — FOR UPDATE SKIP LOCKED (§47, §19).

    Ikki admin/worker bir vaqtda shu batchni ishlasa, bir-birining
    elementini QAYTA olmaydi (concurrency xavfsizligi).
    """
    cur.execute(
        """SELECT id FROM article_import_items
           WHERE batch_id = %s AND status = 'pending'
           ORDER BY row_number LIMIT %s FOR UPDATE SKIP LOCKED""",
        (batch_id, chunk_size))
    return [row[0] for row in cur.fetchall()]


def cas_item_status(cur, item_id, from_statuses, to_status, extra_set='', extra_params=()):
    """Compare-and-set — ikki admin bir vaqtda bosса ikkinchisi 0 qator
    yangilaydi (§46, §47 — idempotency/optimistic lock)."""
    assignments = 'status = %s'
    if extra_set:
        assignments += ', ' + extra_set
    cur.execute(
        f"""UPDATE article_import_items SET {assignments}
           WHERE id = %s AND status = ANY(%s) RETURNING id""",
        (to_status, *extra_params, item_id, list(from_statuses)))
    return cur.fetchone() is not None


def mark_item_processing(cur, item_id):
    cur.execute(
        """UPDATE article_import_items SET status = 'processing',
              started_at = NOW(), attempt_count = attempt_count + 1
           WHERE id = %s AND status = 'pending' RETURNING id""", (item_id,))
    return cur.fetchone() is not None


def record_item_result(cur, item_id, status, *, metadata_candidate=None,
                       provider_status_summary=None, conflicts=None, warnings=None,
                       is_retracted=False, oa_status=None, candidate_pdf_url=None,
                       last_error_code=None, last_error_message=None):
    """Metadata lookup natijasini yozadi va `processing`dan yakuniy statusga
    o'tkazadi. Status o'tishi CHAQIRUVCHIDA (`processor.py`) `state.py` orqali
    allaqachon tekshirilgan bo'ladi."""
    cur.execute(
        """UPDATE article_import_items SET
              status = %s, metadata_candidate = %s, provider_status_summary = %s,
              conflicts = %s, warnings = %s, is_retracted = %s, oa_status = %s,
              candidate_pdf_url = %s, last_error_code = %s, last_error_message = %s,
              processed_at = NOW()
           WHERE id = %s""",
        (status, _json(metadata_candidate), _json(provider_status_summary),
         _json(conflicts), _json(warnings), bool(is_retracted), oa_status,
         candidate_pdf_url, last_error_code,
         _bounded(last_error_message), item_id))


def reset_item_for_retry(cur, item_id, max_attempts):
    """`provider_failed`/`failed` -> `pending` — faqat urinishlar chegarasi
    ichida (§22)."""
    cur.execute(
        """UPDATE article_import_items SET status = 'pending', last_error_code = NULL
           WHERE id = %s AND status IN ('provider_failed', 'failed')
                 AND attempt_count < %s
           RETURNING id""", (item_id, max_attempts))
    return cur.fetchone() is not None


def set_item_reviewed(cur, item_id, status, actor_id, from_statuses,
                      created_article_id=None):
    ok = cas_item_status(
        cur, item_id, from_statuses, status,
        extra_set='reviewed_at = NOW(), reviewed_by = %s, created_article_id = COALESCE(%s, created_article_id)',
        extra_params=(actor_id, created_article_id))
    return ok


def set_item_already_exists(cur, item_id, existing_article_id, actor_id=None):
    cur.execute(
        """UPDATE article_import_items SET status = 'already_exists',
              existing_article_id = %s, processed_at = NOW()
           WHERE id = %s""", (existing_article_id, item_id))


# ── `scientific_articles`ga yozish (approve) ─────────────────────────────────

_LANGUAGE_KEYS = {'uz', 'ru', 'en', 'kaa', 'other'}


def _unique_slug(cur, base, exclude_id=None):
    """`blueprints/articles.py:_unique_slug` bilan BIR XIL naqsh (to'qnashuvda
    -2, -3…). Bu yerda mustaqil takrorlangan: servis qatlami Flask
    blueprint'iga bog'liq bo'lib qolmasligi kerak (§77) — mantiq shunchaki
    "slug band bo'lsa raqam qo'sh", DOI/provider logikasi emas."""
    slug, n = base, 2
    while True:
        cur.execute("SELECT 1 FROM scientific_articles WHERE slug = %s", (slug,))
        if not cur.fetchone():
            return slug
        slug = f'{base}-{n}'
        n += 1


def create_article_from_metadata(cur, metadata, created_by, primary_field_id=None,
                                 extra_field_ids=None):
    """Tasdiqlangan candidate'dan BITTA maqola yaratadi (§30–§34).

    Har doim: `verification_status='pending_review'`, `publication_status='draft'`.
    PDF, litsenziya, access_type — HECH QACHON bu yerdan avtomatik kelmaydi
    (§30, §40–§42): ular DB DEFAULT qiymatida qoladi (`unknown`/`metadata_only`).
    """
    from utils.article_rules import make_slug

    title = (metadata.get('title') or metadata.get('doi') or 'Nomsiz maqola')[:500]
    slug = _unique_slug(cur, make_slug(title))

    language = (metadata.get('language') or '').lower()
    if language not in _LANGUAGE_KEYS:
        language = 'other' if language else 'uz'

    keywords_list = [k.get('name') for k in (metadata.get('keywords') or [])
                     if k.get('name')]
    keywords = ', '.join(keywords_list)[:500] or None

    publication_date = _parse_date(metadata.get('publication_date'))
    citation_checked = _parse_datetime(metadata.get('citation_count_checked_at'))
    source_summary = metadata.get('source_summary') or None

    columns = ['title', 'slug', 'abstract', 'keywords', 'language',
               'publication_year', 'publication_date', 'journal_name', 'volume',
               'issue', 'pages', 'doi', 'external_url', 'openalex_id',
               'crossref_member_id', 'primary_issn', 'is_retracted',
               'citation_count', 'citation_count_source', 'citation_count_checked_at',
               'metadata_source_summary', 'verification_status', 'publication_status',
               'created_by', 'metadata_last_synced_at']
    values = [title[:500], slug, metadata.get('abstract'), keywords, language,
              metadata.get('publication_year'), publication_date,
              (metadata.get('journal_name') or None), metadata.get('volume'),
              metadata.get('issue'), metadata.get('pages'), metadata.get('doi'),
              metadata.get('external_url'), metadata.get('openalex_id'),
              metadata.get('crossref_member_id'), metadata.get('primary_issn'),
              bool(metadata.get('is_retracted')), metadata.get('citation_count'),
              metadata.get('citation_count_source'), citation_checked,
              _json(source_summary), 'pending_review', 'draft', created_by,
              datetime.now()]

    placeholders = ', '.join(['%s'] * len(columns))
    try:
        cur.execute(
            f"INSERT INTO scientific_articles ({', '.join(columns)}) "
            f"VALUES ({placeholders}) RETURNING id", values)
    except psycopg2.IntegrityError:
        # Ikki admin bir xil DOI'ni bir vaqtda approve qilishi mumkin (§33)
        # — Python pre-check yetarli emas, DB UNIQUE constraint YAKUNIY
        # himoya. Chaqiruvchi (service.py) buni ROLLBACK qilib, mavjud
        # maqolani topib, itemni `already_exists`ga o'tkazadi.
        raise
    article_id = cur.fetchone()[0]

    for order, author in enumerate((metadata.get('authors') or []), start=1):
        cur.execute(
            """INSERT INTO article_authors
                  (article_id, author_name, author_order, orcid, affiliation,
                   is_corresponding, openalex_author_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (article_id, (author.get('name') or '')[:300], order,
             (author.get('orcid') or None), (author.get('affiliation') or None),
             bool(author.get('is_corresponding')),
             author.get('openalex_author_id')))

    field_ids = list(dict.fromkeys(
        [primary_field_id] + list(extra_field_ids or []))) if primary_field_id else \
        list(dict.fromkeys(extra_field_ids or []))
    for field_id in field_ids:
        if not field_id:
            continue
        is_primary = (field_id == primary_field_id)
        cur.execute(
            """INSERT INTO article_scientific_fields (article_id, scientific_field_id, is_primary)
               VALUES (%s, %s, %s)
               ON CONFLICT (article_id, scientific_field_id) DO UPDATE SET is_primary = EXCLUDED.is_primary""",
            (article_id, field_id, is_primary))

    return article_id


def find_article_by_doi(cur, doi):
    cur.execute("SELECT id, title, slug FROM scientific_articles WHERE doi = %s", (doi,))
    row = cur.fetchone()
    if not row:
        return None
    return {'id': row[0], 'title': row[1], 'slug': row[2]}


# ── Yordamchilar ─────────────────────────────────────────────────────────────

def _json(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _bounded(text, limit=500):
    """Xato matni — cheklangan, sirlarsiz (§48)."""
    if not text:
        return None
    return str(text)[:limit]


def _parse_date(raw):
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _parse_datetime(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None
