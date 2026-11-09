# -*- coding: utf-8 -*-
"""Maqolalar katalogi — route, shablon, SEO va accessibility testlari.

DB qalbaki (tests/fake_pg.py): u FILTRLAMAYDI, shuning uchun filtr va
xavfsizlik semantikasi YUBORILGAN SQL ustida, qolgani render qilingan HTML
ustida tekshiriladi. Haqiqiy SQL semantikasi staging'da —
tests/test_articles_dbgated.py (TEST_DATABASE_URL bilan).
"""
import contextlib
import html as html_lib
import re
import unittest
from unittest import mock

from app import app
from blueprints.articles import PUBLIC_WHERE, SORT_OPTIONS
from tests.fake_pg import FakeConnection, article, field, new_store


@contextlib.contextmanager
def fake_db(connection, trgm_checked=False):
    """Qalbaki ulanishni HAMMA iste'molchiga beradi.

    `data.get_connection` ham almashtiriladi, chunki app.py dagi
    before_request hooklari (check_blocked, track_visit) har bir so'rovda
    o'zicha ulanish oladi — patchlanmasa testlar mavjud bo'lmagan Neon
    bazasiga tarmoq so'rovi yuborib, sekundlab osilib qoladi.

    Sxema va pg_trgm bayroqlari modul darajasida keshlanadi; har bir test
    toza boshlashi uchun ular ham qayta tiklanadi.
    """
    with mock.patch('blueprints.articles._schema_ready', False), \
            mock.patch('blueprints.articles._trgm_checked', trgm_checked), \
            mock.patch('blueprints.articles.get_connection', return_value=connection), \
            mock.patch('data.get_connection', return_value=connection):
        yield


def link(article_id=1, field_id=1, primary=True, name='Pedagogika fanlari',
         slug='pedagogika-fanlari-130000', code='13.00.00', level=1):
    return {'article_id': article_id, 'is_primary': primary, 'id': field_id,
            'name_uz': name, 'slug': slug, 'code': code, 'level': level}


def author(article_id=1, name='Karimov Sardor', order=1, **extra):
    row = {'article_id': article_id, 'author_name': name, 'author_order': order,
           'orcid': None, 'affiliation': None, 'linked_user_id': None,
           'linked_scientist_id': None, 'is_corresponding': False,
           'openalex_author_id': None}
    row.update(extra)
    return row


class ArticlePageTestCase(unittest.TestCase):
    """Qalbaki ulanish bilan sahifa oladigan umumiy asos."""

    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def get(self, path, store=None, **store_kwargs):
        store = store if store is not None else new_store(**store_kwargs)
        connection = FakeConnection(store)
        with fake_db(connection):
            response = self.client.get(path)
        self.store = store
        self.connection = connection
        return response

    def html(self, path, **kwargs):
        response = self.get(path, **kwargs)
        return response.get_data(as_text=True)

    def sql_texts(self):
        return [sql for sql, _params in self.store['sql']]

    def select_sql(self):
        """Faqat MAQOLALAR modulining so'rovlari.

        app.py dagi before_request hooklari (check_blocked, track_visit) ham
        shu qalbaki ulanishdan foydalanadi va o'z SQL'ini yozadi — ular
        assertionlarga aralashmasligi kerak."""
        return [sql for sql in self.sql_texts()
                if sql.upper().startswith('SELECT')
                and ('scientific_articles' in sql or 'scientific_fields' in sql
                     or 'article_authors' in sql or 'article_scientific_fields' in sql)]

    def catalog_sql(self):
        return [sql for sql in self.select_sql() if 'FROM scientific_articles a' in sql]


