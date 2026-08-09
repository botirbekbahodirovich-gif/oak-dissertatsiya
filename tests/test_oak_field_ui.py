# -*- coding: utf-8 -*-
"""Fan yo'nalishlari UI — katta taksonomiya (289 ta OAK ixtisosligi) bilan.

Qalbaki DB (tests/fake_pg.py) filtrlamaydi; bu yerda muhimi — sahifa
yuzlab yozuvni bir vaqtda ochib tashlamasligi, qidiruv sahifalanishi,
kirill/lotin nomlar to'g'ri joyda chiqishi va rasmiy nomi yo'q guruh uchun
SUN'IY NOM yaratilmasligi.
"""
import io
import os
import re
import unittest

from app import app, login_manager
from blueprints.articles import ADMIN_FIELDS_PER_PAGE, FIELDS_PER_PAGE
from tests.fake_pg import FakeConnection, field, new_store
from tests.test_articles_admin import _FakeUser
from tests.test_articles_catalog import ArticlePageTestCase, fake_db


def oak_like_fields(specialties=60):
    """22 tarmoq emas, ikkita tarmoq + ko'p ixtisoslik — UI yuki uchun yetarli."""
    rows = [
        field(id=1, code='05.00.00', name_uz='Texnika fanlari',
              name_uz_cyrillic='Техника фанлари', slug='texnika-fanlari-050000',
              level=1, article_count=0, review_status='seed'),
        field(id=2, code='13.00.00', name_uz='Pedagogika fanlari',
              name_uz_cyrillic='Педагогика фанлари', slug='pedagogika-fanlari-130000',
              level=1, article_count=0, review_status='seed'),
    ]
    for index in range(specialties):
        code = '05.01.%02d' % (index + 1)
        rows.append(field(
            id=100 + index, parent_id=1, code=code,
            name_uz='Ixtisoslik %d' % (index + 1),
            name_uz_cyrillic='Ихтисослик %d' % (index + 1),
            slug='ixtisoslik-%d-%s' % (index + 1, code.replace('.', '')),
            level=3, article_count=0, review_status='auto_clean',
            source_name='OAK', source_line=100 + index,
            missing_parent_code='05.01.00'))
    return rows


