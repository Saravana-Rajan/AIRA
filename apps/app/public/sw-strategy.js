// AIRA service-worker strategy. Plain script: sw.js loads it with importScripts,
// tests evaluate it the same way, and it also works as a CommonJS module.
// Rule zero: live traffic (WebSocket, Firestore, Google APIs, Cloud Run, /api, /webhooks)
// is NEVER cached, so a stale copy can never hide an outage from a blind shopkeeper.
(function (root) {
  var VERSION = "aira-v1";
  var PRECACHE = ["/", "/index.html", "/manifest.webmanifest", "/icons/aira-logo-192.png", "/icons/aira-logo-512.png"];

  var BYPASS_PATHS = ["/api/", "/webhooks/", "/__/", "/.well-known/"];
  var CACHE_FIRST_PATHS = ["/assets/", "/demo/", "/models/", "/icons/"];
  var STATIC_EXT = /\.(js|mjs|css|png|jpe?g|webp|gif|svg|ico|woff2?|ttf|wasm|tflite|task|mp4|webm|wav|mp3|ogg)$/i;

  function startsWithAny(path, prefixes) {
    for (var i = 0; i < prefixes.length; i++) if (path.indexOf(prefixes[i]) === 0) return true;
    return false;
  }

  // origin (optional): the app's own origin. When given, unknown cross-origin requests bypass.
  function strategyFor(url, mode, origin) {
    var u;
    try { u = new URL(url); } catch (e) { return "bypass"; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return "bypass"; // ws:, wss:, blob:, data:, extensions
    var host = u.hostname;
    var path = u.pathname;

    // Cross-origin hosts we know about.
    if (host === "storage.googleapis.com" && path.indexOf("/mediapipe-models/") === 0) return "cache-first";
    if (/(^|\.)googleapis\.com$/.test(host)) return "bypass"; // Firestore, Storage API, Installations, Fonts CSS
    if (/(^|\.)firebaseio\.com$/.test(host)) return "bypass";
    if (/\.run\.app$/.test(host)) return "bypass"; // aira-live on Cloud Run
    if (host === "cdn.jsdelivr.net" && path.indexOf("/npm/@mediapipe/") === 0) return "cache-first";
    if (host === "fonts.gstatic.com") return "cache-first"; // immutable font files
    if (origin && u.origin !== origin) return "bypass";

    // Same origin.
    if (startsWithAny(path, BYPASS_PATHS)) return "bypass";
    if (mode === "navigate") return "network-first";
    if (/\.webmanifest$/.test(path)) return "network-first";
    if (startsWithAny(path, CACHE_FIRST_PATHS) || STATIC_EXT.test(path)) return "cache-first";
    return "network-first";
  }

  // The cached shell to show for an offline navigation that was never cached itself.
  function fallbackFor(url) {
    var path = new URL(url).pathname;
    return path === "/dashboard" || path.indexOf("/dashboard/") === 0 ? "/dashboard/" : "/";
  }

  var api = { VERSION: VERSION, PRECACHE: PRECACHE, strategyFor: strategyFor, fallbackFor: fallbackFor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.swStrategy = api;
})(typeof self !== "undefined" ? self : globalThis);
