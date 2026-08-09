# -*- coding: utf-8 -*-
"""Ilmiy maqolalar moduli — domen qoidalari (DB'siz).

Bu yerda `can_read_pdf`, litsenziya, DOI, slug, ierarxiya va PDF yuklash
validatsiyasi tekshiriladi. Hech qanday Postgres kerak emas — shu sababli bu
testlar lokal muhitda ham har doim ishlaydi (stale Neon kredensiallariga
bog'liq emas).
"""
import io
import unittest

from security_uploads import (UploadTooLarge, UploadValidationError,
                              resolve_private_pdf, sanitize_pdf_filename,
                              save_private_pdf, validate_pdf_upload)
from utils.article_rules import (ACCESS_TYPES, LICENSES, PDF_ALLOWED_ACCESS_TYPES,
                                 ArticleValidationError, can_read_pdf, can_store_pdf,
                                 make_slug, normalize_doi, pdf_block_reason,
                                 validate_article_rights, validate_choice, validate_doi)
from utils.scientific_fields import (FieldValidationError, branch_code, build_tree,
                                     check_no_cycle, descendant_ids, field_slug,
                                     level_from_code, normalize_code, parent_code,
                                     resolve_level)


def open_article(**overrides):
    """PDF ochiq bo'lishi uchun BARCHA shart bajarilgan maqola."""
    article = {
        'pdf_path': '2026/08/abc.pdf',
        'publication_status': 'published',
        'verification_status': 'verified',
        'access_type': 'open_access',
        'license_type': 'cc_by',
        'source_name': 'Journal of Open Science',
    }
    article.update(overrides)
    return article


class CanReadPdfTests(unittest.TestCase):
    """§23 — PDF mavjudligi o'qish huquqi degani EMAS."""

    def test_fully_verified_open_article_is_readable(self):
        self.assertTrue(can_read_pdf(open_article()))
        self.assertIsNone(pdf_block_reason(open_article()))

    def test_missing_pdf_is_not_readable(self):
        self.assertFalse(can_read_pdf(open_article(pdf_path=None)))
        self.assertFalse(can_read_pdf(open_article(pdf_path='')))

    def test_draft_article_is_never_readable(self):
        for status in ('draft', 'archived'):
            self.assertFalse(can_read_pdf(open_article(publication_status=status)), status)

    def test_unverified_article_is_not_readable(self):
        for status in ('draft', 'pending_review', 'rejected'):
            self.assertFalse(can_read_pdf(open_article(verification_status=status)), status)

    def test_copyright_hold_blocks_reading(self):
        article = open_article(verification_status='copyright_hold')
        self.assertFalse(can_read_pdf(article))
        self.assertIn('huquqi', pdf_block_reason(article))

    def test_unknown_license_blocks_reading(self):
        article = open_article(license_type='unknown')
        self.assertFalse(can_read_pdf(article))
        self.assertIn('Litsenziya', pdf_block_reason(article))

    def test_license_outside_the_allowed_set_blocks_reading(self):
        self.assertFalse(can_read_pdf(open_article(license_type='made-up-license')))

    def test_metadata_only_and_restricted_block_reading(self):
        for access in ('metadata_only', 'restricted'):
            self.assertFalse(can_read_pdf(open_article(access_type=access)), access)

    def test_every_allowed_access_type_can_be_readable(self):
        for access in PDF_ALLOWED_ACCESS_TYPES:
            self.assertTrue(can_read_pdf(open_article(access_type=access)), access)

    def test_missing_source_blocks_reading(self):
        for value in (None, '', '   '):
            self.assertFalse(can_read_pdf(open_article(source_name=value)), repr(value))

    def test_works_on_attribute_objects_too(self):
        class Row:
            pass
        row = Row()
        for key, value in open_article().items():
            setattr(row, key, value)
        self.assertTrue(can_read_pdf(row))


class StoragePermissionTests(unittest.TestCase):
    """§9 — metadata_only / restricted uchun serverga fayl yuklanmaydi."""

    def test_only_the_four_access_types_may_store_a_pdf(self):
        for access in ACCESS_TYPES:
            self.assertEqual(can_store_pdf(access), access in PDF_ALLOWED_ACCESS_TYPES, access)

    def test_metadata_only_upload_is_rejected(self):
        with self.assertRaises(ArticleValidationError) as ctx:
            validate_article_rights('metadata_only', 'cc_by', pdf_present=True,
                                    rights_confirmed=True)
        self.assertIn('metadata', ctx.exception.public_message.lower())

    def test_upload_without_rights_confirmation_is_rejected(self):
        with self.assertRaises(ArticleValidationError):
            validate_article_rights('open_access', 'cc_by', pdf_present=True,
                                    rights_confirmed=False)

    def test_metadata_only_without_a_pdf_is_fine(self):
        self.assertTrue(validate_article_rights('metadata_only', 'unknown',
                                                pdf_present=False, rights_confirmed=False))

    def test_invalid_choice_raises_a_public_message(self):
        with self.assertRaises(ArticleValidationError):
            validate_choice('nonsense', LICENSES, 'license_type', 'Litsenziya')


