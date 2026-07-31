/* 祭りマップ Service Worker — オフラインでも一度見たデータを表示する */
const CACHE = "matsuri-map-v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;

  // データと地図タイルは stale-while-revalidate
  if (url.pathname.endsWith(".json") || url.hostname.includes("gsi.go.jp") || url.hostname.includes("unpkg.com")) {
    e.respondWith(
      caches.open(CACHE).then(async c => {
        const hit = await c.match(e.request);
        const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; })
                                    .catch(() => hit);
        return hit || net;
      })
    );
    return;
  }

  // それ以外は cache-first
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
