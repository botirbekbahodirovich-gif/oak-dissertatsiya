# -*- coding: utf-8 -*-
"""Fan yo'nalishlari ierarxiyasini seed/import qiladi (idempotent, tranzaksiyali).

Loyihada Flask CLI komandalari yo'q — barcha seed/migratsiya ishlari
`scripts/*.py` standalone skriptlari orqali bajariladi (seed_grants.py,
apply_institution_merges.py naqshi).

    # 22 ta fan tarmog'i (repozitoriy seed'i)
    DATABASE_URL=... python scripts/seed_scientific_fields.py \
        --file data/scientific_fields.json --apply

    # OAK ixtisosliklari (parse_oak_specialties.py chiqargan dataset)
    DATABASE_URL=... python scripts/seed_scientific_fields.py \
        --file data/oak/normalized/oak_specialties_approved.json
    DATABASE_URL=... python scripts/seed_scientific_fields.py \
        --file data/oak/normalized/oak_specialties_approved.json --apply

DIQQAT: `--apply` BERILMASA script DRY-RUN rejimida ishlaydi va hech nima
yozmaydi. Bu ataylab: 289 yozuvli datasetni tasodifan productionga tushirib
yuborish oson, uni orqaga qaytarish esa qiyin.

Rejimlar:
  --file <path>          JSON dataset (default: data/scientific_fields.json)
  --apply                haqiqatan yozadi (aks holda dry-run)
  --dry-run              aniqlik uchun; --apply bilan birga berilmasin
  --update-names         MAVJUD yozuvning nomini almashtirishga ruxsat beradi.
                         Berilmasa nom farqi CONFLICT sifatida qayd etiladi va
                         yozuv o'zgarmaydi (§10: mavjud nomni jimlik bilan
                         almashtirmaslik).
  --from-dissertations   yo'nalishlarni production `dissertations` jadvalidagi
                         haqiqiy ixtisoslik/ixtisoslik_nomi juftliklaridan
                         quradi (OAK importidan kelgan ma'lumot).
  --allow-sample         `sample: true` deb belgilangan development faylini
                         qabul qiladi. Bayroqsiz sample fayl RAD ETILADI.

Idempotentlik: yozuvlar `code` bo'yicha (kod bo'lmasa `slug` bo'yicha)
solishtiriladi. Ikkinchi run dublikat yaratmaydi. Datasetdan yo'qolgan yozuv
HECH QACHON avtomatik o'chirilmaydi (unga maqolalar bog'langan bo'lishi
mumkin) — u faqat hisobotda "datasetda yo'q" deb ko'rsatiladi.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.scientific_fields import (branch_code, field_slug,  # noqa: E402
                                     level_from_code, normalize_code,
                                     parent_code, resolve_level)

# Import qilishga ruxsat etilgan review holatlari (§15). Boshqa har qanday
# qiymat — jumladan `review_required` — rad etiladi.
ALLOWED_REVIEW_STATUSES = frozenset({
    'auto_clean', 'auto_normalized', 'manual_approved', 'seed',
})

# Ota yozuvni hal qilish natijasi (§16).
PARENT_EXISTS = 'parent_exists'
PARENT_MISSING_KNOWN_CODE = 'parent_missing_but_known_code'
PARENT_UNRESOLVABLE = 'parent_unresolvable'


class SeedError(Exception):
    """Seed faylidagi yoki ma'lumotdagi aniq, tuzatilishi mumkin bo'lgan xato."""


def load_seed(path, allow_sample=False):
    with io.open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if doc.get('sample') and not allow_sample:
        raise SeedError(
            "Bu development sample fayli. Production bazaga yozish uchun "
            "--allow-sample bayrog'ini ataylab qo'shing.")
    fields = doc.get('fields') or doc.get('records')
    if not isinstance(fields, list) or not fields:
        raise SeedError("Seed faylida 'fields'/'records' ro'yxati yo'q yoki bo'sh.")
    metadata = doc.get('metadata') or {}
    if metadata and not doc.get('source_name'):
        doc.setdefault('source_name', metadata.get('source_file'))
        doc.setdefault('source_sha256', metadata.get('source_sha256'))
    return doc, fields


def normalize_entries(doc, fields):
    """Seed yozuvlarini tekshiradi va tartiblaydi (ota avval, bola keyin).

    Ikki format qabul qilinadi: repozitoriy seed'i (`name_uz` + `code`) va
    OAK dataseti (`name_uz_cyrillic` + `name_uz` + `review_status` + manba
    maydonlari). Ikkalasi bir xil ichki shaklga keltiriladi.
    """
    seen_codes = set()
    entries = []
    for raw in fields:
        name = (raw.get('name_uz') or '').strip()
        cyrillic = (raw.get('name_uz_cyrillic') or '').strip() or None
        if not name and cyrillic:
            name = cyrillic
        if not name:
            raise SeedError('name_uz bo\'sh yozuv topildi: %r' % (raw,))

        code = normalize_code(raw.get('code'))
        if code and code in seen_codes:
            raise SeedError('Seed faylida takroriy shifr: %s' % code)
        if code:
            seen_codes.add(code)

        review_status = (raw.get('review_status') or 'seed').strip()
        if review_status not in ALLOWED_REVIEW_STATUSES:
            raise SeedError(
                'Yozuv tasdiqlanmagan (review_status=%r), import qilinmaydi: %s'
                % (review_status, code or name))

        entries.append({
            'name_uz': name,
            'name_uz_cyrillic': cyrillic,
            'name_ru': (raw.get('name_ru') or '').strip() or None,
            'code': code,
            'parent_code': normalize_code(raw.get('parent_code')) or (
                parent_code(code) if code else None),
            'description': (raw.get('description') or '').strip() or None,
            'sort_order': int(raw.get('sort_order') or 0),
            'level': raw.get('level'),
            'slug': (raw.get('slug') or '').strip() or None,
            'raw_name': raw.get('raw_name'),
            'review_status': review_status,
            'source_name': raw.get('source_name') or doc.get('source_name'),
            'source_url': raw.get('source_url') or doc.get('source_url'),
            'source_updated_at': raw.get('source_updated_at') or doc.get('source_updated_at'),
            'source_line': raw.get('source_line'),
            'source_sha256': raw.get('source_sha256') or doc.get('source_sha256'),
        })

    # OAK shifri ierarxiyani kodlaydi, shuning uchun shifr bo'yicha tartiblash
    # otani boladan oldinga qo'yadi (01.00.00 < 01.01.00 < 01.01.02).
    entries.sort(key=lambda e: (e['code'] or 'zz', e['sort_order'], e['name_uz']))
    return entries


def _existing_index(cur):
    cur.execute("SELECT id, code, slug, name_uz, description, parent_id, level, "
                "       review_status, source_sha256 "
                "  FROM scientific_fields")
    by_code, by_slug = {}, {}
    for row in cur.fetchall():
        record = {'id': row[0], 'code': row[1], 'slug': row[2], 'name_uz': row[3],
                  'description': row[4], 'parent_id': row[5], 'level': row[6],
                  'review_status': row[7], 'source_sha256': row[8]}
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


def resolve_parent(entry, by_code):
    """(status, parent_record_or_None, missing_parent_code_or_None) — §16.

    Ota yozuv bazada bo'lmasa SUN'IY OTA YARATILMAYDI (uning rasmiy nomi
    yo'q). Buning o'rniga eng yaqin MAVJUD ajdod (odatda fan tarmog'i)
    topiladi va haqiqiy ota shifri `missing_parent_code` da saqlanadi —
    UI shu KODni ko'rsatadi, o'ylab topilgan nomni emas.
    """
    target = entry['parent_code']
    if not target:
        return PARENT_EXISTS, None, None          # ildiz yozuv (fan tarmog'i)

    parent = by_code.get(target)
    if parent is not None:
        return PARENT_EXISTS, parent, None

    ancestor_code = branch_code(entry['code']) if entry['code'] else None
    ancestor = by_code.get(ancestor_code) if ancestor_code else None
    if ancestor is not None and ancestor_code != target:
        return PARENT_MISSING_KNOWN_CODE, ancestor, target

    return PARENT_UNRESOLVABLE, None, target


WRITE_COLUMNS = ('parent_id', 'name_uz', 'name_uz_cyrillic', 'name_ru', 'code',
                 'slug', 'level', 'description', 'sort_order', 'source_name',
                 'source_url', 'source_updated_at', 'raw_name', 'source_line',
                 'source_sha256', 'review_status', 'missing_parent_code')


def upsert_fields(cur, entries, dry_run=True, update_names=False):
    """Idempotent upsert. Natija — batafsil hisobot lug'ati."""
    by_code, by_slug = _existing_index(cur)
    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'conflict': 0,
             'unresolvable': 0}
    conflicts = []
    unresolvable = []
    missing_parent = []
    fake_id = 0

    for entry in entries:
        status, parent, missing_code = resolve_parent(entry, by_code)

        if status == PARENT_UNRESOLVABLE:
            # Bog'lash mumkin bo'lgan hech qanday ajdod yo'q. Bunday yozuvni
            # parent_id=NULL bilan kiritish uni daraxtda soxta ILDIZ qilib
            # ko'rsatardi — bu noto'g'ri, shuning uchun o'tkazib yuboriladi.
            stats['unresolvable'] += 1
            unresolvable.append({'code': entry['code'], 'name_uz': entry['name_uz'],
                                 'missing_parent_code': missing_code})
            continue

        if status == PARENT_MISSING_KNOWN_CODE:
            missing_parent.append({'code': entry['code'],
                                   'missing_parent_code': missing_code,
                                   'attached_to': parent['code']})

        parent_id = parent['id'] if parent else None
        parent_level = parent['level'] if parent else None
        level = entry['level'] or resolve_level(entry['code'], parent_level)

        existing = by_code.get(entry['code']) if entry['code'] else None
        if existing is None and entry['slug']:
            existing = by_slug.get(entry['slug'])
        if existing is None:
            existing = by_slug.get(field_slug(entry['name_uz'], entry['code']))

        if existing:
            name_changed = existing['name_uz'] != entry['name_uz']
            if name_changed and not update_names:
                # Mavjud nom jimlik bilan almashtirilmaydi (§10).
                stats['conflict'] += 1
                conflicts.append({
                    'code': entry['code'], 'id': existing['id'],
                    'existing_name_uz': existing['name_uz'],
                    'incoming_name_uz': entry['name_uz'],
                    'existing_review_status': existing['review_status'],
                    'incoming_review_status': entry['review_status'],
                })
                continue

            changed = (name_changed
                       or existing['description'] != entry['description']
                       or existing['parent_id'] != parent_id
                       or existing['level'] != level
                       or existing['source_sha256'] != entry['source_sha256'])
            if not changed:
                stats['skipped'] += 1
                continue

            if not dry_run:
                cur.execute("""UPDATE scientific_fields
                                  SET name_uz = %s, name_uz_cyrillic = %s,
                                      name_ru = %s, description = %s,
                                      parent_id = %s, level = %s, sort_order = %s,
                                      source_name = %s, source_url = %s,
                                      source_updated_at = %s, raw_name = %s,
                                      source_line = %s, source_sha256 = %s,
                                      review_status = %s, missing_parent_code = %s,
                                      updated_at = NOW()
                                WHERE id = %s""",
                            (entry['name_uz'], entry['name_uz_cyrillic'],
                             entry['name_ru'], entry['description'], parent_id,
                             level, entry['sort_order'], entry['source_name'],
                             entry['source_url'], entry['source_updated_at'],
                             entry['raw_name'], entry['source_line'],
                             entry['source_sha256'], entry['review_status'],
                             missing_code, existing['id']))
            stats['updated'] += 1
            existing.update(name_uz=entry['name_uz'], description=entry['description'],
                            parent_id=parent_id, level=level,
                            source_sha256=entry['source_sha256'])
            continue

        base_slug = entry['slug'] or field_slug(entry['name_uz'], entry['code'])
        slug = _unique_slug(cur, base_slug)
        values = {
            'parent_id': parent_id, 'name_uz': entry['name_uz'],
            'name_uz_cyrillic': entry['name_uz_cyrillic'], 'name_ru': entry['name_ru'],
            'code': entry['code'], 'slug': slug, 'level': level,
            'description': entry['description'], 'sort_order': entry['sort_order'],
            'source_name': entry['source_name'], 'source_url': entry['source_url'],
            'source_updated_at': entry['source_updated_at'],
            'raw_name': entry['raw_name'], 'source_line': entry['source_line'],
            'source_sha256': entry['source_sha256'],
            'review_status': entry['review_status'],
            'missing_parent_code': missing_code,
        }
        if dry_run:
            # Dry-run'da ham keyingi bolalar ota ID sini topa olishi kerak,
            # shuning uchun indeksga soxta (manfiy) ID bilan yoziladi.
            fake_id += 1
            new_id = -fake_id
        else:
            cur.execute(
                "INSERT INTO scientific_fields (%s) VALUES (%s) RETURNING id"
                % (', '.join(WRITE_COLUMNS),
                   ', '.join(['%s'] * len(WRITE_COLUMNS))),
                [values[c] for c in WRITE_COLUMNS])
            new_id = cur.fetchone()[0]

        record = {'id': new_id, 'code': entry['code'], 'slug': slug,
                  'name_uz': entry['name_uz'], 'description': entry['description'],
                  'parent_id': parent_id, 'level': level,
                  'review_status': entry['review_status'],
                  'source_sha256': entry['source_sha256']}
        if entry['code']:
            by_code[entry['code']] = record
        by_slug[slug] = record
        stats['created'] += 1

    return {'stats': stats, 'conflicts': conflicts, 'unresolvable': unresolvable,
            'missing_parent': missing_parent}


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
            'review_status': 'seed',
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


def _print_report(result, dry_run):
    stats = result['stats']
    print('\nNatija%s:' % (' (DRY-RUN)' if dry_run else ''))
    print('  Yangi        : %d' % stats['created'])
    print('  Yangilangan  : %d' % stats['updated'])
    print("  O'zgarishsiz : %d" % stats['skipped'])
    print('  Konflikt     : %d' % stats['conflict'])
    print('  Ota topilmadi: %d' % stats['unresolvable'])

    if result['conflicts']:
        print('\nNOM KONFLIKTLARI (yozuvlar O\'ZGARTIRILMADI — --update-names kerak):')
        for item in result['conflicts']:
            print('  %s  mavjud: %r' % (item['code'], item['existing_name_uz']))
            print('  %s  kelgan: %r' % (' ' * len(item['code']), item['incoming_name_uz']))

    if result['unresolvable']:
        print('\nOTA YO\'NALISH TOPILMADI — bu yozuvlar IMPORT QILINMADI:')
        for item in result['unresolvable']:
            print('  %s  (kerakli ota: %s)  %s'
                  % (item['code'], item['missing_parent_code'], item['name_uz']))

    if result['missing_parent']:
        groups = sorted({i['missing_parent_code'] for i in result['missing_parent']})
        print('\nRASMIY GURUH NOMI YO\'Q — %d ta yozuv eng yaqin mavjud tarmoqqa '
              'bog\'landi,' % len(result['missing_parent']))
        print('haqiqiy ota shifri missing_parent_code da saqlandi (sun\'iy nom '
              'YARATILMADI):')
        for code in groups:
            count = sum(1 for i in result['missing_parent']
                        if i['missing_parent_code'] == code)
            print('  %s  — %d ta ixtisoslik' % (code, count))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--file', default='data/scientific_fields.json')
    parser.add_argument('--apply', action='store_true',
                        help='haqiqatan yozadi (berilmasa dry-run)')
    parser.add_argument('--dry-run', action='store_true',
                        help='aniqlik uchun; --apply bilan birga berilmasin')
    parser.add_argument('--update-names', action='store_true')
    parser.add_argument('--from-dissertations', action='store_true')
    parser.add_argument('--allow-sample', action='store_true')
    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        return '--apply va --dry-run birga berilmaydi'
    dry_run = not args.apply

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
                    print("DIQQAT: quyidagi ota yo'nalishlar bazada yo'q va bu "
                          "skript ularning NOMINI o'ylab topmaydi:")
                    for code in gaps:
                        print('  -', code)
            else:
                doc, raw_entries = load_seed(args.file, allow_sample=args.allow_sample)
                print('Dataset : %s (%d ta yozuv)' % (args.file, len(raw_entries)))
                print('Manba   : %s' % (doc.get('source_name') or '—'))
                if doc.get('source_sha256'):
                    print('SHA-256 : %s' % doc['source_sha256'])

            entries = normalize_entries(doc, raw_entries)
            result = upsert_fields(cur, entries, dry_run=dry_run,
                                   update_names=args.update_names)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        _print_report(result, dry_run)
        if dry_run:
            print('\nDRY-RUN — hech narsa yozilmadi. Yozish uchun: --apply')
        else:
            print('\nQo\'llandi: %s' % datetime.now(timezone.utc).isoformat())
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
