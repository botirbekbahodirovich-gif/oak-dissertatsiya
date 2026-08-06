# -*- coding: utf-8 -*-
"""Fan yo'nalishlari ierarxiyasini seed qiladi (idempotent, tranzaksiyali).

Loyihada Flask CLI komandalari yo'q — barcha seed/migratsiya ishlari
`scripts/*.py` standalone skriptlari orqali bajariladi (seed_grants.py,
apply_institution_merges.py naqshi). Shuning uchun bu ham shunday:

    DATABASE_URL=... python scripts/seed_scientific_fields.py \
        --file data/scientific_fields.json --dry-run
    DATABASE_URL=... python scripts/seed_scientific_fields.py \
        --file data/scientific_fields.json

Rejimlar:
  --file <path>          JSON seed fayli (default: data/scientific_fields.json)
  --dry-run              hech nima yozmaydi, faqat nima o'zgarishini ko'rsatadi
  --from-dissertations   yo'nalishlarni production `dissertations` jadvalidagi
                         HAQIQIY ixtisoslik/ixtisoslik_nomi juftliklaridan
                         quradi (OAK importidan kelgan ma'lumot). Sun'iy nom
                         o'ylab topilmaydi: nomi yo'q shifr o'tkazib yuboriladi.
  --allow-sample         `sample: true` deb belgilangan development faylini
                         qabul qiladi. Bu bayroqsiz sample fayl RAD ETILADI —
                         production bazaga tasodifan tushib qolmasin (§32).

Idempotentlik: yozuvlar `code` bo'yicha (kod bo'lmasa `slug` bo'yicha)
solishtiriladi. Ikkinchi marta ishga tushirilganda dublikat yaratilmaydi;
o'zgargan nom/tavsif yangilanadi va "updated" sifatida sanaladi.

Xato bo'lsa butun tranzaksiya rollback qilinadi — yarim seed holat qolmaydi.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.scientific_fields import (branch_code, field_slug, level_from_code,
                                     normalize_code, parent_code, resolve_level)


class SeedError(Exception):
    """Seed faylidagi yoki ma'lumotdagi aniq, tuzatilishi mumkin bo'lgan xato."""


def load_seed(path, allow_sample=False):
    with io.open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if doc.get('sample') and not allow_sample:
        raise SeedError(
            "Bu development sample fayli. Production bazaga yozish uchun "
            "--allow-sample bayrog'ini ataylab qo'shing.")
    fields = doc.get('fields')
    if not isinstance(fields, list) or not fields:
        raise SeedError("Seed faylida 'fields' ro'yxati yo'q yoki bo'sh.")
    return doc, fields


def normalize_entries(doc, fields):
    """Seed yozuvlarini tekshiradi va tartiblaydi (ota avval, bola keyin)."""
    seen_codes = set()
    entries = []
    for raw in fields:
        name = (raw.get('name_uz') or '').strip()
        if not name:
            raise SeedError('name_uz bo\'sh yozuv topildi: %r' % (raw,))
        code = normalize_code(raw.get('code'))
        if code and code in seen_codes:
            raise SeedError('Seed faylida takroriy shifr: %s' % code)
        if code:
            seen_codes.add(code)
        entries.append({
            'name_uz': name,
            'name_uz_cyrillic': (raw.get('name_uz_cyrillic') or '').strip() or None,
            'name_ru': (raw.get('name_ru') or '').strip() or None,
            'code': code,
            'parent_code': normalize_code(raw.get('parent_code')) or (parent_code(code) if code else None),
            'description': (raw.get('description') or '').strip() or None,
            'sort_order': int(raw.get('sort_order') or 0),
            'level': raw.get('level'),
            'source_name': raw.get('source_name') or doc.get('source_name'),
            'source_url': raw.get('source_url') or doc.get('source_url'),
            'source_updated_at': raw.get('source_updated_at') or doc.get('source_updated_at'),
        })

    for entry in entries:
        if entry['parent_code'] and entry['parent_code'] not in seen_codes:
            # Ota yozuv seed faylida bo'lmasligi mumkin — u DB da bo'lishi
            # kerak. Bu yerda rad etmaymiz, upsert paytida tekshiriladi.
            continue
    # OAK shifri ierarxiyani kodlaydi, shuning uchun shifr bo'yicha tartiblash
    # otani boladan oldinga qo'yadi (01.00.00 < 01.01.00 < 01.01.02).
    entries.sort(key=lambda e: (e['code'] or 'zz', e['sort_order'], e['name_uz']))
    return entries


def _existing_index(cur):
    cur.execute("SELECT id, code, slug, name_uz, description, parent_id, level "
                "FROM scientific_fields")
    by_code, by_slug = {}, {}
    for row in cur.fetchall():
        record = {'id': row[0], 'code': row[1], 'slug': row[2], 'name_uz': row[3],
                  'description': row[4], 'parent_id': row[5], 'level': row[6]}
        if record['code']:
            by_code[record['code']] = record
        by_slug[record['slug']] = record
    return by_code, by_slug


def _unique_slug(cur, base, exclude_id=None):
    slug, n = base, 2
    while True:
        if exclude_id:
            cur.execute("SELECT 1 FROM scientific_fields WHERE slug = %s AND id <> %s",
                        (slug, exclude_id))
        else:
            cur.execute("SELECT 1 FROM scientific_fields WHERE slug = %s", (slug,))
        if not cur.fetchone():
            return slug
        slug, n = f'{base}-{n}', n + 1


def upsert_fields(cur, entries, dry_run=False):
    """(created, updated, skipped) — idempotent upsert."""
    by_code, by_slug = _existing_index(cur)
    created = updated = skipped = 0

    for entry in entries:
        parent_id = None
        if entry['parent_code']:
            parent = by_code.get(entry['parent_code'])
            if parent is None:
                raise SeedError(
                    'Ota yo\'nalish topilmadi: %s (bola: %s). Avval ota '
                    'yo\'nalishni seed qiling.' % (entry['parent_code'], entry['code']))
            parent_id = parent['id']

        parent_level = by_code.get(entry['parent_code'], {}).get('level') if entry['parent_code'] else None
        level = entry['level'] or resolve_level(entry['code'], parent_level)

        existing = by_code.get(entry['code']) if entry['code'] else None
        if existing is None:
            existing = by_slug.get(field_slug(entry['name_uz'], entry['code']))

        if existing:
            changed = (existing['name_uz'] != entry['name_uz']
                       or existing['description'] != entry['description']
                       or existing['parent_id'] != parent_id
                       or existing['level'] != level)
            if not changed:
                skipped += 1
                continue
            if not dry_run:
                cur.execute("""UPDATE scientific_fields
                                  SET name_uz = %s, name_uz_cyrillic = %s, name_ru = %s,
                                      description = %s, parent_id = %s, level = %s,
                                      sort_order = %s, source_name = %s, source_url = %s,
                                      source_updated_at = %s, updated_at = NOW()
                                WHERE id = %s""",
                            (entry['name_uz'], entry['name_uz_cyrillic'], entry['name_ru'],
                             entry['description'], parent_id, level, entry['sort_order'],
                             entry['source_name'], entry['source_url'],
                             entry['source_updated_at'], existing['id']))
            updated += 1
            existing.update(name_uz=entry['name_uz'], description=entry['description'],
                            parent_id=parent_id, level=level)
            continue

        slug = _unique_slug(cur, field_slug(entry['name_uz'], entry['code']))
        if dry_run:
            # Dry-run'da ham keyingi bolalar ota ID sini topa olishi kerak,
            # shuning uchun indeksga soxta (manfiy) ID bilan yoziladi.
            record = {'id': -(created + 1), 'code': entry['code'], 'slug': slug,
                      'name_uz': entry['name_uz'], 'description': entry['description'],
                      'parent_id': parent_id, 'level': level}
        else:
            cur.execute("""INSERT INTO scientific_fields
                           (parent_id, name_uz, name_uz_cyrillic, name_ru, code, slug,
                            level, description, sort_order, source_name, source_url,
                            source_updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           RETURNING id""",
                        (parent_id, entry['name_uz'], entry['name_uz_cyrillic'],
                         entry['name_ru'], entry['code'], slug, level,
                         entry['description'], entry['sort_order'], entry['source_name'],
                         entry['source_url'], entry['source_updated_at']))
            record = {'id': cur.fetchone()[0], 'code': entry['code'], 'slug': slug,
                      'name_uz': entry['name_uz'], 'description': entry['description'],
                      'parent_id': parent_id, 'level': level}
        if entry['code']:
            by_code[entry['code']] = record
        by_slug[slug] = record
        created += 1

    return created, updated, skipped


def entries_from_dissertations(cur):
    """Yo'nalishlarni production dissertations jadvalidan quradi.

    Bu SUN'IY ma'lumot emas: `ixtisoslik` (OAK shifri) va `ixtisoslik_nomi`
    juftliklari OAK importidan keladi. Nomi bo'sh bo'lgan shifr o'tkazib
    yuboriladi — nom o'ylab topilmaydi.
    """
    cur.execute("""
        SELECT TRIM(ixtisoslik) AS code,
               MODE() WITHIN GROUP (ORDER BY TRIM(ixtisoslik_nomi)) AS name,
               COUNT(*) AS cnt
          FROM dissertations
         WHERE COALESCE(TRIM(ixtisoslik), '') <> ''
           AND COALESCE(TRIM(ixtisoslik_nomi), '') <> ''
      GROUP BY TRIM(ixtisoslik)
      ORDER BY 1
    """)
    entries = []
    for code, name, _count in cur.fetchall():
        code = normalize_code(code)
        if not code or level_from_code(code) is None:
            continue
        entries.append({
            'name_uz': (name or '').strip(),
            'code': code,
            'sort_order': 0,
            'source_name': 'OAK import (dissertations.ixtisoslik_nomi)',
            'source_url': None,
        })
    return entries


def missing_branches(entries, cur):
    """Bolalari bor, lekin o'zi yo'q fan tarmoqlarini aniqlaydi."""
    cur.execute("SELECT code FROM scientific_fields WHERE code IS NOT NULL")
    known = {row[0] for row in cur.fetchall()} | {e['code'] for e in entries if e.get('code')}
    needed = set()
    for entry in entries:
        for ancestor in (branch_code(entry.get('code')), parent_code(entry.get('code'))):
            if ancestor and ancestor not in known:
                needed.add(ancestor)
    return sorted(needed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--file', default='data/scientific_fields.json')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--from-dissertations', action='store_true')
    parser.add_argument('--allow-sample', action='store_true')
    args = parser.parse_args(argv)

    url = os.environ.get('DATABASE_URL')
    if not url:
        return 'DATABASE_URL sozlanmagan'

    try:
        import psycopg2
    except ImportError:
        return 'pip install psycopg2-binary'

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            from blueprints.articles import _ensure_schema
            _ensure_schema(cur)

            if args.from_dissertations:
                raw_entries = entries_from_dissertations(cur)
                doc = {'source_name': 'OAK import (dissertations)', 'source_url': None,
                       'source_updated_at': None}
                print('dissertations dan %d ta ixtisoslik topildi' % len(raw_entries))
                gaps = missing_branches(raw_entries, cur)
                if gaps:
                    print('DIQQAT: quyidagi ota yo\'nalishlar bazada yo\'q va bu '
                          'skript ularning NOMINI o\'ylab topmaydi:')
                    for code in gaps:
                        print('  -', code)
                    print('Avval `--file data/scientific_fields.json` bilan fan '
                          'tarmoqlarini seed qiling.')
            else:
                doc, raw_entries = load_seed(args.file, allow_sample=args.allow_sample)
                print('Seed fayli: %s (%d ta yozuv)' % (args.file, len(raw_entries)))
                print('Manba: %s' % (doc.get('source_name') or '—'))

            entries = normalize_entries(doc, raw_entries)
            created, updated, skipped = upsert_fields(cur, entries, dry_run=args.dry_run)

        if args.dry_run:
            conn.rollback()
            print('DRY-RUN — hech narsa yozilmadi.')
        else:
            conn.commit()
        print('Yangi: %d | Yangilangan: %d | O\'zgarishsiz: %d' % (created, updated, skipped))
    except SeedError as exc:
        conn.rollback()
        return 'Seed xatosi: %s' % exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
