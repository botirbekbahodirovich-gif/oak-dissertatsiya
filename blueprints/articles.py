# -*- coding: utf-8 -*-
"""Ilmiy maqolalar katalogi (articles_bp) — Phase 2B.1.

Bu modul `blueprints/maqolalar.py` (OpenAlex nashr TAHLILI, /maqolalar-tahlili)
dan butunlay boshqa narsa: bu yerda saytning O'Z maqolalar bazasi — admin qo'lda
joylashtiradigan, huquqi tekshirilgan ochiq PDF maqolalar katalogi.

Konvensiyalar (grants/conferences/saved bilan bir xil):
  * Sxema lazy + idempotent (_ensure_schema) — migrations/007_create_article_catalog.sql
    bilan AYNAN bir xil DDL; server birinchi so'rovda self-migrate qiladi.
  * DB — data.get_connection() (PostgreSQL, psycopg2; ORM yo'q).
  * users ga FK yo'q (users legacy SQLite'da — saved.py bilan bir xil sabab).
  * Admin avtorizatsiyasi — app._require_admin() (is_admin flagi).
  * Qidiruv — utils.search_helper.build_search_clause (kiril<->lotin + trigram,
    pg_trgm faqat MAVJUD bo'lsa; kengaytma hech qachon avtomatik yaratilmaydi).

ENG MUHIM QOIDA: PDF fayl mavjudligi uni ko'rsatishga huquq bermaydi.
Yagona haqiqat manbai — utils.article_rules.can_read_pdf(). Route, shablon va
admin preview aynan shu funksiyani chaqiradi; qoida hech qayerda nusxalanmaydi.

Routes (public):
  GET /maqolalar                    — katalog: qidiruv, filtr, sort, sahifalash
  GET /maqolalar/yonalishlar        — fan yo'nalishlari ierarxiyasi
  GET /maqolalar/yonalish/<slug>    — bitta yo'nalish + undagi maqolalar
  GET /maqolalar/<slug>             — maqola tafsiloti
  GET /maqolalar/<slug>/oqish       — ichki PDF.js o'quvchisi (Phase 2B.2)
  GET /maqolalar/<slug>/pdf         — o'quvchi uchun himoyalangan PDF oqimi
  GET /maqolalar/<slug>/fayl        — PDF yuklab olish (attachment)

Routes (admin):
  /admin/articles[/new|/<id>/edit|/<id>/review|/<id>/delete|/<id>/preview]
  /admin/scientific-fields[/new|/<id>/edit|/<id>/delete]
"""
import math
import os
from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_file)
from flask_login import current_user, login_required

from data import get_connection
from security import log_exception
from security_uploads import (MAX_PDF_BYTES, UploadValidationError,
                              resolve_private_pdf, sanitize_pdf_filename,
                              save_private_pdf, validate_pdf_upload)
from utils.article_rules import (ACCESS_TYPES, LANGUAGES, LICENSE_URLS, LICENSES,
                                 PDF_ALLOWED_ACCESS_TYPES, PUBLICATION_STATUSES,
                                 SOURCE_TYPES, VERIFICATION_STATUSES,
                                 VERSION_TYPES, ArticleValidationError,
                                 can_read_pdf, make_slug, pdf_block_reason,
                                 validate_article_rights, validate_choice,
                                 validate_doi)
from utils.scientific_fields import (FieldValidationError, build_tree,
                                     check_no_cycle, descendant_ids, field_slug,
                                     normalize_code, resolve_level)
from utils.search_helper import build_search_clause

articles_bp = Blueprint('articles', __name__)

PER_PAGE = 20
MAX_PER_PAGE = 60
ADMIN_PER_PAGE = 30
MAX_PAGE = 500                 # sahifalash chegarasi — cheksiz OFFSET skaniga yo'l qo'yilmaydi
MAX_AUTHORS = 40
MAX_EXTRA_FIELDS = 5

_schema_ready = False
_trgm_checked = None           # None — hali tekshirilmagan; bool — natija

# Sort tanlovlari: kalit -> (yorliq, ORDER BY). Kalit oq ro'yxatdan tashqarida
# bo'lsa default ishlatiladi — foydalanuvchi kiritgan matn SQL ga tushmaydi.
SORT_OPTIONS = {
    'yangi': ("Eng yangi", "a.publication_year DESC NULLS LAST, a.publication_date DESC NULLS LAST, a.id DESC"),
    'eski': ("Eng eski", "a.publication_year ASC NULLS LAST, a.publication_date ASC NULLS LAST, a.id ASC"),
    'nom': ("Nomi (A-Z)", "LOWER(a.title) ASC"),
    'korilgan': ("Ko'p ko'rilgan", "a.view_count DESC, a.id DESC"),
    'qoshilgan': ("Yaqinda qo'shilgan", "a.created_at DESC, a.id DESC"),
}
DEFAULT_SORT = 'yangi'

VIEW_MODES = ('karta', 'jadval')

# ── PDF o'quvchisi (Phase 2B.2) ─────────────────────────────────────────────
# Vendor qilingan PDF.js build'i — static/vendor/pdfjs/README.md da versiya,
# manba va checksumlar. Bu qiymat shablonga `?v=` cache-buster sifatida
# beriladi (nginx /static/ ni 7 kun keshlaydi) va asosiy skript bilan worker
# bitta versiyadan ekanini testda tekshirish uchun yagona manba bo'lib turadi.
PDFJS_VERSION = '5.7.284'

# O'quvchining o'z CSS/JS fayllari uchun cache-buster.
VIEWER_ASSET_VERSION = '20260807'

# PDF oqimi keshi. `private`: umumiy proksi keshlamaydi, chunki maqola
# `copyright_hold` ga o'tsa yoki litsenziya olib tashlansa can_read_pdf()
# darhol yopiladi — ulashilgan kesh esa eski nusxani berishda davom etardi.
# Brauzer keshi (1 soat) sahifa yangilanganda PDF qayta yuklanmasligi uchun
# yetarli; ETag/Last-Modified bilan revalidatsiya baribir ishlaydi.
PDF_STREAM_MAX_AGE = 3600


# ── Sxema ────────────────────────────────────────────────────────────────────

def _ensure_schema(cur):
    """migrations/007 bilan bir xil DDL, idempotent (grants konvensiyasi)."""
    global _schema_ready
    if _schema_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scientific_fields (
            id                SERIAL PRIMARY KEY,
            parent_id         INTEGER REFERENCES scientific_fields(id) ON DELETE RESTRICT,
            name_uz           TEXT         NOT NULL,
            name_uz_cyrillic  TEXT,
            name_ru           TEXT,
            code              VARCHAR(32),
            slug              VARCHAR(200) NOT NULL UNIQUE,
            level             SMALLINT     NOT NULL DEFAULT 1,
            description       TEXT,
            sort_order        INTEGER      NOT NULL DEFAULT 0,
            is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
            source_name       TEXT,
            source_url        TEXT,
            source_updated_at DATE,
            created_at        TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMP    NOT NULL DEFAULT NOW()
        )""")
    # Migratsiya 008 — manba (provenance) ustunlari. OAK dataseti import
    # qilinganda har bir yo'nalish qayerdan kelgani kuzatiladi.
    for column, ddl in (
            ('raw_name', 'TEXT'),
            ('source_line', 'INTEGER'),
            ('source_sha256', 'CHAR(64)'),
            ('review_status', 'VARCHAR(24)'),
            ('reviewed_by', 'INTEGER'),
            ('reviewed_at', 'TIMESTAMP'),
            ('missing_parent_code', 'VARCHAR(32)')):
        cur.execute(f"ALTER TABLE scientific_fields "
                    f"ADD COLUMN IF NOT EXISTS {column} {ddl}")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_fields_code "
                "ON scientific_fields (code) WHERE code IS NOT NULL AND code <> ''")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scientific_fields_review "
                "ON scientific_fields (review_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scientific_fields_missing_parent "
                "ON scientific_fields (missing_parent_code) "
                "WHERE missing_parent_code IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scientific_fields_code_sort "
                "ON scientific_fields (code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scientific_fields_parent "
                "ON scientific_fields (parent_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scientific_fields_level "
                "ON scientific_fields (level, sort_order)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scientific_fields_active "
                "ON scientific_fields (is_active) WHERE is_active")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scientific_articles (
            id                  SERIAL PRIMARY KEY,
            title               TEXT         NOT NULL,
            slug                VARCHAR(220) NOT NULL UNIQUE,
            abstract            TEXT,
            keywords            TEXT,
            language            VARCHAR(8)   NOT NULL DEFAULT 'uz',
            publication_year    INTEGER,
            publication_date    DATE,
            journal_name        TEXT,
            volume              VARCHAR(50),
            issue               VARCHAR(50),
            pages               VARCHAR(50),
            doi                 VARCHAR(255),
            external_url        TEXT,
            pdf_path            TEXT,
            pdf_original_name   TEXT,
            pdf_size            BIGINT,
            pdf_mime_type       VARCHAR(100),
            checksum_sha256     CHAR(64),
            license_type        VARCHAR(40)  NOT NULL DEFAULT 'unknown',
            license_url         TEXT,
            access_type         VARCHAR(40)  NOT NULL DEFAULT 'metadata_only',
            version_type        VARCHAR(40)  NOT NULL DEFAULT 'other',
            source_type         VARCHAR(40)  NOT NULL DEFAULT 'other_verified_source',
            source_name         TEXT,
            source_url          TEXT,
            copyright_note      TEXT,
            permission_note     TEXT,
            rights_confirmed_by INTEGER,
            rights_confirmed_at TIMESTAMP,
            index_scopus        BOOLEAN      NOT NULL DEFAULT FALSE,
            index_wos           BOOLEAN      NOT NULL DEFAULT FALSE,
            index_doaj          BOOLEAN      NOT NULL DEFAULT FALSE,
            index_other         TEXT,
            verification_status VARCHAR(24)  NOT NULL DEFAULT 'draft',
            publication_status  VARCHAR(24)  NOT NULL DEFAULT 'draft',
            is_featured         BOOLEAN      NOT NULL DEFAULT FALSE,
            view_count          INTEGER      NOT NULL DEFAULT 0,
            download_count      INTEGER      NOT NULL DEFAULT 0,
            created_by          INTEGER,
            reviewed_by         INTEGER,
            reviewed_at         TIMESTAMP,
            created_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMP    NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_scientific_articles_doi "
                "ON scientific_articles (doi) WHERE doi IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_checksum "
                "ON scientific_articles (checksum_sha256) WHERE checksum_sha256 IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_public ON scientific_articles "
                "(publication_status, verification_status, publication_year DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_year "
                "ON scientific_articles (publication_year DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_language "
                "ON scientific_articles (language)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_journal "
                "ON scientific_articles (journal_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_created "
                "ON scientific_articles (created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_views "
                "ON scientific_articles (view_count DESC)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_authors (
            id                  SERIAL PRIMARY KEY,
            article_id          INTEGER NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
            author_name         TEXT    NOT NULL,
            author_order        INTEGER NOT NULL DEFAULT 1,
            orcid               VARCHAR(64),
            affiliation         TEXT,
            linked_user_id      INTEGER,
            linked_scientist_id INTEGER,
            is_corresponding    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMP NOT NULL DEFAULT NOW()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_authors_article "
                "ON article_authors (article_id, author_order)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_authors_name "
                "ON article_authors (author_name)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS article_scientific_fields (
            id                  SERIAL PRIMARY KEY,
            article_id          INTEGER NOT NULL REFERENCES scientific_articles(id) ON DELETE CASCADE,
            scientific_field_id INTEGER NOT NULL REFERENCES scientific_fields(id) ON DELETE RESTRICT,
            is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (article_id, scientific_field_id)
        )""")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_article_primary_field "
                "ON article_scientific_fields (article_id) WHERE is_primary")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_article_fields_field "
                "ON article_scientific_fields (scientific_field_id)")
    _schema_ready = True


