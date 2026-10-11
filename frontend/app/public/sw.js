// AIRA service worker: offline app shell + cached MediaPipe models and demo media.
// Routing rules live in sw-strategy.js. Only GET requests are ever cached.
importScripts("/sw-strategy.js");
var S = self.swStrategy;

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(S.VERSION).then(function (cache) {
      // One missing file must not block installation; cache: "reload" skips the HTTP cache.
      return Promise.all(S.PRECACHE.map(function (url) {
        return cache.add(new Request(url, { cache: "reload" })).catch(function () { return null; });
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) {
        return k.indexOf("aira-") === 0 && k !== S.VERSION;
      }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

// Cache only complete, same-URL, readable responses (no errors, redirects, opaque or partial).
function store(e, req, res) {
  if (res && res.status === 200 && !res.redirected && res.type !== "opaque") {
    var copy = res.clone();
    e.waitUntil(caches.open(S.VERSION).then(function (c) { return c.put(req, copy); }).catch(function () {}));
  }
  return res;
}

function cacheFirst(e, req) {
  return caches.match(req).then(function (hit) {
    return hit || fetch(req).then(function (res) { return store(e, req, res); });
  });
}

function networkFirst(e, req) {
  return fetch(req).then(function (res) { return store(e, req, res); }).catch(function (err) {
    return caches.match(req).then(function (hit) {
      if (hit) return hit;
      if (req.mode !== "navigate") throw err;
      return caches.match(S.fallbackFor(req.url)).then(function (shell) {
        if (shell) return shell;
        throw err;
      });
    });
  });
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  if (req.headers && req.headers.has("range")) return; // media seeking: let the network serve partial content
  if (req.cache === "only-if-cached" && req.mode !== "same-origin") return;
  var s = S.strategyFor(req.url, req.mode, self.location.origin);
  if (s === "bypass") return;
  e.respondWith(s === "cache-first" ? cacheFirst(e, req) : networkFirst(e, req));
});
