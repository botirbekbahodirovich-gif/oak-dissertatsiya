# -*- coding: utf-8 -*-
"""`scientific_fields` jadvalining xotiradagi minimal modeli.

Seed/import testlari uchun: haqiqiy Postgres yo'q (lokal Neon kredensiallari
eskirgan), lekin upsert mantiqi — idempotentlik, ota-bola bog'lash, konflikt
aniqlash — DB'siz to'liq tekshirilishi mumkin. Haqiqiy SQL semantikasi
staging'da (tests/test_articles_dbgated.py).

Bu kursor `scripts/seed_scientific_fields.py` yuboradigan AYNAN uchta so'rov
shaklini biladi; boshqa so'rov bo'sh natija qaytaradi.
"""
from scripts.seed_scientific_fields import WRITE_COLUMNS


class SeedCursor:
    def __init__(self, rows=None, article_links=0):
        self.rows = list(rows or [])
        self._result = []
        self._next_id = max([r['id'] for r in self.rows], default=0) + 1
        self.writes = 0
        # Maqola<->yo'nalish bog'lanishlari soni. Ierarxiya tuzatish ularga
        # TEGMASLIGI kerak, shuning uchun bu son o'zgarmasligi tekshiriladi.
        self.article_links = article_links

    def execute(self, sql, params=None):
        sql = ' '.join(str(sql).split())
        params = list(params or [])

        if sql.startswith('SELECT COUNT(*) FROM article_scientific_fields'):
            self._result = [(self.article_links,)]
        elif sql.startswith('SELECT id, code, parent_id, level, missing_parent_code'):
            self._result = [(r['id'], r.get('code'), r.get('parent_id'),
                             r.get('level'), r.get('missing_parent_code'),
                             r.get('name_uz')) for r in self.rows]
        elif sql.startswith('SELECT id, code, slug, name_uz, description, parent_id, level'):
            self._result = [(r['id'], r['code'], r['slug'], r['name_uz'],
                             r.get('description'), r.get('parent_id'),
                             r.get('level'), r.get('review_status'),
                             r.get('source_sha256')) for r in self.rows]
        elif sql.startswith('SELECT 1 FROM scientific_fields WHERE slug'):
            self._result = [(1,) for r in self.rows if r['slug'] == params[0]]
        elif sql.startswith('INSERT INTO scientific_fields'):
            self.writes += 1
            row = dict(zip(WRITE_COLUMNS, params))
            row['id'] = self._next_id
            self._next_id += 1
            self.rows.append(row)
            self._result = [(row['id'],)]
        elif sql.startswith('UPDATE scientific_fields SET parent_id = %s, level = %s,'):
            # repair_hierarchy: faqat ota, daraja va bayroq yangilanadi.
            self.writes += 1
            parent_id, level, target = params
            for row in self.rows:
                if row['id'] == target:
                    row.update(parent_id=parent_id, level=level,
                               missing_parent_code=None)
            self._result = []
        elif sql.startswith('UPDATE scientific_fields'):
            self.writes += 1
            target = params[-1]
            for row in self.rows:
                if row['id'] == target:
                    row.update(name_uz=params[0], name_uz_cyrillic=params[1],
                               name_ru=params[2], description=params[3],
                               parent_id=params[4], level=params[5],
                               sort_order=params[6], source_name=params[7],
                               source_url=params[8], source_updated_at=params[9],
                               raw_name=params[10], source_line=params[11],
                               source_sha256=params[12], review_status=params[13],
                               missing_parent_code=None)
            self._result = []
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)
