# -*- coding: utf-8 -*-
"""Generic import race hardening (§17, §43, Phase 2D.1 audit finding).

Ssenariy: item `pending` -> `processing` bo'lib qulflangandan KEYIN, lekin
metadata provayderga so'rov ketishidan OLDIN, boshqa yo'l (masalan boshqa
admin approve'i yoki boshqa import) xuddi shu DOI bilan `scientific_articles`
yozuvini ALLAQACHON yaratgan bo'lishi mumkin (discovery preview bilan
processing orasidagi bo'shliq shu turdagi poyga uchun aynan misol, lekin bu
himoya CSV/DOI-list import uchun ham amal qiladi — processor.py umumiy).

Kutilgan natija: metadata provayderga BEKORGA so'rov YUBORILMAYDI, item
`already_exists`ga o'tadi va mavjud maqolaga bog'lanadi.
"""
import unittest

from services.article_import import persistence, processor
from services.article_import.state import ALREADY_EXISTS
from tests.fake_article_import_pg import ITEM_COLUMNS, FakeImportCursor, new_store


class ProcessorRaceProtectionTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.cur = FakeImportCursor(self.store)
        # Pending item — xuddi mark_item_processing() chaqirilgandan keyingi
        # holat (§ testlarning boshqalari bilan bir xil, `process_item` shu
        # nuqtadan boshlab chaqiriladi).
        item = {c: None for c in ITEM_COLUMNS}
        item.update({
            'id': 1, 'batch_id': 1, 'row_number': 1, 'raw_input': '10.1000/a',
            'normalized_doi': '10.1000/a', 'status': 'processing',
            'is_retracted': False, 'attempt_count': 1,
        })
        self.store['items'].append(item)

    def test_existing_article_short_circuits_before_metadata_fetch(self):
        self.store['articles'].append(
            {'id': 77, 'title': 'Already here', 'slug': 'x', 'doi': '10.1000/a'})
        calls = []

        def fetch_that_must_not_be_called(doi, **kwargs):
            calls.append(doi)
            raise AssertionError('metadata provider chaqirilmasligi kerak edi')

        item = persistence.get_item(self.cur, 1)
        status = processor.process_item(self.cur, item, fetch=fetch_that_must_not_be_called)

        self.assertEqual(status, ALREADY_EXISTS)
        self.assertEqual(calls, [])
        updated = persistence.get_item(self.cur, 1)
        self.assertEqual(updated['status'], ALREADY_EXISTS)
        self.assertEqual(updated['existing_article_id'], 77)

    def test_no_existing_article_falls_through_to_normal_fetch(self):
        calls = []

        def fetch_stub(doi, **kwargs):
            calls.append(doi)
            raise RuntimeError('boom')          # xatolik yo'li ham qamrab olinadi

        item = persistence.get_item(self.cur, 1)
        processor.process_item(self.cur, item, fetch=fetch_stub)
        self.assertEqual(calls, ['10.1000/a'])


if __name__ == '__main__':
    unittest.main()
