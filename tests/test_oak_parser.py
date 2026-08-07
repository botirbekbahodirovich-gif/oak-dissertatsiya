# -*- coding: utf-8 -*-
"""scripts/parse_oak_specialties.py — parsing, normalizatsiya va validatsiya.

Testlar ikki qatlamda: sun'iy kichik inputlar ustida qoidalar, va HAQIQIY
manba fayli (data/oak/source/oak_barcha_shifrlar.md) ustida yakuniy sonlar.
Ikkinchisi muhim — manba fayl o'zgarsa (yangi revizya) testlar buni darhol
ko'rsatadi va jimgina boshqa dataset importga tushib ketmaydi.
"""
import hashlib
import io
import json
import os
import tempfile
import unittest

from scripts.parse_oak_specialties import (CURATED_SUSPECTS, SOURCE_SHA_FOR_CURATED,
                                           apply_reviews, build_report,
                                           declared_total, load_existing_reviews,
                                           normalize_name, parse_records,
                                           quote_balance_issue, read_source,
                                           write_outputs)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(_REPO_ROOT, 'data', 'oak', 'source', 'oak_barcha_shifrlar.md')
OAK_DIR = os.path.join(_REPO_ROOT, 'data', 'oak')

DASH = '–'


def make_source(*lines):
    body = '\n\n'.join(lines)
    return ('OAK.UZ - IXTISOLLIK SHIFRLARI VA NOMLARI\n\n'
            'Jami: %d ta\n\n' % len(lines) +
            '=' * 60 + '\n\n' + body + '\n')


def parse(*lines, sha='deadbeef'):
    text = make_source(*lines)
    records, unparsed = parse_records(text, 'test.md', sha)
    return records, unparsed, text


def entry(code, name):
    return '%s  %s  %s' % (code, DASH, name)


class NormalizationRuleTests(unittest.TestCase):
    """§6 — faqat texnik tozalash, mazmun hech qachon o'zgarmaydi."""

    def test_outer_curly_quotes_are_removed(self):
        name, notes = normalize_name('“Konteyner fizikasi”')
        self.assertEqual(name, 'Konteyner fizikasi')
        self.assertIn('outer_quotes_removed', notes)

    def test_outer_angle_quotes_are_removed(self):
        name, notes = normalize_name('«Seysmik xavfsizlik»')
        self.assertEqual(name, 'Seysmik xavfsizlik')
        self.assertIn('outer_quotes_removed', notes)

    def test_unbalanced_quote_is_left_alone(self):
        # Yopilmagan qo'shtirnoq — kesilgan nom belgisi; u tozalanmaydi,
        # yozuv review'ga chiqadi.
        name, notes = normalize_name('“Tizimli tahlil')
        self.assertEqual(name, '“Tizimli tahlil')
        self.assertNotIn('outer_quotes_removed', notes)

    def test_inner_quotes_are_preserved(self):
        name, _notes = normalize_name('Fizika “A” va “B”')
        self.assertIn('“A”', name)

    def test_markdown_bold_is_removed(self):
        name, notes = normalize_name('**Algebra**')
        self.assertEqual(name, 'Algebra')
        self.assertIn('markdown_bold_removed', notes)

    def test_nbsp_becomes_a_normal_space(self):
        name, notes = normalize_name('Seleksiya va urug\'chilik')
        self.assertEqual(name, "Seleksiya va urug'chilik")
        self.assertIn('nbsp_replaced', notes)

    def test_repeated_spaces_collapse(self):
        name, notes = normalize_name('Optika   fizikasi')
        self.assertEqual(name, 'Optika fizikasi')
        self.assertIn('inner_whitespace_collapsed', notes)

    def test_dashes_are_unified(self):
        name, notes = normalize_name('Yurak–qon tomir')
        self.assertEqual(name, 'Yurak-qon tomir')
        self.assertIn('dash_unified', notes)

    def test_apostrophes_are_unified(self):
        name, notes = normalize_name('O‘zbek tili')
        self.assertEqual(name, "O'zbek tili")
        self.assertIn('apostrophe_unified', notes)

    def test_trailing_period_is_removed(self):
        name, notes = normalize_name('Ozbek tili.')
        self.assertEqual(name, 'Ozbek tili')
        self.assertIn('trailing_period_removed', notes)

    def test_a_clean_name_gets_no_notes(self):
        name, notes = normalize_name('Matematik analiz')
        self.assertEqual(name, 'Matematik analiz')
        self.assertEqual(notes, [])

    def test_quote_balance_detection(self):
        self.assertFalse(quote_balance_issue('“A”'))
        self.assertTrue(quote_balance_issue('“A'))
        self.assertTrue(quote_balance_issue('«A'))
        self.assertTrue(quote_balance_issue('"A'))