class DoiTests(unittest.TestCase):
    """§27 — DOI ixtiyoriy, normalizatsiya qilinadi, noto'g'ri format 500 bermaydi."""

    def test_prefix_is_stripped_and_lowercased(self):
        for raw in ('https://doi.org/10.1234/ABC.2024',
                    'http://dx.doi.org/10.1234/ABC.2024',
                    'doi:10.1234/ABC.2024',
                    '  10.1234/abc.2024  '):
            self.assertEqual(normalize_doi(raw), '10.1234/abc.2024', raw)

    def test_empty_doi_is_allowed(self):
        for raw in (None, '', '   '):
            self.assertIsNone(validate_doi(raw))

    def test_invalid_doi_raises_validation_error_not_crash(self):
        for raw in ('not-a-doi', '11.1234/abc', '10.12/abc', '10.1234'):
            with self.assertRaises(ArticleValidationError, msg=raw):
                validate_doi(raw)

    def test_valid_doi_survives_validation(self):
        self.assertEqual(validate_doi('10.1016/j.jclepro.2021.126677'),
                         '10.1016/j.jclepro.2021.126677')

    def test_overlong_doi_is_rejected(self):
        with self.assertRaises(ArticleValidationError):
            validate_doi('10.1234/' + 'a' * 300)


class SlugTests(unittest.TestCase):
    def test_cyrillic_title_becomes_ascii_slug(self):
        slug = make_slug('Педагогика назарияси')
        self.assertRegex(slug, r'^[a-z0-9-]+$')

    def test_slug_is_never_only_digits(self):
        self.assertNotEqual(make_slug('2024'), '2024')
        self.assertTrue(make_slug('2024').startswith('maqola'))

    def test_apostrophes_are_dropped_not_turned_into_separators(self):
        self.assertNotIn("'", make_slug("O'zbekiston ta'limi"))

    def test_field_slug_appends_the_code_so_same_names_do_not_collide(self):
        first = field_slug('Umumiy pedagogika', '13.00.01')
        second = field_slug('Umumiy pedagogika', '13.00.02')
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith('130001'))


class HierarchyTests(unittest.TestCase):
    """§4 — daraja koddan aniqlanadi, halqa rad etiladi."""

    def test_level_is_derived_from_the_oak_code(self):
        self.assertEqual(level_from_code('01.00.00'), 1)
        self.assertEqual(level_from_code('05.01.00'), 2)
        self.assertEqual(level_from_code('05.01.04'), 3)
        self.assertIsNone(level_from_code('nonsense'))

    def test_parent_and_branch_codes(self):
        self.assertEqual(parent_code('05.01.04'), '05.01.00')
        self.assertEqual(parent_code('05.01.00'), '05.00.00')
        self.assertIsNone(parent_code('05.00.00'))
        self.assertEqual(branch_code('05.01.04'), '05.00.00')

    def test_code_is_optional(self):
        self.assertIsNone(normalize_code(''))
        self.assertIsNone(normalize_code(None))

    def test_short_codes_are_zero_padded(self):
        self.assertEqual(normalize_code('5.1.4'), '05.01.04')

    def test_level_falls_back_to_parent_plus_one_when_there_is_no_code(self):
        self.assertEqual(resolve_level(None, parent_level=3), 4)
        self.assertEqual(resolve_level(None, parent_level=None), 1)

    def test_a_field_cannot_be_its_own_parent(self):
        with self.assertRaises(FieldValidationError):
            check_no_cycle(5, 5, {})

    def test_a_field_cannot_be_moved_under_its_own_descendant(self):
        # 1 -> 2 -> 3; 1 ni 3 ning ostiga qo'yish halqa yaratadi.
        parents = {1: None, 2: 1, 3: 2}
        with self.assertRaises(FieldValidationError):
            check_no_cycle(1, 3, parents)

    def test_an_existing_cycle_in_the_data_is_detected_not_looped_forever(self):
        parents = {1: 2, 2: 1}
        with self.assertRaises(FieldValidationError):
            check_no_cycle(9, 1, parents)

    def test_a_legal_move_is_accepted(self):
        self.assertTrue(check_no_cycle(3, 1, {1: None, 2: 1, 3: 2}))
        self.assertTrue(check_no_cycle(3, None, {1: None}))

    def test_excessive_depth_is_rejected(self):
        parents = {i: i - 1 for i in range(2, 20)}
        parents[1] = None
        with self.assertRaises(FieldValidationError):
            check_no_cycle(99, 19, parents)

    def test_build_tree_nests_children_and_keeps_orphans_visible(self):
        rows = [
            {'id': 1, 'parent_id': None, 'name_uz': 'Texnika', 'sort_order': 1},
            {'id': 2, 'parent_id': 1, 'name_uz': 'Informatika', 'sort_order': 1},
            {'id': 3, 'parent_id': 2, 'name_uz': 'Dasturiy ta\'minot', 'sort_order': 1},
            {'id': 9, 'parent_id': 404, 'name_uz': 'Yetim', 'sort_order': 1},
        ]
        tree = build_tree(rows)
        self.assertEqual({n['id'] for n in tree}, {1, 9})
        self.assertEqual(tree[0]['children'][0]['children'][0]['id'], 3)

    def test_descendant_ids_includes_self_and_terminates_on_cycles(self):
        children = {1: [2], 2: [3], 3: [1]}
        self.assertEqual(sorted(descendant_ids(1, children)), [1, 2, 3])


