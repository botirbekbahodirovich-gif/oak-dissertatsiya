# -*- coding: utf-8 -*-
"""Ochiq litsenziyali maqolalarning PDF'ini serverga olish (Flask'siz).

Faqat `scripts/fetch_open_pdfs.py` chaqiradi. Qoidalar (qabul qilingan):

* Faqat `ARTICLE_AUTO_PDF_LICENSES` (default: cc_by, cc_by_sa, cc_by_nd,
  cc0, public_domain) litsenziyali maqolalar. NC variantlari defaultda YO'Q.
* Faqat OpenAlex/Unpaywall bergan `pdf_url`. Joylashuvning o'z litsenziyasi
  bo'lsa — u maqola litsenziyasiga mos kelishi shart.
* Har bir fayl `security_uploads.validate_pdf_upload` dan o'tadi (MIME,
  `%PDF-`, `%%EOF`, hajm), SHA-256 dublikat tekshiruvi, atomik yozish
  `save_private_pdf` orqali (`storage/articles/YYYY/MM/<random>.pdf`).
* HTTP: timeout, 2 marta qayta urinish (faqat tarmoq/5xx), bitta domenga
  sekundiga ko'pi bilan 1 so'rov, User-Agent'da kontakt email.
  401/403/429, Cloudflare/captcha sahifasi — darhol o'tkazib yuboriladi,
  AYLANIB O'TISHGA URINILMAYDI.
* Diskda 5 GB dan kam joy qolsa — to'xtash.

Bu modul maqolani NASHR QILMAYDI va `verification_status`ga tegmaydi.
"""
from __future__ import annotations

import io
import ipaddress
import json
import shutil
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from security_uploads import (MAX_PDF_BYTES, UploadValidationError,
                              save_private_pdf, validate_pdf_upload)
from services.article_metadata.normalizer import map_license_label
from utils.article_rules import (LICENSE_URLS, ArticleValidationError,
                                 auto_pdf_licenses, validate_article_rights)

MIN_FREE_BYTES = 5 * 1024 ** 3
SKIP_STATUSES = frozenset({401, 403, 429})
RETRY_STATUSES = frozenset({500, 502, 503, 504})
MAX_REDIRECTS = 5

VERSION_MAP = {
    'publishedversion': 'version_of_record',
    'acceptedversion': 'accepted_manuscript',
    'submittedversion': 'preprint',
}

_CHALLENGE_MARKERS = (
    b'cf-browser-verification', b'challenge-platform', b'cf_chl_', b'just a moment',
    b'captcha', b'are you a robot', b'attention required', b'ddos-guard',
    b'access denied',
)


class FetchSkip(Exception):
    """Fayl olinmadi — `reason` hisobotga yoziladi (xato emas, qaror)."""

    def __init__(self, reason, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else '%s: %s' % (reason, detail))


class DiskSpaceLow(Exception):
    pass


@dataclass
class PdfFetchConfig:
    contact_email: str | None = None
    connect_timeout: float = 10.0
    read_timeout: float = 45.0
    retries: int = 2                       # birinchi urinishdan KEYIN
    backoff_seconds: float = 2.0
    per_host_interval: float = 1.0         # bitta domenga sekundiga 1 so'rov
    max_bytes: int = MAX_PDF_BYTES
    min_free_bytes: int = MIN_FREE_BYTES
    allowed_licenses: frozenset = field(default_factory=auto_pdf_licenses)

    def user_agent(self):
        if self.contact_email:
            return 'Olimlar.uz-OA-fetch/1.0 (+https://olimlar.uz; mailto:%s)' % self.contact_email
        return 'Olimlar.uz-OA-fetch/1.0 (+https://olimlar.uz)'


# Domen shu sabablardan biri bilan javob bersa, joriy ishga tushirishda shu
# domenga boshqa so'rov YUBORILMAYDI (aylanib o'tishga urinish yo'q).
HOST_BLOCK_REASONS = frozenset({'http_401', 'http_403', 'http_429', 'challenge_page'})


class HostThrottle:
    """Bitta domenga so'rovlar orasida kamida `interval` soniya; bizni rad
    etgan domenlar joriy ishga tushirish oxirigacha "bloklangan"."""

    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self._last = {}
        self.blocked = {}

    def block(self, host, reason):
        if host:
            self.blocked.setdefault(host, reason)

    def wait(self, host):
        if host in self.blocked:
            raise FetchSkip('host_blocked', '%s (%s)' % (host, self.blocked[host]))
        now = self.clock()
        last = self._last.get(host)
        if last is not None and now - last < self.interval:
            self.sleep(self.interval - (now - last))
        self._last[host] = self.clock()


# ── Nomzod tanlash ───────────────────────────────────────────────────────────

def _location_license(location):
    raw = (location or {}).get('license')
    if not raw:
        return None
    return map_license_label(raw)[0] or 'unrecognized'


