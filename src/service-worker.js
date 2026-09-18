/* eslint-disable no-restricted-globals */
import { clientsClaim } from 'workbox-core';
import { ExpirationPlugin } from 'workbox-expiration';
import { precacheAndRoute, createHandlerBoundToURL } from 'workbox-precaching';
import { registerRoute } from 'workbox-routing';
import { StaleWhileRevalidate, NetworkFirst } from 'workbox-strategies';

clientsClaim();

// Named once here so the activate-time cleanup below always matches whatever
// version the two routes below are actually using — bump these, not the
// string literals further down.
const DATA_CACHE_NAME = 'power-theme-data-v2';
const EXTERNAL_CACHE_NAME = 'external-api-v1';

// Precache all build artifacts (JS, CSS, HTML) — injected at build time
precacheAndRoute(self.__WB_MANIFEST);

// Single-page app navigation fallback
const fileExtensionRegexp = new RegExp('/[^/?]+\\.[^/]+$');
registerRoute(
  ({ request, url }) => {
    if (request.mode !== 'navigate') return false;
    if (url.pathname.startsWith('/_')) return false;
    if (url.pathname.match(fileExtensionRegexp)) return false;
    return true;
  },
  createHandlerBoundToURL(process.env.PUBLIC_URL + '/index.html')
);

// JSON data files — stale-while-revalidate: show last cached snapshot instantly,
// fetch fresh copy in background. Offline users see last known data.
registerRoute(
  ({ url }) => url.pathname.endsWith('.json'),
  new StaleWhileRevalidate({
    cacheName: DATA_CACHE_NAME,
    plugins: [
      new ExpirationPlugin({ maxEntries: 30, maxAgeSeconds: 24 * 60 * 60 }),
    ],
  })
);

// External API calls (IBKR, Gemini, yfinance proxies) — network only, never cache
registerRoute(
  ({ url }) =>
    url.hostname !== self.location.hostname &&
    !url.hostname.endsWith('github.io'),
  new NetworkFirst({ cacheName: EXTERNAL_CACHE_NAME })
);

// Drop any previous-version runtime cache left behind by a version bump above
// (e.g. power-theme-data-v1, orphaned by the v1->v2 bump) instead of leaving
// it in browser storage indefinitely — workbox-precaching already does this
// for its own precache automatically, but these two hand-named caches aren't
// covered by that.
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((n) => (n.startsWith('power-theme-data-') || n.startsWith('external-api-'))
            && n !== DATA_CACHE_NAME && n !== EXTERNAL_CACHE_NAME)
          .map((n) => caches.delete(n))
      )
    )
  );
});

// Skip waiting when a new SW is available (triggered by the app)
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
