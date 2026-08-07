# -*- coding: utf-8 -*-
"""OAK ixtisosliklari manbasini parsing, normalizatsiya va validatsiya qiladi.

Manba — OAK.UZ dan qo'lda yig'ilgan Markdown fayl. U RASMIY DATASET EMAS:
ichida kesilgan nomlar, yopilmagan qo'shtirnoqlar, begona jumlalar va bitta
buzilgan kod bor. Shuning uchun bu script uni "raw manba" sifatida qabul
qiladi va har bir yozuvga ishonchlilik holati beradi:

    clean            — kod va nom aniq, texnik buzilish yo'q
    normalized       — faqat texnik format tozalandi (mazmun o'zgarmadi)
    review_required  — odam ko'rishi shart (kesilgan, begona matn, ziddiyat)
    rejected         — kod yoki nomni ishonchli ajratib bo'lmaydi

Production seed FAQAT clean + normalized (+ keyin qo'lda tasdiqlangan)
yozuvlarni oladi. review_required avtomatik import qilinmaydi.

BU SCRIPT HECH QACHON NOM O'YLAB TOPMAYDI. Kesilgan nom to'ldirilmaydi,
taxminiy rasmiy nom qo'yilmaydi, AI bilan "to'g'rilanmaydi". Asl matn har
doim raw_name da saqlanadi.

Ishlatilishi:
    python scripts/parse_oak_specialties.py \
        --input data/oak/source/oak_barcha_shifrlar.md \
        --output-dir data/oak

    # boshqa manbani tekshirish (yozmasdan)
    python scripts/parse_oak_specialties.py --input <fayl> --dry-run

Deterministik: bir xil input har doim bir xil output beradi (lug'atlar
tartiblangan, vaqt tamg'asi faqat metadata'da va --parsed-at bilan qotiriladi).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from utils.oak_translit import transliterate_name  # noqa: E402
from utils.scientific_fields import (field_slug, level_from_code,  # noqa: E402
                                     normalize_code, parent_code)

PARSER_VERSION = '1'

# Yozuv qatori: KOD  –  NOM (ajratgich — en dash, atrofida bo'sh joy).
# Boshqa ajratgichlar (—, -, :) ham qabul qilinadi, chunki manba qo'lda
# yig'ilgan va keyingi revizyada o'zgarishi mumkin.
RECORD_RE = re.compile(r'^\s*(\d{2}\.\d{2}\.\d{2})\s*[–—:-]\s*(.*?)\s*$')
CODE_ONLY_RE = re.compile(r'^\d{2}\.\d{2}\.\d{2}$')

# Yozuv EMAS deb hisoblanadigan sarlavha/separator qatorlari.
HEADER_RE = re.compile(r'^\s*(?:=+|-{3,}|#.*|Jami\s*:.*|OAK\.UZ.*)\s*$', re.I)

# Nomni to'liq o'rab turgan juft qo'shtirnoqlar.
QUOTE_PAIRS = (('“', '”'), ('«', '»'), ('"', '"'),
               ('‘', '’'))
OPENING_QUOTES = '“«'
CLOSING_QUOTES = '”»'

# Nomga aralashib qolgan kontekst iboralari (§7).
CONTEXT_SUFFIX_RE = re.compile(r'(ихтисослиги(\s+бўйича)?|бўйича)\s*$', re.I)
FOREIGN_TEXT_RE = re.compile(r'диссертация\s+мавзуси|рўйхатга\s+олинган|рақам\s*:', re.I)

# ── Qo'lda tekshirilgan shubhali yozuvlar ───────────────────────────────────
# Bu ro'yxat ATAYIN manbaning AYNAN shu revizyasiga (SHA-256) bog'langan:
# boshqa fayl berilsa u qo'llanmaydi va script buni aytadi. Sabab — bu
# yozuvlarda hech qanday texnik signal yo'q (qo'shtirnoq balansli, begona
# matn yo'q), ular faqat mazmunan tugallanmagan. Umumiy morfologik qoida
# bilan ularni ajratib bo'lmaydi: "Алгебра" ham, "Композицион" ham bitta
# so'z, lekin birinchisi to'liq nom, ikkinchisi sifat.
#
# Bu yerda NOM TAKLIF QILINMAYDI — faqat "odam ko'rsin" deb belgilanadi.
SOURCE_SHA_FOR_CURATED = (
    '5e492b0fa56d5cde4414be718f66d1bfaca9b97f5dd7142107622574365c1792')
CURATED_SUSPECTS = {
    '01.02.02': 'generic plural noun ("Машиналар") — nom tugallanmaganga o\'xshaydi',
    '02.00.07': 'sifat ("Композицион") — ergashuvchi ot yo\'q',
    '02.00.19': 'sifat ("Камёб") — ergashuvchi ot yo\'q',
    '05.08.01': 'umumiy ot ("Мамлакат") — nom tugallanmaganga o\'xshaydi',
    '10.00.04': 'atoqli ot ("Европа") — soha oti yo\'q',
}


class ParseError(Exception):
    """Manbani ishonchli o'qib bo'lmadi — jarayon to'xtaydi."""


