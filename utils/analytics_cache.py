"""Cache for /analytics-data: long TTL, one builder at a time, stale fallback.

Building the payload scans every dissertation, so a cold build is slow. Rules:
- the fresh value lives ANALYTICS_TTL (6 h); OAK imports call invalidate();
- a stale copy lives a week and is served while someone else rebuilds;
- only one process/thread builds: the lock is an O_EXCL file next to the cache
  (gunicorn runs several workers, so a threading.Lock is not enough).

invalidate() talks to the cache directory directly, so cron scripts
(scraper.py, scripts/daily_scraper.py) can use it without importing the app.
"""
import os
import time

ANALYTICS_KEY = 'analytics_data_v2'
ANALYTICS_STALE_KEY = 'analytics_data_v2_stale'
ANALYTICS_TTL = 6 * 3600
ANALYTICS_STALE_TTL = 7 * 24 * 3600
LOCK_TTL = 600          # a lock older than this is from a crashed builder
WAIT_SECONDS = 30       # no stale copy yet: how long a waiter polls for the builder

# Same folder app.py configures for FileSystemCache.
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'flask_cache')


def _lock_path(cache_dir):
    return os.path.join(cache_dir, 'analytics_data.lock')


def _try_lock(path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(path) > LOCK_TTL:
                os.remove(path)
                return _try_lock(path)
        except OSError:
            pass
        return False
    os.close(fd)
    return True


def get_or_build(cache, build, cache_dir=CACHE_DIR):
    """Return the payload, building it at most once at a time; None if none is ready."""
    data = cache.get(ANALYTICS_KEY)
    if data is not None:
        return data
    lock = _lock_path(cache_dir)
    if _try_lock(lock):
        try:
            data = build()
            cache.set(ANALYTICS_KEY, data, timeout=ANALYTICS_TTL)
            cache.set(ANALYTICS_STALE_KEY, data, timeout=ANALYTICS_STALE_TTL)
            return data
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass
    stale = cache.get(ANALYTICS_STALE_KEY)
    if stale is not None:
        return stale
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.5)
        data = cache.get(ANALYTICS_KEY)
        if data is not None:
            return data
    return None


def invalidate(cache_dir=CACHE_DIR):
    """Drop the fresh value after new dissertations land; the stale copy stays
    so visitors keep seeing charts while the next request rebuilds. Never raises."""
    try:
        from cachelib import FileSystemCache
        FileSystemCache(cache_dir).delete(ANALYTICS_KEY)
        return True
    except Exception as e:
        print("analytics keshini tozalab bo'lmadi: " + str(e))
        return False