def select_pdf_location(article, metadata, config):
    """(location dict, None) yoki (None, sabab).

    `article` — `scientific_articles` qatori; `metadata` — import elementining
    `metadata_candidate`i (external_locations shu yerda).
    """
    license_type = article.get('license_type')
    if license_type not in config.allowed_licenses:
        return None, 'license_not_allowed'
    if (article.get('pdf_path') or '').strip():
        return None, 'already_has_pdf'
    if article.get('is_retracted'):
        return None, 'retracted'
    if article.get('publication_status') == 'archived' or article.get(
            'verification_status') in ('rejected', 'copyright_hold'):
        return None, 'status_blocks_pdf'

    locations = [l for l in (metadata or {}).get('external_locations') or []
                 if isinstance(l, dict) and l.get('pdf_url')
                 and l.get('source') in ('openalex', 'unpaywall')]
    if not locations:
        return None, 'no_pdf_url'

    usable = []
    for location in locations:
        version = VERSION_MAP.get((location.get('version') or '').replace(' ', '').lower())
        loc_license = _location_license(location)
        if loc_license is not None and loc_license != license_type:
            continue                     # joylashuv boshqa litsenziya bilan
        if loc_license is None and version != 'version_of_record':
            continue                     # litsenziyasiz nusxa faqat VOR bo'lsa
        if version is None:
            continue
        usable.append((location, version))
    if not usable:
        return None, 'no_matching_location'

    rank = {'version_of_record': 0, 'accepted_manuscript': 1, 'preprint': 2}
    usable.sort(key=lambda lv: (rank[lv[1]], not lv[0].get('is_best'),
                                lv[0].get('source') != 'openalex'))
    location, version = usable[0]
    return dict(location, version_type=version), None


# ── Yuklab olish ─────────────────────────────────────────────────────────────

