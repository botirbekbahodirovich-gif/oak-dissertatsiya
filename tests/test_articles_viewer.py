# -*- coding: utf-8 -*-
"""Ichki PDF o'quvchisi (Phase 2B.2) — kirish, xavfsizlik va vizual kontrakt.

Ikki qatlam tekshiriladi:

  * BACKEND — `/maqolalar/<slug>/oqish` va `/maqolalar/<slug>/pdf` route'lari
    qalbaki DB (tests/fake_pg.py) va HAQIQIY vaqtinchalik PDF fayl ustida.
    Fayl haqiqiy bo'lgani uchun `send_file` xatti-harakati (Range, MIME,
    Content-Disposition) o'ynatilgan emas, o'lchangan.
  * STATIK — shablon, CSS va JS matni ustida. Bu yerda brauzer yo'q, shuning
    uchun "vizual QA" da'vo qilinmaydi; faqat kontrakt (bitta H1, aria
    yorliqlari, 44px, xom rang yo'qligi, tashqi havola xavfsizligi) o'lchanadi.
"""
import io
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from app import app
from blueprints.articles import PDFJS_VERSION
from tests.fake_pg import FakeConnection, article, field, new_store
from tests.test_articles_catalog import author, fake_db, link

ROOT = app.root_path
VIEWER_TEMPLATE = os.path.join(ROOT, 'templates', 'maqolalar_oqish.html')
VIEWER_CSS = os.path.join(ROOT, 'static', 'css', 'maqolalar_viewer.css')
VIEWER_JS = os.path.join(ROOT, 'static', 'js', 'maqolalar_viewer.js')
VENDOR_DIR = os.path.join(ROOT, 'static', 'vendor', 'pdfjs')

# fake_pg.article() shu nisbiy yo'lni beradi — vaqtinchalik saqlash papkasida
# aynan shu joyga haqiqiy fayl yoziladi.
STORED_PATH = '2026/08/a.pdf'


def read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def sample_pdf(size=4096):
    """Kichik, lekin haqiqiy PDF: Range testi uchun bir necha kilobayt kerak."""
    head = (b'%PDF-1.4\n'
            b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
            b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
            b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n')
    tail = b'\ntrailer<</Root 1 0 R>>\n%%EOF\n'
    padding = b'%' + b'x' * max(0, size - len(head) - len(tail) - 1) + b'\n'
    return head + padding + tail


class ViewerRouteTestCase(unittest.TestCase):
    """Haqiqiy fayl bilan ishlaydigan asos: storage papkasi vaqtinchalik."""

    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.pdf_bytes = sample_pdf()

    def setUp(self):
        self.storage = tempfile.mkdtemp(prefix='olimlar-pdf-test-')
        target = os.path.join(self.storage, *STORED_PATH.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(self.pdf_bytes)
        self.addCleanup(shutil.rmtree, self.storage, True)

    def get(self, path, articles=None, headers=None, store_pdf=True):
        store = new_store(articles=articles if articles is not None else [article()],
                          fields=[field()], authors=[author()], links=[link()])
        connection = FakeConnection(store)
        storage = self.storage if store_pdf else os.path.join(self.storage, 'yoq')
        with fake_db(connection), \
                mock.patch('blueprints.articles.storage_dir', return_value=storage):
            response = self.client.get(path, headers=headers or {})
        # send_file WSGI file wrapper'ini yopish: aks holda testlar ochiq fayl
        # deskriptorlarini qoldiradi (ResourceWarning).
        response.get_data()
        response.close()
        self.store = store
        return response

    def module_updates(self, needle):
        """Faqat MAQOLA jadvalidagi UPDATE'lar — sxema DDL'i emas."""
        return [sql for sql, _p in self.store['sql']
                if sql.upper().startswith('UPDATE') and needle in sql]


class ViewerAccessTests(ViewerRouteTestCase):
    """§4, §32 — o'quvchi ham, oqim ham AYNAN can_read_pdf() ga bo'ysunadi."""

    CLOSED = {
        'qoralama': {'publication_status': 'draft'},
        'tekshirilmagan': {'verification_status': 'pending_review'},
        'faqat-metadata': {'access_type': 'metadata_only', 'pdf_path': None},
        'litsenziya-nomalum': {'license_type': 'unknown'},
        'mualliflik-toxtatilgan': {'verification_status': 'copyright_hold'},
        'manba-korsatilmagan': {'source_name': '  '},
    }

    def test_public_article_opens_the_viewer(self):
        response = self.get('/maqolalar/ochiq-maqola/oqish')
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-mqv="root"', response.get_data(as_text=True))

    def test_public_article_streams_its_pdf(self):
        response = self.get('/maqolalar/ochiq-maqola/pdf')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), self.pdf_bytes)

    def test_closed_articles_are_404_on_both_routes(self):
        for name, overrides in self.CLOSED.items():
            for path in ('/maqolalar/ochiq-maqola/oqish', '/maqolalar/ochiq-maqola/pdf'):
                response = self.get(path, articles=[article(**overrides)])
                self.assertEqual(response.status_code, 404, '%s %s' % (name, path))

    def test_missing_file_is_a_safe_404_not_a_500(self):
        response = self.get('/maqolalar/ochiq-maqola/pdf', store_pdf=False)
        self.assertEqual(response.status_code, 404)

    def test_missing_file_never_leaks_the_filesystem_path(self):
        response = self.get('/maqolalar/ochiq-maqola/pdf', store_pdf=False)
        body = response.get_data(as_text=True)
        self.assertNotIn(self.storage, body)
        self.assertNotIn(STORED_PATH, body)

    def test_traversal_in_the_stored_key_cannot_escape_the_storage_root(self):
        # DB qo'lda buzilgan bo'lsa ham storage_dir() dan tashqariga chiqilmaydi.
        outside = os.path.join(self.storage, 'tashqarida.pdf')
        with open(outside, 'wb') as fh:
            fh.write(b'%PDF-1.4 maxfiy')
        for evil in ('../tashqarida.pdf', '../../etc/passwd', '/etc/passwd',
                     '2026/08/../../../tashqarida.pdf'):
            response = self.get('/maqolalar/ochiq-maqola/pdf',
                                articles=[article(pdf_path=evil)])
            self.assertEqual(response.status_code, 404, evil)

    def test_the_stream_does_not_count_a_download(self):
        # Har canvas render'i yoki Range so'rovi hisobni oshirmasin (§26).
        self.get('/maqolalar/ochiq-maqola/pdf')
        self.assertEqual(self.module_updates('download_count'), [])

    def test_the_viewer_page_does_not_count_a_view_twice(self):
        # Ko'rish maqola tafsilotida hisoblangan; o'quvchi uni takrorlamaydi.
        self.get('/maqolalar/ochiq-maqola/oqish')
        self.assertEqual(self.module_updates('view_count'), [])

    def test_the_download_route_still_counts_a_download(self):
        self.get('/maqolalar/ochiq-maqola/fayl')
        self.assertEqual(len(self.module_updates('download_count')), 1)


