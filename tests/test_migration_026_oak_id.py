"""026_dissertations_oak_id: bo'sh bazada dissertations.oak_id yaratiladi.

Bo'sh bazada startup migratsiyasi oak_id ustunisiz idx_dissertations_oak_id ni
yaratmoqchi bo'lib, butun tranzaksiyani bekor qilardi. Ilovani bo'sh bazada
ishga tushirish sinovi qo'lda o'tkazildi (docs/open-issues.md, 4-band); bu
testlar shu tuzatish qaytib buzilmasligini DB'siz tekshiradi.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(ROOT, 'migrations', '026_dissertations_oak_id.sql')


def _statements(path):
    with open(path, encoding='utf-8') as f:
        sql = re.sub(r'--[^\n]*', '', f.read())
    return [s.strip() for s in sql.split(';') if s.strip()]


class Migration026Tests(unittest.TestCase):
    def test_migration_is_additive_and_idempotent(self):
        statements = _statements(MIGRATION)
        self.assertEqual(statements[0].upper(), 'BEGIN')
        self.assertEqual(statements[-1].upper(), 'COMMIT')
        for stmt in statements[1:-1]:
            upper = ' '.join(stmt.upper().split())
            self.assertTrue(
                upper.startswith('ALTER TABLE DISSERTATIONS ADD COLUMN IF NOT EXISTS')
                or re.match(r'CREATE (UNIQUE )?INDEX IF NOT EXISTS ', upper), stmt)
            for word in ('DROP', 'DELETE', 'UPDATE', 'TRUNCATE', 'ALTER COLUMN'):
                self.assertNotIn(word, upper)

    def test_unique_index_reuses_the_prod_constraint_name(self):
        # Prod already has the UNIQUE constraint dissertations_oak_id_key; the
        # same name makes IF NOT EXISTS a no-op there, and ON CONFLICT (oak_id)
        # (scraper.py, upload.py, data.py) needs a unique index everywhere else.
        sql = ' '.join(open(MIGRATION, encoding='utf-8').read().split())
        self.assertIn('ADD COLUMN IF NOT EXISTS oak_id INTEGER', sql)
        self.assertIn('CREATE UNIQUE INDEX IF NOT EXISTS dissertations_oak_id_key '
                      'ON dissertations (oak_id)', sql)

    def test_startup_adds_oak_id_before_indexing_it(self):
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
            src = f.read()
        add = src.index('ADD COLUMN IF NOT EXISTS oak_id INTEGER')
        create_table = src.index('CREATE TABLE IF NOT EXISTS dissertations')
        index = src.index('"idx_dissertations_oak_id"')
        self.assertLess(create_table, add)
        self.assertLess(add, index)

    def test_registered_in_migrations_readme(self):
        with open(os.path.join(ROOT, 'migrations', 'README.md'), encoding='utf-8') as f:
            readme = f.read()
        self.assertIn('`026_dissertations_oak_id.sql`', readme)
        self.assertIn("Keyingi bo'sh raqam: 027", readme)


if __name__ == '__main__':
    unittest.main()
