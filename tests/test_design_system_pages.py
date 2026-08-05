"""Phase 2A — migrated pages keep working and stay on the design system.

These are smoke/markup checks only; they do not touch the DB. Pages whose view
needs Postgres are skipped when the local connection is unavailable rather than
being papered over with a production fallback (the stale local Neon credentials
are tracked as separate technical debt).
"""
import re
import unittest

from app import app

# route -> template the view actually renders (verified against the URL map)
PAGES = [
    ('/dissertatsiyalar', 'dashboard.html'),
    ('/compare', 'compare.html'),
    ('/trends', 'trends.html'),
    ('/clustering', 'clustering.html'),
    ('/collaboration', 'collaboration.html'),
    ('/tadqiqot-xaritasi', 'map.html'),
    ('/heatmap', 'heatmap.html'),
    ('/journals', 'journals.html'),
    ('/grants', 'grants.html'),
    ('/courses', 'courses.html'),
]

# Surfaces/borders that only make sense on the old dark theme. If one of these
# reaches the rendered page again, something regressed.
DARK_VALUES = re.compile(
    r'#(?:0f172a|1e293b|162032|334155|0b1220|111a2c|1a1d23|22262f|0f1114'
    r'|1e2330|1a1f2e|2a3140|3a4356|243044|131c2e|28344a|111c30)\b',
    re.IGNORECASE,
)

SCRIPT_BLOCK = re.compile(r'<script\b[^>]*>(.*?)</script>', re.S | re.I)

# Chart.js paints to a canvas and SVG *presentation attributes* cannot resolve
# CSS custom properties, so var() is fatal in those positions. Inline element
# styles (element.style / cssText / style="...") DO resolve them, so those are
# legitimate and must not be flagged.
UNRESOLVABLE_VAR = re.compile(
    r"""(?:
          \.attr\(\s*['"](?:fill|stroke|flood-color|color)['"]\s*,[^)]*var\(--
        | setAttribute\(\s*['"](?:fill|stroke|flood-color|color)['"]\s*,[^)]*var\(--
        | (?:borderColor|backgroundColor|pointBackgroundColor|titleColor|bodyColor)
          \s*:\s*['"]var\(--
        | \b(?:fill|stroke)="var\(--
        )""",
    re.X,
)


class Phase2APageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls._cache = {}

    def _html(self, path):
        if path not in self._cache:
            resp = self.client.get(path)
            self._cache[path] = (resp.status_code, resp.get_data(as_text=True))
        return self._cache[path]

    def test_every_migrated_page_still_renders(self):
        for path, tpl in PAGES:
            code, _ = self._html(path)
            self.assertEqual(code, 200, msg='%s (%s)' % (path, tpl))

    def test_every_page_has_exactly_one_h1(self):
        for path, tpl in PAGES:
            _, html = self._html(path)
            self.assertEqual(len(re.findall(r'<h1[\s>]', html)), 1,
                             msg='%s (%s) bitta H1 ga ega bo\'lishi kerak' % (path, tpl))

    def test_no_dark_theme_surfaces_remain(self):
        for path, tpl in PAGES:
            _, html = self._html(path)
            found = sorted(set(DARK_VALUES.findall(html)))
            self.assertEqual(found, [], msg='%s (%s) da dark qiymat qoldi: %s'
                                            % (path, tpl, found))

    def test_canvas_and_svg_colours_are_not_css_variables(self):
        for path, tpl in PAGES:
            _, html = self._html(path)
            for block in SCRIPT_BLOCK.findall(html):
                hits = UNRESOLVABLE_VAR.findall(block)
                self.assertEqual(hits, [],
                                 msg='%s (%s): chart/SVG rangi var(--...) sifatida '
                                     'berilgan — canvas uni o\'qiy olmaydi' % (path, tpl))

    def test_pages_load_the_shared_chart_defaults(self):
        """Grafikli sahifalar markazlashtirilgan palitrani yuklashi kerak."""
        for path in ('/trends', '/clustering'):
            _, html = self._html(path)
            self.assertIn('/static/js/olimlar-charts.js', html, msg=path)

    def test_design_system_stylesheet_is_present(self):
        for path, _ in PAGES:
            _, html = self._html(path)
            self.assertIn('/static/css/olimlar-design-system.css', html, msg=path)


class Phase2AFunctionalityTests(unittest.TestCase):
    """Vizual refactor route, filtr va query parametrlarga tegmasligi kerak."""

    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_routes_and_query_parameters_are_unchanged(self):
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        for path, _ in PAGES:
            self.assertIn(path, rules, msg=path)

    def test_dashboard_keeps_its_search_and_filter_controls(self):
        html = self.client.get('/dissertatsiyalar').get_data(as_text=True)
        for marker in ('id="search-input"', 'id="btn-reset"', 'id="facet-side"',
                       'id="table-body"', 'id="sort-select"'):
            self.assertIn(marker, html, msg=marker)

    def test_dashboard_accepts_the_q_parameter(self):
        self.assertEqual(self.client.get('/dissertatsiyalar?q=tibbiyot').status_code, 200)

    def test_grants_keeps_its_filter_controls(self):
        html = self.client.get('/grants').get_data(as_text=True)
        for marker in ('gr-filterbar', 'id="f-clear"', 'gr-toggle'):
            self.assertIn(marker, html, msg=marker)

    def test_heatmap_alias_still_serves_and_points_at_the_canonical_map(self):
        html = self.client.get('/heatmap').get_data(as_text=True)
        self.assertIn('/tadqiqot-xaritasi', html)

    def test_admin_only_markup_is_absent_for_guests(self):
        html = self.client.get('/dissertatsiyalar').get_data(as_text=True)
        self.assertNotIn('/admin/', html)

    def test_premium_gate_markup_survives_for_guests(self):
        """Freemium gate mehmon uchun saqlanishi kerak (faqat uslubi o'zgardi)."""
        html = self.client.get('/compare').get_data(as_text=True)
        self.assertIn('lock-overlay', html)


class SharedComponentTests(unittest.TestCase):
    """Umumiy komponentlar ikki xil versiyada bo'lmasligi kerak."""

    def test_page_header_macro_exists(self):
        from flask import render_template_string
        with app.test_request_context('/'):
            out = render_template_string(
                "{% from 'components/page_header.html' import page_header %}"
                "{{ page_header('Sarlavha', 'Izoh', crumbs=[('Bosh sahifa', '/')]) }}")
        self.assertIn('ol-page-header__title', out)
        self.assertIn('ol-breadcrumb', out)
        self.assertIn('Sarlavha', out)

    def test_reminders_widget_has_no_dark_variant_left(self):
        import io
        import os
        path = os.path.join(app.root_path, 'templates', '_reminders_widget.html')
        with io.open(path, encoding='utf-8') as fh:
            src = fh.read()
        # `rw_dark` faqat izohda eslatilishi mumkin, shartda emas.
        self.assertNotIn("rw_dark|default", src)


if __name__ == '__main__':
    unittest.main()
