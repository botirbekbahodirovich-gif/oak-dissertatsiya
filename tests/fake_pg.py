# -*- coding: utf-8 -*-
"""Maqolalar route testlari uchun minimal psycopg2 o'rnini bosuvchi.

NEGA: lokal Postgres yo'q (Neon kredensiallari eskirgan — bu alohida texnik
qarz sifatida hujjatlashtirilgan, docs/articles-module.md). Haqiqiy SQL
semantikasi (WHERE filtri, ORDER BY, LIMIT) TEST_DATABASE_URL bilan staging'da
tests/test_articles_dbgated.py da tekshiriladi.

Bu qalbaki kursor FILTRLAMAYDI — u faqat qatorlarni qaytaradi. Shuning uchun
testlar filtr/xavfsizlik semantikasini YUBORILGAN SQL matni ustida tekshiradi
(masalan "public WHERE har bir ommaviy so'rovda bormi"), qolganini esa
render qilingan HTML ustida. Bu chegara ataylab ochiq qoldirilgan: qalbaki DB
haqiqiy DB o'rnini bosgandek ko'rinmasligi kerak.
"""
from blueprints.articles import ARTICLE_COLUMNS

ARTICLE_FIELDS = [c.strip().split('.')[-1] for c in ARTICLE_COLUMNS.split(',') if c.strip()]

FIELD_ROW_COLUMNS = ['id', 'parent_id', 'name_uz', 'name_uz_cyrillic', 'name_ru',
                     'code', 'slug', 'level', 'description', 'sort_order',
                     'is_active', 'source_name', 'source_url', 'source_updated_at',
                     'article_count']

AUTHOR_ROW_COLUMNS = ['article_id', 'author_name', 'author_order', 'orcid',
                      'affiliation', 'linked_user_id', 'linked_scientist_id',
                      'is_corresponding']

LINK_ROW_COLUMNS = ['article_id', 'is_primary', 'id', 'name_uz', 'slug', 'code', 'level']

_WRITE_PREFIXES = ('CREATE', 'ALTER', 'DROP', 'INSERT', 'UPDATE', 'DELETE', 'SET')


def article(**overrides):
    """To'liq ustunli maqola qatori — sukut bo'yicha ommaviy va PDF ochiq."""
    row = {name: None for name in ARTICLE_FIELDS}
    row.update({
        'id': 1, 'title': 'Ochiq maqola', 'slug': 'ochiq-maqola',
        'abstract': 'Annotatsiya matni.', 'keywords': 'ta\'lim, raqamlashtirish',
        'language': 'uz', 'publication_year': 2024, 'journal_name': 'Ilm Sarchashmalari',
        'doi': '10.1234/abc.2024', 'pdf_path': '2026/08/a.pdf',
        'pdf_original_name': 'maqola.pdf', 'pdf_size': 524288,
        'license_type': 'cc_by', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
        'access_type': 'open_access', 'version_type': 'version_of_record',
        'source_type': 'open_journal', 'source_name': 'Ilm Sarchashmalari',
        'index_scopus': False, 'index_wos': False, 'index_doaj': True,
        'verification_status': 'verified', 'publication_status': 'published',
        'is_featured': False, 'view_count': 12, 'download_count': 3,
        'created_by': 1,
    })
    row.update(overrides)
    return row


