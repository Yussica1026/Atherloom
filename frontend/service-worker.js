const CACHE = "atherloom-shell-v141";
const SHELL = ["manifest.json", "assets/app-icon.svg", "assets/app-icon-dark.svg", "assets/app-icon-monochrome.svg"];
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET" || new URL(event.request.url).pathname.startsWith("/api/")) return;
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).then(response => { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put("./", copy)); return response; }).catch(() => caches.match("./")));
    return;
  }
  if (event.request.destination === "style" || event.request.destination === "script") {
    event.respondWith(fetch(event.request, { cache: "no-store" }).then(response => {
      const type = response.headers.get("content-type") || "";
      const valid = response.ok && (event.request.destination === "style" ? type.includes("text/css") : type.includes("javascript"));
      if (!valid) throw new Error("Invalid versioned asset response");
      const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response;
    }).catch(() => caches.match(event.request)));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(response => { if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); } return response; })));
});
