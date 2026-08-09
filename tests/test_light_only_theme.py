"""Olimlar.uz light-only — kun/tun almashtirgichi qaytib kelmasligi kerak.

Faqat loyiha o'zi yozgan aktiv kod tekshiriladi: git submodule
(oak-dissertatsiya/), worktree checkoutlari, venv va uchinchi tomon /
minifikatsiya qilingan fayllar tashqarida qoladi.
"""
import io
import os
import re
import unittest

from app import app

ROOT = app.root_path

SKIP_DIRS = {
    '.git', 'node_modules', '__pycache__', 'venv', '.venv', 'wsl_venv',
    'generated_docs', 'artifacts', 'uploads', 'docs',
    'oak-dissertatsiya',   # git submodule — alohida checkout
    '.agents',             # agent skill hujjatlari, loyiha kodi emas
}
SKIP_SUFFIX = ('.min.js', '.min.css', '.map')
SOURCE_EXT = ('.py', '.html', '.css', '.js', '.json')

# Sayt chindan ham to'q bo'lishi kerak bo'lgan joylar (global theme emas).
SEMANTIC_DARK_OK = {
    'static/css/olimlar-design-system.css',   # --color-ai-background tokeni
    'static/js/olimlar-charts.js',            # to'q tooltip foni
    'templates/base.html',                    # legacy bridge izohlari
    'tests/test_light_only_theme.py',
    'tests/test_design_system_pages.py',
}


SELF = 'tests/test_light_only_theme.py'


def source_files():
    """Loyihaning o'z aktiv manba fayllari. Bu testning o'zi ham chiqariladi:
    u tekshirayotgan naqshlarni matn sifatida o'z ichida saqlaydi."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.endswith('-worktree')]
        for fn in filenames:
            if not fn.endswith(SOURCE_EXT) or fn.endswith(SKIP_SUFFIX):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
            if rel == SELF:
                continue
            out.append((rel, path))
    return out


def read(path):
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


class RenderedHtmlHasNoThemeSwitch(unittest.TestCase):
    """DB'ga tegmaydigan public sahifalar orqali tekshiriladi."""

    # DB'ga tegmaydigan sahifalar. /maqolalar va /maqolalar/yonalishlar Phase
    # 2B.1 da haqiqiy katalogga aylandi va endi Postgres talab qiladi — lokal
    # muhitda ular 500 qaytaradi va bu tekshiruv xato sahifani "tekshirgan"
    # bo'lib qolardi. Maqolalar sahifalarining light-only holati
    # tests/test_articles_catalog.py:DesignSystemTests da (qalbaki DB bilan)
    # tekshiriladi.
    PAGES = ['/about', '/login', '/maxfiylik', '/oferta']

    @classmethod
    def setUpClass(cls):
        client = app.test_client()
        cls.pages = {p: client.get(p).get_data(as_text=True) for p in cls.PAGES}

    def test_no_theme_toggle_control(self):
        for path, html in self.pages.items():
            for marker in ('theme-toggle', 'themeToggle', 'toggleTheme',
                           'btn-theme-toggle'):
                self.assertNotIn(marker, html, msg='%s: %s' % (path, marker))

    def test_no_dark_theme_attribute(self):
        for path, html in self.pages.items():
            self.assertNotIn('data-bs-theme="dark"', html, msg=path)
            self.assertNotIn("data-bs-theme='dark'", html, msg=path)

    def test_no_theme_storage_code(self):
        for path, html in self.pages.items():
            self.assertNotIn('oak-theme', html, msg=path)
            self.assertFalse(re.search(r'localStorage[^\n]{0,40}theme', html, re.I),
                             msg='%s: localStorage theme kodi topildi' % path)

    def test_no_day_night_aria_labels(self):
        needles = ('Tun rejimi', 'Kun rejimi', 'Mavzuni', 'dark mode', 'light mode')
        for path, html in self.pages.items():
            for n in needles:
                self.assertNotIn(n, html, msg='%s: %r' % (path, n))

    def test_no_empty_button_left_behind(self):
        for path, html in self.pages.items():
            self.assertFalse(re.search(r'<button[^>]*>\s*</button>', html),
                             msg='%s: bo\'sh button qoldi' % path)

    def test_color_scheme_meta_is_light(self):
        for path, html in self.pages.items():
            self.assertIn('name="color-scheme" content="light"', html, msg=path)


