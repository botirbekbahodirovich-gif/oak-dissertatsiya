const CACHE_NAME = 'olimlar-v3';
const OFFLINE_URL = '/offline';

// Files to cache on install
const PRECACHE_URLS = [
  '/',
  '/offline',
  '/static/images/logo-mark.png',
  '/static/images/favicon.png'
];

// Install — cache essential files
self.addEventListener('install', event => {
  // Activate immediately, even if precaching fails — a broken old worker
  // must never outlive a deploy that fixes it.
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE_URLS))
  );
});

// Activate — clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch — network first, fallback to cache, then offline page
self.addEventListener('fetch', event => {
  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // Cross-origin requests (CDN scripts, fonts, icons) go straight to the
  // network: fetch() from the worker is bound by sw.js's own CSP connect-src,
  // which does not list the CDNs, so proxying them broke d3, Bootstrap and
  // bootstrap-icons for every returning visitor.
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // Skip API calls and admin/cabinet pages
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/admin/') ||
      url.pathname.startsWith('/cabinet/')) return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Cache successful responses for static assets only
        if (response.ok && url.pathname.startsWith('/static/')) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => {
        // Try cache
        return caches.match(event.request).then(cached => {
          if (cached) return cached;
          // Show offline page for navigation requests
          if (event.request.mode === 'navigate') {
            return caches.match('/offline');
          }
          return new Response('Offline', { status: 503 });
        });
      })
  );
});
