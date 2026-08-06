import re
import unittest

from app import app
from utils.navigation import (
    ACCOUNT_MENU,
    ADMIN_MENU,
    ADMIN_SERVICES,
    FOOTER_COLUMNS,
    HEADER_ICON_ITEMS,
    LOGOUT_ITEM,
    PREMIUM_ITEM,
    PRIMARY_NAV,
    NavItem,
    item_is_active,
    section_is_active,
)

# Navigatsiya shelli har bir sahifada bir xil, shuning uchun uni tekshirish
# uchun DB'ga TEGMAYDIGAN, lekin navigatsiyada MAVJUD sahifa tanlanadi
# (aktiv holat ham tekshirilishi kerak). Ilgari bu /maqolalar edi; Phase 2B.1
# dan boshlab u haqiqiy katalog va Postgres talab qiladi, shu sababli lokal
# muhitda 500 qaytarib bu testlarni yashirin ravishda xato sahifa ustida
# ishlatib yuborardi.
NAV_PROBE_PAGE = '/preparation'


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


class _ShellUser:
    """Header'ning logged-in / admin holatini render qilish uchun minimal user."""

    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, username='botirbek', is_admin=False):
        self.id = 1
        self.username = username
        self.is_admin = is_admin

    def get_id(self):
        return '1'


