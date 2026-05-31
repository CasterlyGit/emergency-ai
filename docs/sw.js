/* emergency-ai service worker — eai-v1
 * Cache-first for app shell; network-first with cache fallback for /emergency + API.
 * Privacy: raw situation text never stored. Only {request_id, city, urgency, …} logged.
 */

const CACHE_NAME = 'eai-v1';

// All paths are relative to the service-worker scope (docs/).
const PRECACHE_URLS = [
  // App shell
  './',
  './index.html',
  './manifest.webmanifest',

  // Styles
  './css/styles.css',

  // JavaScript modules
  './js/engine.js',
  './js/app.js',
  './js/effects.js',

  // Data bundles (offline corpus)
  './data/scenarios.json',
  './data/cities.json',
  './data/i18n.json',
  './data/poison.json',
  './data/disasters.json',
  './data/medical_ref.json',

  // Icons
  './assets/icon-192.svg',
  './assets/icon-512.svg',
];

// ─── Install ────────────────────────────────────────────────────────────────

self.addEventListener('install', (event) => {
  // Resilient precache: one missing asset must NOT abort the whole install
  // (a failed addAll() would leave the app with no offline cache at all).
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url)))
      )
      .then(() => self.skipWaiting())
  );
});

// ─── Activate ───────────────────────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

// ─── Fetch ──────────────────────────────────────────────────────────────────

/**
 * Returns true for requests that should use network-first strategy.
 *
 * The live API always lives on a DIFFERENT origin than the PWA (e.g. the PWA is on
 * GitHub Pages at `/emergency-ai/` and the API is on Fly). Treating any cross-origin
 * request as "API" is both correct and robust — it avoids the trap where a substring
 * match on '/emergency' also matches the Pages base path '/emergency-ai/' and forces
 * the entire app shell into network-first, defeating offline-first.
 */
function isApiRequest(request) {
  return new URL(request.url).origin !== self.location.origin;
}

/**
 * Cache-first: serve from cache; fall back to network + update cache on success.
 */
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    // Offline and nothing in cache — return a minimal offline page if html, else 503.
    if (request.destination === 'document') {
      const fallback = await caches.match('./index.html');
      if (fallback) return fallback;
    }
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

/**
 * Network-first: try network; on failure, serve stale cache or 503.
 * Response is cached on success so subsequent offline reads work.
 */
async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);

  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response(
      JSON.stringify({ error: 'offline', message: 'No network and no cached response.' }),
      {
        status: 503,
        statusText: 'Service Unavailable',
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Only handle GET (and SSE) — let POST/PUT etc. go through unless it's the SSE stream.
  // The /emergency endpoint is a streaming SSE POST; we still apply network-first to it
  // so the SW doesn't buffer the whole stream, but we do NOT cache POST bodies.
  if (request.method !== 'GET') {
    if (isApiRequest(request)) {
      // Let SSE POST pass straight to the network; no caching of request bodies.
      event.respondWith(fetch(request));
    }
    // All other non-GET requests fall through (browser default).
    return;
  }

  if (isApiRequest(request)) {
    event.respondWith(networkFirst(request));
  } else {
    event.respondWith(cacheFirst(request));
  }
});
