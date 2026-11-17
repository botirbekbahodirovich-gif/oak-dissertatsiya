"""Startup migratsiyalari (app._run_startup_migrations) faqat APP_ENV=development da.

Migratsiya 026 bilan birga app.py ga `ALTER TABLE dissertations ADD COLUMN IF
NOT EXISTS oak_id INTEGER` qo'shildi. Prod (APP_ENV=production) bu blokni
HECH QACHON ishga tushirmasligi kerak: prod sxemasi faqat migrations/*.sql va
deploy paketi orqali o'zgaradi.

Har holat alohida jarayonda tekshiriladi (app import qilinganda startup ishlaydi):
data.get_connection soxta ulanish bilan almashtiriladi, u bajarilgan SQL'ni
yozib oladi — haqiqiy bazaga hech qachon ulanilmaydi (DATABASE_URL ham
yopiq portga qaratilgan).
"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OAK_ID_SQL = 'ALTER TABLE dissertations ADD COLUMN IF NOT EXISTS oak_id INTEGER'

PROBE = r'''
import json, data
sql, connections = [], []
class Cur:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, q, p=None): sql.append(q)
    def fetchone(self): return (0,)
    def fetchall(self): return []
    def close(self): pass
class Conn:
    def cursor(self, *a, **k): return Cur()
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass
def fake_connection(*a, **k):
    connections.append(1)
    return Conn()
data.get_connection = fake_connection
import app
print('RESULT ' + json.dumps({'connections': len(connections), 'sql': sql}))
'''


def _import_app(app_env):
    env = {
        'PATH': os.environ.get('PATH', ''),
        'HOME': os.environ.get('HOME', ''),
        'APP_ENV': app_env,
        'SESSION_SECRET': 'app-env-lock-test-secret-0123456789',
        # Hech qachon ishlatilmaydi (get_connection soxta), lekin xato qilib
        # ishlatilsa ham hech qayerga ulanmaydi.
        'DATABASE_URL': 'postgresql://nobody:x@127.0.0.1:1/none',
    }
    proc = subprocess.run([sys.executable, '-c', PROBE], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    lines = [l for l in proc.stdout.splitlines() if l.startswith('RESULT ')]
    if proc.returncode != 0 or not lines:
        raise AssertionError('app import failed (%s): %s' % (app_env, proc.stderr[-2000:]))
    return json.loads(lines[-1][len('RESULT '):])


class StartupAppEnvLockTests(unittest.TestCase):
    def test_production_runs_no_startup_sql(self):
        result = _import_app('production')
        self.assertEqual(result['connections'], 0)
        self.assertEqual(result['sql'], [])

    def test_development_runs_the_oak_id_line(self):
        """Nazorat: xuddi shu sinov usuli development'da qatorni ko'radi —
        demak production'dagi bo'sh natija usulning kamchiligi emas."""
        result = _import_app('development')
        self.assertGreater(result['connections'], 0)
        self.assertIn(OAK_ID_SQL, result['sql'])

    def test_startup_is_called_only_under_development_guard(self):
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as f:
            src = f.read()
        calls = [i for i in range(len(src))
                 if src.startswith('_run_startup_migrations()', i)
                 and not src.startswith('def ', max(0, i - 4))]
        self.assertEqual(len(calls), 1, 'startup must be called from exactly one place')
        guard = src.rfind('\n', 0, src.rfind('\n', 0, calls[0]))
        self.assertIn('if APP_ENV == "development":', src[guard:calls[0]])


if __name__ == '__main__':
    unittest.main()
