# -*- coding: utf-8 -*-
"""Ilmiy metadata provayderlari uchun ichki tiplar (Phase 2C.1).

Provayderning XOM javobi hech qachon UI'ga ham, DB'ga ham to'g'ridan-to'g'ri
o'tmaydi: har bir klient shu yerdagi tiplarga aylantiradi, merger esa
`NormalizedArticleMetadata` beradi. Sabab — tashqi API sxemasi bizning
ma'lumot modelimizga aylanib qolmasligi kerak; API o'zgarsa faqat klient
o'zgaradi.

Bu modul Flask'ga ham, DB'ga ham bog'liq EMAS (§43: bir xil service keyin
CSV importer va agent tomonidan qayta ishlatiladi).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

# ── Provayderlar ────────────────────────────────────────────────────────────
CROSSREF = 'crossref'
OPENALEX = 'openalex'
UNPAYWALL = 'unpaywall'
PROVIDERS = (CROSSREF, OPENALEX, UNPAYWALL)

PROVIDER_LABELS = {
    CROSSREF: 'Crossref',
    OPENALEX: 'OpenAlex',
    UNPAYWALL: 'Unpaywall',
}

# ── Provayder holati ────────────────────────────────────────────────────────
# Bu qiymatlar UI'da ham, testlarda ham ishlatiladi — matn emas, kalit.
OK = 'ok'
NOT_FOUND = 'not_found'
TIMEOUT = 'timeout'
RATE_LIMITED = 'rate_limited'
UNAVAILABLE = 'unavailable'            # 5xx, DNS, ulanish uzildi
INVALID_RESPONSE = 'invalid_response'  # JSON emas yoki kutilmagan sxema
NOT_CONFIGURED = 'not_configured'      # kalit/email sozlanmagan

PROVIDER_STATUS_LABELS = {
    OK: 'olindi',
    NOT_FOUND: 'topilmadi',
    TIMEOUT: 'javob bermadi',
    RATE_LIMITED: 'so‘rovlar chegarasi',
    UNAVAILABLE: 'vaqtincha ishlamayapti',
    INVALID_RESPONSE: 'javobi tushunarsiz',
    NOT_CONFIGURED: 'sozlanmagan',
}

# ── Umumiy natija holati ────────────────────────────────────────────────────
LOOKUP_OK = 'ok'                       # hamma sozlangan provayder ishladi
LOOKUP_PARTIAL = 'partial_success'     # kamida bittasi ishladi
LOOKUP_NOT_FOUND = 'not_found'         # hech biri bu DOI'ni bilmaydi
LOOKUP_FAILED = 'failed'               # hech biri javob bera olmadi


@dataclass
class ProviderResult:
    """Bitta provayderning natijasi.

    `data` — provayderdan olingan, ALLAQACHON KESILGAN maydonlar to'plami
    (butun xom javob emas, §20). U keshga tushadi va normalizatorga boradi,
    lekin `public_dict()` ga KIRMAYDI — brauzerga provayder ma'lumotlari xom
    holda qaytarilmaydi (§15).
    """
    provider: str
    status: str
    fetched_at: datetime | None = None
    data: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    http_status: int | None = None
    retry_after: int | None = None
    elapsed_ms: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK

    def public_dict(self) -> dict:
        """UI'ga chiqadigan qism. `data` ATAYIN yo'q."""
        out = {
            'status': self.status,
            'label': PROVIDER_LABELS.get(self.provider, self.provider),
            'status_label': PROVIDER_STATUS_LABELS.get(self.status, self.status),
            'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None,
            'warnings': list(self.warnings),
        }
        if self.retry_after is not None:
            out['retry_after'] = self.retry_after
        return out


@dataclass
class AuthorCandidate:
    """Bitta muallif nomzodi.

    `sources` — bu yozuvni qaysi provayderlar tasdiqlagani. `match_basis`
    ikki provayderdagi mualliflar NEGA bitta odam deb qaralgani: faqat
    `orcid` yoki `name` (aniq mos tushgan normallashtirilgan to'liq ism).
    Fuzzy o'xshashlik bilan HECH QACHON birlashtirilmaydi (§17).
    """
    name: str
    given: str | None = None
    family: str | None = None
    orcid: str | None = None
    affiliation: str | None = None
    openalex_author_id: str | None = None
    institutions: list[str] = field(default_factory=list)
    ror: list[str] = field(default_factory=list)
    is_corresponding: bool = False
    order: int = 0
    sources: list[str] = field(default_factory=list)
    match_basis: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LicenseCandidate:
    """Litsenziya nomzodi — HECH QACHON avtomatik yakuniy litsenziya emas (§28).

    `license_type` — bizning `utils.article_rules.LICENSES` kalitiga
    o'girilgan qiymat yoki None (o'girib bo'lmadi). `url` esa provayder
    bergan asl havola: admin uni o'zi o'qiy oladi.
    """
    source: str
    license_type: str | None
    url: str | None = None
    label: str | None = None
    version: str | None = None
    applies_to: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExternalLocation:
    """Ochiq kirish nusxasi. `pdf_url` — FAQAT nomzod (§24).

    Bu qiymat hech qachon avtomatik yuklab olinmaydi va `pdf_path` ga
    yozilmaydi; u keyingi "Legal OA import" bosqichi uchun data kontrakti.
    """
    source: str
    landing_page_url: str | None = None
    pdf_url: str | None = None
    host_type: str | None = None
    version: str | None = None
    license: str | None = None
    is_best: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TopicCandidate:
    """OpenAlex mavzusi yoki kalit so'zi.

    Bu OAK fan yo'nalishi EMAS va unga avtomatik aylantirilmaydi (§25) —
    admin formasidagi OAK selektori o'z holicha qoladi.
    """
    name: str
    source: str
    score: float | None = None
    is_primary: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FieldConflict:
    """Bir maydon bo'yicha provayderlar rozi emas (§16).

    Bu XATO emas — "tekshirish tavsiya etiladi" ogohlantirishi.
    """
    field: str
    label: str
    preferred: object
    preferred_source: str
    alternatives: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'field': self.field,
            'label': self.label,
            'preferred': _jsonable(self.preferred),
            'preferred_source': self.preferred_source,
            'alternatives': [
                {'source': a['source'], 'value': _jsonable(a['value'])}
                for a in self.alternatives
            ],
        }


@dataclass
class Warning_:
    """Kodli ogohlantirish — UI matnni kalitdan tanlaydi, matnni parse qilmaydi."""
    code: str
    message: str
    severity: str = 'info'      # info | notice

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NormalizedArticleMetadata:
    """Uch provayderdan yig'ilgan yagona ko'rinish.

    Bu DB qatori EMAS: bu admin ko'rib chiqadigan NOMZOD. Hech bir maydon
    o'z-o'zidan `scientific_articles` ga yozilmaydi (§13).
    """
    doi: str
    title: str | None = None
    subtitle: str | None = None
    abstract: str | None = None
    journal_name: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    publication_date: date | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    language: str | None = None
    issn: list[str] = field(default_factory=list)
    primary_issn: str | None = None
    work_type: str | None = None
    external_url: str | None = None

    authors: list[AuthorCandidate] = field(default_factory=list)
    # OpenAlex bergan, lekin Crossref ro'yxatiga ANIQ moslanmagan mualliflar.
    # Ular asosiy ro'yxatga qo'shilmaydi (aks holda o'rta ism farqi tufayli
    # bir odam ikki qator bo'lardi), lekin yo'qolmaydi ham — admin ko'radi.
    unmatched_authors: list[AuthorCandidate] = field(default_factory=list)
    license_candidates: list[LicenseCandidate] = field(default_factory=list)
    external_locations: list[ExternalLocation] = field(default_factory=list)
    topics: list[TopicCandidate] = field(default_factory=list)
    keywords: list[TopicCandidate] = field(default_factory=list)

    # Ochiq kirish — TASHQI signal. Ichki `access_type` bilan 1:1 emas (§29).
    is_oa: bool | None = None
    oa_status: str | None = None
    journal_is_oa: bool | None = None
    journal_is_in_doaj: bool | None = None

    openalex_id: str | None = None
    crossref_member_id: str | None = None

    citation_count: int | None = None
    citation_count_source: str | None = None
    citation_count_checked_at: datetime | None = None

    is_retracted: bool | None = None

    # Qaysi maydon qaysi provayderdan olingani (§19).
    source_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'doi': self.doi,
            'title': self.title,
            'subtitle': self.subtitle,
            'abstract': self.abstract,
            'journal_name': self.journal_name,
            'publisher': self.publisher,
            'publication_year': self.publication_year,
            'publication_date': (self.publication_date.isoformat()
                                 if self.publication_date else None),
            'volume': self.volume,
            'issue': self.issue,
            'pages': self.pages,
            'language': self.language,
            'issn': list(self.issn),
            'primary_issn': self.primary_issn,
            'work_type': self.work_type,
            'external_url': self.external_url,
            'authors': [a.to_dict() for a in self.authors],
            'unmatched_authors': [a.to_dict() for a in self.unmatched_authors],
            'license_candidates': [c.to_dict() for c in self.license_candidates],
            'external_locations': [l.to_dict() for l in self.external_locations],
            'topics': [t.to_dict() for t in self.topics],
            'keywords': [k.to_dict() for k in self.keywords],
            'is_oa': self.is_oa,
            'oa_status': self.oa_status,
            'journal_is_oa': self.journal_is_oa,
            'journal_is_in_doaj': self.journal_is_in_doaj,
            'openalex_id': self.openalex_id,
            'crossref_member_id': self.crossref_member_id,
            'citation_count': self.citation_count,
            'citation_count_source': self.citation_count_source,
            'citation_count_checked_at': (self.citation_count_checked_at.isoformat()
                                          if self.citation_count_checked_at else None),
            'is_retracted': self.is_retracted,
            'source_summary': dict(self.source_summary),
        }

    def filled_field_count(self) -> int:
        """UI'dagi "N ta maydon topildi" uchun — bo'sh bo'lmagan maydonlar."""
        scalar = (self.title, self.abstract, self.journal_name, self.publisher,
                  self.publication_year, self.publication_date, self.volume,
                  self.issue, self.pages, self.language, self.primary_issn,
                  self.oa_status, self.openalex_id, self.citation_count,
                  self.external_url)
        count = sum(1 for value in scalar if value not in (None, ''))
        for collection in (self.authors, self.license_candidates, self.topics):
            if collection:
                count += 1
        return count


@dataclass
class MetadataLookupResult:
    """`fetch_article_metadata()` qaytaradigan yagona natija."""
    doi: str
    status: str
    metadata: NormalizedArticleMetadata
    providers: dict = field(default_factory=dict)      # provider -> ProviderResult
    conflicts: list[FieldConflict] = field(default_factory=list)
    warnings: list[Warning_] = field(default_factory=list)
    from_cache: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict:
        """Brauzerga yuboriladigan JSON (§15). Xom provayder ma'lumoti yo'q."""
        return {
            'doi': self.doi,
            'status': self.status,
            'metadata': self.metadata.to_dict(),
            'field_count': self.metadata.filled_field_count(),
            'providers': {name: result.public_dict()
                          for name, result in self.providers.items()},
            'conflicts': [c.to_dict() for c in self.conflicts],
            'warnings': [w.to_dict() for w in self.warnings],
        }


def _jsonable(value):
    """`date`/`datetime` ni JSON'ga mos qiymatga aylantiradi."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
