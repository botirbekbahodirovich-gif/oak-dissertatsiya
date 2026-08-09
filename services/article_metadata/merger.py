# -*- coding: utf-8 -*-
"""Uch provayder natijasini bitta ko'rinishga yig'ish (Phase 2C.1).

Ikki qat'iy qoida bu modulni belgilaydi:

  1. **Ustuvorlik oshkora.** Har bir maydon uchun manba tartibi quyidagi
     `PRECEDENCE` jadvalida yozilgan. Jadvalda yo'q maydon merge qilinmaydi.
  2. **Farq yashirilmaydi.** Ikki provayder bir maydonga har xil qiymat bersa,
     yuqoriroq manba tanlanadi, LEKIN farq `FieldConflict` sifatida qaytadi
     (§16). Bu xato emas — adminga "tekshirib chiqing" signali.

Litsenziya ALOHIDA: u hech qachon avtomatik yakuniy qiymat bo'lmaydi (§28).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from services.article_metadata.models import (CROSSREF, OPENALEX, UNPAYWALL,
                                              AuthorCandidate, ExternalLocation,
                                              FieldConflict, LicenseCandidate,
                                              NormalizedArticleMetadata,
                                              TopicCandidate, Warning_)
from services.article_metadata.normalizer import name_key, parse_iso_date

# ── Ustuvorlik jadvali ──────────────────────────────────────────────────────
# maydon -> (provayderlar tartibi, provayder ma'lumotidagi kalit)
#
# Crossref bibliografiya uchun birlamchi (u DOI registratsiya agentligi).
# OpenAlex — identifikator, kontekst va o'zgaruvchan ko'rsatkichlar uchun.
# Unpaywall — ochiq kirish holati uchun.
PRECEDENCE = {
    'title':             ((CROSSREF, OPENALEX, UNPAYWALL), 'title'),
    'subtitle':          ((CROSSREF,), 'subtitle'),
    'abstract':          ((CROSSREF,), 'abstract'),
    'journal_name':      ((CROSSREF, OPENALEX, UNPAYWALL), 'journal_name'),
    'publisher':         ((CROSSREF, OPENALEX, UNPAYWALL), 'publisher'),
    'publication_year':  ((CROSSREF, OPENALEX, UNPAYWALL), 'publication_year'),
    'publication_date':  ((CROSSREF, OPENALEX, UNPAYWALL), 'publication_date'),
    'volume':            ((CROSSREF, OPENALEX), 'volume'),
    'issue':             ((CROSSREF, OPENALEX), 'issue'),
    'pages':             ((CROSSREF, OPENALEX), 'pages'),
    'language':          ((CROSSREF, OPENALEX), 'language'),
    'work_type':         ((CROSSREF, OPENALEX), 'work_type'),
    'primary_issn':      ((CROSSREF, OPENALEX), 'primary_issn'),
    'crossref_member_id': ((CROSSREF,), 'crossref_member_id'),
    'openalex_id':       ((OPENALEX,), 'openalex_id'),
    'is_retracted':      ((OPENALEX,), 'is_retracted'),
    'is_oa':             ((UNPAYWALL, OPENALEX), 'is_oa'),
    'oa_status':         ((UNPAYWALL, OPENALEX), 'oa_status'),
    'journal_is_oa':     ((UNPAYWALL,), 'journal_is_oa'),
    'journal_is_in_doaj': ((UNPAYWALL,), 'journal_is_in_doaj'),
}

# Farqi ko'rsatiladigan maydonlar. `openalex_id` kabi bitta manbadan
# keladiganlar bu yerda yo'q — ularda konflikt tushunchasi yo'q.
CONFLICT_FIELDS = {
    'title': 'Maqola nomi',
    'journal_name': 'Jurnal nomi',
    # `publisher` ATAYIN yo'q: u bizning jadvalimizda saqlanmaydi va manbalar
    # deyarli har doim uni boshqacha yozadi ("Public Library of Science" va
    # "Public Library of Science (PLoS)"). Bunday farqni ogohlantirish
    # sifatida ko'rsatish haqiqiy farqlarni shovqin ichida ko'mib yuborardi.
    'publication_year': 'Nashr yili',
    'publication_date': 'Nashr sanasi',
    'volume': 'Jild',
    'issue': 'Son',
    'pages': 'Betlar',
    'oa_status': 'Ochiq kirish holati',
}

# Matnli maydonlarni solishtirishda registr va tinish belgilari e'tiborga
# olinmaydi: "Journal of A" va "JOURNAL OF A" farq emas.
_TEXT_FIELDS = {'title', 'journal_name', 'publisher', 'volume', 'issue', 'pages',
                'oa_status'}


def merge(results):
    """(NormalizedArticleMetadata, conflicts, warnings).

    `results` — provayder nomi -> `ProviderResult`. Ishlamagan provayderlar
    shunchaki e'tiborga olinmaydi: qolganlari bilan qisman natija chiqadi (§4).
    """
    data = {name: result.data for name, result in results.items()
            if result.ok and result.data}

    metadata = NormalizedArticleMetadata(doi='')
    conflicts = []
    warnings = []

    for field, (order, key) in PRECEDENCE.items():
        chosen, chosen_source, alternatives = _pick(data, order, key)
        if chosen is None:
            continue
        setattr(metadata, field, _coerce(field, chosen))
        metadata.source_summary[field] = chosen_source
        if field in CONFLICT_FIELDS and alternatives:
            conflicts.append(FieldConflict(
                field=field, label=CONFLICT_FIELDS[field],
                preferred=_coerce(field, chosen), preferred_source=chosen_source,
                alternatives=[{'source': source, 'value': _coerce(field, value)}
                              for source, value in alternatives]))

    metadata.doi = _merge_doi(data)
    metadata.issn = _merge_issn(data)
    metadata.external_url = _merge_external_url(data, metadata)
    (metadata.authors, metadata.unmatched_authors,
     author_warnings) = merge_authors(data)
    if metadata.authors:
        metadata.source_summary['authors'] = _author_sources(metadata.authors)
    warnings.extend(author_warnings)

    metadata.license_candidates, license_conflict = merge_licenses(data)
    if metadata.license_candidates:
        metadata.source_summary['license_candidates'] = '+'.join(
            sorted({c.source for c in metadata.license_candidates}))
    if license_conflict is not None:
        conflicts.append(license_conflict)
        warnings.append(Warning_(
            code='license_conflict',
            message='Manbalar litsenziyani turlicha ko‘rsatmoqda. Yakuniy '
                    'litsenziyani o‘zingiz tanlang — avtomatik tanlanmadi.',
            severity='notice'))

    metadata.external_locations = merge_locations(data)
    metadata.topics = _topics(data)
    metadata.keywords = _keywords(data)

    _apply_citation_count(metadata, data, results)
    warnings.extend(_semantic_warnings(metadata, data))

    return metadata, conflicts, warnings


# ── Maydon tanlash ──────────────────────────────────────────────────────────

def _pick(data, order, key):
    """(qiymat, manba, [(manba, muqobil qiymat)]) — ustuvorlik bo'yicha."""
    found = []
    for provider in order:
        value = (data.get(provider) or {}).get(key)
        if value is None or value == '' or value == []:
            continue
        found.append((provider, value))
    if not found:
        return None, None, []
    chosen_source, chosen = found[0]
    alternatives = [(source, value) for source, value in found[1:]
                    if not _same(key, chosen, value)]
    return chosen, chosen_source, alternatives


