// Standard CRA service worker registration.
// Registers in production only; on localhost it validates the SW is still valid.
//
// Extended with two things the stock CRA template leaves as an exercise for
// the caller (nobody wired them up here, so a tab left open across a deploy
// would silently keep running the old bundle indefinitely):
//   1. Periodic + on-visibility registration.update() calls — browsers only
//      passively re-check the SW script for changes about once every ~24h,
//      far slower than this project's deploy cadence.
//   2. An auto-reload once a genuinely new SW takes control, so the update
//      that skip-waiting already installs in the background actually shows
//      up without a manual hard refresh.
//
// Note: update-detection byte-compares service-worker.js itself, not this
// file or the app's main bundle — a change here alone won't trigger a real
// SW update cycle to test against.

const isLocalhost = Boolean(
  window.location.hostname === 'localhost' ||
    window.location.hostname === '[::1]' ||
    window.location.hostname.match(/^127(?:\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}$/)
);

export function register(config) {
  if (process.env.NODE_ENV === 'production' && 'serviceWorker' in navigator) {
    const publicUrl = new URL(process.env.PUBLIC_URL, window.location.href);
    if (publicUrl.origin !== window.location.origin) return;

    window.addEventListener('load', () => {
      const swUrl = `${process.env.PUBLIC_URL}/service-worker.js`;
      isLocalhost ? checkValidServiceWorker(swUrl, config) : registerValidSW(swUrl, config);
    });
  }
}

function registerValidSW(swUrl, config) {
  navigator.serviceWorker
    .register(swUrl)
    .then((registration) => {
      // Force a check on our own schedule rather than waiting on the
      // browser's passive ~24h one — every 5 min while the tab is open, and
      // immediately whenever it becomes visible again (e.g. switching back
      // after being away).
      setInterval(() => registration.update(), 5 * 60 * 1000);
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') registration.update();
      });

      let updateDetected = false;
      registration.onupdatefound = () => {
        const installing = registration.installing;
        if (!installing) return;
        installing.onstatechange = () => {
          if (installing.state === 'installed') {
            if (navigator.serviceWorker.controller) {
              // New version available — tell the SW to skip waiting
              updateDetected = true;
              installing.postMessage({ type: 'SKIP_WAITING' });
              config?.onUpdate?.(registration);
            } else {
              config?.onReady?.(registration);
            }
          }
        };
      };

      // Once the new SW actually takes control, reload so the page picks up
      // the new bundle — but only when it's a real update (updateDetected),
      // never on the very first activation of a fresh visit, and only once.
      let reloaded = false;
      navigator.serviceWorker.oncontrollerchange = () => {
        if (!updateDetected || reloaded) return;
        reloaded = true;
        window.location.reload();
      };
    })
    .catch((err) => console.error('SW registration failed:', err));
}

function checkValidServiceWorker(swUrl, config) {
  fetch(swUrl, { headers: { 'Service-Worker': 'script' } })
    .then((res) => {
      const ct = res.headers.get('content-type');
      if (res.status === 404 || (ct && !ct.includes('javascript'))) {
        navigator.serviceWorker.ready.then((r) => r.unregister()).then(() => window.location.reload());
      } else {
        registerValidSW(swUrl, config);
      }
    })
    .catch(() => console.log('Offline — app running from cache.'));
}

export function unregister() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.ready
      .then((r) => r.unregister())
      .catch((err) => console.error(err.message));
  }
}