# ── Texnik normalizatsiya ───────────────────────────────────────────────────

def normalize_name(raw):
    """(name, notes). FAQAT texnik tozalash — mazmun o'zgartirilmaydi (§6)."""
    notes = []
    name = raw

    if ' ' in name:
        name = name.replace(' ', ' ')
        notes.append('nbsp_replaced')

    if name.strip().startswith('**') or name.strip().endswith('**'):
        name = name.strip().strip('*')
        notes.append('markdown_bold_removed')

    stripped = name.strip()
    if stripped != name:
        name = stripped
        notes.append('outer_whitespace_trimmed')

    collapsed = re.sub(r'\s{2,}', ' ', name)
    if collapsed != name:
        name = collapsed
        notes.append('inner_whitespace_collapsed')

    # Nomni TO'LIQ o'rab turgan juft qo'shtirnoq. Yopilmagan qo'shtirnoq
    # ATAYIN tegilmaydi — u kesilgan nom belgisi va review'ga chiqadi.
    for opening, closing in QUOTE_PAIRS:
        if len(name) > 1 and name.startswith(opening) and name.endswith(closing):
            inner = name[1:-1]
            if opening not in inner and closing not in inner:
                name = inner.strip()
                notes.append('outer_quotes_removed')
                break

    # Tire va apostrof kodlashini birxillashtirish.
    unified = name.replace('–', '-').replace('—', '-')
    if unified != name:
        name = unified
        notes.append('dash_unified')
    unified = re.sub('[‘’ʻʼ´`]', "'", name)
    if unified != name:
        name = unified
        notes.append('apostrophe_unified')

    # Oxiridagi ortiqcha nuqta (ehtiyotkorlik bilan: faqat bitta nuqta,
    # qisqartma nuqtasi emas — undan oldin kamida 3 harfli so'z turibdi).
    if re.search(r'(?<=\w{3})\.$', name):
        name = name[:-1].rstrip()
        notes.append('trailing_period_removed')

    return name, notes


def quote_balance_issue(text):
    """Ochilgan, lekin yopilmagan qo'shtirnoq bormi."""
    if text.count('“') != text.count('”'):
        return True
    if text.count('«') != text.count('»'):
        return True
    if text.count('"') % 2:
        return True
    return False


# ── Shubha aniqlash ─────────────────────────────────────────────────────────

def review_reasons_for(code, raw_name, name, source_sha, name_index, code_index):
    """Yozuvni review'ga chiqaradigan sabablar ro'yxati (bo'sh = shubha yo'q)."""
    reasons = []

    if not name:
        reasons.append('empty_name')
        return reasons

    if quote_balance_issue(raw_name):
        # Ochilgan qo'shtirnoq yopilmagan — manba matni kesilgan.
        reasons.append('unbalanced_quotes_possible_truncation')

    if FOREIGN_TEXT_RE.search(name):
        reasons.append('foreign_text_in_name')

    if name.rstrip().endswith(':'):
        reasons.append('name_ends_with_colon')

    if CONTEXT_SUFFIX_RE.search(name):
        reasons.append('contains_specialty_context_suffix')

    if '**' in raw_name:
        reasons.append('markdown_markup_in_source')

    if source_sha == SOURCE_SHA_FOR_CURATED and code in CURATED_SUSPECTS:
        reasons.append('manually_flagged_possible_truncation')

    # Kod shakli bu manbaga mos kelmasa (fayl ixtisosliklar ro'yxati bo'lsa
    # ham ichida guruh/tarmoq kodi chiqib qolsa) — bu jimgina mavjud tarmoq
    # nomini almashtirib yuborishi mumkin.
    level = level_from_code(code)
    if level is not None and level < 3:
        reasons.append('code_level_conflicts_with_specialty_list')

    if len(code_index.get(code, ())) > 1:
        reasons.append('duplicate_code')

    if len(name_index.get(name, ())) > 1:
        others = [c for c in name_index[name] if c != code]
        reasons.append('duplicate_name_with_%s' % ','.join(sorted(others)))

    return reasons