class CatalogRouteTests(ArticlePageTestCase):
    def test_catalog_renders(self):
        response = self.get('/maqolalar', articles=[article()], fields=[field()],
                            authors=[author()], links=[link()])
        self.assertEqual(response.status_code, 200)

    def test_empty_catalog_shows_an_empty_state_not_an_error(self):
        html = self.html('/maqolalar')
        self.assertIn('Katalogda hali maqola', html)

    def test_no_results_state_differs_from_empty_catalog(self):
        html = self.html('/maqolalar?q=yoq-bunday-narsa')
        self.assertIn('Natija topilmadi', html)

    def test_the_connection_is_always_closed(self):
        self.get('/maqolalar')
        self.assertTrue(self.connection.closed)

    def test_every_public_query_is_restricted_to_published_and_verified(self):
        self.get('/maqolalar', articles=[article()], fields=[field()])
        catalog_selects = [s for s in self.select_sql()
                           if 'FROM scientific_articles a' in s]
        self.assertTrue(catalog_selects)
        for sql in catalog_selects:
            self.assertIn("a.publication_status = 'published'", sql, sql[:120])
            self.assertIn("a.verification_status = 'verified'", sql, sql[:120])

    def test_public_where_constant_is_what_the_routes_actually_use(self):
        self.get('/maqolalar')
        self.assertTrue(any(PUBLIC_WHERE in sql for sql in self.select_sql()))


class SearchAndFilterTests(ArticlePageTestCase):
    def test_search_covers_both_titles_abstract_keywords_doi_journal_and_authors(self):
        self.get('/maqolalar?q=talim', fields=[field()])
        search_sql = next(s for s in self.select_sql() if 'ILIKE' in s)
        for column in ('a.title', 'a.title_uz', 'a.abstract', 'a.keywords', 'a.doi',
                       'a.journal_name', 'au.names'):
            self.assertIn(column, search_sql, column)

    def test_similarity_is_not_used_when_pg_trgm_is_absent(self):
        self.get('/maqolalar?q=talim', fields=[field()])
        self.assertFalse(any('similarity(' in s for s in self.select_sql()))

    def test_similarity_is_used_when_pg_trgm_is_present(self):
        store = new_store(fields=[field()], pg_trgm=True)
        connection = FakeConnection(store)
        with fake_db(connection, trgm_checked=None):
            self.client.get('/maqolalar?q=talim')
        self.assertTrue(any('similarity(' in sql for sql, _ in store['sql']))

    def test_year_language_and_index_filters_reach_the_query(self):
        self.get('/maqolalar?year_from=2020&year_to=2024&lang=uz&scopus=1&doaj=1',
                 fields=[field()])
        sql = ' '.join(self.select_sql())
        self.assertIn('a.publication_year >= %s', sql)
        self.assertIn('a.publication_year <= %s', sql)
        self.assertIn('a.language = %s', sql)
        self.assertIn('a.index_scopus IS TRUE', sql)
        self.assertIn('a.index_doaj IS TRUE', sql)

    def test_pdf_filter_matches_the_public_readability_rule_not_mere_presence(self):
        self.get('/maqolalar?pdf=1', fields=[field()])
        sql = ' '.join(self.select_sql())
        self.assertIn('a.pdf_path IS NOT NULL', sql)
        self.assertIn("a.license_type <> 'unknown'", sql)
        self.assertIn('a.access_type = ANY(%s)', sql)

    def test_garbage_filter_values_do_not_500(self):
        for query in ('?year_from=abc', '?year_to=99999999999999', '?page=-5',
                      '?page=notanumber', '?per_page=100000', '?lang=<script>',
                      '?sort=DROP+TABLE', '?access=nonsense', '?license=..',
                      '?view=%00', '?field=' + 'x' * 500):
            response = self.get('/maqolalar' + query, fields=[field()])
            self.assertEqual(response.status_code, 200, query)

    def test_unknown_sort_falls_back_to_the_default_order(self):
        self.get('/maqolalar?sort=; DROP TABLE users', fields=[field()])
        sql = next(s for s in self.catalog_sql() if 'ORDER BY' in s and 'LIMIT' in s)
        self.assertIn(SORT_OPTIONS['yangi'][1], sql)
        self.assertNotIn('DROP TABLE', sql)

    def test_every_sort_option_produces_its_own_order_by(self):
        for key, (_label, order) in SORT_OPTIONS.items():
            self.get('/maqolalar?sort=' + key, fields=[field()])
            sql = next(s for s in self.catalog_sql() if 'ORDER BY' in s and 'LIMIT' in s)
            self.assertIn(order, sql, key)

    def test_unknown_field_slug_yields_no_results_rather_than_an_error(self):
        response = self.get('/maqolalar?field=mavjud-emas', fields=[field()])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('WHERE FALSE' in sql for sql in self.select_sql()))


