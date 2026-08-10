# -*- coding: utf-8 -*-
"""Scheduled article discovery konfiguratsiyasi testlari (Phase 2D.3, §13, §19)."""
import unittest

from services.article_discovery.scheduler_config import (SAFETY_MAX_QUERIES,
                                                          SchedulerConfigError,
                                                          scheduler_config_from_env)


class DefaultsTests(unittest.TestCase):
    def test_disabled_by_default(self):
        """§13 — production xavfsizligi: sukut bo'yicha FALSE."""
        config = scheduler_config_from_env({})
        self.assertFalse(config.enabled)

    def test_default_interval_and_max_queries(self):
        config = scheduler_config_from_env({})
        self.assertEqual(config.interval_minutes, 15)
        self.assertEqual(config.max_queries, 10)


class EnabledParsingTests(unittest.TestCase):
    def test_true_variants_enable(self):
        for value in ('true', 'True', '1', 'yes', 'on'):
            config = scheduler_config_from_env(
                {'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': value})
            self.assertTrue(config.enabled, value)

    def test_false_variants_disable(self):
        for value in ('false', 'False', '0', 'no', 'off'):
            config = scheduler_config_from_env(
                {'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': value})
            self.assertFalse(config.enabled, value)

    def test_invalid_enabled_raises_loudly(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_DISCOVERY_SCHEDULER_ENABLED': 'maybe'})


class MaxQueriesTests(unittest.TestCase):
    def test_custom_max_queries_accepted(self):
        config = scheduler_config_from_env(
            {'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES': '5'})
        self.assertEqual(config.max_queries, 5)

    def test_max_queries_above_safety_cap_rejected(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env(
                {'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES': str(SAFETY_MAX_QUERIES + 1)})

    def test_max_queries_at_safety_cap_accepted(self):
        config = scheduler_config_from_env(
            {'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES': str(SAFETY_MAX_QUERIES)})
        self.assertEqual(config.max_queries, SAFETY_MAX_QUERIES)

    def test_zero_max_queries_rejected(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES': '0'})

    def test_non_integer_max_queries_rejected(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env({'ARTICLE_DISCOVERY_SCHEDULER_MAX_QUERIES': 'abc'})


class IntervalTests(unittest.TestCase):
    def test_custom_interval_accepted(self):
        config = scheduler_config_from_env(
            {'ARTICLE_DISCOVERY_SCHEDULER_INTERVAL_MINUTES': '30'})
        self.assertEqual(config.interval_minutes, 30)

    def test_negative_interval_rejected(self):
        with self.assertRaises(SchedulerConfigError):
            scheduler_config_from_env(
                {'ARTICLE_DISCOVERY_SCHEDULER_INTERVAL_MINUTES': '-5'})


if __name__ == '__main__':
    unittest.main()