def _same(key, left, right):
    if key in _TEXT_FIELDS:
        return name_key(left) == name_key(right)
    return left == right


def _coerce(field, value):
    """Sana maydonlari `date` bo'lib chiqadi (provayderlar ISO matn beradi)."""
    if field == 'publication_date' and isinstance(value, str):
        return parse_iso_date(value)
    return value


def _merge_doi(data):
    """DOI — Crossref kanonik shakli, aks holda birinchi topilgan."""
    for provider in (CROSSREF, OPENALEX, UNPAYWALL):
        value = (data.get(provider) or {}).get('doi')
        if value:
            return str(value).strip().lower()
    return ''


def _merge_issn(data):
    out = []
    for provider in (CROSSREF, OPENALEX):
        for value in ((data.get(provider) or {}).get('issn') or []):
            if value not in out:
                out.append(value)
    return out


def _merge_external_url(data, metadata):
    """Jurnal sahifasi. Crossref `URL` — DOI resolveri, u eng barqarori."""
    crossref_url = (data.get(CROSSREF) or {}).get('external_url')
    if crossref_url:
        return crossref_url
    landing = (data.get(OPENALEX) or {}).get('landing_page_url')
    if landing:
        metadata.source_summary['external_url'] = OPENALEX
        return landing
    return None


