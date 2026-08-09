# -*- coding: utf-8 -*-
"""Discovery uchun DB'dan va Flask'dan mustaqil tiplar (Phase 2D.1).

`services/article_import/models.py` bilan bir xil naqsh: DB/tashqi API
qatorlari shu dataclass'larga aylantiriladi, shablon/JSON javob shulardan
o'qiydi. Bu modul psycopg2'ga ham, Flask'ga ham bog'liq EMAS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

__all__ = [
    'ArticleDiscoveryQuery', 'DiscoveryCandidate', 'DiscoveryPreviewResult',
    'DiscoveryValidationError',
    'NEW', 'EXISTING_ARTICLE', 'ALREADY_QUEUED', 'DUPLICATE_IN_RESULT',
    'INVALID_DOI', 'NO_DOI', 'CLASSIFICATION_LABELS',
]

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

    def canonical(self) -> dict:
        """Provenance'da saqlanadigan JSON — kalit/mailto/xom URL YO'Q (§20)."""
        return {
            'keyword': self.keyword,
            'from_date': self.from_date.isoformat(),
            'to_date': self.to_date.isoformat(),
            'oak_field_id': self.oak_field_id,
            'limit': self.limit,
        }


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
        }