class ParsingTests(unittest.TestCase):
    def test_header_and_separator_lines_are_not_records(self):
        records, unparsed, _ = parse(entry('01.01.01', 'Algebra'))
        self.assertEqual(len(records), 1)
        self.assertEqual(unparsed, [])

    def test_blank_lines_are_ignored(self):
        text = make_source(entry('01.01.01', 'Algebra')) + '\n\n\n   \n'
        records, unparsed = parse_records(text, 'test.md', 'x')
        self.assertEqual(len(records), 1)
        self.assertEqual(unparsed, [])

    def test_code_is_extracted(self):
        records, _u, _t = parse(entry('05.01.05', 'Kiberxavfsizlik'))
        self.assertEqual(records[0]['code'], '05.01.05')

    def test_raw_name_is_preserved_exactly(self):
        raw = '“Nanokimyo'
        records, _u, _t = parse(entry('02.00.12', raw))
        self.assertEqual(records[0]['raw_name'], raw)

    def test_source_line_is_recorded(self):
        records, _u, _t = parse(entry('01.01.01', 'Algebra'),
                                entry('01.01.02', 'Geometriya'))
        # make_source: 5 qator sarlavha/separator + bo'sh qator, keyin
        # yozuvlar orasida bittadan bo'sh qator.
        self.assertEqual([r['source_line'] for r in records], [7, 9])

    def test_a_line_without_a_code_is_reported_as_unparsed(self):
        text = make_source(entry('01.01.01', 'Algebra')) + '\nbegona qator\n'
        _records, unparsed = parse_records(text, 'test.md', 'x')
        self.assertEqual(len(unparsed), 1)
        self.assertIn('begona', unparsed[0]['text'])

    def test_declared_total_is_read_from_the_header(self):
        self.assertEqual(declared_total('Jami: 289 ta'), 289)
        self.assertIsNone(declared_total('boshqa matn'))

    def test_records_are_sorted_by_code(self):
        records, _u, _t = parse(entry('05.01.05', 'B'), entry('01.01.01', 'A'))
        self.assertEqual([r['code'] for r in records], ['01.01.01', '05.01.05'])

    def test_output_is_deterministic(self):
        first, _u, _t = parse(entry('01.01.01', 'Algebra'))
        second, _u2, _t2 = parse(entry('01.01.01', 'Algebra'))
        self.assertEqual(first, second)