class ViewerResponseHeaderTests(ViewerRouteTestCase):
    """§5, §7, §29 — oqim sarlavhalari."""

    def response(self, headers=None):
        return self.get('/maqolalar/ochiq-maqola/pdf', headers=headers)

    def test_content_type_is_pdf(self):
        self.assertEqual(self.response().headers['Content-Type'], 'application/pdf')

    def test_disposition_is_inline_and_hides_the_storage_key(self):
        disposition = self.response().headers['Content-Disposition']
        self.assertTrue(disposition.startswith('inline'), disposition)
        self.assertIn('ochiq-maqola.pdf', disposition)
        # Diskdagi kalit (2026/08/a.pdf) nomga tushmaydi — u slug'dan quriladi.
        self.assertNotIn('2026', disposition)
        self.assertNotIn('/', disposition.split('filename=')[1])

    def test_nosniff_is_set(self):
        self.assertEqual(self.response().headers['X-Content-Type-Options'], 'nosniff')

    def test_the_pdf_itself_is_not_indexable(self):
        self.assertIn('noindex', self.response().headers['X-Robots-Tag'])

    def test_cache_is_private_so_a_takedown_is_not_served_by_a_shared_cache(self):
        cache = self.response().headers['Cache-Control']
        self.assertIn('private', cache)
        self.assertNotIn('public', cache)

    def test_range_requests_are_supported(self):
        """PDF.js hujjatni bo'laklab so'raydi — bu shart, optimizatsiya emas."""
        full = self.response()
        self.assertEqual(full.headers.get('Accept-Ranges'), 'bytes')

        partial = self.response(headers={'Range': 'bytes=0-1023'})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(len(partial.get_data()), 1024)
        self.assertEqual(partial.get_data(), self.pdf_bytes[:1024])
        self.assertEqual(
            partial.headers['Content-Range'],
            'bytes 0-1023/%d' % len(self.pdf_bytes))

    def test_a_second_range_window_returns_the_matching_slice(self):
        partial = self.response(headers={'Range': 'bytes=1024-2047'})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.get_data(), self.pdf_bytes[1024:2048])

    def test_an_unsatisfiable_range_does_not_500(self):
        response = self.response(headers={'Range': 'bytes=999999-1000000'})
        self.assertIn(response.status_code, (206, 416))

    def test_the_download_route_stays_an_attachment(self):
        response = self.get('/maqolalar/ochiq-maqola/fayl')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers['Content-Disposition'].startswith('attachment'))


