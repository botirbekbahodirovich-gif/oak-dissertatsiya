# -*- coding: utf-8 -*-
"""Rasmiy OAK ro'yxati PDF'idan 1- va 2-daraja yozuvlarni ajratadi.

MANBA
    "Олий малакали илмий ва илмий-педагог кадрлар ихтисосликлари рўйхати"
    https://oak.uz/userfiles/files/Ixtisosliklar_ro'yxati_2021.pdf
    SHA-256: 39e4e3fd2641f359de4e36c0dc16f7c6862bc96e4c78db9b422df3355e21ed6a

PDF repozitoriyga QO'SHILMAGAN (1 MB, rasmiy sayt hosting qiladi). Uning
o'rniga natija — `data/oak/normalized/oak_specialty_groups.json` — commit
qilingan, va bu script uni qayta yaratish yo'lini saqlaydi.

NEGA BU KERAK: OAK shifri ierarxiyani belgilaydi, lekin GURUH (XX.YY.00)
nomlari saytdan yig'ilgan ixtisosliklar ro'yxatida yo'q edi. Ularsiz
3-daraja ixtisosliklar 1-daraja tarmoqqa bog'lanib qolardi — bu rasmiy
ierarxiyani buzadi. Nomlar bu hujjatdan AYNAN ko'chiriladi, tarjima yoki
taxmin qilinmaydi.

HUJJAT TUZILMASI: bitta PDF ichida bir xil jadval uch marta takrorlanadi —
o'zbek kirill, rus, ingliz. Til segmentini ofset bilan taxmin qilish mo'rt,
shuning uchun har bir kodning barcha uchrashlari yig'iladi va yozuv turiga
qarab ajratiladi: birinchi kirill = o'zbek, ikkinchi kirill = rus.

ISHLATILISHI (pypdf kerak, u loyiha bog'liqligi EMAS — faqat shu bir martalik
ajratish uchun):

    pip install pypdf
    curl -o /tmp/oak.pdf "https://oak.uz/userfiles/files/Ixtisosliklar_ro'yxati_2021.pdf"
    sha256sum /tmp/oak.pdf     # yuqoridagi hash bilan solishtiring
    python scripts/extract_oak_groups.py --pdf /tmp/oak.pdf \
        --output data/oak/normalized/oak_specialty_groups.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.oak_translit import transliterate_name  # noqa: E402
from utils.scientific_fields import field_slug  # noqa: E402

SOURCE_NAME = ("OAK — Oliy malakali ilmiy va ilmiy-pedagog kadrlar "
               "ixtisosliklari ro'yxati (2021)")
SOURCE_URL = "https://oak.uz/userfiles/files/Ixtisosliklar_ro'yxati_2021.pdf"
SOURCE_SHA256 = '39e4e3fd2641f359de4e36c0dc16f7c6862bc96e4c78db9b422df3355e21ed6a'
SOURCE_YEAR = 2021

CYRILLIC = re.compile(r'[А-Яа-яЁёЎўҚқҲҳҒғ]')
LATIN = re.compile(r'[A-Za-z]')
GROUP_RE = re.compile(r'(\d{2}\.\d{2}\.00)\s*–\s*([^\n]+(?:\n(?!\s*\d{2}\.)[^\n]*){0,3})')
# 21.00.00 jadval katagida ustunga sig'may ikki qatorga bo'lingan.
BRANCH_21_UZ = re.compile(r'21\.00\.00\s*\n\s*(Ҳарбий)\s*\n\s*(фанлар)')
BRANCH_21_RU = re.compile(r'21\.00\.00\s*\n\s*(Военные)\s*\n\s*(науки)')


def pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit('pypdf kerak: pip install pypdf')
    reader = PdfReader(path)
    chunks = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            body = page.extract_text() or ''
        except Exception:                       # buzilgan sahifa butun ishni to'xtatmasin
            body = ''
        chunks.append('\n===== PAGE %d =====\n%s' % (index, body))
    return '\n'.join(chunks)


def page_index(text):
    return [(m.start(), int(m.group(1)))
            for m in re.finditer(r'===== PAGE (\d+) =====', text)]


def page_of(pages, offset):
    current = None
    for start, number in pages:
        if start <= offset:
            current = number
        else:
            break
    return current


def is_cyrillic(name):
    return bool(CYRILLIC.search(name)) and not LATIN.search(name)


def clean_name(raw):
    name = ' '.join(raw.split())
    name = re.split(r'\s*\d{2}\.\d{2}\.\d{2}', name)[0]
    return name.strip(' -–—|')


def collect_groups(text, pages):
    """{code: [{'name', 'page', 'offset'}, ...]} — barcha til variantlari."""
    found = {}
    for match in GROUP_RE.finditer(text):
        code = match.group(1)
        if code.endswith('.00.00'):
            continue
        name = clean_name(match.group(2))
        if not name:
            continue
        entries = found.setdefault(code, [])
        for existing in entries:
            # Jadval ustuni nomni kesib qo'yishi mumkin — eng uzunini saqlaymiz.
            if existing['name'] == name or name in existing['name']:
                break
            if existing['name'] in name:
                existing.update(name=name, page=page_of(pages, match.start()))
                break
        else:
            entries.append({'name': name, 'page': page_of(pages, match.start()),
                            'offset': match.start()})
    return found


def build_records(text, verified_at):
    pages = page_index(text)
    records = []

    branch = BRANCH_21_UZ.search(text)
    branch_ru = BRANCH_21_RU.search(text)
    if branch:
        records.append({
            'code': '21.00.00',
            'name_uz_cyrillic': '%s %s' % (branch.group(1), branch.group(2)),
            'name_ru': ('%s %s' % (branch_ru.group(1), branch_ru.group(2))
                        if branch_ru else None),
            'parent_code': None,
            'level': 1,
            'source_page': page_of(pages, branch.start()),
        })

    for code, entries in sorted(collect_groups(text, pages).items()):
        ordered = sorted(entries, key=lambda e: e['offset'])
        cyrillic = [e for e in ordered if is_cyrillic(e['name'])]
        if not cyrillic:
            print('DIQQAT: %s uchun kirill nom topilmadi — o\'tkazib yuborildi' % code)
            continue
        records.append({
            'code': code,
            'name_uz_cyrillic': cyrillic[0]['name'],
            'name_ru': cyrillic[1]['name'] if len(cyrillic) > 1 else None,
            'parent_code': code[:2] + '.00.00',
            'level': 2,
            'source_page': cyrillic[0]['page'],
        })

    out = []
    for item in records:
        latin, warnings = transliterate_name(item['name_uz_cyrillic'])
        record = {
            'code': item['code'],
            'name_uz_cyrillic': item['name_uz_cyrillic'],
            'name_uz': latin,
            'parent_code': item['parent_code'],
            'level': item['level'],
            'slug': field_slug(latin, item['code']),
            'review_status': 'official_source',
            'transliteration_warnings': warnings,
            'source_name': '%s, %d-bet' % (SOURCE_NAME, item['source_page']),
            'source_url': SOURCE_URL,
            'source_page': item['source_page'],
            'source_year': SOURCE_YEAR,
            'source_sha256': SOURCE_SHA256,
            'source_updated_at': verified_at,
        }
        if item['name_ru']:
            record['name_ru'] = item['name_ru']
        out.append(record)

    out.sort(key=lambda r: (r['level'], r['code']))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--output',
                        default='data/oak/normalized/oak_specialty_groups.json')
    parser.add_argument('--verified-at', default='2026-08-07')
    args = parser.parse_args(argv)

    if not os.path.isfile(args.pdf):
        return 'PDF topilmadi: %s' % args.pdf

    import hashlib
    with io.open(args.pdf, 'rb') as fh:
        actual = hashlib.sha256(fh.read()).hexdigest()
    if actual != SOURCE_SHA256:
        print('OGOHLANTIRISH: PDF hash kutilganidan farq qiladi.')
        print('  kutilgan: %s' % SOURCE_SHA256)
        print('  olingan : %s' % actual)
        print('Manba yangilangan bo\'lishi mumkin — natijani QO\'LDA tekshiring.')

    records = build_records(pdf_text(args.pdf), args.verified_at)
    doc = {
        'metadata': {
            'source_name': SOURCE_NAME,
            'source_url': SOURCE_URL,
            'source_sha256': SOURCE_SHA256,
            'source_year': SOURCE_YEAR,
            'source_verified_at': args.verified_at,
            'source_language': 'uz-Cyrl',
            'record_count': len(records),
            'note': ("OAK klassifikatsiyasining rasmiy 1- va 2-daraja yozuvlari. "
                     "Nomlar rasmiy PDF dan AYNAN ko'chirilgan (o'zbek kirill "
                     "bo'limi); lotin varianti utils/oak_translit.py orqali "
                     "hosil qilingan va rasmiy tarjima emas."),
        },
        'records': records,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with io.open(args.output, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write('\n')

    print('%d ta yozuv -> %s' % (len(records), args.output))
    for record in records:
        print('  %s L%d %s' % (record['code'], record['level'], record['name_uz']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
