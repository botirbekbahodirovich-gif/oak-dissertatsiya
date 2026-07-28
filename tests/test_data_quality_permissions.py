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

    # -- duplicate review queue ----------------------------------------------
    def test_anonymous_queue_is_login_walled(self):
        r = self.client.get("/admin/data-quality/duplicates", follow_redirects=False)
        self.assertIn(r.status_code, (302, 401))

    def test_nonadmin_queue_403(self):
        self._login(is_admin=False)
        r = self.client.get("/admin/data-quality/duplicates")
        self.assertEqual(r.status_code, 403)

    def test_admin_queue_passes_authz(self):
        self._login(is_admin=True)
        r = self.client.get("/admin/data-quality/duplicates")
        self.assertNotEqual(r.status_code, 403)
        self.assertNotIn(r.status_code, (302, 401))

    def test_nonadmin_review_403(self):
        """CSRF is checked before the view, so a tokenless POST 400s whoever
        sends it. Turn CSRF off for this one case to prove the endpoint is also
        admin-gated — a non-admin holding a valid token must still be refused."""
        self.addCleanup(app.config.__setitem__, "WTF_CSRF_ENABLED",
                        app.config.get("WTF_CSRF_ENABLED", True))
        app.config["WTF_CSRF_ENABLED"] = False
        self._login(is_admin=False)
        r = self.client.post("/admin/data-quality/duplicate/1/review",
                             data={"status": "confirmed"})
        self.assertEqual(r.status_code, 403)

    def test_review_rejects_merged_status(self):
        """'merged' must not be settable from the review screen — merging is a
        separate job, and this endpoint never touches the records themselves."""
        self.addCleanup(app.config.__setitem__, "WTF_CSRF_ENABLED",
                        app.config.get("WTF_CSRF_ENABLED", True))
        app.config["WTF_CSRF_ENABLED"] = False
        self._login(is_admin=True)
        r = self.client.post("/admin/data-quality/duplicate/1/review",
                             data={"status": "merged"})
        self.assertEqual(r.status_code, 400)

    # -- CSRF ----------------------------------------------------------------
    def test_post_without_csrf_rejected(self):
        self._login(is_admin=True)
        r = self.client.post("/admin/data-quality/scan", data={"limit": "10"})
        self.assertEqual(r.status_code, 400)  # CSRFProtect blocks the tokenless POST

    def test_ajax_review_without_csrf_rejected(self):
        """The keyboard queue posts via fetch(); it must still carry a token."""
        self._login(is_admin=True)
        r = self.client.post("/admin/data-quality/duplicate/1/review",
                             data={"status": "confirmed"},
                             headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 400)


class TemplateRenderTests(unittest.TestCase):
    """The queue page cannot be opened locally (no DB), so render it directly
    with the shapes the blueprint passes. Catches template errors that would
    otherwise only show up on staging."""

    def _candidates(self):
        from data_quality import duplicate_review as review
        row = {
            "id": 5, "record_id_1": 11, "record_id_2": 22, "score": 0.94,
            "match_level": "strong", "status": "pending",
            "reasons": ["same author", "title similarity 0.94"],
            "created_at": None, "reviewed_at": None,
            "r1_mavzu": "Sun'iy intellekt asoslari", "r2_mavzu": "Sun'iy intellekt asoslari",
            "r1_olim": "Aliyev Vali", "r2_olim": "Aliev Vali",
            "r1_ilmiy_rahbar": "Yusupov R", "r2_ilmiy_rahbar": "",
            "r1_muassasa": "TATU", "r2_muassasa": "TATU",
            "r1_ixtisoslik": "05.01.02", "r2_ixtisoslik": "05.01.02",
            "r1_sana": "10.03.2020", "r2_sana": "12.03.2020",
            "r1_link": "https://oak.uz/pages/1", "r2_link": "",
        }
        return [review.decorate(row)]

    def _render(self, **overrides):
        from flask import render_template

        from data_quality import duplicate_review as review
        ctx = dict(
            tables_ready=True, candidates=self._candidates(),
            progress=review.progress_view([
                {"match_level": "exact", "status": "confirmed", "count": 100},
                {"match_level": "exact", "status": "pending", "count": 8},
                {"match_level": "possible", "status": "pending", "count": 1050},
            ]),
            filters={"status": "pending"}, page=1, total_pages=3, total=1597,
            level_labels=review.LEVEL_LABEL, status_labels=review.STATUS_LABEL,
            bucket_labels=review.BUCKET_LABEL)
        ctx.update(overrides)
        with app.test_request_context("/admin/data-quality/duplicates"):
            return render_template("admin_duplicate_review.html", **ctx)

    def test_renders_with_candidates(self):
        html = self._render()
        self.assertIn("Dublikat nomzodlar navbati", html)
        self.assertIn("intellekt asoslari", html)
        self.assertIn("data-id=\"5\"", html)

    def test_record_values_are_html_escaped(self):
        """Dissertation text is rendered as data, never as markup."""
        html = self._render()
        self.assertIn("Sun&#39;iy", html)          # apostrophe escaped
        self.assertNotIn("Sun'iy intellekt", html)

    def test_injected_markup_in_a_record_is_not_executed(self):
        from data_quality import duplicate_review as review
        row = {
            "id": 9, "record_id_1": 1, "record_id_2": 2, "score": 0.9,
            "match_level": "possible", "status": "pending", "reasons": [],
            "r1_mavzu": "<script>alert(1)</script>", "r2_mavzu": "boshqa mavzu",
        }
        html = self._render(candidates=[review.decorate(row)])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_differing_fields_are_marked(self):
        html = self._render()
        self.assertIn("f-differs", html)      # olim: Aliyev vs Aliev
        self.assertIn("f-one_sided", html)    # ilmiy_rahbar only on one side

    def test_csrf_token_is_the_raw_value_not_an_input_element(self):
        """The fetch() call posts the token in the body; csrf_token() would
        inject a whole <input> and break the script."""
        html = self._render()
        self.assertNotIn('var CSRF = "<input', html)
        self.assertRegex(html, r'var CSRF = "[^"<>]+";')

    def test_renders_empty_queue(self):
        html = self._render(candidates=[], total=0)
        self.assertIn("mos nomzod yo‘q", html)

    def test_renders_without_tables(self):
        html = self._render(tables_ready=False, candidates=[], progress=None)
        self.assertIn("004-migratsiyani", html)


if __name__ == "__main__":
    unittest.main()
