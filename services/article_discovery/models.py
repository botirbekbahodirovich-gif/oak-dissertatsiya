# -*- coding: utf-8 -*-
"""Discovery uchun DB'dan va Flask'dan mustaqil tiplar (Phase 2D.1/2D.2).

`services/article_import/models.py` bilan bir xil naqsh: DB/tashqi API
qatorlari shu dataclass'larga aylantiriladi, shablon/JSON javob shulardan
o'qiydi. Bu modul psycopg2'ga ham, Flask'ga ham bog'liq EMAS.

Phase 2D.2: provider-neutral qism shu yerga ko'chirildi — `DiscoverySearchResult`
avval `openalex.py` ichida edi (bitta provayder bo'lgani uchun yetarli edi),
endi `crossref.py` HAM AYNAN shu kontraktni qondiradi (§3 — "kichik
provider-neutral interfeys", plagin arxitekturasi emas: shunchaki bir xil
funksiya imzosi — `search(query, config) -> DiscoverySearchResult`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from services.article_metadata.models import CROSSREF, OPENALEX

__all__ = [
    'ArticleDiscoveryQuery', 'DiscoveryCandidate', 'DiscoveryPreviewResult',
    'DiscoverySearchResult', 'DiscoveryValidationError',
    'NEW', 'EXISTING_ARTICLE', 'ALREADY_QUEUED', 'DUPLICATE_IN_RESULT',
    'INVALID_DOI', 'NO_DOI', 'CLASSIFICATION_LABELS',
    'SUPPORTED_PROVIDERS', 'MULTI_PROVIDER', 'PROVIDER_LABELS',
]

# ── Provayderlar (§3, §16) ────────────────────────────────────────────────────
# `services.article_metadata.models`dagi bir xil 'openalex'/'crossref'
# qiymatlarini qayta ishlatadi — discovery uchun ikkinchi nom o'ylab
# topilmaydi (bitta provayder bitta kanonik nomga ega, modul bo'yicha).
SUPPORTED_PROVIDERS = (OPENALEX, CROSSREF)
# Provenance'da (`article_import_batches.source_provider`) ikkalasi ham
# ishtirok etganda ishlatiladigan qiymat (§16).
MULTI_PROVIDER = 'multi'
PROVIDER_LABELS = {OPENALEX: 'OpenAlex', CROSSREF: 'Crossref'}

# ── Nomzod klassifikatsiyasi (§13) ───────────────────────────────────────────
NEW = 'new'
EXISTING_ARTICLE = 'existing_article'
ALREADY_QUEUED = 'already_queued'
DUPLICATE_IN_RESULT = 'duplicate_in_result'
INVALID_DOI = 'invalid_doi'
NO_DOI = 'no_doi'

CLASSIFICATION_LABELS = {
    NEW: 'Yangi',
    EXISTING_ARTICLE: 'Bazadagi',
    ALREADY_QUEUED: 'Import navbatida',
    DUPLICATE_IN_RESULT: 'Takroriy',
    INVALID_DOI: "DOI noto'g'ri",
    NO_DOI: 'DOI yo\'q',
}

# Faqat shu klassifikatsiya navbatga yuborilishi mumkin (§27, §28).
ENQUEUABLE_CLASSIFICATIONS = frozenset({NEW})


class DiscoveryValidationError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan so'rov xatosi (texnik trace emas)."""

    def __init__(self, code, public_message):
        self.code = code
        self.public_message = public_message
        super().__init__(public_message)


@dataclass(frozen=True)
class ArticleDiscoveryQuery:
    keyword: str
    from_date: date
    to_date: date
    oak_field_id: int | None
    limit: int
    # Yangi maydon OXIRIDA, default bilan (§Backward compat) — Phase 2D.1
    # davridagi barcha mavjud `ArticleDiscoveryQuery(...)` chaqiruvlari
    # (testlar, keshdagi eski entry'lar) providers'ni bermaydi va shu bilan
    # ishlashda davom etadi: bitta provayder, OpenAlex — aynan 2D.1
    # xulq-atvori.
    providers: tuple = (OPENALEX,)

    def canonical(self) -> dict:
        """Provenance'da saqlanadigan JSON — kalit/mailto/xom URL YO'Q (§20)."""
        return {
            'keyword': self.keyword,
            'from_date': self.from_date.isoformat(),
            'to_date': self.to_date.isoformat(),
            'oak_field_id': self.oak_field_id,
            'limit': self.limit,
            'providers': list(self.providers),
        }

    @property
    def source_provider_label(self) -> str:
        """`article_import_batches.source_provider` uchun (§16): bitta
        provayder bo'lsa uning nomi, ikkitasi bo'lsa 'multi'."""
        return MULTI_PROVIDER if len(self.providers) > 1 else (
            self.providers[0] if self.providers else OPENALEX)