class PaginationTests(ArticlePageTestCase):
    def test_pagination_is_server_side_with_limit_and_offset(self):
        self.get('/maqolalar?page=3', articles=[article()], fields=[field()])
        sql, params = next((s, p) for s, p in self.store['sql']
                           if 'LIMIT %s OFFSET %s' in s)
        self.assertEqual(params[-2:], [20, 40])

    def test_page_size_is_capped(self):
        self.get('/maqolalar?per_page=99999', articles=[article()], fields=[field()])
        _sql, params = next((s, p) for s, p in self.store['sql']
                            if 'LIMIT %s OFFSET %s' in s)
        self.assertLessEqual(params[-2], 60)

    def test_page_number_is_capped_so_offset_cannot_run_away(self):
        self.get('/maqolalar?page=999999', articles=[article()], fields=[field()])
        _sql, params = next((s, p) for s, p in self.store['sql']
                            if 'LIMIT %s OFFSET %s' in s)
        self.assertLessEqual(params[-1], 500 * 20)

    def test_filters_survive_pagination_links(self):
        html = self.html('/maqolalar?q=talim&lang=uz&page=1',
                         articles=[article(id=i) for i in range(1, 4)],
                         fields=[field()], authors=[author()], links=[link()])
        self.assertIn('q=talim', html)


class PdfVisibilityTests(ArticlePageTestCase):
    """§18/§23 — o'qish tugmasi faqat backend ruxsat bergan holatda chiqadi."""

    def _catalog_html(self, **article_overrides):
        return self.html('/maqolalar',
                         articles=[article(**article_overrides)],
                         fields=[field()], authors=[author()], links=[link()])

    def test_verified_open_article_offers_the_read_button(self):
        html = self._catalog_html()
        self.assertIn('/maqolalar/ochiq-maqola/oqish', html)
        self.assertIn('Ochiq PDF', html)

    def test_metadata_only_article_has_no_read_button(self):
        html = self._catalog_html(access_type='metadata_only', pdf_path=None)
        self.assertNotIn('/oqish', html)
        self.assertIn('Faqat metadata', html)
        self.assertIn('/maqolalar/ochiq-maqola"', html)

    def test_unknown_license_hides_the_read_button_even_with_a_pdf(self):
        html = self._catalog_html(license_type='unknown')
        self.assertNotIn('/oqish', html)

    def test_copyright_hold_hides_the_read_button(self):
        html = self._catalog_html(verification_status='copyright_hold')
        self.assertNotIn('/oqish', html)


class ArticleDetailTests(ArticlePageTestCase):
    def _detail(self, **overrides):
        return self.html('/maqolalar/ochiq-maqola',
                         articles=[article(**overrides)], fields=[field()],
                         authors=[author(), author(name='Aliyeva Nigora', order=2)],
                         links=[link()])

    def test_detail_page_renders_metadata(self):
        html = self._detail()
        for needle in ('Ochiq maqola', 'Karimov Sardor', 'Aliyeva Nigora',
                       'Ilm Sarchashmalari', '2024', '10.1234/abc.2024',
                       'CC BY', 'Ochiq kirish'):
            self.assertIn(needle, html, needle)

    def test_uzbek_title_is_primary_and_original_title_is_preserved(self):
        html = html_lib.unescape(self._detail(title='Original English title',
                                              title_uz="Maqolaning o'zbekcha nomi"))
        self.assertIn("Maqolaning o'zbekcha nomi", html)
        self.assertIn('Asl nomi: Original English title', html)

    def test_related_public_articles_are_shown_below_the_detail(self):
        related = article(id=2, slug='yaqin-maqola', title='Yaqin maqola',
                          title_uz="O'xshash tadqiqot")
        html = html_lib.unescape(self.html('/maqolalar/ochiq-maqola',
                                           articles=[article(), related], fields=[field()],
                                           authors=[author()], links=[link()]))
        self.assertIn("O'xshash maqolalar", html)
        self.assertIn("O'xshash tadqiqot", html)
        related_sql = next(s for s in self.select_sql()
                           if 'a.id <> %s' in s and 'ORDER BY' in s)
        self.assertIn(PUBLIC_WHERE, related_sql)

    def test_authors_keep_their_order(self):
        html = self._detail()
        self.assertLess(html.index('Karimov Sardor'), html.index('Aliyeva Nigora'))

    def test_missing_article_is_a_404_not_a_500(self):
        response = self.get('/maqolalar/mavjud-emas', articles=[], fields=[field()])
        self.assertEqual(response.status_code, 404)

    def test_detail_lookup_is_restricted_to_public_articles(self):
        self.get('/maqolalar/ochiq-maqola', articles=[article()], fields=[field()])
        lookup = next(s for s in self.select_sql() if 'WHERE a.slug = %s' in s)
        self.assertIn("a.publication_status = 'published'", lookup)

    def test_primary_cta_is_internal_not_the_external_source(self):
        html = self._detail(source_url='https://jurnal.uz/maqola')
        cta = re.search(r'class="ol-btn ol-btn--primary" href="([^"]+)"', html)
        self.assertTrue(cta)
        self.assertTrue(cta.group(1).startswith('/maqolalar/'), cta.group(1))

    def test_external_source_link_is_nofollow_metadata(self):
        html = self._detail(source_url='https://jurnal.uz/maqola')
        self.assertIn('rel="nofollow noopener"', html)

    def test_view_count_is_incremented(self):
        self.get('/maqolalar/ochiq-maqola', articles=[article()], fields=[field()])
        self.assertTrue(any('view_count = view_count + 1' in sql
                            for sql in self.sql_texts()))


