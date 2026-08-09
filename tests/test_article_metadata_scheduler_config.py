# -*- coding: utf-8 -*-
"""`services/article_sync/scheduler_config.py` — env validatsiyasi
(Phase 2C.3C, §28, §33)."""
import unittest

from services.article_sync.scheduler_config import (DEFAULT_BATCH_SIZE,
                                                     DEFAULT_INTERVAL_DAYS,
                                                     SchedulerConfigError,
                                                     scheduler_config_from_env)


class DefaultsTests(unittest.TestCase):
    def test_empty_env_uses_conservative_defaults(self):
        config = scheduler_config_from_env({})
        self.assertTrue(config.enabled)
        self.assertEqual(config.interval_days, DEFAULT_INTERVAL_DAYS)
        self.assertEqual(config.batch_size, DEFAULT_BATCH_SIZE)


class EnabledParsingTests(unittest.TestCase):
    def test_true_variants(self):
        for raw in ('true', 'True', '1', 'yes', 'on'):
            config = scheduler_config_from_env({'ARTICLE_METADATA_SYNC_ENABLED': raw})
            self.assertTrue(config.enabled, raw)

    def test_false_variants(self):
        for raw in ('false', 'False', '0', 'no', 'off'):
            config = scheduler_config_from_env({'ARTICLE_METADATA_SYNC_ENABLED': raw})
            self.assertFalse(config.enabled, raw)

    def test_invalid_enabled_raises_loudly(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_ENABLED': 'maybe'})


class IntervalAndBatchValidationTests(unittest.TestCase):
    def test_valid_override(self):
        config = scheduler_config_from_env({
            'ARTICLE_METADATA_SYNC_INTERVAL_DAYS': '7',
            'ARTICLE_METADATA_SYNC_BATCH_SIZE': '25',
        })
        self.assertEqual(config.interval_days, 7)
        self.assertEqual(config.batch_size, 25)

    def test_negative_interval_raises(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_INTERVAL_DAYS': '-1'})

    def test_zero_interval_raises(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_INTERVAL_DAYS': '0'})

    def test_non_numeric_interval_raises(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_INTERVAL_DAYS': 'abc'})

    def test_negative_batch_raises(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_BATCH_SIZE': '-5'})

    def test_zero_batch_raises(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_BATCH_SIZE': '0'})

    def test_non_numeric_batch_raises(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_METADATA_SYNC_BATCH_SIZE': 'lots'})

    def test_does_not_mutate_process_environ(self):
        """`environ=None` bo'lsa `os.environ`dan o'qiydi, lekin test bu
        yerda ATAYIN alohida dict beradi — process env'ga tegilmasligini
        tekshiradi (§43 bilan bir xil printsip: testlar env'ga tegmaydi)."""
        import os
        before = dict(os.environ)
        scheduler_config_from_env({'ARTICLE_METADATA_SYNC_BATCH_SIZE': '99'})
        self.assertEqual(before, dict(os.environ))


if __name__ == '__main__':
    unittest.main()
