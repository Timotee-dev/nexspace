/* NexSpace service worker ({{ version }}).
 * - Static files: cache first.
 * - Pages: network first; the last-loaded home feed is kept for offline reading.
 * - API calls and form posts: never cached.
 * - Push: shows notifications and opens the right page when tapped. */
const VERSION = "{{ version }}";
const SHELL = `${VERSION}-shell`;
const PAGES = "nexspace-pages";
const PRECACHE = {{ precache|safe }};

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== PAGES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "clear-private-cache") caches.delete(PAGES);
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/django-admin/")) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(request, copy));
        return res;
      }))
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).then((res) => {
        if (res.ok && url.pathname === "/" && !url.searchParams.has("partial")) {
          const copy = res.clone();
          caches.open(PAGES).then((c) => c.put("/", copy));
        }
        return res;
      }).catch(async () => (await caches.match(url.pathname === "/" ? "/" : request)) ||
                         (await caches.match("/offline/")))
    );
  }
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "NexSpace", {
    body: data.body || "",
    icon: "{% load static %}{% static 'img/icon-192.png' %}",
    badge: "{% static 'img/icon-192.png' %}",
    tag: data.tag,
    data: { url: data.url || "/notifications/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data.url, self.location.origin).href;
  event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const win of wins) {
      if ("focus" in win) { win.navigate(target); return win.focus(); }
    }
    return clients.openWindow(target);
  }));
});