class StatusAssignmentTests(unittest.TestCase):
    def status(self, name, code='01.01.01', sha='deadbeef'):
        records, _u, _t = parse(entry(code, name), sha=sha)
        return records[0]

    def test_a_clean_name_is_clean(self):
        self.assertEqual(self.status('Математика')['status'], 'clean')

    def test_a_technically_cleaned_name_is_normalized(self):
        record = self.status('“Математика”')
        self.assertEqual(record['status'], 'normalized')
        self.assertIn('outer_quotes_removed', record['normalization_notes'])

    def test_unbalanced_quotes_go_to_review(self):
        record = self.status('“Тери')
        self.assertEqual(record['status'], 'review_required')
        self.assertIn('unbalanced_quotes_possible_truncation', record['review_reasons'])

    def test_foreign_text_goes_to_review(self):
        record = self.status(
            'Ижтимоий фалсафа. '
            'Диссертация мавзуси '
            'рўйхатга олинган '
            'рақам:')
        self.assertEqual(record['status'], 'review_required')
        self.assertIn('foreign_text_in_name', record['review_reasons'])
        self.assertIn('name_ends_with_colon', record['review_reasons'])

    def test_specialty_context_suffix_goes_to_review(self):
        record = self.status(
            'Лазер физикаси '
            'ихтисослиги '
            'бўйича')
        self.assertEqual(record['status'], 'review_required')
        self.assertIn('contains_specialty_context_suffix', record['review_reasons'])

    def test_markdown_markup_goes_to_review(self):
        record = self.status('**Алгебра**')
        self.assertIn('markdown_markup_in_source', record['review_reasons'])

    def test_a_branch_level_code_in_a_specialty_list_goes_to_review(self):
        # 10.00.00 — fan tarmog'i shifri. Uni jimgina import qilish MAVJUD
        # tarmoq nomini ("Filologiya fanlari") almashtirib yuborardi.
        record = self.status('Матншунослик',
                             code='10.00.00')
        self.assertEqual(record['status'], 'review_required')
        self.assertIn('code_level_conflicts_with_specialty_list', record['review_reasons'])

    def test_duplicate_names_across_codes_go_to_review(self):
        records, _u, _t = parse(entry('03.00.06', 'Зоология'),
                                entry('06.00.03', 'Зоология'))
        for record in records:
            self.assertEqual(record['status'], 'review_required')
            self.assertTrue(any(r.startswith('duplicate_name_with_')
                                for r in record['review_reasons']))

    def test_duplicate_codes_go_to_review(self):
        records, _u, _t = parse(entry('03.00.06', 'A'), entry('03.00.06', 'B'))
        for record in records:
            self.assertIn('duplicate_code', record['review_reasons'])

    def test_an_empty_name_is_rejected(self):
        records, _u, _t = parse(entry('01.01.01', ''))
        self.assertEqual(records[0]['status'], 'rejected')
        self.assertIn('empty_name', records[0]['review_reasons'])

    def test_curated_suspects_apply_only_to_the_pinned_source_revision(self):
        code = sorted(CURATED_SUSPECTS)[0]
        pinned = self.status('Машиналар', code=code,
                             sha=SOURCE_SHA_FOR_CURATED)
        self.assertIn('manually_flagged_possible_truncation', pinned['review_reasons'])
        other = self.status('Машиналар', code=code,
                            sha='boshqa-fayl')
        self.assertNotIn('manually_flagged_possible_truncation', other['review_reasons'])

    def test_the_parser_never_invents_a_name(self):
        # Kesilgan nom AYNAN o'sha holda qoladi — to'ldirilmaydi.
        record = self.status('“Тери')
        self.assertEqual(record['normalized_candidate'], '“Тери')
        self.assertEqual(record['raw_name'], '“Тери')


class HierarchyTests(unittest.TestCase):
    def test_level_and_parent_are_derived_from_the_code(self):
        records, _u, _t = parse(entry('05.01.05', 'A'))
        self.assertEqual(records[0]['level'], 3)
        self.assertEqual(records[0]['parent_code'], '05.01.00')

    def test_a_branch_code_has_no_parent(self):
        records, _u, _t = parse(entry('05.00.00', 'A'))
        self.assertEqual(records[0]['level'], 1)
        self.assertIsNone(records[0]['parent_code'])

    def test_the_parser_creates_no_group_records(self):
        # Guruh (05.01.00) manbada yo'q — parser uni O'YLAB TOPMAYDI.
        records, _u, _t = parse(entry('05.01.05', 'A'), entry('05.02.03', 'B'))
        self.assertEqual({r['code'] for r in records}, {'05.01.05', '05.02.03'})