def _fuzzy_available(cur):
    """pg_trgm MAVJUD bo'lsagina similarity() ishlatiladi.

    Kengaytma hech qachon avtomatik yaratilmaydi (data_quality/config.py bilan
    bir xil siyosat) — indekssiz muhitda similarity() to'liq skan qilib
    katalogni sekinlashtiradi, shuning uchun yo'q bo'lsa ILIKE qatlami qoladi.
    """
    global _trgm_checked
    if _trgm_checked is None:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        _trgm_checked = cur.fetchone() is not None
    return _trgm_checked


def storage_dir():
    """PDF saqlash papkasi — static/ TASHQARISIDA.

    nginx `/static/` ni to'g'ridan-to'g'ri beradi (deploy/olimlar.uz.conf),
    shuning uchun PDF u yerda tursa can_read_pdf() qoidasini butunlay chetlab
    o'tish mumkin bo'lardi. Bu papka faqat Flask route orqali o'qiladi.
    """
    from flask import current_app
    configured = os.environ.get('ARTICLE_STORAGE_DIR')
    if configured:
        return configured
    return os.path.join(current_app.root_path, 'storage', 'articles')


# ── Umumiy yordamchilar ─────────────────────────────────────────────────────

def _dict_rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _int_arg(name, default=None, minimum=None, maximum=None):
    """Query parametrni butun songa aylantiradi. Noto'g'ri qiymat 500 bermaydi
    (§16) — default qaytadi."""
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def _choice_arg(name, allowed, default):
    value = (request.args.get(name) or '').strip()
    return value if value in allowed else default


def _flag_arg(name):
    return (request.args.get(name) or '').strip() in ('1', 'true', 'on', 'ha')


def _unique_slug(cur, table, base, exclude_id=None):
    """To'qnashuvda -2, -3… (mavjud conferences.make_slug naqshi)."""
    slug, n = base, 2
    while True:
        if exclude_id:
            cur.execute(f"SELECT 1 FROM {table} WHERE slug = %s AND id <> %s",
                        (slug, exclude_id))
        else:
            cur.execute(f"SELECT 1 FROM {table} WHERE slug = %s", (slug,))
        if not cur.fetchone():
            return slug
        slug = f'{base}-{n}'
        n += 1


def _query_string(**overrides):
    """Joriy filtrlarni saqlagan holda yangi query string (sahifalash/sort uchun)."""
    params = {k: v for k, v in request.args.items(multi=False) if v not in (None, '')}
    for key, value in overrides.items():
        if value in (None, '', False):
            params.pop(key, None)
        else:
            params[key] = value
    from urllib.parse import urlencode
    encoded = urlencode(params)
    return f'?{encoded}' if encoded else ''


def _decorate(article):
    """Har bir maqolaga hosila maydonlarni qo'shadi. Shablon PDF qoidasini
    QAYTA YOZMAYDI — faqat shu yerdagi `can_read_pdf` ni o'qiydi (§23)."""
    article['can_read_pdf'] = can_read_pdf(article)
    article['pdf_block_reason'] = pdf_block_reason(article)
    article['access_label'] = ACCESS_TYPES.get(article.get('access_type'), '—')
    article['version_label'] = VERSION_TYPES.get(article.get('version_type'), '—')
    article['license_label'] = LICENSES.get(article.get('license_type'), 'Unknown')
    article['source_type_label'] = SOURCE_TYPES.get(article.get('source_type'), '—')
    article['language_label'] = LANGUAGES.get(article.get('language'), article.get('language') or '—')
    article['verification_label'] = VERIFICATION_STATUSES.get(article.get('verification_status'), '—')
    article['publication_label'] = PUBLICATION_STATUSES.get(article.get('publication_status'), '—')
    article['is_public'] = (article.get('publication_status') == 'published'
                            and article.get('verification_status') == 'verified')
    return article


def _attach_authors_and_fields(cur, articles):
    """Mualliflar va yo'nalishlarni IKKI so'rovda biriktiradi (N+1 emas, §25)."""
    if not articles:
        return articles
    ids = [a['id'] for a in articles]
    by_id = {a['id']: a for a in articles}
    for a in articles:
        a['authors'] = []
        a['fields'] = []
        a['primary_field'] = None

    cur.execute("""SELECT article_id, author_name, author_order, orcid, affiliation,
                          linked_user_id, linked_scientist_id, is_corresponding
                     FROM article_authors
                    WHERE article_id = ANY(%s)
                    ORDER BY article_id, author_order, id""", (ids,))
    for row in _dict_rows(cur):
        by_id[row['article_id']]['authors'].append(row)

    cur.execute("""SELECT asf.article_id, asf.is_primary, f.id, f.name_uz, f.slug,
                          f.code, f.level
                     FROM article_scientific_fields asf
                     JOIN scientific_fields f ON f.id = asf.scientific_field_id
                    WHERE asf.article_id = ANY(%s)
                    ORDER BY asf.article_id, asf.is_primary DESC, f.sort_order, f.code""",
                (ids,))
    for row in _dict_rows(cur):
        article = by_id[row['article_id']]
        article['fields'].append(row)
        if row['is_primary']:
            article['primary_field'] = row
    return articles


ARTICLE_COLUMNS = """
    a.id, a.title, a.slug, a.abstract, a.keywords, a.language, a.publication_year,
    a.publication_date, a.journal_name, a.volume, a.issue, a.pages, a.doi,
    a.external_url, a.pdf_path, a.pdf_original_name, a.pdf_size, a.checksum_sha256,
    a.license_type, a.license_url, a.access_type, a.version_type, a.source_type,
    a.source_name, a.source_url, a.copyright_note, a.permission_note,
    a.rights_confirmed_by, a.rights_confirmed_at,
    a.index_scopus, a.index_wos, a.index_doaj, a.index_other,
    a.verification_status, a.publication_status, a.is_featured,
    a.view_count, a.download_count, a.created_by, a.reviewed_by, a.reviewed_at,
    a.created_at, a.updated_at
"""

# Ommaga ko'rinadigan maqola: nashr etilgan VA tekshirilgan. Bu shart har bir
# public so'rovga qo'shiladi — draft maqola hech qachon katalogda ko'rinmaydi.
PUBLIC_WHERE = ("a.publication_status = 'published' "
                "AND a.verification_status = 'verified'")


# ── Katalog filtrlari ────────────────────────────────────────────────────────

