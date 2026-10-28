import ast
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from institution_backfill import build_plan, plan_csv_rows, CSV_HEADER
from institution_identity import classify_institution_name, normalize_institution_identity as N

UZMU = 'Ўзбекистон Миллий университети'
FA = 'Ўзбекистон Республикаси Фанлар академияси'
YADRO = 'Ўзбекистон Республикаси Фанлар академияси Ядро физикаси институти'
ASTRO = 'Астрономия институти'


def _plan(map_rows, diss=None, **kw):
    return build_plan(map_rows, diss or {}, **kw)


class ClassifyTests(unittest.TestCase):
    def test_huzuridagi_is_one_child_with_parent(self):
        c = classify_institution_name(
            'Ўзбекистон Республикаси Вазирлар Маҳкамаси ҳузуридаги Банк-молия академияси')
        self.assertEqual(c['kind'], 'child')
        self.assertEqual(c['parent'], 'ozbekiston respublikasi vazirlar mahkamasi')

    def test_huzuridagi_beats_va_inside_child_name(self):
        c = classify_institution_name(
            'Тошкент давлат техника университети ҳузуридаги Фан ва технологиялар маркази')
        self.assertEqual(c['kind'], 'child')

    def test_academy_institute_is_child(self):
        c = classify_institution_name(YADRO)
        self.assertEqual((c['kind'], c['parent']), ('child', N(FA)))

    def test_hamda_is_two_organisations(self):
        c = classify_institution_name(UZMU + ' ҳамда ' + ASTRO)
        self.assertEqual(c['parts'], [N(UZMU), N(ASTRO)])

    def test_two_full_names_with_va(self):
        c = classify_institution_name(UZMU + ' ва ' + ASTRO)
        self.assertEqual((c['kind'], c['parts']), ('multiple', [N(UZMU), N(ASTRO)]))

    def test_official_names_with_va_or_hamda_stay_single(self):
        for name in ('Iqtisodiyot va pedagogika universiteti',
                     'Fan hamda texnologiyalar universiteti',
                     'Ўзбекистон Миллий университети Биофизика ва биокимё институти',
                     'Тошкент давлат техника университети «Фан ва тараққиёт» ДУК'):
            self.assertEqual(classify_institution_name(name)['kind'], 'single', name)


class PlanTests(unittest.TestCase):
    def test_legacy_merge_variants_become_aliases_not_queue(self):
        plan = _plan([('Миллий университет', UZMU, 'universitet', 'Toshkent'),
                      ('НУУз', UZMU, 'universitet', 'Toshkent'),
                      (UZMU, None, 'universitet', 'Toshkent')],
                     {'НУУз': 3, UZMU: 5, 'Миллий университет': 2})
        self.assertEqual(plan.queue, [])
        alias = plan.aliases[N('НУУз')]
        self.assertEqual((alias['target'], alias['alias_type']), (N(UZMU), 'legacy_merge'))
        self.assertEqual(plan.stats['diss_linkable'], 10)

    def test_category_majority_and_tie(self):
        plan = _plan([('a', 'X universiteti', 'universitet', None),
                      ('b', 'X universiteti', 'universitet', None),
                      ('c', 'X universiteti', 'institut', None),
                      ('d', 'Y markazi', 'markaz', None),
                      ('e', 'Y markazi', 'institut', None)])
        self.assertEqual(plan.institutions[N('X universiteti')]['category'], 'universitet')
        self.assertEqual(plan.institutions[N('Y markazi')]['category'], 'boshqa')
        self.assertIn(('Y markazi', N('Y markazi'), 'legacy_review', 'category_tie', N('Y markazi')),
                      plan.queue)

    def test_child_gets_parent_and_no_multiple_flag(self):
        plan = _plan([(FA, None, 'akademiya', None), (YADRO, None, 'institut', None)])
        self.assertEqual(plan.institutions[N(YADRO)]['parent'], N(FA))
        self.assertFalse([q for q in plan.queue if q[2] == 'multiple'])

    def test_missing_parent_is_reported_not_created(self):
        plan = _plan([(YADRO, None, 'institut', None)])
        self.assertIsNone(plan.institutions[N(YADRO)]['parent'])
        self.assertNotIn(N(FA), plan.institutions)
        self.assertIn('ota_topilmadi', plan_csv_rows(plan)[0][7])

    def test_joint_defence_links_both(self):
        joint = UZMU + ' ҳамда ' + ASTRO
        plan = _plan([(UZMU, None, 'universitet', None), (ASTRO, None, 'institut', None),
                      (joint, None, 'universitet', None)], {joint: 4})
        self.assertEqual(plan.aliases[N(joint)]['target'], N(UZMU))
        self.assertEqual(plan.co_links[joint], [N(ASTRO)])
        self.assertNotIn(N(joint), plan.institutions)

    def test_unmapped_exact_name_links_and_unknown_is_queued(self):
        plan = _plan([(UZMU, None, 'universitet', None)],
                     {UZMU: 1, 'Ўзбекистон  миллий университети': 2, 'Номаълум марказ': 1})
        self.assertEqual(plan.aliases[N('Ўзбекистон  миллий университети')]['target'], N(UZMU))
        self.assertEqual([q[2] for q in plan.queue], ['unresolved'])

    def test_existing_ids_are_reused(self):
        plan = _plan([(UZMU, None, 'universitet', None)], existing_institutions=[(7, N(UZMU))])
        self.assertEqual(plan_csv_rows(plan)[0][3], '7')
        self.assertEqual(plan.stats['institutions_new'], 0)

    def test_same_key_variant_in_another_group_still_links_its_defences(self):
        twin = 'Мирзо Улуғбек номидаги Ўзбекистон Миллий университети'
        plan = _plan([(twin, None, 'universitet', None),
                      ('Мирзо Улуғбек номидаги Оʻзбекистон Миллий университети', 'НУУз', 'universitet', None),
                      ('НУУз', None, 'universitet', None)],
                     {twin: 5, 'Мирзо Улуғбек номидаги Оʻзбекистон Миллий университети': 3})
        self.assertEqual(plan.links['Мирзо Улуғбек номидаги Оʻзбекистон Миллий университети'], N('НУУз'))
        self.assertEqual(plan.stats['diss_linkable'], 8)
        self.assertEqual([q[2] for q in plan.queue], ['legacy_review'])

    def test_canonical_name_of_one_group_never_steals_another_groups_variant(self):
        # UzMU's admin put raw R in its group; another group is NAMED R.
        r = 'Мирзо Улуғбек номидаги Ўзбекистон Миллий университети'
        plan = _plan([(r, UZMU, 'universitet', None), (UZMU, None, 'universitet', None),
                      ('ЎзМУ (М. Улуғбек)', r, 'universitet', None)],
                     {r: 260, UZMU: 5, 'ЎзМУ (М. Улуғбек)': 10})
        self.assertLess(UZMU, r)  # UzMU group is planned first
        self.assertEqual(plan.links[r], N(UZMU))
        self.assertEqual(plan.links['ЎзМУ (М. Улуғбек)'], N(r))

    def test_short_academy_parent_resolves_to_full_academy(self):
        child = 'ЎзР ФА Математика институти'
        plan = _plan([(FA, None, 'akademiya', None), (child, None, 'institut', None)])
        self.assertEqual(plan.institutions[N(child)]['parent'], N(FA))

    def test_csv_header(self):
        self.assertEqual(CSV_HEADER, ['eski_guruh', 'variantlar', 'dissertatsiyalar', 'taklif_id',
                                      'parent_id', 'kategoriya', 'qoida', 'muammo'])