# ── Parsing ─────────────────────────────────────────────────────────────────

def read_source(path):
    with io.open(path, 'rb') as fh:
        data = fh.read()
    sha = hashlib.sha256(data).hexdigest()
    text = data.decode('utf-8')
    return text, sha


def declared_total(text):
    """Manbadagi "Jami: N ta" qatoridan kutilgan yozuvlar soni."""
    match = re.search(r'Jami\s*:\s*(\d+)\s*ta', text, re.I)
    return int(match.group(1)) if match else None


def parse_records(text, source_name, source_sha):
    """(records, unparsed_lines). Har bir record — raw + normalized."""
    raw_records = []
    unparsed = []

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or HEADER_RE.match(line):
            continue
        match = RECORD_RE.match(line)
        if not match:
            unparsed.append({'source_line': number, 'text': line.rstrip()})
            continue
        code = normalize_code(match.group(1))
        raw_name = match.group(2)
        if not code or not CODE_ONLY_RE.match(code):
            unparsed.append({'source_line': number, 'text': line.rstrip()})
            continue
        raw_records.append({'code': code, 'raw_name': raw_name,
                            'source_file': source_name, 'source_line': number})

    # Dublikat indekslari — sabablarni hisoblash uchun oldindan quriladi.
    code_index = collections.defaultdict(list)
    name_index = collections.defaultdict(list)
    normalized_names = {}
    for record in raw_records:
        code_index[record['code']].append(record['code'])
        name, _notes = normalize_name(record['raw_name'])
        normalized_names[id(record)] = name
        name_index[name].append(record['code'])

    records = []
    for record in raw_records:
        code = record['code']
        name, notes = normalize_name(record['raw_name'])
        reasons = review_reasons_for(code, record['raw_name'], name, source_sha,
                                     name_index, code_index)

        latin, translit_warnings = transliterate_name(name)
        if name and not latin.strip():
            reasons.append('empty_transliteration')

        if not name:
            status = 'rejected'
        elif reasons:
            status = 'review_required'
        elif notes:
            status = 'normalized'
        else:
            status = 'clean'

        entry = dict(record)
        entry.update({
            'name_uz_cyrillic': name,
            'name_uz': latin,
            'normalized_candidate': name,
            'parent_code': parent_code(code),
            'level': level_from_code(code),
            'status': status,
            'normalization_notes': notes,
            'review_reasons': sorted(set(reasons)),
            'transliteration_warnings': translit_warnings,
            'slug': field_slug(latin or name, code) if name else None,
            'source_sha256': source_sha,
        })
        if status == 'review_required' and code in CURATED_SUSPECTS:
            entry['curated_note'] = CURATED_SUSPECTS[code]
        records.append(entry)

    records.sort(key=lambda r: (r['code'], r['source_line']))
    return records, unparsed


# ── Hisobot ─────────────────────────────────────────────────────────────────

