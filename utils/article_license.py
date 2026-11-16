# -*- coding: utf-8 -*-
"""Litsenziyani uch mustaqil manbadan kelishtirish (DB'siz, sof Python).

Manbalar (`metadata_candidate['license_candidates']`, Phase 2C.1 merger):

* **Crossref** `license[]` — noshir depozit qilgan yozuvlar. Faqat
  `content-version == 'vor'` (nashr etilgan yakuniy versiya) hisobga
  olinadi; `am`/`tdm`/`unspecified` e'tiborsiz qoladi.
* **OpenAlex** `best_oa_location.license`.
* **Unpaywall** `best_oa_location.license`.

Qaror qoidasi (qabul qilingan):

1. Manbalar TANIB OLINGAN, lekin bir-biriga zid litsenziya bersa (yoki bitta
   manba ichida ikki xil VOR litsenziya bo'lsa) — ``conflict``: litsenziya
   ``unknown`` qoladi va maqola qo'lda ko'rishga tushadi.
2. Ziddiyat yo'q bo'lsa va Crossref'da start sanasi nashr sanasidan KECH
   BO'LMAGAN VOR litsenziya bo'lsa — shu litsenziya (`crossref_vor`).
3. Aks holda kamida IKKI manba bir xil litsenziyani bersa — shu
   (`two_sources`).
4. Qolgan hamma holatda ``unknown`` (`insufficient`).

Tanib olinmagan matnli litsenziya (masalan `publisher-specific-oa`,
`other-oa`) "ochiq emas" signali sifatida ziddiyat hisoblanadi — u bilan
CC litsenziyani avtomatik qabul qilib bo'lmaydi.
"""
from __future__ import annotations

from datetime import date

from utils.article_rules import LICENSE_URLS, LICENSES, UNKNOWN_LICENSE

CROSSREF = 'crossref'
OPENALEX = 'openalex'
UNPAYWALL = 'unpaywall'
SOURCES = (CROSSREF, OPENALEX, UNPAYWALL)

RESOLVED = 'resolved'
CONFLICT = 'conflict'
INSUFFICIENT = 'insufficient'

BASIS_CROSSREF_VOR = 'crossref_vor'
BASIS_TWO_SOURCES = 'two_sources'

# Litsenziya deb hisoblanmaydigan bo'sh/umumiy belgilar.
_IGNORED_LABELS = frozenset({'', 'none', 'null', 'unknown', 'unspecified'})


def _parse_date(raw):
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _publication_date(metadata):
    """Nashr sanasi; faqat yil bo'lsa — o'sha yilning 31-dekabri (embargo
    tekshiruvi uchun eng yumshoq emas, eng XAVFSIZ chegara: start yil
    ichida bo'lsa ham "kech emas" deb qabul qilinadi)."""
    parsed = _parse_date(metadata.get('publication_date'))
    if parsed:
        return parsed
    year = metadata.get('publication_year')
    try:
        return date(int(year), 12, 31) if year else None
    except (TypeError, ValueError):
        return None


def _vote_value(candidate):
    """Nomzod -> ovoz qiymati: tanib olingan kalit, yoki `raw:<label>`
    (tanib olinmagan, lekin bo'sh bo'lmagan litsenziya), yoki None."""
    license_type = candidate.get('license_type')
    if license_type and license_type in LICENSES and license_type != UNKNOWN_LICENSE:
        return license_type
    label = (candidate.get('label') or candidate.get('url') or '').strip()
    if label.lower() in _IGNORED_LABELS:
        return None
    return 'raw:%s' % label[:80]


def resolve_license(metadata, today=None):
    """`metadata_candidate` (dict) -> natija dict.

    ``{'status', 'license_type', 'license_url', 'basis', 'votes',
       'evidence', 'reason'}``. `license_type` har doim to'ldirilgan
    (`unknown` bo'lishi mumkin). `evidence` — har bir manba nima bergani
    (permission_note va hisobot uchun).
    """
    today = today or date.today()
    candidates = [c for c in (metadata or {}).get('license_candidates') or []
                  if isinstance(c, dict)]
    pub_date = _publication_date(metadata or {})

    evidence = []
    votes = {}                 # source -> set(vote values)
    crossref_vor_ok = set()    # start <= nashr sanasi bo'lgan VOR litsenziyalar
    crossref_url = {}

    for candidate in candidates:
        source = candidate.get('source')
        if source not in SOURCES:
            continue
        applies_to = (candidate.get('applies_to') or '').strip().lower()
        start = _parse_date(candidate.get('start_date'))
        evidence.append({
            'source': source,
            'license_type': candidate.get('license_type'),
            'label': candidate.get('label'),
            'url': candidate.get('url'),
            'applies_to': candidate.get('applies_to'),
            'start_date': start.isoformat() if start else None,
        })
        if source == CROSSREF:
            if applies_to != 'vor':
                continue
            # Hali kuchga kirmagan (embargo davom etayotgan) litsenziya ovoz bermaydi.
            if start and start > today:
                continue
        value = _vote_value(candidate)
        if value is None:
            continue
        votes.setdefault(source, set()).add(value)
        if source == CROSSREF and not value.startswith('raw:'):
            crossref_url.setdefault(value, candidate.get('url'))
            if start and pub_date and start <= pub_date:
                crossref_vor_ok.add(value)

    result = {'status': INSUFFICIENT, 'license_type': UNKNOWN_LICENSE,
              'license_url': None, 'basis': None,
              'votes': {s: sorted(v) for s, v in votes.items()},
              'evidence': evidence, 'reason': None}

    all_values = set().union(*votes.values()) if votes else set()
    if len(all_values) > 1:
        result['status'] = CONFLICT
        result['reason'] = 'Manbalar har xil litsenziya bermoqda: %s' % ', '.join(
            '%s=%s' % (s, '/'.join(sorted(v))) for s, v in sorted(votes.items()))
        return result
    if not all_values:
        result['reason'] = 'Hech bir manba litsenziya bermadi.'
        return result

    (value,) = all_values
    if value.startswith('raw:'):
        result['reason'] = 'Litsenziya tanib olinmadi (%s).' % value[4:]
        return result

    if value in crossref_vor_ok:
        basis = BASIS_CROSSREF_VOR
    elif len(votes) >= 2:
        basis = BASIS_TWO_SOURCES
    else:
        result['reason'] = ('Faqat bitta manba (%s) va Crossref VOR tasdig\'i yo\'q.'
                            % next(iter(votes)))
        return result

    result.update(status=RESOLVED, license_type=value, basis=basis,
                  license_url=crossref_url.get(value) or LICENSE_URLS.get(value))
    return result


def evidence_note(resolution, extra_lines=()):
    """Odam o'qiydigan qisqa izoh (`permission_note` uchun)."""
    lines = []
    basis = {BASIS_CROSSREF_VOR: 'Crossref VOR (noshir depoziti)',
             BASIS_TWO_SOURCES: 'kamida ikki mustaqil manba'}.get(resolution.get('basis'))
    lines.append('Litsenziya: %s (%s).' % (
        LICENSES.get(resolution.get('license_type'), resolution.get('license_type')),
        basis or resolution.get('status')))
    for item in resolution.get('evidence') or []:
        lines.append('- %s: %s%s%s' % (
            item.get('source'),
            item.get('label') or item.get('license_type') or item.get('url') or '?',
            (' [%s]' % item['applies_to']) if item.get('applies_to') else '',
            (' start=%s' % item['start_date']) if item.get('start_date') else ''))
    lines.extend(extra_lines)
    return '\n'.join(lines)[:2000]
