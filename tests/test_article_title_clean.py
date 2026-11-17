# -*- coding: utf-8 -*-
"""Maqola sarlavhasini tozalash (clean_title) — docs/open-issues.md, 5-band.

Entity'lar (jumladan ikki marta kodlangani) ochiladi, inline teglar olib
tashlanadi, bo'shliqlar yig'iladi; LaTeX va oddiy sarlavha o'zgarmaydi.
"""
import os
import unittest

os.environ.setdefault('APP_ENV', 'testing')

from services.article_metadata.normalizer import clean_title  # noqa: E402

LATEX = [
    r'Search for exotic decays of the Higgs boson to a pair of pseudoscalars in the '
    r'$$\upmu \upmu \text{ b } \text{ b } $$ and $$\uptau \uptau \text{ b } \text{ b } $$ final states',
    r'Measurement of the $\alpha$-decay of $^{3}$He with \alpha and \beta_{2}',
    r'Constraints on $m_{\nu} < 0.8$ eV',
]
PLAIN = [
    'Raqamli iqtisodiyot sharoitida kichik biznesni rivojlantirish',
    "O'zbekistonda paxta yetishtirishning iqtisodiy samaradorligi",
    'Research on ultra-thin cadmium telluride heterojunction thin film solar cells',
    'R&D investment and growth: evidence from Central Asia',
    'When a < b > c holds: an inequality study',
    'COVID-19 va ta’lim: “masofaviy” o‘qitish',
]


class CleanTitleTests(unittest.TestCase):
    def test_entity_is_unescaped(self):
        self.assertEqual(clean_title('A &gt;200 Wh kg−1 anode-free Na pouch battery'),
                         'A >200 Wh kg−1 anode-free Na pouch battery')

    def test_double_encoded_entity_is_unescaped(self):
        self.assertEqual(clean_title('A &amp;gt;200 Wh kg−1 battery'), 'A >200 Wh kg−1 battery')
        self.assertEqual(clean_title('Smith &amp;amp; Sons'), 'Smith & Sons')
        self.assertEqual(clean_title('Na&#8211;ion &amp;#946;-phase'), 'Na–ion β-phase')

    def test_inline_tags_are_removed(self):
        self.assertEqual(clean_title('Effect of <i>teg</i> on growth'), 'Effect of teg on growth')
        self.assertEqual(clean_title('CO<sub>2</sub> capture by <sup>13</sup>C'), 'CO2 capture by 13C')
        self.assertEqual(clean_title('<jats:italic>E. coli</jats:italic> strains'), 'E. coli strains')
        self.assertEqual(clean_title('<span class="x">Bold</span> claim<br/>part two'),
                         'Bold claim part two')

    def test_encoded_tags_are_removed_too(self):
        self.assertEqual(clean_title('Effect of &lt;i&gt;teg&lt;/i&gt; on growth'),
                         'Effect of teg on growth')
        self.assertEqual(clean_title('H&amp;lt;sub&amp;gt;2&amp;lt;/sub&amp;gt;O'), 'H2O')

    def test_whitespace_is_normalized(self):
        self.assertEqual(clean_title('  A\n\ttitle  with   gaps  '), 'A title with gaps')

    def test_latex_is_left_untouched(self):
        for title in LATEX:
            with self.subTest(title=title[:40]):
                self.assertEqual(clean_title(title), title)

    def test_plain_titles_are_unchanged(self):
        for title in PLAIN:
            with self.subTest(title=title):
                self.assertEqual(clean_title(title), title)

    def test_idempotent(self):
        samples = LATEX + PLAIN + ['A &amp;gt;200', '&lt;i&gt;x&lt;/i&gt; y', 'CO<sub>2</sub>']
        for title in samples:
            with self.subTest(title=title[:40]):
                once = clean_title(title)
                self.assertEqual(clean_title(once), once)

    def test_empty_and_length(self):
        self.assertIsNone(clean_title(None))
        self.assertIsNone(clean_title('   '))
        self.assertIsNone(clean_title('<i></i>'))
        self.assertEqual(len(clean_title('x' * 600)), 500)
        self.assertEqual(clean_title('abc def', max_length=4), 'abc')


class ProviderPathsUseCleanTitleTests(unittest.TestCase):
    """Har bir provayder sarlavhani clean_title orqali o'tkazadi."""

    RAW = 'A &amp;gt;200 Wh <i>kg</i>−1 battery'
    CLEAN = 'A >200 Wh kg−1 battery'

    def test_openalex_metadata(self):
        from services.article_metadata.openalex import extract
        self.assertEqual(extract({'title': self.RAW})[0]['title'], self.CLEAN)

    def test_crossref_metadata(self):
        from services.article_metadata.crossref import extract
        self.assertEqual(extract({'title': [self.RAW]})[0]['title'], self.CLEAN)

    def test_unpaywall_metadata(self):
        from services.article_metadata.unpaywall import extract
        self.assertEqual(extract({'title': self.RAW})[0]['title'], self.CLEAN)

    def test_openalex_discovery(self):
        from services.article_discovery.openalex import _extract_candidate
        candidate = _extract_candidate({'id': 'https://openalex.org/W1234567',
                                        'doi': 'https://doi.org/10.1234/abc',
                                        'title': self.RAW})
        self.assertEqual(candidate.title, self.CLEAN)

    def test_crossref_discovery(self):
        from services.article_discovery.crossref import _extract_candidate
        candidate = _extract_candidate({'DOI': '10.1234/abc', 'title': [self.RAW]})
        self.assertEqual(candidate.title, self.CLEAN)


class AdminFormCleansTitlesTests(unittest.TestCase):
    def test_title_and_title_uz_are_cleaned(self):
        from app import app
        from blueprints.articles import _form_values
        with app.test_request_context('/admin/articles/new', method='POST', data={
                'title': '  A &amp;gt;200 <i>Wh</i> ', 'title_uz': '&lt;i&gt;Sarlavha&lt;/i&gt;',
                'access_type': 'metadata_only'}):
            values = _form_values()
        self.assertEqual(values['title'], 'A >200 Wh')
        self.assertEqual(values['title_uz'], 'Sarlavha')

    def test_empty_titles_stay_empty_strings(self):
        from app import app
        from blueprints.articles import _form_values
        with app.test_request_context('/admin/articles/new', method='POST', data={}):
            values = _form_values()
        self.assertEqual((values['title'], values['title_uz']), ('', ''))


class FixArticleTitlesScriptTests(unittest.TestCase):
    def test_plan_changes_only_lists_real_differences(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'scripts', 'fix_article_titles.py')
        spec = importlib.util.spec_from_file_location('fix_article_titles', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [
            {'id': 1, 'title': 'A &gt;200 Wh', 'title_uz': None},
            {'id': 2, 'title': LATEX[0], 'title_uz': ''},
            {'id': 3, 'title': 'Plain', 'title_uz': '&lt;i&gt;Uz&lt;/i&gt;'},
            {'id': 4, 'title': '<i></i>', 'title_uz': None},   # bo'sh qolardi -> tegilmaydi
        ]
        self.assertEqual(module.plan_changes(rows), [
            (1, 'title', 'A &gt;200 Wh', 'A >200 Wh'),
            (3, 'title_uz', '&lt;i&gt;Uz&lt;/i&gt;', 'Uz'),
        ])


if __name__ == '__main__':
    unittest.main()