def field(**overrides):
    row = {
        'id': 1, 'parent_id': None, 'name_uz': 'Pedagogika fanlari',
        'name_uz_cyrillic': None, 'name_ru': None, 'code': '13.00.00',
        'slug': 'pedagogika-fanlari-130000', 'level': 1, 'description': None,
        'sort_order': 130, 'is_active': True, 'source_name': 'OAK', 'source_url': None,
        'source_updated_at': None, 'article_count': 1,
    }
    row.update(overrides)
    return row


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._rows = []
        self.description = None

    # ── context manager ──
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    # ── psycopg2 yuzasi ──
    def execute(self, sql, params=None):
        normalized = ' '.join(str(sql).split())
        self.store['sql'].append((normalized, params))
        self._rows, self.description = self._dispatch(normalized, params)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    # ── dispatch ──
    def _dispatch(self, sql, params):
        upper = sql.upper()
        if upper.startswith(_WRITE_PREFIXES):
            if 'RETURNING' in upper:
                return [(self.store.get('new_id', 101),)], [('id',)]
            return [], None

        if 'FROM PG_EXTENSION' in upper:
            return ([(1,)] if self.store.get('pg_trgm') else []), [('x',)]

        if upper.startswith('SELECT COUNT(*)'):
            if 'FROM SCIENTIFIC_FIELDS' in upper:
                return [(self.store.get('child_count', 0),)], [('count',)]
            if 'FROM ARTICLE_SCIENTIFIC_FIELDS' in upper:
                return [(self.store.get('link_count', 0),)], [('count',)]
            return [(len(self.store['articles']),)], [('count',)]

        if 'MIN(A.PUBLICATION_YEAR)' in upper:
            return [(2015, 2026)], [('min',), ('max',)]

        if 'A.JOURNAL_NAME, COUNT(*)' in upper:
            names = sorted({a['journal_name'] for a in self.store['articles']
                            if a.get('journal_name')})
            return [(n, 1) for n in names], [('journal_name',), ('cnt',)]

        if sql.startswith('SELECT a.id, a.title, a.slug'):
            rows = [tuple(a[name] for name in ARTICLE_FIELDS)
                    for a in self.store['articles']]
            return rows, [(name,) for name in ARTICLE_FIELDS]

        if sql.startswith('SELECT article_id, author_name'):
            rows = [tuple(a.get(name) for name in AUTHOR_ROW_COLUMNS)
                    for a in self.store['authors']]
            return rows, [(name,) for name in AUTHOR_ROW_COLUMNS]

        if sql.startswith('SELECT asf.article_id'):
            rows = [tuple(link.get(name) for name in LINK_ROW_COLUMNS)
                    for link in self.store['links']]
            return rows, [(name,) for name in LINK_ROW_COLUMNS]

        if sql.startswith('SELECT f.id, f.parent_id'):
            rows = [tuple(f[name] for name in FIELD_ROW_COLUMNS)
                    for f in self.store['fields']]
            return rows, [(name,) for name in FIELD_ROW_COLUMNS]

        if sql.startswith('SELECT id, parent_id FROM scientific_fields'):
            return [(f['id'], f['parent_id']) for f in self.store['fields']], [('id',), ('parent_id',)]

        if sql.startswith('SELECT id FROM scientific_fields WHERE slug'):
            slug = (params or [None])[0]
            return [(f['id'],) for f in self.store['fields'] if f['slug'] == slug], [('id',)]

        if sql.startswith('SELECT publication_status, COUNT(*)'):
            return [('published', len(self.store['articles']))], [('s',), ('c',)]

        if sql.startswith('SELECT level FROM scientific_fields'):
            return [(1,)], [('level',)]

        if sql.startswith('SELECT 1 FROM scientific_fields WHERE id'):
            wanted = (params or [None])[0]
            return ([(1,)] if any(f['id'] == wanted for f in self.store['fields'])
                    else []), [('x',)]

        if sql.startswith('SELECT 1 FROM scientific_fields WHERE code'):
            return ([(1,)] if self.store.get('duplicate_code') else []), [('x',)]

        if sql.startswith('SELECT 1 FROM scientific_articles WHERE slug') \
                or sql.startswith('SELECT 1 FROM scientific_fields WHERE slug'):
            # Slug to'qnashuvi yo'q deb faraz qilinadi (aks holda cheksiz sikl).
            return [], [('x',)]

        if sql.startswith('SELECT id FROM scientific_articles WHERE doi'):
            return ([(99,)] if self.store.get('duplicate_doi') else []), [('id',)]

        if sql.startswith('SELECT id, title FROM scientific_articles WHERE checksum'):
            return ([(99, 'Boshqa maqola')] if self.store.get('duplicate_checksum')
                    else []), [('id',), ('title',)]

        return [], None


class FakeConnection:
    def __init__(self, store):
        self.store = store
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def new_store(articles=None, fields=None, authors=None, links=None, **extra):
    store = {
        'articles': list(articles if articles is not None else []),
        'fields': list(fields if fields is not None else []),
        'authors': list(authors if authors is not None else []),
        'links': list(links if links is not None else []),
        'sql': [],
    }
    store.update(extra)
    return store