class NavigationShellTests(unittest.TestCase):
    """Har bir sahifa bitta umumiy shell'dan foydalanishi kerak."""

    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def _html_as(self, user, path=NAV_PROBE_PAGE):
        """DB'ga tegmaydigan shell sahifasini berilgan foydalanuvchi sifatida render qiladi."""
        client = app.test_client()
        original = app.login_manager._user_callback
        app.login_manager._user_callback = lambda uid: user
        with client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True
        try:
            return client.get(path).get_data(as_text=True)
        finally:
            # Asl loader'ni tiklash shart — aks holda keyingi testlar
            # "Missing user_loader" bilan yiqiladi.
            app.login_manager._user_callback = original

    def test_public_maqolalar_routes_are_registered(self):
        # Phase 2B.1 dan boshlab bu sahifalar haqiqiy katalog va Postgres
        # talab qiladi, shuning uchun bu yerda ular CHAQIRILMAYDI — mavjudligi
        # url_map orqali tekshiriladi. Sahifalarning o'zi
        # tests/test_articles_catalog.py da (qalbaki DB bilan) tekshiriladi.
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        for path in ['/maqolalar', '/maqolalar/yonalishlar',
                     '/maqolalar/<slug>', '/maqolalar/yonalish/<slug>']:
            self.assertIn(path, rules, msg=path)

    def test_legacy_heatmap_route_still_responds(self):
        self.assertEqual(self.client.get('/heatmap').status_code, 200)

    def test_shared_shell_renders_header_and_footer(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('data-nav-shell', html)
        self.assertIn('ol-footer', html)

    def test_every_primary_section_appears_in_the_header(self):
        html = self.client.get(NAV_PROBE_PAGE).get_data(as_text=True)
        for section in PRIMARY_NAV:
            self.assertIn('ol-nav-menu-%s' % section.key, html)

    def test_old_boshqa_dropdown_is_gone(self):
        html = self.client.get(NAV_PROBE_PAGE).get_data(as_text=True)
        self.assertNotIn('nav-dd-menu', html)
        self.assertNotIn('id="nav-links"', html)

    def test_header_exposes_search_premium_and_login_controls(self):
        html = self.client.get(NAV_PROBE_PAGE).get_data(as_text=True)
        self.assertIn('aria-label="Qidiruvni ochish"', html)
        self.assertIn('ol-premium-btn', html)
        self.assertIn('href="/login"', html)

    def test_dropdown_triggers_are_wired_for_accessibility(self):
        html = self.client.get(NAV_PROBE_PAGE).get_data(as_text=True)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('aria-controls="ol-nav-menu-tadqiqot"', html)

    def test_active_item_is_marked_on_the_current_page(self):
        html = self.client.get(NAV_PROBE_PAGE).get_data(as_text=True)
        self.assertIn('ol-menu__link--active', html)

    def test_design_system_stylesheet_is_linked(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('/static/css/olimlar-design-system.css', html)

    def test_every_navigation_href_resolves_to_a_real_route(self):
        hrefs = {i.href for s in PRIMARY_NAV for i in s.children}
        hrefs |= {i.href for c in FOOTER_COLUMNS for i in c.items}
        hrefs |= {i.href for i in ACCOUNT_MENU}
        hrefs |= {i.href for i in HEADER_ICON_ITEMS}
        hrefs |= {PREMIUM_ITEM.href, LOGOUT_ITEM.href}
        # Admin bandlar eski "Boshqa" menyusidan ko'chirilgan — ular ham
        # haqiqiy route'larga ishora qilishi kerak.
        hrefs |= {i.href for i in ADMIN_MENU}
        hrefs |= {i.href for i in ADMIN_SERVICES}
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        for href in sorted(hrefs):
            self.assertTrue(
                href in rules or href.rstrip('/') in rules or href + '/' in rules,
                msg='navigatsiyada mavjud bo\'lmagan route: %s' % href,
            )


class HeaderAuthStateTests(unittest.TestCase):
    """Logged-out / logged-in / admin header holatlari."""

    def _html_as(self, user, path=NAV_PROBE_PAGE):
        client = app.test_client()
        original = app.login_manager._user_callback
        app.login_manager._user_callback = lambda uid: user
        with client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True
        try:
            return client.get(path).get_data(as_text=True)
        finally:
            # Asl loader'ni tiklash shart — aks holda keyingi testlar
            # "Missing user_loader" bilan yiqiladi.
            app.login_manager._user_callback = original

    def test_logged_out_header_shows_login_and_no_account_menu(self):
        html = app.test_client().get(NAV_PROBE_PAGE).get_data(as_text=True)
        self.assertIn('href="/login"', html)
        self.assertNotIn('ol-account-menu', html)
        self.assertNotIn('href="/logout"', html)

    def test_logged_in_header_shows_account_menu_not_login_button(self):
        html = self._html_as(_ShellUser('botirbek', is_admin=False))
        self.assertIn('ol-account-menu', html)
        self.assertIn('href="/logout"', html)
        self.assertIn('botirbek', html)

    def test_admin_links_are_not_rendered_for_public_users(self):
        html = app.test_client().get(NAV_PROBE_PAGE).get_data(as_text=True)
        for item in ADMIN_SERVICES:
            self.assertNotIn('href="%s"' % item.href, html,
                             msg='mehmonga admin link ko\'rindi: %s' % item.href)
        self.assertNotIn('/admin/analytics', html)

    def test_admin_links_are_not_rendered_for_ordinary_users(self):
        html = self._html_as(_ShellUser('botirbek', is_admin=False))
        self.assertNotIn('/admin/analytics', html)
        self.assertNotIn('/admin/data-quality', html)

    def test_admin_user_keeps_an_admin_entry_point(self):
        html = self._html_as(_ShellUser('adminuser', is_admin=True))
        self.assertIn('/admin/analytics', html)

    def test_admin_dropdown_stays_short(self):
        """22 bandli dropdown 1440x900 da 812px ga cho'zilgan edi."""
        self.assertLessEqual(len(ADMIN_MENU), 5)
        # ...lekin to'liq ro'yxat yo'qolmasligi kerak.
        self.assertGreaterEqual(len(ADMIN_SERVICES), 15)

    def test_full_admin_service_list_is_reachable_from_the_admin_hub(self):
        from utils.navigation import ADMIN_HUB
        self.assertEqual(ADMIN_HUB.href, '/admin/analytics')
        self.assertIn(ADMIN_HUB, ADMIN_MENU)


class AccessibilityMarkupTests(unittest.TestCase):
    """Spetsifikatsiya talab qiladigan minimal a11y belgilari."""

    @classmethod
    def setUpClass(cls):
        cls.html = app.test_client().get(NAV_PROBE_PAGE).get_data(as_text=True)

    def test_landmarks_are_present(self):
        for marker in ('<header', '<nav', '<main', '<footer'):
            self.assertIn(marker, self.html)

    def test_skip_link_targets_main_content(self):
        self.assertIn('ol-skip-link', self.html)
        self.assertIn('href="#main-content"', self.html)
        self.assertIn('id="main-content"', self.html)

    def test_every_dropdown_trigger_declares_expanded_and_controls(self):
        triggers = re.findall(r'<button[^>]*data-nav-trigger[^>]*>', self.html)
        self.assertTrue(triggers)
        for trigger in triggers:
            self.assertIn('aria-expanded=', trigger)
            self.assertIn('aria-controls=', trigger)
            self.assertIn('aria-haspopup=', trigger)

    def test_aria_controls_point_at_real_elements(self):
        for target in re.findall(r'aria-controls="([^"]+)"', self.html):
            self.assertIn('id="%s"' % target, self.html,
                          msg='aria-controls mavjud bo\'lmagan id ga ishora qiladi: %s' % target)

    def test_icon_buttons_have_accessible_labels(self):
        for btn in re.findall(r'<button[^>]*class="[^"]*ol-icon-btn[^"]*"[^>]*>', self.html):
            self.assertIn('aria-label=', btn)

    def test_accordion_triggers_are_wired(self):
        accs = re.findall(r'<button[^>]*data-acc-trigger[^>]*>', self.html)
        self.assertTrue(accs)
        for acc in accs:
            self.assertIn('aria-expanded=', acc)
            self.assertIn('aria-controls=', acc)

    def test_search_control_is_an_icon_button_with_a_label(self):
        self.assertIn('aria-label="Qidiruvni ochish"', self.html)

    def test_search_overlay_states_its_scope(self):
        """Global qidiruv mavjud emas — overlay buni yashirmasligi kerak."""
        self.assertIn("Dissertatsiyalar bo&#39;yicha qidiruv", self.html)
        self.assertIn('ol-search-hint', self.html)

    def test_footer_links_render_from_config(self):
        for column in FOOTER_COLUMNS:
            for item in column.items:
                self.assertIn('href="%s"' % item.href, self.html,
                              msg='footer linki yo\'q: %s' % item.href)


if __name__ == '__main__':
    unittest.main()
