"""Profildagi tashqi havolalarni (Telegram, ORCID, Google Scholar, umumiy URL)
kanonik shaklga keltirish. Foydalanuvchi "@user", "user", "t.me/user" yoki
to'liq URL kiritishi mumkin — barchasi bitta formatga normalizatsiya qilinadi.
"""
import re

_TG_PREFIX_RE = re.compile(r'^https?://(www\.)?t\.me/', re.I)
_TG_BARE_RE = re.compile(r'^t\.me/', re.I)
_ORCID_PREFIX_RE = re.compile(r'^https?://(www\.)?orcid\.org/', re.I)
_ORCID_BARE_RE = re.compile(r'^orcid\.org/', re.I)
_SCHOLAR_USER_RE = re.compile(r'user=([\w-]+)')
_SCHOLAR_DOMAIN_RE = re.compile(r'^(https?://)?(www\.)?scholar\.google\.com', re.I)
_URL_SCHEME_RE = re.compile(r'^https?://', re.I)


def normalize_telegram(value):
    """"@user" / "user" / "t.me/user" / "https://t.me/user" -> "https://t.me/user"."""
    if not value:
        return value
    v = value.strip()
    v = _TG_PREFIX_RE.sub('', v)
    v = _TG_BARE_RE.sub('', v)
    v = v.lstrip('@').strip('/')
    return f'https://t.me/{v}' if v else None


def normalize_orcid(value):
    """"0000-0001-2345-6789" / "orcid.org/0000-..." -> "https://orcid.org/0000-...". """
    if not value:
        return value
    v = value.strip()
    v = _ORCID_PREFIX_RE.sub('', v)
    v = _ORCID_BARE_RE.sub('', v)
    v = v.strip('/')
    return f'https://orcid.org/{v}' if v else None


def normalize_scholar(value):
    """"user=ABC123" / "scholar.google.com/citations?user=ABC123" -> full HTTPS URL."""
    if not value:
        return value
    v = value.strip()
    m = _SCHOLAR_USER_RE.search(v)
    if m:
        return f'https://scholar.google.com/citations?user={m.group(1)}'
    if _SCHOLAR_DOMAIN_RE.match(v):
        return v if _URL_SCHEME_RE.match(v) else f'https://{v}'
    return normalize_url(v)


def normalize_url(value):
    """Add a https:// scheme when the value doesn't already start with http(s)://."""
    if not value:
        return value
    v = value.strip()
    return v if _URL_SCHEME_RE.match(v) else f'https://{v}'