class StorageIsNotPubliclyReachableTests(ViewerRouteTestCase):
    """§6 — PDF hech qachon static URL sifatida berilmaydi."""

    def test_storage_is_outside_the_static_folder(self):
        with app.test_request_context('/'):
            from blueprints.articles import storage_dir
            resolved = os.path.realpath(storage_dir())
        static_root = os.path.realpath(app.static_folder)
        self.assertFalse(resolved.startswith(static_root + os.sep))
        self.assertNotEqual(resolved, static_root)

    def test_there_is_no_route_serving_the_storage_folder(self):
        rules = [str(rule) for rule in app.url_map.iter_rules()]
        self.assertEqual([r for r in rules if r.startswith('/storage')], [])

    def test_direct_storage_urls_are_not_served(self):
        for path in ('/storage/articles/2026/08/a.pdf',
                     '/static/../storage/articles/2026/08/a.pdf',
                     '/static/storage/articles/2026/08/a.pdf'):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 200, path)

    def test_the_viewer_html_never_exposes_a_filesystem_path_or_storage_key(self):
        html = self.get('/maqolalar/ochiq-maqola/oqish').get_data(as_text=True)
        self.assertNotIn(STORED_PATH, html)
        self.assertNotIn(self.storage, html)
        self.assertNotIn(app.root_path, html)
        # Faqat himoyalangan route manzili bo'lishi kerak.
        self.assertIn('data-pdf-url="/maqolalar/ochiq-maqola/pdf"', html)


class ViewerSeoTests(ViewerRouteTestCase):
    """§29 — o'quvchi indekslanmaydi, kanonik sahifa maqola tafsiloti."""

    def html(self):
        return self.get('/maqolalar/ochiq-maqola/oqish').get_data(as_text=True)

    def test_viewer_is_noindex_nofollow(self):
        self.assertIn('<meta name="robots" content="noindex, nofollow">', self.html())

    def test_only_one_robots_directive_is_emitted(self):
        self.assertEqual(len(re.findall(r'name="robots"', self.html())), 1)

    def test_canonical_points_at_the_article_detail_page(self):
        html = self.html()
        self.assertIn('rel="canonical" href="https://olimlar.uz/maqolalar/ochiq-maqola"',
                      html)
        self.assertEqual(len(re.findall(r'rel="canonical"', html)), 1)

    def test_the_route_also_sends_the_header(self):
        response = self.get('/maqolalar/ochiq-maqola/oqish')
        self.assertIn('noindex', response.headers['X-Robots-Tag'])


