/* Deriv Signal Lab service worker: app-shell cache + Web Push. API responses are never cached. */
const CACHE = "dsl-shell-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icon.svg", "/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return; // live data only from network
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok && url.origin === self.location.origin) {
          const copy = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return r;
      })
      .catch(() => caches.match(e.request).then((m) => m || caches.match("/index.html"))),
  );
});
self.addEventListener("push", (e) => {
  let data = { title: "Deriv Signal Lab", body: "" };
  try { data = e.data.json(); } catch (_) { /* ignore */ }
  e.waitUntil(self.registration.showNotification(data.title, {
    body: data.body, icon: "/icon-192.png", badge: "/icon-192.png", tag: data.signal_id ? `${data.signal_id}-${data.event}` : undefined,
    data: { url: data.signal_id ? `/#/signals/${data.signal_id}` : "/" },
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => {
    for (const c of cs) { if ("focus" in c) { c.navigate(target); return c.focus(); } }
    return self.clients.openWindow(target);
  }));
});
