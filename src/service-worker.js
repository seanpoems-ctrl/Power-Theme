/* eslint-disable no-restricted-globals */
import { clientsClaim } from 'workbox-core';
import { ExpirationPlugin } from 'workbox-expiration';
import { precacheAndRoute, createHandlerBoundToURL } from 'workbox-precaching';
import { registerRoute } from 'workbox-routing';
import { StaleWhileRevalidate, NetworkFirst } from 'workbox-strategies';

clientsClaim();

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
    cacheName: 'power-theme-data-v1',
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
  new NetworkFirst({ cacheName: 'external-api-v1' })
);

// Skip waiting when a new SW is available (triggered by the app)
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
