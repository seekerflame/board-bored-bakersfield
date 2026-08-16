/* sw.js — Board Bored service worker.
 *
 * Makes the map installable and usable with no signal. Deliberately conservative:
 *   - only same-origin GETs are ever intercepted. Cross-origin POSTs (Formspree)
 *     pass straight through so an offline submit FAILS at the page, where the
 *     IndexedDB queue in pwa.js catches it. The SW must never swallow a POST.
 *   - board.json is network-first (fresh vendor data when online, last-known
 *     when offline). Everything else is cache-first (the shell rarely changes).
 *
 * Paths are relative to this file's scope, so the same code works at the domain
 * root locally and under /board-bored-bakersfield/ on GitHub Pages.
 */
var VERSION = "bb-v6";
var SHELL = VERSION + "-shell";

// Resolved relative to the SW scope — correct under any subpath.
var PRECACHE = [
  "./",
  "first-friday/",
  "first-friday/about.html",
  "first-friday/submit-event.html",
  "first-friday/calendar.html",
  "first-friday/share.png",
  "first-friday/qr.png",
  "feedback.html",
  "vendor-signup.html",
  "index.html",
  "manifest.webmanifest",
  "lib/pwa.js",
  "lib/qrcode.js",
  "lib/cal.js",
  "data/board.json",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(SHELL).then(function (c) {
      // addAll is all-or-nothing; add individually so one 404 can't abort the SW.
      return Promise.all(PRECACHE.map(function (u) {
        return c.add(u).catch(function () { /* skip anything missing */ });
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== SHELL) return caches.delete(k);   // drop old versions
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isBoardJson(url) {
  return url.pathname.indexOf("data/board.json") !== -1;
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;                          // never touch POSTs
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;           // never touch cross-origin

  if (isBoardJson(url)) {
    // network-first: try fresh, fall back to cache, update cache on success
    e.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(SHELL).then(function (c) { c.put(req, copy); });
        return res;
      }).catch(function () { return caches.match(req); })
    );
    return;
  }

  // cache-first for the shell; fill the cache on first network hit
  e.respondWith(
    caches.match(req).then(function (hit) {
      return hit || fetch(req).then(function (res) {
        if (res && res.status === 200 && res.type === "basic") {
          var copy = res.clone();
          caches.open(SHELL).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () {
        // last resort: an offline navigation with nothing cached lands on the map
        if (req.mode === "navigate") return caches.match("first-friday/");
      });
    })
  );
});