# ── Mualliflar ──────────────────────────────────────────────────────────────

def merge_authors(data):
    """(mualliflar, moslanmaganlar, ogohlantirishlar).

    Moslashtirish FAQAT ikki asosda (§17):
      1. ORCID aynan teng — eng ishonchli;
      2. normallashtirilgan TO'LIQ ism aynan teng.

    Boshqa hech qanday o'xshashlik ishlatilmaydi. "J. Smith" va "John Smith"
    BIRLASHTIRILMAYDI — ular boshqa odam bo'lishi mumkin.

    Moslanmagan OpenAlex mualliflari ro'yxatga QO'SHILMAYDI, chunki bu
    haqiqiy hujjatlarda ro'yxatni ikki barobar shishirib yuboradi: OpenAlex
    o'rta ismni tashlab ketadi ("Heather A. Piwowar" -> "Heather Piwowar"),
    natijada bir odam ikki qator bo'lib qolardi. Ular alohida ro'yxatda
    adminga KO'RSATILADI — ma'lumot yo'qolmaydi, lekin bibliografik ro'yxat
    Crossref'niki bo'lib qoladi (§10).

    Crossref umuman muallif bermasa, OpenAlex ro'yxati asosiy bo'ladi.
    """
    warnings = []
    crossref_authors = (data.get(CROSSREF) or {}).get('authors') or []
    openalex_authors = (data.get(OPENALEX) or {}).get('authors') or []

    merged = []
    for entry in crossref_authors:
        merged.append(AuthorCandidate(
            name=entry.get('name') or '',
            given=entry.get('given'),
            family=entry.get('family'),
            orcid=entry.get('orcid'),
            affiliation=entry.get('affiliation'),
            is_corresponding=bool(entry.get('is_corresponding')),
            order=entry.get('order') or (len(merged) + 1),
            sources=[CROSSREF],
        ))

    by_orcid = {a.orcid: a for a in merged if a.orcid}
    by_name = {}
    for author in merged:
        key = name_key(author.name)
        # Bir xil ism ikki marta uchrasa, avtomatik moslashtirish xavfli —
        # kalitni bekor qilamiz va ikkalasi ham qo'lda tekshiriladi.
        by_name[key] = None if key in by_name else author

    unmatched = []
    for entry in openalex_authors:
        target = None
        basis = None
        orcid = entry.get('orcid')
        if orcid and orcid in by_orcid:
            target, basis = by_orcid[orcid], 'orcid'
        else:
            key = name_key(entry.get('name'))
            candidate = by_name.get(key)
            if candidate is not None:
                # ORCID'lar mavjud va QARAMA-QARSHI bo'lsa, bu bir odam
                # ekaniga ishonch yo'q — birlashtirmaymiz.
                if orcid and candidate.orcid and orcid != candidate.orcid:
                    target = None
                else:
                    target, basis = candidate, 'name'

        if target is None:
            candidate = AuthorCandidate(
                name=entry.get('name') or '',
                orcid=orcid,
                affiliation=entry.get('affiliation'),
                openalex_author_id=entry.get('openalex_author_id'),
                institutions=list(entry.get('institutions') or []),
                ror=[r for r in (entry.get('ror') or []) if r],
                is_corresponding=bool(entry.get('is_corresponding')),
                order=entry.get('order') or 0,
                sources=[OPENALEX],
            )
            if merged:
                unmatched.append(candidate)      # ro'yxatga QO'SHILMAYDI
            else:
                merged.append(candidate)         # Crossref muallif bermagan
            continue

        target.match_basis = basis
        if OPENALEX not in target.sources:
            target.sources.append(OPENALEX)
        target.orcid = target.orcid or orcid
        target.openalex_author_id = target.openalex_author_id or entry.get('openalex_author_id')
        target.affiliation = target.affiliation or entry.get('affiliation')
        if not target.institutions:
            target.institutions = list(entry.get('institutions') or [])
        if not target.ror:
            target.ror = [r for r in (entry.get('ror') or []) if r]
        target.is_corresponding = target.is_corresponding or bool(entry.get('is_corresponding'))

    if unmatched:
        warnings.append(Warning_(
            code='authors_unmatched',
            message='%d ta muallif OpenAlex‘da Crossref ro‘yxatiga aniq '
                    'moslanmadi (odatda o‘rta ism farqi). Ular ro‘yxatga '
                    'qo‘shilmadi — pastda alohida ko‘rsatilgan.' % len(unmatched),
            severity='info'))

    for index, author in enumerate(merged, start=1):
        author.order = index
    for index, author in enumerate(unmatched, start=1):
        author.order = index
    return merged, unmatched, warnings


