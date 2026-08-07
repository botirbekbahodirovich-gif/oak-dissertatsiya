# -*- coding: utf-8 -*-
"""Fan yo'nalishlari UI — katta taksonomiya (289 ta OAK ixtisosligi) bilan.

Qalbaki DB (tests/fake_pg.py) filtrlamaydi; bu yerda muhimi — sahifa
yuzlab yozuvni bir vaqtda ochib tashlamasligi, qidiruv sahifalanishi,
kirill/lotin nomlar to'g'ri joyda chiqishi va rasmiy nomi yo'q guruh uchun
SUN'IY NOM yaratilmasligi.
"""
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


if __name__ == '__main__':
    unittest.main()
