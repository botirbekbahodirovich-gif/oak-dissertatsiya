# -*- coding: utf-8 -*-
"""DOI ro'yxati / CSV parsing va normalizatsiya testlari (§64)."""
import unittest

from services.article_import.deduplicator import (find_existing_db_duplicates,
                                                   find_in_batch_duplicates)
from services.article_import.parser import ImportParseError, parse_csv, parse_doi_list
from services.article_import.validator import (ImportLimitError, enforce_max_items,
                                                normalize_rows)


class DoiListParsingTests(unittest.TestCase):
    def test_plain_doi(self):
        parsed = parse_doi_list('10.1000/abc')
        self.assertEqual(parsed.rows, [(1, '10.1000/abc')])

    def test_doi_prefix_and_url_forms_pass_through_as_raw(self):
        text = 'doi:10.1000/a\nhttps://doi.org/10.1000/b\nDOI:10.1000/C'
        parsed = parse_doi_list(text)
        self.assertEqual([r for _, r in parsed.rows],
                         ['doi:10.1000/a', 'https://doi.org/10.1000/b', 'DOI:10.1000/C'])

    def test_blank_lines_are_skipped(self):
        parsed = parse_doi_list('10.1000/a\n\n\n10.1000/b\n   \n')
        self.assertEqual(len(parsed.rows), 2)
        self.assertEqual(parsed.blank_lines_skipped, 4)

    def test_whitespace_trimmed(self):
        parsed = parse_doi_list('  10.1000/a  \n')
        self.assertEqual(parsed.rows[0][1], '10.1000/a')

    def test_row_numbers_are_sequential_ignoring_blanks(self):
        parsed = parse_doi_list('10.1000/a\n\n10.1000/b')
        self.assertEqual([n for n, _ in parsed.rows], [1, 2])

    def test_too_many_rows_raises(self):
        text = '\n'.join('10.1000/%d' % i for i in range(10))
        with self.assertRaises(ImportParseError):
            parse_doi_list(text, max_rows=5)

    def test_empty_text_yields_no_rows(self):
        parsed = parse_doi_list('')
        self.assertEqual(parsed.rows, [])


class CsvParsingTests(unittest.TestCase):
    def test_basic_comma_csv(self):
        raw = b'doi,title\n10.1000/a,Foo\n10.1000/b,Bar\n'
        parsed = parse_csv(raw)
        self.assertEqual([r for _, r in parsed.rows], ['10.1000/a', '10.1000/b'])

    def test_semicolon_csv(self):
        raw = b'doi;title\n10.1000/a;Foo\n10.1000/b;Bar\n'
        parsed = parse_csv(raw)
        self.assertEqual([r for _, r in parsed.rows], ['10.1000/a', '10.1000/b'])

    def test_utf8_bom_csv(self):
        raw = b'\xef\xbb\xbf' + 'doi\n10.1000/a\n'.encode('utf-8')
        parsed = parse_csv(raw)
        self.assertEqual(parsed.rows, [(1, '10.1000/a')])

    def test_uppercase_and_mixed_case_doi_column(self):
        for header in ('DOI', 'Doi', 'doi'):
            raw = ('%s\n10.1000/a\n' % header).encode('utf-8')
            parsed = parse_csv(raw)
            self.assertEqual(parsed.rows, [(1, '10.1000/a')])

    def test_missing_doi_column_raises(self):
        raw = b'title\nFoo\n'
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(raw)
        self.assertEqual(ctx.exception.code, 'missing_doi_column')

    def test_duplicate_header_raises(self):
        raw = b'doi,doi\n10.1000/a,10.1000/b\n'
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(raw)
        self.assertEqual(ctx.exception.code, 'duplicate_headers')

    def test_empty_file_raises(self):
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(b'')
        self.assertEqual(ctx.exception.code, 'empty_file')

    def test_header_only_csv_raises_empty(self):
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(b'doi\n')
        self.assertEqual(ctx.exception.code, 'empty_file')

    def test_blank_doi_values_are_skipped(self):
        raw = b'doi\n10.1000/a\n\n10.1000/b\n'
        parsed = parse_csv(raw)
        self.assertEqual(len(parsed.rows), 2)

    def test_invalid_encoding_raises(self):
        raw = b'doi\n\xff\xfe10.1000/a\n'
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(raw)
        self.assertEqual(ctx.exception.code, 'invalid_encoding')

    def test_too_many_rows_raises(self):
        rows = '\n'.join('10.1000/%d' % i for i in range(10))
        raw = ('doi\n' + rows + '\n').encode('utf-8')
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(raw, max_rows=5)
        self.assertEqual(ctx.exception.code, 'too_many_rows')

    def test_oversized_field_in_header_raises_safe_error_not_csv_error(self):
        """Hardening audit (Phase 2C.2.1), §7 — bitta maydon
        `csv.field_size_limit()`dan (131072) katta bo'lsa, xom `csv.Error`
        500 sifatida CHIQMASLIGI kerak."""
        huge = 'x' * 200_000
        raw = (huge + '\ndoi\n10.1000/a\n').encode('utf-8')
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(raw)
        self.assertEqual(ctx.exception.code, 'malformed_csv')

    def test_oversized_field_in_data_row_raises_safe_error(self):
        huge = 'x' * 200_000
        raw = ('doi\n' + huge + '\n10.1000/a\n').encode('utf-8')
        with self.assertRaises(ImportParseError) as ctx:
            parse_csv(raw)
        self.assertEqual(ctx.exception.code, 'malformed_csv')


