#!/usr/bin/env python3.12
# -*- coding: utf-8 -*-
"""Haqiqiy Crossref / OpenAlex / Unpaywall bilan developer smoke testi.

Bu skript unittest to'plamiga KIRMAYDI (§34, §40): u tashqi tarmoqqa chiqadi,
demak API yotganda "kod buzildi" degan yolg'on signal berardi. Uni qo'lda,
sozlamalarni tekshirish uchun ishga tushiring:

    SCHOLARLY_API_EMAIL=siz@example.uz \\
        python3.12 scripts/smoke_article_metadata.py

    # bitta DOI bilan
    python3.12 scripts/smoke_article_metadata.py 10.1038/nature12373

Chiqishda API kaliti yoki e-mail ko'rsatilmaydi — faqat sozlangan/sozlanmagan
holati. Hech qanday javob repozitoriyga saqlanmaydi.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.article_metadata import (config_from_env, describe_configuration,  # noqa: E402
                                       fetch_article_metadata)
from services.article_metadata.models import PROVIDER_LABELS  # noqa: E402

# Ommaviy, barqaror DOI'lar: biri ochiq kirishli, biri yopiq.
DEFAULT_DOIS = [
    '10.1371/journal.pone.0000308',   # PLOS ONE — ochiq kirish (gold)
    '10.1038/nature12373',            # Nature — odatda yopiq
]


def main(argv):
    dois = argv[1:] or DEFAULT_DOIS
    config = config_from_env()
    status = describe_configuration(config)
    print('Sozlamalar (qiymatlar ko‘rsatilmaydi):')
    for provider, configured in status.items():
        print('  %-10s %s' % (PROVIDER_LABELS[provider],
                              'sozlangan' if configured else 'SOZLANMAGAN'))
    print()

    failures = 0
    for doi in dois:
        print('── %s' % doi)
        try:
            result = fetch_article_metadata(doi, config=config)
        except Exception as exc:                       # noqa: BLE001 — smoke
            print('   XATO: %s: %s' % (type(exc).__name__, exc))
            failures += 1
            continue

        print('   holat: %s' % result.status)
        for provider, outcome in sorted(result.providers.items()):
            print('   %-10s %-16s %s ms' % (provider, outcome.status,
                                            outcome.elapsed_ms))
        meta = result.metadata
        print('   sarlavha: %s' % (meta.title or '—'))
        print('   jurnal:   %s (%s)' % (meta.journal_name or '—',
                                        meta.publication_year or '—'))
        print('   mualliflar: %d, ORCID bilan: %d'
              % (len(meta.authors), sum(1 for a in meta.authors if a.orcid)))
        print('   OA: %s · iqtibos: %s · OpenAlex: %s'
              % (meta.oa_status or '—', meta.citation_count, meta.openalex_id or '—'))
        print('   litsenziya nomzodlari: %s'
              % ([c.license_type or c.label for c in meta.license_candidates] or '—'))
        if result.conflicts:
            print('   FARQLAR:')
            for conflict in result.conflicts:
                print('     %s: %s (%s) ← %s' % (
                    conflict.field, conflict.preferred, conflict.preferred_source,
                    conflict.alternatives))
        for warning in result.warnings:
            print('   ! %s' % warning.message)
        if result.status == 'failed':
            failures += 1
        print()

    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