class ReadRouteTests(ArticlePageTestCase):
    """§3 — /oqish ichki PDF o'quvchisi; kirish qoidasi to'liq kuchda.

    O'quvchining o'z tafsilotlari (PDF.js, oqim, sarlavhalar, tartib)
    tests/test_articles_viewer.py da."""

    def test_open_article_reaches_the_viewer(self):
        response = self.get('/maqolalar/ochiq-maqola/oqish',
                            articles=[article()], fields=[field()])
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-mqv="root"', response.get_data(as_text=True))

    def test_closed_pdf_is_a_404_and_does_not_leak_existence(self):
        for overrides in ({'license_type': 'unknown'},
                          {'access_type': 'metadata_only', 'pdf_path': None},
                          {'verification_status': 'copyright_hold'}):
            response = self.get('/maqolalar/ochiq-maqola/oqish',
                                articles=[article(**overrides)], fields=[field()])
            self.assertEqual(response.status_code, 404, overrides)

    def test_viewer_page_is_not_indexed(self):
        html = self.html('/maqolalar/ochiq-maqola/oqish',
                         articles=[article()], fields=[field()])
        self.assertIn('name="robots" content="noindex', html)

    def test_file_route_refuses_when_the_pdf_is_not_public(self):
        response = self.get('/maqolalar/ochiq-maqola/fayl',
                            articles=[article(license_type='unknown')], fields=[field()])
        self.assertEqual(response.status_code, 404)