def build_report(records, unparsed, text, source_name, source_path, source_sha,
                 parsed_at, known_branch_codes):
    by_status = collections.Counter(r['status'] for r in records)
    reason_freq = collections.Counter()
    for record in records:
        for reason in record['review_reasons']:
            # Faqat dublikat-nom sababi ichida kod bor (u har bir yozuvda
            # boshqacha) — chastota uchun umumiy shaklga keltiriladi. Boshqa
            # sabablar o'z nomi bilan qoladi.
            if reason.startswith('duplicate_name_with_'):
                reason = 'duplicate_name_with_other_code'
            reason_freq[reason] += 1

    translit_warn_freq = collections.Counter()
    for record in records:
        for warning in record['transliteration_warnings']:
            translit_warn_freq[warning] += 1

    codes = [r['code'] for r in records]
    duplicate_codes = sorted(c for c, n in collections.Counter(codes).items() if n > 1)
    names = [r['name_uz_cyrillic'] for r in records if r['name_uz_cyrillic']]
    duplicate_names = sorted(n for n, c in collections.Counter(names).items() if c > 1)

    parent_codes = sorted({r['parent_code'] for r in records if r['parent_code']})
    known_codes = set(codes) | set(known_branch_codes)
    missing_parents = sorted(p for p in parent_codes if p not in known_codes)
    missing_parent_records = [r['code'] for r in records
                              if r['parent_code'] and r['parent_code'] not in known_codes]

    return {
        'source_file': source_name,
        'source_path': source_path,
        'source_sha256': source_sha,
        'source_record_count': declared_total(text),
        'parsed_at': parsed_at,
        'parser_version': PARSER_VERSION,

        'source_total': declared_total(text),
        'parsed_total': len(records),
        'clean_total': by_status.get('clean', 0),
        'normalized_total': by_status.get('normalized', 0),
        'review_required_total': by_status.get('review_required', 0),
        'rejected_total': by_status.get('rejected', 0),
        'duplicate_code_count': len(duplicate_codes),
        'duplicate_name_count': len(duplicate_names),
        'missing_parent_count': len(missing_parent_records),
        'unbalanced_quote_count': sum(
            1 for r in records if quote_balance_issue(r['raw_name'])),
        'possible_truncation_count': sum(
            1 for r in records
            if any(x in ('unbalanced_quotes_possible_truncation',
                         'manually_flagged_possible_truncation')
                   for x in r['review_reasons'])),
        'foreign_text_count': sum(
            1 for r in records if 'foreign_text_in_name' in r['review_reasons']),
        'transliteration_warning_count': sum(
            1 for r in records if r['transliteration_warnings']),

        'review_reason_frequency': dict(sorted(reason_freq.items())),
        'transliteration_warning_frequency': dict(sorted(translit_warn_freq.items())),
        'counts_by_code_prefix': dict(sorted(
            collections.Counter(c.split('.')[0] for c in codes).items())),
        'counts_by_level': dict(sorted(
            collections.Counter(r['level'] for r in records).items(),
            key=lambda kv: (kv[0] is None, kv[0]))),
        'first_code': codes[0] if codes else None,
        'last_code': codes[-1] if codes else None,
        'duplicate_codes': duplicate_codes,
        'duplicate_names': duplicate_names,
        'distinct_parent_codes': parent_codes,
        'missing_parent_codes': missing_parents,
        'missing_parent_records': sorted(missing_parent_records),
        'unparsed_lines': unparsed,
        'review_codes': sorted(r['code'] for r in records
                               if r['status'] == 'review_required'),
        'rejected_codes': sorted(r['code'] for r in records
                                 if r['status'] == 'rejected'),
    }


# ── Yozish ──────────────────────────────────────────────────────────────────

