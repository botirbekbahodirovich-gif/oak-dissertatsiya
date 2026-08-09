# -*- coding: utf-8 -*-
"""Dashboard filtr SQL qurilishi — sof funksiya, DB'siz (Phase 2C.3B, §28).

`persistence._dashboard_where()` matnini tekshiradi — xuddi
`tests/test_articles_catalog.py` mavjud maqolalar katalogi filtrlarini
YUBORILGAN SQL matni ustida tekshirgani kabi (haqiqiy `DISTINCT ON`/JOIN
semantikasi — staging'da, tests/test_article_sync_dashboard_dbgated.py).
"""
import unittest

from services.article_sync.persistence import _dashboard_where


class DefaultActionableTests(unittest.TestCase):
    def test_no_queue_status_defaults_to_actionable(self):
        where, params = _dashboard_where(None, False, False, False, None, None)
        self.assertIn("status = 'checked'", where)
        self.assertIn('NOT is_stale', where)
        self.assertIn('change_count > 0', where)
        self.assertEqual(params, [])

    def test_actionable_explicit_same_as_none(self):
        where_none, _ = _dashboard_where(None, False, False, False, None, None)
        where_explicit, _ = _dashboard_where('actionable', False, False, False, None, None)
        self.assertEqual(where_none, where_explicit)


class QueueStatusFilterTests(unittest.TestCase):
    def test_needs_review_excludes_critical(self):
        where, _ = _dashboard_where('needs_review', False, False, False, None, None)
        self.assertIn('critical_change_count = 0', where)
        self.assertIn('change_count > 0', where)

    def test_critical_filter(self):
        where, _ = _dashboard_where('critical', False, False, False, None, None)
        self.assertIn('critical_change_count > 0', where)
        self.assertIn('NOT is_stale', where)

    def test_no_changes_filter(self):
        where, _ = _dashboard_where('no_changes', False, False, False, None, None)
        self.assertIn('change_count = 0', where)

    def test_stale_filter(self):
        where, _ = _dashboard_where('stale', False, False, False, None, None)
        self.assertIn('is_stale', where)
        self.assertNotIn('NOT is_stale', where)

    def test_applied_filter_ignores_staleness(self):
        where, _ = _dashboard_where('applied', False, False, False, None, None)
        self.assertIn("status = 'applied'", where)
        self.assertNotIn('is_stale', where)

    def test_dismissed_filter(self):
        where, _ = _dashboard_where('dismissed', False, False, False, None, None)
        self.assertIn("status = 'dismissed'", where)

    def test_all_has_no_status_restriction(self):
        where, _ = _dashboard_where('all', False, False, False, None, None)
        self.assertNotIn('status', where)


class ToggleFilterTests(unittest.TestCase):
    def test_retraction_toggle(self):
        where, _ = _dashboard_where(None, True, False, False, None, None)
        self.assertIn('has_retraction_change', where)

    def test_citation_only_toggle_requires_single_change(self):
        where, _ = _dashboard_where(None, False, True, False, None, None)
        self.assertIn('has_citation_change', where)
        self.assertIn('change_count = 1', where)

    def test_provider_issue_toggle(self):
        where, _ = _dashboard_where(None, False, False, True, None, None)
        self.assertIn('has_provider_issue', where)

    def test_publication_status_uses_parameter_not_interpolation(self):
        where, params = _dashboard_where(None, False, False, False, 'published', None)
        self.assertIn('publication_status = %s', where)
        self.assertNotIn('published', where)   # qiymat matnga QUYILMAGAN
        self.assertEqual(params, ['published'])

    def test_actor_type_uses_parameter(self):
        where, params = _dashboard_where(None, False, False, False, None, 'system')
        self.assertIn('actor_type = %s', where)
        self.assertEqual(params, ['system'])

    def test_combined_filters_and_params_order(self):
        where, params = _dashboard_where('needs_review', True, False, False,
                                         'published', 'admin')
        self.assertIn('has_retraction_change', where)
        self.assertIn('publication_status = %s', where)
        self.assertIn('actor_type = %s', where)
        self.assertEqual(params, ['published', 'admin'])


if __name__ == '__main__':
    unittest.main()