class NormalizeRowsTests(unittest.TestCase):
    def test_valid_doi_normalized(self):
        rows = normalize_rows([(1, 'https://doi.org/10.1000/ABC')])
        self.assertTrue(rows[0].is_valid)
        self.assertEqual(rows[0].normalized_doi, '10.1000/abc')

    def test_invalid_doi_marked(self):
        rows = normalize_rows([(1, 'not-a-doi')])
        self.assertFalse(rows[0].is_valid)
        self.assertIsNone(rows[0].normalized_doi)
        self.assertIsNotNone(rows[0].error_message)


class MaxItemsTests(unittest.TestCase):
    def test_enforce_max_items_raises_over_limit(self):
        with self.assertRaises(ImportLimitError):
            enforce_max_items(501, 500)

    def test_enforce_max_items_raises_on_empty(self):
        with self.assertRaises(ImportParseError):
            enforce_max_items(0, 500)

    def test_enforce_max_items_passes_within_limit(self):
        enforce_max_items(500, 500)   # ko'tarilmasligi kerak


class DeduplicatorTests(unittest.TestCase):
    def test_in_batch_duplicate_detected(self):
        rows = normalize_rows([(1, '10.1000/a'), (2, '10.1000/b'), (3, '10.1000/a')])
        dups = find_in_batch_duplicates(rows)
        self.assertEqual(dups, {3: 1})

    def test_invalid_rows_are_not_considered_duplicates(self):
        rows = normalize_rows([(1, 'bad-doi'), (2, 'bad-doi')])
        dups = find_in_batch_duplicates(rows)
        self.assertEqual(dups, {})

    def test_existing_db_duplicates_single_query(self):
        class Cur:
            def __init__(self):
                self.calls = 0

            def execute(self, sql, params):
                self.calls += 1
                self.result = [(1, 'Title', 'slug-1', '10.1000/a')]

            def fetchall(self):
                return self.result

        cur = Cur()
        out = find_existing_db_duplicates(cur, ['10.1000/a', '10.1000/a', '10.1000/b'])
        self.assertEqual(cur.calls, 1)
        self.assertEqual(out, {'10.1000/a': {'id': 1, 'title': 'Title', 'slug': 'slug-1'}})


if __name__ == '__main__':
    unittest.main()