def _dump(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write('\n')


RAW_FIELDS = ('code', 'raw_name', 'source_file', 'source_line')
APPROVED_FIELDS = ('code', 'name_uz_cyrillic', 'name_uz', 'raw_name', 'parent_code',
                   'level', 'slug', 'status', 'normalization_notes',
                   'transliteration_warnings', 'source_file', 'source_line',
                   'source_sha256', 'review_status', 'reviewer', 'reviewed_at',
                   'review_note')
REVIEW_FIELDS = ('code', 'raw_name', 'normalized_candidate', 'name_uz', 'level',
                 'parent_code', 'status', 'review_reasons', 'curated_note',
                 'transliteration_warnings', 'source_file', 'source_line',
                 'source_sha256', 'approved_name_uz_cyrillic', 'reviewer',
                 'reviewed_at', 'review_note')


def _project(record, fields):
    return {k: record[k] for k in fields if k in record}


def load_existing_reviews(review_path):
    """Oldingi review faylidan QO'LDA TASDIQLANGAN yozuvlarni o'qiydi.

    Manba qayta parse qilinganda odam qilgan ish yo'qolmasligi kerak — aks
    holda har bir re-parse 34 ta yozuvni qaytadan ko'rib chiqishni talab
    qilardi. Tasdiqlangan deb faqat UCHALA maydon ham to'ldirilgan yozuv
    hisoblanadi: approved_name_uz_cyrillic, reviewer, reviewed_at.

    Qaytadi: {code: {'name': ..., 'reviewer': ..., 'reviewed_at': ...,
                     'note': ..., 'source_sha256': ...}}
    """
    if not os.path.isfile(review_path):
        return {}
    with io.open(review_path, encoding='utf-8') as fh:
        doc = json.load(fh)
    approved = {}
    for record in doc.get('records', []):
        name = (record.get('approved_name_uz_cyrillic') or '').strip()
        reviewer = (record.get('reviewer') or '').strip()
        reviewed_at = (record.get('reviewed_at') or '').strip()
        if not (name and reviewer and reviewed_at):
            continue
        approved[record['code']] = {
            'name': name, 'reviewer': reviewer, 'reviewed_at': reviewed_at,
            'note': record.get('review_note'),
            'source_sha256': record.get('source_sha256'),
        }
    return approved


def apply_reviews(records, reviews, source_sha):
    """Tasdiqlangan review yozuvlarini `manual_approved` holatiga o'tkazadi.

    Tasdiq manbaning AYNAN o'sha revizyasiga bog'langan: fayl o'zgargan
    bo'lsa (SHA boshqa) tasdiq avtomatik qo'llanmaydi va yozuv review'da
    qoladi — chunki asl matn o'zgargan bo'lishi mumkin.
    """
    applied, stale = [], []
    for record in records:
        review = reviews.get(record['code'])
        if not review or record['status'] != 'review_required':
            continue
        if review.get('source_sha256') and review['source_sha256'] != source_sha:
            stale.append(record['code'])
            continue
        latin, warnings = transliterate_name(review['name'])
        record.update({
            'name_uz_cyrillic': review['name'],
            'name_uz': latin,
            'normalized_candidate': review['name'],
            'transliteration_warnings': warnings,
            'slug': field_slug(latin or review['name'], record['code']),
            'status': 'manual_approved',
            'reviewer': review['reviewer'],
            'reviewed_at': review['reviewed_at'],
            'review_note': review.get('note'),
        })
        applied.append(record['code'])
    return applied, stale


def write_outputs(output_dir, records, report, metadata):
    raw_path = os.path.join(output_dir, 'raw', 'oak_specialties_raw.json')
    approved_path = os.path.join(output_dir, 'normalized',
                                 'oak_specialties_approved.json')
    review_path = os.path.join(output_dir, 'review',
                               'oak_specialties_review_required.json')
    rejected_path = os.path.join(output_dir, 'review',
                                 'oak_specialties_rejected.json')
    report_path = os.path.join(output_dir, 'reports',
                               'oak_specialties_validation.json')

    _dump(raw_path, {'metadata': metadata,
                     'records': [_project(r, RAW_FIELDS) for r in records]})

    approved = []
    for record in records:
        if record['status'] not in ('clean', 'normalized', 'manual_approved'):
            continue
        entry = _project(record, APPROVED_FIELDS)
        # Yozuv qanday tasdiqlangani ochiq yoziladi: avtomatik o'tdimi
        # (texnik buzilish yo'q edi) yoki odam ko'rib tasdiqladimi.
        if record['status'] == 'manual_approved':
            entry['review_status'] = 'manual_approved'
            entry['reviewer'] = record.get('reviewer')
            entry['reviewed_at'] = record.get('reviewed_at')
            entry['review_note'] = record.get('review_note')
        else:
            entry['review_status'] = 'auto_' + record['status']
        approved.append(entry)
    # Kalit ataylab `record_fields` — `fields` EMAS: seed skripti `fields` ni
    # yozuvlar ro'yxati deb o'qiydi (repozitoriy seed formati), ustun nomlari
    # ro'yxati deb emas.
    _dump(approved_path, {'metadata': metadata,
                          'record_fields': list(APPROVED_FIELDS),
                          'records': approved})

    review = []
    for record in records:
        # Tasdiqlangan yozuv ham review faylida QOLADI (tasdiq maydonlari
        # bilan) — aks holda keyingi re-parse uni qaytadan navbatga qo'yardi.
        if record['status'] not in ('review_required', 'manual_approved'):
            continue
        entry = _project(record, REVIEW_FIELDS)
        if record['status'] == 'manual_approved':
            entry['approved_name_uz_cyrillic'] = record['name_uz_cyrillic']
        # Review navbati uchun bo'sh maydonlar — ular qo'lda to'ldiriladi.
        entry.setdefault('approved_name_uz_cyrillic', None)
        entry.setdefault('reviewer', None)
        entry.setdefault('reviewed_at', None)
        entry.setdefault('review_note', None)
        review.append(entry)
    _dump(review_path, {'metadata': metadata, 'records': review})

    rejected = [_project(r, REVIEW_FIELDS) for r in records
                if r['status'] == 'rejected']
    _dump(rejected_path, {'metadata': metadata, 'records': rejected})

    _dump(report_path, report)
    return {'raw': raw_path, 'approved': approved_path, 'review': review_path,
            'rejected': rejected_path, 'report': report_path}


def known_branches():
    """Repozitoriyda allaqachon tasdiqlangan 22 ta fan tarmog'i shifri."""
    from app import SPECIALTY_NAMES
    return set(SPECIALTY_NAMES)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', default='data/oak/source/oak_barcha_shifrlar.md')
    parser.add_argument('--output-dir', default='data/oak')
    parser.add_argument('--dry-run', action='store_true',
                        help='hech nima yozmaydi, faqat hisobotni chiqaradi')
    parser.add_argument('--parsed-at', default=None,
                        help='ISO vaqt (deterministik output uchun)')
    parser.add_argument('--allow-count-mismatch', action='store_true',
                        help="manbadagi 'Jami: N ta' bilan mos kelmasa ham davom etadi")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        print('Manba fayl topilmadi: %s' % args.input, file=sys.stderr)
        return 2

    text, source_sha = read_source(args.input)
    source_name = os.path.basename(args.input)
    parsed_at = args.parsed_at or datetime.now(timezone.utc).isoformat()

    try:
        branch_codes = known_branches()
    except Exception:                       # app import DB'siz ham ishlaydi
        branch_codes = set()

    records, unparsed = parse_records(text, source_name, source_sha)

    review_path = os.path.join(args.output_dir, 'review',
                               'oak_specialties_review_required.json')
    reviews = load_existing_reviews(review_path)
    applied, stale = apply_reviews(records, reviews, source_sha)
    if applied:
        print("Qo'lda tasdiqlangan yozuvlar qabul qilindi: %d %s"
              % (len(applied), ', '.join(applied)))
    if stale:
        print("OGOHLANTIRISH: %d ta tasdiq manbaning boshqa revizyasiga tegishli "
              "va QO'LLANMADI: %s" % (len(stale), ', '.join(stale)))

    report = build_report(records, unparsed, text, source_name, args.input,
                          source_sha, parsed_at, branch_codes)
    report['manual_approved_total'] = sum(
        1 for r in records if r['status'] == 'manual_approved')
    report['manual_approved_codes'] = sorted(applied)
    report['stale_review_codes'] = sorted(stale)

    metadata = {
        'source_file': source_name,
        'source_path': args.input,
        'source_sha256': source_sha,
        'source_record_count': report['source_total'],
        'parsed_at': parsed_at,
        'parser_version': PARSER_VERSION,
    }

    print('Manba      : %s' % args.input)
    print('SHA-256    : %s' % source_sha)
    print("Jami (manba): %s" % report['source_total'])
    print('Parsed     : %d' % report['parsed_total'])
    print('  clean            : %d' % report['clean_total'])
    print('  normalized       : %d' % report['normalized_total'])
    print('  manual_approved  : %d' % report['manual_approved_total'])
    print('  review_required  : %d' % report['review_required_total'])
    print('  rejected         : %d' % report['rejected_total'])
    print('Dublikat kod   : %d %s' % (report['duplicate_code_count'],
                                      report['duplicate_codes'] or ''))
    print('Dublikat nom   : %d' % report['duplicate_name_count'])
    print('Yetishmayotgan parent: %d ta yozuv, %d ta kod'
          % (report['missing_parent_count'], len(report['missing_parent_codes'])))
    print('Translit warning     : %d' % report['transliteration_warning_count'])
    if unparsed:
        print('PARSE QILINMAGAN QATORLAR: %d' % len(unparsed))
        for item in unparsed[:10]:
            print('   %d: %r' % (item['source_line'], item['text']))

    expected = report['source_total']
    if expected is not None and expected != report['parsed_total']:
        message = ("XATO: manbada 'Jami: %s ta' deyilgan, lekin %d ta yozuv "
                   "parse qilindi." % (expected, report['parsed_total']))
        if not args.allow_count_mismatch:
            print(message, file=sys.stderr)
            print('Jarayon to\'xtatildi (--allow-count-mismatch bilan majburlash mumkin).',
                  file=sys.stderr)
            return 1
        print('OGOHLANTIRISH: ' + message)

    if args.dry_run:
        print('\nDRY-RUN — fayllar yozilmadi.')
        return 0

    paths = write_outputs(args.output_dir, records, report, metadata)
    print('\nYozildi:')
    for key in ('raw', 'approved', 'review', 'rejected', 'report'):
        print('  %-9s %s' % (key, paths[key]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