class ReviewCarryForwardTests(unittest.TestCase):
    """§13 — qo'lda tasdiqlangan yozuv re-parse'da yo'qolmaydi."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'review.json')

    def tearDown(self):
        self.tmp.cleanup()

    def write_review(self, **overrides):
        record = {'code': '02.00.07', 'source_sha256': 'deadbeef',
                  'approved_name_uz_cyrillic': 'Композицион материаллар',
                  'reviewer': 'botirbek', 'reviewed_at': '2026-08-07'}
        record.update(overrides)
        with io.open(self.path, 'w', encoding='utf-8') as fh:
            json.dump({'records': [record]}, fh, ensure_ascii=False)

    def test_a_fully_filled_review_is_loaded(self):
        self.write_review()
        reviews = load_existing_reviews(self.path)
        self.assertIn('02.00.07', reviews)

    def test_a_partially_filled_review_is_ignored(self):
        for missing in ('approved_name_uz_cyrillic', 'reviewer', 'reviewed_at'):
            self.write_review(**{missing: ''})
            self.assertEqual(load_existing_reviews(self.path), {}, missing)

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(load_existing_reviews('/yoq/bunday/fayl.json'), {})

    def test_an_approved_review_flips_the_record_to_manual_approved(self):
        self.write_review()
        records, _u, _t = parse(entry('02.00.07', 'Композицион'))
        records[0]['review_reasons'] = ['manually_flagged_possible_truncation']
        records[0]['status'] = 'review_required'
        applied, stale = apply_reviews(records, load_existing_reviews(self.path),
                                       'deadbeef')
        self.assertEqual(applied, ['02.00.07'])
        self.assertEqual(stale, [])
        self.assertEqual(records[0]['status'], 'manual_approved')
        self.assertEqual(records[0]['name_uz_cyrillic'],
                         'Композицион материаллар')
        self.assertTrue(records[0]['name_uz'].startswith('Kompozision'))

    def test_a_review_for_a_different_source_revision_is_not_applied(self):
        self.write_review(source_sha256='boshqa-revizya')
        records, _u, _t = parse(entry('02.00.07', 'Композицион'))
        records[0]['status'] = 'review_required'
        applied, stale = apply_reviews(records, load_existing_reviews(self.path),
                                       'deadbeef')
        self.assertEqual(applied, [])
        self.assertEqual(stale, ['02.00.07'])
        self.assertEqual(records[0]['status'], 'review_required')


class SourceHashTests(unittest.TestCase):
    def test_hash_matches_a_plain_sha256_of_the_bytes(self):
        text, sha = read_source(SOURCE)
        with io.open(SOURCE, 'rb') as fh:
            self.assertEqual(sha, hashlib.sha256(fh.read()).hexdigest())
        self.assertTrue(text)

    def test_the_curated_list_is_pinned_to_the_committed_source(self):
        _text, sha = read_source(SOURCE)
        self.assertEqual(sha, SOURCE_SHA_FOR_CURATED,
                         'manba fayl o\'zgargan — qo\'lda tekshirilgan ro\'yxat '
                         'qayta ko\'rib chiqilishi kerak')

    def test_git_is_told_not_to_normalise_the_source_bytes(self):
        """.gitattributes manba faylni bayt-ba-bayt saqlashi SHART.

        Repo `* text=auto eol=lf` bilan normalizatsiya qiladi. Manba fayl
        CRLF bilan, shuning uchun normalizatsiya klondan keyin uning
        SHA-256 ini o'zgartirardi va butun provenance zanjiri (dataset,
        tasdiqlangan review yozuvlari, bu testlar) buzilardi.
        """
        path = os.path.join(_REPO_ROOT, '.gitattributes')
        with io.open(path, encoding='utf-8') as fh:
            rules = fh.read()
        self.assertIn('data/oak/source/** -text', rules)

    def test_the_committed_source_still_uses_crlf(self):
        with io.open(SOURCE, 'rb') as fh:
            self.assertIn(b'\r\n', fh.read(4096))


class RealSourceTests(unittest.TestCase):
    """Haqiqiy manba fayli ustidagi yakuniy sonlar."""

    @classmethod
    def setUpClass(cls):
        cls.text, cls.sha = read_source(SOURCE)
        cls.records, cls.unparsed = parse_records(cls.text, 'oak_barcha_shifrlar.md',
                                                  cls.sha)

    def test_the_declared_and_parsed_totals_match(self):
        self.assertEqual(declared_total(self.text), 289)
        self.assertEqual(len(self.records), 289)

    def test_nothing_is_left_unparsed(self):
        self.assertEqual(self.unparsed, [])

    def test_no_duplicate_codes(self):
        codes = [r['code'] for r in self.records]
        self.assertEqual(len(codes), len(set(codes)))

    def test_status_breakdown_is_stable(self):
        counts = {}
        for record in self.records:
            counts[record['status']] = counts.get(record['status'], 0) + 1
        self.assertEqual(counts, {'clean': 216, 'normalized': 39,
                                  'review_required': 34})

    def test_every_record_has_a_latin_name(self):
        for record in self.records:
            if record['status'] != 'rejected':
                self.assertTrue(record['name_uz'].strip(), record['code'])

    def test_every_record_keeps_its_raw_name(self):
        for record in self.records:
            self.assertIsNotNone(record['raw_name'])

    def test_the_corrupted_branch_code_is_not_auto_imported(self):
        record = next(r for r in self.records if r['code'] == '10.00.00')
        self.assertEqual(record['status'], 'review_required')

    def test_known_truncated_entries_are_all_in_review(self):
        for code in ('02.00.12', '05.01.02', '05.01.04', '05.06.03', '05.04.02',
                     '22.00.02', '19.00.03'):
            record = next(r for r in self.records if r['code'] == code)
            self.assertEqual(record['status'], 'review_required', code)

    def test_curated_suspects_are_all_in_review(self):
        for code in CURATED_SUSPECTS:
            record = next(r for r in self.records if r['code'] == code)
            self.assertEqual(record['status'], 'review_required', code)


class GeneratedFileTests(unittest.TestCase):
    """Repozitoriydagi chiqarilgan fayllar manbaga mos bo'lishi kerak."""

    @classmethod
    def setUpClass(cls):
        def read(*parts):
            with io.open(os.path.join(OAK_DIR, *parts), encoding='utf-8') as fh:
                return json.load(fh)
        cls.raw = read('raw', 'oak_specialties_raw.json')
        cls.approved = read('normalized', 'oak_specialties_approved.json')
        cls.review = read('review', 'oak_specialties_review_required.json')
        cls.report = read('reports', 'oak_specialties_validation.json')

    def test_raw_holds_every_source_record(self):
        self.assertEqual(len(self.raw['records']), 289)

    def test_approved_excludes_review_and_rejected(self):
        self.assertEqual(len(self.approved['records']), 255)
        self.assertEqual(len(self.review['records']), 34)
        self.assertEqual(len(self.approved['records']) + len(self.review['records']),
                         289)

    def test_approved_records_carry_provenance(self):
        for record in self.approved['records']:
            self.assertIn(record['review_status'],
                          ('auto_clean', 'auto_normalized', 'manual_approved'))
            self.assertTrue(record['source_line'])
            self.assertEqual(record['source_sha256'], SOURCE_SHA_FOR_CURATED)
            self.assertTrue(record['name_uz_cyrillic'])
            self.assertTrue(record['name_uz'])

    def test_approved_never_contains_a_review_required_record(self):
        approved_codes = {r['code'] for r in self.approved['records']}
        review_codes = {r['code'] for r in self.review['records']}
        self.assertEqual(approved_codes & review_codes, set())

    def test_review_records_expose_everything_a_reviewer_needs(self):
        for record in self.review['records']:
            for key in ('code', 'raw_name', 'normalized_candidate', 'source_line',
                        'review_reasons', 'approved_name_uz_cyrillic', 'reviewer',
                        'reviewed_at'):
                self.assertIn(key, record, record['code'])

    def test_the_report_matches_the_generated_files(self):
        self.assertEqual(self.report['source_total'], 289)
        self.assertEqual(self.report['parsed_total'], 289)
        self.assertEqual(self.report['review_required_total'],
                         len(self.review['records']))
        self.assertEqual(self.report['duplicate_code_count'], 0)
        self.assertEqual(self.report['unparsed_lines'], [])
        self.assertEqual(self.report['source_sha256'], SOURCE_SHA_FOR_CURATED)

    def test_the_report_lists_the_missing_group_codes(self):
        self.assertIn('05.01.00', self.report['missing_parent_codes'])
        self.assertIn('01.01.00', self.report['missing_parent_codes'])
        self.assertGreater(self.report['missing_parent_count'], 0)

    def test_regenerating_produces_the_same_files(self):
        text, sha = read_source(SOURCE)
        records, unparsed = parse_records(text, 'oak_barcha_shifrlar.md', sha)
        report = build_report(records, unparsed, text, 'oak_barcha_shifrlar.md',
                              SOURCE, sha, self.report['parsed_at'], set())
        with tempfile.TemporaryDirectory() as tmp:
            write_outputs(tmp, records, report,
                          self.approved['metadata'])
            with io.open(os.path.join(tmp, 'normalized',
                                      'oak_specialties_approved.json'),
                         encoding='utf-8') as fh:
                regenerated = json.load(fh)
        self.assertEqual([r['code'] for r in regenerated['records']],
                         [r['code'] for r in self.approved['records']])


if __name__ == '__main__':
    unittest.main()
