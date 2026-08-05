import unittest

from app import app
from utils.navigation import (
    ACCOUNT_MENU,
    ADMIN_MENU,
    FOOTER_COLUMNS,
    PRIMARY_NAV,
    NavItem,
    item_is_active,
    section_is_active,
)


class NavigationConfigTests(unittest.TestCase):
    """utils/navigation.py — yagona konfiguratsiya va aktiv holat mantiqi."""

    def test_primary_nav_has_the_six_agreed_sections(self):
        self.assertEqual(
            [s.label for s in PRIMARY_NAV],
            ['Tadqiqot', 'Olimlar', 'Universitet', 'Ilmiy resurslar',
             'Imkoniyatlar', 'Tahlil'],
        )

    def test_tadqiqot_items_keep_the_agreed_order(self):
        tadqiqot = next(s for s in PRIMARY_NAV if s.key == 'tadqiqot')
        self.assertEqual(
            [i.href for i in tadqiqot.children],
            ['/dissertatsiyalar', '/compare', '/maqolalar-tahlili',
             '/rahbar-topish', '/councils', '/workspace', '/preparation'],
        )

    def test_no_section_links_to_news_or_blog(self):
        hrefs = {i.href for s in PRIMARY_NAV for i in s.children}
        self.assertNotIn('/yangiliklar', hrefs)
        self.assertNotIn('/blog', hrefs)

    def test_news_and_blog_live_in_the_footer_axborot_column(self):
        axborot = next(c for c in FOOTER_COLUMNS if c.title == 'Axborot')
        hrefs = [i.href for i in axborot.items]
        self.assertIn('/yangiliklar', hrefs)
        self.assertIn('/blog', hrefs)

    def test_premium_search_and_cabinet_are_not_primary_nav_items(self):
        hrefs = {i.href for s in PRIMARY_NAV for i in s.children}
        for reserved in ('/premium', '/cabinet', '/login'):
            self.assertNotIn(reserved, hrefs)

    def test_active_state_ignores_query_parameters(self):
        item = NavItem('Dissertatsiyalar', '/dissertatsiyalar')
        self.assertTrue(item_is_active(item, '/dissertatsiyalar?q=tibbiyot&page=3'))

    def test_active_state_matches_nested_routes(self):
        item = NavItem('Konferensiyalar', '/konferensiyalar')
        self.assertTrue(item_is_active(item, '/konferensiyalar/xalqaro'))

    def test_exact_items_do_not_swallow_their_siblings(self):
        tahlil = next(s for s in PRIMARY_NAV if s.key == 'tahlil')
        hindex = next(i for i in tahlil.children if i.href == '/reyting')
        ball = next(i for i in tahlil.children if i.href == '/reyting/ball')
        self.assertFalse(item_is_active(hindex, '/reyting/ball'))
        self.assertTrue(item_is_active(ball, '/reyting/ball'))

    def test_legacy_heatmap_keeps_the_research_map_item_active(self):
        tahlil = next(s for s in PRIMARY_NAV if s.key == 'tahlil')
        xarita = next(i for i in tahlil.children if i.href == '/tadqiqot-xaritasi')
        self.assertTrue(item_is_active(xarita, '/heatmap'))

    def test_dashboard_alias_activates_the_dissertations_section(self):
        tadqiqot = next(s for s in PRIMARY_NAV if s.key == 'tadqiqot')
        self.assertTrue(section_is_active(tadqiqot, '/dashboard'))


class NavigationShellTests(unittest.TestCase):
    """Har bir sahifa bitta umumiy shell'dan foydalanishi kerak."""

    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_public_maqolalar_pages_are_available(self):
        for path in ['/maqolalar', '/maqolalar/yonalishlar']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, msg=path)

    def test_legacy_heatmap_route_still_responds(self):
        self.assertEqual(self.client.get('/heatmap').status_code, 200)

    def test_shared_shell_renders_header_and_footer(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('data-nav-shell', html)
        self.assertIn('ol-footer', html)

    def test_every_primary_section_appears_in_the_header(self):
        html = self.client.get('/maqolalar').get_data(as_text=True)
        for section in PRIMARY_NAV:
            self.assertIn('ol-nav-menu-%s' % section.key, html)

    def test_old_boshqa_dropdown_is_gone(self):
        html = self.client.get('/maqolalar').get_data(as_text=True)
        self.assertNotIn('nav-dd-menu', html)
        self.assertNotIn('id="nav-links"', html)

    def test_header_exposes_search_premium_and_login_controls(self):
        html = self.client.get('/maqolalar').get_data(as_text=True)
        self.assertIn('aria-label="Qidiruvni ochish"', html)
        self.assertIn('ol-premium-btn', html)
        self.assertIn('href="/login"', html)

    def test_dropdown_triggers_are_wired_for_accessibility(self):
        html = self.client.get('/maqolalar').get_data(as_text=True)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('aria-controls="ol-nav-menu-tadqiqot"', html)

    def test_active_item_is_marked_on_the_current_page(self):
        html = self.client.get('/maqolalar').get_data(as_text=True)
        self.assertIn('ol-menu__link--active', html)

    def test_design_system_stylesheet_is_linked(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('/static/css/olimlar-design-system.css', html)

    def test_every_navigation_href_resolves_to_a_real_route(self):
        hrefs = {i.href for s in PRIMARY_NAV for i in s.children}
        hrefs |= {i.href for c in FOOTER_COLUMNS for i in c.items}
        hrefs |= {i.href for i in ACCOUNT_MENU}
        # Admin bandlar eski "Boshqa" menyusidan ko'chirilgan — ular ham
        # haqiqiy route'larga ishora qilishi kerak.
        hrefs |= {i.href for i in ADMIN_MENU}
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        for href in sorted(hrefs):
            self.assertTrue(
                href in rules or href.rstrip('/') in rules or href + '/' in rules,
                msg='navigatsiyada mavjud bo\'lmagan route: %s' % href,
            )


if __name__ == '__main__':
    unittest.main()