def _author_sources(authors):
    sources = sorted({s for a in authors for s in a.sources})
    return '+'.join(sources)


# ── Litsenziya ──────────────────────────────────────────────────────────────

def merge_licenses(data):
    """(nomzodlar, konflikt_yoki_None). Yakuniy litsenziya TANLANMAYDI (§28)."""
    candidates = []
    for entry in ((data.get(CROSSREF) or {}).get('licenses') or []):
        candidates.append(LicenseCandidate(
            source=CROSSREF,
            license_type=entry.get('license_type'),
            url=entry.get('url'),
            label=entry.get('label'),
            applies_to=entry.get('applies_to')))
    unpaywall_license = (data.get(UNPAYWALL) or {}).get('license')
    if unpaywall_license:
        candidates.append(LicenseCandidate(
            source=UNPAYWALL,
            license_type=unpaywall_license.get('license_type'),
            url=unpaywall_license.get('url'),
            label=unpaywall_license.get('label'),
            applies_to=unpaywall_license.get('applies_to')))

    # Konflikt: turli manbalar bir-biriga mos kelmaydigan TANIB OLINGAN
    # litsenziya turini beradi. Tanib olinmaganlari (None) konflikt yaratmaydi
    # — ular baribir admin ko'rigiga tushadi.
    by_source = {}
    for candidate in candidates:
        if candidate.license_type and candidate.source not in by_source:
            by_source[candidate.source] = candidate
    distinct = {c.license_type for c in by_source.values()}
    if len(distinct) < 2:
        return candidates, None

    order = [CROSSREF, UNPAYWALL]
    ordered = sorted(by_source.values(), key=lambda c: order.index(c.source))
    preferred = ordered[0]
    return candidates, FieldConflict(
        field='license_type', label='Litsenziya',
        preferred=preferred.license_type, preferred_source=preferred.source,
        alternatives=[{'source': c.source, 'value': c.license_type}
                      for c in ordered[1:]])


# ── Ochiq nusxalar, mavzular ────────────────────────────────────────────────

def merge_locations(data):
    """Ochiq kirish nusxalari. `pdf_url` — faqat nomzod, yuklab olinmaydi (§24)."""
    locations = []
    seen = set()

    def add(entry, source, is_best):
        if not entry:
            return
        landing = entry.get('landing_page_url')
        pdf = entry.get('pdf_url')
        key = (landing, pdf)
        if key in seen or not (landing or pdf):
            return
        seen.add(key)
        locations.append(ExternalLocation(
            source=source, landing_page_url=landing, pdf_url=pdf,
            host_type=entry.get('host_type'), version=entry.get('version'),
            license=entry.get('license'), is_best=is_best))

    for entry in ((data.get(UNPAYWALL) or {}).get('locations') or []):
        add(entry, UNPAYWALL, bool(entry.get('is_best')))
    add((data.get(OPENALEX) or {}).get('best_oa_location'), OPENALEX, False)
    return locations


