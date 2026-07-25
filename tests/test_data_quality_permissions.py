"""Authorization + CSRF tests for the admin Data Quality dashboard.

Uses the Flask test client in APP_ENV=testing (DB disabled). The 403/redirect/
CSRF paths all resolve BEFORE any database access, so they run with no DB. The
admin *success* path needs a database and is covered by the staging runbook.
"""
import os
import unittest

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "dq-perms-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

from app import app, login_manager  # noqa: E402


class _FakeUser:
    def __init__(self, is_admin):
        self.is_admin = is_admin
        self.id = 1

    is_authenticated = True
    is_active = True
    is_anonymous = False

    def get_id(self):
        return "1"


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self._orig_loader = login_manager._user_callback
        self._orig_protection = login_manager.session_protection
        login_manager.session_protection = None  # skip _id fingerprint check

    def tearDown(self):
        login_manager._user_callback = self._orig_loader
        login_manager.session_protection = self._orig_protection

    def _login(self, is_admin):
        user = _FakeUser(is_admin)
        login_manager._user_callback = lambda uid: user
        with self.client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

    # -- anonymous -----------------------------------------------------------
    def test_anonymous_is_login_walled(self):
        r = self.client.get("/admin/data-quality", follow_redirects=False)
        self.assertIn(r.status_code, (302, 401))

    # -- non-admin -----------------------------------------------------------
    def test_nonadmin_dashboard_403(self):
        self._login(is_admin=False)
        r = self.client.get("/admin/data-quality")
        self.assertEqual(r.status_code, 403)

    def test_nonadmin_export_403(self):
        self._login(is_admin=False)
        r = self.client.get("/admin/data-quality/export.csv")
        self.assertEqual(r.status_code, 403)

    # -- admin passes authorization (body may 500 without a DB) --------------
    def test_admin_passes_authz(self):
        self._login(is_admin=True)
        r = self.client.get("/admin/data-quality")
        self.assertNotEqual(r.status_code, 403)
        self.assertNotIn(r.status_code, (302, 401))

    # -- CSRF ----------------------------------------------------------------
    def test_post_without_csrf_rejected(self):
        self._login(is_admin=True)
        r = self.client.post("/admin/data-quality/scan", data={"limit": "10"})
        self.assertEqual(r.status_code, 400)  # CSRFProtect blocks the tokenless POST


if __name__ == "__main__":
    unittest.main()