class FieldBrowsingTests(ArticlePageTestCase):
    def _fields(self):
        return [
            field(id=1, code='13.00.00', name_uz='Pedagogika fanlari',
                  slug='pedagogika-fanlari-130000', level=1, article_count=0),
            field(id=2, parent_id=1, code='13.00.01', name_uz='Umumiy pedagogika',
                  slug='umumiy-pedagogika-130001', level=3, article_count=1),
        ]

    def test_hierarchy_page_renders_the_tree(self):
        html = self.html('/maqolalar/yonalishlar', fields=self._fields())
        self.assertIn('Pedagogika fanlari', html)
        self.assertIn('Umumiy pedagogika', html)

    def test_child_and_article_counts_are_shown(self):
        html = self.html('/maqolalar/yonalishlar', fields=self._fields())
        self.assertIn('ta ichki yo', html)
        self.assertIn('ta maqola', html)

    def test_children_are_collapsed_by_default_progressive_disclosure(self):
        html = self.html('/maqolalar/yonalishlar', fields=self._fields())
        self.assertIn('data-mq-tree-toggle', html)
        self.assertIn('aria-expanded', html)

    def test_empty_hierarchy_says_so_without_inventing_categories(self):
        html = self.html('/maqolalar/yonalishlar', fields=[])
        self.assertIn("hali to'ldirilmagan", html)

    def test_field_search_by_code_works(self):
        html = self.html('/maqolalar/yonalishlar?q=13.00.01', fields=self._fields())
        self.assertIn('Umumiy pedagogika', html)

    def test_field_detail_renders(self):
        response = self.get('/maqolalar/yonalish/pedagogika-fanlari-130000',
                            fields=self._fields(), articles=[article()],
                            authors=[author()], links=[link()])
        self.assertEqual(response.status_code, 200)

    def test_unknown_field_slug_is_a_404(self):
        response = self.get('/maqolalar/yonalish/yoq', fields=self._fields())
        self.assertEqual(response.status_code, 404)

    def test_top_branch_includes_descendant_articles_by_default(self):
        self.get('/maqolalar/yonalish/pedagogika-fanlari-130000', fields=self._fields())
        _sql, params = next((s, p) for s, p in self.store['sql']
                            if 'scientific_field_id = ANY(%s)' in s)
        self.assertIn(2, params[0])

    def test_a_leaf_specialty_shows_only_its_own_articles_by_default(self):
        self.get('/maqolalar/yonalish/umumiy-pedagogika-130001', fields=self._fields())
        _sql, params = next((s, p) for s, p in self.store['sql']
                            if 'scientific_field_id = ANY(%s)' in s)
        self.assertEqual(params[0], [2])

    def test_the_scope_can_be_switched_explicitly(self):
        self.get('/maqolalar/yonalish/umumiy-pedagogika-130001?faqat=0',
                 fields=self._fields())
        _sql, params = next((s, p) for s, p in self.store['sql']
                            if 'scientific_field_id = ANY(%s)' in s)
        self.assertEqual(params[0], [2])
        html = self.html('/maqolalar/yonalish/pedagogika-fanlari-130000?faqat=1',
                         fields=self._fields())
        self.assertIn('Faqat aynan shu', html)


class SeoTests(ArticlePageTestCase):
    def test_catalog_has_a_canonical_url(self):
        html = self.html('/maqolalar', fields=[field()])
        self.assertIn('rel="canonical"', html)
        self.assertIn('/maqolalar', html)

    def test_catalog_has_a_meta_description_and_open_graph(self):
        html = self.html('/maqolalar', fields=[field()])
        self.assertIn('name="description"', html)
        self.assertIn('property="og:title"', html)

    def test_article_detail_emits_scholarly_article_json_ld(self):
        html = self.html('/maqolalar/ochiq-maqola', articles=[article()],
                         fields=[field()], authors=[author()], links=[link()])
        self.assertIn('"ScholarlyArticle"', html)
        self.assertIn('Karimov Sardor', html)
        self.assertIn('10.1234/abc.2024', html)

    def test_json_ld_omits_fields_the_article_does_not_have(self):
        html = self.html('/maqolalar/ochiq-maqola',
                         articles=[article(doi=None, journal_name=None, pages=None)],
                         fields=[field()], authors=[author()], links=[link()])
        block = re.search(r'"@type": "ScholarlyArticle".*?</script>', html, re.S).group(0)
        self.assertNotIn('"isPartOf"', block)
        self.assertNotIn('"pagination"', block)

    def test_json_ld_never_claims_a_citation_count(self):
        html = self.html('/maqolalar/ochiq-maqola', articles=[article()],
                         fields=[field()], authors=[author()], links=[link()])
        self.assertNotIn('citationCount', html)

    def test_admin_preview_is_noindex(self):
        # Preview shabloni public bilan bir xil — farq faqat robots va badge.
        from flask import render_template
        with app.test_request_context('/admin/articles/1/preview'):
            html = render_template(
                'maqolalar_detail.html',
                article=dict(article(), can_read_pdf=False, pdf_block_reason='test',
                             authors=[], fields=[], primary_field=None,
                             access_label='—', version_label='—', license_label='—',
                             source_type_label='—', language_label='—',
                             verification_label='Qoralama', publication_label='Qoralama',
                             is_public=False),
                field_crumbs=[], preview=True)
        self.assertIn('name="robots" content="noindex, nofollow"', html)
        self.assertNotIn('ScholarlyArticle', html)