class HierarchyPageTests(ArticlePageTestCase):
    def test_page_renders_with_a_large_taxonomy(self):
        response = self.get('/maqolalar/yonalishlar', fields=oak_like_fields())
        self.assertEqual(response.status_code, 200)

    def test_children_are_collapsed_so_hundreds_of_rows_are_not_dumped(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        panels = re.findall(r'<div class="mq-tree__panel"[^>]*>', html)
        self.assertTrue(panels)
        for panel in panels:
            self.assertIn('hidden', panel, 'ichki yo\'nalishlar yopiq bo\'lishi kerak')

    def test_toggles_start_collapsed(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        toggles = re.findall(r'data-mq-tree-toggle[^>]*aria-expanded="(\w+)"', html)
        self.assertTrue(toggles)
        self.assertEqual(set(toggles), {'false'})

    def test_branch_filter_options_are_offered(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        self.assertIn('name="tarmoq"', html)
        self.assertIn('Texnika fanlari', html)

    def test_child_and_article_counts_are_visible(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        self.assertIn('ta ichki yo', html)
        self.assertIn('ta maqola', html)

    def test_cyrillic_name_is_shown_as_secondary_metadata(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        self.assertIn('lang="uz-Cyrl"', html)
        self.assertIn('Техника фанлари', html)

    def test_latin_name_is_the_link_text(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        match = re.search(
            r'<a class="mq-tree__name" href="/maqolalar/yonalish/[^"]+">([^<]+)</a>',
            html)
        self.assertTrue(match)
        self.assertNotRegex(match.group(1), r'[Ѐ-ӿ]')


class HierarchySearchTests(ArticlePageTestCase):
    def test_search_switches_to_a_flat_paginated_list(self):
        html = self.html('/maqolalar/yonalishlar?q=Ixtisoslik',
                         fields=oak_like_fields())
        self.assertIn('mq-results', html)
        self.assertIn('ta yo', html)
        self.assertIn('aria-label="Sahifalash"', html)

    def test_a_search_page_never_renders_more_than_the_page_size(self):
        html = self.html('/maqolalar/yonalishlar?q=Ixtisoslik',
                         fields=oak_like_fields(specialties=200))
        items = re.findall(r'<li class="mq-results__item">', html)
        self.assertLessEqual(len(items), FIELDS_PER_PAGE)

    def test_code_prefix_search_works(self):
        html = self.html('/maqolalar/yonalishlar?q=05.01', fields=oak_like_fields())
        self.assertIn('mq-results', html)
        self.assertNotIn('Pedagogika fanlari</a>', html)

    def test_branch_filter_narrows_the_result(self):
        html = self.html('/maqolalar/yonalishlar?tarmoq=pedagogika-fanlari-130000',
                         fields=oak_like_fields())
        self.assertIn('Pedagogika fanlari', html)
        self.assertNotIn('Ixtisoslik 1<', html)

    def test_an_unknown_branch_slug_yields_no_results_not_an_error(self):
        response = self.get('/maqolalar/yonalishlar?tarmoq=yoq-bunday',
                            fields=oak_like_fields())
        self.assertEqual(response.status_code, 200)
        self.assertIn('topilmadi', response.get_data(as_text=True))

    def test_search_results_show_the_parent_context(self):
        html = self.html('/maqolalar/yonalishlar?q=Ixtisoslik',
                         fields=oak_like_fields())
        self.assertIn('mq-results__context', html)
        self.assertIn('Texnika fanlari', html)

    def test_garbage_query_parameters_do_not_500(self):
        for query in ('?page=-1', '?page=abcd', '?q=' + 'x' * 400,
                      '?tarmoq=' + '%00', '?bor=maybe'):
            response = self.get('/maqolalar/yonalishlar' + query,
                                fields=oak_like_fields())
            self.assertEqual(response.status_code, 200, query)


class MissingParentDisplayTests(ArticlePageTestCase):
    """§17 — rasmiy guruh nomi yo'q bo'lsa SUN'IY NOM chiqarilmaydi."""

    def test_the_group_code_is_shown_not_an_invented_name(self):
        html = self.html('/maqolalar/yonalishlar?q=Ixtisoslik',
                         fields=oak_like_fields())
        self.assertIn('05.01', html)
        # "05.01 guruhi" — bu KOD konteksti, o'ylab topilgan nom emas.
        # Hech qanday soxta soha nomi paydo bo'lmasligi kerak.
        self.assertNotIn('Informatika va axborot texnologiyalari', html)

    def test_field_detail_explains_the_missing_group(self):
        rows = oak_like_fields()
        html = self.html('/maqolalar/yonalish/' + rows[2]['slug'], fields=rows)
        self.assertIn('05.01.00', html)
        self.assertIn('rasmiy nomi hali', html)

    def test_field_detail_shows_the_official_cyrillic_name(self):
        rows = oak_like_fields()
        html = self.html('/maqolalar/yonalish/' + rows[2]['slug'], fields=rows)
        self.assertIn('lang="uz-Cyrl"', html)
        self.assertIn('Ихтисослик 1', html)


class HierarchyAccessibilityTests(ArticlePageTestCase):
    PATHS = ['/maqolalar/yonalishlar',
             '/maqolalar/yonalishlar?q=Ixtisoslik',
             '/maqolalar/yonalishlar?tarmoq=texnika-fanlari-050000']

    def test_single_h1(self):
        for path in self.PATHS:
            html = self.html(path, fields=oak_like_fields())
            self.assertEqual(len(re.findall(r'<h1[\s>]', html)), 1, path)

    def test_search_and_filter_controls_are_labelled(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        for control in ('mq-field-q', 'mq-branch', 'mq-only-with'):
            self.assertRegex(html, r'<label[^>]*for="%s"' % control)

    def test_result_count_is_announced(self):
        html = self.html('/maqolalar/yonalishlar?q=Ixtisoslik',
                         fields=oak_like_fields())
        self.assertIn('role="status"', html)

    def test_tree_toggles_are_operable_targets(self):
        html = self.html('/maqolalar/yonalishlar', fields=oak_like_fields())
        self.assertIn('aria-controls=', html)
        self.assertIn('aria-label=', html)


class AdminTaxonomyTests(unittest.TestCase):
    """§18 — yuzlab yozuv bitta jadvalga birdan render qilinmaydi."""

    def setUp(self):
        self.client = app.test_client()
        self._loader = login_manager._user_callback
        self._protection = login_manager.session_protection
        login_manager.session_protection = None
        user = _FakeUser(True)
        login_manager._user_callback = lambda uid: user
        with self.client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True

    def tearDown(self):
        login_manager._user_callback = self._loader
        login_manager.session_protection = self._protection

    def get(self, path, rows=None):
        store = new_store(fields=rows if rows is not None else oak_like_fields())
        with fake_db(FakeConnection(store)):
            return self.client.get(path)

    def html(self, path, rows=None):
        return self.get(path, rows).get_data(as_text=True)

    def test_admin_page_renders(self):
        self.assertEqual(self.get('/admin/scientific-fields').status_code, 200)

    def test_the_table_is_paginated(self):
        html = self.html('/admin/scientific-fields', rows=oak_like_fields(200))
        rows = re.findall(r'<th scope="row" class="mq-tree__code">', html)
        self.assertLessEqual(len(rows), ADMIN_FIELDS_PER_PAGE)
        self.assertIn('aria-label="Sahifalash"', html)

    def test_all_required_filters_are_present(self):
        html = self.html('/admin/scientific-fields')
        for control in ('sf-q', 'sf-code', 'sf-level', 'sf-review', 'sf-source',
                        'sf-status', 'sf-orphan'):
            self.assertRegex(html, r'<label[^>]*for="%s"' % control, control)

    def test_code_prefix_filter_narrows_the_table(self):
        html = self.html('/admin/scientific-fields?code=13')
        self.assertIn('13.00.00', html)
        self.assertNotIn('>05.01.01<', html)

    def test_missing_parent_filter_works(self):
        html = self.html('/admin/scientific-fields?yetim=1')
        self.assertIn('05.01.00', html)
        self.assertNotIn('>05.00.00<', html)

    def test_level_filter_works(self):
        html = self.html('/admin/scientific-fields?level=1')
        self.assertIn('05.00.00', html)
        self.assertNotIn('>05.01.01<', html)

    def test_provenance_columns_are_shown(self):
        html = self.html('/admin/scientific-fields')
        for header in ('Shifr', 'Tasdiqlash', 'Manba', 'Qator', 'Ota konteksti'):
            self.assertIn(header, html)

    def test_the_tree_view_is_available_but_not_the_default(self):
        self.assertNotIn('mq-tree__panel', self.html('/admin/scientific-fields'))
        self.assertIn('mq-tree__panel',
                      self.html('/admin/scientific-fields?view=daraxt'))

    def test_single_h1_on_admin_pages(self):
        for path in ('/admin/scientific-fields', '/admin/scientific-fields?view=daraxt'):
            self.assertEqual(
                len(re.findall(r'<h1[\s>]', self.html(path))), 1, path)


class AdminReviewQueueTests(AdminTaxonomyTests):
    """§13 — review navbati faqat ko'rish; yozuvlar bazada yo'q."""

    def test_the_queue_renders_the_repository_file(self):
        response = self.get('/admin/scientific-fields/review')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # Manbadagi haqiqiy shubhali yozuvlar ko'rinishi kerak.
        self.assertIn('02.00.07', html)
        self.assertIn('unbalanced_quotes_possible_truncation', html)

    def test_the_queue_shows_everything_a_reviewer_needs(self):
        html = self.html('/admin/scientific-fields/review')
        for header in ('Shifr', 'Manbadagi asl nom', 'Normalizatsiya nomzodi',
                       'Qator', 'Sabab'):
            self.assertIn(header, html)

    def test_the_workflow_is_documented_on_the_page(self):
        html = self.html('/admin/scientific-fields/review')
        self.assertIn('approved_name_uz_cyrillic', html)
        self.assertIn('parse_oak_specialties.py', html)

    def test_a_missing_queue_file_is_a_readable_message_not_a_500(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {'OAK_REVIEW_FILE': '/yoq/bunday.json'}):
            response = self.get('/admin/scientific-fields/review')
        self.assertEqual(response.status_code, 200)
        self.assertIn('topilmadi', response.get_data(as_text=True))

    def test_the_queue_is_not_indexed(self):
        self.assertIn('noindex', self.html('/admin/scientific-fields/review'))

    def test_non_admin_cannot_open_the_queue(self):
        user = _FakeUser(False)
        login_manager._user_callback = lambda uid: user
        self.assertEqual(self.get('/admin/scientific-fields/review').status_code, 403)


def three_level_fields():
    """Rasmiy ierarxiya: tarmoq -> guruh -> ixtisoslik (05 va 21 tarmoqlari)."""
    return [
        field(id=1, code='05.00.00', name_uz='Texnika fanlari',
              name_uz_cyrillic='Техника фанлари', slug='texnika-fanlari-050000',
              level=1, article_count=0, review_status='seed'),
        field(id=2, parent_id=1, code='05.01.00',
              name_uz='Axborot texnologiyalari, boshqaruv va kompyuter grafikasi',
              name_uz_cyrillic='Ахборот технологиялари, бошқарув ва компьютер графикаси',
              slug='axborot-texnologiyalari-050100', level=2, article_count=0,
              review_status='official_source',
              source_name="OAK ixtisosliklari ro'yxati (2021), 13-bet"),
        field(id=3, parent_id=2, code='05.01.05',
              name_uz='Axborotlarni himoyalash usullari va tizimlari',
              name_uz_cyrillic='Ахборотларни ҳимоялаш усуллари ва тизимлари',
              slug='axborotlarni-himoyalash-050105', level=3, article_count=2,
              review_status='auto_normalized', source_line=161),
        field(id=4, code='21.00.00', name_uz='Harbiy fanlar',
              name_uz_cyrillic='Ҳарбий фанлар', slug='harbiy-fanlar-210000',
              level=1, article_count=0, review_status='official_source'),
        field(id=5, parent_id=4, code='21.02.00', name_uz='Harbiy-maxsus fanlar',
              name_uz_cyrillic='Ҳарбий-махсус фанлар',
              slug='harbiy-maxsus-fanlar-210200', level=2, article_count=0,
              review_status='official_source'),
        field(id=6, parent_id=5, code='21.02.23', name_uz='Harbiy tibbiyot',
              name_uz_cyrillic='Ҳарбий тиббиёт', slug='harbiy-tibbiyot-210223',
              level=3, article_count=1, review_status='auto_clean'),
    ]


class ThreeLevelHierarchyTests(ArticlePageTestCase):
    """§12 — sahifa to'liq uch darajani ko'rsatishi kerak."""

    def test_the_tree_nests_three_levels(self):
        html = self.html('/maqolalar/yonalishlar', fields=three_level_fields())
        # Guruh ixtisoslikni o'z paneli ichida saqlaydi.
        self.assertIn('Axborot texnologiyalari', html)
        self.assertIn('Axborotlarni himoyalash', html)
        branch_at = html.index('Texnika fanlari')
        group_at = html.index('Axborot texnologiyalari')
        specialty_at = html.index('Axborotlarni himoyalash')
        self.assertLess(branch_at, group_at)
        self.assertLess(group_at, specialty_at)

    def test_the_group_name_is_shown_not_only_its_code(self):
        html = self.html('/maqolalar/yonalishlar', fields=three_level_fields())
        self.assertIn('Axborot texnologiyalari, boshqaruv va kompyuter grafikasi',
                      html)

    def test_no_invented_group_label_is_rendered(self):
        # Eski vaqtinchalik ko'rinish "05.01 guruhi" degan yorliq chiqarardi.
        html = self.html('/maqolalar/yonalishlar', fields=three_level_fields())
        self.assertNotIn('guruhi</span>', html)

    def test_search_result_shows_the_full_official_chain(self):
        html = self.html('/maqolalar/yonalishlar?q=himoyalash',
                         fields=three_level_fields())
        self.assertIn('Texnika fanlari', html)
        self.assertIn('Axborot texnologiyalari', html)
        self.assertIn('Axborotlarni himoyalash', html)

    def test_specialty_page_breadcrumb_lists_branch_then_group(self):
        rows = three_level_fields()
        html = self.html('/maqolalar/yonalish/axborotlarni-himoyalash-050105',
                         fields=rows)
        crumbs = html[html.index('ol-breadcrumb'):html.index('</ol>')]
        self.assertIn('Texnika fanlari', crumbs)
        self.assertIn('Axborot texnologiyalari', crumbs)
        self.assertLess(crumbs.index('Texnika fanlari'),
                        crumbs.index('Axborot texnologiyalari'))

    def test_specialty_page_has_no_missing_group_notice(self):
        html = self.html('/maqolalar/yonalish/axborotlarni-himoyalash-050105',
                         fields=three_level_fields())
        self.assertNotIn('rasmiy nomi hali', html)

    def test_the_military_branch_renders_with_its_group(self):
        html = self.html('/maqolalar/yonalishlar?q=tibbiyot',
                         fields=three_level_fields())
        self.assertIn('Harbiy fanlar', html)
        self.assertIn('Harbiy-maxsus fanlar', html)
        self.assertIn('Harbiy tibbiyot', html)

    def test_branch_filter_offers_every_branch(self):
        html = self.html('/maqolalar/yonalishlar', fields=three_level_fields())
        self.assertIn('Texnika fanlari', html)
        self.assertIn('Harbiy fanlar', html)

    def test_the_source_note_names_the_official_document(self):
        html = self.html('/maqolalar/yonalishlar', fields=three_level_fields())
        self.assertIn('2021', html)
        self.assertIn('ixtisosliklari', html)

    def test_group_pages_list_their_specialties(self):
        html = self.html('/maqolalar/yonalish/axborot-texnologiyalari-050100',
                         fields=three_level_fields())
        self.assertIn('Ichki yo', html)
        self.assertIn('Axborotlarni himoyalash', html)


class DesignSystemComplianceTests(ArticlePageTestCase):
    """§14 — ushbu vazifada tegilgan UI fayllari dizayn tizimiga mos."""

    TOUCHED_TEMPLATES = [
        'templates/maqolalar_yonalishlar.html',
        'templates/maqolalar_yonalish.html',
        'templates/components/field_tree.html',
        'templates/admin/scientific_fields.html',
        'templates/admin/scientific_field_review.html',
    ]
    TOUCHED_CSS = 'static/css/maqolalar.css'

    def read(self, relative):
        with io.open(os.path.join(app.root_path, relative), encoding='utf-8') as fh:
            return fh.read()

    def test_no_raw_colour_literals(self):
        for relative in self.TOUCHED_TEMPLATES + [self.TOUCHED_CSS]:
            body = self.read(relative)
            self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b|rgba?\(', body), [],
                             relative)

    def test_no_gradient_blur_or_dark_theme(self):
        for relative in self.TOUCHED_TEMPLATES + [self.TOUCHED_CSS]:
            body = self.read(relative)
            for banned in ('linear-gradient', 'radial-gradient',
                           'backdrop-filter', 'data-bs-theme'):
                self.assertNotIn(banned, body, '%s: %s' % (relative, banned))

    def test_no_prefers_color_scheme_rule(self):
        for relative in self.TOUCHED_TEMPLATES + [self.TOUCHED_CSS]:
            body = self.read(relative)
            self.assertIsNone(
                re.search(r'@media[^{]*prefers-color-scheme', body), relative)

    def test_no_inline_style_attributes_left_in_touched_templates(self):
        for relative in self.TOUCHED_TEMPLATES:
            body = self.read(relative)
            self.assertEqual(re.findall(r'style="[^"]+"', body), [], relative)

    def test_page_local_css_is_namespaced(self):
        css = self.read(self.TOUCHED_CSS)
        selectors = re.findall(r'^\.([a-zA-Z][\w-]*)', css, re.M)
        self.assertTrue(selectors)
        for selector in set(selectors):
            self.assertTrue(selector.startswith('mq-'), selector)

    def test_no_new_important_or_outline_none(self):
        css = self.read(self.TOUCHED_CSS)
        self.assertNotIn('!important', css)
        self.assertNotIn('outline: none', css)

    def test_interaction_targets_are_44px_inside_the_module(self):
        css = self.read(self.TOUCHED_CSS)
        for selector in ('.mq-page .ol-pager a', '.mq-page .ol-chip',
                         '.mq-page .ol-facets__opt', '.mq-toolbar .ol-btn'):
            block = re.search(re.escape(selector) + r'[^{]*\{[^}]*\}', css)
            self.assertIsNotNone(block, selector)
            self.assertIn('44px', block.group(0), selector)

    def test_module_pages_carry_the_scope_class(self):
        for relative in ('templates/maqolalar_yonalishlar.html',
                         'templates/maqolalar_yonalish.html',
                         'templates/admin/scientific_fields.html',
                         'templates/admin/scientific_field_review.html'):
            self.assertIn('ol-page mq-page', self.read(relative), relative)

    def test_icons_are_bootstrap_not_emoji(self):
        for relative in self.TOUCHED_TEMPLATES:
            body = self.read(relative)
            emoji = [ch for ch in body if ord(ch) > 0x2500 and ord(ch) not in
                     (0x2013, 0x2014, 0x2018, 0x2019, 0x201c, 0x201d, 0x2192,
                      0x2190, 0x00ab, 0x00bb, 0x2026)]
            self.assertEqual(emoji, [], '%s: %r' % (relative, emoji[:5]))

    def test_state_is_not_conveyed_by_opacity_alone(self):
        # Nofaol qator shaffof bo'ladi, LEKIN yonida "Nofaol" yorlig'i ham bor.
        body = self.read('templates/admin/scientific_fields.html')
        self.assertIn('mq-row--inactive', body)
        self.assertIn('>Nofaol<', body)

    def test_static_assets_carry_a_cache_buster(self):
        for relative in self.TOUCHED_TEMPLATES:
            body = self.read(relative)
            for match in re.findall(r'/static/[^"]+', body):
                self.assertIn('?v=', match, '%s: %s' % (relative, match))


if __name__ == '__main__':
    unittest.main()