class SourceTreeIsLightOnly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = source_files()

    def test_no_dark_theme_selectors_anywhere(self):
        offenders = [rel for rel, path in self.files
                     if 'data-bs-theme="dark"' in read(path)]
        self.assertEqual(offenders, [], msg='o\'lik dark selektorlar: %s' % offenders)

    def test_no_prefers_color_scheme_dark(self):
        offenders = [rel for rel, path in self.files
                     if re.search(r'prefers-color-scheme\s*:\s*dark', read(path))]
        self.assertEqual(offenders, [], msg=offenders)

    def test_no_color_scheme_dark_declaration(self):
        offenders = []
        for rel, path in self.files:
            for m in re.finditer(r'color-scheme\s*:\s*([^;}\n"\']+)', read(path)):
                if 'dark' in m.group(1):
                    offenders.append('%s -> %s' % (rel, m.group(1).strip()))
        self.assertEqual(offenders, [], msg=offenders)

    def test_no_theme_toggle_code_left(self):
        offenders = [rel for rel, path in self.files
                     if re.search(r'theme-?toggle|toggleTheme|themeToggle',
                                  read(path), re.I)]
        self.assertEqual(offenders, [], msg=offenders)

    def test_no_system_theme_detection(self):
        """matchMedia faqat hover / reduced-motion / display-mode uchun."""
        offenders = []
        for rel, path in self.files:
            for m in re.finditer(r'matchMedia\(\s*[\'"]([^\'"]+)', read(path)):
                if 'color-scheme' in m.group(1):
                    offenders.append('%s -> %s' % (rel, m.group(1)))
        self.assertEqual(offenders, [], msg=offenders)

    def test_no_theme_preference_storage(self):
        offenders = [rel for rel, path in self.files
                     if re.search(r'(localStorage|sessionStorage)[^\n]{0,40}theme',
                                  read(path), re.I)]
        self.assertEqual(offenders, [], msg=offenders)

    def test_no_backend_theme_preference(self):
        """Foydalanuvchi modelida yoki sozlamalarda theme maydoni bo'lmasin."""
        offenders = []
        for rel, path in self.files:
            if not rel.endswith('.py'):
                continue
            if re.search(r'\b(dark_mode|theme_preference|user\.theme)\b', read(path)):
                offenders.append(rel)
        self.assertEqual(offenders, [], msg=offenders)


class DesignTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = read(os.path.join(ROOT, 'static/css/olimlar-design-system.css'))

    def test_root_declares_light_color_scheme(self):
        self.assertRegex(self.css, r':root\s*\{[^}]*color-scheme:\s*light')

    def test_font_serif_is_an_actual_serif(self):
        m = re.search(r'--font-serif:\s*([^;]+);', self.css)
        self.assertIsNotNone(m, 'token yo\'q')
        stack = m.group(1)
        self.assertNotIn('Manrope', stack,
                         'Manrope sans-serif — u --font-serif ichida bo\'lmasligi kerak')
        self.assertTrue(stack.rstrip().endswith('serif'),
                        'serif stack `serif` bilan tugashi kerak: %s' % stack)
        self.assertTrue('Georgia' in stack or 'Noto Serif' in stack, stack)

    def test_headings_use_the_serif_token(self):
        self.assertRegex(self.css, r'h1,\s*h2\s*\{[^}]*var\(--font-serif\)')

    def test_subheadings_use_the_sans_token(self):
        self.assertRegex(self.css, r'h3,\s*h4,\s*h5,\s*h6\s*\{[^}]*var\(--font-sans\)')


class ManifestTests(unittest.TestCase):
    def test_manifest_colours_are_light(self):
        import json
        with io.open(os.path.join(ROOT, 'static/manifest.json'), encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertEqual(data['background_color'].lower(), '#ffffff')
        self.assertEqual(data['theme_color'].lower(), '#ffffff')

    def test_apple_status_bar_is_default(self):
        html = app.test_client().get('/about').get_data(as_text=True)
        self.assertIn('apple-mobile-web-app-status-bar-style" content="default"', html)


class DeadTemplateTests(unittest.TestCase):
    def test_old_landing_page_is_gone(self):
        self.assertFalse(
            os.path.exists(os.path.join(ROOT, 'templates', 'index.html')),
            'templates/index.html o\'lik edi va o\'chirilgan — qayta qo\'shilmasin')

    def test_removal_is_documented(self):
        path = os.path.join(ROOT, 'docs', 'dead-templates.md')
        self.assertTrue(os.path.exists(path))
        self.assertIn('templates/index.html', read(path))


if __name__ == '__main__':
    unittest.main()