class AccessibilityTests(ArticlePageTestCase):
    PAGES = [
        ('/maqolalar', dict(articles=[article()], fields=[field()],
                            authors=[author()], links=[link()])),
        ('/maqolalar?view=jadval', dict(articles=[article()], fields=[field()],
                                        authors=[author()], links=[link()])),
        ('/maqolalar/yonalishlar', dict(fields=[field()])),
        ('/maqolalar/yonalish/pedagogika-fanlari-130000',
         dict(fields=[field()], articles=[article()], authors=[author()], links=[link()])),
        ('/maqolalar/ochiq-maqola', dict(articles=[article()], fields=[field()],
                                         authors=[author()], links=[link()])),
    ]

    def test_every_page_has_exactly_one_h1(self):
        for path, kwargs in self.PAGES:
            html = self.html(path, **kwargs)
            self.assertEqual(len(re.findall(r'<h1[\s>]', html)), 1, path)

    def test_every_module_input_and_select_has_a_label(self):
        # Faqat MAQOLALAR modulining maydonlari (mq-* prefiksi) tekshiriladi:
        # base.html shellidagi umumiy elementlar bu modulning mas'uliyati emas.
        checked = 0
        for path, kwargs in self.PAGES:
            html = self.html(path, **kwargs)
            ids = {i for i in re.findall(
                r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', html)
                if i.startswith('mq-')}
            if not ids:
                continue        # maqola tafsilotida forma yo'q — bu normal
            checked += 1
            labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', html))
            self.assertEqual(ids - labelled, set(), path)
        self.assertGreaterEqual(checked, 3, 'formali sahifalar tekshirilmadi')

    def test_table_view_uses_semantic_headers(self):
        html = self.html('/maqolalar?view=jadval', articles=[article()],
                         fields=[field()], authors=[author()], links=[link()])
        self.assertIn('<caption', html)
        self.assertIn('scope="col"', html)
        self.assertIn('scope="row"', html)

    def test_table_scrolls_instead_of_being_squashed_on_mobile(self):
        html = self.html('/maqolalar?view=jadval', articles=[article()],
                         fields=[field()], authors=[author()], links=[link()])
        self.assertIn('ol-table-wrap', html)

    def test_result_count_is_announced_to_assistive_tech(self):
        html = self.html('/maqolalar', fields=[field()])
        self.assertRegex(html, r'role="status"')

    def test_action_targets_declare_a_44px_minimum(self):
        css_path = app.root_path + '/static/css/maqolalar.css'
        with open(css_path, encoding='utf-8') as fh:
            css = fh.read()
        for selector in ('.mq-card__actions .ol-btn', '.mq-table__actions .ol-btn',
                         '.mq-tree__toggle', '.mq-form__check'):
            block = re.search(re.escape(selector) + r'\s*\{[^}]*\}', css)
            self.assertIsNotNone(block, selector)
            self.assertIn('44px', block.group(0), selector)

    def test_pager_is_a_labelled_navigation(self):
        html = self.html('/maqolalar', articles=[article(id=i) for i in range(1, 30)],
                         fields=[field()], authors=[author()], links=[link()])
        self.assertIn('aria-label="Sahifalash"', html)


class DesignSystemTests(ArticlePageTestCase):
    DARK_VALUES = re.compile(
        r'#(?:0f172a|1e293b|162032|334155|0b1220|111a2c|1a1d23|22262f|0f1114'
        r'|1e2330|1a1f2e|2a3140|3a4356|243044|131c2e|28344a|111c30)\b', re.I)

    def test_pages_carry_no_dark_theme_surfaces(self):
        for path, kwargs in AccessibilityTests.PAGES:
            html = self.html(path, **kwargs)
            self.assertEqual(sorted(set(self.DARK_VALUES.findall(html))), [], path)

    def test_module_css_only_uses_design_tokens_for_colour(self):
        with open(app.root_path + '/static/css/maqolalar.css', encoding='utf-8') as fh:
            css = fh.read()
        # Ranglar faqat var(--color-*) orqali; xom hex qiymat qo'shilmasin.
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b', css), [])

    def test_no_theme_switching_code_in_the_module(self):
        for name in ('static/css/maqolalar.css', 'static/js/maqolalar.js'):
            with open(app.root_path + '/' + name, encoding='utf-8') as fh:
                text = fh.read()
            self.assertNotRegex(text, r'(?i)theme-?toggle|prefers-color-scheme\s*:\s*dark')


if __name__ == '__main__':
    unittest.main()
