import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import check_contrast_tokens as cc  # noqa: E402


class ContrastMathTests(unittest.TestCase):
    def test_black_on_white_is_21(self):
        self.assertAlmostEqual(cc.ratio((0, 0, 0), (255, 255, 255)), 21.0, places=2)

    def test_same_colour_is_1(self):
        self.assertAlmostEqual(cc.ratio((10, 20, 30), (10, 20, 30)), 1.0, places=6)

    def test_var_chains_resolve(self):
        tokens = cc.load_tokens()
        self.assertEqual(tokens['color-action'], tokens['color-action-blue'])
        self.assertEqual(tokens['color-premium'], '#1f2a44')


class DesignTokenContrastTests(unittest.TestCase):
    def test_every_declared_pair_meets_wcag_aa(self):
        failures = [f'--color-{fg} on --color-{bg}: {r and round(r, 2)} < {m} ({use})'
                    for fg, bg, _, _, r, m, use, ok in cc.check() if not ok]
        self.assertEqual(failures, [])

    def test_approved_palette_values(self):
        t = cc.load_tokens()
        self.assertEqual(t['color-brand-orange'], '#e8631a')
        self.assertEqual(t['color-action-blue'], '#0056d6')
        self.assertEqual(t['color-premium'], '#1f2a44')
        self.assertEqual(t['color-error'], '#af1d1d')
        self.assertEqual(t['color-notice'], '#c98a16')
        self.assertEqual(t['color-success'], '#0c8930')
        self.assertEqual(t['color-background'], '#f4f7fb')
        self.assertEqual(t['color-surface'], '#ffffff')

    def test_hover_and_pressed_are_darker_than_action(self):
        t = cc.load_tokens()
        base = cc.luminance(cc.to_rgb(t['color-action']))
        self.assertLess(cc.luminance(cc.to_rgb(t['color-action-hover'])), base)
        self.assertLess(cc.luminance(cc.to_rgb(t['color-action-pressed'])),
                        cc.luminance(cc.to_rgb(t['color-action-hover'])))

    def test_hierarchy_pairs_are_distinct(self):
        t = cc.load_tokens()
        for a, b in (('text-secondary', 'text-muted'), ('background', 'background-soft'),
                     ('border', 'border-subtle'), ('premium', 'ai-background')):
            self.assertNotEqual(t['color-' + a], t['color-' + b], (a, b))


if __name__ == '__main__':
    unittest.main()