class DryRunIsReadOnlyTests(unittest.TestCase):
    def _source(self, rel):
        with io.open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
            return fh.read()

    def test_read_inputs_and_plan_only_select(self):
        tree = ast.parse(self._source('institution_backfill.py'))
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and fn.name != 'apply_plan':
                text = ast.get_source_segment(self._source('institution_backfill.py'), fn).upper()
                for verb in ('INSERT ', 'UPDATE ', 'DELETE ', 'ALTER ', 'CREATE ', 'LOCK '):
                    self.assertNotIn(verb, text, f'{fn.name} contains {verb}')

    def test_script_dry_run_is_read_only_session(self):
        src = self._source('scripts/backfill_institution_ids.py')
        self.assertIn('default_transaction_read_only=on', src)
        self.assertNotIn('ensure_identity_schema', src)
        self.assertIn('--expect-db', src)

    def test_migration_has_rollback(self):
        up = self._source('migrations/021_institution_links.sql')
        down = self._source('migrations/rollback/021_institution_links.down.sql')
        self.assertIn('CREATE TABLE IF NOT EXISTS dissertation_institutions', up)
        self.assertNotIn('DROP', up.upper())
        self.assertIn('DROP TABLE IF EXISTS dissertation_institutions', down)

    def test_admin_form_defaults_to_boshqa(self):
        self.assertIn('<option value="boshqa" selected>', self._source('templates/admin_institutions.html'))


class ReytingJointDefenceTests(unittest.TestCase):
    def test_joint_defence_gives_full_score_to_both(self):
        from blueprints.reyting import _aggregate_rating
        imap = {1: {'canonical': 'A', 'category': 'universitet', 'region': ''},
                2: {'canonical': 'B', 'category': 'institut', 'region': ''}}
        data = _aggregate_rating(imap, [('Olim Bir', (1, 2), 'PhD', '01')], {}, {})
        orgs = {o['canonical']: o for o in data['orgs']}
        self.assertEqual(orgs['A']['score'], orgs['B']['score'])
        self.assertEqual((orgs['A']['diss'], orgs['B']['diss']), (1, 1))
        self.assertEqual(data['scholars'][0]['org'], 'A')

    def test_blank_institution_scholar_listed_as_unknown_but_no_org(self):
        from blueprints.reyting import _aggregate_rating
        imap = {1: {'canonical': 'A', 'category': 'universitet', 'region': ''}}
        data = _aggregate_rating(imap, [('Olim Bir', (1,), 'PhD', ''),
                                        ('Olim Ikki', (), 'DSc', '')], {}, {})
        self.assertEqual([o['canonical'] for o in data['orgs']], ['A'])
        unknown = next(s for s in data['scholars'] if s['name'] == 'Olim Ikki')
        self.assertEqual((unknown['org'], unknown['org_latin'], unknown['org_rank']),
                         ('', 'Noma’lum', None))

    def test_coverage_ignores_blank_muassasa(self):
        import inspect
        from blueprints.reyting import _identity_coverage
        self.assertIn("NULLIF(TRIM(d.muassasa), '') IS NOT NULL", inspect.getsource(_identity_coverage))


if __name__ == '__main__':
    unittest.main()
