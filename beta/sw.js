/* Akashic Chords TEST version offline helper. Only looks after the /beta/ folder; its caches are named akbeta-... so the current version's cache is never touched. */
var CACHE = "akbeta-10444967c2";
var OCR = "akbeta-ocr-25cd554a"; /* the photo reader files: kept across app updates, fetched again only when they change */
var FILES = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];
var OCR_FILES = ["ocr/tesseract.min.js", "ocr/worker.min.js", "ocr/tesseract-core-lstm.wasm.js", "ocr/tesseract-core-simd-lstm.wasm.js", "ocr/eng.traineddata.gz"];
function fillOcr() { return caches.open(OCR).then(function (c) { return c.match(OCR_FILES[OCR_FILES.length - 1]).then(function (m) { if (!m) return c.addAll(OCR_FILES); }); }); }
self.addEventListener("install", function (e) { e.waitUntil(Promise.all([caches.open(CACHE).then(function (c) { return c.addAll(FILES.map(function (f) { return new Request(f, { cache: "reload" }); })); }).then(function () { return self.skipWaiting(); }), fillOcr().catch(function () {})])); });
self.addEventListener("activate", function (e) { e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k !== CACHE && k !== OCR && k.indexOf("akbeta-") === 0; }).map(function (k) { return caches.delete(k); })); }).then(function () { return self.clients.claim(); })); fillOcr().catch(function () {}); });
function fromNetwork(req, ms) {
  return new Promise(function (resolve, reject) {
    var done = false, t = setTimeout(function () { if (!done) { done = true; reject(new Error("slow")); } }, ms);
    fetch(req, { cache: "no-cache" }).then(function (r) { if (!done) { done = true; clearTimeout(t); resolve(r); } }, function (err) { if (!done) { done = true; clearTimeout(t); reject(err); } });
  });
}
self.addEventListener("fetch", function (e) {
  var req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.indexOf(self.registration.scope.replace(self.location.origin, "")) !== 0) return;
  if (url.pathname.indexOf("/request/") >= 0) return; /* the audience page is always loaded fresh and never replaces the app's offline copy */
  if (req.mode === "navigate") {
    e.respondWith(fromNetwork(req, 3500).then(function (r) { if (r && r.ok) { var copy = r.clone(); caches.open(CACHE).then(function (c) { c.put("index.html", copy); }); } return r; }).catch(function () { return caches.match("index.html", { cacheName: CACHE }).then(function (m) { return m || caches.match("./", { cacheName: CACHE }); }); }));
    return;
  }
  var box = url.pathname.indexOf("/ocr/") >= 0 ? OCR : CACHE;
  e.respondWith(caches.match(req, { cacheName: box }).then(function (m) { return m || fetch(req).then(function (r) { if (r && r.ok) { var copy = r.clone(); caches.open(box).then(function (c) { c.put(req, copy); }); } return r; }); }));
});