def _check_public_host(url, resolver=socket.getaddrinfo):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise FetchSkip('bad_url')
    try:
        infos = resolver(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
    except OSError:
        raise FetchSkip('dns_error')
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            raise FetchSkip('non_public_host')
    return parts.hostname.lower()


def _looks_like_challenge(head):
    lowered = head[:4096].lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def download_pdf(url, session, config, throttle, *, resolver=socket.getaddrinfo,
                 sleep=time.sleep):
    """URL -> (bytes, content_type, final_url). Muvaffaqiyatsiz bo'lsa FetchSkip.

    Redirect'lar qo'lda kuzatiladi: har bir sakrash ham ommaviy host
    tekshiruvidan va domen throttle'idan o'tadi.
    """
    import requests

    attempts = 1 + max(0, config.retries)
    last_reason = 'network_error'
    for attempt in range(attempts):
        current = url
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                host = _check_public_host(current, resolver)
                throttle.wait(host)
                response = session.get(
                    current, stream=True, allow_redirects=False,
                    timeout=(config.connect_timeout, config.read_timeout),
                    headers={'User-Agent': config.user_agent(),
                             'Accept': 'application/pdf,*/*;q=0.5'})
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    response.close()
                    if not location:
                        raise FetchSkip('bad_redirect')
                    from urllib.parse import urljoin
                    current = urljoin(current, location)
                    continue
                break
            else:
                raise FetchSkip('too_many_redirects')

            status = response.status_code
            if status in SKIP_STATUSES:
                response.close()
                throttle.block(host, 'http_%d' % status)
                raise FetchSkip('http_%d' % status)
            if status in RETRY_STATUSES:
                response.close()
                last_reason = 'http_%d' % status
                raise _Retry()
            if status != 200:
                response.close()
                raise FetchSkip('http_%d' % status)

            content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            declared = response.headers.get('Content-Length')
            if declared and declared.isdigit() and int(declared) > config.max_bytes:
                response.close()
                raise FetchSkip('too_large')

            chunks, total = [], 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > config.max_bytes:
                    response.close()
                    raise FetchSkip('too_large')
                chunks.append(chunk)
            response.close()
            data = b''.join(chunks)
            if not data.startswith(b'%PDF-'):
                if content_type.startswith('text/html') or data.lstrip()[:1] == b'<':
                    if _looks_like_challenge(data):
                        throttle.block(host, 'challenge_page')
                        raise FetchSkip('challenge_page')
                    raise FetchSkip('html_not_pdf')
                raise FetchSkip('not_pdf')
            return data, content_type, current
        except _Retry:
            pass
        except FetchSkip:
            raise
        except requests.Timeout:
            last_reason = 'timeout'
        except requests.RequestException:
            last_reason = 'network_error'
        if attempt + 1 < attempts:
            sleep(config.backoff_seconds * (attempt + 1))
    raise FetchSkip(last_reason)


class _Retry(Exception):
    pass


def validate_downloaded_pdf(data, content_type, max_bytes):
    """`security_uploads.validate_pdf_upload` ni AYNAN qayta ishlatadi:
    MIME oq ro'yxati, `%PDF-`, `%%EOF`, chegaralangan hajm, SHA-256."""
    from werkzeug.datastructures import FileStorage

    storage = FileStorage(stream=io.BytesIO(data), filename='download.pdf',
                          content_type=content_type or 'application/pdf',
                          content_length=len(data))
    try:
        return validate_pdf_upload(storage, max_bytes=max_bytes)
    except UploadValidationError as exc:
        raise FetchSkip('invalid_pdf', exc.public_message)


def ensure_disk_space(path, min_free_bytes, usage=shutil.disk_usage):
    import os
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    free = usage(probe or '/').free
    if free < min_free_bytes:
        raise DiskSpaceLow('Diskda %.1f GB qoldi (< %.1f GB)'
                           % (free / 1024 ** 3, min_free_bytes / 1024 ** 3))
    return free


# ── DB ───────────────────────────────────────────────────────────────────────

CANDIDATE_SQL = """
    SELECT a.id, a.title, a.doi, a.journal_name, a.license_type, a.license_url,
           a.pdf_path, a.is_retracted, a.publication_status, a.verification_status,
           a.ingest_evidence, i.metadata_candidate
      FROM scientific_articles a
      JOIN LATERAL (
            SELECT it.metadata_candidate FROM article_import_items it
             WHERE it.created_article_id = a.id
             ORDER BY it.id DESC LIMIT 1) i ON TRUE
     WHERE a.license_type = ANY(%s)
       AND COALESCE(a.pdf_path, '') = ''
       AND NOT a.is_retracted
       AND a.publication_status <> 'archived'
       AND a.verification_status NOT IN ('rejected', 'copyright_hold')
       AND COALESCE(a.ingest_evidence -> 'pdf' ->> 'status', '') <> 'skipped_final'
       AND EXISTS (SELECT 1 FROM jsonb_array_elements(
                        COALESCE(i.metadata_candidate -> 'external_locations', '[]'::jsonb)) l
                    WHERE COALESCE(l ->> 'pdf_url', '') <> '')
     ORDER BY a.id
     LIMIT %s
"""


def load_candidates(cur, config, limit):
    cur.execute(CANDIDATE_SQL, (sorted(config.allowed_licenses), limit))
    cols = [c[0] for c in cur.description]
    rows = []
    for raw in cur.fetchall():
        row = dict(zip(cols, raw))
        for key in ('metadata_candidate', 'ingest_evidence'):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except ValueError:
                    row[key] = None
        rows.append(row)
    return rows


def find_checksum_duplicate(cur, checksum):
    cur.execute("SELECT id FROM scientific_articles WHERE checksum_sha256 = %s LIMIT 1",
                (checksum,))
    row = cur.fetchone()
    return row[0] if row else None


def permission_note_text(article, location, checksum, fetched_at):
    evidence = ((article.get('ingest_evidence') or {}).get('license') or {})
    lines = ['Avtomatik OA PDF (scripts/fetch_open_pdfs.py), %s.' % fetched_at,
             'Litsenziya kelishuvi: %s (%s).' % (article.get('license_type'),
                                                 evidence.get('basis') or '?')]
    for item in evidence.get('evidence') or []:
        lines.append('- %s: %s%s' % (
            item.get('source'),
            item.get('label') or item.get('license_type') or item.get('url') or '?',
            (' [%s]' % item['applies_to']) if item.get('applies_to') else ''))
    lines.append('PDF manbasi: %s (%s, %s).' % (
        location.get('pdf_url'), location.get('source'), location.get('version')))
    lines.append('SHA-256: %s' % checksum)
    return '\n'.join(lines)[:4000]


def apply_pdf(cur, article, location, *, rel_path, size, checksum, admin_id,
              final_url, fetched_at):
    """PDF va huquqiy maydonlarni yozadi. Huquq kombinatsiyasi avval
    `validate_article_rights` dan o'tadi. `pdf_path` bo'sh bo'lsagina
    yangilaydi (parallel ishga tushirishdan himoya). True — yozildi."""
    validate_article_rights('open_access', article['license_type'], True, True)
    evidence = {'pdf': {
        'status': 'stored', 'pdf_url': location.get('pdf_url'),
        'final_url': final_url, 'source': location.get('source'),
        'location_version': location.get('version'),
        'location_license': location.get('license'),
        'sha256': checksum, 'size': size, 'fetched_at': fetched_at,
        'admin_id': admin_id}}
    note = permission_note_text(article, location, checksum, fetched_at)
    cur.execute(
        """UPDATE scientific_articles SET
              pdf_path = %s, pdf_original_name = %s, pdf_size = %s,
              checksum_sha256 = %s, access_type = 'open_access',
              version_type = %s, source_type = 'open_journal',
              source_name = %s, source_url = %s,
              license_url = COALESCE(NULLIF(license_url, ''), %s),
              rights_confirmed_by = %s, rights_confirmed_at = NOW(),
              permission_note = CASE
                  WHEN COALESCE(BTRIM(permission_note), '') = '' THEN %s
                  ELSE permission_note || E'\\n\\n' || %s END,
              ingest_evidence = COALESCE(ingest_evidence, '{}'::jsonb) || %s::jsonb,
              updated_at = NOW()
            WHERE id = %s AND COALESCE(pdf_path, '') = ''""",
        (rel_path, 'maqola-%d.pdf' % article['id'], size, checksum,
         location['version_type'],
         (article.get('journal_name') or '').strip() or None,
         location.get('landing_page_url') or location.get('pdf_url'),
         LICENSE_URLS.get(article['license_type']), admin_id,
         # Admin yoki oldingi bosqich yozgan izoh saqlanadi — dalil OXIRIGA qo'shiladi.
         note, note,
         json.dumps(evidence, ensure_ascii=False), article['id']))
    return cur.rowcount == 1


# Qayta urinish ma'nosiz sabablar — keyingi ishga tushirishda nomzod
# bo'lmaydi (`skipped_final`). Tarmoq/timeout/5xx — qayta urinsa bo'ladi.
FINAL_SKIP_REASONS = frozenset({
    'http_401', 'http_403', 'http_404', 'http_410', 'challenge_page',
    'html_not_pdf', 'not_pdf', 'invalid_pdf', 'too_large', 'duplicate_checksum',
    'rights_invalid', 'no_matching_location', 'no_pdf_url', 'non_public_host',
    'bad_url', 'too_many_redirects', 'bad_redirect', 'retracted', 'status_blocks_pdf',
})


def record_skip(cur, article_id, reason, detail=None, url=None):
    status = 'skipped_final' if reason in FINAL_SKIP_REASONS else 'skipped'
    evidence = {'pdf': {'status': status, 'reason': reason,
                        'detail': (str(detail)[:300] if detail else None),
                        'pdf_url': url,
                        'checked_at': datetime.now(timezone.utc).isoformat()}}
    cur.execute("""UPDATE scientific_articles
                      SET ingest_evidence = COALESCE(ingest_evidence, '{}'::jsonb) || %s::jsonb
                    WHERE id = %s""",
                (json.dumps(evidence, ensure_ascii=False), article_id))


# ── Bitta maqola ─────────────────────────────────────────────────────────────

def fetch_one(cur, article, *, config, session, throttle, storage_dir, admin_id,
              execute, resolver=socket.getaddrinfo, sleep=time.sleep,
              save=save_private_pdf):
    """Natija: ('stored'|'would_fetch'|'skipped', reason, info)."""
    location, reason = select_pdf_location(article, article.get('metadata_candidate'), config)
    if location is None:
        if execute and reason in FINAL_SKIP_REASONS:
            record_skip(cur, article['id'], reason)
        return 'skipped', reason, None
    if not execute:
        return 'would_fetch', None, location

    ensure_disk_space(storage_dir, config.min_free_bytes)
    try:
        data, content_type, final_url = download_pdf(
            location['pdf_url'], session, config, throttle, resolver=resolver, sleep=sleep)
        data, checksum = validate_downloaded_pdf(data, content_type, config.max_bytes)
    except FetchSkip as exc:
        record_skip(cur, article['id'], exc.reason, exc.detail, location.get('pdf_url'))
        return 'skipped', exc.reason, location

    duplicate = find_checksum_duplicate(cur, checksum)
    if duplicate:
        record_skip(cur, article['id'], 'duplicate_checksum', 'maqola #%s' % duplicate,
                    location.get('pdf_url'))
        return 'skipped', 'duplicate_checksum', location

    try:
        validate_article_rights('open_access', article['license_type'], True, True)
    except ArticleValidationError as exc:
        record_skip(cur, article['id'], 'rights_invalid', exc.public_message)
        return 'skipped', 'rights_invalid', location

    rel_path = save(data, storage_dir)
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        written = apply_pdf(cur, article, location, rel_path=rel_path, size=len(data),
                            checksum=checksum, admin_id=admin_id,
                            final_url=final_url, fetched_at=fetched_at)
    except Exception:
        _remove_quietly(storage_dir, rel_path)
        raise
    if not written:
        _remove_quietly(storage_dir, rel_path)
        return 'skipped', 'already_has_pdf', location
    return 'stored', None, dict(location, rel_path=rel_path, size=len(data))


def _remove_quietly(storage_dir, rel_path):
    import os
    from security_uploads import resolve_private_pdf
    path = resolve_private_pdf(storage_dir, rel_path)
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