def _catalog_filters(cur, field_ids=None):
    """(where_sql, params) — query parametrlaridan katalog filtri.

    `field_ids` — yo'nalish sahifasi uchun oldindan hisoblangan ID ro'yxati.
    Har bir noto'g'ri qiymat jimgina e'tiborsiz qoldiriladi (§16: 500 yo'q).
    """
    where = [PUBLIC_WHERE]
    params = []

    query = (request.args.get('q') or '').strip()[:200]
    if query:
        clause, clause_params, _, _ = build_search_clause(
            query,
            ['a.title', 'a.abstract', 'a.keywords', 'a.doi', 'a.journal_name',
             "COALESCE(au.names, '')"],
            use_fuzzy=_fuzzy_available(cur))
        where.append(clause)
        params.extend(clause_params)

    if field_ids is None:
        # Katalogdagi ?field=<slug> — yo'nalish VA uning barcha avlodlari.
        # Foydalanuvchi "Pedagogika fanlari" ni tanlaganda undagi ixtisosliklar
        # maqolalari ham chiqishi kutiladi (yo'nalish sahifasidagi default
        # bilan bir xil mantiq).
        slug = (request.args.get('field') or '').strip()[:200]
        if slug:
            cur.execute("SELECT id FROM scientific_fields WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if row:
                cur.execute("SELECT id, parent_id FROM scientific_fields")
                children = {}
                for child_id, parent_id in cur.fetchall():
                    children.setdefault(parent_id, []).append(child_id)
                field_ids = descendant_ids(row[0], children)
            else:
                field_ids = []      # mavjud bo'lmagan slug -> bo'sh natija, 500 emas

    if field_ids is not None:
        if not field_ids:
            return 'FALSE', []
        where.append("EXISTS (SELECT 1 FROM article_scientific_fields x "
                     "WHERE x.article_id = a.id AND x.scientific_field_id = ANY(%s))")
        params.append(list(field_ids))

    year_from = _int_arg('year_from', minimum=1900, maximum=2100)
    year_to = _int_arg('year_to', minimum=1900, maximum=2100)
    if year_from:
        where.append('a.publication_year >= %s')
        params.append(year_from)
    if year_to:
        where.append('a.publication_year <= %s')
        params.append(year_to)

    language = (request.args.get('lang') or '').strip()
    if language in LANGUAGES:
        where.append('a.language = %s')
        params.append(language)

    journal = (request.args.get('journal') or '').strip()[:200]
    if journal:
        where.append('a.journal_name ILIKE %s')
        params.append(f'%{journal}%')

    access = (request.args.get('access') or '').strip()
    if access in ACCESS_TYPES:
        where.append('a.access_type = %s')
        params.append(access)

    license_type = (request.args.get('license') or '').strip()
    if license_type in LICENSES:
        where.append('a.license_type = %s')
        params.append(license_type)

    version = (request.args.get('version') or '').strip()
    if version in VERSION_TYPES:
        where.append('a.version_type = %s')
        params.append(version)

    if _flag_arg('pdf'):
        # "PDF mavjud" foydalanuvchi uchun "o'qish mumkin" degani — shunchaki
        # fayl borligi emas. Filtr can_read_pdf() bilan bir xil shartni beradi.
        where.append("a.pdf_path IS NOT NULL AND a.pdf_path <> '' "
                     "AND a.access_type = ANY(%s) AND a.license_type <> 'unknown' "
                     "AND COALESCE(TRIM(a.source_name), '') <> ''")
        params.append(sorted(PDF_ALLOWED_ACCESS_TYPES))

    for flag, column in (('scopus', 'index_scopus'), ('wos', 'index_wos'),
                         ('doaj', 'index_doaj')):
        if _flag_arg(flag):
            where.append(f'a.{column} IS TRUE')

    return ' AND '.join(where), params


CATALOG_FROM = """
      FROM scientific_articles a
 LEFT JOIN LATERAL (
           SELECT string_agg(au2.author_name, ' ') AS names
             FROM article_authors au2 WHERE au2.article_id = a.id
           ) au ON TRUE
"""


def _fetch_catalog(cur, where_sql, params, page, per_page, sort_key):
    """(total, articles) — server-side sahifalash, mualliflar/yo'nalishlar bilan."""
    cur.execute(f"SELECT COUNT(*) {CATALOG_FROM} WHERE {where_sql}", params)
    total = int(cur.fetchone()[0])

    order_sql = SORT_OPTIONS.get(sort_key, SORT_OPTIONS[DEFAULT_SORT])[1]
    offset = (page - 1) * per_page
    cur.execute(
        f"SELECT {ARTICLE_COLUMNS} {CATALOG_FROM} WHERE {where_sql} "
        f"ORDER BY {order_sql} LIMIT %s OFFSET %s",
        params + [per_page, offset])
    articles = [_decorate(a) for a in _dict_rows(cur)]
    _attach_authors_and_fields(cur, articles)
    return total, articles


def _facet_values(cur):
    """Filtr paneli uchun mavjud qiymatlar — joriy tanlovdan QAT'I NAZAR butun
    ommaviy katalog bo'yicha, aks holda tanlangan filtr o'z variantini ro'yxatdan
    o'chirib, foydalanuvchi uni qaytara olmay qoladi."""
    cur.execute(f"""SELECT MIN(a.publication_year), MAX(a.publication_year)
                    {CATALOG_FROM} WHERE {PUBLIC_WHERE}""")
    year_min, year_max = cur.fetchone()
    cur.execute(f"""SELECT a.journal_name, COUNT(*) AS cnt
                    {CATALOG_FROM} WHERE {PUBLIC_WHERE}
                      AND COALESCE(TRIM(a.journal_name), '') <> ''
                    GROUP BY a.journal_name ORDER BY cnt DESC, a.journal_name LIMIT 40""")
    journals = [r[0] for r in cur.fetchall()]
    return {
        'year_min': year_min or 2000,
        'year_max': year_max or date.today().year,
        'journals': journals,
    }


# ── Fan yo'nalishlari ───────────────────────────────────────────────────────

def _load_fields(cur, only_active=True):
    """Barcha yo'nalishlar + har biridagi maqolalar soni (2 so'rov, N+1 emas).

    `article_count` — AYNAN shu yo'nalikdagi ommaviy maqolalar; `total_count`
    — avlodlari bilan birga. Ikkalasi ham UI'da ochiq ko'rsatiladi (§21).
    """
    cur.execute(f"""
        SELECT f.id, f.parent_id, f.name_uz, f.name_uz_cyrillic, f.name_ru, f.code,
               f.slug, f.level, f.description, f.sort_order, f.is_active,
               f.source_name, f.source_url, f.source_updated_at,
               f.raw_name, f.source_line, f.source_sha256, f.review_status,
               f.missing_parent_code,
               COALESCE(c.cnt, 0) AS article_count
          FROM scientific_fields f
     LEFT JOIN (
               SELECT asf.scientific_field_id AS fid, COUNT(DISTINCT a.id) AS cnt
                 FROM article_scientific_fields asf
                 JOIN scientific_articles a ON a.id = asf.article_id
                WHERE {PUBLIC_WHERE}
             GROUP BY asf.scientific_field_id
           ) c ON c.fid = f.id
         {'WHERE f.is_active' if only_active else ''}
      ORDER BY f.level, f.sort_order, f.code NULLS LAST, f.name_uz
    """)
    return _dict_rows(cur)


def _children_map(rows):
    children = {}
    for row in rows:
        children.setdefault(row.get('parent_id'), []).append(row['id'])
    return children


def _roll_up_counts(node):
    """Daraxt bo'ylab maqolalar sonini yig'adi (post-order, rekursiv emas edi —
    bu yerda daraxt sayoz, rekursiya xavfsiz va o'qishga oson)."""
    total = node.get('article_count') or 0
    for child in node['children']:
        total += _roll_up_counts(child)
    node['total_count'] = total
    node['child_count'] = len(node['children'])
    return total


def _field_tree(cur, only_active=True):
    rows = _load_fields(cur, only_active=only_active)
    tree = build_tree(rows)
    for root in tree:
        _roll_up_counts(root)
    return tree, rows


def _field_breadcrumb(rows_by_id, field):
    """Ildizgacha bo'lgan zanjir (yo'nalishning o'zi oxirida emas — sahifa
    sarlavhasi alohida chiqadi). Halqa bo'lsa ham tugaydi."""
    chain, seen = [], set()
    current = rows_by_id.get(field.get('parent_id'))
    while current and current['id'] not in seen:
        seen.add(current['id'])
        chain.append(current)
        current = rows_by_id.get(current.get('parent_id'))
    return list(reversed(chain))


# ═══════════════════════════════════════════════════════════════════════════
#  Public routes
# ═══════════════════════════════════════════════════════════════════════════

@articles_bp.route('/maqolalar')
def catalog():
    """Maqolalar katalogi — qidiruv, filtr, sort, server-side sahifalash."""
    page = _int_arg('page', 1, minimum=1, maximum=MAX_PAGE) or 1
    per_page = _int_arg('per_page', PER_PAGE, minimum=5, maximum=MAX_PER_PAGE) or PER_PAGE
    sort_key = _choice_arg('sort', SORT_OPTIONS, DEFAULT_SORT)
    view_mode = _choice_arg('view', VIEW_MODES, 'karta')

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            where_sql, params = _catalog_filters(cur)
            total, articles = _fetch_catalog(cur, where_sql, params, page, per_page, sort_key)
            facets = _facet_values(cur)
            tree, _rows = _field_tree(cur)
    finally:
        conn.close()

    pages = max(1, math.ceil(total / per_page))
    return render_template(
        'maqolalar_katalog.html',
        articles=articles, total=total, page=page, pages=pages, per_page=per_page,
        sort_key=sort_key, sort_options=SORT_OPTIONS, view_mode=view_mode,
        field_tree=tree, facets=facets,
        access_types=ACCESS_TYPES, licenses=LICENSES, version_types=VERSION_TYPES,
        languages=LANGUAGES, qs=_query_string,
        active_filters=_active_filter_chips(tree),
    )


def _active_filter_chips(tree):
    """Tanlangan filtrlarni chip sifatida ko'rsatish uchun (yorliq, param)."""
    flat = {}

    def walk(nodes):
        for node in nodes:
            flat[node['slug']] = node['name_uz']
            walk(node['children'])
    walk(tree)

    chips = []
    labels = [
        ('q', lambda v: f'Qidiruv: {v}'),
        ('field', lambda v: flat.get(v, v)),
        ('year_from', lambda v: f'{v} yildan'),
        ('year_to', lambda v: f'{v} yilgacha'),
        ('lang', lambda v: LANGUAGES.get(v, v)),
        ('journal', lambda v: f'Jurnal: {v}'),
        ('access', lambda v: ACCESS_TYPES.get(v, v)),
        ('license', lambda v: LICENSES.get(v, v)),
        ('version', lambda v: VERSION_TYPES.get(v, v)),
        ('pdf', lambda v: 'PDF mavjud'),
        ('scopus', lambda v: 'Scopus'),
        ('wos', lambda v: 'Web of Science'),
        ('doaj', lambda v: 'DOAJ'),
    ]
    for key, render in labels:
        value = (request.args.get(key) or '').strip()
        if value:
            chips.append({'key': key, 'label': render(value)})
    return chips


FIELDS_PER_PAGE = 40


@articles_bp.route('/maqolalar/yonalishlar')
def fields_index():
    """Fan yo'nalishlari ierarxiyasi — progressive disclosure (§20).

    Ikki rejim bor, chunki taksonomiya katta (OAK 289 ixtisoslik):

      * qidiruvsiz — DARAXT. Faqat fan tarmoqlari ko'rinadi, ichki
        yo'nalishlar tugma ortida. Yuzlab yozuv bir vaqtda yoyilmaydi.
      * qidiruv/tarmoq filtri bilan — TEKIS, SAHIFALANGAN ro'yxat. 289 ta
        yozuv ichidan izlaganda daraxt konteksti emas, natija muhim; har bir
        natijada ota konteksti alohida ko'rsatiladi.
    """
    only_with_articles = _flag_arg('bor')
    query = (request.args.get('q') or '').strip()[:120]
    branch_slug = (request.args.get('tarmoq') or '').strip()[:200]
    page = _int_arg('page', 1, minimum=1, maximum=MAX_PAGE) or 1

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            tree, rows = _field_tree(cur)
            cur.execute(f"SELECT COUNT(*) FROM scientific_articles a WHERE {PUBLIC_WHERE}")
            article_total = int(cur.fetchone()[0])
    finally:
        conn.close()

    rows_by_id = {r['id']: r for r in rows}
    branches = [dict(r) for r in rows if not r.get('parent_id')]

    if only_with_articles:
        tree = _prune_empty(tree)

    flat_results = None
    pages = 1
    total_matches = 0
    if query or branch_slug:
        matches = _flat_field_matches(rows, rows_by_id, query, branch_slug,
                                      only_with_articles, tree)
        total_matches = len(matches)
        pages = max(1, math.ceil(total_matches / FIELDS_PER_PAGE))
        page = min(page, pages)
        start = (page - 1) * FIELDS_PER_PAGE
        flat_results = matches[start:start + FIELDS_PER_PAGE]

    return render_template('maqolalar_yonalishlar.html',
                           field_tree=tree, field_total=len(rows),
                           article_total=article_total, branches=branches,
                           query=query, branch_slug=branch_slug,
                           only_with_articles=only_with_articles,
                           flat_results=flat_results, total_matches=total_matches,
                           page=page, pages=pages, qs=_query_string)


def _flat_field_matches(rows, rows_by_id, query, branch_slug, only_with_articles,
                        tree):
    """Qidiruv/filtr natijasi — tekis ro'yxat, har birida ota konteksti.

    Kod bo'yicha qidiruv ataylab alohida: "05.01" yozilganda nomlarda emas,
    shifrda prefiks bo'yicha topilishi kerak.
    """
    from utils.search_helper import matches_query

    totals = {}

    def collect(nodes):
        for node in nodes:
            totals[node['id']] = node.get('total_count') or 0
            collect(node['children'])
    collect(tree)

    branch_id = None
    if branch_slug:
        branch = next((r for r in rows if r['slug'] == branch_slug
                       and not r.get('parent_id')), None)
        if branch is None:
            return []
        branch_id = branch['id']

    needle = (query or '').lower()
    results = []
    for row in rows:
        if branch_id is not None and _root_id_of(row, rows_by_id) != branch_id:
            continue
        if only_with_articles and not totals.get(row['id']):
            continue
        if query:
            code = (row.get('code') or '').lower()
            hit = (needle in code
                   or matches_query(query, row.get('name_uz'),
                                    row.get('name_uz_cyrillic'), row.get('name_ru')))
            if not hit:
                continue
        entry = dict(row)
        entry['total_count'] = totals.get(row['id'], row.get('article_count') or 0)
        entry['ancestors'] = _field_breadcrumb(rows_by_id, row)
        results.append(entry)

    results.sort(key=lambda r: (r.get('code') or 'zz', r.get('name_uz') or ''))
    return results


def _root_id_of(row, rows_by_id):
    """Yozuvning eng yuqori ajdodi (fan tarmog'i) ID si. Halqada tugaydi."""
    current, seen = row, set()
    while current.get('parent_id') and current['id'] not in seen:
        seen.add(current['id'])
        parent = rows_by_id.get(current['parent_id'])
        if parent is None:
            break
        current = parent
    return current['id']


def _prune_empty(nodes):
    kept = []
    for node in nodes:
        node = dict(node, children=_prune_empty(node['children']))
        if node.get('total_count') or node['children']:
            kept.append(node)
    return kept


@articles_bp.route('/maqolalar/yonalish/<slug>')
def field_detail(slug):
    """Yo'nalish tafsiloti + undagi maqolalar.

    Default qaror (§21): yuqori fan tarmog'ida (level 1-2) avlod yo'nalishlardagi
    maqolalar ham ko'rsatiladi, aniq ixtisoslikda (level >= 3) faqat o'ziniki.
    `?faqat=1` / `?faqat=0` bilan foydalanuvchi buni ochiq o'zgartira oladi.
    """
    page = _int_arg('page', 1, minimum=1, maximum=MAX_PAGE) or 1
    per_page = _int_arg('per_page', PER_PAGE, minimum=5, maximum=MAX_PER_PAGE) or PER_PAGE
    sort_key = _choice_arg('sort', SORT_OPTIONS, DEFAULT_SORT)
    view_mode = _choice_arg('view', VIEW_MODES, 'karta')

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            tree, rows = _field_tree(cur)
            rows_by_id = {r['id']: r for r in rows}
            field = next((r for r in rows if r['slug'] == slug), None)
            if field is None:
                abort(404)

            children_map = _children_map(rows)
            deep_default = (field['level'] or 1) < 3
            raw = (request.args.get('faqat') or '').strip()
            include_descendants = deep_default if raw not in ('0', '1') else raw == '0'

            field_ids = (descendant_ids(field['id'], children_map)
                         if include_descendants else [field['id']])
            where_sql, params = _catalog_filters(cur, field_ids=field_ids)
            total, articles = _fetch_catalog(cur, where_sql, params, page, per_page, sort_key)
            facets = _facet_values(cur)

            own_count = field.get('article_count') or 0
            child_nodes = [r for r in rows if r.get('parent_id') == field['id']]
            crumbs = _field_breadcrumb(rows_by_id, field)
    finally:
        conn.close()

    subtree = _find_node(tree, field['id'])
    pages = max(1, math.ceil(total / per_page))
    return render_template(
        'maqolalar_yonalish.html',
        field=field, crumbs=crumbs, children=child_nodes, subtree=subtree,
        articles=articles, total=total, page=page, pages=pages, per_page=per_page,
        sort_key=sort_key, sort_options=SORT_OPTIONS, view_mode=view_mode,
        include_descendants=include_descendants, own_count=own_count, facets=facets,
        qs=_query_string, active_filters=_active_filter_chips(tree),
        access_types=ACCESS_TYPES, licenses=LICENSES, version_types=VERSION_TYPES,
        languages=LANGUAGES)


def _find_node(nodes, field_id):
    for node in nodes:
        if node['id'] == field_id:
            return node
        found = _find_node(node['children'], field_id)
        if found:
            return found
    return None


def _primary_field_crumbs(rows_by_id, article):
    """Asosiy yo'nalishgacha bo'lgan zanjir. Yo'nalish nofaol qilingan yoki
    o'chirilgan bo'lsa sahifa yiqilmaydi — breadcrumb shunchaki bo'sh qoladi."""
    primary = article.get('primary_field')
    row = rows_by_id.get(primary['id']) if primary else None
    if row is None:
        return []
    return _field_breadcrumb(rows_by_id, row) + [row]


def _load_article(cur, slug=None, article_id=None, public_only=True):
    where = 'a.slug = %s' if slug else 'a.id = %s'
    params = [slug if slug else article_id]
    if public_only:
        where = f'{where} AND {PUBLIC_WHERE}'
    cur.execute(f"SELECT {ARTICLE_COLUMNS} FROM scientific_articles a WHERE {where}", params)
    rows = _dict_rows(cur)
    if not rows:
        return None
    article = _decorate(rows[0])
    _attach_authors_and_fields(cur, [article])
    return article


@articles_bp.route('/maqolalar/<slug>')
def article_detail(slug):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            article = _load_article(cur, slug=slug)
            if article is None:
                abort(404)
            rows_by_id = {r['id']: r for r in _load_fields(cur, only_active=False)}
            crumbs = _primary_field_crumbs(rows_by_id, article)
            cur.execute("UPDATE scientific_articles SET view_count = view_count + 1 "
                        "WHERE id = %s", (article['id'],))
            conn.commit()
    finally:
        conn.close()
    return render_template('maqolalar_detail.html', article=article,
                           field_crumbs=crumbs, preview=False)


def _log_missing_pdf(article):
    """DB "PDF bor" deydi, disk "yo'q" deydi.

    Bu jimgina 500 bo'lmasligi kerak, lekin yo'qolib ham ketmasin: adminga
    korrelyatsiya ID bilan log qoladi, foydalanuvchi esa 404 oladi va fayl
    tuzilmasi haqida hech narsa bilmaydi. `security.log_exception` bu yerda
    ishlamaydi — u faol istisno kontekstini talab qiladi, bu yerda esa istisno
    yo'q, shunchaki nomuvofiqlik.
    """
    current_app.logger.error(
        'Maqola #%s uchun PDF diskda topilmadi (pdf_path=%r) request_id=%s',
        article.get('id'), article.get('pdf_path'),
        getattr(g, 'request_id', '-'))


def _public_pdf_or_404(cur, slug):
    """(article, absolute_path) — yoki 404.

    PDF beradigan HAR BIR route (o'quvchi sahifasi, oqim, yuklab olish) aynan
    shu yerdan o'tadi. Kirish qoidasi nusxalanmaydi: yagona manba
    `can_read_pdf()`. Fayl yo'li DB dagi `pdf_path` kalitidan olinadi va
    `resolve_private_pdf` realpath solishtiruvi bilan storage_dir() ichida
    qolishga majburlanadi — so'rovdan kelgan hech qanday matn yo'lga
    aralashmaydi, shuning uchun `..` bilan chiqib bo'lmaydi.
    """
    article = _load_article(cur, slug=slug)
    if article is None or not article['can_read_pdf']:
        abort(404)
    path = resolve_private_pdf(storage_dir(), article['pdf_path'])
    if not path:
        _log_missing_pdf(article)
        abort(404)
    return article, path


@articles_bp.route('/maqolalar/<slug>/oqish')
def article_read(slug):
    """Ichki PDF o'quvchisi (PDF.js) — foydalanuvchi saytdan chiqmaydi.

    Sahifa faqat karkasni beradi; PDF baytlari alohida himoyalangan oqim
    route'idan (`/maqolalar/<slug>/pdf`) keladi va u kirish qoidasini QAYTA
    tekshiradi. Shablon hech qanday huquq qoidasini o'zi hisoblamaydi.

    Ko'rishlar soni bu yerda oshirilmaydi: u maqola tafsiloti route'ida
    hisoblanadi (§26) va o'quvchi sahifasi shu tashrifning davomi.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            article = _load_article(cur, slug=slug)
            if article is None or not article['can_read_pdf']:
                abort(404)
            rows_by_id = {r['id']: r for r in _load_fields(cur, only_active=False)}
            crumbs = _primary_field_crumbs(rows_by_id, article)
    finally:
        conn.close()

    response = current_app.make_response(render_template(
        'maqolalar_oqish.html', article=article, field_crumbs=crumbs,
        pdfjs_version=PDFJS_VERSION, asset_version=VIEWER_ASSET_VERSION))
    # Kanonik ilmiy sahifa — maqola tafsiloti; o'quvchi uning ko'rish rejimi.
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def _stream_headers(response):
    """PDF oqimi uchun umumiy sarlavhalar (oqim ham, yuklab olish ham)."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # PDF matni maqola tafsiloti orqali indekslanadi; faylning o'zi qidiruv
    # natijasida can_read_pdf() dan tashqarida turgan alohida hujjat bo'lib
    # qolmasligi kerak.
    response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    return response


@articles_bp.route('/maqolalar/<slug>/pdf')
def article_pdf_stream(slug):
    """O'quvchi o'qiydigan himoyalangan PDF oqimi.

    * `conditional=True` — Werkzeug `Accept-Ranges: bytes` beradi va `Range:`
      so'roviga 206 + `Content-Range` bilan javob qaytaradi. PDF.js aynan
      shunga tayanadi: u hujjatni bo'laklab so'raydi, shuning uchun katta
      fayl birinchi sahifa uchun to'liq yuklanmaydi.
    * Fayl WSGI file wrapper orqali oqadi — baytlar Python xotirasiga to'liq
      o'qilmaydi.
    * `Content-Disposition: inline` — o'quvchi uni o'zi render qiladi.
      Yuklab olish taklif qilinmaydi (§23: o'qish huquqi = yuklab olish
      huquqi EMAS); nom slug'dan olinadi, storage kaliti oshkor qilinmaydi.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            _article, path = _public_pdf_or_404(cur, slug)
    finally:
        conn.close()

    response = send_file(path, mimetype='application/pdf', as_attachment=False,
                         conditional=True, max_age=PDF_STREAM_MAX_AGE)
    response.headers['Content-Disposition'] = (
        'inline; filename="%s"' % sanitize_pdf_filename(f'{slug}.pdf'))
    response.cache_control.public = False
    response.cache_control.private = True
    response.cache_control.max_age = PDF_STREAM_MAX_AGE
    return _stream_headers(response)


@articles_bp.route('/maqolalar/<slug>/fayl')
def article_file(slug):
    """PDF yuklab olish (attachment).

    Bu route Phase 2B.1 dan qolgan va ATAYIN hech qayerda tugma sifatida
    ko'rsatilmaydi: hozirgi huquqiy model faqat "o'qish" huquqini biladi,
    yuklab olish/qayta tarqatish huquqi litsenziyaga bog'liq va alohida
    `can_download_pdf()` kontrakti bilan hal qilinadi (docs/articles-module.md).
    O'quvchi o'rniga `/pdf` oqimidan foydalanadi.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            article, path = _public_pdf_or_404(cur, slug)
            cur.execute("UPDATE scientific_articles SET download_count = download_count + 1 "
                        "WHERE id = %s", (article['id'],))
            conn.commit()
            download_name = article.get('pdf_original_name') or f"{slug}.pdf"
    finally:
        conn.close()
    response = send_file(path, mimetype='application/pdf', as_attachment=True,
                         download_name=sanitize_pdf_filename(download_name))
    return _stream_headers(response)


# ═══════════════════════════════════════════════════════════════════════════
#  Admin — maqolalar
# ═══════════════════════════════════════════════════════════════════════════

def _require_admin():
    from app import _require_admin as guard
    guard()


def _form_values():
    """Formadan kelgan xom qiymatlar. Validatsiya alohida — xato bo'lsa
    forma AYNAN shu qiymatlar bilan qayta ko'rsatiladi (§26)."""
    form = request.form
    get = lambda k: (form.get(k) or '').strip()
    authors = []
    for index, name in enumerate(form.getlist('author_name')[:MAX_AUTHORS]):
        name = (name or '').strip()
        if not name:
            continue
        authors.append({
            'author_name': name[:300],
            'author_order': index + 1,
            'orcid': (form.getlist('author_orcid')[index:index + 1] or [''])[0].strip()[:64] or None,
            'affiliation': (form.getlist('author_affiliation')[index:index + 1] or [''])[0].strip()[:400] or None,
            'is_corresponding': str(index) in form.getlist('author_corresponding'),
        })
    return {
        'title': get('title')[:500],
        'abstract': get('abstract'),
        'keywords': get('keywords')[:500],
        'language': get('language') or 'uz',
        'publication_year': get('publication_year'),
        'publication_date': get('publication_date'),
        'journal_name': get('journal_name')[:300],
        'volume': get('volume')[:50],
        'issue': get('issue')[:50],
        'pages': get('pages')[:50],
        'doi': get('doi'),
        'external_url': get('external_url')[:1000],
        'license_type': get('license_type') or 'unknown',
        'license_url': get('license_url')[:500],
        'access_type': get('access_type') or 'metadata_only',
        'version_type': get('version_type') or 'other',
        'source_type': get('source_type') or 'other_verified_source',
        'source_name': get('source_name')[:300],
        'source_url': get('source_url')[:1000],
        'copyright_note': get('copyright_note'),
        'permission_note': get('permission_note'),
        'index_scopus': bool(get('index_scopus')),
        'index_wos': bool(get('index_wos')),
        'index_doaj': bool(get('index_doaj')),
        'index_other': get('index_other')[:300],
        'verification_status': get('verification_status') or 'draft',
        'publication_status': get('publication_status') or 'draft',
        'is_featured': bool(get('is_featured')),
        'rights_confirmed': bool(get('rights_confirmed')),
        'remove_pdf': bool(get('remove_pdf')),
        'primary_field_id': get('primary_field_id'),
        'extra_field_ids': [v for v in request.form.getlist('extra_field_ids')
                            if v.strip()][:MAX_EXTRA_FIELDS],
        'authors': authors,
    }


def _validate_article(cur, values, article_id=None, pdf_present=False):
    """Barcha validatsiya bitta joyda — xato ArticleValidationError bilan."""
    if not values['title']:
        raise ArticleValidationError('Maqola nomi majburiy.', 'title')

    validate_choice(values['language'], LANGUAGES, 'language', 'Til')
    validate_choice(values['version_type'], VERSION_TYPES, 'version_type', 'Versiya turi')
    validate_choice(values['source_type'], SOURCE_TYPES, 'source_type', 'Manba turi')
    validate_choice(values['verification_status'], VERIFICATION_STATUSES,
                    'verification_status', 'Tekshiruv holati')
    validate_choice(values['publication_status'], PUBLICATION_STATUSES,
                    'publication_status', 'Nashr holati')
    validate_article_rights(values['access_type'], values['license_type'],
                            pdf_present, values['rights_confirmed'])

    year = values['publication_year']
    if year:
        try:
            year = int(year)
        except ValueError:
            raise ArticleValidationError('Nashr yili butun son bo\'lishi kerak.',
                                         'publication_year')
        if not 1800 <= year <= date.today().year + 1:
            raise ArticleValidationError('Nashr yili haqiqiy oraliqda emas.',
                                         'publication_year')
    values['publication_year'] = year or None

    if values['publication_date']:
        try:
            values['publication_date'] = date.fromisoformat(values['publication_date'])
        except ValueError:
            raise ArticleValidationError('Nashr sanasi formati noto\'g\'ri.',
                                         'publication_date')
    else:
        values['publication_date'] = None

    doi = validate_doi(values['doi'])
    if doi:
        if article_id:
            cur.execute("SELECT id FROM scientific_articles WHERE doi = %s AND id <> %s",
                        (doi, article_id))
        else:
            cur.execute("SELECT id FROM scientific_articles WHERE doi = %s", (doi,))
        if cur.fetchone():
            raise ArticleValidationError('Bu DOI bilan maqola allaqachon mavjud.', 'doi')
    values['doi'] = doi

    if not values['primary_field_id']:
        raise ArticleValidationError('Asosiy fan yo\'nalishini tanlang.', 'primary_field_id')
    try:
        primary_id = int(values['primary_field_id'])
    except ValueError:
        raise ArticleValidationError('Asosiy yo\'nalish noto\'g\'ri.', 'primary_field_id')
    cur.execute("SELECT 1 FROM scientific_fields WHERE id = %s", (primary_id,))
    if not cur.fetchone():
        raise ArticleValidationError('Tanlangan yo\'nalish topilmadi.', 'primary_field_id')
    values['primary_field_id'] = primary_id

    extra = []
    for raw in values['extra_field_ids']:
        try:
            value = int(raw)
        except ValueError:
            continue
        if value != primary_id and value not in extra:
            extra.append(value)
    values['extra_field_ids'] = extra

    if not values['authors']:
        raise ArticleValidationError('Kamida bitta muallif kiriting.', 'authors')

    if not values['license_url'] and values['license_type'] in LICENSE_URLS:
        values['license_url'] = LICENSE_URLS[values['license_type']]
    return values


def _write_associations(cur, article_id, values):
    """Mualliflar va yo'nalishlarni to'liq almashtiradi (tartib saqlanadi).

    Asosiy yo'nalish OXIRIDA yoziladi: partial UNIQUE indeks bitta primary ga
    ruxsat beradi, eski qatorlar oldin o'chirilgani uchun to'qnashuv bo'lmaydi.
    """
    cur.execute("DELETE FROM article_authors WHERE article_id = %s", (article_id,))
    for author in values['authors']:
        cur.execute("""INSERT INTO article_authors
                       (article_id, author_name, author_order, orcid, affiliation,
                        is_corresponding)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (article_id, author['author_name'], author['author_order'],
                     author['orcid'], author['affiliation'], author['is_corresponding']))

    cur.execute("DELETE FROM article_scientific_fields WHERE article_id = %s", (article_id,))
    for field_id in values['extra_field_ids']:
        cur.execute("""INSERT INTO article_scientific_fields
                       (article_id, scientific_field_id, is_primary)
                       VALUES (%s, %s, FALSE)
                       ON CONFLICT (article_id, scientific_field_id) DO NOTHING""",
                    (article_id, field_id))
    cur.execute("""INSERT INTO article_scientific_fields
                   (article_id, scientific_field_id, is_primary)
                   VALUES (%s, %s, TRUE)
                   ON CONFLICT (article_id, scientific_field_id)
                   DO UPDATE SET is_primary = TRUE""",
                (article_id, values['primary_field_id']))


def _handle_pdf(cur, values, existing=None):
    """(pdf_fields, old_path_to_delete). Fayl DB dan OLDIN yoziladi (§12).

    Ketma-ketlik ataylab: yangi fayl diskka muvaffaqiyatli tushmaguncha DB
    o'zgarmaydi, eski fayl esa tranzaksiya commit bo'lgandan KEYIN o'chiriladi.
    Xato bo'lsa eski fayl joyida qoladi va maqola ishlashda davom etadi.
    """
    upload = request.files.get('pdf_file')
    has_upload = bool(upload and upload.filename)
    old_path = (existing or {}).get('pdf_path')

    if values['remove_pdf'] and not has_upload:
        return ({'pdf_path': None, 'pdf_original_name': None, 'pdf_size': None,
                 'pdf_mime_type': None, 'checksum_sha256': None}, old_path)

    if not has_upload:
        return ({}, None)

    if not values['rights_confirmed']:
        raise ArticleValidationError(
            'PDF yuklash uchun huquqiy tasdiqni belgilang.', 'rights_confirmed')
    if values['access_type'] not in PDF_ALLOWED_ACCESS_TYPES:
        raise ArticleValidationError(
            '"%s" kirish turida PDF saqlanmaydi.' % ACCESS_TYPES.get(values['access_type'], '—'),
            'access_type')

    data, checksum = validate_pdf_upload(upload)

    article_id = (existing or {}).get('id')
    if article_id:
        cur.execute("SELECT id, title FROM scientific_articles "
                    "WHERE checksum_sha256 = %s AND id <> %s", (checksum, article_id))
    else:
        cur.execute("SELECT id, title FROM scientific_articles WHERE checksum_sha256 = %s",
                    (checksum,))
    duplicate = cur.fetchone()
    if duplicate:
        raise ArticleValidationError(
            'Aynan shu PDF fayl "%s" maqolasida allaqachon mavjud (#%s).'
            % (duplicate[1], duplicate[0]), 'pdf_file')

    relative = save_private_pdf(data, storage_dir())
    return ({'pdf_path': relative,
             'pdf_original_name': sanitize_pdf_filename(upload.filename),
             'pdf_size': len(data),
             'pdf_mime_type': 'application/pdf',
             'checksum_sha256': checksum}, old_path)


def _delete_stored_pdf(relative_path):
    """Eski faylni o'chiradi — faqat DB commit bo'lgandan keyin chaqiriladi."""
    if not relative_path:
        return
    path = resolve_private_pdf(storage_dir(), relative_path)
    if path:
        try:
            os.unlink(path)
        except OSError:             # disk xatosi maqolani buzmasin
            log_exception(current_app.logger, 'Eski maqola PDF fayli o\'chirilmadi')


ARTICLE_WRITE_COLUMNS = (
    'title', 'abstract', 'keywords', 'language', 'publication_year',
    'publication_date', 'journal_name', 'volume', 'issue', 'pages', 'doi',
    'external_url', 'license_type', 'license_url', 'access_type', 'version_type',
    'source_type', 'source_name', 'source_url', 'copyright_note', 'permission_note',
    'index_scopus', 'index_wos', 'index_doaj', 'index_other',
    'verification_status', 'publication_status', 'is_featured',
)


def _admin_form_context(cur, values=None, article=None, error=None):
    tree, rows = _field_tree(cur, only_active=False)
    return {
        'field_tree': tree, 'field_rows': rows,
        'access_types': ACCESS_TYPES, 'licenses': LICENSES,
        'version_types': VERSION_TYPES, 'source_types': SOURCE_TYPES,
        'verification_statuses': VERIFICATION_STATUSES,
        'publication_statuses': PUBLICATION_STATUSES, 'languages': LANGUAGES,
        'pdf_allowed_access': sorted(PDF_ALLOWED_ACCESS_TYPES),
        'max_pdf_mb': round(MAX_PDF_BYTES / (1024 * 1024), 1),
        'values': values, 'article': article, 'error': error,
    }


@articles_bp.route('/admin/articles')
@login_required
def admin_articles():
    _require_admin()
    page = _int_arg('page', 1, minimum=1, maximum=MAX_PAGE) or 1

    where, params = ['TRUE'], []
    for key, column, allowed in (
            ('status', 'publication_status', PUBLICATION_STATUSES),
            ('verification', 'verification_status', VERIFICATION_STATUSES),
            ('access', 'access_type', ACCESS_TYPES),
            ('license', 'license_type', LICENSES)):
        value = (request.args.get(key) or '').strip()
        if value in allowed:
            where.append(f'a.{column} = %s')
            params.append(value)
    year = _int_arg('year', minimum=1800, maximum=2100)
    if year:
        where.append('a.publication_year = %s')
        params.append(year)
    field = (request.args.get('field') or '').strip()
    if field:
        where.append("EXISTS (SELECT 1 FROM article_scientific_fields x "
                     "JOIN scientific_fields f ON f.id = x.scientific_field_id "
                     "WHERE x.article_id = a.id AND f.slug = %s)")
        params.append(field)
    pdf = (request.args.get('pdf') or '').strip()
    if pdf == '1':
        where.append("COALESCE(a.pdf_path, '') <> ''")
    elif pdf == '0':
        where.append("COALESCE(a.pdf_path, '') = ''")
    for flag, column in (('scopus', 'index_scopus'), ('wos', 'index_wos'),
                         ('doaj', 'index_doaj')):
        if _flag_arg(flag):
            where.append(f'a.{column} IS TRUE')
    where_sql = ' AND '.join(where)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            cur.execute(f"SELECT COUNT(*) FROM scientific_articles a WHERE {where_sql}", params)
            total = int(cur.fetchone()[0])
            cur.execute(
                f"SELECT {ARTICLE_COLUMNS} FROM scientific_articles a WHERE {where_sql} "
                f"ORDER BY a.updated_at DESC, a.id DESC LIMIT %s OFFSET %s",
                params + [ADMIN_PER_PAGE, (page - 1) * ADMIN_PER_PAGE])
            articles = [_decorate(a) for a in _dict_rows(cur)]
            _attach_authors_and_fields(cur, articles)
            cur.execute("""SELECT publication_status, COUNT(*) FROM scientific_articles
                           GROUP BY publication_status""")
            stats = dict(cur.fetchall())
            _tree, field_rows = _field_tree(cur, only_active=False)
    finally:
        conn.close()

    return render_template('admin/articles.html', articles=articles, total=total,
                           page=page, pages=max(1, math.ceil(total / ADMIN_PER_PAGE)),
                           stats=stats, field_rows=field_rows,
                           access_types=ACCESS_TYPES, licenses=LICENSES,
                           verification_statuses=VERIFICATION_STATUSES,
                           publication_statuses=PUBLICATION_STATUSES, qs=_query_string)


@articles_bp.route('/admin/articles/new', methods=['GET', 'POST'])
@login_required
def admin_article_new():
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            if request.method == 'GET':
                return render_template('admin/article_form.html', edit_mode=False,
                                       **_admin_form_context(cur))

            values = _form_values()
            upload = request.files.get('pdf_file')
            saved_path = None
            try:
                _validate_article(cur, values, pdf_present=bool(upload and upload.filename))
                pdf_fields, _old = _handle_pdf(cur, values)
                saved_path = pdf_fields.get('pdf_path')

                slug = _unique_slug(cur, 'scientific_articles', make_slug(values['title']))
                columns = list(ARTICLE_WRITE_COLUMNS) + list(pdf_fields.keys())
                data = [values[c] for c in ARTICLE_WRITE_COLUMNS] + list(pdf_fields.values())
                columns += ['slug', 'created_by']
                data += [slug, current_user.id]
                if values['rights_confirmed']:
                    columns += ['rights_confirmed_by', 'rights_confirmed_at']
                    data += [current_user.id, datetime.now()]
                if values['verification_status'] == 'verified':
                    columns += ['reviewed_by', 'reviewed_at']
                    data += [current_user.id, datetime.now()]
                placeholders = ', '.join(['%s'] * len(columns))
                cur.execute(
                    f"INSERT INTO scientific_articles ({', '.join(columns)}) "
                    f"VALUES ({placeholders}) RETURNING id", data)
                article_id = cur.fetchone()[0]
                _write_associations(cur, article_id, values)
                conn.commit()
            except (ArticleValidationError, UploadValidationError) as exc:
                conn.rollback()
                if saved_path:            # DB yozilmadi — yangi fayl ham qolmasin
                    _delete_stored_pdf(saved_path)
                return render_template(
                    'admin/article_form.html', edit_mode=False,
                    **_admin_form_context(cur, values=values, error=exc.public_message)), 400
    finally:
        conn.close()

    flash('Maqola qo\'shildi.')
    return redirect(f'/admin/articles/{article_id}/edit')


@articles_bp.route('/admin/articles/<int:article_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_article_edit(article_id):
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            existing = _load_article(cur, article_id=article_id, public_only=False)
            if existing is None:
                abort(404)
            if request.method == 'GET':
                return render_template('admin/article_form.html', edit_mode=True,
                                       **_admin_form_context(cur, article=existing))

            values = _form_values()
            upload = request.files.get('pdf_file')
            pdf_present = bool(upload and upload.filename) or (
                bool(existing['pdf_path']) and not values['remove_pdf'])
            saved_path = old_path = None
            try:
                _validate_article(cur, values, article_id=article_id, pdf_present=pdf_present)
                pdf_fields, old_path = _handle_pdf(cur, values, existing=existing)
                saved_path = pdf_fields.get('pdf_path')

                columns = list(ARTICLE_WRITE_COLUMNS) + list(pdf_fields.keys())
                data = [values[c] for c in ARTICLE_WRITE_COLUMNS] + list(pdf_fields.values())
                assignments = [f'{c} = %s' for c in columns] + ['updated_at = NOW()']
                if values['rights_confirmed'] and not existing.get('rights_confirmed_at'):
                    assignments.append('rights_confirmed_by = %s')
                    data.append(current_user.id)
                    assignments.append('rights_confirmed_at = NOW()')
                if (values['verification_status'] == 'verified'
                        and existing.get('verification_status') != 'verified'):
                    assignments.append('reviewed_by = %s')
                    data.append(current_user.id)
                    assignments.append('reviewed_at = NOW()')
                cur.execute(
                    f"UPDATE scientific_articles SET {', '.join(assignments)} WHERE id = %s",
                    data + [article_id])
                _write_associations(cur, article_id, values)
                conn.commit()
            except (ArticleValidationError, UploadValidationError) as exc:
                conn.rollback()
                if saved_path:
                    _delete_stored_pdf(saved_path)
                return render_template(
                    'admin/article_form.html', edit_mode=True,
                    **_admin_form_context(cur, values=values, article=existing,
                                          error=exc.public_message)), 400
            # Commit muvaffaqiyatli — endi eski faylni o'chirish xavfsiz (§12).
            if old_path and old_path != saved_path:
                _delete_stored_pdf(old_path)
    finally:
        conn.close()

    flash('Maqola saqlandi.')
    return redirect(f'/admin/articles/{article_id}/edit')


@articles_bp.route('/admin/articles/<int:article_id>/review', methods=['GET', 'POST'])
@login_required
def admin_article_review(article_id):
    """Moderatsiya: tekshiruv holati + nashr holati (forma to'liq ochilmaydi)."""
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            article = _load_article(cur, article_id=article_id, public_only=False)
            if article is None:
                abort(404)
            if request.method == 'POST':
                verification = (request.form.get('verification_status') or '').strip()
                publication = (request.form.get('publication_status') or '').strip()
                note = (request.form.get('permission_note') or '').strip()
                if (verification not in VERIFICATION_STATUSES
                        or publication not in PUBLICATION_STATUSES):
                    return render_template('admin/article_review.html', article=article,
                                           verification_statuses=VERIFICATION_STATUSES,
                                           publication_statuses=PUBLICATION_STATUSES,
                                           error='Holat qiymati noto\'g\'ri.'), 400
                cur.execute("""UPDATE scientific_articles
                                  SET verification_status = %s, publication_status = %s,
                                      permission_note = COALESCE(NULLIF(%s, ''), permission_note),
                                      reviewed_by = %s, reviewed_at = NOW(), updated_at = NOW()
                                WHERE id = %s""",
                            (verification, publication, note, current_user.id, article_id))
                conn.commit()
                flash('Maqola holati yangilandi.')
                return redirect('/admin/articles')
    finally:
        conn.close()
    return render_template('admin/article_review.html', article=article,
                           verification_statuses=VERIFICATION_STATUSES,
                           publication_statuses=PUBLICATION_STATUSES, error=None)


@articles_bp.route('/admin/articles/<int:article_id>/preview')
@login_required
def admin_article_preview(article_id):
    """Publish qilishdan oldin ko'rish — AYNAN public shablon (§28).

    Alohida ikkinchi dizayn yaratilmaydi; farq faqat draft badge va
    `<meta name="robots" content="noindex">` da.
    """
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            article = _load_article(cur, article_id=article_id, public_only=False)
            if article is None:
                abort(404)
            rows_by_id = {r['id']: r for r in _load_fields(cur, only_active=False)}
            crumbs = _primary_field_crumbs(rows_by_id, article)
    finally:
        conn.close()
    return render_template('maqolalar_detail.html', article=article,
                           field_crumbs=crumbs, preview=True)


@articles_bp.route('/admin/articles/<int:article_id>/delete', methods=['POST'])
@login_required
def admin_article_delete(article_id):
    """Arxivlash — qattiq o'chirish emas (§12).

    Loyiha siyosati (grants: is_active=FALSE, dissertations: merged_into_id)
    ma'lumotni yo'q qilmaslik. Maqola `archived` bo'ladi va katalogdan
    chiqadi; PDF fayl diskda qoladi va qo'lda ko'rib chiqiladi.
    """
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("""UPDATE scientific_articles
                              SET publication_status = 'archived', updated_at = NOW()
                            WHERE id = %s""", (article_id,))
            conn.commit()
    finally:
        conn.close()
    flash('Maqola arxivlandi (fayl o\'chirilmadi).')
    return redirect('/admin/articles')


# ═══════════════════════════════════════════════════════════════════════════
#  Admin — fan yo'nalishlari
# ═══════════════════════════════════════════════════════════════════════════

ADMIN_FIELDS_PER_PAGE = 50

REVIEW_STATUS_LABELS = {
    'auto_clean': 'Avtomatik (toza)',
    'auto_normalized': 'Avtomatik (formatlangan)',
    'manual_approved': "Qo'lda tasdiqlangan",
    'seed': 'Repozitoriy seed',
}


@articles_bp.route('/admin/scientific-fields')
@login_required
def admin_fields():
    """Taksonomiya boshqaruvi — sahifalangan jadval + daraxt ko'rinishi.

    OAK dataseti bilan bu jadvalda 275+ yozuv bo'ladi, shuning uchun
    hammasi bir sahifaga render QILINMAYDI (§18): default — filtrlanadigan,
    sahifalangan tekis jadval. Daraxt ko'rinishi `?view=daraxt` bilan
    ochiladi va u ham progressive disclosure ishlatadi.
    """
    _require_admin()
    view_mode = _choice_arg('view', ('jadval', 'daraxt'), 'jadval')
    page = _int_arg('page', 1, minimum=1, maximum=MAX_PAGE) or 1
    query = (request.args.get('q') or '').strip()[:120]
    code_query = (request.args.get('code') or '').strip()[:32]
    level = _int_arg('level', minimum=1, maximum=6)
    review_status = (request.args.get('review') or '').strip()[:24]
    source_query = (request.args.get('source') or '').strip()[:120]
    status_filter = (request.args.get('status') or '').strip()
    missing_only = _flag_arg('yetim')

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            tree, rows = _field_tree(cur, only_active=False)
    finally:
        conn.close()

    rows_by_id = {r['id']: r for r in rows}
    sources = sorted({(r.get('source_name') or '').strip()
                      for r in rows if (r.get('source_name') or '').strip()})
    review_values = sorted({(r.get('review_status') or '').strip()
                            for r in rows if (r.get('review_status') or '').strip()})

    from utils.search_helper import matches_query
    filtered = []
    for row in rows:
        if code_query and not (row.get('code') or '').startswith(code_query):
            continue
        if query and not matches_query(query, row.get('name_uz'),
                                       row.get('name_uz_cyrillic'),
                                       row.get('raw_name')):
            continue
        if level and (row.get('level') or 0) != level:
            continue
        if review_status and (row.get('review_status') or '') != review_status:
            continue
        if source_query and source_query.lower() not in (row.get('source_name') or '').lower():
            continue
        if status_filter == 'faol' and not row.get('is_active'):
            continue
        if status_filter == 'nofaol' and row.get('is_active'):
            continue
        if missing_only and not row.get('missing_parent_code'):
            continue
        entry = dict(row)
        entry['ancestors'] = _field_breadcrumb(rows_by_id, row)
        filtered.append(entry)

    filtered.sort(key=lambda r: (r.get('code') or 'zz', r.get('name_uz') or ''))
    total = len(filtered)
    pages = max(1, math.ceil(total / ADMIN_FIELDS_PER_PAGE))
    page = min(page, pages)
    start = (page - 1) * ADMIN_FIELDS_PER_PAGE
    page_rows = filtered[start:start + ADMIN_FIELDS_PER_PAGE]

    return render_template('admin/scientific_fields.html', field_tree=tree,
                           field_total=len(rows), rows=page_rows, total=total,
                           page=page, pages=pages, view_mode=view_mode,
                           sources=sources, review_values=review_values,
                           review_labels=REVIEW_STATUS_LABELS,
                           missing_parent_total=sum(
                               1 for r in rows if r.get('missing_parent_code')),
                           qs=_query_string)


def review_queue_path():
    """Parser chiqargan review navbati fayli (§13)."""
    from flask import current_app
    configured = os.environ.get('OAK_REVIEW_FILE')
    if configured:
        return configured
    return os.path.join(current_app.root_path, 'data', 'oak', 'review',
                        'oak_specialties_review_required.json')


@articles_bp.route('/admin/scientific-fields/review')
@login_required
def admin_field_review():
    """OAK review navbati — FAQAT KO'RISH.

    Bu ataylab yangi moderatsiya tizimi EMAS (§13). Tasdiqlash ish oqimi
    fayl ustida: admin `approved_name_uz_cyrillic`, `reviewer` va
    `reviewed_at` maydonlarini to'ldiradi, so'ng parser qayta ishga
    tushiriladi va u tasdiqlangan yozuvni approved datasetga o'tkazadi.
    Sahifa shu jarayon uchun kerak bo'lgan hamma narsani ko'rsatadi: shifr,
    asl nom, normalizatsiya nomzodi, manba qatori va sabablar.

    Yozuvlar bazada YO'Q — ular ataylab import qilinmagan.
    """
    _require_admin()
    path = review_queue_path()
    records, error, metadata = [], None, {}
    if not os.path.isfile(path):
        error = ("Review fayli topilmadi: %s — avval "
                 "scripts/parse_oak_specialties.py ni ishga tushiring." % path)
    else:
        try:
            import json
            with open(path, encoding='utf-8') as fh:
                doc = json.load(fh)
            metadata = doc.get('metadata') or {}
            records = doc.get('records') or []
        except (ValueError, OSError):
            log_exception(current_app.logger, 'OAK review faylini o\'qib bo\'lmadi')
            error = "Review faylini o'qib bo'lmadi (format buzilgan bo'lishi mumkin)."

    pending = [r for r in records if not (r.get('approved_name_uz_cyrillic')
                                          and r.get('reviewer')
                                          and r.get('reviewed_at'))]
    approved = [r for r in records if r not in pending]
    return render_template('admin/scientific_field_review.html',
                           pending=pending, approved=approved, metadata=metadata,
                           error=error, queue_path=path)


def _field_form_values():
    form = request.form
    get = lambda k: (form.get(k) or '').strip()
    return {
        'name_uz': get('name_uz')[:300],
        'name_uz_cyrillic': get('name_uz_cyrillic')[:300] or None,
        'name_ru': get('name_ru')[:300] or None,
        'code': normalize_code(get('code')),
        'parent_id': get('parent_id'),
        'description': get('description') or None,
        'sort_order': get('sort_order'),
        'is_active': bool(get('is_active')),
        'source_name': get('source_name')[:300] or None,
        'source_url': get('source_url')[:1000] or None,
        'source_updated_at': get('source_updated_at') or None,
    }


def _validate_field(cur, values, field_id=None):
    if not values['name_uz']:
        raise FieldValidationError('Yo\'nalish nomi majburiy.', 'name_uz')

    parent_id = None
    parent_level = None
    if values['parent_id']:
        try:
            parent_id = int(values['parent_id'])
        except ValueError:
            raise FieldValidationError('Ota yo\'nalish noto\'g\'ri.', 'parent_id')
        cur.execute("SELECT level FROM scientific_fields WHERE id = %s", (parent_id,))
        row = cur.fetchone()
        if not row:
            raise FieldValidationError('Ota yo\'nalish topilmadi.', 'parent_id')
        parent_level = row[0]

    cur.execute("SELECT id, parent_id FROM scientific_fields")
    parent_of = {r[0]: r[1] for r in cur.fetchall()}
    check_no_cycle(field_id, parent_id, parent_of)

    if values['code']:
        if field_id:
            cur.execute("SELECT 1 FROM scientific_fields WHERE code = %s AND id <> %s",
                        (values['code'], field_id))
        else:
            cur.execute("SELECT 1 FROM scientific_fields WHERE code = %s", (values['code'],))
        if cur.fetchone():
            raise FieldValidationError('Bu shifr allaqachon band.', 'code')

    try:
        values['sort_order'] = int(values['sort_order'] or 0)
    except ValueError:
        values['sort_order'] = 0
    if values['source_updated_at']:
        try:
            values['source_updated_at'] = date.fromisoformat(values['source_updated_at'])
        except ValueError:
            raise FieldValidationError('Manba sanasi formati noto\'g\'ri.',
                                       'source_updated_at')
    values['parent_id'] = parent_id
    values['level'] = resolve_level(values['code'], parent_level)
    return values


FIELD_WRITE_COLUMNS = ('parent_id', 'name_uz', 'name_uz_cyrillic', 'name_ru', 'code',
                       'level', 'description', 'sort_order', 'is_active',
                       'source_name', 'source_url', 'source_updated_at')


@articles_bp.route('/admin/scientific-fields/new', methods=['GET', 'POST'])
@login_required
def admin_field_new():
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            _tree, rows = _field_tree(cur, only_active=False)
            if request.method == 'GET':
                return render_template('admin/scientific_field_form.html', edit_mode=False,
                                       field_rows=rows, values=None, error=None)
            values = _field_form_values()
            try:
                _validate_field(cur, values)
                slug = _unique_slug(cur, 'scientific_fields',
                                    field_slug(values['name_uz'], values['code']))
                columns = list(FIELD_WRITE_COLUMNS) + ['slug']
                data = [values[c] for c in FIELD_WRITE_COLUMNS] + [slug]
                cur.execute(f"INSERT INTO scientific_fields ({', '.join(columns)}) "
                            f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id", data)
                conn.commit()
            except FieldValidationError as exc:
                conn.rollback()
                return render_template('admin/scientific_field_form.html', edit_mode=False,
                                       field_rows=rows, values=values,
                                       error=exc.public_message), 400
    finally:
        conn.close()
    flash('Yo\'nalish qo\'shildi.')
    return redirect('/admin/scientific-fields')


@articles_bp.route('/admin/scientific-fields/<int:field_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_field_edit(field_id):
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            conn.commit()
            _tree, rows = _field_tree(cur, only_active=False)
            existing = next((r for r in rows if r['id'] == field_id), None)
            if existing is None:
                abort(404)
            if request.method == 'GET':
                return render_template('admin/scientific_field_form.html', edit_mode=True,
                                       field_rows=rows, values=existing, error=None,
                                       field_id=field_id)
            values = _field_form_values()
            try:
                _validate_field(cur, values, field_id=field_id)
                assignments = [f'{c} = %s' for c in FIELD_WRITE_COLUMNS] + ['updated_at = NOW()']
                data = [values[c] for c in FIELD_WRITE_COLUMNS]
                cur.execute(f"UPDATE scientific_fields SET {', '.join(assignments)} "
                            f"WHERE id = %s", data + [field_id])
                conn.commit()
            except FieldValidationError as exc:
                conn.rollback()
                return render_template('admin/scientific_field_form.html', edit_mode=True,
                                       field_rows=rows, values=values, field_id=field_id,
                                       error=exc.public_message), 400
    finally:
        conn.close()
    flash('Yo\'nalish saqlandi.')
    return redirect('/admin/scientific-fields')


@articles_bp.route('/admin/scientific-fields/<int:field_id>/delete', methods=['POST'])
@login_required
def admin_field_delete(field_id):
    """Bolalari yoki maqolalari bor yo'nalish o'chirilmaydi — nofaol qilinadi (§4)."""
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("SELECT COUNT(*) FROM scientific_fields WHERE parent_id = %s",
                        (field_id,))
            children = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM article_scientific_fields "
                        "WHERE scientific_field_id = %s", (field_id,))
            linked = int(cur.fetchone()[0])
            if children or linked:
                cur.execute("UPDATE scientific_fields SET is_active = FALSE, "
                            "updated_at = NOW() WHERE id = %s", (field_id,))
                conn.commit()
                flash('Yo\'nalishda ichki yo\'nalish yoki maqola bor — o\'chirilmadi, '
                      'nofaol qilindi.')
            else:
                cur.execute("DELETE FROM scientific_fields WHERE id = %s", (field_id,))
                conn.commit()
                flash('Yo\'nalish o\'chirildi.')
    finally:
        conn.close()
    return redirect('/admin/scientific-fields')
