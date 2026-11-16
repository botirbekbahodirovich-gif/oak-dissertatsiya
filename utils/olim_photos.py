"""Scholar photo lookup: olim_profiles.photo_url → olim_photos → placeholder.

olim_photos (migration 023) maps utils.name_norm.strict_key(name) to a
256×256 WebP under static/olim_photos/. The whole table (~7k rows) is cached
in memory for PHOTO_CACHE_TTL seconds. dissertations.photo_url and
ilmiy_rahbar_photo_url (dead Supabase links) are deliberately never read.
"""
import threading
import time

from utils.name_norm import strict_key

PHOTO_URL_PREFIX = '/static/olim_photos/'
PHOTO_CACHE_TTL = 600
_RETRY_AFTER_ERROR = 60

_lock = threading.Lock()
_cache = {'map': {}, 'expires': 0.0}


def _load_map():
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.olim_photos')")
            if cur.fetchone()[0] is None:
                return {}
            cur.execute("SELECT name_norm, file FROM olim_photos")
            return {k: f for k, f in cur.fetchall()}
    finally:
        conn.close()


def photo_map():
    now = time.monotonic()
    if now < _cache['expires']:
        return _cache['map']
    with _lock:
        if now < _cache['expires']:
            return _cache['map']
        try:
            _cache['map'] = _load_map()
            _cache['expires'] = now + PHOTO_CACHE_TTL
        except Exception:
            _cache['expires'] = now + _RETRY_AFTER_ERROR
        return _cache['map']


def clear_cache():
    _cache['map'], _cache['expires'] = {}, 0.0


def matched_photo_url(name):
    """URL of the matched archive photo for this name, or None."""
    key = strict_key(name)
    f = photo_map().get(key) if key else None
    return PHOTO_URL_PREFIX + f if f else None


def scholar_photo_url(name, profile_photo=None):
    """Display order: the scholar's own upload, then the archive photo, else None (placeholder)."""
    own = (profile_photo or '').strip()
    if own and 'supabase.co' not in own:
        return own
    return matched_photo_url(name)