def _topics(data):
    return [TopicCandidate(name=t['name'], source=OPENALEX, score=t.get('score'),
                           is_primary=bool(t.get('is_primary')))
            for t in ((data.get(OPENALEX) or {}).get('topics') or [])]


def _keywords(data):
    return [TopicCandidate(name=k['name'], source=OPENALEX, score=k.get('score'))
            for k in ((data.get(OPENALEX) or {}).get('keywords') or [])]


# ── Iqtibos soni va semantik ogohlantirishlar ───────────────────────────────

def _apply_citation_count(metadata, data, results):
    """Iqtibos soni — DOIMIY bibliografik fakt EMAS (§27).

    Shuning uchun u har doim manba va tekshirilgan sana bilan birga yuradi;
    sanasiz saqlash uni "haqiqat" ga aylantirib qo'yardi.
    """
    count = (data.get(OPENALEX) or {}).get('cited_by_count')
    if count is None:
        return
    metadata.citation_count = count
    metadata.citation_count_source = OPENALEX
    result = results.get(OPENALEX)
    metadata.citation_count_checked_at = (
        result.fetched_at if result and result.fetched_at
        else datetime.now(timezone.utc))
    metadata.source_summary['citation_count'] = OPENALEX


def _semantic_warnings(metadata, data):
    """Adminni to'xtatib turadigan signallar."""
    warnings = []

    if metadata.is_retracted:
        warnings.append(Warning_(
            code='retracted',
            message='OpenAlex bu maqolani CHAQIRIB OLINGAN (retracted) deb '
                    'belgilagan. Nashr holatini "Nashr etilgan" qilishdan '
                    'oldin tekshiruvdan o‘tkazing.',
            severity='notice'))

    for entry in ((data.get(CROSSREF) or {}).get('update_to') or []):
        if (entry.get('type') or '').lower() in ('retraction', 'withdrawal',
                                                 'removal'):
            warnings.append(Warning_(
                code='crossref_update_notice',
                message='Crossref bu DOI uchun "%s" yozuvini ko‘rsatmoqda '
                        '(%s).' % (entry.get('type'), entry.get('doi') or '—'),
                severity='notice'))
            break

    if metadata.oa_status:
        warnings.append(Warning_(
            code='oa_status_is_not_access_type',
            message='Ochiq kirish holati "%s" — bu TASHQI signal. Saytdagi '
                    '"Kirish turi" huquqiy tushuncha va avtomatik '
                    'to‘ldirilmaydi.' % metadata.oa_status,
            severity='info'))

    for location in metadata.external_locations:
        if location.pdf_url:
            warnings.append(Warning_(
                code='pdf_candidate_only',
                message='Tashqi PDF havolasi topildi. U NOMZOD sifatida '
                        'ko‘rsatiladi — fayl avtomatik yuklanmaydi va '
                        'saqlanmaydi.',
                severity='info'))
            break

    if (data.get(CROSSREF) or {}).get('is_paratext') or \
            (data.get(OPENALEX) or {}).get('is_paratext') or \
            (data.get(UNPAYWALL) or {}).get('is_paratext'):
        warnings.append(Warning_(
            code='paratext',
            message='Manbalar bu yozuvni maqola emas, nashr yordamchi matni '
                    '(mundarija, muqova) deb belgilagan.',
            severity='notice'))

    if metadata.work_type and metadata.work_type in ('posted-content', 'preprint'):
        warnings.append(Warning_(
            code='preprint',
            message='Yozuv turi "%s" — preprint bo‘lishi mumkin. "Maqola '
                    'versiyasi" maydonini tekshiring.' % metadata.work_type,
            severity='info'))

    return warnings