class ViewerAccessibilityTests(ViewerRouteTestCase):
    """§22 — o'quvchi klaviatura va skrinrider bilan ishlatiladi."""

    def setUp(self):
        super().setUp()
        self.page = self.get('/maqolalar/ochiq-maqola/oqish').get_data(as_text=True)

    def test_exactly_one_h1(self):
        self.assertEqual(len(re.findall(r'<h1[\s>]', self.page)), 1)

    def test_every_icon_only_button_has_an_uzbek_aria_label(self):
        # Faqat o'quvchi shabloni: base.html shelli bu modulning mas'uliyati emas.
        markup = read(VIEWER_TEMPLATE)
        buttons = re.findall(r'<button\b[^>]*class="[^"]*ol-icon-btn[^"]*"[^>]*>', markup)
        self.assertGreaterEqual(len(buttons), 8)
        for tag in buttons:
            self.assertIn('aria-label="', tag, tag)

    def test_decorative_icons_are_hidden_from_assistive_tech(self):
        icons = re.findall(r'<i class="bi [^"]+"[^>]*>', read(VIEWER_TEMPLATE))
        self.assertTrue(icons)
        for tag in icons:
            self.assertIn('aria-hidden="true"', tag, tag)

    def test_control_groups_are_named(self):
        for group in re.findall(r'<div class="mqv-bar__group[^"]*"[^>]*>', self.page):
            self.assertIn('aria-label="', group, group)

    def test_loading_and_error_states_are_announced(self):
        self.assertIn('data-mqv="loading"', self.page)
        self.assertIn('aria-live="polite"', self.page)
        self.assertRegex(self.page, r'role="alert"[^>]*hidden|hidden[^>]*role="alert"')

    def test_the_active_page_is_announced(self):
        self.assertIn('data-mqv="announce"', self.page)
        self.assertIn('role="status"', self.page)

    def test_the_drawer_toggle_declares_its_relationship(self):
        toggle = re.search(r'<button[^>]*data-mqv="side-toggle"[^>]*>', self.page).group(0)
        self.assertIn('aria-expanded=', toggle)
        self.assertIn('aria-controls="mqv-side"', toggle)

    def test_every_viewer_form_control_has_a_label(self):
        ids = {i for i in re.findall(
            r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', self.page)
            if i.startswith('mq-')}
        self.assertTrue(ids)
        labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', self.page))
        self.assertEqual(ids - labelled, set())

    def test_the_canvas_area_carries_an_accessible_name(self):
        region = re.search(r'<div class="mqv-doc"[^>]*>', self.page).group(0)
        self.assertIn('aria-label="', region)
        self.assertIn('role="region"', region)

    def test_a_no_script_fallback_points_back_to_the_article(self):
        self.assertIn('<noscript>', self.page)
        self.assertIn('/maqolalar/ochiq-maqola', self.page)


class ViewerDesignSystemTests(unittest.TestCase):
    """§11 — o'quvchi Olimlar.uz dizayn tizimidan chiqib ketmaydi."""

    @classmethod
    def setUpClass(cls):
        cls.css = read(VIEWER_CSS)
        cls.js = read(VIEWER_JS)
        cls.markup = read(VIEWER_TEMPLATE)

    def test_no_raw_colour_literals_in_the_viewer_css(self):
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b', self.css), [])
        self.assertEqual(re.findall(r'\brgba?\(', self.css), [])
        self.assertEqual(re.findall(r'\bhsla?\(', self.css), [])

    def test_no_gradient_blur_or_oversized_shadow(self):
        for pattern in ('gradient', 'backdrop-filter', 'filter: blur'):
            self.assertNotIn(pattern, self.css, pattern)
        # Yagona ruxsat etilgan soya — dizayn tizimi tokeni.
        for match in re.findall(r'box-shadow:\s*([^;]+);', self.css):
            self.assertIn('var(--shadow-dropdown)', match, match)

    def test_no_dark_theme_anywhere_in_the_viewer(self):
        for name, text in (('css', self.css), ('js', self.js), ('html', self.markup)):
            self.assertNotRegex(text, r'(?i)theme-?toggle', name)
            self.assertNotRegex(text, r'color-scheme\s*:\s*[^;}\n]*dark', name)
        self.assertNotIn('--color-ai-background', self.css)

    def test_the_toolbar_surface_is_light(self):
        block = re.search(r'\.mqv-bar\s*\{[^}]*\}', self.css).group(0)
        self.assertIn('var(--color-white)', block)

    def test_no_decorative_emoji_in_the_viewer(self):
        emoji = re.compile('[\U0001F300-\U0001FAFF←-⇿☀-➿]')
        for name, text in (('css', self.css), ('js', self.js), ('html', self.markup)):
            self.assertEqual(emoji.findall(text), [], name)

    def test_interactive_targets_declare_the_44px_minimum(self):
        for selector in ('.mqv-bar__page', '.mqv-bar__zoom', '.mqv-thumb',
                         '.mqv-outline__link', '.mqv-menu__item'):
            block = re.search(re.escape(selector) + r'\s*\{[^}]*\}', self.css)
            self.assertIsNotNone(block, selector)
            self.assertIn('44px', block.group(0), selector)

    def test_the_drawer_has_a_mobile_breakpoint(self):
        self.assertIn('@media (max-width: 767px)', self.css)
        self.assertIn('[data-side="open"] .mqv-side', self.css)

    def test_static_assets_carry_a_cache_buster(self):
        for href in re.findall(r'(?:href|src)="(/static/[^"]+)"', self.markup):
            self.assertIn('?v=', href, href)


class ViewerSecurityContractTests(unittest.TestCase):
    """§16, §30, §31 — PDF ichidagi kod ijro etilmaydi, CSP bo'shatilmaydi."""

    @classmethod
    def setUpClass(cls):
        cls.js = read(VIEWER_JS)
        cls.markup = read(VIEWER_TEMPLATE)

    def test_pdf_scripting_is_disabled(self):
        self.assertIn('enableScripting: false', self.js)

    def test_the_pdf_javascript_engine_is_not_even_shipped(self):
        # quickjs — PDF ichidagi JS dvigateli; serverda umuman bo'lmasligi kerak.
        wasm = os.listdir(os.path.join(VENDOR_DIR, 'wasm'))
        self.assertEqual([f for f in wasm if 'quickjs' in f], [])

    def test_eval_is_not_required_so_the_csp_stays_strict(self):
        self.assertIn('isEvalSupported: false', self.js)
        from security import _CSP
        self.assertNotIn("'unsafe-eval'", _CSP)
        # Worker `'self'` dan yuklanadi — blob: yoki tashqi host kerak emas.
        self.assertNotIn('worker-src', _CSP)
        self.assertIn("default-src 'self'", _CSP)

    def test_no_native_pdf_embed_is_used_as_the_viewer(self):
        for tag in ('<iframe', '<embed', '<object'):
            self.assertNotIn(tag, self.markup, tag)

    def test_external_links_open_safely(self):
        self.assertIn("rel = 'noopener noreferrer nofollow'".replace("'", ''),
                      self.js.replace("'", ''))
        self.assertIn("target = '_blank'", self.js)

    def test_link_urls_are_protocol_checked(self):
        block = re.search(r'function safeExternalUrl\([^)]*\)\s*\{.*?\n\}',
                          self.js, re.S).group(0)
        self.assertIn("parsed.protocol !== 'http:'", block)
        self.assertIn("parsed.protocol !== 'https:'", block)

    def test_no_fake_drm_tricks(self):
        for pattern in ('contextmenu', 'user-select: none', 'oncopy',
                        'preventDefault();\n      return false'):
            self.assertNotIn(pattern, self.markup, pattern)
        # Matn tanlash faqat PDF.js span'lari uchun cheklanadi (rasm roli),
        # sahifa matni uchun emas.
        self.assertNotIn("document.addEventListener('contextmenu'", self.js)

    def test_no_download_button_in_the_viewer(self):
        """§23 — o'qish huquqi yuklab olish huquqi EMAS."""
        self.assertNotIn('/fayl', self.markup)
        self.assertNotIn('download', self.markup.lower())

    def test_no_explicit_print_button_in_the_viewer(self):
        """§24 — chop etish huquqi ham litsenziyaga bog'liq, majburlanmaydi."""
        self.assertNotIn('window.print', self.js)
        self.assertNotIn('bi-printer', self.markup)


class VendoredPdfJsTests(unittest.TestCase):
    """§3, §8 — vendoring to'liq, versiyalar bir xil, atributsiya saqlangan."""

    def test_the_build_and_the_worker_are_present(self):
        for name in ('pdf.min.js', 'pdf.worker.min.js', 'LICENSE', 'README.md'):
            self.assertTrue(os.path.isfile(os.path.join(VENDOR_DIR, name)), name)

    def test_the_licence_attribution_is_kept(self):
        licence = read(os.path.join(VENDOR_DIR, 'LICENSE'))
        self.assertIn('Apache License', licence)

    def test_the_library_and_the_worker_are_the_same_version(self):
        needle = '"%s"' % PDFJS_VERSION
        for name in ('pdf.min.js', 'pdf.worker.min.js'):
            with io.open(os.path.join(VENDOR_DIR, name), encoding='utf-8',
                         errors='replace') as fh:
                self.assertIn(needle, fh.read(), name)

    def test_the_template_wires_both_files_from_the_same_version(self):
        markup = read(VIEWER_TEMPLATE)
        self.assertIn('data-lib="/static/vendor/pdfjs/pdf.min.js?v={{ pdfjs_version }}"',
                      markup)
        self.assertIn('data-worker="/static/vendor/pdfjs/pdf.worker.min.js'
                      '?v={{ pdfjs_version }}"', markup)

    def test_the_viewer_verifies_the_version_at_runtime(self):
        js = read(VIEWER_JS)
        self.assertIn('pdfjsLib.version !== data.version', js)

    def test_a_worker_failure_is_diagnosed_not_silently_faked(self):
        js = read(VIEWER_JS)
        self.assertIn("port.addEventListener('error'", js)
        self.assertIn('Worker yuklanmadi', js)

    def test_the_upstream_dark_mode_stylesheet_was_not_vendored(self):
        self.assertFalse(os.path.exists(os.path.join(VENDOR_DIR, 'pdf_viewer.css')))
        self.assertFalse(os.path.exists(os.path.join(VENDOR_DIR, 'web')))


if __name__ == '__main__':
    unittest.main()
