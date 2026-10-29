import re
import unittest
from pathlib import Path
from unittest import mock

from utils import olim_photos
from utils.name_norm import loose_key, strict_key, translit

ROOT = Path(__file__).resolve().parents[1]


class NameNormTests(unittest.TestCase):
    def test_cyrillic_and_latin_spellings_share_one_key(self):
        self.assertEqual(strict_key('Хаитов Хамза Аҳмадович'), 'xaitov xamza ahmadovich')
        self.assertEqual(strict_key('  XAITOV   Xamza  Ahmadovich '), 'xaitov xamza ahmadovich')
        self.assertEqual(strict_key('Abatov_Ruslan_Kuatbayevich'), 'abatov ruslan kuatbayevich')

    def test_uzbek_letters(self):
        self.assertEqual(translit('ўғқҳшчёюяц'), "o'g'qhshchyoyuyats")

    def test_apostrophe_variants_are_one(self):
        keys = {strict_key(f"Mansurov Azizjon Farxod o{a}g{a}li") for a in "'`ʻʼ‘’"}
        self.assertEqual(keys, {'mansurov azizjon farxod ogli'})
        self.assertEqual(strict_key('Мансуров Азизжон Фарход ўғли'), 'mansurov azizjon farxod ogli')

    def test_initial_ye(self):
        self.assertEqual(strict_key('Елизаров Максим'), 'yelizarov maksim')
        self.assertEqual(strict_key('Джумаева Заррина'), 'djumayeva zarrina')

    def test_loose_key_folds_patronymic_and_variants(self):
        self.assertEqual(loose_key('Abdusattarova Gavxar Shuxrat qizi'),
                         loose_key('Абдусаттарова Гавхар Шухрат кизи'))
        self.assertEqual(loose_key('Madaminov Axmad Yuldashevich'),
                         loose_key('Мадаминов Аҳмад Юлдашевич'))
        self.assertNotEqual(loose_key('Saidbekova Saidaxon Alisherovna'),
                            loose_key('Саидбекова Саида Алишеровна'))

    def test_empty(self):
        self.assertEqual(strict_key(None), '')
        self.assertEqual(strict_key(' - '), '')


class PhotoLookupTests(unittest.TestCase):
    def setUp(self):
        olim_photos.clear_cache()
        patcher = mock.patch.object(olim_photos, '_load_map',
                                    return_value={'xaitov xamza ahmadovich': 'abc123def456.webp'})
        self.load = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(olim_photos.clear_cache)

    def test_matched_photo_by_any_spelling(self):
        self.assertEqual(olim_photos.matched_photo_url('Хаитов Хамза Аҳмадович'),
                         '/static/olim_photos/abc123def456.webp')
        self.assertIsNone(olim_photos.matched_photo_url('Boshqa Olim'))

    def test_own_upload_wins_then_archive_then_none(self):
        own = '/static/uploads/avatars/me.png'
        self.assertEqual(olim_photos.scholar_photo_url('Хаитов Хамза Аҳмадович', own), own)
        self.assertEqual(olim_photos.scholar_photo_url('Хаитов Хамза Аҳмадович', ''),
                         '/static/olim_photos/abc123def456.webp')
        self.assertIsNone(olim_photos.scholar_photo_url('Boshqa Olim', None))

    def test_dead_supabase_profile_photo_is_ignored(self):
        dead = 'https://x.supabase.co/storage/v1/object/public/avatars/A.jpg'
        self.assertEqual(olim_photos.scholar_photo_url('Хаитов Хамза Аҳмадович', dead),
                         '/static/olim_photos/abc123def456.webp')

    def test_map_is_cached(self):
        olim_photos.matched_photo_url('a b')
        olim_photos.matched_photo_url('c d')
        self.assertEqual(self.load.call_count, 1)

    def test_db_error_falls_back_to_placeholder(self):
        olim_photos.clear_cache()
        self.load.side_effect = RuntimeError('db down')
        self.assertIsNone(olim_photos.scholar_photo_url('Хаитов Хамза Аҳмадович'))


class DeadPhotoColumnsTests(unittest.TestCase):
    """dissertations.photo_url / ilmiy_rahbar_photo_url hold dead Supabase links: never read them."""

    READERS = ['app.py', 'data.py', 'blueprints/content.py', 'blueprints/olimlar_catalog.py',
               'templates/olim_profile.html', 'templates/dissertation.html']

    def test_no_reads_of_dead_columns(self):
        pat = re.compile(r'MAX\((d\.)?ilmiy_rahbar_photo_url\)|MAX\(daraja\), MAX\(photo_url\)|COALESCE\(photo_url|'
                         r'ixtisoslik, photo_url|dissertations\[0\]\.photo_url|row\.get\(\'photo_url\'\)')
        for rel in self.READERS:
            text = (ROOT / rel).read_text(encoding='utf-8')
            self.assertIsNone(pat.search(text), rel)

    def test_scraper_writes_null_photo_urls(self):
        text = (ROOT / 'scripts/daily_scraper.py').read_text(encoding='utf-8')
        self.assertNotIn('supabase.co', text)
        self.assertIn('"photo_url":            None', text)

    def test_avatar_filter_has_no_supabase(self):
        self.assertNotIn('supabase.co', (ROOT / 'app.py').read_text(encoding='utf-8'))

    def test_migration_and_rollback_exist(self):
        up = (ROOT / 'migrations/022_olim_photos.sql').read_text(encoding='utf-8')
        down = (ROOT / 'migrations/rollback/022_olim_photos.down.sql').read_text(encoding='utf-8')
        self.assertIn('name_norm TEXT PRIMARY KEY', up)
        self.assertIn('DROP TABLE IF EXISTS olim_photos', down)


if __name__ == '__main__':
    unittest.main()