@dataclass
class DiscoveryCandidate:
    doi: str
    external_source_id: str | None = None
    title: str | None = None
    publication_date: date | None = None
    source_name: str | None = None
    is_retracted: bool = False
    topics: list = field(default_factory=list)

    classification: str = NEW
    existing_article_id: int | None = None
    existing_article_slug: str | None = None

    # Phase 2D.2 (§7, §16) — qaysi provayder(lar) shu DOI'ni topdi. Har bir
    # provider adapteri o'zining nomi bilan BITTA elementli ro'yxat qo'yadi
    # (masalan `['crossref']`); ko'p-provayderli dedup bosqichi (service.py)
    # bir xil DOI uchraganda shu ro'yxatlarni birlashtiradi.
    providers: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'doi': self.doi,
            'external_source_id': self.external_source_id,
            'title': self.title,
            'publication_date': self.publication_date.isoformat()
                                if self.publication_date else None,
            'source_name': self.source_name,
            'is_retracted': self.is_retracted,
            'topics': list(self.topics or []),
            'classification': self.classification,
            'classification_label': CLASSIFICATION_LABELS.get(
                self.classification, self.classification),
            'existing_article_id': self.existing_article_id,
            'existing_article_slug': self.existing_article_slug,
            'providers': list(self.providers or []),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DiscoveryCandidate':
        pub_date = data.get('publication_date')
        return cls(
            doi=data['doi'],
            external_source_id=data.get('external_source_id'),
            title=data.get('title'),
            publication_date=date.fromisoformat(pub_date) if pub_date else None,
            source_name=data.get('source_name'),
            is_retracted=bool(data.get('is_retracted')),
            topics=list(data.get('topics') or []),
            classification=data.get('classification', NEW),
            existing_article_id=data.get('existing_article_id'),
            existing_article_slug=data.get('existing_article_slug'),
            providers=list(data.get('providers') or []),
        )


@dataclass
class DiscoveryPreviewResult:
    status: str                        # 'ok' | provider status | 'not_configured'
    query: ArticleDiscoveryQuery | None = None
    candidates: list = field(default_factory=list)
    total_count: int = 0                # provayderdagi JAMI mos (§33)
    inspected_count: int = 0            # bu so'rovda haqiqatan ko'rilgan son
    excluded_no_doi_count: int = 0
    error_code: str | None = None
    preview_token: str | None = None
    # Phase 2D.2 (§13, §19) — har bir SO'RALGAN provayderning holati va xom
    # (dedup'dan OLDINGI) topilma soni: {'openalex': {'status': 'ok',
    # 'count': 12}, 'crossref': {'status': 'timeout', 'count': 0}}. Bitta
    # provayder muvaffaqiyatsiz bo'lsa ham, ikkinchisining natijasi
    # (`candidates`) YO'QOLMAYDI — shu dict adminga aniq "qaysi biri
    # ishlamadi"ni ko'rsatadi.
    provider_status: dict = field(default_factory=dict)

    def summary(self) -> dict:
        counts = {c: 0 for c in (NEW, EXISTING_ARTICLE, ALREADY_QUEUED,
                                 DUPLICATE_IN_RESULT)}
        for candidate in self.candidates:
            if candidate.classification in counts:
                counts[candidate.classification] += 1
        return {
            'total_count': self.total_count,
            'inspected_count': self.inspected_count,
            'new_count': counts[NEW],
            'existing_count': counts[EXISTING_ARTICLE],
            'already_queued_count': counts[ALREADY_QUEUED],
            'duplicate_in_result_count': counts[DUPLICATE_IN_RESULT],
            'no_doi_count': self.excluded_no_doi_count,
            'provider_status': dict(self.provider_status),
        }


class DiscoverySearchResult:
    """Bitta provayderning bitta qidiruv chaqiruvi natijasi.

    Har bir provider moduli (`openalex.py`, `crossref.py`) `search(query,
    config) -> DiscoverySearchResult` funksiyasini beradi — provider-neutral
    interfeys shu funksiya IMZOSINING O'ZI (§3): alohida abstrakt klass yoki
    plagin-reestr yo'q, chunki bitta qo'shimcha qatlam hech narsa
    qo'shmaydi — ikkita modul bor, ikkalasi ham shu shaklga mos keladi.
    """

    __slots__ = ('status', 'candidates', 'total_count', 'excluded_no_doi_count',
                'http_status', 'error_code')

    def __init__(self, status, candidates=None, total_count=0,
                excluded_no_doi_count=0, http_status=None, error_code=None):
        self.status = status
        self.candidates = candidates or []
        self.total_count = total_count
        self.excluded_no_doi_count = excluded_no_doi_count
        self.http_status = http_status
        self.error_code = error_code

    @property
    def ok(self):
        from services.article_metadata.models import OK
        return self.status == OK