def make_pdf(size=1024):
    body = b'%PDF-1.7\n' + b'x' * size + b'\n%%EOF\n'
    return body


class FakeUpload:
    """werkzeug FileStorage'ning testga yetarli minimal o'rnini bosuvchisi."""

    def __init__(self, data, filename='maqola.pdf', mimetype='application/pdf'):
        self.stream = io.BytesIO(data)
        self.filename = filename
        self.mimetype = mimetype
        self.content_length = None


class PdfUploadTests(unittest.TestCase):
    """§11 — extension, MIME, magic bytes, hajm, checksum, nom tozalash."""

    def test_valid_pdf_returns_data_and_checksum(self):
        data, checksum = validate_pdf_upload(FakeUpload(make_pdf()))
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertEqual(len(checksum), 64)

    def test_identical_files_hash_identically(self):
        _, first = validate_pdf_upload(FakeUpload(make_pdf()))
        _, second = validate_pdf_upload(FakeUpload(make_pdf()))
        self.assertEqual(first, second)

    def test_non_pdf_extension_is_rejected(self):
        with self.assertRaises(UploadValidationError):
            validate_pdf_upload(FakeUpload(make_pdf(), filename='maqola.exe'))

    def test_wrong_mimetype_is_rejected(self):
        with self.assertRaises(UploadValidationError):
            validate_pdf_upload(FakeUpload(make_pdf(), mimetype='image/png'))

    def test_a_renamed_non_pdf_is_caught_by_magic_bytes(self):
        with self.assertRaises(UploadValidationError) as ctx:
            validate_pdf_upload(FakeUpload(b'MZ\x90\x00 not a pdf %%EOF'))
        self.assertIn('%PDF', ctx.exception.public_message)

    def test_truncated_pdf_is_rejected(self):
        with self.assertRaises(UploadValidationError):
            validate_pdf_upload(FakeUpload(b'%PDF-1.7\n' + b'x' * 500))

    def test_oversized_upload_is_rejected(self):
        with self.assertRaises(UploadTooLarge):
            validate_pdf_upload(FakeUpload(make_pdf(4096)), max_bytes=1024)

    def test_declared_content_length_shortcuts_the_size_check(self):
        upload = FakeUpload(make_pdf())
        upload.content_length = 99_000_000
        with self.assertRaises(UploadTooLarge):
            validate_pdf_upload(upload, max_bytes=1024)

    def test_empty_upload_is_rejected(self):
        with self.assertRaises(UploadValidationError):
            validate_pdf_upload(FakeUpload(b''))


class PdfFilenameTests(unittest.TestCase):
    def test_directory_traversal_is_stripped_from_the_display_name(self):
        for evil in ('../../etc/passwd.pdf', r'..\..\windows\system.pdf',
                     '/etc/shadow.pdf'):
            safe = sanitize_pdf_filename(evil)
            self.assertNotIn('/', safe)
            self.assertNotIn('\\', safe)
            self.assertNotIn('..', safe)

    def test_result_always_ends_with_pdf(self):
        self.assertTrue(sanitize_pdf_filename('hujjat').endswith('.pdf'))
        self.assertTrue(sanitize_pdf_filename('').endswith('.pdf'))


class PdfStorageTests(unittest.TestCase):
    """Fayl nomi serverda generatsiya qilinadi va traversal to'silgan."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_stored_name_is_not_derived_from_the_upload_name(self):
        relative = save_private_pdf(make_pdf(), self.root)
        self.assertNotIn('maqola', relative)
        self.assertTrue(relative.endswith('.pdf'))

    def test_two_saves_of_the_same_bytes_get_different_names(self):
        first = save_private_pdf(make_pdf(), self.root)
        second = save_private_pdf(make_pdf(), self.root)
        self.assertNotEqual(first, second)

    def test_saved_file_resolves_back(self):
        relative = save_private_pdf(make_pdf(), self.root)
        self.assertIsNotNone(resolve_private_pdf(self.root, relative))

    def test_traversal_paths_never_resolve_outside_the_storage_root(self):
        for evil in ('../../../etc/passwd', '/etc/passwd', '..%2f..%2fetc', ''):
            self.assertIsNone(resolve_private_pdf(self.root, evil), evil)

    def test_missing_file_resolves_to_none(self):
        self.assertIsNone(resolve_private_pdf(self.root, '2026/08/yoq.pdf'))

    def test_no_temp_file_is_left_behind(self):
        import os
        save_private_pdf(make_pdf(), self.root)
        leftovers = [name for _root, _dirs, files in os.walk(self.root)
                     for name in files if name.startswith('.upload_')]
        self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main()
