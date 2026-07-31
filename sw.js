/* 祭りマップ Service Worker
 *
 * 方針:
 *   - 祭りデータ(JSON) … ネットワーク優先。毎月更新されるので、古い月のデータを
 *     見せ続けないことを最優先する。オフライン時だけキャッシュにフォールバック。
 *   - 地図タイル・CDN … キャッシュ優先（内容が変わらないため）
 *   - サイト本体 … ネットワーク優先（更新をすぐ反映させる）
 */
const VERSION = "v3";
const SHELL_CACHE = "matsuri-shell-" + VERSION;
const DATA_CACHE = "matsuri-data-" + VERSION;
const ASSET_CACHE = "matsuri-asset-" + VERSION;

const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg"];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(SHELL_CACHE)
      .then(c => c.addAll(SHELL))
      .catch(() => {})
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  const keep = [SHELL_CACHE, DATA_CACHE, ASSET_CACHE];
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => !keep.includes(k)).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ネットワーク優先。失敗したらキャッシュ。
async function networkFirst(req, cacheName, timeoutMs) {
  const cache = await caches.open(cacheName);
  try {
    const controller = new AbortController();
    const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;
    const res = await fetch(req, { cache: "no-cache", signal: controller.signal });
    if (timer) clearTimeout(timer);
    if (res && res.ok) {
      cache.put(req, res.clone());
      return res;
    }
    throw new Error("bad response");
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw err;
  }
}

// キャッシュ優先。無ければ取得して保存。
async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res && res.ok) cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // 祭りデータ: 常に最新を取りに行く（オフライン時のみキャッシュ）
  if (url.pathname.endsWith(".json")) {
    e.respondWith(networkFirst(req, DATA_CACHE, 6000));
    return;
  }

  // 地図タイル・CDNライブラリ: 変わらないのでキャッシュ優先
  if (url.hostname.includes("gsi.go.jp") || url.hostname.includes("unpkg.com")) {
    e.respondWith(cacheFirst(req, ASSET_CACHE));
    return;
  }

  // 同一オリジンのページ・スクリプト: 更新をすぐ反映したいのでネットワーク優先
  if (url.origin === self.location.origin) {
    e.respondWith(networkFirst(req, SHELL_CACHE, 6000));
    return;
  }

  e.respondWith(fetch(req).catch(() => caches.match(req)));
});
